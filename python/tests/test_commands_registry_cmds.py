"""Tests for amil_utils.commands.registry_cmds module.

Covers the Click sub-commands registered via register_registry_commands:
- registry list
- registry show
- registry remove
- registry rebuild
- registry validate
- registry import
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import click
import click.testing
import pytest

from amil_utils.commands.registry_cmds import register_registry_commands


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def cli():
    """Build a minimal Click group with registry sub-commands."""
    grp = click.Group("test")
    register_registry_commands(grp)
    return grp


@pytest.fixture()
def runner():
    return click.testing.CliRunner()


@pytest.fixture()
def empty_registry(tmp_path: Path) -> Path:
    """Return path to a registry file that does not yet exist."""
    return tmp_path / "model_registry.json"


@pytest.fixture()
def populated_registry(tmp_path: Path) -> Path:
    """Create a registry file with one module and two models."""
    reg_path = tmp_path / "model_registry.json"
    data = {
        "_meta": {
            "version": "1.0",
            "last_updated": "2026-01-01T00:00:00+00:00",
            "odoo_version": "17.0",
            "modules_registered": 1,
        },
        "models": {
            "test.order": {
                "module": "test_mod",
                "fields": {
                    "name": {"type": "Char"},
                    "partner_id": {"type": "Many2one", "comodel_name": "res.partner"},
                },
                "inherits": [],
                "mixins": [],
                "description": "Test Order",
            },
            "test.order.line": {
                "module": "test_mod",
                "fields": {
                    "order_id": {"type": "Many2one", "comodel_name": "test.order"},
                    "qty": {"type": "Float"},
                },
                "inherits": [],
                "mixins": [],
                "description": "Order Line",
            },
        },
        "dependency_graph": {"test_mod": ["base"]},
    }
    reg_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return reg_path


def _patch_rp(registry_path: Path):
    """Patch find_registry_path to return the given path."""
    return patch(
        "amil_utils.commands.registry_cmds.find_registry_path",
        return_value=registry_path,
    )


# ---------------------------------------------------------------------------
# registry list
# ---------------------------------------------------------------------------


class TestRegistryList:
    def test_list_empty(self, cli, runner, empty_registry):
        """Happy path: empty registry shows 'No modules registered.'"""
        with _patch_rp(empty_registry):
            result = runner.invoke(cli, ["list"])
        assert result.exit_code == 0
        assert "No modules registered" in result.output

    def test_list_with_modules(self, cli, runner, populated_registry):
        """Happy path: populated registry lists module and model count."""
        with _patch_rp(populated_registry):
            result = runner.invoke(cli, ["list"])
        assert result.exit_code == 0
        assert "test_mod" in result.output
        assert "2" in result.output

    def test_list_json_output(self, cli, runner, populated_registry):
        """Happy path: --json flag produces valid JSON with module data."""
        with _patch_rp(populated_registry):
            result = runner.invoke(cli, ["list", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "test_mod" in data
        assert len(data["test_mod"]) == 2


# ---------------------------------------------------------------------------
# registry show
# ---------------------------------------------------------------------------


class TestRegistryShow:
    def test_show_existing_model(self, cli, runner, populated_registry):
        """Happy path: show a model that exists in the registry."""
        with _patch_rp(populated_registry):
            result = runner.invoke(cli, ["show", "test.order"])
        assert result.exit_code == 0
        assert "test.order" in result.output
        assert "test_mod" in result.output

    def test_show_nonexistent_model(self, cli, runner, populated_registry):
        """Edge case: show a model that does not exist."""
        with _patch_rp(populated_registry):
            result = runner.invoke(cli, ["show", "nonexistent.model"])
        assert result.exit_code == 0
        assert "not found" in result.output.lower()

    def test_show_missing_argument(self, cli, runner, populated_registry):
        """Error handling: missing model_name argument shows an error."""
        with _patch_rp(populated_registry):
            result = runner.invoke(cli, ["show"])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# registry remove
# ---------------------------------------------------------------------------


class TestRegistryRemove:
    def test_remove_existing_module(self, cli, runner, populated_registry):
        """Happy path: remove an existing module."""
        with _patch_rp(populated_registry):
            result = runner.invoke(cli, ["remove", "test_mod"])
        assert result.exit_code == 0
        assert "Removed" in result.output

        # Verify registry was updated on disk
        data = json.loads(populated_registry.read_text(encoding="utf-8"))
        assert "test.order" not in data["models"]

    def test_remove_nonexistent_module(self, cli, runner, populated_registry):
        """Edge case: removing a module that doesn't exist."""
        with _patch_rp(populated_registry):
            result = runner.invoke(cli, ["remove", "ghost_mod"])
        assert result.exit_code == 0
        assert "not found" in result.output.lower()

    def test_remove_missing_argument(self, cli, runner, populated_registry):
        """Error handling: missing module_name argument."""
        with _patch_rp(populated_registry):
            result = runner.invoke(cli, ["remove"])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# registry rebuild
# ---------------------------------------------------------------------------


class TestRegistryRebuild:
    def test_rebuild_scans_modules(self, cli, runner, tmp_path: Path):
        """Happy path: rebuild finds __manifest__.py and builds registry."""
        reg_path = tmp_path / "model_registry.json"

        # Create a module directory with manifest and model
        mod_dir = tmp_path / "scan_root" / "my_module"
        models_dir = mod_dir / "models"
        models_dir.mkdir(parents=True)

        (mod_dir / "__manifest__.py").write_text(
            "{'name': 'My Module', 'depends': ['base']}", encoding="utf-8"
        )
        (models_dir / "my_model.py").write_text(
            "from odoo import models, fields\n\n"
            "class MyModel(models.Model):\n"
            "    _name = 'my.model'\n"
            "    name = fields.Char()\n",
            encoding="utf-8",
        )

        scan_root = str(tmp_path / "scan_root")
        with _patch_rp(reg_path):
            result = runner.invoke(cli, ["rebuild", "--scan-root", scan_root])
        assert result.exit_code == 0
        assert "1 module(s)" in result.output
        assert reg_path.exists()

    def test_rebuild_empty_directory(self, cli, runner, tmp_path: Path):
        """Edge case: rebuild with no modules found."""
        reg_path = tmp_path / "model_registry.json"
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()

        with _patch_rp(reg_path):
            result = runner.invoke(cli, ["rebuild", "--scan-root", str(empty_dir)])
        assert result.exit_code == 0
        assert "0 module(s)" in result.output

    def test_rebuild_skips_bad_manifests(self, cli, runner, tmp_path: Path):
        """Error handling: malformed manifests are skipped."""
        reg_path = tmp_path / "model_registry.json"
        mod_dir = tmp_path / "scan" / "bad_mod"
        mod_dir.mkdir(parents=True)

        (mod_dir / "__manifest__.py").write_text(
            "this is not valid python dict {{{", encoding="utf-8"
        )

        with _patch_rp(reg_path):
            result = runner.invoke(cli, ["rebuild", "--scan-root", str(tmp_path / "scan")])
        assert result.exit_code == 0
        assert "Skip" in result.output
        assert "0 module(s)" in result.output


# ---------------------------------------------------------------------------
# registry validate
# ---------------------------------------------------------------------------


class TestRegistryValidate:
    def test_validate_clean_registry(self, cli, runner, populated_registry):
        """Happy path: valid registry passes validation."""
        with _patch_rp(populated_registry):
            result = runner.invoke(cli, ["validate"])
        assert result.exit_code == 0
        assert "passed" in result.output.lower()

    def test_validate_broken_comodel(self, cli, runner, tmp_path: Path):
        """Edge case: broken comodel reference produces a warning."""
        reg_path = tmp_path / "model_registry.json"
        data = {
            "_meta": {
                "version": "1.0",
                "last_updated": "",
                "odoo_version": "17.0",
                "modules_registered": 1,
            },
            "models": {
                "broken.model": {
                    "module": "broken_mod",
                    "fields": {
                        "bad_ref": {"type": "Many2one", "comodel_name": "missing.model"},
                    },
                    "inherits": [],
                    "mixins": [],
                    "description": "",
                },
            },
            "dependency_graph": {"broken_mod": ["base"]},
        }
        reg_path.write_text(json.dumps(data), encoding="utf-8")

        with _patch_rp(reg_path):
            result = runner.invoke(cli, ["validate"])
        assert "WARNING" in result.output or "warning" in result.output.lower()

    def test_validate_cycle_error(self, cli, runner, tmp_path: Path):
        """Error handling: circular dependencies exit with code 1."""
        reg_path = tmp_path / "model_registry.json"
        data = {
            "_meta": {
                "version": "1.0",
                "last_updated": "",
                "odoo_version": "17.0",
                "modules_registered": 2,
            },
            "models": {},
            "dependency_graph": {"mod_a": ["mod_b"], "mod_b": ["mod_a"]},
        }
        reg_path.write_text(json.dumps(data), encoding="utf-8")

        with _patch_rp(reg_path):
            result = runner.invoke(cli, ["validate"])
        assert result.exit_code == 1
        assert "ERROR" in result.output or "error" in result.output.lower()


# ---------------------------------------------------------------------------
# registry import
# ---------------------------------------------------------------------------


class TestRegistryImport:
    def test_import_manifest(self, cli, runner, tmp_path: Path):
        """Happy path: import a valid manifest with a model."""
        reg_path = tmp_path / "model_registry.json"

        mod_dir = tmp_path / "import_mod"
        models_dir = mod_dir / "models"
        models_dir.mkdir(parents=True)

        (mod_dir / "__manifest__.py").write_text(
            "{'name': 'Import Module', 'depends': ['base', 'mail']}",
            encoding="utf-8",
        )
        (models_dir / "imp_model.py").write_text(
            "from odoo import models, fields\n\n"
            "class ImpModel(models.Model):\n"
            "    _name = 'imp.model'\n"
            "    _description = 'Import Model'\n"
            "    name = fields.Char()\n",
            encoding="utf-8",
        )

        manifest = str(mod_dir / "__manifest__.py")
        with _patch_rp(reg_path):
            result = runner.invoke(cli, ["import", "--from-manifest", manifest])
        assert result.exit_code == 0
        assert "import_mod" in result.output.lower() or "Imported" in result.output

        data = json.loads(reg_path.read_text(encoding="utf-8"))
        assert "imp.model" in data["models"]

    def test_import_invalid_manifest(self, cli, runner, tmp_path: Path):
        """Error handling: malformed manifest exits with error."""
        reg_path = tmp_path / "model_registry.json"

        mod_dir = tmp_path / "bad_import"
        mod_dir.mkdir()
        bad_manifest = mod_dir / "__manifest__.py"
        bad_manifest.write_text("not a dict {{{", encoding="utf-8")

        with _patch_rp(reg_path):
            result = runner.invoke(cli, ["import", "--from-manifest", str(bad_manifest)])
        assert result.exit_code != 0

    def test_import_missing_file(self, cli, runner, tmp_path: Path):
        """Error handling: non-existent manifest path fails."""
        reg_path = tmp_path / "model_registry.json"
        fake_path = str(tmp_path / "nonexistent" / "__manifest__.py")

        with _patch_rp(reg_path):
            result = runner.invoke(cli, ["import", "--from-manifest", fake_path])
        assert result.exit_code != 0
