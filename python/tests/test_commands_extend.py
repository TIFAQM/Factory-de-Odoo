"""Tests for amil_utils.commands.extend module.

Covers execute_extend_module:
- Happy path with all mocks wired
- Missing GitHub token (needs_auth)
- Clone error handling
- Analysis error handling
- Spec file saving
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from amil_utils.commands.extend import execute_extend_module


# ---------------------------------------------------------------------------
# Fake ModuleAnalysis for mocking
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class _FakeAnalysis:
    module_name: str = "test_module"
    manifest: dict = dataclasses.field(default_factory=lambda: {"name": "Test"})
    model_names: tuple = ("test.model",)
    model_fields: dict = dataclasses.field(
        default_factory=lambda: {"test.model": ("name", "active")}
    )
    field_types: dict = dataclasses.field(
        default_factory=lambda: {"test.model": {"name": "Char", "active": "Boolean"}}
    )
    view_types: dict = dataclasses.field(
        default_factory=lambda: {"test.model": ("form", "tree")}
    )
    security_groups: tuple = ()
    data_files: tuple = ()
    has_wizards: bool = False
    has_tests: bool = True
    inherited_models: tuple = ()


def _mock_analysis():
    return _FakeAnalysis()


def _extend_patches(token, cloned, analysis, companion):
    """Return a combined context manager for all extend mocks."""
    import contextlib

    return contextlib.ExitStack()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestExecuteExtendModule:
    def test_happy_path(self, tmp_path: Path):
        """Happy path: successful clone, analysis, and companion setup."""
        cloned = tmp_path / "cloned" / "test_module"
        cloned.mkdir(parents=True)
        companion = tmp_path / "companion" / "test_module_ext"
        companion.mkdir(parents=True)

        analysis = _mock_analysis()

        with (
            patch(
                "amil_utils.search.get_github_token",
                return_value="ghp_fake_token",
            ),
            patch(
                "amil_utils.search.fork.clone_oca_module",
                return_value=cloned,
            ),
            patch(
                "amil_utils.search.analyzer.analyze_module",
                return_value=analysis,
            ),
            patch(
                "amil_utils.search.analyzer.format_analysis_text",
                return_value="Analysis summary text",
            ),
            patch(
                "amil_utils.search.fork.setup_companion_dir",
                return_value=companion,
            ),
        ):
            result = execute_extend_module(
                module_name="test_module",
                repo="OCA/test-repo",
                output_dir=str(tmp_path / "output"),
            )

        assert result["error"] is None
        assert result["cloned_path"] == str(cloned)
        assert result["companion_path"] == str(companion)
        assert result["analysis"] is analysis
        assert result["analysis_text"] == "Analysis summary text"
        assert result["needs_auth"] is False
        assert result["spec_saved"] is False

    def test_no_github_token(self, tmp_path: Path):
        """Edge case: missing GitHub token sets needs_auth=True and returns early."""
        with patch(
            "amil_utils.search.get_github_token",
            return_value=None,
        ):
            result = execute_extend_module(
                module_name="mod",
                repo="OCA/repo",
                output_dir=str(tmp_path),
            )

        assert result["needs_auth"] is True
        assert result["cloned_path"] == ""
        assert result["error"] is None

    def test_clone_error(self, tmp_path: Path):
        """Error handling: clone failure returns error string."""
        with (
            patch(
                "amil_utils.search.get_github_token",
                return_value="ghp_token",
            ),
            patch(
                "amil_utils.search.fork.clone_oca_module",
                side_effect=RuntimeError("git clone failed"),
            ),
        ):
            result = execute_extend_module(
                module_name="mod",
                repo="OCA/repo",
                output_dir=str(tmp_path),
            )

        assert result["error"] is not None
        assert "cloning" in result["error"].lower()
        assert result["cloned_path"] == ""

    def test_analysis_file_not_found(self, tmp_path: Path):
        """Error handling: missing manifest in cloned module."""
        cloned = tmp_path / "cloned" / "mod"
        cloned.mkdir(parents=True)

        with (
            patch(
                "amil_utils.search.get_github_token",
                return_value="ghp_token",
            ),
            patch(
                "amil_utils.search.fork.clone_oca_module",
                return_value=cloned,
            ),
            patch(
                "amil_utils.search.analyzer.analyze_module",
                side_effect=FileNotFoundError("__manifest__.py not found"),
            ),
        ):
            result = execute_extend_module(
                module_name="mod",
                repo="OCA/repo",
                output_dir=str(tmp_path),
            )

        assert result["error"] is not None
        assert "analyzing" in result["error"].lower()
        assert result["cloned_path"] == str(cloned)

    def test_spec_file_saved(self, tmp_path: Path):
        """Happy path: spec_file is written to companion directory."""
        cloned = tmp_path / "cloned" / "mod"
        cloned.mkdir(parents=True)
        companion = tmp_path / "companion" / "mod_ext"
        companion.mkdir(parents=True)

        analysis = _mock_analysis()
        spec_content = '{"module": "test_spec"}'

        spec_file = tmp_path / "spec.json"
        spec_file.write_text(spec_content, encoding="utf-8")

        with (
            patch(
                "amil_utils.search.get_github_token",
                return_value="ghp_token",
            ),
            patch(
                "amil_utils.search.fork.clone_oca_module",
                return_value=cloned,
            ),
            patch(
                "amil_utils.search.analyzer.analyze_module",
                return_value=analysis,
            ),
            patch(
                "amil_utils.search.analyzer.format_analysis_text",
                return_value="text",
            ),
            patch(
                "amil_utils.search.fork.setup_companion_dir",
                return_value=companion,
            ),
        ):
            result = execute_extend_module(
                module_name="mod",
                repo="OCA/repo",
                output_dir=str(tmp_path),
                spec_file=str(spec_file),
            )

        assert result["spec_saved"] is True
        assert (companion / "spec.json").exists()
        assert (companion / "spec.json").read_text(encoding="utf-8") == spec_content

    def test_custom_branch(self, tmp_path: Path):
        """Happy path: branch parameter is forwarded to clone_oca_module."""
        cloned = tmp_path / "cloned" / "mod"
        cloned.mkdir(parents=True)
        companion = tmp_path / "companion" / "mod_ext"
        companion.mkdir(parents=True)

        analysis = _mock_analysis()
        mock_clone = MagicMock(return_value=cloned)

        with (
            patch(
                "amil_utils.search.get_github_token",
                return_value="ghp_token",
            ),
            patch(
                "amil_utils.search.fork.clone_oca_module",
                mock_clone,
            ),
            patch(
                "amil_utils.search.analyzer.analyze_module",
                return_value=analysis,
            ),
            patch(
                "amil_utils.search.analyzer.format_analysis_text",
                return_value="text",
            ),
            patch(
                "amil_utils.search.fork.setup_companion_dir",
                return_value=companion,
            ),
        ):
            execute_extend_module(
                module_name="mod",
                repo="OCA/repo",
                output_dir=str(tmp_path),
                branch="17.0",
            )

        mock_clone.assert_called_once()
        call_kwargs = mock_clone.call_args
        # branch can be positional or keyword
        assert "17.0" in str(call_kwargs)

    def test_analysis_dict_serializable(self, tmp_path: Path):
        """Happy path: analysis_dict is a plain dict (JSON-serializable)."""
        cloned = tmp_path / "cloned" / "mod"
        cloned.mkdir(parents=True)
        companion = tmp_path / "companion" / "mod_ext"
        companion.mkdir(parents=True)

        analysis = _mock_analysis()

        with (
            patch(
                "amil_utils.search.get_github_token",
                return_value="ghp_token",
            ),
            patch(
                "amil_utils.search.fork.clone_oca_module",
                return_value=cloned,
            ),
            patch(
                "amil_utils.search.analyzer.analyze_module",
                return_value=analysis,
            ),
            patch(
                "amil_utils.search.analyzer.format_analysis_text",
                return_value="text",
            ),
            patch(
                "amil_utils.search.fork.setup_companion_dir",
                return_value=companion,
            ),
        ):
            result = execute_extend_module(
                module_name="mod",
                repo="OCA/repo",
                output_dir=str(tmp_path),
            )

        d = result["analysis_dict"]
        assert isinstance(d, dict)
        assert d["module_name"] == "test_module"
        # model_names should be a list (not tuple) after conversion
        assert isinstance(d["model_names"], list)
