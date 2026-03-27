"""Live Docker integration tests for Odoo module validation.

These tests run against a real Docker daemon with real Odoo 19.0 + PostgreSQL 16
containers. They validate that docker_install_module() and docker_run_tests() work
end-to-end without mocking.

Skipped when Docker daemon is unavailable.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

logger = logging.getLogger(__name__)

from amil_utils.validation.docker_runner import (
    check_docker_available,
    docker_install_module,
    docker_run_tests,
)

pytestmark = pytest.mark.docker

skip_no_docker = pytest.mark.skipif(
    not check_docker_available(),
    reason="Docker daemon not available -- skipping Docker integration tests",
)

FIXTURE_MODULE = Path(__file__).parent / "fixtures" / "docker_test_module"


@skip_no_docker
def test_check_docker_available() -> None:
    """Sanity check: Docker daemon is reachable and check_docker_available() returns True.

    This verifies the skip mechanism and the function agree under real conditions.
    """
    assert check_docker_available() is True


@skip_no_docker
def test_docker_install_real_module() -> None:
    """Install the fixture module in a live Odoo 19.0 + PostgreSQL 16 container.

    Validates that docker_install_module() returns InstallResult(success=True)
    with non-empty log output when given a valid Odoo module. No mocking used.
    """
    result = docker_install_module(FIXTURE_MODULE)

    assert result.success, f"docker_install_module failed: {result.errors}"
    install = result.data
    logger.info("Log output length: %d chars", len(install.log_output))
    assert install.success is True, f"Module install failed: {install.error_message}"
    assert install.log_output != "", "Expected non-empty log output from install"
    assert install.error_message is None or install.error_message == "", (
        f"Expected no error message, got: {install.error_message}"
    )


@skip_no_docker
def test_docker_run_tests_real_module() -> None:
    """Run the fixture module's tests in a live Odoo 19.0 + PostgreSQL 16 container.

    Validates that docker_run_tests() returns at least one TestResult with
    passed=True. No mocking used.
    """
    result = docker_run_tests(FIXTURE_MODULE)

    assert result.success, f"docker_run_tests failed: {result.errors}"
    results = result.data
    logger.info("Test names found: %s", [r.test_name for r in results])
    assert len(results) > 0, "Expected at least one test result from docker_run_tests"
    assert results[0].passed is True, (
        f"Expected first test to pass, got: {results[0].error_message}"
    )
    assert results[0].test_name != "", "Expected non-empty test name"
