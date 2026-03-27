"""Tests for Phase 15 Odoo dev instance infrastructure.

Unit tests validate config files and scripts without Docker.
Integration tests (docker-marked) validate the full stack when Docker is available.
"""

from __future__ import annotations

import ast
import os
import subprocess
import urllib.request
import xmlrpc.client
from pathlib import Path

import pytest

from amil_utils.validation.docker_runner import check_docker_available

# Project root: two parents up from tests/ dir
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Docker skip decorator (same pattern as test_docker_integration.py)
skip_no_docker = pytest.mark.skipif(
    not check_docker_available(),
    reason="Docker daemon not available -- skipping Docker integration tests",
)

# ---------------------------------------------------------------------------
# Unit tests: Config file validation (no Docker needed)
# ---------------------------------------------------------------------------


class TestDevInstanceConfig:
    """Validate Docker Compose config, Odoo conf, and .env defaults."""

    def test_compose_file_exists(self) -> None:
        """docker/dev/docker-compose.yml must exist."""
        compose_path = PROJECT_ROOT / "docker" / "dev" / "docker-compose.yml"
        assert compose_path.is_file(), f"Compose file not found at {compose_path}"

    def test_compose_uses_named_volumes(self) -> None:
        """Compose must use named volumes (odoo-dev-db, odoo-dev-data), not tmpfs."""
        compose_path = PROJECT_ROOT / "docker" / "dev" / "docker-compose.yml"
        content = compose_path.read_text()

        assert "odoo-dev-db" in content, "Missing named volume 'odoo-dev-db'"
        assert "odoo-dev-data" in content, "Missing named volume 'odoo-dev-data'"
        assert "tmpfs" not in content, "Dev compose must not use tmpfs (persistent volumes required)"

    def test_compose_separate_from_validation(self) -> None:
        """Dev compose must have name: odoo-dev; validation compose must NOT."""
        dev_compose = PROJECT_ROOT / "docker" / "dev" / "docker-compose.yml"
        val_compose = PROJECT_ROOT / "docker" / "docker-compose.yml"

        dev_content = dev_compose.read_text()
        assert "name: odoo-dev" in dev_content, "Dev compose must declare 'name: odoo-dev'"

        if val_compose.is_file():
            val_content = val_compose.read_text()
            assert "name: odoo-dev" not in val_content, (
                "Validation compose must NOT contain 'name: odoo-dev'"
            )

    def test_compose_healthcheck_uses_python(self) -> None:
        """Healthcheck must use python3 (not curl), since curl may not be in Odoo image."""
        compose_path = PROJECT_ROOT / "docker" / "dev" / "docker-compose.yml"
        content = compose_path.read_text()

        assert "python3 -c" in content, "Healthcheck must use 'python3 -c' (not curl)"
        # Ensure curl is not used in healthcheck section
        lines = content.splitlines()
        in_healthcheck = False
        for line in lines:
            stripped = line.strip()
            if "healthcheck:" in stripped:
                in_healthcheck = True
            elif in_healthcheck and not stripped.startswith(("-", "test:", "interval:", "timeout:", "retries:", "start_period:", '"')):
                in_healthcheck = False
            if in_healthcheck and "curl" in stripped:
                pytest.fail("Healthcheck must not use curl")

    def test_odoo_conf_settings(self) -> None:
        """Odoo conf must have correct db_name, list_db, and admin_passwd settings."""
        conf_path = PROJECT_ROOT / "docker" / "dev" / "odoo.conf"
        content = conf_path.read_text()

        assert "db_name = odoo_dev" in content, "odoo.conf must set db_name = odoo_dev"
        assert "list_db = False" in content, "odoo.conf must set list_db = False"
        assert "admin_passwd = False" in content, "odoo.conf must set admin_passwd = False (no hardcoded passwords)"

    def test_env_file_defaults(self) -> None:
        """docker/dev/.env must contain ODOO_DEV_PORT=8069."""
        env_path = PROJECT_ROOT / "docker" / "dev" / ".env"
        content = env_path.read_text()

        assert "ODOO_DEV_PORT=8069" in content, ".env must set ODOO_DEV_PORT=8069"


# ---------------------------------------------------------------------------
# Unit tests: Management script validation (no Docker needed)
# ---------------------------------------------------------------------------


class TestManagementScript:
    """Validate the management script content and permissions."""

    def test_script_executable(self) -> None:
        """scripts/odoo-dev.sh must have executable permission."""
        script_path = PROJECT_ROOT / "scripts" / "odoo-dev.sh"
        assert script_path.is_file(), f"Script not found at {script_path}"
        assert os.access(script_path, os.X_OK), "odoo-dev.sh must be executable"

    def test_script_uses_run_for_init(self) -> None:
        """_init_modules must use 'run --rm' (not exec) to avoid serialization failures.

        See CLAUDE.md mistake #4: Two Odoo processes on the same DB cause
        psycopg2.errors.SerializationFailure.
        """
        script_path = PROJECT_ROOT / "scripts" / "odoo-dev.sh"
        content = script_path.read_text()

        # Find the _init_modules function body
        lines = content.splitlines()
        in_init = False
        init_body: list[str] = []
        for line in lines:
            if "_init_modules()" in line or "_init_modules ()" in line:
                in_init = True
                continue
            if in_init:
                if line.startswith("}"):
                    break
                init_body.append(line)

        init_text = "\n".join(init_body)
        assert "run --rm" in init_text, (
            "_init_modules must use 'run --rm', not 'exec' (avoids serialization failures)"
        )

    def test_script_has_all_commands(self) -> None:
        """Management script must support start, stop, status, reset, and logs."""
        script_path = PROJECT_ROOT / "scripts" / "odoo-dev.sh"
        content = script_path.read_text()

        required_commands = ["start", "stop", "status", "reset", "logs"]
        for cmd in required_commands:
            assert cmd in content, f"Script missing command: {cmd}"

    def test_script_bash_syntax_valid(self) -> None:
        """scripts/odoo-dev.sh must pass bash -n syntax check."""
        script_path = PROJECT_ROOT / "scripts" / "odoo-dev.sh"
        result = subprocess.run(
            ["bash", "-n", str(script_path)],
            capture_output=True,
            timeout=10,
        )
        assert result.returncode == 0, (
            f"Bash syntax error in odoo-dev.sh:\n{result.stderr.decode()}"
        )

    def test_verify_script_parseable(self) -> None:
        """scripts/verify-odoo-dev.py must be valid Python (parses without SyntaxError)."""
        verify_path = PROJECT_ROOT / "scripts" / "verify-odoo-dev.py"
        source = verify_path.read_text()
        try:
            ast.parse(source, filename=str(verify_path))
        except SyntaxError as exc:
            pytest.fail(f"verify-odoo-dev.py has syntax error: {exc}")

    def test_verify_script_checks_required_modules(self) -> None:
        """Verify script must reference all 6 required modules."""
        verify_path = PROJECT_ROOT / "scripts" / "verify-odoo-dev.py"
        content = verify_path.read_text()

        required_modules = ["base", "mail", "sale", "purchase", "hr", "account"]
        for mod in required_modules:
            assert mod in content, (
                f"verify-odoo-dev.py missing reference to required module: {mod}"
            )


# ---------------------------------------------------------------------------
# Docker integration tests: Live instance verification
# ---------------------------------------------------------------------------

# XML-RPC connection defaults (matching docker/dev/.env)
_ODOO_DEV_PORT = os.environ.get("ODOO_DEV_PORT", "8069")
_ODOO_URL = f"http://localhost:{_ODOO_DEV_PORT}"
_ODOO_DB = os.environ.get("ODOO_DEV_DB", "odoo_dev")
_ODOO_USER = "admin"
_ODOO_PASSWORD = "admin"


def _wait_for_health(url: str, timeout: int = 120) -> bool:
    """Poll the Odoo health endpoint until it responds or timeout."""
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            resp = urllib.request.urlopen(f"{url}/web/health", timeout=5)
            if resp.status == 200:
                return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(3)
    return False


def _xmlrpc_auth(
    url: str = _ODOO_URL,
    db: str = _ODOO_DB,
    username: str = _ODOO_USER,
    password: str = _ODOO_PASSWORD,
) -> tuple[int, xmlrpc.client.ServerProxy]:
    """Authenticate via XML-RPC, returning (uid, models_proxy)."""
    common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common")
    uid = common.authenticate(db, username, password, {})
    assert uid, f"XML-RPC authentication failed for {username}@{db}"
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")
    return uid, models


@pytest.fixture(scope="class")
def dev_instance():
    """Start the Odoo dev instance, yield its URL, then stop it.

    Uses scripts/odoo-dev.sh for lifecycle management.
    Class-scoped so all tests in TestDevInstanceDocker share one startup cycle.
    """
    script = PROJECT_ROOT / "scripts" / "odoo-dev.sh"

    # Start the instance
    result = subprocess.run(
        [str(script), "start"],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        timeout=300,
    )
    assert result.returncode == 0, (
        f"odoo-dev.sh start failed:\n{result.stderr.decode()}"
    )

    # Wait for health
    url = _ODOO_URL
    healthy = _wait_for_health(url, timeout=120)
    assert healthy, f"Odoo dev instance did not become healthy at {url}"

    yield url

    # Teardown: stop (not reset -- preserve data between runs)
    subprocess.run(
        [str(script), "stop"],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        timeout=60,
    )


class TestDevInstanceDocker:
    """Integration tests requiring Docker daemon.

    All tests use @pytest.mark.docker and @skip_no_docker decorators.
    """

    @pytest.mark.docker
    @skip_no_docker
    @pytest.mark.timeout(120)
    def test_compose_starts_instance(self, dev_instance: str) -> None:
        """Dev instance should start and respond to health endpoint."""
        resp = urllib.request.urlopen(f"{dev_instance}/web/health", timeout=10)
        assert resp.status == 200, f"Health check failed with status {resp.status}"

    @pytest.mark.docker
    @skip_no_docker
    @pytest.mark.timeout(120)
    def test_xmlrpc_connectivity(self, dev_instance: str) -> None:
        """XML-RPC authenticate as admin and query ir.model."""
        uid, models = _xmlrpc_auth(url=dev_instance)
        assert uid, "Expected truthy uid from authentication"

        count = models.execute_kw(
            _ODOO_DB, uid, _ODOO_PASSWORD,
            "ir.model", "search_count", [[]],
        )
        assert count > 0, f"Expected ir.model count > 0, got {count}"

    @pytest.mark.docker
    @skip_no_docker
    @pytest.mark.timeout(120)
    def test_required_modules_installed(self, dev_instance: str) -> None:
        """All 6 required modules must have state=installed."""
        uid, models = _xmlrpc_auth(url=dev_instance)

        installed = models.execute_kw(
            _ODOO_DB, uid, _ODOO_PASSWORD,
            "ir.module.module", "search_read",
            [[["state", "=", "installed"]]],
            {"fields": ["name"]},
        )
        installed_names = {m["name"] for m in installed}
        required = {"base", "mail", "sale", "purchase", "hr", "account"}
        missing = required - installed_names

        assert not missing, f"Missing required modules: {missing}"

    @pytest.mark.docker
    @skip_no_docker
    @pytest.mark.timeout(180)
    def test_data_persistence(self, dev_instance: str) -> None:
        """Data must survive stop/start cycle (named volumes)."""
        script = PROJECT_ROOT / "scripts" / "odoo-dev.sh"

        # Create a test partner
        uid, models = _xmlrpc_auth(url=dev_instance)
        partner_id = models.execute_kw(
            _ODOO_DB, uid, _ODOO_PASSWORD,
            "res.partner", "create",
            [{"name": "amil-test-persistence"}],
        )
        assert partner_id, "Failed to create test partner"

        # Stop the instance
        result = subprocess.run(
            [str(script), "stop"],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            timeout=60,
        )
        assert result.returncode == 0, "odoo-dev.sh stop failed"

        # Re-start the instance
        result = subprocess.run(
            [str(script), "start"],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            timeout=300,
        )
        assert result.returncode == 0, "odoo-dev.sh start failed after stop"

        # Wait for health
        healthy = _wait_for_health(dev_instance, timeout=120)
        assert healthy, "Instance did not become healthy after restart"

        # Re-authenticate and search for the partner
        uid2, models2 = _xmlrpc_auth(url=dev_instance)
        found = models2.execute_kw(
            _ODOO_DB, uid2, _ODOO_PASSWORD,
            "res.partner", "search",
            [[["name", "=", "amil-test-persistence"]]],
        )
        assert len(found) > 0, "Test partner did not persist across stop/start"

        # Clean up: remove the test partner
        models2.execute_kw(
            _ODOO_DB, uid2, _ODOO_PASSWORD,
            "res.partner", "unlink", [found],
        )
