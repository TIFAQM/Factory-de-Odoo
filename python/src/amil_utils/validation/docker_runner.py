"""Docker lifecycle management for Odoo module validation.

Manages ephemeral Docker Compose environments (Odoo + PostgreSQL) for
module installation and test execution. The Odoo version is configurable
via the ``odoo_version`` parameter (default: ``"19.0"``). Containers are
always torn down after validation, even on errors.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path

from amil_utils.validation.log_parser import parse_install_log, parse_test_log
from amil_utils.validation.types import InstallResult, Result, TestResult

logger = logging.getLogger(__name__)

_VALID_MODULE_NAME = re.compile(r"[a-z][a-z0-9_]+$")

_DOCKER_MAX_RETRY_ATTEMPTS: int = 3
_DOCKER_RETRY_DELAY_SECONDS: float = 2.0
_DB_STARTUP_TIMEOUT_SECONDS: int = 30
_COMPOSE_TIMEOUT_S: int = 120
_INSTALL_TIMEOUT_S: int = 300
_TEST_TIMEOUT_S: int = 600
_DOCKER_INFO_TIMEOUT_S: int = 10
_TEARDOWN_TIMEOUT_S: int = 60

# Only these host env vars are passed to Docker compose subprocesses.
# Prevents leaking secrets (AWS keys, GitHub tokens, etc.) — CWE-200.
_PASSTHROUGH_ENV_VARS = frozenset({
    "PATH", "HOME", "USER", "LANG", "LC_ALL",
    "DOCKER_HOST", "DOCKER_CERT_PATH", "DOCKER_TLS_VERIFY",
    "DOCKER_CONFIG", "COMPOSE_FILE", "COMPOSE_PROJECT_NAME",
    "TMPDIR", "TMP", "TEMP",
    # Factory-specific vars needed by compose files
    "FACTORY_DB_PASSWORD", "ODOO_MAJOR_VERSION", "POSTGRES_VERSION",
})


def _unique_project_name(module_name: str) -> str:
    """Generate a unique Docker Compose project name.

    Returns a name in the format ``factory-{module_name}-{hex8}`` to prevent
    port conflicts between concurrent or orphaned Docker Compose environments.
    """
    return f"factory-{module_name}-{uuid.uuid4().hex[:8]}"


def _validate_module_name(name: str) -> str | None:
    """Validate an Odoo module name.

    Returns None if valid, or an error message if invalid.
    """
    if not _VALID_MODULE_NAME.fullmatch(name):
        return (
            f"Invalid module name '{name}': must start with a lowercase letter "
            f"and contain only lowercase letters, digits, and underscores"
        )
    return None


def check_docker_available() -> bool:
    """Check if Docker CLI is present and functional.

    Returns:
        True if docker is installed and the daemon is reachable.
    """
    if shutil.which("docker") is None:
        return False

    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=_DOCKER_INFO_TIMEOUT_S,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def get_compose_file() -> Path:
    """Return the path to the docker-compose.yml shipped with the package.

    Resolution order:
    1. ``AMIL_COMPOSE_FILE`` environment variable (explicit override).
    2. ``importlib.resources`` lookup inside ``amil_utils/data/``.

    Returns:
        Path to docker-compose.yml.
    """
    env_path = os.environ.get("AMIL_COMPOSE_FILE")
    if env_path:
        return Path(env_path)

    from importlib.resources import files

    ref = files("amil_utils").joinpath("data", "docker-compose.yml")
    return Path(str(ref))


def _run_compose(
    compose_file: Path,
    args: list[str],
    env: dict[str, str],
    timeout: int = _COMPOSE_TIMEOUT_S,
    project_name: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a docker compose command with the given arguments.

    Args:
        compose_file: Path to docker-compose.yml.
        args: Arguments to pass after 'docker compose -f <file>'.
        env: Environment variables to merge with os.environ.
        timeout: Subprocess timeout in seconds.
        project_name: Optional Docker Compose project name (``--project-name``).

    Returns:
        CompletedProcess with stdout and stderr captured as text.
    """
    project_args = ["--project-name", project_name] if project_name else []
    cmd = ["docker", "compose", *project_args, "-f", str(compose_file), *args]
    base_env = {k: v for k, v in os.environ.items() if k in _PASSTHROUGH_ENV_VARS}
    merged_env = {**base_env, **env}
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=merged_env,
    )


def _teardown(
    compose_file: Path,
    env: dict[str, str],
    project_name: str | None = None,
) -> None:
    """Tear down Docker containers and volumes.

    Runs 'docker compose down -v --remove-orphans' with up to 3 retry
    attempts using exponential backoff. Logs to stderr on final failure.
    This function never raises.
    """
    project_args = ["--project-name", project_name] if project_name else []
    cmd = [
        "docker",
        "compose",
        *project_args,
        "-f",
        str(compose_file),
        "down",
        "-v",
        "--remove-orphans",
    ]
    base_env = {k: v for k, v in os.environ.items() if k in _PASSTHROUGH_ENV_VARS}
    merged_env = {**base_env, **env}
    max_attempts = _DOCKER_MAX_RETRY_ATTEMPTS

    for attempt in range(1, max_attempts + 1):
        try:
            subprocess.run(
                cmd,
                capture_output=True,
                timeout=_TEARDOWN_TIMEOUT_S,
                env=merged_env,
            )
            return  # Success — exit immediately
        except Exception as exc:
            if attempt < max_attempts:
                backoff = 2 ** (attempt - 1)  # 1s, 2s
                logger.warning(
                    "Teardown attempt %d/%d failed, retrying in %ds: %s",
                    attempt,
                    max_attempts,
                    backoff,
                    exc,
                )
                logger.debug("Full traceback:", exc_info=True)
                time.sleep(backoff)
            else:
                logger.error(
                    "Teardown failed after %d attempts — containers/volumes may be leaked: %s",
                    max_attempts,
                    exc,
                )
                logger.debug("Full traceback:", exc_info=True)
                # logger.error above already logs; avoid redundant print


def _start_db_with_retry(
    compose_file: Path,
    env: dict[str, str],
    max_attempts: int = _DOCKER_MAX_RETRY_ATTEMPTS,
    timeout: int = _COMPOSE_TIMEOUT_S,
    project_name: str | None = None,
) -> None:
    """Start the database service with retry and teardown between attempts.

    Raises the last exception if all attempts fail.
    """
    for attempt in range(1, max_attempts + 1):
        try:
            _run_compose(
                compose_file,
                ["up", "-d", "--wait", "db"],
                env,
                timeout=timeout,
                project_name=project_name,
            )
            return  # Success
        except Exception as exc:
            if attempt < max_attempts:
                backoff = 2 ** (attempt - 1)  # 1s, 2s
                logger.warning(
                    "DB startup attempt %d/%d failed, tearing down and retrying in %ds: %s",
                    attempt,
                    max_attempts,
                    backoff,
                    exc,
                )
                logger.debug("Full traceback:", exc_info=True)
                _teardown(compose_file, env, project_name=project_name)
                time.sleep(backoff)
            else:
                raise


def docker_install_module(
    module_path: Path,
    compose_file: Path | None = None,
    timeout: int = _INSTALL_TIMEOUT_S,
    odoo_version: str = "19.0",
) -> Result[InstallResult]:
    """Install an Odoo module in an ephemeral Docker environment.

    Starts Odoo + PostgreSQL containers, runs module installation,
    parses the log output for success/failure, and tears down containers.

    Args:
        module_path: Path to the Odoo module directory.
        compose_file: Path to docker-compose.yml. Uses default if None.
        timeout: Timeout in seconds for the install command.
        odoo_version: Odoo version string (e.g. "17.0", "19.0"). The major
            version is extracted and passed as ODOO_MAJOR_VERSION env var
            to the Docker Compose file.

    Returns:
        Result.ok(InstallResult) on successful execution,
        Result.fail(message) on infrastructure errors.
    """
    if not check_docker_available():
        return Result.fail("Docker not available")

    if compose_file is None:
        compose_file = get_compose_file()

    module_name = module_path.name
    name_error = _validate_module_name(module_name)
    if name_error:
        return Result.fail(name_error)

    project_name = _unique_project_name(module_name)
    major = odoo_version.split(".")[0]

    env = {
        "MODULE_PATH": str(module_path.resolve()),
        "MODULE_NAME": module_name,
        "ODOO_MAJOR_VERSION": major,
    }

    try:
        # Pre-cleanup: remove any orphaned containers from previous runs.
        _run_compose(
            compose_file,
            ["down", "--remove-orphans", "-v", "--timeout", "5"],
            env,
            timeout=_DB_STARTUP_TIMEOUT_SECONDS,
            project_name=project_name,
        )

        # Start only the database service with retry for transient failures.
        _start_db_with_retry(compose_file, env, project_name=project_name)

        # Install in a fresh container (no entrypoint server conflict).
        result = _run_compose(
            compose_file,
            [
                "run",
                "--rm",
                "-T",
                "odoo",
                "odoo",
                "-i",
                module_name,
                "-d",
                "test_db",
                "--stop-after-init",
                "--no-http",
                "--log-level=info",
            ],
            env,
            timeout=timeout,
            project_name=project_name,
        )

        combined_output = result.stdout + result.stderr
        success, error_msg = parse_install_log(combined_output)

        return Result.ok(
            InstallResult(
                success=success,
                log_output=combined_output,
                error_message=error_msg,
            )
        )
    except subprocess.TimeoutExpired:
        return Result.fail(f"Timeout after {timeout}s waiting for module install")
    except Exception as exc:
        return Result.fail(str(exc))
    finally:
        _teardown(compose_file, env, project_name=project_name)


def docker_run_tests(
    module_path: Path,
    compose_file: Path | None = None,
    timeout: int = _TEST_TIMEOUT_S,
    odoo_version: str = "19.0",
) -> Result[tuple[TestResult, ...]]:
    """Run Odoo module tests in an ephemeral Docker environment.

    Starts Odoo + PostgreSQL containers, runs module tests with
    --test-enable, parses per-test results from the log output, and
    tears down containers.

    Args:
        module_path: Path to the Odoo module directory.
        compose_file: Path to docker-compose.yml. Uses default if None.
        timeout: Timeout in seconds for the test command.
        odoo_version: Odoo version string (e.g. "17.0", "19.0"). The major
            version is extracted and passed as ODOO_MAJOR_VERSION env var
            to the Docker Compose file.

    Returns:
        Result.ok(test_results) on successful execution,
        Result.fail(message) on infrastructure errors.
    """
    if not check_docker_available():
        return Result.fail("Docker not available")

    if compose_file is None:
        compose_file = get_compose_file()

    module_name = module_path.name
    name_error = _validate_module_name(module_name)
    if name_error:
        return Result.fail(name_error)

    project_name = _unique_project_name(module_name)
    major = odoo_version.split(".")[0]

    env = {
        "MODULE_PATH": str(module_path.resolve()),
        "MODULE_NAME": module_name,
        "ODOO_MAJOR_VERSION": major,
    }

    try:
        # Pre-cleanup: remove any orphaned containers from previous runs.
        _run_compose(
            compose_file,
            ["down", "--remove-orphans", "-v", "--timeout", "5"],
            env,
            timeout=_DB_STARTUP_TIMEOUT_SECONDS,
            project_name=project_name,
        )

        # Start only the database service with retry for transient failures.
        _start_db_with_retry(compose_file, env, project_name=project_name)

        # Run tests in a fresh container (no entrypoint server conflict).
        # --test-tags filters to only this module's tests, avoiding the
        # 900+ base module tests that would otherwise run.
        result = _run_compose(
            compose_file,
            [
                "run",
                "--rm",
                "-T",
                "odoo",
                "odoo",
                "-i",
                module_name,
                "-d",
                "test_db",
                "--test-enable",
                f"--test-tags={module_name}",
                "--stop-after-init",
                "--no-http",
                "--log-level=test",
            ],
            env,
            timeout=timeout,
            project_name=project_name,
        )

        combined_output = result.stdout + result.stderr
        return Result.ok(parse_test_log(combined_output))
    except subprocess.TimeoutExpired:
        logger.warning("Docker test run timed out after %ds", timeout)
        return Result.fail(f"Docker test run timed out after {timeout}s")
    except Exception as exc:
        logger.warning("Docker test run failed: %s", exc)
        logger.debug("Full traceback:", exc_info=True)
        return Result.fail(f"Docker test run failed: {exc}")
    finally:
        _teardown(compose_file, env, project_name=project_name)
