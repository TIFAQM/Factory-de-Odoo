"""UI-parity pass: generated modules must match stock-Odoo workspace
ergonomics — humanized labels, grouped menus under a shared app root,
state kanbans, badges, conditional visibility, and a real module icon.

Driven by a visual audit of the live instance vs stock Maintenance/Fleet.
"""
from __future__ import annotations

from pathlib import Path

from amil_utils.renderer import get_template_dir, render_module


def _spec(**over) -> dict:
    spec = {
        "module_name": "uni_ui_check",
        "module_title": "UI Check",
        "odoo_version": "19.0",
        "depends": ["base", "mail"],
        "models": [
            {
                # operational: has workflow + chatter
                "name": "uni.ui.application",
                "description": "A very long catalogue-style description that "
                               "must never become a breadcrumb title.",
                "chatter": True,
                "fields": [
                    {"name": "name", "type": "Char", "required": True},
                    {"name": "pay_system", "type": "Selection",
                     "selection": [["bps", "BPS"], ["tts", "TTS"]],
                     "default": "bps"},
                    {"name": "bps_grade", "type": "Integer",
                     "invisible_when": "pay_system != 'bps'"},
                    {"name": "state", "type": "Selection",
                     "selection": [["draft", "Draft"], ["done", "Done"]],
                     "default": "draft"},
                ],
                "record_rules": [],
            },
            {
                # config: no workflow, no chatter
                "name": "uni.ui.fee.head",
                "description": "A catalogue of fee components.",
                "chatter": False,
                "fields": [{"name": "name", "type": "Char", "required": True}],
                "record_rules": [],
            },
        ],
        "security": {},
        "workflow": [{
            "model": "uni.ui.application",
            "states": ["draft", "done"],
            "transitions": [
                {"from": "draft", "to": "done", "action": "action_done"},
            ],
        }],
    }
    spec.update(over)
    return spec


def _render(tmp_path: Path, **over) -> Path:
    render_module(_spec(**over), get_template_dir(), tmp_path)
    return tmp_path / "uni_ui_check"


def test_action_names_are_humanized_plurals(tmp_path: Path) -> None:
    mod = _render(tmp_path)
    action = (mod / "views" / "uni_ui_application_action.xml").read_text()
    assert "<field name=\"name\">Applications</field>" in action
    assert "catalogue-style description" not in action.split("help")[0]
    fee = (mod / "views" / "uni_ui_fee_head_action.xml").read_text()
    assert "<field name=\"name\">Fee Heads</field>" in fee


def test_action_help_keeps_description_with_singular_cta(tmp_path: Path) -> None:
    mod = _render(tmp_path)
    action = (mod / "views" / "uni_ui_application_action.xml").read_text()
    assert "o_view_nocontent_smiling_face" in action
    assert "Create your first Application" in action
    # description survives as guidance, not as the title
    assert "catalogue-style description" in action


def test_stateful_action_defaults_to_kanban_grouped_by_state(tmp_path: Path) -> None:
    mod = _render(tmp_path)
    action = (mod / "views" / "uni_ui_application_action.xml").read_text()
    assert ">kanban,list,form" in action
    assert "default_group_by" in action and "state" in action
    fee = (mod / "views" / "uni_ui_fee_head_action.xml").read_text()
    assert ">list,form" in fee  # config models stay list-first


def test_state_kanban_view_rendered_and_in_manifest(tmp_path: Path) -> None:
    mod = _render(tmp_path)
    kanban = mod / "views" / "uni_ui_application_kanban.xml"
    assert kanban.exists()
    content = kanban.read_text()
    assert 'name="state"' in content
    manifest = (mod / "__manifest__.py").read_text()
    assert "views/uni_ui_application_kanban.xml" in manifest
    # config model gets none
    assert not (mod / "views" / "uni_ui_fee_head_kanban.xml").exists()


def test_menus_grouped_operations_then_configuration(tmp_path: Path) -> None:
    mod = _render(tmp_path)
    menu = (mod / "views" / "menu.xml").read_text()
    assert 'name="Applications"' in menu
    assert 'name="Configuration"' in menu
    assert 'name="Fee Heads"' in menu
    # config model hangs under the Configuration submenu
    assert menu.index('name="Applications"') < menu.index('name="Configuration"')
    # no raw descriptions as menu names
    assert "catalogue of fee components" not in menu


def test_shared_app_root_reference(tmp_path: Path) -> None:
    mod = _render(tmp_path, app_root_ref="university_base.menu_university_base_root")
    menu = (mod / "views" / "menu.xml").read_text()
    assert 'parent="university_base.menu_university_base_root"' in menu
    # must NOT create its own root app entry
    assert "menu_uni_ui_check_root" not in menu


def test_provides_app_root_renders_web_icon_root(tmp_path: Path) -> None:
    mod = _render(tmp_path, provides_app_root=True)
    menu = (mod / "views" / "menu.xml").read_text()
    assert "menu_uni_ui_check_root" in menu
    assert "web_icon" in menu


def test_invisible_when_renders_on_form(tmp_path: Path) -> None:
    mod = _render(tmp_path)
    form = (mod / "views" / "uni_ui_application_views.xml").read_text()
    assert "invisible=\"pay_system != 'bps'\"" in form


def test_state_column_uses_badge_widget(tmp_path: Path) -> None:
    mod = _render(tmp_path)
    form = (mod / "views" / "uni_ui_application_views.xml").read_text()
    list_part = form.split("<list", 1)[1]
    assert 'widget="badge"' in list_part


def test_module_icon_png_written(tmp_path: Path) -> None:
    mod = _render(tmp_path)
    icon = mod / "static" / "description" / "icon.png"
    assert icon.exists()
    assert icon.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_nonstandard_state_model_gets_kanban(tmp_path: Path) -> None:
    """payment_status machines (challans) must also get the pipeline kanban
    — manifest and render stage must agree (live upgrade failure)."""
    spec = _spec()
    spec["models"][0]["fields"] = [
        {"name": "name", "type": "Char", "required": True},
        {"name": "payment_status", "type": "Selection",
         "selection": [["unpaid", "Unpaid"], ["paid", "Paid"]],
         "default": "unpaid"},
    ]
    spec["workflow"] = [{
        "model": "uni.ui.application",
        "states": ["unpaid", "paid"],
        "transitions": [{"from": "unpaid", "to": "paid",
                          "action": "action_pay"}],
    }]
    render_module(spec, get_template_dir(), tmp_path)
    mod = tmp_path / "uni_ui_check"
    kanban = mod / "views" / "uni_ui_application_kanban.xml"
    assert kanban.exists()
    assert 'default_group_by="payment_status"' in kanban.read_text()
    manifest = (mod / "__manifest__.py").read_text()
    assert "views/uni_ui_application_kanban.xml" in manifest
