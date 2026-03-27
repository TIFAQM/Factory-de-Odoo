"""Tests for amil_utils.commands.misc module.

Covers:
- execute_show_state: manifest loading, legacy state, no state
- execute_diff_spec: spec comparison
- execute_gen_migration: migration generation
- execute_validate_kb: knowledge base validation
- _kb_lines: helper for formatting KB output
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from amil_utils.commands.misc import (
    _kb_lines,
    execute_diff_spec,
    execute_gen_migration,
    execute_show_state,
    execute_validate_kb,
)


# ---------------------------------------------------------------------------
# execute_show_state
# ---------------------------------------------------------------------------


class TestExecuteShowState:
    def test_manifest_found(self, tmp_path: Path):
        """Happy path: load_manifest returns a manifest object."""
        mock_stage = MagicMock()
        mock_stage.status = "complete"
        mock_stage.duration_ms = 123
        mock_stage.error = None

        mock_artifacts = MagicMock()
        mock_artifacts.total_files = 10
        mock_artifacts.total_lines = 500

        mock_preprocessing = MagicMock()
        mock_preprocessing.preprocessors_run = ["field_labels"]
        mock_preprocessing.duration_ms = 45

        mock_manifest = MagicMock()
        mock_manifest.module = "test_module"
        mock_manifest.generated_at = "2026-01-01T00:00:00"
        mock_manifest.odoo_version = "17.0"
        mock_manifest.spec_sha256 = "abcdef123456789012"
        mock_manifest.artifacts = mock_artifacts
        mock_manifest.stages = {"scaffold": mock_stage}
        mock_manifest.preprocessing = mock_preprocessing
        mock_manifest.models_registered = ["test.model", "test.line"]
        mock_manifest.model_dump.return_value = {"module": "test_module"}

        with patch("amil_utils.manifest.load_manifest", return_value=mock_manifest):
            result = execute_show_state(str(tmp_path))

        assert result["found"] is True
        assert result["legacy"] is False
        assert result["manifest_data"] == {"module": "test_module"}
        assert "test_module" in result["text"]
        assert "17.0" in result["text"]
        assert "[OK]" in result["text"]
        assert "Preprocessors" in result["text"]
        assert "test.model" in result["text"]

    def test_legacy_state_file(self, tmp_path: Path):
        """Edge case: legacy .amil-state.json file exists."""
        (tmp_path / ".amil-state.json").write_text("{}", encoding="utf-8")

        with patch("amil_utils.manifest.load_manifest", return_value=None):
            result = execute_show_state(str(tmp_path))

        assert result["found"] is True
        assert result["legacy"] is True
        assert result["manifest_data"] is None
        assert "Legacy" in result["text"]

    def test_no_state_found(self, tmp_path: Path):
        """Edge case: no manifest and no legacy state."""
        with patch("amil_utils.manifest.load_manifest", return_value=None):
            result = execute_show_state(str(tmp_path))

        assert result["found"] is False
        assert result["legacy"] is False
        assert "No manifest found" in result["text"]

    def test_stage_with_error(self, tmp_path: Path):
        """Error handling: stage with error is displayed."""
        mock_stage = MagicMock()
        mock_stage.status = "failed"
        mock_stage.duration_ms = 50
        mock_stage.error = "Template rendering failed"

        mock_artifacts = MagicMock()
        mock_artifacts.total_files = 0
        mock_artifacts.total_lines = 0

        mock_preprocessing = MagicMock()
        mock_preprocessing.preprocessors_run = []
        mock_preprocessing.duration_ms = 0

        mock_manifest = MagicMock()
        mock_manifest.module = "broken_mod"
        mock_manifest.generated_at = "2026-01-01"
        mock_manifest.odoo_version = "17.0"
        mock_manifest.spec_sha256 = "deadbeef12345678"
        mock_manifest.artifacts = mock_artifacts
        mock_manifest.stages = {"view_gen": mock_stage}
        mock_manifest.preprocessing = mock_preprocessing
        mock_manifest.models_registered = []
        mock_manifest.model_dump.return_value = {}

        with patch("amil_utils.manifest.load_manifest", return_value=mock_manifest):
            result = execute_show_state(str(tmp_path))

        assert result["found"] is True
        assert "[!!]" in result["text"]
        assert "Template rendering failed" in result["text"]


# ---------------------------------------------------------------------------
# execute_diff_spec
# ---------------------------------------------------------------------------


class TestExecuteDiffSpec:
    def test_happy_path(self, tmp_path: Path):
        """Happy path: compare two valid spec files."""
        old_spec = tmp_path / "old.json"
        new_spec = tmp_path / "new.json"

        old_data = {
            "module_name": "test_mod",
            "models": [
                {
                    "name": "test.model",
                    "fields": [
                        {"name": "name", "type": "Char"},
                    ],
                }
            ],
        }
        new_data = {
            "module_name": "test_mod",
            "models": [
                {
                    "name": "test.model",
                    "fields": [
                        {"name": "name", "type": "Char"},
                        {"name": "code", "type": "Char"},
                    ],
                }
            ],
        }

        old_spec.write_text(json.dumps(old_data), encoding="utf-8")
        new_spec.write_text(json.dumps(new_data), encoding="utf-8")

        result = execute_diff_spec(str(old_spec), str(new_spec))

        assert result["error"] is None
        assert isinstance(result["result"], dict)
        assert isinstance(result["human_summary"], str)

    def test_invalid_json(self, tmp_path: Path):
        """Error handling: invalid JSON returns error string."""
        old_spec = tmp_path / "old.json"
        new_spec = tmp_path / "new.json"

        old_spec.write_text("not json!", encoding="utf-8")
        new_spec.write_text("{}", encoding="utf-8")

        result = execute_diff_spec(str(old_spec), str(new_spec))

        assert result["error"] is not None
        assert result["result"] == {}

    def test_missing_file(self, tmp_path: Path):
        """Error handling: non-existent file returns error."""
        result = execute_diff_spec(
            str(tmp_path / "missing_old.json"),
            str(tmp_path / "missing_new.json"),
        )

        assert result["error"] is not None
        assert result["result"] == {}
        assert result["human_summary"] == ""


# ---------------------------------------------------------------------------
# execute_gen_migration
# ---------------------------------------------------------------------------


class TestExecuteGenMigration:
    def test_happy_path_migration_required(self, tmp_path: Path):
        """Happy path: migration is required and generated."""
        old_spec = tmp_path / "old.json"
        new_spec = tmp_path / "new.json"
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        old_data = {
            "module_name": "test_mod",
            "models": [
                {"_name": "test.model", "fields": {"name": {"type": "Char"}}}
            ],
        }
        new_data = {
            "module_name": "test_mod",
            "models": [
                {
                    "_name": "test.model",
                    "fields": {
                        "name": {"type": "Char"},
                        "code": {"type": "Char"},
                    },
                }
            ],
        }

        old_spec.write_text(json.dumps(old_data), encoding="utf-8")
        new_spec.write_text(json.dumps(new_data), encoding="utf-8")

        diff_result = {
            "migration_required": True,
            "destructive_count": 0,
            "changes": {},
        }

        with (
            patch("amil_utils.spec_differ.diff_specs", return_value=diff_result),
            patch("amil_utils.migration_generator.generate_migration") as mock_gen,
        ):
            result = execute_gen_migration(
                str(old_spec), str(new_spec), "17.0.1.0.1", str(output_dir)
            )

        assert result["migration_required"] is True
        assert result["error"] is None
        mock_gen.assert_called_once()

    def test_no_migration_required(self, tmp_path: Path):
        """Edge case: identical specs need no migration."""
        old_spec = tmp_path / "old.json"
        new_spec = tmp_path / "new.json"

        data = {"module_name": "test_mod", "models": []}
        old_spec.write_text(json.dumps(data), encoding="utf-8")
        new_spec.write_text(json.dumps(data), encoding="utf-8")

        diff_result = {"migration_required": False}

        with patch("amil_utils.spec_differ.diff_specs", return_value=diff_result):
            result = execute_gen_migration(
                str(old_spec), str(new_spec), "17.0.1.0.0", str(tmp_path)
            )

        assert result["migration_required"] is False
        assert result["migration_dir"] == ""
        assert result["error"] is None

    def test_invalid_spec_file(self, tmp_path: Path):
        """Error handling: invalid JSON spec returns error."""
        old_spec = tmp_path / "old.json"
        old_spec.write_text("invalid", encoding="utf-8")

        result = execute_gen_migration(
            str(old_spec),
            str(tmp_path / "missing.json"),
            "17.0.1.0.0",
            str(tmp_path),
        )

        assert result["error"] is not None
        assert result["migration_required"] is False


# ---------------------------------------------------------------------------
# execute_validate_kb
# ---------------------------------------------------------------------------


class TestExecuteValidateKb:
    def test_kb_not_found(self, tmp_path: Path):
        """Error handling: no knowledge base directory found."""
        with (
            patch("amil_utils.commands.misc.Path.home", return_value=tmp_path / "nohome"),
            patch("amil_utils.commands.misc.Path.cwd", return_value=tmp_path / "nocwd"),
        ):
            result = execute_validate_kb("all")

        assert result["error"] == "Knowledge base not found."
        assert result["output_lines"] == []

    def test_scope_custom_with_no_custom_dir(self, tmp_path: Path):
        """Edge case: KB exists but has no custom/ subdirectory."""
        kb_dir = tmp_path / "knowledge"
        kb_dir.mkdir()

        with (
            patch("amil_utils.commands.misc.Path.home", return_value=tmp_path / "nohome"),
            patch("amil_utils.commands.misc.Path.cwd", return_value=tmp_path),
        ):
            result = execute_validate_kb("custom")

        assert result["error"] is None
        assert any("No custom/ directory" in line for line in result["output_lines"])

    def test_scope_all_with_valid_files(self, tmp_path: Path):
        """Happy path: validate 'all' scope with KB containing valid files."""
        kb_dir = tmp_path / "knowledge"
        kb_dir.mkdir()
        (kb_dir / "rule1.md").write_text("# Rule 1\nSome content.\n", encoding="utf-8")

        custom_dir = kb_dir / "custom"
        custom_dir.mkdir()
        (custom_dir / "custom_rule.md").write_text("# Custom\nContent.\n", encoding="utf-8")

        mock_validate = MagicMock(
            return_value={
                "valid": True,
                "files": {
                    "rule1.md": {
                        "valid": True,
                        "errors": [],
                        "warnings": [],
                    }
                },
                "summary": {"valid": 1, "invalid": 0, "warnings": 0},
            }
        )

        with (
            patch("amil_utils.commands.misc.Path.home", return_value=tmp_path / "nohome"),
            patch("amil_utils.commands.misc.Path.cwd", return_value=tmp_path),
            patch("amil_utils.kb_validator.validate_kb_directory", mock_validate),
        ):
            result = execute_validate_kb("all")

        assert result["error"] is None
        assert result["has_errors"] is False
        assert any("Validating" in line for line in result["output_lines"])


# ---------------------------------------------------------------------------
# _kb_lines helper
# ---------------------------------------------------------------------------


class TestKbLines:
    def test_valid_file(self):
        """Happy path: valid file produces [+] marker."""
        lines: list[str] = []
        _kb_lines(
            "rule.md",
            {"valid": True, "errors": [], "warnings": []},
            lines,
        )
        assert len(lines) == 1
        assert "[+]" in lines[0]
        assert "VALID" in lines[0]

    def test_invalid_file_with_errors(self):
        """Error handling: invalid file shows [x] and error details."""
        lines: list[str] = []
        _kb_lines(
            "bad_rule.md",
            {"valid": False, "errors": ["Missing heading"], "warnings": ["Short content"]},
            lines,
        )
        assert "[x]" in lines[0]
        assert "INVALID" in lines[0]
        assert any("ERROR" in line for line in lines)
        assert any("WARN" in line for line in lines)

    def test_empty_errors_and_warnings(self):
        """Edge case: invalid file with no error/warning details."""
        lines: list[str] = []
        _kb_lines(
            "empty.md",
            {"valid": False, "errors": [], "warnings": []},
            lines,
        )
        assert len(lines) == 1
        assert "INVALID" in lines[0]
