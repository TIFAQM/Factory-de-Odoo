"""Persistent Docker instance for incremental module installation.

Unlike the ephemeral docker_runner, this keeps a single Odoo+PostgreSQL
instance alive across multiple module installations. Modules accumulate
in the running instance, allowing cross-module interaction testing.

At 90+ modules, the instance holds the full ERP. Users access it via
browser to verify functionality. The manager tracks install order and
can roll back individual modules if needed.

Usage:
    manager = PersistentDockerManager()
    manager.ensure_running()
    result = manager.install_module(module_path)
    result = manager.run_module_tests(module_path)
    # ... install more modules ...
    # User accesses http://localhost:8069 to interact with ERP
    manager.stop()  # Only when human says done
"""

import re
import subprocess
import time
import json
import logging
from pathlib import Path
from dataclasses import dataclass, field

from .types import Result, InstallResult, TestResult

_MODULE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")

_INFRA_PATTERNS = re.compile(
    r"(?:Container\s+\w{12})|"   # Container IDs (12 hex chars)
    r"(?:network\s+\S+)|"        # Network names
    r"(?:\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}:\d+)",  # IP:port
    re.IGNORECASE,
)


def _sanitize_docker_error(stderr: str, max_length: int = 200) -> str:
    """Truncate and sanitize Docker stderr for error messages.

    Removes infrastructure details (container IDs, network names, IP:port)
    that could expose internal topology. Truncates to max_length chars.
    """
    cleaned = _INFRA_PATTERNS.sub("[REDACTED]", stderr)
    if len(cleaned) > max_length:
        cleaned = cleaned[:max_length] + "..."
    return cleaned

logger = logging.getLogger(__name__)

COMPOSE_FILE = Path(__file__).parent.parent / "data" / "docker" / "persistent-compose.yml"
PROJECT_NAME = "factory-de-odoo"
STATE_FILE = ".factory-docker-state.json"


@dataclass
class PersistentDockerManager:
    """Manages a long-lived Odoo Docker instance for incremental installs.

    At 90+ modules, this instance may run for hours or days. State is
    persisted to disk so it survives process restarts and context resets.
    """

    compose_file: Path = COMPOSE_FILE
    project_name: str = PROJECT_NAME
    installed_modules: list[str] = field(default_factory=list)
    install_order: list[dict] = field(default_factory=list)  # {name, timestamp, success}
    _running: bool = False
    _state_dir: Path | None = None

    def ensure_running(self, state_dir: Path | None = None) -> bool:
        """Start the persistent instance if not already running.

        Args:
            state_dir: Directory to persist state (for resume across context resets).
        """
        self._state_dir = state_dir
        self._load_state()

        if self._running and self._health_check():
            return True

        # Start containers
        result = subprocess.run(
            ["docker", "compose", "-f", str(self.compose_file),
             "-p", self.project_name, "up", "-d", "--wait"],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            logger.error("Failed to start persistent Docker: %s", _sanitize_docker_error(result.stderr))
            return False

        # Wait for Odoo to be healthy
        for attempt in range(30):
            if self._health_check():
                self._running = True
                self._save_state()
                return True
            time.sleep(2)

        return False

    @staticmethod
    def _validate_module_name(name: str) -> bool:
        """Validate module name against Odoo naming convention."""
        return bool(_MODULE_NAME_RE.match(name))

    def install_module(self, module_path: Path) -> Result[InstallResult]:
        """Install a module into the running instance incrementally."""
        if not self._running:
            return Result(success=False, errors=("Persistent Docker not running",))

        module_name = module_path.name
        if not self._validate_module_name(module_name):
            return Result(success=False,
                          errors=(f"Invalid module name: {module_name!r} (must match [a-z][a-z0-9_]*)",))

        # Copy module into the running container's addons path
        copy_result = subprocess.run(
            ["docker", "compose", "-f", str(self.compose_file),
             "-p", self.project_name,
             "cp", str(module_path), f"odoo:/mnt/extra-addons/{module_name}"],
            capture_output=True, text=True, timeout=30,
        )
        if copy_result.returncode != 0:
            return Result(success=False,
                          errors=(f"Failed to copy module: {_sanitize_docker_error(copy_result.stderr)}",))

        # Install via odoo CLI (list-form args — no bash -c to avoid CWE-78)
        install_result = subprocess.run(
            ["docker", "compose", "-f", str(self.compose_file),
             "-p", self.project_name,
             "exec", "-T", "odoo",
             "odoo", "-c", "/etc/odoo/odoo.conf", "-d", "odoo_factory",
             "--no-http", "--stop-after-init",
             "-i", module_name],
            capture_output=True, text=True, timeout=300,
        )

        from .log_parser import parse_install_log
        success, error_msg = parse_install_log(install_result.stdout)

        install = InstallResult(
            success=success,
            log_output=install_result.stdout,
            error_message=error_msg,
        )

        entry = {
            "name": module_name,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "success": success,
            "error": error_msg if not success else None,
        }
        self.install_order = [*self.install_order, entry]

        if success:
            self.installed_modules = [*self.installed_modules, module_name]

        self._save_state()
        return Result(success=True, data=install)

    def run_module_tests(self, module_path: Path) -> Result[tuple[TestResult, ...]]:
        """Run tests for a specific module in the persistent instance."""
        module_name = module_path.name
        if not self._validate_module_name(module_name):
            return Result(success=False,
                          errors=(f"Invalid module name: {module_name!r}",))

        test_result = subprocess.run(
            ["docker", "compose", "-f", str(self.compose_file),
             "-p", self.project_name,
             "exec", "-T", "odoo",
             "odoo", "-c", "/etc/odoo/odoo.conf", "-d", "odoo_factory",
             "--no-http", "--stop-after-init",
             "--test-tags", module_name,
             "-u", module_name],
            capture_output=True, text=True, timeout=600,
        )

        from .log_parser import parse_test_log
        test_results = parse_test_log(test_result.stdout)

        return Result(success=True, data=test_results)

    def run_cross_module_test(self, module_names: list[str]) -> Result[tuple[TestResult, ...]]:
        """Run tests that span multiple installed modules.

        At 90+ modules, cross-module interactions are common. This runs
        tests for a set of modules together, catching integration issues
        that per-module tests miss.
        """
        for name in module_names:
            if not self._validate_module_name(name):
                return Result(success=False,
                              errors=(f"Invalid module name: {name!r}",))
        tags = ",".join(module_names)
        modules = ",".join(module_names)

        test_result = subprocess.run(
            ["docker", "compose", "-f", str(self.compose_file),
             "-p", self.project_name,
             "exec", "-T", "odoo",
             "odoo", "-c", "/etc/odoo/odoo.conf", "-d", "odoo_factory",
             "--no-http", "--stop-after-init",
             "--test-tags", tags,
             "-u", modules],
            capture_output=True, text=True, timeout=900,
        )

        from .log_parser import parse_test_log
        test_results = parse_test_log(test_result.stdout)

        return Result(success=True, data=test_results)

    def get_installed_modules(self) -> list[str]:
        """Return list of successfully installed modules."""
        return list(self.installed_modules)

    def get_install_history(self) -> list[dict]:
        """Return full install history with timestamps and errors."""
        return list(self.install_order)

    def get_web_url(self) -> str:
        """Return the URL for the user to access the running Odoo instance."""
        return "http://localhost:8069"

    def stop(self) -> None:
        """Stop the persistent instance (data preserved in volumes)."""
        subprocess.run(
            ["docker", "compose", "-f", str(self.compose_file),
             "-p", self.project_name, "stop"],
            capture_output=True, timeout=30,
        )
        self._running = False
        self._save_state()

    def reset(self) -> None:
        """Destroy the persistent instance and all data."""
        subprocess.run(
            ["docker", "compose", "-f", str(self.compose_file),
             "-p", self.project_name, "down", "-v"],
            capture_output=True, timeout=30,
        )
        self._running = False
        self.installed_modules = []
        self.install_order = []
        self._save_state()

    def _health_check(self) -> bool:
        """Check if the Odoo instance is responding."""
        try:
            result = subprocess.run(
                ["docker", "compose", "-f", str(self.compose_file),
                 "-p", self.project_name,
                 "exec", "-T", "odoo", "curl", "-sf", "http://localhost:8069/web/health"],
                capture_output=True, timeout=10,
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False

    def _save_state(self) -> None:
        """Persist state to disk for resume across context resets."""
        if not self._state_dir:
            return
        state_path = self._state_dir / STATE_FILE
        state = {
            "running": self._running,
            "installed_modules": self.installed_modules,
            "install_order": self.install_order,
        }
        state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")

    def _load_state(self) -> None:
        """Load state from disk if available."""
        if not self._state_dir:
            return
        state_path = self._state_dir / STATE_FILE
        if state_path.exists():
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                logger.warning("Corrupt state file %s, resetting: %s", state_path, exc)
                return
            self.installed_modules = list(state.get("installed_modules", []))
            self.install_order = list(state.get("install_order", []))
            self._running = state.get("running", False)
