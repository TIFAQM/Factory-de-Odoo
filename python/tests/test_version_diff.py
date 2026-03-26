"""Tests for version upgrade diff tool."""
from __future__ import annotations

from pathlib import Path

from amil_utils.version_diff import compute_version_diff


def _make_minimal_spec(odoo_version: str = "19.0") -> dict:
    """Helper to construct a minimal spec for diff testing."""
    return {
        "module_name": "test_diff",
        "depends": ["base"],
        "odoo_version": odoo_version,
        "models": [
            {
                "name": "test.model",
                "description": "Test Model",
                "fields": [
                    {"name": "name", "type": "Char", "required": True},
                    {"name": "description", "type": "Text"},
                ],
            }
        ],
    }


class TestComputeVersionDiff:
    """Tests for compute_version_diff()."""

    def test_same_version_returns_empty_diffs(self, tmp_path: Path):
        """Rendering at same version should produce no diffs."""
        spec = _make_minimal_spec()
        diffs = compute_version_diff(
            spec=spec,
            from_version="19.0",
            to_version="19.0",
            output_base=tmp_path,
        )
        assert len(diffs) == 0

    def test_different_versions_returns_diffs(self, tmp_path: Path):
        """17.0 vs 19.0 should produce at least some diffs."""
        spec = _make_minimal_spec()
        diffs = compute_version_diff(
            spec=spec,
            from_version="17.0",
            to_version="19.0",
            output_base=tmp_path,
        )
        # Should have at least one diff (e.g., tree vs list tag changes)
        assert len(diffs) > 0

    def test_diff_contains_file_path(self, tmp_path: Path):
        """Each diff entry should have a file path."""
        spec = _make_minimal_spec()
        diffs = compute_version_diff(
            spec=spec,
            from_version="17.0",
            to_version="19.0",
            output_base=tmp_path,
        )
        assert len(diffs) > 0, "Expected at least one diff between 17.0 and 19.0"
        for entry in diffs:
            assert "file" in entry
            assert isinstance(entry["file"], str)
            assert len(entry["file"]) > 0

    def test_diff_contains_unified_diff(self, tmp_path: Path):
        """Each diff entry should have diff content in unified diff format."""
        spec = _make_minimal_spec()
        diffs = compute_version_diff(
            spec=spec,
            from_version="17.0",
            to_version="19.0",
            output_base=tmp_path,
        )
        assert len(diffs) > 0, "Expected at least one diff between 17.0 and 19.0"
        for entry in diffs:
            assert "diff" in entry
            assert isinstance(entry["diff"], str)
            # Unified diff format starts with --- and +++
            assert "---" in entry["diff"]
            assert "+++" in entry["diff"]

    def test_diff_file_paths_are_relative(self, tmp_path: Path):
        """File paths in diffs should be relative (no absolute paths)."""
        spec = _make_minimal_spec()
        diffs = compute_version_diff(
            spec=spec,
            from_version="17.0",
            to_version="19.0",
            output_base=tmp_path,
        )
        for entry in diffs:
            assert not entry["file"].startswith("/"), (
                f"File path should be relative, got: {entry['file']}"
            )

    def test_diff_header_contains_version_info(self, tmp_path: Path):
        """Unified diff headers should reference the from/to versions."""
        spec = _make_minimal_spec()
        diffs = compute_version_diff(
            spec=spec,
            from_version="17.0",
            to_version="19.0",
            output_base=tmp_path,
        )
        if diffs:
            first_diff = diffs[0]["diff"]
            assert "17.0" in first_diff
            assert "19.0" in first_diff

    def test_adjacent_versions_may_have_fewer_diffs(self, tmp_path: Path):
        """17.0 vs 18.0 should produce diffs (tree->list change)."""
        spec = _make_minimal_spec()
        diffs = compute_version_diff(
            spec=spec,
            from_version="17.0",
            to_version="18.0",
            output_base=tmp_path,
        )
        assert len(diffs) > 0
