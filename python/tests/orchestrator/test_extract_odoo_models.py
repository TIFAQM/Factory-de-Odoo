"""Tests for scripts/extract_odoo_models.py against the mini fixture tree."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

FIXTURE = Path(__file__).parent.parent / "fixtures" / "odoo_source_mini"
SCRIPT = Path(__file__).parents[3] / "scripts" / "extract_odoo_models.py"

spec = importlib.util.spec_from_file_location("extract_odoo_models", SCRIPT)
mod = importlib.util.module_from_spec(spec)
sys.modules["extract_odoo_models"] = mod
spec.loader.exec_module(mod)


def test_extracts_models_with_module_and_fields() -> None:
    data = mod.extract_models(FIXTURE)
    models = data["models"]
    assert "sale.order" in models
    so = models["sale.order"]
    assert so["module"] == "sale"
    assert so["fields"]["partner_id"] == {
        "type": "Many2one", "comodel_name": "res.partner"}
    assert so["fields"]["company_id"]["comodel_name"] == "res.company"
    assert so["fields"]["amount_total"] == {"type": "Monetary"}


def test_abstract_model_marked_mixin() -> None:
    data = mod.extract_models(FIXTURE)
    assert data["models"]["sale.mixin"]["is_mixin"] is True
    assert data["models"]["sale.order"]["is_mixin"] is False


def test_base_addons_scanned_too() -> None:
    data = mod.extract_models(FIXTURE)
    assert "res.partner" in data["models"]
    assert data["models"]["res.partner"]["module"] == "base"


def test_meta_counts() -> None:
    data = mod.extract_models(FIXTURE)
    assert data["_meta"]["model_count"] == len(data["models"]) >= 4


def test_standard_modules_listing() -> None:
    mods = mod.extract_standard_modules(FIXTURE)
    assert mods["sale"]["summary"] == "Quotations and sales orders"
    assert mods["sale"]["depends"] == ["base"]
