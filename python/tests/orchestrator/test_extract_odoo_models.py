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
    # Fixtures define exactly 5 models:
    #   res.partner, sale.order, sale.order.line, sale.mixin, sale.contract
    data = mod.extract_models(FIXTURE)
    assert data["_meta"]["model_count"] == len(data["models"]) == 5


def test_standard_modules_listing() -> None:
    mods = mod.extract_standard_modules(FIXTURE)
    assert mods["sale"]["summary"] == "Quotations and sales orders"
    assert mods["sale"]["depends"] == ["base"]


# C1 — _inherits delegation
def test_inherits_delegation() -> None:
    """sale.contract uses _inherits = {'res.partner': 'partner_id'}.

    After extraction the model must contain:
    - its own field 'contract_ref'
    - its own FK field 'partner_id'
    - the delegated field 'email' from res.partner
    """
    data = mod.extract_models(FIXTURE)
    models = data["models"]
    assert "sale.contract" in models
    fields = models["sale.contract"]["fields"]
    assert "contract_ref" in fields, "own field missing"
    assert "partner_id" in fields, "FK field missing"
    assert "email" in fields, "delegated field from res.partner missing"


# C2 — canonical-module selection on duplicate _name
def test_canonical_module_wins_and_fields_merged() -> None:
    """res.partner is defined in base (canonical) and extended in sale_extra.

    The canonical module should win, but fields from the extension must be
    merged in.
    """
    data = mod.extract_models(FIXTURE)
    models = data["models"]
    assert "res.partner" in models
    rp = models["res.partner"]
    assert rp["module"] == "base", f"expected 'base', got '{rp['module']}'"
    fields = rp["fields"]
    assert "email" in fields, "base field 'email' missing"
    assert "loyalty_points" in fields, "extended field 'loyalty_points' missing"


# I1 — _resolve_inherits unit test
def test_resolve_inherits_standalone() -> None:
    """_resolve_inherits merges parent fields into child at lower priority."""
    models = {
        "parent.model": {"module": "base", "fields": {"foo": {"type": "Char"}, "bar": {"type": "Integer"}}, "is_mixin": False},
        "child.model": {"module": "sale", "fields": {"bar": {"type": "Text"}, "baz": {"type": "Float"}}, "is_mixin": False},
    }
    inherits_map = {"child.model": ["parent.model"]}
    result = mod._resolve_inherits(models, inherits_map)
    child_fields = result["child.model"]["fields"]
    # 'foo' comes from parent (not in child originally)
    assert "foo" in child_fields
    assert child_fields["foo"] == {"type": "Char"}
    # 'bar' keeps child's own definition (child takes priority)
    assert child_fields["bar"] == {"type": "Text"}
    # 'baz' is the child's own field, untouched
    assert child_fields["baz"] == {"type": "Float"}
