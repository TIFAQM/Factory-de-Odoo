"""Tests for odoo-ls auto-fix patterns."""

from __future__ import annotations

import ast

from pathlib import Path

from amil_utils.validation.odoo_ls_fixer import (
    fix_missing_manifest_depends,
    dispatch_ols_fix,
)
from amil_utils.validation.types import OLSDiagnostic


class TestFixMissingDepends:
    def test_adds_missing_module(self, tmp_path: Path) -> None:
        module_dir = tmp_path / "my_module"
        module_dir.mkdir()
        (module_dir / "__manifest__.py").write_text(
            '{\n    "name": "My Module",\n    "depends": ["base"],\n}\n'
        )
        diag = OLSDiagnostic(
            "x.py", 5, 0, "OLS30003",
            "Missing dependency 'hr' for comodel 'hr.employee'", 1,
        )
        result = fix_missing_manifest_depends(module_dir, diag)
        assert result is True
        manifest = ast.literal_eval(
            (module_dir / "__manifest__.py").read_text()
        )
        assert "hr" in manifest["depends"]
        assert "base" in manifest["depends"]

    def test_skips_if_already_present(self, tmp_path: Path) -> None:
        module_dir = tmp_path / "my_module"
        module_dir.mkdir()
        (module_dir / "__manifest__.py").write_text(
            '{\n    "name": "My Module",\n    "depends": ["base", "hr"],\n}\n'
        )
        diag = OLSDiagnostic(
            "x.py", 5, 0, "OLS30003",
            "Missing dependency 'hr' for comodel 'hr.employee'", 1,
        )
        result = fix_missing_manifest_depends(module_dir, diag)
        assert result is False

    def test_returns_false_if_no_manifest(self, tmp_path: Path) -> None:
        module_dir = tmp_path / "my_module"
        module_dir.mkdir()
        diag = OLSDiagnostic(
            "x.py", 5, 0, "OLS30003",
            "Missing dependency 'hr'", 1,
        )
        result = fix_missing_manifest_depends(module_dir, diag)
        assert result is False

    def test_returns_false_if_no_module_name_in_message(
        self, tmp_path: Path,
    ) -> None:
        module_dir = tmp_path / "my_module"
        module_dir.mkdir()
        (module_dir / "__manifest__.py").write_text(
            '{"name": "X", "depends": ["base"]}'
        )
        diag = OLSDiagnostic(
            "x.py", 5, 0, "OLS30003",
            "Some other error without module name", 1,
        )
        result = fix_missing_manifest_depends(module_dir, diag)
        assert result is False

    def test_preserves_existing_depends_order(self, tmp_path: Path) -> None:
        """New dependency is appended after existing entries."""
        module_dir = tmp_path / "my_module"
        module_dir.mkdir()
        (module_dir / "__manifest__.py").write_text(
            '{\n    "name": "X",\n    "depends": ["base", "mail"],\n}\n'
        )
        diag = OLSDiagnostic(
            "x.py", 1, 0, "OLS30003",
            "Missing dependency 'sale'", 1,
        )
        result = fix_missing_manifest_depends(module_dir, diag)
        assert result is True
        manifest = ast.literal_eval(
            (module_dir / "__manifest__.py").read_text()
        )
        assert manifest["depends"] == ["base", "mail", "sale"]

    def test_manifest_with_no_depends_key(self, tmp_path: Path) -> None:
        """Manifest without a depends key should return False gracefully."""
        module_dir = tmp_path / "my_module"
        module_dir.mkdir()
        (module_dir / "__manifest__.py").write_text('{"name": "X"}')
        diag = OLSDiagnostic(
            "x.py", 1, 0, "OLS30003",
            "Missing dependency 'hr'", 1,
        )
        result = fix_missing_manifest_depends(module_dir, diag)
        assert result is False


class TestDispatchOlsFix:
    def test_dispatches_ols30003(self, tmp_path: Path) -> None:
        module_dir = tmp_path / "my_module"
        module_dir.mkdir()
        (module_dir / "__manifest__.py").write_text(
            '{"name": "X", "depends": ["base"]}'
        )
        diag = OLSDiagnostic(
            "x.py", 5, 0, "OLS30003",
            "Missing dependency 'sale'", 1,
        )
        result = dispatch_ols_fix(module_dir, diag)
        assert result is True

    def test_returns_false_for_unknown_code(self, tmp_path: Path) -> None:
        diag = OLSDiagnostic("x.py", 1, 0, "OLS99999", "Unknown", 1)
        result = dispatch_ols_fix(tmp_path, diag)
        assert result is False
