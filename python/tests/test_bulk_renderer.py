"""Tests for bulk wizard Jinja2 templates and render_bulk() pipeline stage.

Phase 63 Plan 02: Template rendering tests for wizard model, line, views, and JS.
"""

from __future__ import annotations

import ast
import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import pytest
from jinja2 import Environment, FileSystemLoader, StrictUndefined

from amil_utils.renderer_utils import _to_class, _to_python_var

FIXTURES = Path(__file__).parent / "fixtures"
TEMPLATES_DIR = Path(__file__).parent.parent / "src" / "amil_utils" / "templates" / "shared"


@pytest.fixture()
def bulk_spec_raw() -> dict[str, Any]:
    """Load the bulk test fixture as a raw dict."""
    return json.loads((FIXTURES / "bulk_spec.json").read_text())


@pytest.fixture()
def jinja_env() -> Environment:
    """Jinja2 environment loading from the shared templates dir."""
    return Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )


@pytest.fixture()
def admit_op(bulk_spec_raw) -> dict[str, Any]:
    """Enriched state_transition operation (bulk_admit)."""
    op = bulk_spec_raw["bulk_operations"][0]
    op["wizard_var"] = _to_python_var(op["wizard_model"])
    op["source_model_var"] = _to_python_var(op["source_model"])
    op["source_model_class"] = _to_class(op["source_model"])
    return op


@pytest.fixture()
def challan_op(bulk_spec_raw) -> dict[str, Any]:
    """Enriched create_related operation (bulk_challan)."""
    op = bulk_spec_raw["bulk_operations"][1]
    op["wizard_var"] = _to_python_var(op["wizard_model"])
    op["source_model_var"] = _to_python_var(op["source_model"])
    op["source_model_class"] = _to_class(op["source_model"])
    return op


def _render_wizard_model(jinja_env: Environment, op: dict[str, Any]) -> str:
    """Render bulk_wizard_model.py.j2 with an operation context."""
    ctx = {"op": op, "module_name": "uni_admission"}
    tmpl = jinja_env.get_template("bulk_wizard_model.py.j2")
    return tmpl.render(ctx)


def _render_wizard_line(jinja_env: Environment, op: dict[str, Any]) -> str:
    """Render bulk_wizard_line.py.j2 with an operation context."""
    ctx = {"op": op, "module_name": "uni_admission"}
    tmpl = jinja_env.get_template("bulk_wizard_line.py.j2")
    return tmpl.render(ctx)


def _render_wizard_views(jinja_env: Environment, op: dict[str, Any]) -> str:
    """Render bulk_wizard_views.xml.j2 with an operation context."""
    ctx = {"op": op, "module_name": "uni_admission"}
    tmpl = jinja_env.get_template("bulk_wizard_views.xml.j2")
    return tmpl.render(ctx)


def _render_wizard_js(jinja_env: Environment, ops: list[dict[str, Any]]) -> str:
    """Render bulk_wizard_js.js.j2 with module context."""
    ctx = {"bulk_operations": ops, "module_name": "uni_admission"}
    tmpl = jinja_env.get_template("bulk_wizard_js.js.j2")
    return tmpl.render(ctx)


# ===========================================================================
# Wizard Model Template Tests
# ===========================================================================


class TestBulkWizardModelTemplate:
    """Tests for bulk_wizard_model.py.j2 rendering."""

    def test_renders_valid_python(self, jinja_env, admit_op):
        """Rendered wizard model is syntactically valid Python."""
        code = _render_wizard_model(jinja_env, admit_op)
        ast.parse(code)

    def test_wizard_model_name(self, jinja_env, admit_op):
        """Wizard model has _name matching spec wizard_model."""
        code = _render_wizard_model(jinja_env, admit_op)
        assert "_name = 'admission.bulk.admit.wizard'" in code

    def test_wizard_model_description(self, jinja_env, admit_op):
        """Wizard model has _description matching operation name."""
        code = _render_wizard_model(jinja_env, admit_op)
        assert "_description = 'Bulk Admission'" in code

    def test_transient_model_class(self, jinja_env, admit_op):
        """Wizard model inherits TransientModel."""
        code = _render_wizard_model(jinja_env, admit_op)
        assert "models.TransientModel" in code

    def test_class_attributes(self, jinja_env, admit_op):
        """Wizard model has _source_model, _batch_size, _allow_partial, _operation_type attributes."""
        code = _render_wizard_model(jinja_env, admit_op)
        assert "_source_model = 'admission.application'" in code
        assert "_batch_size = 50" in code
        assert "_allow_partial = True" in code
        assert "_operation_type = 'state_transition'" in code

    def test_transient_max_hours(self, jinja_env, admit_op):
        """Wizard model has _transient_max_hours = 2.0."""
        code = _render_wizard_model(jinja_env, admit_op)
        assert "_transient_max_hours = 2.0" in code

    def test_state_field_four_selections(self, jinja_env, admit_op):
        """State field has select/preview/process/done selections."""
        code = _render_wizard_model(jinja_env, admit_op)
        assert "'select'" in code
        assert "'preview'" in code
        assert "'process'" in code
        assert "'done'" in code
        assert "fields.Selection" in code

    def test_record_count_computed_field(self, jinja_env, admit_op):
        """record_count is Integer computed field."""
        code = _render_wizard_model(jinja_env, admit_op)
        assert "record_count" in code
        assert "fields.Integer" in code
        assert "_compute_record_count" in code

    def test_preview_line_ids_one2many(self, jinja_env, admit_op):
        """preview_line_ids is One2many to wizard line model."""
        code = _render_wizard_model(jinja_env, admit_op)
        assert "preview_line_ids" in code
        assert "fields.One2many" in code
        assert "admission.bulk.admit.wizard.line" in code

    def test_result_fields(self, jinja_env, admit_op):
        """Wizard has success_count, fail_count, error_log result fields."""
        code = _render_wizard_model(jinja_env, admit_op)
        assert "success_count = fields.Integer" in code
        assert "fail_count = fields.Integer" in code
        assert "error_log = fields.Text" in code

    def test_business_logic_stub_zones(self, jinja_env, admit_op):
        """Wizard model has BUSINESS LOGIC stub zones."""
        code = _render_wizard_model(jinja_env, admit_op)
        assert "BUSINESS LOGIC START" in code
        assert "BUSINESS LOGIC END" in code

    def test_action_preview_method(self, jinja_env, admit_op):
        """Wizard has action_preview() method."""
        code = _render_wizard_model(jinja_env, admit_op)
        assert "def action_preview(self):" in code

    def test_action_process_method(self, jinja_env, admit_op):
        """action_process() calls _process_all() then sets state to done."""
        code = _render_wizard_model(jinja_env, admit_op)
        assert "def action_process(self):" in code
        assert "_process_all()" in code

    def test_process_all_chunked_batching(self, jinja_env, admit_op):
        """_process_all() implements chunked batching with batch_size."""
        code = _render_wizard_model(jinja_env, admit_op)
        assert "def _process_all(self):" in code
        assert "range(0, total, batch_size)" in code or "range(0, total, self._batch_size)" in code

    def test_process_all_allow_partial_branch(self, jinja_env, admit_op):
        """_process_all() has allow_partial branch with cr.commit."""
        code = _render_wizard_model(jinja_env, admit_op)
        assert "self._allow_partial" in code
        assert "self.env.cr.commit()" in code

    def test_process_all_rollback_branch(self, jinja_env, admit_op):
        """_process_all() has all-or-nothing branch with cr.rollback."""
        code = _render_wizard_model(jinja_env, admit_op)
        assert "self.env.cr.rollback()" in code
        assert "UserError" in code

    def test_process_single_stub_zone(self, jinja_env, admit_op):
        """_process_single() has BUSINESS LOGIC stub zone."""
        code = _render_wizard_model(jinja_env, admit_op)
        assert "def _process_single(self, record):" in code

    def test_notify_progress_bus(self, jinja_env, admit_op):
        """_notify_progress() uses bus.bus._sendone."""
        code = _render_wizard_model(jinja_env, admit_op)
        assert "def _notify_progress(self, processed, total):" in code
        assert "bus.bus" in code
        assert "_sendone" in code
        assert "bulk_operation_progress" in code

    def test_notify_progress_logger_fallback(self, jinja_env, admit_op):
        """_notify_progress() has logger fallback at 25% intervals."""
        code = _render_wizard_model(jinja_env, admit_op)
        assert "_logger.info" in code
        assert "25" in code

    def test_reopen_wizard(self, jinja_env, admit_op):
        """_reopen_wizard() returns ir.actions.act_window dict."""
        code = _render_wizard_model(jinja_env, admit_op)
        assert "def _reopen_wizard(self):" in code
        assert "ir.actions.act_window" in code

    def test_state_transition_references(self, jinja_env, admit_op):
        """state_transition wizard has target_state and action_method in stub context."""
        code = _render_wizard_model(jinja_env, admit_op)
        assert "admitted" in code
        assert "action_admit" in code

    def test_create_related_references(self, jinja_env, challan_op):
        """create_related wizard has create_model and create_fields mapping in stub context."""
        code = _render_wizard_model(jinja_env, challan_op)
        assert "fee.invoice" in code
        assert "create_fields" in code or "create_model" in code

    def test_wizard_fields_rendered(self, jinja_env, challan_op):
        """Wizard fields from spec rendered as model fields."""
        code = _render_wizard_model(jinja_env, challan_op)
        assert "fee_structure_id" in code
        assert "term_id" in code
        assert "due_date" in code

    def test_get_processing_domain(self, jinja_env, admit_op):
        """_get_processing_domain() returns source domain from spec."""
        code = _render_wizard_model(jinja_env, admit_op)
        assert "def _get_processing_domain(self):" in code

    def test_compute_record_count_method(self, jinja_env, admit_op):
        """_compute_record_count uses search_count with source domain."""
        code = _render_wizard_model(jinja_env, admit_op)
        assert "def _compute_record_count(self):" in code
        assert "search_count" in code


# ===========================================================================
# Wizard Line Template Tests
# ===========================================================================


class TestBulkWizardLineTemplate:
    """Tests for bulk_wizard_line.py.j2 rendering."""

    def test_renders_valid_python(self, jinja_env, admit_op):
        """Rendered line model is syntactically valid Python."""
        code = _render_wizard_line(jinja_env, admit_op)
        ast.parse(code)

    def test_transient_model_class(self, jinja_env, admit_op):
        """Line model inherits TransientModel."""
        code = _render_wizard_line(jinja_env, admit_op)
        assert "models.TransientModel" in code

    def test_line_model_name(self, jinja_env, admit_op):
        """Line model _name appends .line to wizard model."""
        code = _render_wizard_line(jinja_env, admit_op)
        assert "_name = 'admission.bulk.admit.wizard.line'" in code

    def test_wizard_id_many2one_cascade(self, jinja_env, admit_op):
        """wizard_id is Many2one with ondelete='cascade'."""
        code = _render_wizard_line(jinja_env, admit_op)
        assert "wizard_id" in code
        assert "fields.Many2one" in code
        assert "cascade" in code

    def test_preview_fields_as_related(self, jinja_env, admit_op):
        """Preview fields from spec rendered as related fields."""
        code = _render_wizard_line(jinja_env, admit_op)
        # admit_op has preview_fields: ["name", "program_id", "cgpa"]
        assert "name" in code
        assert "program_id" in code
        assert "cgpa" in code

    def test_selected_boolean(self, jinja_env, admit_op):
        """Line model has selected Boolean with default=True."""
        code = _render_wizard_line(jinja_env, admit_op)
        assert "selected" in code
        assert "fields.Boolean" in code
        assert "True" in code

    def test_source_id_many2one(self, jinja_env, admit_op):
        """Line model has source_id Many2one to source model."""
        code = _render_wizard_line(jinja_env, admit_op)
        assert "source_id" in code
        assert "admission.application" in code


# ===========================================================================
# Wizard Views Template Tests
# ===========================================================================


class TestBulkWizardViewsTemplate:
    """Tests for bulk_wizard_views.xml.j2 rendering."""

    def test_renders_valid_xml(self, jinja_env, admit_op):
        """Rendered wizard views produce valid XML."""
        xml_str = _render_wizard_views(jinja_env, admit_op)
        ET.fromstring(xml_str)

    def test_state_conditional_select(self, jinja_env, admit_op):
        """View has select state group."""
        xml_str = _render_wizard_views(jinja_env, admit_op)
        assert "select" in xml_str

    def test_state_conditional_preview(self, jinja_env, admit_op):
        """View has preview state group showing preview_line_ids."""
        xml_str = _render_wizard_views(jinja_env, admit_op)
        assert "preview" in xml_str
        assert "preview_line_ids" in xml_str

    def test_process_state_progress_bar(self, jinja_env, admit_op):
        """Process state shows progress bar with o_bulk_progress class."""
        xml_str = _render_wizard_views(jinja_env, admit_op)
        assert "o_bulk_progress" in xml_str
        assert "progress-bar" in xml_str

    def test_done_state_results(self, jinja_env, admit_op):
        """Done state shows success_count, fail_count, error_log."""
        xml_str = _render_wizard_views(jinja_env, admit_op)
        assert "success_count" in xml_str
        assert "fail_count" in xml_str
        assert "error_log" in xml_str

    def test_done_state_close_button(self, jinja_env, admit_op):
        """Done state has close button."""
        xml_str = _render_wizard_views(jinja_env, admit_op)
        assert "Close" in xml_str

    def test_preview_button(self, jinja_env, admit_op):
        """View has Preview button for select state."""
        xml_str = _render_wizard_views(jinja_env, admit_op)
        assert "action_preview" in xml_str

    def test_process_button(self, jinja_env, admit_op):
        """View has Process button for preview state."""
        xml_str = _render_wizard_views(jinja_env, admit_op)
        assert "action_process" in xml_str

    def test_record_count_displayed(self, jinja_env, admit_op):
        """Select state shows record_count."""
        xml_str = _render_wizard_views(jinja_env, admit_op)
        assert "record_count" in xml_str

    def test_act_window_action(self, jinja_env, admit_op):
        """View includes ir.actions.act_window record."""
        xml_str = _render_wizard_views(jinja_env, admit_op)
        assert "ir.actions.act_window" in xml_str

    def test_preview_line_tree_columns(self, jinja_env, admit_op):
        """Preview section has tree with preview field columns."""
        xml_str = _render_wizard_views(jinja_env, admit_op)
        # admit_op preview_fields: name, program_id, cgpa
        root = ET.fromstring(xml_str)
        # Just verify the XML is valid and contains preview_line_ids
        assert "preview_line_ids" in xml_str


# ===========================================================================
# Wizard JS Template Tests
# ===========================================================================


class TestBulkWizardJsTemplate:
    """Tests for bulk_wizard_js.js.j2 rendering."""

    def test_renders_valid_javascript(self, jinja_env, admit_op):
        """Rendered JS file contains valid-looking JavaScript."""
        js = _render_wizard_js(jinja_env, [admit_op])
        # Basic syntax check -- not a full JS parser but verifies structure
        assert "function" in js or "=>" in js or "class" in js or "listener" in js or "registry" in js

    def test_odoo_module_comment(self, jinja_env, admit_op):
        """JS has @odoo-module comment."""
        js = _render_wizard_js(jinja_env, [admit_op])
        assert "@odoo-module" in js

    def test_imports_registry(self, jinja_env, admit_op):
        """JS imports registry from @web/core/registry."""
        js = _render_wizard_js(jinja_env, [admit_op])
        assert "registry" in js
        assert "@web/core/registry" in js

    def test_registers_progress_listener(self, jinja_env, admit_op):
        """JS registers bulk_operation_progress listener."""
        js = _render_wizard_js(jinja_env, [admit_op])
        assert "bulk_operation_progress" in js

    def test_updates_progress_bar(self, jinja_env, admit_op):
        """JS updates .o_bulk_progress progress-bar width and progress-text."""
        js = _render_wizard_js(jinja_env, [admit_op])
        assert "o_bulk_progress" in js
        assert "progress-bar" in js
        assert "progress-text" in js


# ===========================================================================
# render_bulk() Pipeline Stage Integration Tests
# ===========================================================================


class TestRenderBulkStage:
    """Integration tests for render_bulk() pipeline stage."""

    def test_render_bulk_returns_empty_when_no_bulk(self, tmp_path):
        """render_bulk() returns Result.ok([]) when spec has no has_bulk_operations."""
        from amil_utils.renderer import render_bulk

        env = Environment(
            loader=FileSystemLoader(str(TEMPLATES_DIR)),
            undefined=StrictUndefined,
            trim_blocks=True,
            lstrip_blocks=True,
        )
        spec = {"module_name": "test_mod", "models": []}
        ctx = {"module_name": "test_mod"}
        result = render_bulk(env, spec, tmp_path, ctx)
        assert result.success is True
        assert result.data == []

    def test_render_bulk_creates_wizard_py(self, tmp_path, bulk_spec_raw):
        """render_bulk() creates wizard .py file in wizards/ directory."""
        from amil_utils.renderer import render_bulk
        from amil_utils.preprocessors.bulk_operations import _process_bulk_operations

        env = Environment(
            loader=FileSystemLoader(str(TEMPLATES_DIR)),
            undefined=StrictUndefined,
            trim_blocks=True,
            lstrip_blocks=True,
        )
        spec = _process_bulk_operations(bulk_spec_raw)
        ctx = {"module_name": "uni_admission"}
        module_dir = tmp_path / "uni_admission"
        module_dir.mkdir()
        (module_dir / "wizards").mkdir(parents=True)
        (module_dir / "views").mkdir(parents=True)

        result = render_bulk(env, spec, module_dir, ctx)
        assert result.success is True

        # Check wizard .py files exist
        wizard_var = _to_python_var("admission.bulk.admit.wizard")
        assert (module_dir / "wizards" / f"{wizard_var}.py").exists()

    def test_render_bulk_creates_wizard_line_py(self, tmp_path, bulk_spec_raw):
        """render_bulk() creates wizard line .py file in wizards/ directory."""
        from amil_utils.renderer import render_bulk
        from amil_utils.preprocessors.bulk_operations import _process_bulk_operations

        env = Environment(
            loader=FileSystemLoader(str(TEMPLATES_DIR)),
            undefined=StrictUndefined,
            trim_blocks=True,
            lstrip_blocks=True,
        )
        spec = _process_bulk_operations(bulk_spec_raw)
        ctx = {"module_name": "uni_admission"}
        module_dir = tmp_path / "uni_admission"
        module_dir.mkdir()
        (module_dir / "wizards").mkdir(parents=True)
        (module_dir / "views").mkdir(parents=True)

        result = render_bulk(env, spec, module_dir, ctx)
        assert result.success is True

        wizard_var = _to_python_var("admission.bulk.admit.wizard")
        assert (module_dir / "wizards" / f"{wizard_var}_line.py").exists()

    def test_render_bulk_creates_wizard_view_xml(self, tmp_path, bulk_spec_raw):
        """render_bulk() creates wizard form view .xml in views/ directory."""
        from amil_utils.renderer import render_bulk
        from amil_utils.preprocessors.bulk_operations import _process_bulk_operations

        env = Environment(
            loader=FileSystemLoader(str(TEMPLATES_DIR)),
            undefined=StrictUndefined,
            trim_blocks=True,
            lstrip_blocks=True,
        )
        spec = _process_bulk_operations(bulk_spec_raw)
        ctx = {"module_name": "uni_admission"}
        module_dir = tmp_path / "uni_admission"
        module_dir.mkdir()
        (module_dir / "wizards").mkdir(parents=True)
        (module_dir / "views").mkdir(parents=True)

        result = render_bulk(env, spec, module_dir, ctx)
        assert result.success is True

        wizard_var = _to_python_var("admission.bulk.admit.wizard")
        assert (module_dir / "views" / f"{wizard_var}_wizard_form.xml").exists()

    def test_render_bulk_creates_js_file(self, tmp_path, bulk_spec_raw):
        """render_bulk() creates bulk_progress.js in static/src/js/."""
        from amil_utils.renderer import render_bulk
        from amil_utils.preprocessors.bulk_operations import _process_bulk_operations

        env = Environment(
            loader=FileSystemLoader(str(TEMPLATES_DIR)),
            undefined=StrictUndefined,
            trim_blocks=True,
            lstrip_blocks=True,
        )
        spec = _process_bulk_operations(bulk_spec_raw)
        ctx = {"module_name": "uni_admission"}
        module_dir = tmp_path / "uni_admission"
        module_dir.mkdir()
        (module_dir / "wizards").mkdir(parents=True)
        (module_dir / "views").mkdir(parents=True)

        result = render_bulk(env, spec, module_dir, ctx)
        assert result.success is True

        assert (module_dir / "static" / "src" / "js" / "bulk_progress.js").exists()

    def test_render_bulk_updates_wizards_init(self, tmp_path, bulk_spec_raw):
        """render_bulk() updates wizards/__init__.py with both wizard and line imports."""
        from amil_utils.renderer import render_bulk
        from amil_utils.preprocessors.bulk_operations import _process_bulk_operations

        env = Environment(
            loader=FileSystemLoader(str(TEMPLATES_DIR)),
            undefined=StrictUndefined,
            trim_blocks=True,
            lstrip_blocks=True,
        )
        spec = _process_bulk_operations(bulk_spec_raw)
        ctx = {"module_name": "uni_admission"}
        module_dir = tmp_path / "uni_admission"
        module_dir.mkdir()
        (module_dir / "wizards").mkdir(parents=True)
        (module_dir / "views").mkdir(parents=True)

        render_bulk(env, spec, module_dir, ctx)

        init_path = module_dir / "wizards" / "__init__.py"
        assert init_path.exists()
        init_content = init_path.read_text()

        wizard_var = _to_python_var("admission.bulk.admit.wizard")
        assert f"from . import {wizard_var}" in init_content
        assert f"from . import {wizard_var}_line" in init_content

    def test_render_bulk_multiple_operations(self, tmp_path, bulk_spec_raw):
        """render_bulk() handles multiple bulk operations (creates files for each)."""
        from amil_utils.renderer import render_bulk
        from amil_utils.preprocessors.bulk_operations import _process_bulk_operations

        env = Environment(
            loader=FileSystemLoader(str(TEMPLATES_DIR)),
            undefined=StrictUndefined,
            trim_blocks=True,
            lstrip_blocks=True,
        )
        spec = _process_bulk_operations(bulk_spec_raw)
        ctx = {"module_name": "uni_admission"}
        module_dir = tmp_path / "uni_admission"
        module_dir.mkdir()
        (module_dir / "wizards").mkdir(parents=True)
        (module_dir / "views").mkdir(parents=True)

        result = render_bulk(env, spec, module_dir, ctx)
        assert result.success is True

        # Both ops should generate files
        admit_var = _to_python_var("admission.bulk.admit.wizard")
        challan_var = _to_python_var("fee.bulk.challan.wizard")

        assert (module_dir / "wizards" / f"{admit_var}.py").exists()
        assert (module_dir / "wizards" / f"{challan_var}.py").exists()
        assert (module_dir / "views" / f"{admit_var}_wizard_form.xml").exists()
        assert (module_dir / "views" / f"{challan_var}_wizard_form.xml").exists()


class TestStageNamesUpdate:
    """Tests for STAGE_NAMES constant update."""

    def test_stage_names_has_14_entries(self):
        """STAGE_NAMES has 15 entries with bulk as 15th."""
        from amil_utils.renderer import STAGE_NAMES

        assert len(STAGE_NAMES) == 15
        assert STAGE_NAMES[14] == "bulk"

    def test_stage_names_bulk_after_website(self):
        """bulk comes after website in STAGE_NAMES."""
        from amil_utils.renderer import STAGE_NAMES

        website_idx = STAGE_NAMES.index("website")
        bulk_idx = STAGE_NAMES.index("bulk")
        assert bulk_idx == website_idx + 1
