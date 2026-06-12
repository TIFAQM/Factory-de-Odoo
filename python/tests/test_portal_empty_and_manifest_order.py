"""Audit regressions: empty portal blocks must not render portal artifacts,
and portal_rules.xml must load with the security group, before views."""
from __future__ import annotations

import json
from pathlib import Path

from amil_utils.renderer import get_template_dir, render_module


def _base_spec(portal) -> dict:
    return {
        "module_name": "uni_portal_check",
        "module_title": "Portal Check",
        "odoo_version": "19.0",
        "depends": ["base"],
        "models": [{
            "name": "uni.portal.check",
            "description": "Portal Check",
            "fields": [{"name": "name", "type": "Char", "required": True}],
        }],
        "security": {},
        "portal": portal,
    }


def test_empty_portal_block_renders_no_portal_artifacts(tmp_path: Path) -> None:
    render_module(_base_spec({}), get_template_dir(), tmp_path)
    mod = tmp_path / "uni_portal_check"
    assert not (mod / "security" / "portal_rules.xml").exists()
    manifest = (mod / "__manifest__.py").read_text()
    assert "portal_rules" not in manifest


def test_portal_rules_load_before_views_in_manifest(tmp_path: Path) -> None:
    portal = {"pages": [{
        "id": "challans", "model": "uni.portal.check",
        "title": "My Challans", "fields": ["name"],
        "type": "list", "route": "/my/challans", "ownership": "user_id",
    }]}
    render_module(_base_spec(portal), get_template_dir(), tmp_path)
    mod = tmp_path / "uni_portal_check"
    assert (mod / "security" / "portal_rules.xml").exists()
    data = eval((mod / "__manifest__.py").read_text())["data"]
    rules_idx = data.index("security/portal_rules.xml")
    view_indices = [i for i, f in enumerate(data) if f.startswith("views/")]
    assert view_indices, data
    assert rules_idx < min(view_indices), (
        f"portal_rules.xml at {rules_idx} loads after views {data}")
