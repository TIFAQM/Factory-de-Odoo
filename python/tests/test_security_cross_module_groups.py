"""External (cross-module) group references must survive rendering intact.

The PRD defines 26 global roles in `university_base`; the other 30 modules
reference them as `university_base.group_*` in field groups and record
rules. These tests prove the security preprocessor passes dotted external
IDs through unchanged end-to-end (spec -> preprocessing -> templates).
"""
from __future__ import annotations

from pathlib import Path

from amil_utils.renderer import get_template_dir, render_module


SPEC = {
    "module_name": "uni_fee",
    "module_title": "University Fees",
    "odoo_version": "19.0",
    "depends": ["base", "university_base"],
    "models": [{
        "name": "uni.fee.challan",
        "description": "Fee Challan",
        "fields": [
            {"name": "name", "type": "Char", "required": True},
            # cross-module field-level security: only the Treasurer role
            # (defined in university_base) may see the bank reference
            {"name": "bank_ref", "type": "Char",
             "groups": "university_base.group_treasurer"},
        ],
        "record_rules": [{
            "name": "registrar_all_challans",
            "group": "university_base.group_registrar",
            "domain_force": "[(1, '=', 1)]",
        }],
    }],
    "security": {},
}


def _render(tmp_path: Path) -> Path:
    render_module(SPEC, get_template_dir(), tmp_path)
    return tmp_path / "uni_fee"


def test_external_field_groups_survive_render(tmp_path: Path) -> None:
    module = _render(tmp_path)
    model_files = list((module / "models").glob("*.py"))
    assert model_files, "no model files rendered"
    content = "".join(f.read_text() for f in model_files)
    assert 'university_base.group_treasurer' in content, (
        "cross-module field groups= reference was lost or rewritten")


def test_external_record_rule_group_survives_render(tmp_path: Path) -> None:
    module = _render(tmp_path)
    security_files = list(module.glob("security/*.xml"))
    assert security_files, "no security XML rendered"
    content = "".join(f.read_text() for f in security_files)
    assert "university_base.group_registrar" in content, (
        "cross-module record-rule group reference was lost or rewritten")
    assert "[(1, '=', 1)]" in content


def test_bare_role_names_still_resolve_locally(tmp_path: Path) -> None:
    """Dotted refs pass through; bare names still resolve module-locally."""
    spec = {
        **SPEC,
        "models": [{
            "name": "uni.fee.challan",
            "description": "Fee Challan",
            "fields": [{"name": "name", "type": "Char", "required": True}],
            "record_rules": [{
                "name": "local_users_rule",
                "group": "user",
                "domain_force": "[(1, '=', 1)]",
            }],
        }],
        "security": {
            "roles": ["user", "manager"],
            "defaults": {"user": "cru", "manager": "crud"},
        },
    }
    render_module(spec, get_template_dir(), tmp_path)
    security_files = list((tmp_path / "uni_fee").glob("security/*.xml"))
    content = "".join(f.read_text() for f in security_files)
    assert "uni_fee.group_uni_fee_user" in content, (
        "bare role name should resolve to the local module group")


def test_explicit_empty_record_rules_disables_autodetect(tmp_path: Path) -> None:
    """record_rules: [] must suppress the department/ownership heuristics —
    auto department rules reference user.department_id which does not exist
    on res.users in CE (live-install failure on university_base)."""
    spec = {
        **SPEC,
        "module_name": "uni_norules_check",
        "models": [{
            "name": "uni.norules.check",
            "description": "No Rules Check",
            "fields": [
                {"name": "name", "type": "Char", "required": True},
                {"name": "department_id", "type": "Many2one",
                 "comodel_name": "university.department"},
                {"name": "user_id", "type": "Many2one",
                 "comodel_name": "res.users"},
            ],
            "record_rules": [],
        }],
    }
    render_module(spec, get_template_dir(), tmp_path)
    rules = tmp_path / "uni_norules_check" / "security" / "record_rules.xml"
    if rules.exists():
        content = rules.read_text()
        assert "department_id" not in content
        assert "rule_" not in content
