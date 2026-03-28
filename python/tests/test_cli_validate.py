"""Tests for the validate CLI subcommand."""

from __future__ import annotations

import json
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from amil_utils.cli import main
from amil_utils.commands.validate import _cap_concurrency, execute_validate_parallel
from amil_utils.validation.types import (
    InstallResult,
    Result,
    TestResult,
    ValidationReport,
    Violation,
)


@pytest.fixture()
def runner() -> CliRunner:
    """Create a Click CLI test runner."""
    return CliRunner()


@pytest.fixture()
def module_dir(tmp_path: Path) -> Path:
    """Create a temporary module directory with __manifest__.py."""
    manifest = tmp_path / "__manifest__.py"
    manifest.write_text(
        "{'name': 'Test Module', 'version': '17.0.1.0.0', 'depends': ['base']}",
        encoding="utf-8",
    )
    return tmp_path


class TestValidateHelp:
    """Tests for validate --help."""

    def test_validate_help(self, runner: CliRunner) -> None:
        """validate --help shows usage and expected options."""
        result = runner.invoke(main, ["validate", "--help"])
        assert result.exit_code == 0
        assert "--pylint-only" in result.output
        assert "--json" in result.output
        assert "--pylintrc" in result.output


class TestValidatePylintOnly:
    """Tests for --pylint-only mode."""

    @patch("amil_utils.validation.run_pylint_odoo")
    @patch("amil_utils.validation.check_docker_available")
    @patch("amil_utils.validation.docker_install_module")
    @patch("amil_utils.validation.docker_run_tests")
    def test_validate_pylint_only(
        self,
        mock_docker_tests: MagicMock,
        mock_docker_install: MagicMock,
        mock_docker_check: MagicMock,
        mock_pylint: MagicMock,
        runner: CliRunner,
        module_dir: Path,
    ) -> None:
        """With --pylint-only, only pylint runner is called, no Docker."""
        mock_pylint.return_value = Result.ok(())

        result = runner.invoke(main, ["validate", str(module_dir), "--pylint-only"])

        mock_pylint.assert_called_once()
        mock_docker_check.assert_not_called()
        mock_docker_install.assert_not_called()
        mock_docker_tests.assert_not_called()
        assert result.exit_code == 0


class TestValidateJsonOutput:
    """Tests for --json output."""

    @patch("amil_utils.validation.run_pylint_odoo")
    @patch("amil_utils.validation.check_docker_available")
    def test_validate_json_output(
        self,
        mock_docker_check: MagicMock,
        mock_pylint: MagicMock,
        runner: CliRunner,
        module_dir: Path,
    ) -> None:
        """With --json, output is valid JSON with expected keys."""
        mock_pylint.return_value = Result.ok(())
        mock_docker_check.return_value = False

        result = runner.invoke(main, ["validate", str(module_dir), "--json"])

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "module_name" in data
        assert "pylint_violations" in data
        assert "docker_available" in data


class TestValidateMissingModule:
    """Tests for missing module path."""

    def test_validate_missing_module(self, runner: CliRunner) -> None:
        """When module_path doesn't exist, exits with error."""
        result = runner.invoke(main, ["validate", "/nonexistent/path/to/module"])
        assert result.exit_code != 0


class TestValidateMissingManifest:
    """Tests for missing __manifest__.py."""

    def test_validate_missing_manifest(self, runner: CliRunner, tmp_path: Path) -> None:
        """When module_path exists but has no __manifest__.py, exits with error."""
        result = runner.invoke(main, ["validate", str(tmp_path)])
        assert result.exit_code != 0
        assert "__manifest__.py" in result.output


class TestValidateFullPipeline:
    """Tests for full validation pipeline."""

    @patch("amil_utils.validation.diagnose_errors")
    @patch("amil_utils.validation.docker_run_tests")
    @patch("amil_utils.validation.docker_install_module")
    @patch("amil_utils.validation.check_docker_available")
    @patch("amil_utils.validation.run_pylint_odoo")
    def test_validate_full_pipeline(
        self,
        mock_pylint: MagicMock,
        mock_docker_check: MagicMock,
        mock_docker_install: MagicMock,
        mock_docker_tests: MagicMock,
        mock_diagnose: MagicMock,
        runner: CliRunner,
        module_dir: Path,
    ) -> None:
        """Without flags, calls pylint + docker install + docker tests + diagnosis + report."""
        mock_pylint.return_value = Result.ok(())
        mock_docker_check.return_value = True
        mock_docker_install.return_value = Result.ok(InstallResult(
            success=True, log_output="modules loaded", error_message=""
        ))
        mock_docker_tests.return_value = Result.ok((
            TestResult(test_name="test_create", passed=True),
        ))
        mock_diagnose.return_value = ()

        result = runner.invoke(main, ["validate", str(module_dir)])

        mock_pylint.assert_called_once()
        mock_docker_check.assert_called_once()
        mock_docker_install.assert_called_once()
        mock_docker_tests.assert_called_once()
        assert result.exit_code == 0


class TestValidateDockerUnavailable:
    """Tests for Docker-unavailable graceful degradation."""

    @patch("amil_utils.validation.check_docker_available")
    @patch("amil_utils.validation.run_pylint_odoo")
    def test_validate_docker_unavailable(
        self,
        mock_pylint: MagicMock,
        mock_docker_check: MagicMock,
        runner: CliRunner,
        module_dir: Path,
    ) -> None:
        """When Docker not available, pylint runs but Docker steps show Skipped."""
        mock_pylint.return_value = Result.ok(())
        mock_docker_check.return_value = False

        result = runner.invoke(main, ["validate", str(module_dir)])

        mock_pylint.assert_called_once()
        assert result.exit_code == 0
        assert "Skipped" in result.output or "SKIP" in result.output


class TestValidateExitCodes:
    """Tests for exit codes."""

    @patch("amil_utils.validation.check_docker_available")
    @patch("amil_utils.validation.run_pylint_odoo")
    def test_validate_exit_code_clean(
        self,
        mock_pylint: MagicMock,
        mock_docker_check: MagicMock,
        runner: CliRunner,
        module_dir: Path,
    ) -> None:
        """When no violations and all pass, exit code 0."""
        mock_pylint.return_value = Result.ok(())
        mock_docker_check.return_value = False

        result = runner.invoke(main, ["validate", str(module_dir)])
        assert result.exit_code == 0

    @patch("amil_utils.validation.check_docker_available")
    @patch("amil_utils.validation.run_pylint_odoo")
    def test_validate_exit_code_violations(
        self,
        mock_pylint: MagicMock,
        mock_docker_check: MagicMock,
        runner: CliRunner,
        module_dir: Path,
    ) -> None:
        """When violations found, exit code 1."""
        mock_pylint.return_value = Result.ok((
            Violation(
                file="models/sale.py",
                line=10,
                column=0,
                rule_code="C8101",
                symbol="missing-readme",
                severity="convention",
                message="Missing README",
            ),
        ))
        mock_docker_check.return_value = False

        result = runner.invoke(main, ["validate", str(module_dir)])
        assert result.exit_code == 1


class TestCapConcurrency:
    """Tests for the _cap_concurrency resource guard."""

    def test_cap_concurrency_low_memory(self) -> None:
        """When only 2GB available, cap concurrency to 1."""
        mock_psutil = MagicMock()
        mock_psutil.virtual_memory.return_value = MagicMock(
            available=2 * (1024**3),  # 2 GB
        )
        with patch.dict("sys.modules", {"psutil": mock_psutil}):
            result = _cap_concurrency(3)
        assert result == 1

    def test_cap_concurrency_adequate_memory(self) -> None:
        """When 6GB available, keep requested concurrency of 3."""
        mock_psutil = MagicMock()
        mock_psutil.virtual_memory.return_value = MagicMock(
            available=6 * (1024**3),  # 6 GB
        )
        with patch.dict("sys.modules", {"psutil": mock_psutil}):
            result = _cap_concurrency(3)
        assert result == 3

    def test_cap_concurrency_no_psutil(self) -> None:
        """When psutil is not installed, return requested value unchanged."""
        with patch.dict("sys.modules", {"psutil": None}):
            result = _cap_concurrency(5)
        assert result == 5


class TestDefaultConcurrency:
    """Tests for the updated default concurrency value."""

    def test_default_concurrency_is_3(self) -> None:
        """Verify execute_validate_parallel default concurrency is now 3."""
        import inspect

        sig = inspect.signature(execute_validate_parallel)
        default = sig.parameters["concurrency"].default
        assert default == 3
