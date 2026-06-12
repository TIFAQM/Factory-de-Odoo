"""Generated tests must derive Many2one values from the field's comodel,
not hardcode base.main_company (audit P0-3)."""
from __future__ import annotations

import copy
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
    render_module(SPEC, get_template_dir(), tmp_path)
    tests_dir = tmp_path / "uni_m2o_check" / "tests"
    model_test = tests_dir / "test_uni_m2o_check.py"
    assert model_test.exists(), f"expected {model_test} — no generated test file found in {tests_dir}"
    return model_test.read_text()


def test_m2o_uses_comodel_helper(tmp_path: Path) -> None:
    content = _render(tmp_path)
    assert 'cls._m2o("uni.advisor")' in content
    assert "def _m2o(" in content


def test_res_company_still_resolves_via_helper(tmp_path: Path) -> None:
    content = _render(tmp_path)
    assert 'cls._m2o("res.company")' in content
    assert 'env.ref("base.main_company")' not in content


def test_m2o_search_is_deterministic(tmp_path: Path) -> None:
    """_m2o helper must use order="id asc" for deterministic record selection."""
    content = _render(tmp_path)
    assert 'Model.search([], limit=1, order="id asc")' in content


def test_missing_comodel_fails_loudly_at_render_time(tmp_path: Path) -> None:
    """A Many2one field with no comodel_name must fail loudly — either raising at render time
    or aborting the tests stage — and must never silently produce _m2o("None")."""
    bad_spec = copy.deepcopy(SPEC)
    # Add a Many2one field with no comodel_name or comodel
    bad_spec["models"][0]["fields"].append(
        {"name": "broken_id", "type": "Many2one", "required": True}
    )
    # render_module catches stage errors internally and returns a failed stage;
    # the tests stage is aborted so the model test file is never written.
    files, _warnings = render_module(bad_spec, get_template_dir(), tmp_path)
    tests_dir = tmp_path / "uni_m2o_check" / "tests"
    model_test = tests_dir / "test_uni_m2o_check.py"
    # The guard must prevent any test file from being emitted with _m2o("None")
    if model_test.exists():
        content = model_test.read_text()
        assert '_m2o("None")' not in content, (
            "Guard failed: _m2o(\"None\") found in generated test file — "
            "missing comodel was silently rendered instead of caught"
        )
    else:
        # Preferred outcome: stage aborted, no file written
        test_files = [f for f in files if f.name.startswith("test_uni_m2o_check")]
        assert not test_files, "Stage should have been aborted for missing comodel_name"


def test_m2o_recursively_satisfies_required_fields(tmp_path: Path) -> None:
    """without_demo databases have no rows: the fallback create must fill
    required fields (incl. nested Many2one) or 60% of generated tests error
    in setUpClass (observed on university_base live install)."""
    content = _render(tmp_path)
    assert "_depth" in content
    assert 'f.type == "many2one" and f.comodel_name != comodel' in content
    assert 'fields_get([fname])[fname]["selection"]' in content
