"""End-to-end integration test for odoo-ls validation pipeline.

Requires:
  - odoo_ls_server binary (ODOO_LS_BINARY env var or tools/odoo-ls/odoo_ls_server)
  - Odoo 19.0 source (ODOO_SOURCE_PATH env var or tools/odoo-source/19.0/)

Run with: pytest tests/test_odoo_ls_integration.py -m odoo_ls -v
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Final

import pytest

# Resolve paths relative to project root
_PROJECT_ROOT = Path(__file__).parent.parent.parent  # Factory-de-Odoo/
_DEFAULT_BINARY = _PROJECT_ROOT / "tools" / "odoo-ls" / "odoo_ls_server"
_DEFAULT_SOURCE = _PROJECT_ROOT / "tools" / "odoo-source" / "19.0"

OLS_BINARY = Path(os.environ.get("ODOO_LS_BINARY", str(_DEFAULT_BINARY)))
ODOO_SOURCE = Path(os.environ.get("ODOO_SOURCE_PATH", str(_DEFAULT_SOURCE)))

# Indexing the full Odoo 19.0 source tree can take several minutes on
# slower machines.  Allow overriding via an env-var for CI flexibility.
_INDEX_TIMEOUT: Final[int] = int(os.environ.get("ODOO_LS_INDEX_TIMEOUT", "300"))

skip_no_ols = pytest.mark.skipif(
    not OLS_BINARY.exists(),
    reason=f"odoo_ls_server not found at {OLS_BINARY}",
)
skip_no_source = pytest.mark.skipif(
    not ODOO_SOURCE.exists(),
    reason=f"Odoo 19.0 source not found at {ODOO_SOURCE}",
)


@skip_no_ols
@skip_no_source
@pytest.mark.odoo_ls
class TestOdooLSIntegration:
    """Full pipeline: generate config -> start server -> validate module."""

    def _make_valid_module(self, base_dir: Path) -> Path:
        """Create a minimal valid Odoo module."""
        mod = base_dir / "test_valid"
        mod.mkdir(parents=True)
        (mod / "__manifest__.py").write_text(
            '{"name": "Test Valid", "version": "19.0.1.0.0", '
            '"depends": ["base"], "data": [], "license": "LGPL-3"}',
        )
        (mod / "__init__.py").write_text("from . import models\n")
        models = mod / "models"
        models.mkdir()
        (models / "__init__.py").write_text("from . import test_model\n")
        (models / "test_model.py").write_text(
            'from odoo import fields, models\n\n'
            'class TestValid(models.Model):\n'
            '    _name = "test.valid"\n'
            '    _description = "Test Valid"\n\n'
            '    name = fields.Char(string="Name", required=True)\n'
            '    partner_id = fields.Many2one(comodel_name="res.partner")\n',
        )
        return mod

    def _make_bad_module(self, base_dir: Path) -> Path:
        """Create a module with a known error (nonexistent comodel)."""
        mod = base_dir / "test_bad"
        mod.mkdir(parents=True)
        (mod / "__manifest__.py").write_text(
            '{"name": "Test Bad", "version": "19.0.1.0.0", '
            '"depends": ["base"], "data": [], "license": "LGPL-3"}',
        )
        (mod / "__init__.py").write_text("from . import models\n")
        models = mod / "models"
        models.mkdir()
        (models / "__init__.py").write_text("from . import test_model\n")
        (models / "test_model.py").write_text(
            'from odoo import fields, models\n\n'
            'class TestBad(models.Model):\n'
            '    _name = "test.bad"\n'
            '    _description = "Test Bad"\n\n'
            '    ghost_id = fields.Many2one(comodel_name="nonexistent.model")\n',
        )
        return mod

    def test_server_starts_and_indexes(self, tmp_path: Path) -> None:
        """Server should start, index Odoo source, and become ready."""
        from amil_utils.validation.odoo_ls_client import OdooLSClient
        from amil_utils.validation.odoo_ls_config import generate_odools_toml

        addons_dir = tmp_path / "addons"
        addons_dir.mkdir()

        config = generate_odools_toml(
            output_path=tmp_path / "odools.toml",
            odoo_source_path=ODOO_SOURCE,
            addons_output_dir=addons_dir,
        )

        client = OdooLSClient(
            binary_path=OLS_BINARY,
            config_path=config,
            workspace_root=addons_dir,
            index_timeout=_INDEX_TIMEOUT,
        )
        try:
            client.start()
            assert client.is_alive
        finally:
            client.shutdown()

    def test_valid_module_no_errors(self, tmp_path: Path) -> None:
        """A well-formed module should produce zero errors."""
        from amil_utils.validation.odoo_ls_client import OdooLSClient
        from amil_utils.validation.odoo_ls_config import generate_odools_toml
        from amil_utils.validation.odoo_ls_validator import classify_ols_diagnostics

        addons_dir = tmp_path / "addons"
        self._make_valid_module(addons_dir)

        config = generate_odools_toml(
            output_path=tmp_path / "odools.toml",
            odoo_source_path=ODOO_SOURCE,
            addons_output_dir=addons_dir,
        )

        client = OdooLSClient(
            binary_path=OLS_BINARY,
            config_path=config,
            workspace_root=addons_dir,
            index_timeout=_INDEX_TIMEOUT,
        )
        try:
            client.start()
            diags = client.validate_module(addons_dir / "test_valid")
            classified = classify_ols_diagnostics(diags)
            assert len(classified.errors) == 0, (
                f"Expected 0 errors, got: {[d.message for d in classified.errors]}"
            )
        finally:
            client.shutdown()
