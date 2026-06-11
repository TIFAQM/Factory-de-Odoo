"""Generated tests must derive Many2one values from the field's comodel,
not hardcode base.main_company (audit P0-3)."""
from __future__ import annotations

import tempfile
from pathlib import Path

from amil_utils.renderer import get_template_dir, render_module


SPEC = {
    "module_name": "uni_m2o_check",
    "module_title": "M2O Check",
    "odoo_version": "19.0",
    "depends": ["base"],
    "models": [{
        "name": "uni.m2o.check",
        "description": "M2O Check",
        "fields": [
            {"name": "name", "type": "Char", "required": True},
            {"name": "advisor_id", "type": "Many2one",
             "comodel_name": "uni.advisor", "required": True},
            {"name": "company_id", "type": "Many2one",
             "comodel_name": "res.company", "required": True},
        ],
    }],
    "security": {},
}


def _render(tmp_path: Path) -> str:
    files, _ = render_module(SPEC, get_template_dir(), tmp_path)
    tests_dir = tmp_path / "uni_m2o_check" / "tests"
    candidates = list(tests_dir.glob("test_*.py"))
    # Exclude __init__.py and behavioral smoke tests
    candidates = [f for f in candidates if f.name != "test_behavioral.py"]
    assert candidates, f"no generated test files in {tests_dir}"
    # Return the model test file specifically
    model_test = tests_dir / "test_uni_m2o_check.py"
    assert model_test.exists(), f"expected {model_test}, got: {[f.name for f in candidates]}"
    return model_test.read_text()


def test_m2o_uses_comodel_helper(tmp_path: Path) -> None:
    content = _render(tmp_path)
    assert 'cls._m2o("uni.advisor")' in content
    assert "def _m2o(" in content


def test_res_company_still_resolves_via_helper(tmp_path: Path) -> None:
    content = _render(tmp_path)
    assert 'cls._m2o("res.company")' in content
    assert 'env.ref("base.main_company")' not in content
