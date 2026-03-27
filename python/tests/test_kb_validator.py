"""Tests for kb_validator module — knowledge base file format validation."""
from __future__ import annotations

from pathlib import Path

import pytest

from amil_utils.kb_validator import MAX_LINES, validate_kb_directory, validate_kb_file


# ===========================================================================
# validate_kb_file
# ===========================================================================


class TestValidateKbFile:
    """Tests for single-file validation."""

    # -- Happy path ---------------------------------------------------------

    def test_valid_file(self, tmp_path):
        """A well-formed KB file passes all checks."""
        content = (
            "# My Rules\n"
            "\n"
            "## Category\n"
            "\n"
            "### Rule 1\n"
            "\n"
            "```python\n"
            "x = 1\n"
            "```\n"
        )
        f = tmp_path / "rules.md"
        f.write_text(content, encoding="utf-8")

        result = validate_kb_file(f)
        assert result["valid"] is True
        assert result["errors"] == []

    def test_multiple_rules_and_code_blocks(self, tmp_path):
        content = (
            "# Rules\n"
            "### Rule A\n"
            "```python\ncode\n```\n"
            "### Rule B\n"
            "```python\ncode\n```\n"
        )
        f = tmp_path / "multi.md"
        f.write_text(content, encoding="utf-8")
        result = validate_kb_file(f)
        assert result["valid"] is True

    # -- Error cases --------------------------------------------------------

    def test_file_not_found(self, tmp_path):
        result = validate_kb_file(tmp_path / "nonexistent.md")
        assert result["valid"] is False
        assert any("not found" in e for e in result["errors"])

    def test_not_a_file(self, tmp_path):
        d = tmp_path / "subdir"
        d.mkdir()
        result = validate_kb_file(d)
        assert result["valid"] is False
        assert any("Not a file" in e for e in result["errors"])

    def test_empty_file(self, tmp_path):
        f = tmp_path / "empty.md"
        f.write_text("", encoding="utf-8")
        result = validate_kb_file(f)
        assert result["valid"] is False
        assert any("empty" in e.lower() for e in result["errors"])

    def test_whitespace_only_file(self, tmp_path):
        f = tmp_path / "blank.md"
        f.write_text("   \n  \n  ", encoding="utf-8")
        result = validate_kb_file(f)
        assert result["valid"] is False
        assert any("empty" in e.lower() for e in result["errors"])

    def test_no_heading_start(self, tmp_path):
        content = "Some text without heading\n### Rule\n```\ncode\n```\n"
        f = tmp_path / "no_head.md"
        f.write_text(content, encoding="utf-8")
        result = validate_kb_file(f)
        assert result["valid"] is False
        assert any("heading" in e.lower() for e in result["errors"])

    def test_no_rule_sections(self, tmp_path):
        content = "# Heading\n\nNo rule sections here\n```\ncode\n```\n"
        f = tmp_path / "no_rules.md"
        f.write_text(content, encoding="utf-8")
        result = validate_kb_file(f)
        assert result["valid"] is False
        assert any("rule sections" in e.lower() for e in result["errors"])

    def test_no_code_blocks(self, tmp_path):
        content = "# Heading\n### Rule 1\nJust text no code\n"
        f = tmp_path / "no_code.md"
        f.write_text(content, encoding="utf-8")
        result = validate_kb_file(f)
        assert result["valid"] is False
        assert any("code block" in e.lower() for e in result["errors"])

    def test_exceeds_max_lines(self, tmp_path):
        lines = ["# Heading\n", "### Rule 1\n", "```\ncode\n```\n"]
        lines.extend([f"line {i}\n" for i in range(MAX_LINES + 10)])
        f = tmp_path / "too_long.md"
        f.write_text("".join(lines), encoding="utf-8")
        result = validate_kb_file(f)
        assert result["valid"] is False
        assert any(str(MAX_LINES) in e for e in result["errors"])

    def test_unclosed_code_block(self, tmp_path):
        content = "# Heading\n### Rule 1\n```python\ncode\n"
        f = tmp_path / "unclosed.md"
        f.write_text(content, encoding="utf-8")
        result = validate_kb_file(f)
        assert result["valid"] is False
        assert any("unclosed" in e.lower() for e in result["errors"])

    # -- Warnings -----------------------------------------------------------

    def test_warning_near_line_limit(self, tmp_path):
        """File at 80%+ of MAX_LINES triggers a warning."""
        line_count = int(MAX_LINES * 0.85)
        lines = ["# Heading\n", "### Rule 1\n", "```\ncode\n```\n"]
        lines.extend([f"line {i}\n" for i in range(line_count - len(lines))])
        f = tmp_path / "near_limit.md"
        f.write_text("".join(lines), encoding="utf-8")
        result = validate_kb_file(f)
        assert result["valid"] is True
        assert any("splitting" in w.lower() for w in result["warnings"])

    def test_warning_rules_exceed_code_blocks(self, tmp_path):
        """More rules than code blocks triggers a warning."""
        content = (
            "# Heading\n"
            "### Rule 1\n"
            "### Rule 2\n"
            "### Rule 3\n"
            "```\ncode\n```\n"
        )
        f = tmp_path / "few_code.md"
        f.write_text(content, encoding="utf-8")
        result = validate_kb_file(f)
        assert result["valid"] is True
        assert any("lack code" in w.lower() for w in result["warnings"])

    # -- Edge cases ---------------------------------------------------------

    def test_heading_after_blank_lines(self, tmp_path):
        """File that starts with blank lines then a heading is valid."""
        content = "\n\n# Heading\n### Rule\n```\ncode\n```\n"
        f = tmp_path / "blanks_first.md"
        f.write_text(content, encoding="utf-8")
        result = validate_kb_file(f)
        assert result["valid"] is True

    def test_result_structure(self, tmp_path):
        """Result always has valid, errors, warnings keys."""
        f = tmp_path / "test.md"
        f.write_text("# H\n### R\n```\nc\n```\n", encoding="utf-8")
        result = validate_kb_file(f)
        assert "valid" in result
        assert "errors" in result
        assert "warnings" in result
        assert isinstance(result["errors"], list)
        assert isinstance(result["warnings"], list)


# ===========================================================================
# validate_kb_directory
# ===========================================================================


class TestValidateKbDirectory:
    def test_nonexistent_directory(self, tmp_path):
        result = validate_kb_directory(tmp_path / "ghost")
        assert result["valid"] is False
        assert result["summary"]["total"] == 0

    def test_not_a_directory(self, tmp_path):
        f = tmp_path / "file.txt"
        f.write_text("text")
        result = validate_kb_directory(f)
        assert result["valid"] is False

    def test_empty_directory(self, tmp_path):
        result = validate_kb_directory(tmp_path)
        assert result["valid"] is True
        assert result["files"] == {}
        assert result["summary"]["total"] == 0

    def test_single_valid_file(self, tmp_path):
        (tmp_path / "rules.md").write_text(
            "# Rules\n### Rule 1\n```\ncode\n```\n", encoding="utf-8"
        )
        result = validate_kb_directory(tmp_path)
        assert result["valid"] is True
        assert result["summary"]["total"] == 1
        assert result["summary"]["valid"] == 1
        assert result["summary"]["invalid"] == 0

    def test_mixed_valid_and_invalid(self, tmp_path):
        (tmp_path / "good.md").write_text(
            "# Good\n### Rule\n```\ncode\n```\n", encoding="utf-8"
        )
        (tmp_path / "bad.md").write_text("", encoding="utf-8")
        result = validate_kb_directory(tmp_path)
        assert result["valid"] is False
        assert result["summary"]["total"] == 2
        assert result["summary"]["valid"] == 1
        assert result["summary"]["invalid"] == 1

    def test_readme_skipped(self, tmp_path):
        (tmp_path / "README.md").write_text("# Docs\nJust docs\n", encoding="utf-8")
        result = validate_kb_directory(tmp_path)
        assert result["valid"] is True
        assert result["summary"]["total"] == 0

    def test_warnings_counted(self, tmp_path):
        """Files with warnings are counted in summary."""
        line_count = int(MAX_LINES * 0.85)
        lines = ["# Heading\n", "### Rule 1\n", "```\ncode\n```\n"]
        lines.extend([f"line {i}\n" for i in range(line_count - len(lines))])
        (tmp_path / "big.md").write_text("".join(lines), encoding="utf-8")
        result = validate_kb_directory(tmp_path)
        assert result["valid"] is True
        assert result["summary"]["warnings"] >= 1

    def test_non_md_files_ignored(self, tmp_path):
        (tmp_path / "notes.txt").write_text("not markdown", encoding="utf-8")
        (tmp_path / "data.json").write_text("{}", encoding="utf-8")
        result = validate_kb_directory(tmp_path)
        assert result["summary"]["total"] == 0
