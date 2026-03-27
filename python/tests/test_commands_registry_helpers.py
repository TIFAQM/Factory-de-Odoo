"""Tests for amil_utils.commands.registry_helpers module.

Covers:
- find_registry_path: returns correct relative Path
- parse_module_dir_to_spec: AST-based parsing of Odoo module directories
"""

from __future__ import annotations

from pathlib import Path

import pytest

from amil_utils.commands.registry_helpers import (
    find_registry_path,
    parse_module_dir_to_spec,
)


# ---------------------------------------------------------------------------
# find_registry_path
# ---------------------------------------------------------------------------


class TestFindRegistryPath:
    def test_returns_expected_path(self):
        """Happy path: returns the canonical relative registry path."""
        result = find_registry_path()
        assert result == Path(".planning/model_registry.json")

    def test_returns_path_instance(self):
        """Edge case: return type is always a Path, not a string."""
        result = find_registry_path()
        assert isinstance(result, Path)

    def test_path_has_json_suffix(self):
        """The registry file must be a JSON file."""
        result = find_registry_path()
        assert result.suffix == ".json"


# ---------------------------------------------------------------------------
# parse_module_dir_to_spec
# ---------------------------------------------------------------------------


class TestParseModuleDirToSpec:
    def test_happy_path_parses_model(self, tmp_path: Path):
        """Parse a module directory with a single model and fields."""
        mod_dir = tmp_path / "sale_custom"
        models_dir = mod_dir / "models"
        models_dir.mkdir(parents=True)

        (models_dir / "__init__.py").write_text(
            "from . import sale_order\n", encoding="utf-8"
        )
        (models_dir / "sale_order.py").write_text(
            "from odoo import models, fields\n\n"
            "class SaleOrder(models.Model):\n"
            "    _name = 'sale.custom.order'\n"
            "    _description = 'Custom Sale Order'\n\n"
            "    name = fields.Char(string='Name')\n"
            "    amount = fields.Float(string='Amount')\n"
            "    partner_id = fields.Many2one('res.partner', string='Partner')\n",
            encoding="utf-8",
        )

        manifest = {"depends": ["sale", "base"], "data": []}
        result = parse_module_dir_to_spec("sale_custom", manifest, mod_dir)

        assert result["module_name"] == "sale_custom"
        assert result["depends"] == ["sale", "base"]
        assert len(result["models"]) == 1

        model = result["models"][0]
        assert model["_name"] == "sale.custom.order"
        assert model["description"] == "Custom Sale Order"
        assert "name" in model["fields"]
        assert model["fields"]["name"]["type"] == "Char"
        assert model["fields"]["partner_id"]["type"] == "Many2one"
        assert model["fields"]["partner_id"]["comodel_name"] == "res.partner"

    def test_no_models_directory(self, tmp_path: Path):
        """Edge case: module with no models/ directory returns empty models list."""
        mod_dir = tmp_path / "empty_mod"
        mod_dir.mkdir()

        manifest = {"depends": ["base"]}
        result = parse_module_dir_to_spec("empty_mod", manifest, mod_dir)

        assert result["module_name"] == "empty_mod"
        assert result["models"] == []
        assert result["depends"] == ["base"]

    def test_missing_depends_defaults_to_base(self, tmp_path: Path):
        """Edge case: manifest without depends key defaults to ['base']."""
        mod_dir = tmp_path / "no_dep_mod"
        mod_dir.mkdir()

        manifest = {}  # no 'depends' key
        result = parse_module_dir_to_spec("no_dep_mod", manifest, mod_dir)

        assert result["depends"] == ["base"]

    def test_syntax_error_in_model_file(self, tmp_path: Path):
        """Error handling: files with syntax errors are silently skipped."""
        mod_dir = tmp_path / "bad_mod"
        models_dir = mod_dir / "models"
        models_dir.mkdir(parents=True)

        (models_dir / "broken.py").write_text(
            "class Broken(models.Model\n"  # missing closing paren + colon
            "    _name = 'broken.model'\n",
            encoding="utf-8",
        )

        manifest = {"depends": ["base"]}
        result = parse_module_dir_to_spec("bad_mod", manifest, mod_dir)

        # Should not raise -- broken file is skipped
        assert result["module_name"] == "bad_mod"
        assert result["models"] == []

    def test_skips_init_py(self, tmp_path: Path):
        """Edge case: __init__.py is not treated as a model file."""
        mod_dir = tmp_path / "init_mod"
        models_dir = mod_dir / "models"
        models_dir.mkdir(parents=True)

        (models_dir / "__init__.py").write_text(
            "# Odoo models init\n"
            "class NotAModel:\n"
            "    _name = 'should.not.appear'\n",
            encoding="utf-8",
        )

        manifest = {"depends": ["base"]}
        result = parse_module_dir_to_spec("init_mod", manifest, mod_dir)

        assert result["models"] == []

    def test_multiple_models_in_single_file(self, tmp_path: Path):
        """Happy path: two classes with _name in one file both get parsed."""
        mod_dir = tmp_path / "multi_mod"
        models_dir = mod_dir / "models"
        models_dir.mkdir(parents=True)

        (models_dir / "models.py").write_text(
            "from odoo import models, fields\n\n"
            "class ModelA(models.Model):\n"
            "    _name = 'multi.a'\n"
            "    _description = 'Model A'\n"
            "    val = fields.Integer()\n\n"
            "class ModelB(models.Model):\n"
            "    _name = 'multi.b'\n"
            "    _description = 'Model B'\n"
            "    ref_id = fields.Many2one(comodel_name='multi.a')\n",
            encoding="utf-8",
        )

        manifest = {"depends": ["base"]}
        result = parse_module_dir_to_spec("multi_mod", manifest, mod_dir)

        names = {m["_name"] for m in result["models"]}
        assert names == {"multi.a", "multi.b"}

        b_model = next(m for m in result["models"] if m["_name"] == "multi.b")
        assert b_model["fields"]["ref_id"]["comodel_name"] == "multi.a"

    def test_private_fields_excluded(self, tmp_path: Path):
        """Edge case: fields starting with _ are not included in field list."""
        mod_dir = tmp_path / "priv_mod"
        models_dir = mod_dir / "models"
        models_dir.mkdir(parents=True)

        (models_dir / "model.py").write_text(
            "from odoo import models, fields\n\n"
            "class PrivModel(models.Model):\n"
            "    _name = 'priv.model'\n"
            "    _order = 'name asc'\n"
            "    name = fields.Char()\n"
            "    _sql_constraints = []\n",
            encoding="utf-8",
        )

        manifest = {"depends": ["base"]}
        result = parse_module_dir_to_spec("priv_mod", manifest, mod_dir)

        assert len(result["models"]) == 1
        model = result["models"][0]
        assert "name" in model["fields"]
        # _order and _sql_constraints should NOT appear as fields
        assert "_order" not in model["fields"]
        assert "_sql_constraints" not in model["fields"]

    def test_class_without_name_skipped(self, tmp_path: Path):
        """Edge case: class without _name assignment is not included."""
        mod_dir = tmp_path / "no_name_mod"
        models_dir = mod_dir / "models"
        models_dir.mkdir(parents=True)

        (models_dir / "mixin.py").write_text(
            "from odoo import models, fields\n\n"
            "class MyMixin(models.AbstractModel):\n"
            "    # No _name assignment\n"
            "    active = fields.Boolean(default=True)\n",
            encoding="utf-8",
        )

        manifest = {"depends": ["base"]}
        result = parse_module_dir_to_spec("no_name_mod", manifest, mod_dir)

        assert result["models"] == []

    def test_relational_field_comodel_from_keyword(self, tmp_path: Path):
        """Happy path: comodel_name extracted from keyword argument."""
        mod_dir = tmp_path / "kw_mod"
        models_dir = mod_dir / "models"
        models_dir.mkdir(parents=True)

        (models_dir / "model.py").write_text(
            "from odoo import models, fields\n\n"
            "class KwModel(models.Model):\n"
            "    _name = 'kw.model'\n"
            "    tag_ids = fields.Many2many(comodel_name='kw.tag')\n"
            "    line_ids = fields.One2many('kw.line', 'parent_id')\n",
            encoding="utf-8",
        )

        manifest = {"depends": ["base"]}
        result = parse_module_dir_to_spec("kw_mod", manifest, mod_dir)

        model = result["models"][0]
        assert model["fields"]["tag_ids"]["comodel_name"] == "kw.tag"
        assert model["fields"]["line_ids"]["comodel_name"] == "kw.line"
