"""Field defaults must render as correct Python: sequence defaults via lambda
(forward reference crashes at import otherwise) and literals unquoted for
bool/int/float. Surfaced by the first real university_base render."""
from __future__ import annotations

from pathlib import Path

from amil_utils.renderer import get_template_dir, render_module


SPEC = {
    "module_name": "uni_defaults_check",
    "module_title": "Defaults Check",
    "odoo_version": "19.0",
    "depends": ["base"],
    "models": [{
        "name": "uni.defaults.check",
        "description": "Defaults Check",
        "fields": [
            {"name": "name", "type": "Char", "required": True},
            {"name": "code", "type": "Char", "required": True},
            {"name": "active", "type": "Boolean", "default": True},
            {"name": "is_open", "type": "Boolean", "default": False},
            {"name": "duration", "type": "Integer", "default": 8},
            {"name": "ratio", "type": "Float", "default": 1.5},
            {"name": "state", "type": "Selection",
             "selection": [["draft", "Draft"], ["done", "Done"]],
             "default": "draft"},
        ],
    }],
    "security": {},
}


def _model_source(tmp_path: Path) -> str:
    render_module(SPEC, get_template_dir(), tmp_path)
    return (tmp_path / "uni_defaults_check" / "models" /
            "uni_defaults_check.py").read_text()


def test_sequence_default_uses_lambda(tmp_path: Path) -> None:
    src = _model_source(tmp_path)
    assert "default=lambda self: self._default_code()" in src
    assert "def _default_code(" in src
    # the broken bare-name form must be gone
    assert "default=_default_code" not in src


def test_boolean_defaults_are_python_literals(tmp_path: Path) -> None:
    src = _model_source(tmp_path)
    assert "default=True," in src
    assert "default=False," in src
    assert 'default="True"' not in src


def test_numeric_defaults_unquoted(tmp_path: Path) -> None:
    src = _model_source(tmp_path)
    assert "default=8," in src
    assert "default=1.5," in src


def test_string_default_stays_quoted(tmp_path: Path) -> None:
    src = _model_source(tmp_path)
    assert 'default="draft",' in src


def test_model_imports_cleanly(tmp_path: Path) -> None:
    """The real failure mode was NameError at class-body execution."""
    src = _model_source(tmp_path)
    stub = (
        "class _F:\n"
        "    def __getattr__(self, n):\n"
        "        return lambda *a, **k: None\n"
        "class _M:\n"
        "    Model = object\n"
        "    AbstractModel = object\n"
        "    TransientModel = object\n"
        "    def Constraint(self, *a, **k):\n"
        "        return None\n"
        "import sys, types\n"
        "odoo = types.ModuleType('odoo')\n"
        "odoo.models = _M(); odoo.fields = _F(); odoo.api = _F()\n"
        "odoo.exceptions = types.ModuleType('odoo.exceptions')\n"
        "odoo.exceptions.ValidationError = Exception\n"
        "odoo.exceptions.UserError = Exception\n"
        "sys.modules['odoo'] = odoo\n"
        "sys.modules['odoo.exceptions'] = odoo.exceptions\n"
    )
    ns: dict = {}
    exec(stub, ns)  # noqa: S102 — controlled test stub
    exec(compile(src, "model.py", "exec"), ns)  # raises NameError pre-fix


def test_constraint_attribute_underscore_prefixed(tmp_path: Path) -> None:
    """Odoo 19 asserts SQL-object attribute names start with '_'."""
    spec = {
        **SPEC,
        "module_name": "uni_constraint_check",
        "models": [{
            "name": "uni.constraint.check",
            "description": "Constraint Check",
            "fields": [
                {"name": "name", "type": "Char", "required": True},
            ],
            "unique_together": [{"fields": ["name"],
                                  "message": "Name must be unique."}],
        }],
    }
    render_module(spec, get_template_dir(), tmp_path)
    src = (tmp_path / "uni_constraint_check" / "models" /
           "uni_constraint_check.py").read_text()
    assert " = models.Constraint(" in src
    import re
    for m in re.finditer(r"^\s{4}(\w+) = models\.Constraint\(", src, re.M):
        assert m.group(1).startswith("_"), m.group(1)


def test_xml_comments_survive_double_dash_descriptions(tmp_path: Path) -> None:
    """'--' in a model description must not break XML comments (menu/demo)."""
    import xml.etree.ElementTree as ET
    spec = {
        **SPEC,
        "module_name": "uni_dashdash_check",
        "models": [{
            "name": "uni.dashdash.check",
            "description": "A challan -- never a manual mark-paid button",
            "fields": [{"name": "name", "type": "Char", "required": True}],
        }],
    }
    render_module(spec, get_template_dir(), tmp_path)
    for xf in (tmp_path / "uni_dashdash_check").rglob("*.xml"):
        ET.parse(xf)


def test_nameless_model_imports_api(tmp_path: Path) -> None:
    """Auto _compute_display_name (19.0 nameless models) emits @api.depends;
    the import line must include api (NameError on live install otherwise)."""
    spec = {
        **SPEC,
        "module_name": "uni_nameless_check",
        "models": [{
            "name": "uni.nameless.check",
            "description": "Nameless",
            "fields": [
                {"name": "section", "type": "Char", "required": True},
            ],
        }],
    }
    render_module(spec, get_template_dir(), tmp_path)
    src = (tmp_path / "uni_nameless_check" / "models" /
           "uni_nameless_check.py").read_text()
    if "@api.depends" in src:
        first = src.splitlines()[0]
        assert "api" in first, first


WF_SPEC = {
    "module_name": "uni_wf_check",
    "module_title": "WF Check",
    "odoo_version": "19.0",
    "depends": ["base"],
    "models": [{
        "name": "uni.wf.check",
        "description": "WF Check",
        "fields": [
            {"name": "name", "type": "Char", "required": True},
            {"name": "state", "type": "Selection",
             "selection": [["draft", "Draft"], ["review", "Review"],
                            ["approved", "Approved"], ["cancelled", "Cancelled"]],
             "default": "draft"},
        ],
        "record_rules": [],
    }],
    "security": {},
    "workflow": [{
        "model": "uni.wf.check",
        "states": ["draft", "review", "approved", "cancelled"],
        "transitions": [
            {"from": "draft", "to": "review", "action": "action_submit"},
            {"from": "review", "to": "approved", "action": "action_approve",
             "group": "university_base.group_university_base_registrar"},
            {"from": "draft", "to": "cancelled", "action": "action_cancel"},
            {"from": "review", "to": "cancelled", "action": "action_cancel"},
        ],
    }],
}


def test_workflow_transition_methods_render(tmp_path: Path) -> None:
    """Spec workflow transitions must materialize as model action methods —
    the PRD state machines were silently dropped before this."""
    render_module(WF_SPEC, get_template_dir(), tmp_path)
    src = (tmp_path / "uni_wf_check" / "models" / "uni_wf_check.py").read_text()
    assert "def action_submit(self):" in src
    assert "def action_approve(self):" in src
    assert "def action_cancel(self):" in src
    # merged sources for the shared cancel action
    assert '"draft", "review"' in src or '"review", "draft"' in src
    # group gate on approve
    assert 'has_group("university_base.group_university_base_registrar")' in src
    assert "AccessError" in src.splitlines()[1] or "AccessError" in src
    assert "from odoo.exceptions import UserError, AccessError" in src


def test_workflow_buttons_render_in_form(tmp_path: Path) -> None:
    render_module(WF_SPEC, get_template_dir(), tmp_path)
    view = (tmp_path / "uni_wf_check" / "views" /
            "uni_wf_check_views.xml").read_text()
    assert '<button name="action_submit"' in view
    assert '<button name="action_approve"' in view
    assert 'groups="university_base.group_university_base_registrar"' in view
    assert 'invisible="state not in' in view


def test_workflow_model_executes(tmp_path: Path) -> None:
    """Rendered model with workflow methods must import cleanly."""
    render_module(WF_SPEC, get_template_dir(), tmp_path)
    src = (tmp_path / "uni_wf_check" / "models" / "uni_wf_check.py").read_text()
    stub = (
        "class _F:\n"
        "    def __getattr__(self, n):\n"
        "        return lambda *a, **k: None\n"
        "class _M:\n"
        "    Model = object\n"
        "    AbstractModel = object\n"
        "    TransientModel = object\n"
        "    def Constraint(self, *a, **k):\n"
        "        return None\n"
        "import sys, types\n"
        "odoo = types.ModuleType('odoo')\n"
        "odoo.models = _M(); odoo.fields = _F(); odoo.api = _F()\n"
        "odoo.exceptions = types.ModuleType('odoo.exceptions')\n"
        "odoo.exceptions.ValidationError = Exception\n"
        "odoo.exceptions.UserError = Exception\n"
        "odoo.exceptions.AccessError = Exception\n"
        "sys.modules['odoo'] = odoo\n"
        "sys.modules['odoo.exceptions'] = odoo.exceptions\n"
    )
    ns: dict = {}
    exec(stub, ns)  # noqa: S102
    exec(compile(src, "model.py", "exec"), ns)
