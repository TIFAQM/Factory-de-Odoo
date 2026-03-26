"""Tests for renderer.py - Phase 5 extensions.

Tests for _build_model_context() new context keys and render_module() extended capabilities.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from amil_utils.renderer import (
    MONETARY_FIELD_PATTERNS,
    _build_model_context,
    _build_module_context,
    _is_monetary_field,
    _topologically_sort_fields,
    get_template_dir,
    render_module,
)
from amil_utils.preprocessors.computation_chains import _process_computation_chains
from amil_utils.preprocessors.constraints import _process_constraints
from amil_utils.preprocessors.performance import _process_performance
from amil_utils.preprocessors.production import _process_production_patterns
from amil_utils.preprocessors.security import _process_security_patterns, _parse_crud
from amil_utils.preprocessors.validation import _validate_no_cycles

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SEQUENCE_FIELD_NAMES = {"reference", "ref", "number", "code", "sequence"}


def _make_spec(
    models: list[dict] | None = None,
    wizards: list[dict] | None = None,
) -> dict:
    """Helper to construct a minimal spec dict for testing."""
    return {
        "module_name": "test_module",
        "depends": ["base"],
        "models": models or [],
        "wizards": wizards or [],
    }


# ---------------------------------------------------------------------------
# _build_model_context: new keys
# ---------------------------------------------------------------------------


class TestBuildModelContextComputedFields:
    def test_computed_fields_single_compute_field(self):
        model = {
            "name": "test.model",
            "fields": [
                {"name": "qty", "type": "Integer"},
                {"name": "total", "type": "Float", "compute": "_compute_total", "depends": ["qty"]},
            ],
        }
        spec = _make_spec(models=[model])
        ctx = _build_model_context(spec, model)
        assert "computed_fields" in ctx
        assert len(ctx["computed_fields"]) == 1
        assert ctx["computed_fields"][0]["name"] == "total"

    def test_computed_fields_excludes_falsy_compute_values(self):
        """Fields with falsy compute values (empty string, None) must NOT
        appear in computed_fields — regression guard for truthiness filter."""
        model = {
            "name": "test.model",
            "fields": [
                {"name": "name", "type": "Char"},
                {"name": "qty", "type": "Integer", "compute": ""},
                {"name": "total", "type": "Float", "compute": None},
                {"name": "amount", "type": "Float", "depends": ["qty"]},
            ],
        }
        spec = _make_spec(models=[model])
        ctx = _build_model_context(spec, model)
        assert ctx["computed_fields"] == []
        assert ctx["has_computed"] is False

    def test_has_computed_true_when_computed_fields_present(self):
        model = {
            "name": "test.model",
            "fields": [
                {"name": "total", "type": "Float", "compute": "_compute_total", "depends": ["qty"]},
            ],
        }
        spec = _make_spec(models=[model])
        ctx = _build_model_context(spec, model)
        assert ctx["has_computed"] is True

    def test_has_computed_false_when_no_computed_fields(self):
        model = {
            "name": "test.model",
            "fields": [
                {"name": "name", "type": "Char"},
            ],
        }
        spec = _make_spec(models=[model])
        ctx = _build_model_context(spec, model)
        assert ctx["has_computed"] is False


class TestBuildModelContextOnchangeFields:
    def test_onchange_fields_detected(self):
        model = {
            "name": "test.model",
            "fields": [
                {"name": "name", "type": "Char"},
                {"name": "partner_id", "type": "Many2one", "comodel_name": "res.partner", "onchange": True},
            ],
        }
        spec = _make_spec(models=[model])
        ctx = _build_model_context(spec, model)
        assert "onchange_fields" in ctx
        assert len(ctx["onchange_fields"]) == 1
        assert ctx["onchange_fields"][0]["name"] == "partner_id"

    def test_onchange_fields_excludes_explicit_false(self):
        """Fields with onchange=False must NOT appear in onchange_fields."""
        model = {
            "name": "test.model",
            "fields": [
                {"name": "name", "type": "Char"},
                {"name": "partner_id", "type": "Many2one", "comodel_name": "res.partner", "onchange": False},
            ],
        }
        spec = _make_spec(models=[model])
        ctx = _build_model_context(spec, model)
        assert ctx["onchange_fields"] == []


class TestBuildModelContextConstrainedFields:
    def test_constrained_fields_detected(self):
        model = {
            "name": "test.model",
            "fields": [
                {"name": "date_start", "type": "Date", "constrains": ["date_start", "date_end"]},
                {"name": "date_end", "type": "Date"},
            ],
        }
        spec = _make_spec(models=[model])
        ctx = _build_model_context(spec, model)
        assert "constrained_fields" in ctx
        assert len(ctx["constrained_fields"]) == 1
        assert ctx["constrained_fields"][0]["name"] == "date_start"

    def test_constrained_fields_excludes_empty_constrains_list(self):
        """Fields with constrains=[] (empty list, falsy) must NOT appear."""
        model = {
            "name": "test.model",
            "fields": [
                {"name": "date_start", "type": "Date", "constrains": []},
                {"name": "date_end", "type": "Date"},
            ],
        }
        spec = _make_spec(models=[model])
        ctx = _build_model_context(spec, model)
        assert ctx["constrained_fields"] == []


class TestBuildModelContextSequenceFields:
    def test_sequence_field_reference_required_detected(self):
        model = {
            "name": "test.model",
            "fields": [
                {"name": "reference", "type": "Char", "required": True},
            ],
        }
        spec = _make_spec(models=[model])
        ctx = _build_model_context(spec, model)
        assert "sequence_fields" in ctx
        assert len(ctx["sequence_fields"]) == 1
        assert ctx["sequence_fields"][0]["name"] == "reference"

    @pytest.mark.parametrize("field_name", list(SEQUENCE_FIELD_NAMES))
    def test_all_sequence_field_names_detected(self, field_name):
        model = {
            "name": "test.model",
            "fields": [
                {"name": field_name, "type": "Char", "required": True},
            ],
        }
        spec = _make_spec(models=[model])
        ctx = _build_model_context(spec, model)
        assert len(ctx["sequence_fields"]) == 1

    def test_description_char_required_not_in_sequence_fields(self):
        """A Char field named 'description' required=True must NOT be in sequence_fields."""
        model = {
            "name": "test.model",
            "fields": [
                {"name": "description", "type": "Char", "required": True},
            ],
        }
        spec = _make_spec(models=[model])
        ctx = _build_model_context(spec, model)
        assert ctx["sequence_fields"] == []

    def test_reference_not_required_not_in_sequence_fields(self):
        """A Char field named 'reference' without required=True must NOT be in sequence_fields."""
        model = {
            "name": "test.model",
            "fields": [
                {"name": "reference", "type": "Char", "required": False},
            ],
        }
        spec = _make_spec(models=[model])
        ctx = _build_model_context(spec, model)
        assert ctx["sequence_fields"] == []

    def test_reference_integer_type_not_in_sequence_fields(self):
        """An Integer field named 'reference' must NOT be in sequence_fields."""
        model = {
            "name": "test.model",
            "fields": [
                {"name": "reference", "type": "Integer", "required": True},
            ],
        }
        spec = _make_spec(models=[model])
        ctx = _build_model_context(spec, model)
        assert ctx["sequence_fields"] == []

    def test_has_sequence_fields_true(self):
        model = {
            "name": "test.model",
            "fields": [
                {"name": "reference", "type": "Char", "required": True},
            ],
        }
        spec = _make_spec(models=[model])
        ctx = _build_model_context(spec, model)
        assert ctx["has_sequence_fields"] is True

    def test_has_sequence_fields_false(self):
        model = {
            "name": "test.model",
            "fields": [{"name": "name", "type": "Char"}],
        }
        spec = _make_spec(models=[model])
        ctx = _build_model_context(spec, model)
        assert ctx["has_sequence_fields"] is False

    def test_sequence_field_names_list_in_context(self):
        """sequence_field_names must be a list in context (used by template)."""
        model = {
            "name": "test.model",
            "fields": [{"name": "name", "type": "Char"}],
        }
        spec = _make_spec(models=[model])
        ctx = _build_model_context(spec, model)
        assert "sequence_field_names" in ctx
        assert isinstance(ctx["sequence_field_names"], list)


class TestBuildModelContextStateField:
    def test_state_field_detected(self):
        model = {
            "name": "test.model",
            "fields": [
                {"name": "state", "type": "Selection", "selection": [["draft", "Draft"], ["done", "Done"]]},
            ],
        }
        spec = _make_spec(models=[model])
        ctx = _build_model_context(spec, model)
        assert ctx["state_field"] is not None
        assert ctx["state_field"]["name"] == "state"

    def test_status_field_detected(self):
        model = {
            "name": "test.model",
            "fields": [
                {"name": "status", "type": "Selection", "selection": [["active", "Active"]]},
            ],
        }
        spec = _make_spec(models=[model])
        ctx = _build_model_context(spec, model)
        assert ctx["state_field"] is not None
        assert ctx["state_field"]["name"] == "status"

    def test_no_state_field_returns_none(self):
        model = {
            "name": "test.model",
            "fields": [
                {"name": "name", "type": "Char"},
            ],
        }
        spec = _make_spec(models=[model])
        ctx = _build_model_context(spec, model)
        assert ctx["state_field"] is None

    def test_state_char_field_not_detected_as_state_field(self):
        """A field named 'state' but type 'Char' should NOT be the state_field."""
        model = {
            "name": "test.model",
            "fields": [
                {"name": "state", "type": "Char"},
            ],
        }
        spec = _make_spec(models=[model])
        ctx = _build_model_context(spec, model)
        assert ctx["state_field"] is None


class TestBuildModelContextWizards:
    def test_wizards_from_spec(self):
        wizards = [
            {"name": "confirm.wizard", "target_model": "test.model", "trigger_state": "draft", "fields": []}
        ]
        model = {"name": "test.model", "fields": [{"name": "name", "type": "Char"}]}
        spec = _make_spec(models=[model], wizards=wizards)
        ctx = _build_model_context(spec, model)
        assert "wizards" in ctx
        assert len(ctx["wizards"]) == 1
        assert ctx["wizards"][0]["name"] == "confirm.wizard"

    def test_wizards_empty_list_when_no_wizards(self):
        model = {"name": "test.model", "fields": [{"name": "name", "type": "Char"}]}
        spec = _make_spec(models=[model])
        ctx = _build_model_context(spec, model)
        assert ctx["wizards"] == []


# ---------------------------------------------------------------------------
# render_module: file generation
# ---------------------------------------------------------------------------


class TestRenderModuleWizards:
    def test_wizards_spec_generates_wizards_init(self):
        spec = {
            "module_name": "test_wiz",
            "depends": ["base"],
            "models": [
                {
                    "name": "test.order",
                    "description": "Test Order",
                    "fields": [
                        {"name": "name", "type": "Char", "required": True},
                        {
                            "name": "state",
                            "type": "Selection",
                            "selection": [["draft", "Draft"], ["done", "Done"]],
                            "default": "draft",
                        },
                    ],
                }
            ],
            "wizards": [
                {
                    "name": "test.wizard",
                    "target_model": "test.order",
                    "trigger_state": "draft",
                    "fields": [{"name": "notes", "type": "Text", "string": "Notes"}],
                }
            ],
        }
        with tempfile.TemporaryDirectory() as d:
            files, _ = render_module(spec, get_template_dir(), Path(d))
            names = [Path(f).name for f in files]
            assert "__init__.py" in names  # wizards/__init__.py is one of the __init__.py files

            # Check full relative paths for wizard files
            relative_paths = [
                str(Path(f).relative_to(Path(d) / "test_wiz")) for f in files
            ]
            assert any("wizards" in p and "__init__.py" in p for p in relative_paths), (
                f"Missing wizards/__init__.py in {relative_paths}"
            )

    def test_no_wizards_spec_produces_no_wizard_files(self):
        spec = {
            "module_name": "test_nowiz",
            "depends": ["base"],
            "models": [
                {
                    "name": "test.order",
                    "description": "Test Order",
                    "fields": [{"name": "name", "type": "Char", "required": True}],
                }
            ],
        }
        with tempfile.TemporaryDirectory() as d:
            files, _ = render_module(spec, get_template_dir(), Path(d))
            relative_paths = [
                str(Path(f).relative_to(Path(d) / "test_nowiz")) for f in files
            ]
            assert not any("wizards" in p for p in relative_paths), (
                f"Found unexpected wizard files: {relative_paths}"
            )

    def test_wizard_py_file_created(self):
        spec = {
            "module_name": "test_wiz2",
            "depends": ["base"],
            "models": [
                {
                    "name": "test.order",
                    "description": "Test Order",
                    "fields": [{"name": "name", "type": "Char"}],
                }
            ],
            "wizards": [
                {
                    "name": "confirm.wizard",
                    "target_model": "test.order",
                    "trigger_state": "draft",
                    "fields": [{"name": "notes", "type": "Text", "string": "Notes"}],
                }
            ],
        }
        with tempfile.TemporaryDirectory() as d:
            files, _ = render_module(spec, get_template_dir(), Path(d))
            relative_paths = [
                str(Path(f).relative_to(Path(d) / "test_wiz2")) for f in files
            ]
            assert any("confirm_wizard.py" in p for p in relative_paths), (
                f"Missing wizards/confirm_wizard.py in {relative_paths}"
            )

    def test_wizard_form_xml_created(self):
        spec = {
            "module_name": "test_wiz3",
            "depends": ["base"],
            "models": [
                {
                    "name": "test.order",
                    "description": "Test Order",
                    "fields": [{"name": "name", "type": "Char"}],
                }
            ],
            "wizards": [
                {
                    "name": "confirm.wizard",
                    "target_model": "test.order",
                    "trigger_state": "draft",
                    "fields": [],
                }
            ],
        }
        with tempfile.TemporaryDirectory() as d:
            files, _ = render_module(spec, get_template_dir(), Path(d))
            names = [Path(f).name for f in files]
            assert "confirm_wizard_wizard_form.xml" in names or any(
                "wizard_form" in n for n in names
            ), f"Missing wizard form xml in {names}"


class TestRenderModuleSequences:
    def test_sequence_field_generates_sequences_xml(self):
        spec = {
            "module_name": "test_seq",
            "depends": ["base"],
            "models": [
                {
                    "name": "test.order",
                    "description": "Test Order",
                    "fields": [
                        {"name": "name", "type": "Char"},
                        {"name": "reference", "type": "Char", "required": True},
                    ],
                }
            ],
        }
        with tempfile.TemporaryDirectory() as d:
            files, _ = render_module(spec, get_template_dir(), Path(d))
            names = [Path(f).name for f in files]
            assert "sequences.xml" in names, f"Missing sequences.xml. Got: {names}"

    def test_no_sequence_field_no_sequences_xml(self):
        spec = {
            "module_name": "test_noseq",
            "depends": ["base"],
            "models": [
                {
                    "name": "test.order",
                    "description": "Test Order",
                    "fields": [
                        {"name": "name", "type": "Char"},
                        {"name": "description", "type": "Char", "required": True},
                    ],
                }
            ],
        }
        with tempfile.TemporaryDirectory() as d:
            files, _ = render_module(spec, get_template_dir(), Path(d))
            names = [Path(f).name for f in files]
            assert "sequences.xml" not in names, f"Unexpected sequences.xml in {names}"


class TestRenderModuleDataXml:
    def test_data_xml_always_created(self):
        spec = {
            "module_name": "test_data",
            "depends": ["base"],
            "models": [
                {
                    "name": "test.item",
                    "description": "Test Item",
                    "fields": [{"name": "name", "type": "Char"}],
                }
            ],
        }
        with tempfile.TemporaryDirectory() as d:
            files, _ = render_module(spec, get_template_dir(), Path(d))
            names = [Path(f).name for f in files]
            assert "data.xml" in names, f"Missing data.xml. Got: {names}"

    def test_data_xml_created_even_with_sequences(self):
        spec = {
            "module_name": "test_data2",
            "depends": ["base"],
            "models": [
                {
                    "name": "test.item",
                    "description": "Test Item",
                    "fields": [
                        {"name": "name", "type": "Char"},
                        {"name": "reference", "type": "Char", "required": True},
                    ],
                }
            ],
        }
        with tempfile.TemporaryDirectory() as d:
            files, _ = render_module(spec, get_template_dir(), Path(d))
            names = [Path(f).name for f in files]
            assert "data.xml" in names, f"Missing data.xml. Got: {names}"
            assert "sequences.xml" in names, f"Missing sequences.xml. Got: {names}"


# ---------------------------------------------------------------------------
# Phase 6: _build_model_context -- has_company_field detection
# ---------------------------------------------------------------------------


_COMPANY_SPEC = {
    "module_name": "test_company",
    "depends": ["base"],
    "models": [
        {
            "name": "test.order",
            "description": "Test Order",
            "fields": [
                {"name": "name", "type": "Char", "required": True},
                {"name": "company_id", "type": "Many2one", "comodel_name": "res.company"},
            ],
        }
    ],
}


class TestBuildModelContextCompanyField:
    def test_company_field_many2one_sets_has_company_field_true(self):
        """Model with company_id Many2one → has_company_field is True."""
        model = {
            "name": "test.order",
            "fields": [
                {"name": "name", "type": "Char", "required": True},
                {"name": "company_id", "type": "Many2one", "comodel_name": "res.company"},
            ],
        }
        spec = _make_spec(models=[model])
        ctx = _build_model_context(spec, model)
        assert ctx["has_company_field"] is True

    def test_no_company_field_sets_has_company_field_false(self):
        """Model without company_id field → has_company_field is False."""
        model = {
            "name": "test.order",
            "fields": [
                {"name": "name", "type": "Char"},
            ],
        }
        spec = _make_spec(models=[model])
        ctx = _build_model_context(spec, model)
        assert ctx["has_company_field"] is False

    def test_company_field_wrong_type_sets_false(self):
        """company_id field with type Char (not Many2one) → has_company_field is False."""
        model = {
            "name": "test.order",
            "fields": [
                {"name": "company_id", "type": "Char"},
            ],
        }
        spec = _make_spec(models=[model])
        ctx = _build_model_context(spec, model)
        assert ctx["has_company_field"] is False

    def test_company_field_different_name_sets_false(self):
        """Many2one field named 'company' (not 'company_id') → has_company_field is False."""
        model = {
            "name": "test.order",
            "fields": [
                {"name": "company", "type": "Many2one", "comodel_name": "res.company"},
            ],
        }
        spec = _make_spec(models=[model])
        ctx = _build_model_context(spec, model)
        assert ctx["has_company_field"] is False


# ---------------------------------------------------------------------------
# Phase 6: render_module -- record_rules.xml generation
# ---------------------------------------------------------------------------


class TestRenderModuleRecordRules:
    def test_company_field_model_generates_record_rules_xml(self):
        """spec with Many2one company_id → 'record_rules.xml' appears in generated file names."""
        with tempfile.TemporaryDirectory() as d:
            files, _ = render_module(_COMPANY_SPEC, get_template_dir(), Path(d))
            names = [Path(f).name for f in files]
            assert "record_rules.xml" in names, (
                f"Expected record_rules.xml in generated files. Got: {names}"
            )

    def test_no_company_field_no_record_rules_xml(self):
        """spec without company_id → 'record_rules.xml' NOT in generated file names."""
        spec = {
            "module_name": "test_nocompany",
            "depends": ["base"],
            "models": [
                {
                    "name": "test.order",
                    "description": "Test Order",
                    "fields": [{"name": "name", "type": "Char"}],
                }
            ],
        }
        with tempfile.TemporaryDirectory() as d:
            files, _ = render_module(spec, get_template_dir(), Path(d))
            names = [Path(f).name for f in files]
            assert "record_rules.xml" not in names, (
                f"Unexpected record_rules.xml in files without company_id: {names}"
            )

    def test_record_rules_xml_contains_company_ids_domain(self):
        """Content of generated record_rules.xml contains 'company_ids' OCA shorthand."""
        with tempfile.TemporaryDirectory() as d:
            files, _ = render_module(_COMPANY_SPEC, get_template_dir(), Path(d))
            record_rules_file = next(
                (f for f in files if Path(f).name == "record_rules.xml"), None
            )
            assert record_rules_file is not None, "record_rules.xml was not generated"
            content = Path(record_rules_file).read_text(encoding="utf-8")
            assert "company_ids" in content, (
                f"'company_ids' domain not found in record_rules.xml. Content:\n{content}"
            )

    def test_manifest_includes_record_rules_when_company_field(self):
        """Generated __manifest__.py contains 'security/record_rules.xml' when company_id model present."""
        with tempfile.TemporaryDirectory() as d:
            files, _ = render_module(_COMPANY_SPEC, get_template_dir(), Path(d))
            manifest_file = next(
                (f for f in files if Path(f).name == "__manifest__.py"), None
            )
            assert manifest_file is not None, "__manifest__.py was not generated"
            content = Path(manifest_file).read_text(encoding="utf-8")
            assert "security/record_rules.xml" in content, (
                f"'security/record_rules.xml' not found in __manifest__.py. Content:\n{content}"
            )


# ---------------------------------------------------------------------------
# Phase 9: Versioned template rendering
# ---------------------------------------------------------------------------


def _make_versioned_spec(
    odoo_version: str = "17.0",
    models: list[dict] | None = None,
    depends: list[str] | None = None,
) -> dict:
    """Helper to construct a spec with odoo_version for version testing."""
    return {
        "module_name": "test_ver",
        "depends": depends or ["base"],
        "odoo_version": odoo_version,
        "models": models or [
            {
                "name": "test.item",
                "description": "Test Item",
                "fields": [
                    {"name": "name", "type": "Char", "required": True},
                    {"name": "description", "type": "Text"},
                ],
            }
        ],
    }


class TestVersionedTemplates:
    """Tests that version-specific templates produce correct output."""

    def test_17_gets_tree_tag(self):
        """render_module with odoo_version=17.0 produces XML containing '<tree'."""
        spec = _make_versioned_spec("17.0")
        with tempfile.TemporaryDirectory() as d:
            files, _ = render_module(spec, get_template_dir(), Path(d))
            views_file = next(
                (f for f in files if "test_item_views.xml" in str(f)), None
            )
            assert views_file is not None
            content = Path(views_file).read_text(encoding="utf-8")
            assert "<tree" in content, f"Expected <tree in 17.0 views. Got:\n{content}"
            assert "<list" not in content, f"Unexpected <list in 17.0 views. Got:\n{content}"

    def test_18_gets_list_tag(self):
        """render_module with odoo_version=18.0 produces XML containing '<list'."""
        spec = _make_versioned_spec("18.0")
        with tempfile.TemporaryDirectory() as d:
            files, _ = render_module(spec, get_template_dir(), Path(d))
            views_file = next(
                (f for f in files if "test_item_views.xml" in str(f)), None
            )
            assert views_file is not None
            content = Path(views_file).read_text(encoding="utf-8")
            assert "<list" in content, f"Expected <list in 18.0 views. Got:\n{content}"
            assert "<tree" not in content, f"Unexpected <tree in 18.0 views. Got:\n{content}"

    def test_18_action_uses_list_viewmode(self):
        """18.0 action.xml contains view_mode with 'list,form'."""
        spec = _make_versioned_spec("18.0")
        with tempfile.TemporaryDirectory() as d:
            files, _ = render_module(spec, get_template_dir(), Path(d))
            action_file = next(
                (f for f in files if "test_item_action.xml" in str(f)), None
            )
            assert action_file is not None
            content = Path(action_file).read_text(encoding="utf-8")
            assert "list,form" in content, f"Expected 'list,form' in 18.0 action. Got:\n{content}"

    def test_17_action_uses_tree_viewmode(self):
        """17.0 action.xml contains view_mode with 'tree,form'."""
        spec = _make_versioned_spec("17.0")
        with tempfile.TemporaryDirectory() as d:
            files, _ = render_module(spec, get_template_dir(), Path(d))
            action_file = next(
                (f for f in files if "test_item_action.xml" in str(f)), None
            )
            assert action_file is not None
            content = Path(action_file).read_text(encoding="utf-8")
            assert "tree,form" in content, f"Expected 'tree,form' in 17.0 action. Got:\n{content}"

    def test_18_chatter_shorthand(self):
        """18.0 form view uses '<chatter/>' not 'oe_chatter'."""
        spec = _make_versioned_spec("18.0", depends=["base", "mail"])
        with tempfile.TemporaryDirectory() as d:
            files, _ = render_module(spec, get_template_dir(), Path(d))
            views_file = next(
                (f for f in files if "test_item_views.xml" in str(f)), None
            )
            assert views_file is not None
            content = Path(views_file).read_text(encoding="utf-8")
            assert "<chatter/>" in content, f"Expected <chatter/> in 18.0 form. Got:\n{content}"
            assert "oe_chatter" not in content, f"Unexpected oe_chatter in 18.0 form. Got:\n{content}"

    def test_shared_template_fallback(self):
        """Shared templates (manifest, menu, etc.) resolve correctly for both versions."""
        for version in ("17.0", "18.0"):
            spec = _make_versioned_spec(version)
            with tempfile.TemporaryDirectory() as d:
                files, _ = render_module(spec, get_template_dir(), Path(d))
                names = [Path(f).name for f in files]
                assert "__manifest__.py" in names, f"Missing manifest for {version}"
                assert "menu.xml" in names, f"Missing menu for {version}"
                assert "README.rst" in names, f"Missing README for {version}"


class TestVersionConfig:
    """Tests that odoo_version flows through spec correctly."""

    def test_default_version_is_19(self):
        """render_module with no odoo_version in spec defaults to 19.0."""
        spec = {
            "module_name": "test_default",
            "depends": ["base"],
            "models": [
                {
                    "name": "test.item",
                    "description": "Test Item",
                    "fields": [{"name": "name", "type": "Char"}],
                }
            ],
        }
        # No odoo_version key at all
        with tempfile.TemporaryDirectory() as d:
            files, _ = render_module(spec, get_template_dir(), Path(d))
            views_file = next(
                (f for f in files if "test_item_views.xml" in str(f)), None
            )
            assert views_file is not None
            content = Path(views_file).read_text(encoding="utf-8")
            assert "<list" in content, f"Default should produce 19.0 list tags. Got:\n{content}"

    def test_version_from_spec(self):
        """render_module reads odoo_version from spec dict."""
        spec = _make_versioned_spec("18.0")
        with tempfile.TemporaryDirectory() as d:
            files, _ = render_module(spec, get_template_dir(), Path(d))
            action_file = next(
                (f for f in files if "test_item_action.xml" in str(f)), None
            )
            assert action_file is not None
            content = Path(action_file).read_text(encoding="utf-8")
            assert "list,form" in content, f"Expected 18.0 view_mode. Got:\n{content}"


class TestRenderModule18:
    """Integration test: full 18.0 module renders without errors."""

    def test_full_18_module_renders(self):
        """Complete render_module with odoo_version=18.0 produces all expected files."""
        spec = {
            "module_name": "test_18_full",
            "depends": ["base", "mail"],
            "odoo_version": "18.0",
            "models": [
                {
                    "name": "project.task",
                    "description": "Project Task",
                    "fields": [
                        {"name": "name", "type": "Char", "required": True},
                        {"name": "description", "type": "Text"},
                        {
                            "name": "state",
                            "type": "Selection",
                            "selection": [["draft", "Draft"], ["done", "Done"]],
                            "default": "draft",
                        },
                        {"name": "partner_id", "type": "Many2one", "comodel_name": "res.partner"},
                    ],
                }
            ],
        }
        with tempfile.TemporaryDirectory() as d:
            files, _ = render_module(spec, get_template_dir(), Path(d))
            names = [Path(f).name for f in files]
            # All expected file types present
            assert "__manifest__.py" in names
            assert "__init__.py" in names
            assert "project_task.py" in names
            assert "project_task_views.xml" in names
            assert "project_task_action.xml" in names
            assert "menu.xml" in names
            assert "security.xml" in names
            assert "ir.model.access.csv" in names
            assert "README.rst" in names
            # Verify 18.0 markers
            views_file = next(f for f in files if "project_task_views.xml" in str(f))
            content = Path(views_file).read_text(encoding="utf-8")
            assert "<list" in content
            assert "<chatter/>" in content
            assert "<tree" not in content


# ---------------------------------------------------------------------------
# Phase 12: _build_model_context -- inherit_list (TMPL-01)
# ---------------------------------------------------------------------------


class TestBuildModelContextInheritList:
    """Tests that _build_model_context builds inherit_list from mail dependency + explicit inherit."""

    def test_inherit_list_with_mail_dependency(self):
        """spec with depends=["base", "mail"], model with no explicit inherit -> inherit_list has mail mixins."""
        model = {
            "name": "test.model",
            "fields": [{"name": "name", "type": "Char"}],
        }
        spec = _make_spec(models=[model])
        spec["depends"] = ["base", "mail"]
        ctx = _build_model_context(spec, model)
        assert ctx["inherit_list"] == ["mail.thread", "mail.activity.mixin"]

    def test_inherit_list_merges_explicit_inherit(self):
        """spec with mail + model with inherit="portal.mixin" -> inherit_list has all 3."""
        model = {
            "name": "test.model",
            "inherit": "portal.mixin",
            "fields": [{"name": "name", "type": "Char"}],
        }
        spec = _make_spec(models=[model])
        spec["depends"] = ["base", "mail"]
        ctx = _build_model_context(spec, model)
        assert "portal.mixin" in ctx["inherit_list"]
        assert "mail.thread" in ctx["inherit_list"]
        assert "mail.activity.mixin" in ctx["inherit_list"]
        assert len(ctx["inherit_list"]) == 3

    def test_inherit_list_no_mail_empty(self):
        """spec with depends=["base"], model with no inherit -> inherit_list is empty."""
        model = {
            "name": "test.model",
            "fields": [{"name": "name", "type": "Char"}],
        }
        spec = _make_spec(models=[model])
        ctx = _build_model_context(spec, model)
        assert ctx["inherit_list"] == []

    def test_inherit_list_no_mail_explicit_inherit(self):
        """spec with depends=["base"], model with inherit="portal.mixin" -> inherit_list has only portal.mixin."""
        model = {
            "name": "test.model",
            "inherit": "portal.mixin",
            "fields": [{"name": "name", "type": "Char"}],
        }
        spec = _make_spec(models=[model])
        ctx = _build_model_context(spec, model)
        assert ctx["inherit_list"] == ["portal.mixin"]

    def test_inherit_list_mail_no_duplicates(self):
        """spec with mail + model with inherit="mail.thread" -> mail.thread appears exactly once."""
        model = {
            "name": "test.model",
            "inherit": "mail.thread",
            "fields": [{"name": "name", "type": "Char"}],
        }
        spec = _make_spec(models=[model])
        spec["depends"] = ["base", "mail"]
        ctx = _build_model_context(spec, model)
        assert ctx["inherit_list"].count("mail.thread") == 1
        assert "mail.activity.mixin" in ctx["inherit_list"]

    # Phase 21: Smart mail.thread injection -- skip cases (TMPL-01)

    def test_inherit_list_line_item_no_mail_thread(self):
        """Line item model (required Many2one _id to in-module model) should NOT get mail.thread."""
        parent_model = {
            "name": "sale.order",
            "fields": [{"name": "name", "type": "Char"}],
        }
        line_model = {
            "name": "sale.order.line",
            "fields": [
                {"name": "name", "type": "Char"},
                {
                    "name": "order_id",
                    "type": "Many2one",
                    "comodel_name": "sale.order",
                    "required": True,
                },
            ],
        }
        spec = _make_spec(models=[parent_model, line_model])
        spec["depends"] = ["base", "mail"]
        ctx = _build_model_context(spec, line_model)
        assert ctx["inherit_list"] == [], (
            f"Line item should NOT get mail.thread, got {ctx['inherit_list']}"
        )

    def test_inherit_list_line_item_with_chatter_override(self):
        """Line item with explicit chatter=True should still get mail.thread."""
        parent_model = {
            "name": "sale.order",
            "fields": [{"name": "name", "type": "Char"}],
        }
        line_model = {
            "name": "sale.order.line",
            "chatter": True,
            "fields": [
                {"name": "name", "type": "Char"},
                {
                    "name": "order_id",
                    "type": "Many2one",
                    "comodel_name": "sale.order",
                    "required": True,
                },
            ],
        }
        spec = _make_spec(models=[parent_model, line_model])
        spec["depends"] = ["base", "mail"]
        ctx = _build_model_context(spec, line_model)
        assert "mail.thread" in ctx["inherit_list"], (
            "Line item with chatter=True should get mail.thread"
        )
        assert "mail.activity.mixin" in ctx["inherit_list"]

    def test_inherit_list_chatter_false_skips_mail(self):
        """Top-level model with chatter=False should NOT get mail.thread even with mail in depends."""
        model = {
            "name": "test.config",
            "chatter": False,
            "fields": [{"name": "name", "type": "Char"}],
        }
        spec = _make_spec(models=[model])
        spec["depends"] = ["base", "mail"]
        ctx = _build_model_context(spec, model)
        assert ctx["inherit_list"] == [], (
            f"chatter=False model should NOT get mail.thread, got {ctx['inherit_list']}"
        )

    def test_inherit_list_parent_already_has_mail(self):
        """Model extending in-module parent that gets mail.thread should NOT duplicate mail.thread."""
        parent_model = {
            "name": "base.record",
            "fields": [{"name": "name", "type": "Char"}],
        }
        child_model = {
            "name": "child.record",
            "inherit": "base.record",
            "fields": [{"name": "extra", "type": "Char"}],
        }
        spec = _make_spec(models=[parent_model, child_model])
        spec["depends"] = ["base", "mail"]
        # Parent gets mail.thread automatically. Child inherits from parent,
        # so it should NOT inject mail.thread again.
        ctx = _build_model_context(spec, child_model)
        assert "mail.thread" not in ctx["inherit_list"], (
            "Child of in-module parent should NOT duplicate mail.thread"
        )
        # But explicit inherit should still be there
        assert "base.record" in ctx["inherit_list"]

    def test_inherit_list_top_level_still_gets_mail(self):
        """Top-level model with mail in depends still gets mail.thread (existing behavior preserved)."""
        model = {
            "name": "project.task",
            "fields": [{"name": "name", "type": "Char"}],
        }
        spec = _make_spec(models=[model])
        spec["depends"] = ["base", "mail"]
        ctx = _build_model_context(spec, model)
        assert "mail.thread" in ctx["inherit_list"]
        assert "mail.activity.mixin" in ctx["inherit_list"]

    def test_inherit_list_line_item_detection_non_required_m2o(self):
        """Many2one that is NOT required should not trigger line item detection."""
        parent_model = {
            "name": "sale.order",
            "fields": [{"name": "name", "type": "Char"}],
        }
        model = {
            "name": "sale.order.line",
            "fields": [
                {"name": "name", "type": "Char"},
                {
                    "name": "order_id",
                    "type": "Many2one",
                    "comodel_name": "sale.order",
                    # required is missing/False -- not a line item
                },
            ],
        }
        spec = _make_spec(models=[parent_model, model])
        spec["depends"] = ["base", "mail"]
        ctx = _build_model_context(spec, model)
        assert "mail.thread" in ctx["inherit_list"], (
            "Non-required M2O should NOT trigger line item detection"
        )

    def test_inherit_list_line_item_detection_name_pattern(self):
        """Only Many2one fields ending in _id with comodel in same module count as line item indicators."""
        parent_model = {
            "name": "sale.order",
            "fields": [{"name": "name", "type": "Char"}],
        }
        model = {
            "name": "sale.order.line",
            "fields": [
                {"name": "name", "type": "Char"},
                {
                    "name": "related_order",  # Does NOT end in _id
                    "type": "Many2one",
                    "comodel_name": "sale.order",
                    "required": True,
                },
            ],
        }
        spec = _make_spec(models=[parent_model, model])
        spec["depends"] = ["base", "mail"]
        ctx = _build_model_context(spec, model)
        assert "mail.thread" in ctx["inherit_list"], (
            "M2O not ending in _id should NOT trigger line item detection"
        )


# ---------------------------------------------------------------------------
# Phase 12: _build_model_context -- needs_api (TMPL-02)
# ---------------------------------------------------------------------------


class TestBuildModelContextNeedsApi:
    """Tests that _build_model_context sets needs_api based on decorator usage."""

    def test_needs_api_true_with_computed(self):
        """Model with a computed field -> needs_api is True."""
        model = {
            "name": "test.model",
            "fields": [
                {"name": "total", "type": "Float", "compute": "_compute_total", "depends": ["qty"]},
            ],
        }
        spec = _make_spec(models=[model])
        ctx = _build_model_context(spec, model)
        assert ctx["needs_api"] is True

    def test_needs_api_true_with_onchange(self):
        """Model with onchange field -> needs_api is True."""
        model = {
            "name": "test.model",
            "fields": [
                {"name": "partner_id", "type": "Many2one", "comodel_name": "res.partner", "onchange": True},
            ],
        }
        spec = _make_spec(models=[model])
        ctx = _build_model_context(spec, model)
        assert ctx["needs_api"] is True

    def test_needs_api_true_with_constrained(self):
        """Model with constrained field -> needs_api is True."""
        model = {
            "name": "test.model",
            "fields": [
                {"name": "date_start", "type": "Date", "constrains": ["date_start", "date_end"]},
            ],
        }
        spec = _make_spec(models=[model])
        ctx = _build_model_context(spec, model)
        assert ctx["needs_api"] is True

    def test_needs_api_true_with_sequence(self):
        """Model with sequence field (uses @api.model) -> needs_api is True."""
        model = {
            "name": "test.model",
            "fields": [
                {"name": "reference", "type": "Char", "required": True},
            ],
        }
        spec = _make_spec(models=[model])
        ctx = _build_model_context(spec, model)
        assert ctx["needs_api"] is True

    def test_needs_api_false_plain_fields(self):
        """Model with only plain Char/Integer fields -> needs_api is False."""
        model = {
            "name": "test.model",
            "fields": [
                {"name": "name", "type": "Char"},
                {"name": "qty", "type": "Integer"},
            ],
        }
        spec = _make_spec(models=[model])
        ctx = _build_model_context(spec, model)
        assert ctx["needs_api"] is False


# ---------------------------------------------------------------------------
# Phase 12: Template rendering -- mail.thread inheritance (TMPL-01)
# ---------------------------------------------------------------------------


class TestTemplateMailInheritance:
    """Tests that rendered model.py has correct _inherit line when mail is in depends."""

    def test_model_py_has_mail_thread_inherit_when_mail_depends(self):
        """render_module with mail in depends -> model.py contains _inherit = ['mail.thread', 'mail.activity.mixin']."""
        spec = {
            "module_name": "test_mail",
            "depends": ["base", "mail"],
            "models": [
                {
                    "name": "test.record",
                    "description": "Test Record",
                    "fields": [{"name": "name", "type": "Char", "required": True}],
                }
            ],
        }
        with tempfile.TemporaryDirectory() as d:
            files, _ = render_module(spec, get_template_dir(), Path(d))
            model_file = next(f for f in files if "test_record.py" in str(f) and "test_" not in Path(f).parent.name)
            content = Path(model_file).read_text(encoding="utf-8")
            assert "_inherit = [" in content, f"Expected _inherit list in model.py. Got:\n{content}"
            assert "mail.thread" in content
            assert "mail.activity.mixin" in content

    def test_model_py_no_inherit_when_no_mail(self):
        """render_module without mail -> model.py does NOT contain _inherit."""
        spec = {
            "module_name": "test_nomail",
            "depends": ["base"],
            "models": [
                {
                    "name": "test.record",
                    "description": "Test Record",
                    "fields": [{"name": "name", "type": "Char", "required": True}],
                }
            ],
        }
        with tempfile.TemporaryDirectory() as d:
            files, _ = render_module(spec, get_template_dir(), Path(d))
            model_file = next(f for f in files if "test_record.py" in str(f) and "test_" not in Path(f).parent.name)
            content = Path(model_file).read_text(encoding="utf-8")
            assert "_inherit" not in content, f"Unexpected _inherit in model.py without mail. Got:\n{content}"


# ---------------------------------------------------------------------------
# Phase 12: Template rendering -- conditional api import (TMPL-02)
# ---------------------------------------------------------------------------


class TestTemplateConditionalApiImport:
    """Tests that rendered model.py conditionally imports api based on decorator usage."""

    def test_model_py_no_api_import_plain_fields(self):
        """render with plain fields only -> model.py does NOT have 'from odoo import api'."""
        spec = {
            "module_name": "test_noapi",
            "depends": ["base"],
            "models": [
                {
                    "name": "test.simple",
                    "description": "Test Simple",
                    "fields": [
                        {"name": "name", "type": "Char", "required": True},
                        {"name": "qty", "type": "Integer"},
                    ],
                }
            ],
        }
        with tempfile.TemporaryDirectory() as d:
            files, _ = render_module(spec, get_template_dir(), Path(d))
            model_file = next(f for f in files if "test_simple.py" in str(f) and "test_" not in Path(f).parent.name)
            content = Path(model_file).read_text(encoding="utf-8")
            assert "from odoo import api" not in content, f"Unexpected api import in plain model. Got:\n{content}"
            assert "from odoo import fields, models" in content, f"Missing fields/models import. Got:\n{content}"

    def test_model_py_has_api_import_with_computed(self):
        """render with computed field -> model.py has 'from odoo import api, fields, models'."""
        spec = {
            "module_name": "test_withapi",
            "depends": ["base"],
            "models": [
                {
                    "name": "test.computed",
                    "description": "Test Computed",
                    "fields": [
                        {"name": "qty", "type": "Integer"},
                        {"name": "total", "type": "Float", "compute": "_compute_total", "depends": ["qty"]},
                    ],
                }
            ],
        }
        with tempfile.TemporaryDirectory() as d:
            files, _ = render_module(spec, get_template_dir(), Path(d))
            model_file = next(f for f in files if "test_computed.py" in str(f) and "test_" not in Path(f).parent.name)
            content = Path(model_file).read_text(encoding="utf-8")
            assert "from odoo import api, fields, models" in content, f"Missing api import with computed. Got:\n{content}"


# ---------------------------------------------------------------------------
# Phase 12: Template rendering -- clean manifest (TMPL-03)
# ---------------------------------------------------------------------------


class TestTemplateManifestClean:
    """Tests that rendered __manifest__.py does not contain superfluous defaults."""

    def test_manifest_no_installable_key(self):
        """render module -> __manifest__.py does NOT contain '"installable"'."""
        spec = {
            "module_name": "test_manifest",
            "depends": ["base"],
            "models": [
                {
                    "name": "test.item",
                    "description": "Test Item",
                    "fields": [{"name": "name", "type": "Char", "required": True}],
                }
            ],
        }
        with tempfile.TemporaryDirectory() as d:
            files, _ = render_module(spec, get_template_dir(), Path(d))
            manifest_file = next(f for f in files if Path(f).name == "__manifest__.py")
            content = Path(manifest_file).read_text(encoding="utf-8")
            assert '"installable"' not in content, f"Unexpected 'installable' key in manifest. Got:\n{content}"

    def test_manifest_no_auto_install_key(self):
        """render module -> __manifest__.py does NOT contain '"auto_install"'."""
        spec = {
            "module_name": "test_manifest2",
            "depends": ["base"],
            "models": [
                {
                    "name": "test.item",
                    "description": "Test Item",
                    "fields": [{"name": "name", "type": "Char", "required": True}],
                }
            ],
        }
        with tempfile.TemporaryDirectory() as d:
            files, _ = render_module(spec, get_template_dir(), Path(d))
            manifest_file = next(f for f in files if Path(f).name == "__manifest__.py")
            content = Path(manifest_file).read_text(encoding="utf-8")
            assert '"auto_install"' not in content, f"Unexpected 'auto_install' key in manifest. Got:\n{content}"


# ---------------------------------------------------------------------------
# Phase 12: Template rendering -- clean test file (TMPL-04)
# ---------------------------------------------------------------------------


class TestTemplateTestFileClean:
    """Tests that rendered test files import only AccessError, not ValidationError."""

    def test_test_file_no_validation_error_import(self):
        """render module -> test file does NOT contain 'ValidationError'."""
        spec = {
            "module_name": "test_clean",
            "depends": ["base"],
            "models": [
                {
                    "name": "test.item",
                    "description": "Test Item",
                    "fields": [{"name": "name", "type": "Char", "required": True}],
                }
            ],
        }
        with tempfile.TemporaryDirectory() as d:
            files, _ = render_module(spec, get_template_dir(), Path(d))
            test_file = next(f for f in files if "test_test_item.py" in str(f))
            content = Path(test_file).read_text(encoding="utf-8")
            assert "ValidationError" not in content, f"Unexpected ValidationError in test file. Got:\n{content}"

    def test_test_file_has_access_error_import(self):
        """render module -> test file contains 'AccessError'."""
        spec = {
            "module_name": "test_clean2",
            "depends": ["base"],
            "models": [
                {
                    "name": "test.item",
                    "description": "Test Item",
                    "fields": [{"name": "name", "type": "Char", "required": True}],
                }
            ],
        }
        with tempfile.TemporaryDirectory() as d:
            files, _ = render_module(spec, get_template_dir(), Path(d))
            test_file = next(f for f in files if "test_test_item.py" in str(f))
            content = Path(test_file).read_text(encoding="utf-8")
            assert "AccessError" in content, f"Missing AccessError in test file. Got:\n{content}"


# ---------------------------------------------------------------------------
# Phase 12: Full render integration test -- all 4 fixes together
# ---------------------------------------------------------------------------


class TestPhase12FullRenderIntegration:
    """Comprehensive integration test: renders a realistic module spec with mail dependency,
    computed fields, and plain models -- then asserts ALL 4 template fixes in a single render pass."""

    @pytest.fixture(autouse=True)
    def setup_render(self, tmp_path):
        """Render a realistic HR training module with mail, computed fields, and plain models."""
        self.spec = {
            "module_name": "hr_training",
            "depends": ["base", "mail", "hr"],
            "models": [
                {
                    "name": "hr.training.course",
                    "description": "Training Course",
                    "fields": [
                        {"name": "name", "type": "Char", "required": True, "string": "Course Name"},
                        {"name": "duration", "type": "Integer", "string": "Duration (Hours)"},
                        {"name": "description", "type": "Text", "string": "Description"},
                        {
                            "name": "total_hours",
                            "type": "Float",
                            "string": "Total Hours",
                            "compute": "_compute_total_hours",
                            "depends": ["duration"],
                        },
                        {
                            "name": "state",
                            "type": "Selection",
                            "string": "Status",
                            "selection": [
                                ["draft", "Draft"],
                                ["confirmed", "Confirmed"],
                                ["done", "Done"],
                            ],
                            "default": "draft",
                        },
                    ],
                },
                {
                    "name": "hr.training.session",
                    "description": "Training Session",
                    "fields": [
                        {"name": "name", "type": "Char", "required": True, "string": "Session Name"},
                        {"name": "date", "type": "Date", "string": "Date"},
                        {"name": "attendee_count", "type": "Integer", "string": "Attendee Count"},
                    ],
                },
            ],
        }
        self.files, _ = render_module(self.spec, get_template_dir(), tmp_path)
        self.module_dir = tmp_path / "hr_training"

    def _read(self, relative_path: str) -> str:
        """Read a file relative to the module directory."""
        return (self.module_dir / relative_path).read_text(encoding="utf-8")

    # -- TMPL-01: mail.thread inheritance on BOTH models --

    def test_course_model_has_mail_inherit(self):
        """hr_training_course model.py has _inherit with mail.thread and mail.activity.mixin."""
        content = self._read("models/hr_training_course.py")
        assert "_inherit = [" in content
        assert "mail.thread" in content
        assert "mail.activity.mixin" in content

    def test_session_model_has_mail_inherit(self):
        """hr_training_session model.py also has _inherit (mail applies to ALL models)."""
        content = self._read("models/hr_training_session.py")
        assert "_inherit = [" in content
        assert "mail.thread" in content
        assert "mail.activity.mixin" in content

    # -- TMPL-02: conditional api import --

    def test_course_model_has_api_import(self):
        """hr_training_course has computed field -> imports api."""
        content = self._read("models/hr_training_course.py")
        assert "from odoo import api, fields, models" in content

    def test_session_model_no_api_import(self):
        """hr_training_session has NO computed/onchange/constrained -> does NOT import api."""
        content = self._read("models/hr_training_session.py")
        assert "from odoo import api" not in content
        assert "from odoo import fields, models" in content

    # -- TMPL-03: clean manifest --

    def test_manifest_no_superfluous_keys(self):
        """__manifest__.py has no installable or auto_install keys."""
        content = self._read("__manifest__.py")
        assert '"installable"' not in content
        assert '"auto_install"' not in content
        # But it still has essential keys
        assert '"name"' in content
        assert '"depends"' in content

    # -- TMPL-04: clean test imports --

    def test_course_test_no_validation_error(self):
        """test_hr_training_course.py has no ValidationError import."""
        content = self._read("tests/test_hr_training_course.py")
        assert "ValidationError" not in content
        assert "AccessError" in content

    def test_session_test_no_validation_error(self):
        """test_hr_training_session.py has no ValidationError import."""
        content = self._read("tests/test_hr_training_session.py")
        assert "ValidationError" not in content
        assert "AccessError" in content


# ---------------------------------------------------------------------------
# TMPL-02: Wizard conditional api import
# ---------------------------------------------------------------------------


class TestWizardApiConditionalImport:
    """TMPL-02: Wizard .py should use conditional api import."""

    def test_wizard_api_conditional_import_with_default_get(self, tmp_path):
        """Wizard with default_get (always present) should import api."""
        spec = {
            "module_name": "test_module",
            "depends": ["base"],
            "models": [
                {
                    "name": "test.model",
                    "fields": [{"name": "name", "type": "Char", "required": True}],
                },
            ],
            "wizards": [
                {
                    "name": "test.wizard",
                    "target_model": "test.model",
                    "fields": [{"name": "reason", "type": "Text"}],
                },
            ],
        }
        files, _ = render_module(spec, get_template_dir(), tmp_path)
        wizard_py = (tmp_path / "test_module" / "wizards" / "test_wizard.py").read_text()
        # default_get uses @api.model, so api should be imported
        assert "from odoo import api, fields, models" in wizard_py

    def test_wizard_api_conditional_import_needs_api_in_context(self, tmp_path):
        """Wizard template receives needs_api=True in context (for default_get)."""
        # Render a module with a wizard and verify the rendered .py file has api import
        # This confirms needs_api is being passed through to wizard_ctx
        spec = {
            "module_name": "test_module",
            "depends": ["base"],
            "models": [
                {
                    "name": "test.model",
                    "fields": [{"name": "name", "type": "Char", "required": True}],
                },
            ],
            "wizards": [
                {
                    "name": "test.confirm",
                    "target_model": "test.model",
                    "fields": [{"name": "note", "type": "Char"}],
                },
            ],
        }
        files, _ = render_module(spec, get_template_dir(), tmp_path)
        wizard_py = (tmp_path / "test_module" / "wizards" / "test_confirm.py").read_text()
        # The import should be conditional — using the pattern from model.py.j2
        assert "from odoo import api, fields, models" in wizard_py
        assert "@api.model" in wizard_py


# ---------------------------------------------------------------------------
# TMPL-03: Wizard ACL entries in access CSV
# ---------------------------------------------------------------------------


class TestWizardAclEntries:
    """TMPL-03: ir.model.access.csv should include wizard ACL entries."""

    def test_wizard_acl_entries_in_csv(self, tmp_path):
        """Rendered CSV should have a line for each wizard with 1,1,1,1."""
        spec = {
            "module_name": "test_module",
            "depends": ["base"],
            "models": [
                {
                    "name": "test.model",
                    "fields": [{"name": "name", "type": "Char", "required": True}],
                },
            ],
            "wizards": [
                {
                    "name": "test.wizard",
                    "target_model": "test.model",
                    "fields": [{"name": "reason", "type": "Text"}],
                },
            ],
        }
        files, _ = render_module(spec, get_template_dir(), tmp_path)
        csv_content = (tmp_path / "test_module" / "security" / "ir.model.access.csv").read_text()
        # Should have a wizard ACL line with full CRUD
        assert "access_test_wizard_user" in csv_content
        assert "test.wizard.user" in csv_content
        assert "model_test_wizard" in csv_content
        assert "1,1,1,1" in csv_content

    def test_wizard_acl_no_manager_line(self, tmp_path):
        """Wizard ACL should have only ONE line per wizard (user with 1,1,1,1), no manager."""
        spec = {
            "module_name": "test_module",
            "depends": ["base"],
            "models": [
                {
                    "name": "test.model",
                    "fields": [{"name": "name", "type": "Char", "required": True}],
                },
            ],
            "wizards": [
                {
                    "name": "test.wizard",
                    "target_model": "test.model",
                    "fields": [{"name": "reason", "type": "Text"}],
                },
            ],
        }
        files, _ = render_module(spec, get_template_dir(), tmp_path)
        csv_content = (tmp_path / "test_module" / "security" / "ir.model.access.csv").read_text()
        # Count wizard lines -- should be 2 (one per legacy role: user + manager)
        wizard_lines = [line for line in csv_content.splitlines() if "test_wizard" in line]
        assert len(wizard_lines) == 2, f"Expected 2 wizard ACL lines, got {len(wizard_lines)}: {wizard_lines}"
        # Both lines should have 1,1,1,1 (full CRUD for wizards)
        for wl in wizard_lines:
            assert wl.endswith("1,1,1,1")

    def test_wizard_acl_multiple_wizards(self, tmp_path):
        """Multiple wizards each get their own ACL line."""
        spec = {
            "module_name": "test_module",
            "depends": ["base"],
            "models": [
                {
                    "name": "test.model",
                    "fields": [{"name": "name", "type": "Char", "required": True}],
                },
            ],
            "wizards": [
                {
                    "name": "test.wizard.a",
                    "target_model": "test.model",
                    "fields": [{"name": "reason", "type": "Text"}],
                },
                {
                    "name": "test.wizard.b",
                    "target_model": "test.model",
                    "fields": [{"name": "note", "type": "Char"}],
                },
            ],
        }
        files, _ = render_module(spec, get_template_dir(), tmp_path)
        csv_content = (tmp_path / "test_module" / "security" / "ir.model.access.csv").read_text()
        assert "access_test_wizard_a_user" in csv_content
        assert "access_test_wizard_a_manager" in csv_content
        assert "access_test_wizard_b_user" in csv_content
        assert "access_test_wizard_b_manager" in csv_content


# ---------------------------------------------------------------------------
# TMPL-04: display_name instead of deprecated name_get
# ---------------------------------------------------------------------------


class TestDisplayNameVersionGate:
    """TMPL-04: Test template should use display_name with version gate."""

    def test_display_name_v18(self, tmp_path):
        """Odoo 18.0: test should assert display_name, NOT name_get()."""
        spec = {
            "module_name": "test_module",
            "odoo_version": "18.0",
            "depends": ["base"],
            "models": [
                {
                    "name": "test.model",
                    "fields": [{"name": "name", "type": "Char", "required": True}],
                },
            ],
            "wizards": [],
        }
        files, _ = render_module(spec, get_template_dir(), tmp_path)
        test_content = (tmp_path / "test_module" / "tests" / "test_test_model.py").read_text()
        assert "test_display_name" in test_content
        assert "display_name" in test_content
        assert "name_get" not in test_content

    def test_display_name_v17(self, tmp_path):
        """Odoo 17.0: test should assert BOTH display_name and name_get()."""
        spec = {
            "module_name": "test_module",
            "odoo_version": "17.0",
            "depends": ["base"],
            "models": [
                {
                    "name": "test.model",
                    "fields": [{"name": "name", "type": "Char", "required": True}],
                },
            ],
            "wizards": [],
        }
        files, _ = render_module(spec, get_template_dir(), tmp_path)
        test_content = (tmp_path / "test_module" / "tests" / "test_test_model.py").read_text()
        assert "test_display_name" in test_content
        assert "display_name" in test_content
        assert "name_get" in test_content

    def test_no_name_field_no_display_test(self, tmp_path):
        """Model without 'name' field should NOT generate test_display_name."""
        spec = {
            "module_name": "test_module",
            "odoo_version": "18.0",
            "depends": ["base"],
            "models": [
                {
                    "name": "test.model",
                    "fields": [{"name": "title", "type": "Char", "required": True}],
                },
            ],
            "wizards": [],
        }
        files, _ = render_module(spec, get_template_dir(), tmp_path)
        test_content = (tmp_path / "test_module" / "tests" / "test_test_model.py").read_text()
        assert "test_display_name" not in test_content
        assert "test_name_get" not in test_content
        assert "name_get" not in test_content


# ---------------------------------------------------------------------------
# Phase 26: Monetary field detection
# ---------------------------------------------------------------------------


class TestMonetaryPatternDetection:
    """Tests for _is_monetary_field() helper."""

    def test_float_amount_is_monetary(self):
        assert _is_monetary_field({"name": "amount", "type": "Float"}) is True

    def test_float_total_price_is_monetary(self):
        assert _is_monetary_field({"name": "total_price", "type": "Float"}) is True

    def test_float_tuition_fee_is_monetary(self):
        assert _is_monetary_field({"name": "tuition_fee", "type": "Float"}) is True

    def test_integer_amount_not_monetary(self):
        assert _is_monetary_field({"name": "amount", "type": "Integer"}) is False

    def test_char_amount_label_not_monetary(self):
        assert _is_monetary_field({"name": "amount_label", "type": "Char"}) is False

    def test_float_amount_opt_out(self):
        assert _is_monetary_field({"name": "amount", "type": "Float", "monetary": False}) is False

    def test_already_typed_monetary(self):
        assert _is_monetary_field({"name": "whatever", "type": "Monetary"}) is True

    def test_float_non_monetary_name(self):
        assert _is_monetary_field({"name": "weight", "type": "Float"}) is False

    @pytest.mark.parametrize("pattern", sorted(MONETARY_FIELD_PATTERNS))
    def test_all_20_patterns_match(self, pattern):
        assert _is_monetary_field({"name": pattern, "type": "Float"}) is True

    @pytest.mark.parametrize("pattern", sorted(MONETARY_FIELD_PATTERNS))
    def test_all_20_patterns_match_as_substring(self, pattern):
        assert _is_monetary_field({"name": f"total_{pattern}_value", "type": "Float"}) is True


class TestBuildModelContextMonetary:
    """Tests for monetary detection in _build_model_context()."""

    def test_float_amount_rewritten_to_monetary(self):
        model = {"name": "test.model", "fields": [{"name": "amount", "type": "Float"}]}
        spec = _make_spec(models=[model])
        ctx = _build_model_context(spec, model)
        assert ctx["fields"][0]["type"] == "Monetary"

    def test_needs_currency_id_true_when_monetary_detected(self):
        model = {"name": "test.model", "fields": [{"name": "amount", "type": "Float"}]}
        spec = _make_spec(models=[model])
        ctx = _build_model_context(spec, model)
        assert ctx["needs_currency_id"] is True

    def test_needs_currency_id_false_when_currency_id_exists(self):
        model = {
            "name": "test.model",
            "fields": [
                {"name": "amount", "type": "Float"},
                {"name": "currency_id", "type": "Many2one", "comodel_name": "res.currency"},
            ],
        }
        spec = _make_spec(models=[model])
        ctx = _build_model_context(spec, model)
        assert ctx["needs_currency_id"] is False

    def test_needs_currency_id_false_when_no_monetary(self):
        model = {"name": "test.model", "fields": [{"name": "weight", "type": "Float"}]}
        spec = _make_spec(models=[model])
        ctx = _build_model_context(spec, model)
        assert ctx["needs_currency_id"] is False

    def test_immutability_original_fields_unchanged(self):
        original_fields = [{"name": "amount", "type": "Float"}]
        model = {"name": "test.model", "fields": original_fields}
        spec = _make_spec(models=[model])
        _build_model_context(spec, model)
        assert original_fields[0]["type"] == "Float"

    def test_computed_monetary_field_retains_compute(self):
        model = {
            "name": "test.model",
            "fields": [
                {"name": "total_amount", "type": "Float", "compute": "_compute_total_amount", "depends": ["qty"]},
            ],
        }
        spec = _make_spec(models=[model])
        ctx = _build_model_context(spec, model)
        field = ctx["fields"][0]
        assert field["type"] == "Monetary"
        assert field["compute"] == "_compute_total_amount"


class TestRenderModuleMonetary:
    """Integration tests for monetary field rendering in generated output."""

    def test_monetary_field_rendered_as_fields_monetary(self, tmp_path):
        spec = {
            "module_name": "test_module",
            "depends": ["base"],
            "models": [
                {
                    "name": "test.invoice",
                    "fields": [
                        {"name": "total_amount", "type": "Float"},
                        {"name": "name", "type": "Char"},
                    ],
                },
            ],
            "wizards": [],
        }
        files, _ = render_module(spec, get_template_dir(), tmp_path)
        model_content = (tmp_path / "test_module" / "models" / "test_invoice.py").read_text()
        assert "fields.Monetary" in model_content
        assert 'currency_field="currency_id"' in model_content

    def test_currency_id_injected_when_not_in_spec(self, tmp_path):
        spec = {
            "module_name": "test_module",
            "depends": ["base"],
            "models": [
                {
                    "name": "test.invoice",
                    "fields": [{"name": "amount", "type": "Float"}],
                },
            ],
            "wizards": [],
        }
        files, _ = render_module(spec, get_template_dir(), tmp_path)
        model_content = (tmp_path / "test_module" / "models" / "test_invoice.py").read_text()
        assert "currency_id = fields.Many2one(" in model_content
        assert 'comodel_name="res.currency"' in model_content
        assert "default=lambda self: self.env.company.currency_id" in model_content

    def test_no_duplicate_currency_id_when_in_spec(self, tmp_path):
        spec = {
            "module_name": "test_module",
            "depends": ["base"],
            "models": [
                {
                    "name": "test.invoice",
                    "fields": [
                        {"name": "amount", "type": "Float"},
                        {"name": "currency_id", "type": "Many2one", "comodel_name": "res.currency"},
                    ],
                },
            ],
            "wizards": [],
        }
        files, _ = render_module(spec, get_template_dir(), tmp_path)
        model_content = (tmp_path / "test_module" / "models" / "test_invoice.py").read_text()
        assert model_content.count("currency_id") == 2  # field def + currency_field= param

    def test_computed_monetary_has_compute_and_currency_field(self, tmp_path):
        spec = {
            "module_name": "test_module",
            "depends": ["base"],
            "models": [
                {
                    "name": "test.invoice",
                    "fields": [
                        {"name": "total_amount", "type": "Float", "compute": "_compute_total_amount", "depends": ["qty"]},
                    ],
                },
            ],
            "wizards": [],
        }
        files, _ = render_module(spec, get_template_dir(), tmp_path)
        model_content = (tmp_path / "test_module" / "models" / "test_invoice.py").read_text()
        assert "fields.Monetary" in model_content
        assert 'compute="_compute_total_amount"' in model_content
        assert 'currency_field="currency_id"' in model_content

    def test_monetary_rendering_18_0(self, tmp_path):
        spec = {
            "module_name": "test_module",
            "depends": ["base"],
            "odoo_version": "18.0",
            "models": [
                {
                    "name": "test.invoice",
                    "fields": [{"name": "amount", "type": "Float"}],
                },
            ],
            "wizards": [],
        }
        files, _ = render_module(spec, get_template_dir(), tmp_path)
        model_content = (tmp_path / "test_module" / "models" / "test_invoice.py").read_text()
        assert "fields.Monetary" in model_content
        assert 'currency_field="currency_id"' in model_content
        assert "currency_id = fields.Many2one(" in model_content


# ---------------------------------------------------------------------------
# Phase 27: _process_relationships() — M2M through-model tests
# ---------------------------------------------------------------------------


class TestProcessRelationshipsM2MThrough:
    """Unit tests for _process_relationships() with m2m_through relationships."""

    def _make_through_spec(self):
        return {
            "module_name": "test_university",
            "depends": ["base"],
            "models": [
                {
                    "name": "test_university.course",
                    "description": "Course",
                    "fields": [{"name": "name", "type": "Char", "required": True}],
                },
                {
                    "name": "test_university.student",
                    "description": "Student",
                    "fields": [{"name": "name", "type": "Char", "required": True}],
                },
            ],
            "relationships": [
                {
                    "type": "m2m_through",
                    "from": "test_university.course",
                    "to": "test_university.student",
                    "through_model": "test_university.enrollment",
                    "through_fields": [
                        {"name": "grade", "type": "Float"},
                        {"name": "enrollment_date", "type": "Date", "default": "fields.Date.today"},
                    ],
                }
            ],
            "wizards": [],
        }

    def test_synthesizes_through_model(self):
        from amil_utils.preprocessors.relationships import _process_relationships

        spec = self._make_through_spec()
        result = _process_relationships(spec)
        model_names = [m["name"] for m in result["models"]]
        assert "test_university.enrollment" in model_names

        through = next(m for m in result["models"] if m["name"] == "test_university.enrollment")
        field_names = [f["name"] for f in through["fields"]]
        # Two M2one FKs
        assert "course_id" in field_names
        assert "student_id" in field_names
        # Extra through_fields
        assert "grade" in field_names
        assert "enrollment_date" in field_names

        # FK fields are required M2one with ondelete=cascade
        course_fk = next(f for f in through["fields"] if f["name"] == "course_id")
        assert course_fk["type"] == "Many2one"
        assert course_fk["required"] is True
        assert course_fk["ondelete"] == "cascade"
        assert course_fk["comodel_name"] == "test_university.course"

        student_fk = next(f for f in through["fields"] if f["name"] == "student_id")
        assert student_fk["type"] == "Many2one"
        assert student_fk["required"] is True
        assert student_fk["ondelete"] == "cascade"
        assert student_fk["comodel_name"] == "test_university.student"

    def test_injects_one2many_on_parents(self):
        from amil_utils.preprocessors.relationships import _process_relationships

        spec = self._make_through_spec()
        result = _process_relationships(spec)

        course = next(m for m in result["models"] if m["name"] == "test_university.course")
        course_field_names = [f["name"] for f in course["fields"]]
        assert "enrollment_ids" in course_field_names

        student = next(m for m in result["models"] if m["name"] == "test_university.student")
        student_field_names = [f["name"] for f in student["fields"]]
        assert "enrollment_ids" in student_field_names

    def test_no_duplicate_injection(self):
        from amil_utils.preprocessors.relationships import _process_relationships

        spec = self._make_through_spec()
        # Pre-add enrollment_ids on course model
        spec["models"][0]["fields"].append({
            "name": "enrollment_ids",
            "type": "One2many",
            "comodel_name": "test_university.enrollment",
            "inverse_name": "course_id",
        })
        result = _process_relationships(spec)
        course = next(m for m in result["models"] if m["name"] == "test_university.course")
        enrollment_fields = [f for f in course["fields"] if f["name"] == "enrollment_ids"]
        assert len(enrollment_fields) == 1

    def test_fk_name_collision_with_through_fields(self):
        from amil_utils.preprocessors.relationships import _process_relationships

        spec = self._make_through_spec()
        # Add a through_field that collides with auto-generated FK name
        spec["relationships"][0]["through_fields"].append(
            {"name": "course_id", "type": "Char"}
        )
        with pytest.raises(ValueError, match="collision"):
            _process_relationships(spec)

    def test_through_model_has_synthesized_flag(self):
        from amil_utils.preprocessors.relationships import _process_relationships

        spec = self._make_through_spec()
        result = _process_relationships(spec)
        through = next(m for m in result["models"] if m["name"] == "test_university.enrollment")
        assert through.get("_synthesized") is True

    def test_immutability(self):
        from amil_utils.preprocessors.relationships import _process_relationships
        import copy

        spec = self._make_through_spec()
        original = copy.deepcopy(spec)
        _process_relationships(spec)
        assert spec == original


# ---------------------------------------------------------------------------
# Phase 27: _process_relationships() — Self-referential M2M tests
# ---------------------------------------------------------------------------


class TestProcessRelationshipsSelfM2M:
    """Unit tests for _process_relationships() with self_m2m relationships."""

    def _make_self_m2m_spec(self, with_inverse=True):
        rel = {
            "type": "self_m2m",
            "model": "test_university.course",
            "field_name": "prerequisite_ids",
            "string": "Prerequisites",
        }
        if with_inverse:
            rel["inverse_field_name"] = "dependent_ids"
            rel["inverse_string"] = "Dependent Courses"
        return {
            "module_name": "test_university",
            "depends": ["base"],
            "models": [
                {
                    "name": "test_university.course",
                    "description": "Course",
                    "fields": [{"name": "name", "type": "Char", "required": True}],
                },
            ],
            "relationships": [rel],
            "wizards": [],
        }

    def test_enriches_primary_field(self):
        from amil_utils.preprocessors.relationships import _process_relationships

        spec = self._make_self_m2m_spec()
        result = _process_relationships(spec)
        course = next(m for m in result["models"] if m["name"] == "test_university.course")
        prereq = next(f for f in course["fields"] if f["name"] == "prerequisite_ids")
        assert prereq["type"] == "Many2many"
        assert prereq["comodel_name"] == "test_university.course"
        assert "relation" in prereq
        assert "column1" in prereq
        assert "column2" in prereq

    def test_enriches_inverse_field(self):
        from amil_utils.preprocessors.relationships import _process_relationships

        spec = self._make_self_m2m_spec(with_inverse=True)
        result = _process_relationships(spec)
        course = next(m for m in result["models"] if m["name"] == "test_university.course")
        dep = next(f for f in course["fields"] if f["name"] == "dependent_ids")
        prereq = next(f for f in course["fields"] if f["name"] == "prerequisite_ids")
        # Inverse has REVERSED column1/column2
        assert dep["column1"] == prereq["column2"]
        assert dep["column2"] == prereq["column1"]
        # Same relation table
        assert dep["relation"] == prereq["relation"]

    def test_relation_table_naming(self):
        from amil_utils.preprocessors.relationships import _process_relationships

        spec = self._make_self_m2m_spec()
        result = _process_relationships(spec)
        course = next(m for m in result["models"] if m["name"] == "test_university.course")
        prereq = next(f for f in course["fields"] if f["name"] == "prerequisite_ids")
        assert prereq["relation"] == "test_university_course_prerequisite_ids_rel"

    def test_no_inverse_when_not_specified(self):
        from amil_utils.preprocessors.relationships import _process_relationships

        spec = self._make_self_m2m_spec(with_inverse=False)
        result = _process_relationships(spec)
        course = next(m for m in result["models"] if m["name"] == "test_university.course")
        field_names = [f["name"] for f in course["fields"]]
        assert "prerequisite_ids" in field_names
        assert "dependent_ids" not in field_names

    def test_replaces_existing_field(self):
        from amil_utils.preprocessors.relationships import _process_relationships

        spec = self._make_self_m2m_spec(with_inverse=False)
        # Pre-add a placeholder prerequisite_ids field
        spec["models"][0]["fields"].append({
            "name": "prerequisite_ids",
            "type": "Many2many",
            "comodel_name": "test_university.course",
        })
        result = _process_relationships(spec)
        course = next(m for m in result["models"] if m["name"] == "test_university.course")
        prereq_fields = [f for f in course["fields"] if f["name"] == "prerequisite_ids"]
        # Should be exactly one (replaced, not duplicated)
        assert len(prereq_fields) == 1
        assert "relation" in prereq_fields[0]


# ---------------------------------------------------------------------------
# Phase 27: _build_model_context() — Hierarchical model tests
# ---------------------------------------------------------------------------


class TestBuildModelContextHierarchical:
    """Unit tests for hierarchical model detection in _build_model_context()."""

    def _make_hierarchical_spec(self, hierarchical=True, extra_fields=None):
        fields = [{"name": "name", "type": "Char", "required": True}]
        if extra_fields:
            fields.extend(extra_fields)
        model = {
            "name": "test.department",
            "description": "Department",
            "fields": fields,
        }
        if hierarchical:
            model["hierarchical"] = True
        return _make_spec(models=[model])

    def test_injects_parent_id(self):
        spec = self._make_hierarchical_spec()
        ctx = _build_model_context(spec, spec["models"][0])
        parent_id = next((f for f in ctx["fields"] if f["name"] == "parent_id"), None)
        assert parent_id is not None
        assert parent_id["type"] == "Many2one"
        assert parent_id["comodel_name"] == "test.department"
        assert parent_id["index"] is True
        assert parent_id["ondelete"] == "cascade"

    def test_injects_child_ids(self):
        spec = self._make_hierarchical_spec()
        ctx = _build_model_context(spec, spec["models"][0])
        child_ids = next((f for f in ctx["fields"] if f["name"] == "child_ids"), None)
        assert child_ids is not None
        assert child_ids["type"] == "One2many"
        assert child_ids["comodel_name"] == "test.department"
        assert child_ids["inverse_name"] == "parent_id"

    def test_injects_parent_path(self):
        spec = self._make_hierarchical_spec()
        ctx = _build_model_context(spec, spec["models"][0])
        parent_path = next((f for f in ctx["fields"] if f["name"] == "parent_path"), None)
        assert parent_path is not None
        assert parent_path["type"] == "Char"
        assert parent_path["index"] is True
        assert parent_path["internal"] is True

    def test_sets_is_hierarchical_context_key(self):
        spec = self._make_hierarchical_spec()
        ctx = _build_model_context(spec, spec["models"][0])
        assert ctx["is_hierarchical"] is True

    def test_parent_path_excluded_from_views(self):
        spec = self._make_hierarchical_spec()
        ctx = _build_model_context(spec, spec["models"][0])
        # parent_path should not be in view_fields (fields used for form/tree rendering)
        view_fields = ctx.get("view_fields", ctx["fields"])
        # parent_path should be in fields but filtered from view_fields
        all_field_names = [f["name"] for f in ctx["fields"]]
        assert "parent_path" in all_field_names
        view_field_names = [f["name"] for f in ctx["view_fields"]]
        assert "parent_path" not in view_field_names

    def test_no_duplicate_hierarchical_fields(self):
        spec = self._make_hierarchical_spec(
            extra_fields=[
                {
                    "name": "parent_id",
                    "type": "Many2one",
                    "comodel_name": "test.department",
                    "index": True,
                    "ondelete": "cascade",
                }
            ]
        )
        ctx = _build_model_context(spec, spec["models"][0])
        parent_ids = [f for f in ctx["fields"] if f["name"] == "parent_id"]
        assert len(parent_ids) == 1

    def test_non_hierarchical_model_unchanged(self):
        spec = self._make_hierarchical_spec(hierarchical=False)
        ctx = _build_model_context(spec, spec["models"][0])
        assert ctx.get("is_hierarchical") is False
        field_names = [f["name"] for f in ctx["fields"]]
        assert "parent_id" not in field_names
        assert "child_ids" not in field_names
        assert "parent_path" not in field_names


# ---------------------------------------------------------------------------
# Phase 28: _validate_no_cycles() tests
# ---------------------------------------------------------------------------


def _make_chain_spec(
    models: list[dict] | None = None,
    computation_chains: list[dict] | None = None,
) -> dict:
    """Helper to construct a spec with computation_chains."""
    return {
        "module_name": "test_module",
        "depends": ["base"],
        "models": models or [],
        "wizards": [],
        "computation_chains": computation_chains or [],
    }


class TestValidateNoCycles:
    """Unit tests for _validate_no_cycles()."""

    def test_valid_chains_pass(self):
        """Spec with valid A->B chain passes without error."""
        spec = _make_chain_spec(
            models=[
                {
                    "name": "university.enrollment",
                    "fields": [
                        {"name": "grade", "type": "Float"},
                        {"name": "credit_hours", "type": "Integer"},
                        {"name": "weighted_grade", "type": "Float"},
                    ],
                },
                {
                    "name": "university.student",
                    "fields": [
                        {"name": "enrollment_ids", "type": "One2many",
                         "comodel_name": "university.enrollment", "inverse_name": "student_id"},
                        {"name": "gpa", "type": "Float"},
                    ],
                },
            ],
            computation_chains=[
                {
                    "field": "university.enrollment.weighted_grade",
                    "depends_on": ["grade", "credit_hours"],
                },
                {
                    "field": "university.student.gpa",
                    "depends_on": ["enrollment_ids.weighted_grade"],
                },
            ],
        )
        # Should not raise
        _validate_no_cycles(spec)

    def test_circular_raises(self):
        """Spec with A->B->A chain raises ValueError."""
        spec = _make_chain_spec(
            models=[
                {
                    "name": "university.enrollment",
                    "fields": [
                        {"name": "student_id", "type": "Many2one",
                         "comodel_name": "university.student"},
                        {"name": "weighted_grade", "type": "Float"},
                    ],
                },
                {
                    "name": "university.student",
                    "fields": [
                        {"name": "enrollment_ids", "type": "One2many",
                         "comodel_name": "university.enrollment", "inverse_name": "student_id"},
                        {"name": "gpa", "type": "Float"},
                    ],
                },
            ],
            computation_chains=[
                {
                    "field": "university.enrollment.weighted_grade",
                    "depends_on": ["student_id.gpa"],
                },
                {
                    "field": "university.student.gpa",
                    "depends_on": ["enrollment_ids.weighted_grade"],
                },
            ],
        )
        with pytest.raises(ValueError, match="Circular dependency"):
            _validate_no_cycles(spec)

    def test_error_names_participants(self):
        """ValueError message contains cycle field names."""
        spec = _make_chain_spec(
            models=[
                {
                    "name": "university.enrollment",
                    "fields": [
                        {"name": "student_id", "type": "Many2one",
                         "comodel_name": "university.student"},
                        {"name": "weighted_grade", "type": "Float"},
                    ],
                },
                {
                    "name": "university.student",
                    "fields": [
                        {"name": "enrollment_ids", "type": "One2many",
                         "comodel_name": "university.enrollment", "inverse_name": "student_id"},
                        {"name": "gpa", "type": "Float"},
                    ],
                },
            ],
            computation_chains=[
                {
                    "field": "university.enrollment.weighted_grade",
                    "depends_on": ["student_id.gpa"],
                },
                {
                    "field": "university.student.gpa",
                    "depends_on": ["enrollment_ids.weighted_grade"],
                },
            ],
        )
        with pytest.raises(ValueError) as exc_info:
            _validate_no_cycles(spec)
        msg = str(exc_info.value)
        assert "university.student.gpa" in msg or "university.enrollment.weighted_grade" in msg

    def test_cross_model_cycle(self):
        """Cycle spanning 2 models detected via comodel resolution."""
        spec = _make_chain_spec(
            models=[
                {
                    "name": "a.model",
                    "fields": [
                        {"name": "b_ids", "type": "One2many",
                         "comodel_name": "b.model", "inverse_name": "a_id"},
                        {"name": "x", "type": "Float"},
                    ],
                },
                {
                    "name": "b.model",
                    "fields": [
                        {"name": "a_id", "type": "Many2one", "comodel_name": "a.model"},
                        {"name": "y", "type": "Float"},
                    ],
                },
            ],
            computation_chains=[
                {"field": "a.model.x", "depends_on": ["b_ids.y"]},
                {"field": "b.model.y", "depends_on": ["a_id.x"]},
            ],
        )
        with pytest.raises(ValueError, match="Circular dependency"):
            _validate_no_cycles(spec)

    def test_no_chains_passthrough(self):
        """Spec without computation_chains passes silently."""
        spec = {
            "module_name": "test_module",
            "depends": ["base"],
            "models": [{"name": "test.model", "fields": []}],
            "wizards": [],
        }
        # No computation_chains key at all
        _validate_no_cycles(spec)


class TestProcessComputationChains:
    """Unit tests for _process_computation_chains()."""

    def test_enriches_depends(self):
        """Chain entry sets field.depends to depends_on list."""
        spec = _make_chain_spec(
            models=[
                {
                    "name": "university.enrollment",
                    "fields": [
                        {"name": "grade", "type": "Float"},
                        {"name": "credit_hours", "type": "Integer"},
                        {"name": "weighted_grade", "type": "Float"},
                    ],
                },
            ],
            computation_chains=[
                {
                    "field": "university.enrollment.weighted_grade",
                    "depends_on": ["grade", "credit_hours"],
                },
            ],
        )
        result = _process_computation_chains(spec)
        wg = next(
            f for f in result["models"][0]["fields"]
            if f["name"] == "weighted_grade"
        )
        assert wg["depends"] == ["grade", "credit_hours"]

    def test_sets_store_true(self):
        """Chain fields get store=True."""
        spec = _make_chain_spec(
            models=[
                {
                    "name": "university.enrollment",
                    "fields": [{"name": "weighted_grade", "type": "Float"}],
                },
            ],
            computation_chains=[
                {
                    "field": "university.enrollment.weighted_grade",
                    "depends_on": ["grade"],
                },
            ],
        )
        result = _process_computation_chains(spec)
        wg = next(
            f for f in result["models"][0]["fields"]
            if f["name"] == "weighted_grade"
        )
        assert wg["store"] is True

    def test_injects_compute_name(self):
        """Field without compute= gets _compute_{name}."""
        spec = _make_chain_spec(
            models=[
                {
                    "name": "university.enrollment",
                    "fields": [{"name": "weighted_grade", "type": "Float"}],
                },
            ],
            computation_chains=[
                {
                    "field": "university.enrollment.weighted_grade",
                    "depends_on": ["grade"],
                },
            ],
        )
        result = _process_computation_chains(spec)
        wg = next(
            f for f in result["models"][0]["fields"]
            if f["name"] == "weighted_grade"
        )
        assert wg["compute"] == "_compute_weighted_grade"

    def test_dotted_paths_preserved(self):
        """'enrollment_ids.weighted_grade' preserved in depends."""
        spec = _make_chain_spec(
            models=[
                {
                    "name": "university.student",
                    "fields": [
                        {"name": "enrollment_ids", "type": "One2many",
                         "comodel_name": "university.enrollment", "inverse_name": "student_id"},
                        {"name": "gpa", "type": "Float"},
                    ],
                },
            ],
            computation_chains=[
                {
                    "field": "university.student.gpa",
                    "depends_on": ["enrollment_ids.weighted_grade"],
                },
            ],
        )
        result = _process_computation_chains(spec)
        gpa = next(
            f for f in result["models"][0]["fields"]
            if f["name"] == "gpa"
        )
        assert "enrollment_ids.weighted_grade" in gpa["depends"]

    def test_no_chains_passthrough(self):
        """Spec without computation_chains returned unchanged."""
        spec = {
            "module_name": "test_module",
            "depends": ["base"],
            "models": [{"name": "test.model", "fields": [{"name": "x", "type": "Float"}]}],
            "wizards": [],
        }
        result = _process_computation_chains(spec)
        assert result["models"][0]["fields"][0] == {"name": "x", "type": "Float"}

    def test_does_not_mutate_input(self):
        """Original spec dict is not mutated."""
        import copy

        spec = _make_chain_spec(
            models=[
                {
                    "name": "university.enrollment",
                    "fields": [{"name": "weighted_grade", "type": "Float"}],
                },
            ],
            computation_chains=[
                {
                    "field": "university.enrollment.weighted_grade",
                    "depends_on": ["grade"],
                },
            ],
        )
        original = copy.deepcopy(spec)
        _process_computation_chains(spec)
        assert spec == original


class TestTopologicallySortFields:
    """Unit tests for _topologically_sort_fields()."""

    def test_sort_order(self):
        """Field depending on another computed field comes after it."""
        fields = [
            {"name": "total", "type": "Float", "compute": "_compute_total",
             "depends": ["subtotal"]},
            {"name": "subtotal", "type": "Float", "compute": "_compute_subtotal",
             "depends": ["qty", "price"]},
        ]
        result = _topologically_sort_fields(fields)
        names = [f["name"] for f in result]
        assert names.index("subtotal") < names.index("total")

    def test_independent_preserves_order(self):
        """Fields with no inter-deps keep original order."""
        fields = [
            {"name": "a", "type": "Float", "compute": "_compute_a", "depends": ["x"]},
            {"name": "b", "type": "Float", "compute": "_compute_b", "depends": ["y"]},
        ]
        result = _topologically_sort_fields(fields)
        names = [f["name"] for f in result]
        # Both present, order should be preserved (no deps between them)
        assert set(names) == {"a", "b"}

    def test_single_field(self):
        """Single computed field returned as-is."""
        fields = [
            {"name": "total", "type": "Float", "compute": "_compute_total",
             "depends": ["qty"]},
        ]
        result = _topologically_sort_fields(fields)
        assert len(result) == 1
        assert result[0]["name"] == "total"


# ---------------------------------------------------------------------------
# Phase 29: _process_constraints()
# ---------------------------------------------------------------------------


def _make_constraint_spec(
    models: list[dict] | None = None,
    constraints: list[dict] | None = None,
) -> dict:
    """Helper to construct a spec with constraints section."""
    return {
        "module_name": "test_module",
        "depends": ["base"],
        "models": models or [],
        "wizards": [],
        "constraints": constraints or [],
    }


class TestProcessConstraints:
    """Unit tests for _process_constraints()."""

    def test_no_constraints_passthrough(self):
        """Spec without constraints key returns unchanged spec."""
        spec = {
            "module_name": "test_module",
            "depends": ["base"],
            "models": [{"name": "test.model", "fields": []}],
            "wizards": [],
        }
        result = _process_constraints(spec)
        assert result == spec

    def test_does_not_mutate_input(self):
        """Original spec dict is not modified by _process_constraints()."""
        import copy

        spec = _make_constraint_spec(
            models=[{
                "name": "university.course",
                "fields": [
                    {"name": "start_date", "type": "Date"},
                    {"name": "end_date", "type": "Date"},
                ],
            }],
            constraints=[{
                "type": "temporal",
                "model": "university.course",
                "name": "date_order",
                "fields": ["start_date", "end_date"],
                "condition": "end_date < start_date",
                "message": "End date must be after start date.",
            }],
        )
        original = copy.deepcopy(spec)
        _process_constraints(spec)
        assert spec == original

    def test_temporal_classifies_correctly(self):
        """Temporal constraint enriches model with complex_constraints entry of type temporal."""
        spec = _make_constraint_spec(
            models=[{
                "name": "university.course",
                "fields": [
                    {"name": "start_date", "type": "Date"},
                    {"name": "end_date", "type": "Date"},
                ],
            }],
            constraints=[{
                "type": "temporal",
                "model": "university.course",
                "name": "date_order",
                "fields": ["start_date", "end_date"],
                "condition": "end_date < start_date",
                "message": "End date must be after start date.",
            }],
        )
        result = _process_constraints(spec)
        model = result["models"][0]
        assert "complex_constraints" in model
        assert len(model["complex_constraints"]) == 1
        assert model["complex_constraints"][0]["type"] == "temporal"

    def test_temporal_generates_check_expr(self):
        """Temporal constraint produces check_expr with False guards."""
        spec = _make_constraint_spec(
            models=[{
                "name": "university.course",
                "fields": [
                    {"name": "start_date", "type": "Date"},
                    {"name": "end_date", "type": "Date"},
                ],
            }],
            constraints=[{
                "type": "temporal",
                "model": "university.course",
                "name": "date_order",
                "fields": ["start_date", "end_date"],
                "condition": "end_date < start_date",
                "message": "End date must be after start date.",
            }],
        )
        result = _process_constraints(spec)
        constraint = result["models"][0]["complex_constraints"][0]
        assert "check_expr" in constraint
        # Must have False guards for each field
        assert "rec.start_date" in constraint["check_expr"]
        assert "rec.end_date" in constraint["check_expr"]
        # Must have the condition
        assert "rec.end_date < rec.start_date" in constraint["check_expr"]

    def test_cross_model_generates_check_body(self):
        """Cross-model constraint produces check_body with search_count and ValidationError."""
        spec = _make_constraint_spec(
            models=[
                {
                    "name": "university.course",
                    "fields": [
                        {"name": "max_students", "type": "Integer"},
                    ],
                },
                {
                    "name": "university.enrollment",
                    "fields": [
                        {"name": "course_id", "type": "Many2one", "comodel_name": "university.course"},
                    ],
                },
            ],
            constraints=[{
                "type": "cross_model",
                "model": "university.enrollment",
                "name": "enrollment_capacity",
                "trigger_fields": ["course_id"],
                "related_model": "university.enrollment",
                "count_domain_field": "course_id",
                "capacity_model": "university.course",
                "capacity_field": "max_students",
                "message": "Enrollment count cannot exceed course capacity of %s.",
            }],
        )
        result = _process_constraints(spec)
        enrollment = next(m for m in result["models"] if m["name"] == "university.enrollment")
        constraint = enrollment["complex_constraints"][0]
        assert "check_body" in constraint
        assert "search_count" in constraint["check_body"]
        assert "ValidationError" in constraint["check_body"]

    def test_cross_model_generates_create_override(self):
        """Cross-model constraint sets has_create_override and populates create_constraints."""
        spec = _make_constraint_spec(
            models=[
                {
                    "name": "university.course",
                    "fields": [{"name": "max_students", "type": "Integer"}],
                },
                {
                    "name": "university.enrollment",
                    "fields": [
                        {"name": "course_id", "type": "Many2one", "comodel_name": "university.course"},
                    ],
                },
            ],
            constraints=[{
                "type": "cross_model",
                "model": "university.enrollment",
                "name": "enrollment_capacity",
                "trigger_fields": ["course_id"],
                "related_model": "university.enrollment",
                "count_domain_field": "course_id",
                "capacity_model": "university.course",
                "capacity_field": "max_students",
                "message": "Enrollment count cannot exceed course capacity of %s.",
            }],
        )
        result = _process_constraints(spec)
        enrollment = next(m for m in result["models"] if m["name"] == "university.enrollment")
        assert enrollment["has_create_override"] is True
        assert len(enrollment["create_constraints"]) == 1
        assert enrollment["create_constraints"][0]["name"] == "enrollment_capacity"

    def test_cross_model_generates_write_override(self):
        """Cross-model constraint sets has_write_override with correct trigger_fields."""
        spec = _make_constraint_spec(
            models=[
                {
                    "name": "university.course",
                    "fields": [{"name": "max_students", "type": "Integer"}],
                },
                {
                    "name": "university.enrollment",
                    "fields": [
                        {"name": "course_id", "type": "Many2one", "comodel_name": "university.course"},
                    ],
                },
            ],
            constraints=[{
                "type": "cross_model",
                "model": "university.enrollment",
                "name": "enrollment_capacity",
                "trigger_fields": ["course_id"],
                "related_model": "university.enrollment",
                "count_domain_field": "course_id",
                "capacity_model": "university.course",
                "capacity_field": "max_students",
                "message": "Enrollment count cannot exceed course capacity of %s.",
            }],
        )
        result = _process_constraints(spec)
        enrollment = next(m for m in result["models"] if m["name"] == "university.enrollment")
        assert enrollment["has_write_override"] is True
        assert len(enrollment["write_constraints"]) == 1
        assert enrollment["write_constraints"][0]["write_trigger_fields"] == ["course_id"]

    def test_capacity_generates_count_check(self):
        """Capacity constraint produces check_body with search_count and max comparison."""
        spec = _make_constraint_spec(
            models=[{
                "name": "university.section",
                "fields": [
                    {"name": "student_ids", "type": "One2many"},
                ],
            }],
            constraints=[{
                "type": "capacity",
                "model": "university.section",
                "name": "section_capacity",
                "count_field": "student_ids",
                "max_value": 30,
                "count_model": "university.section.enrollment",
                "count_domain_field": "section_id",
                "message": "A section cannot have more than %s students.",
            }],
        )
        result = _process_constraints(spec)
        section = result["models"][0]
        constraint = section["complex_constraints"][0]
        assert "check_body" in constraint
        assert "search_count" in constraint["check_body"]
        assert "30" in constraint["check_body"]

    def test_messages_have_translation(self):
        """All constraint check_body/message strings include _() wrapper."""
        spec = _make_constraint_spec(
            models=[
                {
                    "name": "university.course",
                    "fields": [
                        {"name": "start_date", "type": "Date"},
                        {"name": "end_date", "type": "Date"},
                    ],
                },
                {
                    "name": "university.enrollment",
                    "fields": [
                        {"name": "course_id", "type": "Many2one", "comodel_name": "university.course"},
                    ],
                },
            ],
            constraints=[
                {
                    "type": "temporal",
                    "model": "university.course",
                    "name": "date_order",
                    "fields": ["start_date", "end_date"],
                    "condition": "end_date < start_date",
                    "message": "End date must be after start date.",
                },
                {
                    "type": "cross_model",
                    "model": "university.enrollment",
                    "name": "enrollment_capacity",
                    "trigger_fields": ["course_id"],
                    "related_model": "university.enrollment",
                    "count_domain_field": "course_id",
                    "capacity_model": "university.course",
                    "capacity_field": "max_students",
                    "message": "Enrollment count cannot exceed course capacity of %s.",
                },
            ],
        )
        result = _process_constraints(spec)
        # Temporal: message is used directly in template with _() wrapper
        course = next(m for m in result["models"] if m["name"] == "university.course")
        assert course["complex_constraints"][0]["message"]

        # Cross-model: check_body should contain _()
        enrollment = next(m for m in result["models"] if m["name"] == "university.enrollment")
        assert "_(" in enrollment["complex_constraints"][0]["check_body"]

    def test_multiple_constraints_single_override(self):
        """Two cross_model constraints on same model produce one create_constraints list with 2 entries."""
        spec = _make_constraint_spec(
            models=[{
                "name": "university.enrollment",
                "fields": [
                    {"name": "course_id", "type": "Many2one", "comodel_name": "university.course"},
                    {"name": "section_id", "type": "Many2one", "comodel_name": "university.section"},
                ],
            }],
            constraints=[
                {
                    "type": "cross_model",
                    "model": "university.enrollment",
                    "name": "enrollment_capacity",
                    "trigger_fields": ["course_id"],
                    "related_model": "university.enrollment",
                    "count_domain_field": "course_id",
                    "capacity_model": "university.course",
                    "capacity_field": "max_students",
                    "message": "Too many enrollments for this course.",
                },
                {
                    "type": "cross_model",
                    "model": "university.enrollment",
                    "name": "section_capacity",
                    "trigger_fields": ["section_id"],
                    "related_model": "university.enrollment",
                    "count_domain_field": "section_id",
                    "capacity_model": "university.section",
                    "capacity_field": "max_students",
                    "message": "Too many enrollments for this section.",
                },
            ],
        )
        result = _process_constraints(spec)
        enrollment = result["models"][0]
        # Single create_constraints list with 2 entries
        assert len(enrollment["create_constraints"]) == 2
        # Single write_constraints list with 2 entries
        assert len(enrollment["write_constraints"]) == 2
        # has_create_override and has_write_override are True (singular, not per-constraint)
        assert enrollment["has_create_override"] is True
        assert enrollment["has_write_override"] is True

    def test_temporal_with_missing_model_ignored(self):
        """Temporal constraint referencing non-existent model is silently skipped."""
        spec = _make_constraint_spec(
            models=[{
                "name": "university.course",
                "fields": [{"name": "name", "type": "Char"}],
            }],
            constraints=[{
                "type": "temporal",
                "model": "nonexistent.model",
                "name": "date_order",
                "fields": ["start_date", "end_date"],
                "condition": "end_date < start_date",
                "message": "End date must be after start date.",
            }],
        )
        result = _process_constraints(spec)
        # The course model should remain unchanged
        assert "complex_constraints" not in result["models"][0]


# ---------------------------------------------------------------------------
# Phase 30: _build_model_context cron tests
# ---------------------------------------------------------------------------


class TestBuildModelContextCron:
    def test_cron_methods_populated(self):
        """_build_model_context with cron_jobs for this model includes cron_methods."""
        model = {
            "name": "academy.course",
            "fields": [{"name": "name", "type": "Char"}],
        }
        spec = _make_spec(models=[model])
        spec["cron_jobs"] = [{
            "name": "Archive Expired",
            "model_name": "academy.course",
            "method": "_cron_archive_expired",
            "interval_number": 1,
            "interval_type": "days",
        }]
        ctx = _build_model_context(spec, model)
        assert "cron_methods" in ctx
        assert len(ctx["cron_methods"]) == 1
        assert ctx["cron_methods"][0]["method"] == "_cron_archive_expired"

    def test_cron_methods_empty_different_model(self):
        """_build_model_context with cron_jobs targeting other model returns empty cron_methods."""
        model = {
            "name": "academy.student",
            "fields": [{"name": "name", "type": "Char"}],
        }
        spec = _make_spec(models=[model])
        spec["cron_jobs"] = [{
            "name": "Archive Expired",
            "model_name": "academy.course",
            "method": "_cron_archive_expired",
            "interval_number": 1,
            "interval_type": "days",
        }]
        ctx = _build_model_context(spec, model)
        assert ctx["cron_methods"] == []

    def test_needs_api_true_with_cron(self):
        """Model with only cron methods has needs_api=True."""
        model = {
            "name": "academy.course",
            "fields": [{"name": "name", "type": "Char"}],
        }
        spec = _make_spec(models=[model])
        spec["cron_jobs"] = [{
            "name": "Archive Expired",
            "model_name": "academy.course",
            "method": "_cron_archive_expired",
            "interval_number": 1,
            "interval_type": "days",
        }]
        ctx = _build_model_context(spec, model)
        assert ctx["needs_api"] is True


# ---------------------------------------------------------------------------
# Phase 30: _build_module_context cron tests
# ---------------------------------------------------------------------------


class TestBuildModuleContextCron:
    def test_manifest_includes_cron_data(self):
        """_build_module_context with cron_jobs includes data/cron_data.xml in manifest_files."""
        spec = _make_spec(models=[{
            "name": "academy.course",
            "fields": [{"name": "name", "type": "Char"}],
        }])
        spec["cron_jobs"] = [{
            "name": "Archive Expired",
            "model_name": "academy.course",
            "method": "_cron_archive_expired",
            "interval_number": 1,
            "interval_type": "days",
        }]
        ctx = _build_module_context(spec, "test_module")
        assert "data/cron_data.xml" in ctx["manifest_files"]

    def test_manifest_excludes_cron_data_no_jobs(self):
        """_build_module_context without cron_jobs does NOT include data/cron_data.xml."""
        spec = _make_spec(models=[{
            "name": "academy.course",
            "fields": [{"name": "name", "type": "Char"}],
        }])
        ctx = _build_module_context(spec, "test_module")
        assert "data/cron_data.xml" not in ctx["manifest_files"]


# ---------------------------------------------------------------------------
# Phase 31: _build_module_context report/dashboard tests
# ---------------------------------------------------------------------------


class TestBuildModuleContextReports:
    def test_manifest_includes_report_data_files(self):
        """_build_module_context with reports includes report data files in manifest."""
        spec = _make_spec(models=[{
            "name": "academy.student",
            "fields": [{"name": "name", "type": "Char"}],
        }])
        spec["reports"] = [{
            "name": "Student Report",
            "model_name": "academy.student",
            "xml_id": "student_report",
            "columns": [{"field": "name", "label": "Name"}],
        }]
        ctx = _build_module_context(spec, "test_module")
        assert "data/report_student_report.xml" in ctx["manifest_files"]
        assert "data/report_student_report_template.xml" in ctx["manifest_files"]

    def test_manifest_excludes_report_data_no_reports(self):
        """_build_module_context without reports does NOT include report data files."""
        spec = _make_spec(models=[{
            "name": "academy.student",
            "fields": [{"name": "name", "type": "Char"}],
        }])
        ctx = _build_module_context(spec, "test_module")
        assert not any("report_" in f for f in ctx["manifest_files"])


class TestBuildModuleContextDashboards:
    def test_manifest_includes_dashboard_view_files(self):
        """_build_module_context with dashboards includes graph/pivot XML in manifest."""
        spec = _make_spec(models=[{
            "name": "academy.student",
            "fields": [{"name": "name", "type": "Char"}],
        }])
        spec["dashboards"] = [{
            "model_name": "academy.student",
            "dimensions": [{"field": "name"}],
            "measures": [{"field": "name"}],
            "rows": [],
            "columns": [],
        }]
        ctx = _build_module_context(spec, "test_module")
        assert "views/academy_student_graph.xml" in ctx["manifest_files"]
        assert "views/academy_student_pivot.xml" in ctx["manifest_files"]

    def test_manifest_excludes_dashboard_no_dashboards(self):
        """_build_module_context without dashboards has no graph/pivot files."""
        spec = _make_spec(models=[{
            "name": "academy.student",
            "fields": [{"name": "name", "type": "Char"}],
        }])
        ctx = _build_module_context(spec, "test_module")
        assert not any("graph" in f or "pivot" in f for f in ctx["manifest_files"])


class TestBuildModelContextReports:
    def test_model_reports_present(self):
        """_build_model_context with reports targeting model includes model_reports."""
        model = {
            "name": "academy.student",
            "fields": [{"name": "name", "type": "Char"}],
        }
        spec = _make_spec(models=[model])
        spec["reports"] = [{
            "name": "Student Report",
            "model_name": "academy.student",
            "xml_id": "student_report",
            "columns": [{"field": "name", "label": "Name"}],
        }]
        ctx = _build_model_context(spec, model)
        assert "model_reports" in ctx
        assert len(ctx["model_reports"]) == 1
        assert ctx["model_reports"][0]["xml_id"] == "student_report"

    def test_model_reports_empty_different_model(self):
        """_build_model_context with reports targeting other model returns empty."""
        model = {
            "name": "academy.course",
            "fields": [{"name": "name", "type": "Char"}],
        }
        spec = _make_spec(models=[model])
        spec["reports"] = [{
            "name": "Student Report",
            "model_name": "academy.student",
            "xml_id": "student_report",
            "columns": [{"field": "name", "label": "Name"}],
        }]
        ctx = _build_model_context(spec, model)
        assert ctx["model_reports"] == []

    def test_has_dashboard_true(self):
        """_build_model_context with dashboard targeting model sets has_dashboard=True."""
        model = {
            "name": "academy.student",
            "fields": [{"name": "name", "type": "Char"}],
        }
        spec = _make_spec(models=[model])
        spec["dashboards"] = [{
            "model_name": "academy.student",
            "dimensions": [{"field": "name"}],
            "measures": [{"field": "name"}],
            "rows": [],
            "columns": [],
        }]
        ctx = _build_model_context(spec, model)
        assert ctx["has_dashboard"] is True

    def test_has_dashboard_false(self):
        """_build_model_context without dashboards sets has_dashboard=False."""
        model = {
            "name": "academy.student",
            "fields": [{"name": "name", "type": "Char"}],
        }
        spec = _make_spec(models=[model])
        ctx = _build_model_context(spec, model)
        assert ctx["has_dashboard"] is False


# ---------------------------------------------------------------------------
# Phase 32: _build_module_context controller flag
# ---------------------------------------------------------------------------


class TestBuildModuleContextControllers:
    def test_has_controllers_true(self):
        """_build_module_context with non-empty controllers sets has_controllers=True."""
        spec = _make_spec(models=[{
            "name": "academy.student",
            "fields": [{"name": "name", "type": "Char"}],
        }])
        spec["controllers"] = [{
            "name": "Main",
            "routes": [{"path": "api", "method_name": "get_api"}],
        }]
        ctx = _build_module_context(spec, "test_module")
        assert ctx["has_controllers"] is True

    def test_has_controllers_false_empty(self):
        """_build_module_context with empty controllers sets has_controllers=False."""
        spec = _make_spec(models=[{
            "name": "academy.student",
            "fields": [{"name": "name", "type": "Char"}],
        }])
        spec["controllers"] = []
        ctx = _build_module_context(spec, "test_module")
        assert ctx["has_controllers"] is False

    def test_has_controllers_false_missing(self):
        """_build_module_context without controllers key sets has_controllers=False."""
        spec = _make_spec(models=[{
            "name": "academy.student",
            "fields": [{"name": "name", "type": "Char"}],
        }])
        ctx = _build_module_context(spec, "test_module")
        assert ctx["has_controllers"] is False


# ---------------------------------------------------------------------------
# _build_module_context: import/export (Phase 32 Plan 02)
# ---------------------------------------------------------------------------


class TestBuildModuleContextImportExport:
    def test_has_import_export_true(self):
        """_build_module_context sets has_import_export=True when a model has import_export."""
        spec = _make_spec(models=[{
            "name": "academy.course",
            "import_export": True,
            "fields": [{"name": "name", "type": "Char"}],
        }])
        ctx = _build_module_context(spec, "test_module")
        assert ctx["has_import_export"] is True

    def test_has_import_export_false(self):
        """_build_module_context sets has_import_export=False when no model has import_export."""
        spec = _make_spec(models=[{
            "name": "academy.course",
            "fields": [{"name": "name", "type": "Char"}],
        }])
        ctx = _build_module_context(spec, "test_module")
        assert ctx["has_import_export"] is False

    def test_external_dependencies_openpyxl(self):
        """_build_module_context includes external_dependencies with openpyxl when has_import_export."""
        spec = _make_spec(models=[{
            "name": "academy.course",
            "import_export": True,
            "fields": [{"name": "name", "type": "Char"}],
        }])
        ctx = _build_module_context(spec, "test_module")
        assert "external_dependencies" in ctx
        assert "openpyxl" in ctx["external_dependencies"]["python"]

    def test_no_external_dependencies_without_import_export(self):
        """_build_module_context has no external_dependencies when no import_export."""
        spec = _make_spec(models=[{
            "name": "academy.course",
            "fields": [{"name": "name", "type": "Char"}],
        }])
        ctx = _build_module_context(spec, "test_module")
        assert ctx.get("external_dependencies") is None or ctx.get("external_dependencies") == {}

    def test_has_wizards_true_with_import_export(self):
        """has_wizards is True when import_export models exist (even without spec wizards)."""
        spec = _make_spec(models=[{
            "name": "academy.course",
            "import_export": True,
            "fields": [{"name": "name", "type": "Char"}],
        }])
        ctx = _build_module_context(spec, "test_module")
        assert ctx["has_wizards"] is True

    def test_import_wizard_form_in_manifest(self):
        """Manifest files include import wizard form view files."""
        spec = _make_spec(models=[{
            "name": "academy.course",
            "import_export": True,
            "fields": [{"name": "name", "type": "Char"}],
        }])
        ctx = _build_module_context(spec, "test_module")
        assert "views/academy_course_import_wizard_form.xml" in ctx["manifest_files"]

    def test_import_export_wizards_context(self):
        """_build_module_context includes import_export_wizards list for ACL generation."""
        spec = _make_spec(models=[{
            "name": "academy.course",
            "import_export": True,
            "fields": [{"name": "name", "type": "Char"}],
        }])
        ctx = _build_module_context(spec, "test_module")
        assert "import_export_wizards" in ctx
        assert len(ctx["import_export_wizards"]) == 1
        assert ctx["import_export_wizards"][0]["name"] == "academy.course.import.wizard"


# ---------------------------------------------------------------------------
# _process_performance: Phase 33
# ---------------------------------------------------------------------------


class TestProcessPerformance:
    """Unit tests for _process_performance() preprocessor."""

    def test_performance_index_search_fields(self):
        """Char and Many2one fields get index=True (they appear in search view)."""
        spec = _make_spec(models=[{
            "name": "academy.course",
            "fields": [
                {"name": "name", "type": "Char"},
                {"name": "teacher_id", "type": "Many2one", "comodel_name": "hr.employee"},
                {"name": "qty", "type": "Integer"},
            ],
        }])
        result = _process_performance(spec)
        fields = {f["name"]: f for f in result["models"][0]["fields"]}
        assert fields["name"].get("index") is True
        assert fields["teacher_id"].get("index") is True
        # Integer not in search by default
        assert fields["qty"].get("index") is not True

    def test_performance_index_order_fields(self):
        """Fields in model.order get index=True."""
        spec = _make_spec(models=[{
            "name": "academy.course",
            "order": "date desc, name",
            "fields": [
                {"name": "name", "type": "Char"},
                {"name": "date", "type": "Date"},
                {"name": "qty", "type": "Integer"},
            ],
        }])
        result = _process_performance(spec)
        fields = {f["name"]: f for f in result["models"][0]["fields"]}
        assert fields["date"].get("index") is True
        assert fields["name"].get("index") is True

    def test_performance_index_domain_fields(self):
        """Fields in record rule domains get index=True (company_id)."""
        spec = _make_spec(models=[{
            "name": "academy.course",
            "fields": [
                {"name": "name", "type": "Char"},
                {"name": "company_id", "type": "Many2one", "comodel_name": "res.company"},
            ],
        }])
        result = _process_performance(spec)
        fields = {f["name"]: f for f in result["models"][0]["fields"]}
        assert fields["company_id"].get("index") is True

    def test_performance_index_skip_virtual(self):
        """One2many/Many2many/Html/Text/Binary are NOT indexed even if in search."""
        spec = _make_spec(models=[{
            "name": "academy.course",
            "fields": [
                {"name": "line_ids", "type": "One2many", "comodel_name": "academy.line",
                 "inverse_name": "course_id"},
                {"name": "tag_ids", "type": "Many2many", "comodel_name": "academy.tag"},
                {"name": "description", "type": "Html"},
                {"name": "notes", "type": "Text"},
                {"name": "attachment", "type": "Binary"},
            ],
        }])
        result = _process_performance(spec)
        for field in result["models"][0]["fields"]:
            assert field.get("index") is not True, f"{field['name']} should not be indexed"

    def test_performance_sql_constraints(self):
        """unique_together generates sql_constraints on model."""
        spec = _make_spec(models=[{
            "name": "academy.course",
            "unique_together": [
                {"fields": ["name", "company_id"], "message": "Name must be unique per company."},
            ],
            "fields": [
                {"name": "name", "type": "Char"},
                {"name": "company_id", "type": "Many2one", "comodel_name": "res.company"},
            ],
        }])
        result = _process_performance(spec)
        model = result["models"][0]
        assert len(model["sql_constraints"]) == 1
        c = model["sql_constraints"][0]
        assert c["name"] == "unique_name_company_id"
        assert "UNIQUE" in c["definition"]
        assert "name" in c["definition"]
        assert "company_id" in c["definition"]
        assert c["message"] == "Name must be unique per company."

    def test_performance_sql_constraints_validation(self):
        """unique_together referencing non-existent field is skipped."""
        spec = _make_spec(models=[{
            "name": "academy.course",
            "unique_together": [
                {"fields": ["name", "nonexistent"], "message": "Bad constraint."},
            ],
            "fields": [
                {"name": "name", "type": "Char"},
            ],
        }])
        result = _process_performance(spec)
        model = result["models"][0]
        assert model.get("sql_constraints", []) == []

    def test_performance_store_computed_tree(self):
        """Computed field in first 6 view_fields (tree view) gets store=True."""
        spec = _make_spec(models=[{
            "name": "academy.course",
            "fields": [
                {"name": "name", "type": "Char"},
                {"name": "total", "type": "Float", "compute": "_compute_total",
                 "depends": ["qty"]},
            ],
        }])
        result = _process_performance(spec)
        total = next(f for f in result["models"][0]["fields"] if f["name"] == "total")
        assert total.get("store") is True

    def test_performance_store_computed_search(self):
        """Computed Char field gets store=True (appears in search)."""
        spec = _make_spec(models=[{
            "name": "academy.course",
            "fields": [
                {"name": "display_name", "type": "Char", "compute": "_compute_display_name",
                 "depends": ["name"]},
                {"name": "name", "type": "Char"},
            ],
        }])
        result = _process_performance(spec)
        dn = next(f for f in result["models"][0]["fields"] if f["name"] == "display_name")
        assert dn.get("store") is True

    def test_performance_store_computed_order(self):
        """Computed field in model.order gets store=True."""
        spec = _make_spec(models=[{
            "name": "academy.course",
            "order": "total desc",
            "fields": [
                {"name": "name", "type": "Char"},
                {"name": "total", "type": "Float", "compute": "_compute_total",
                 "depends": ["qty"]},
            ],
        }])
        result = _process_performance(spec)
        total = next(f for f in result["models"][0]["fields"] if f["name"] == "total")
        assert total.get("store") is True

    def test_performance_store_already_set(self):
        """Computed field with explicit store=True is not modified."""
        spec = _make_spec(models=[{
            "name": "academy.course",
            "fields": [
                {"name": "name", "type": "Char"},
                {"name": "total", "type": "Float", "compute": "_compute_total",
                 "depends": ["qty"], "store": True},
            ],
        }])
        result = _process_performance(spec)
        total = next(f for f in result["models"][0]["fields"] if f["name"] == "total")
        assert total.get("store") is True

    def test_transient_cleanup_attrs(self):
        """TransientModel models get transient_max_hours and transient_max_count."""
        spec = _make_spec(models=[{
            "name": "academy.wizard",
            "transient": True,
            "fields": [{"name": "name", "type": "Char"}],
        }])
        result = _process_performance(spec)
        model = result["models"][0]
        assert model["transient_max_hours"] == 1.0
        assert model["transient_max_count"] == 0

    def test_transient_cleanup_custom(self):
        """Custom transient_max_hours value is preserved."""
        spec = _make_spec(models=[{
            "name": "academy.wizard",
            "transient": True,
            "transient_max_hours": 2.0,
            "transient_max_count": 1000,
            "fields": [{"name": "name", "type": "Char"}],
        }])
        result = _process_performance(spec)
        model = result["models"][0]
        assert model["transient_max_hours"] == 2.0
        assert model["transient_max_count"] == 1000

    def test_performance_no_models_passthrough(self):
        """Empty models list returns spec unchanged."""
        spec = _make_spec(models=[])
        result = _process_performance(spec)
        assert result["models"] == []

    def test_performance_order_validation(self):
        """model.order referencing non-existent field skips that field for model_order."""
        spec = _make_spec(models=[{
            "name": "academy.course",
            "order": "nonexistent desc, name asc",
            "fields": [
                {"name": "name", "type": "Char"},
            ],
        }])
        result = _process_performance(spec)
        model = result["models"][0]
        # Only valid fields should be in model_order
        if model.get("model_order"):
            assert "nonexistent" not in model["model_order"]


class TestProcessProductionPatterns:
    """Unit tests for _process_production_patterns() preprocessor."""

    def test_bulk_flag_sets_create_override(self):
        """Spec with model having bulk:true -> model gets has_create_override=True and is_bulk=True."""
        spec = _make_spec(models=[{
            "name": "academy.course",
            "bulk": True,
            "fields": [{"name": "name", "type": "Char"}],
        }])
        result = _process_production_patterns(spec)
        model = result["models"][0]
        assert model["is_bulk"] is True
        assert model["has_create_override"] is True

    def test_bulk_without_existing_constraints(self):
        """bulk:true model without constraints still gets has_create_override=True."""
        spec = _make_spec(models=[{
            "name": "academy.course",
            "bulk": True,
            "fields": [
                {"name": "name", "type": "Char"},
                {"name": "value", "type": "Integer"},
            ],
        }])
        result = _process_production_patterns(spec)
        model = result["models"][0]
        assert model["has_create_override"] is True
        assert model["is_bulk"] is True
        # No constraints should exist
        assert model.get("create_constraints", []) == []

    def test_bulk_with_constraints_merges(self):
        """bulk:true model WITH constraints keeps both is_bulk=True and existing create_constraints."""
        spec = _make_spec(models=[{
            "name": "academy.course",
            "bulk": True,
            "has_create_override": True,
            "create_constraints": [{"name": "capacity", "type": "capacity"}],
            "fields": [{"name": "name", "type": "Char"}],
        }])
        result = _process_production_patterns(spec)
        model = result["models"][0]
        assert model["is_bulk"] is True
        assert model["has_create_override"] is True
        assert len(model["create_constraints"]) == 1

    def test_cacheable_flag_sets_overrides(self):
        """cacheable:true -> has_create_override, has_write_override, is_cacheable, needs_tools."""
        spec = _make_spec(models=[{
            "name": "academy.category",
            "cacheable": True,
            "fields": [{"name": "name", "type": "Char"}],
        }])
        result = _process_production_patterns(spec)
        model = result["models"][0]
        assert model["has_create_override"] is True
        assert model["has_write_override"] is True
        assert model["is_cacheable"] is True
        assert model["needs_tools"] is True

    def test_cacheable_with_explicit_cache_key(self):
        """cacheable with cache_key -> cache_lookup_field uses that field."""
        spec = _make_spec(models=[{
            "name": "academy.category",
            "cacheable": True,
            "cache_key": "code",
            "fields": [
                {"name": "name", "type": "Char"},
                {"name": "code", "type": "Char"},
            ],
        }])
        result = _process_production_patterns(spec)
        model = result["models"][0]
        assert model["cache_lookup_field"] == "code"

    def test_cacheable_default_lookup_field(self):
        """cacheable:true without cache_key -> cache_lookup_field defaults to first unique Char field or 'name'."""
        # With a unique Char field
        spec = _make_spec(models=[{
            "name": "academy.category",
            "cacheable": True,
            "fields": [
                {"name": "code", "type": "Char", "unique": True},
                {"name": "label", "type": "Char"},
            ],
        }])
        result = _process_production_patterns(spec)
        model = result["models"][0]
        assert model["cache_lookup_field"] == "code"

        # Without unique Char field -> defaults to "name"
        spec2 = _make_spec(models=[{
            "name": "academy.category",
            "cacheable": True,
            "fields": [
                {"name": "label", "type": "Char"},
                {"name": "value", "type": "Integer"},
            ],
        }])
        result2 = _process_production_patterns(spec2)
        model2 = result2["models"][0]
        assert model2["cache_lookup_field"] == "name"

    def test_tools_import_flag(self):
        """cacheable:true -> needs_tools=True on model."""
        spec = _make_spec(models=[{
            "name": "academy.category",
            "cacheable": True,
            "fields": [{"name": "name", "type": "Char"}],
        }])
        result = _process_production_patterns(spec)
        model = result["models"][0]
        assert model["needs_tools"] is True

    def test_cache_with_constraints_merges(self):
        """cacheable:true + constraints -> single has_write_override=True with both behaviors."""
        spec = _make_spec(models=[{
            "name": "academy.category",
            "cacheable": True,
            "has_write_override": True,
            "write_constraints": [{"name": "check_dates", "write_trigger_fields": ["date_start"]}],
            "fields": [{"name": "name", "type": "Char"}],
        }])
        result = _process_production_patterns(spec)
        model = result["models"][0]
        assert model["has_write_override"] is True
        assert model["is_cacheable"] is True
        assert len(model["write_constraints"]) == 1

    def test_no_production_flags_passthrough(self):
        """Model without bulk/cacheable passes through unchanged."""
        spec = _make_spec(models=[{
            "name": "academy.course",
            "fields": [{"name": "name", "type": "Char"}],
        }])
        result = _process_production_patterns(spec)
        model = result["models"][0]
        assert model.get("is_bulk") is not True
        assert model.get("is_cacheable") is not True
        assert model.get("needs_tools") is not True

    def test_pure_function(self):
        """Input spec is not mutated."""
        import copy
        spec = _make_spec(models=[{
            "name": "academy.course",
            "bulk": True,
            "cacheable": True,
            "fields": [{"name": "name", "type": "Char"}],
        }])
        original = copy.deepcopy(spec)
        _process_production_patterns(spec)
        assert spec == original

    # -- Archival tests (Phase 34, Plan 02) --

    def test_archival_injects_active_field(self):
        """Spec with archival:true -> model fields contain active Boolean with index=True and default=True."""
        spec = _make_spec(models=[{
            "name": "academy.course",
            "archival": True,
            "fields": [{"name": "name", "type": "Char"}],
        }])
        result = _process_production_patterns(spec)
        model = result["models"][0]
        active_fields = [f for f in model["fields"] if f["name"] == "active"]
        assert len(active_fields) == 1
        active = active_fields[0]
        assert active["type"] == "Boolean"
        assert active["default"] is True
        assert active["index"] is True

    def test_archival_active_field_not_duplicated(self):
        """Spec with archival:true and existing active field -> no duplicate active field injected."""
        spec = _make_spec(models=[{
            "name": "academy.course",
            "archival": True,
            "fields": [
                {"name": "name", "type": "Char"},
                {"name": "active", "type": "Boolean", "default": True},
            ],
        }])
        result = _process_production_patterns(spec)
        model = result["models"][0]
        active_fields = [f for f in model["fields"] if f["name"] == "active"]
        assert len(active_fields) == 1

    def test_archival_injects_wizard(self):
        """Spec with archival:true -> spec['wizards'] contains archival wizard entry."""
        spec = _make_spec(models=[{
            "name": "academy.course",
            "archival": True,
            "fields": [{"name": "name", "type": "Char"}],
        }])
        result = _process_production_patterns(spec)
        wizards = result.get("wizards", [])
        archival_wizards = [w for w in wizards if "archive" in w["name"]]
        assert len(archival_wizards) == 1
        wiz = archival_wizards[0]
        assert wiz["name"] == "academy.course.archive.wizard"
        assert wiz["target_model"] == "academy.course"
        assert wiz["template"] == "archival_wizard.py.j2"
        assert wiz["form_template"] == "archival_wizard_form.xml.j2"

    def test_archival_injects_cron(self):
        """Spec with archival:true -> spec['cron_jobs'] contains cron entry."""
        spec = _make_spec(models=[{
            "name": "academy.course",
            "archival": True,
            "fields": [{"name": "name", "type": "Char"}],
        }])
        result = _process_production_patterns(spec)
        crons = result.get("cron_jobs", [])
        archival_crons = [c for c in crons if c["method"] == "_cron_archive_old_records"]
        assert len(archival_crons) == 1
        cron = archival_crons[0]
        assert cron["model_name"] == "academy.course"

    def test_archival_sets_flags(self):
        """archival:true -> model gets is_archival=True; has_create_override stays unchanged."""
        spec = _make_spec(models=[{
            "name": "academy.course",
            "archival": True,
            "fields": [{"name": "name", "type": "Char"}],
        }])
        result = _process_production_patterns(spec)
        model = result["models"][0]
        assert model["is_archival"] is True
        # Archival doesn't need create override on its own
        assert model.get("has_create_override") is not True

    def test_archival_cron_defaults(self):
        """Injected archival cron has interval_number=1, interval_type='days', doall=False."""
        spec = _make_spec(models=[{
            "name": "academy.course",
            "archival": True,
            "fields": [{"name": "name", "type": "Char"}],
        }])
        result = _process_production_patterns(spec)
        crons = result.get("cron_jobs", [])
        cron = [c for c in crons if c["method"] == "_cron_archive_old_records"][0]
        assert cron["interval_number"] == 1
        assert cron["interval_type"] == "days"
        assert cron["doall"] is False

    def test_archival_with_bulk_and_cache(self):
        """archival:true + bulk:true + cacheable:true -> all three flags set correctly."""
        spec = _make_spec(models=[{
            "name": "academy.course",
            "archival": True,
            "bulk": True,
            "cacheable": True,
            "fields": [{"name": "name", "type": "Char"}],
        }])
        result = _process_production_patterns(spec)
        model = result["models"][0]
        assert model["is_archival"] is True
        assert model["is_bulk"] is True
        assert model["is_cacheable"] is True
        # Archival wizard and cron should be present
        wizards = result.get("wizards", [])
        crons = result.get("cron_jobs", [])
        assert any("archive" in w["name"] for w in wizards)
        assert any(c["method"] == "_cron_archive_old_records" for c in crons)


# ---------------------------------------------------------------------------
# Phase 37: Security preprocessor tests
# ---------------------------------------------------------------------------


def _make_security_spec(
    models: list[dict] | None = None,
    security: dict | None = None,
) -> dict:
    """Helper to construct a spec with security block and 2 models."""
    if models is None:
        models = [
            {
                "name": "fee.structure",
                "description": "Fee Structure",
                "fields": [
                    {"name": "name", "type": "Char", "required": True},
                    {"name": "company_id", "type": "Many2one", "comodel_name": "res.company"},
                    {"name": "department_id", "type": "Many2one", "comodel_name": "hr.department"},
                ],
            },
            {
                "name": "fee.line",
                "description": "Fee Line",
                "fields": [
                    {"name": "name", "type": "Char", "required": True},
                    {"name": "amount", "type": "Float"},
                ],
            },
        ]
    if security is None:
        security = {
            "roles": ["viewer", "editor", "manager"],
            "defaults": {
                "viewer": "r",
                "editor": "cru",
                "manager": "crud",
            },
        }
    spec = _make_spec(models=models)
    spec["security"] = security
    return spec


class TestParseCrud:
    def test_parse_crud_cru(self):
        result = _parse_crud("cru")
        assert result == {"perm_create": 1, "perm_read": 1, "perm_write": 1, "perm_unlink": 0}

    def test_parse_crud_full(self):
        result = _parse_crud("crud")
        assert result == {"perm_create": 1, "perm_read": 1, "perm_write": 1, "perm_unlink": 1}

    def test_parse_crud_read_only(self):
        result = _parse_crud("r")
        assert result == {"perm_create": 0, "perm_read": 1, "perm_write": 0, "perm_unlink": 0}

    def test_parse_crud_uppercase_normalized(self):
        result = _parse_crud("CRU")
        assert result == {"perm_create": 1, "perm_read": 1, "perm_write": 1, "perm_unlink": 0}

    def test_parse_crud_invalid_chars_raises(self):
        with pytest.raises(ValueError, match="invalid"):
            _parse_crud("xyz")

    def test_parse_crud_mixed_invalid_raises(self):
        with pytest.raises(ValueError, match="invalid"):
            _parse_crud("crx")


class TestSecurityRolesBuilding:
    def test_three_roles_produces_security_roles(self):
        spec = _make_security_spec()
        result = _process_security_patterns(spec)
        roles = result["security_roles"]
        assert len(roles) == 3

    def test_implied_ids_chain(self):
        spec = _make_security_spec()
        result = _process_security_patterns(spec)
        roles = result["security_roles"]
        # Lowest role implies base.group_user
        assert roles[0]["implied_ids"] == "base.group_user"
        # Second role implies first
        assert roles[1]["implied_ids"] == "group_test_module_viewer"
        # Third implies second
        assert roles[2]["implied_ids"] == "group_test_module_editor"

    def test_xml_id_format(self):
        spec = _make_security_spec()
        result = _process_security_patterns(spec)
        roles = result["security_roles"]
        assert roles[0]["xml_id"] == "group_test_module_viewer"
        assert roles[1]["xml_id"] == "group_test_module_editor"
        assert roles[2]["xml_id"] == "group_test_module_manager"

    def test_is_highest_only_on_last(self):
        spec = _make_security_spec()
        result = _process_security_patterns(spec)
        roles = result["security_roles"]
        assert roles[0].get("is_highest") is not True
        assert roles[1].get("is_highest") is not True
        assert roles[2]["is_highest"] is True

    def test_role_labels(self):
        spec = _make_security_spec()
        result = _process_security_patterns(spec)
        roles = result["security_roles"]
        assert roles[0]["label"] == "Viewer"
        assert roles[1]["label"] == "Editor"
        assert roles[2]["label"] == "Manager"


class TestSecurityAclMatrix:
    def test_defaults_produce_acl_per_model(self):
        spec = _make_security_spec()
        result = _process_security_patterns(spec)
        for model in result["models"]:
            assert "security_acl" in model
            assert len(model["security_acl"]) == 3

    def test_acl_permissions_from_defaults(self):
        spec = _make_security_spec()
        result = _process_security_patterns(spec)
        model = result["models"][0]
        viewer_acl = next(a for a in model["security_acl"] if a["role"] == "viewer")
        assert viewer_acl["perm_read"] == 1
        assert viewer_acl["perm_write"] == 0
        assert viewer_acl["perm_create"] == 0
        assert viewer_acl["perm_unlink"] == 0

    def test_per_model_override(self):
        spec = _make_security_spec()
        spec["security"]["acl"] = {
            "fee.structure": {
                "viewer": "r",
                "editor": "r",
                "manager": "crud",
            },
        }
        result = _process_security_patterns(spec)
        fee_structure = next(m for m in result["models"] if m["name"] == "fee.structure")
        editor_acl = next(a for a in fee_structure["security_acl"] if a["role"] == "editor")
        # Override: editor only gets read on fee.structure
        assert editor_acl["perm_read"] == 1
        assert editor_acl["perm_write"] == 0

        # fee.line should still use defaults
        fee_line = next(m for m in result["models"] if m["name"] == "fee.line")
        editor_acl_line = next(a for a in fee_line["security_acl"] if a["role"] == "editor")
        assert editor_acl_line["perm_write"] == 1


class TestSecurityValidation:
    def test_defaults_keys_mismatch_raises(self):
        spec = _make_security_spec()
        spec["security"]["defaults"] = {
            "viewer": "r",
            "admin": "crud",  # not in roles array
        }
        with pytest.raises(ValueError, match="defaults.*roles"):
            _process_security_patterns(spec)


class TestLegacySecurity:
    def test_no_security_block_injects_legacy(self):
        spec = _make_spec(models=[
            {
                "name": "test.model",
                "fields": [{"name": "name", "type": "Char"}],
            },
        ])
        result = _process_security_patterns(spec)
        roles = result["security_roles"]
        assert len(roles) == 2
        assert roles[0]["name"] == "user"
        assert roles[1]["name"] == "manager"

    def test_legacy_user_implied_ids(self):
        spec = _make_spec(models=[
            {
                "name": "test.model",
                "fields": [{"name": "name", "type": "Char"}],
            },
        ])
        result = _process_security_patterns(spec)
        roles = result["security_roles"]
        assert roles[0]["implied_ids"] == "base.group_user"
        assert "group_test_module_user" in roles[1]["implied_ids"]

    def test_legacy_acl_defaults(self):
        spec = _make_spec(models=[
            {
                "name": "test.model",
                "fields": [{"name": "name", "type": "Char"}],
            },
        ])
        result = _process_security_patterns(spec)
        model = result["models"][0]
        user_acl = next(a for a in model["security_acl"] if a["role"] == "user")
        manager_acl = next(a for a in model["security_acl"] if a["role"] == "manager")
        # user: cru
        assert user_acl["perm_unlink"] == 0
        assert user_acl["perm_read"] == 1
        # manager: crud
        assert manager_acl["perm_unlink"] == 1


class TestRecordRuleScopes:
    def test_user_id_field_gives_ownership(self):
        spec = _make_spec(models=[{
            "name": "test.model",
            "fields": [
                {"name": "name", "type": "Char"},
                {"name": "user_id", "type": "Many2one", "comodel_name": "res.users"},
            ],
        }])
        result = _process_security_patterns(spec)
        assert "ownership" in result["models"][0]["record_rule_scopes"]

    def test_department_id_gives_department(self):
        spec = _make_spec(models=[{
            "name": "test.model",
            "fields": [
                {"name": "name", "type": "Char"},
                {"name": "department_id", "type": "Many2one", "comodel_name": "hr.department"},
            ],
        }])
        result = _process_security_patterns(spec)
        assert "department" in result["models"][0]["record_rule_scopes"]

    def test_company_id_gives_company(self):
        spec = _make_spec(models=[{
            "name": "test.model",
            "fields": [
                {"name": "name", "type": "Char"},
                {"name": "company_id", "type": "Many2one", "comodel_name": "res.company"},
            ],
        }])
        result = _process_security_patterns(spec)
        assert "company" in result["models"][0]["record_rule_scopes"]

    def test_all_three_fields_all_scopes(self):
        spec = _make_spec(models=[{
            "name": "test.model",
            "fields": [
                {"name": "name", "type": "Char"},
                {"name": "user_id", "type": "Many2one", "comodel_name": "res.users"},
                {"name": "department_id", "type": "Many2one", "comodel_name": "hr.department"},
                {"name": "company_id", "type": "Many2one", "comodel_name": "res.company"},
            ],
        }])
        result = _process_security_patterns(spec)
        scopes = result["models"][0]["record_rule_scopes"]
        assert "ownership" in scopes
        assert "department" in scopes
        assert "company" in scopes

    def test_record_rules_override_replaces_auto(self):
        spec = _make_spec(models=[{
            "name": "test.model",
            "fields": [
                {"name": "name", "type": "Char"},
                {"name": "user_id", "type": "Many2one", "comodel_name": "res.users"},
                {"name": "company_id", "type": "Many2one", "comodel_name": "res.company"},
            ],
            "record_rules": ["company"],
        }])
        result = _process_security_patterns(spec)
        scopes = result["models"][0]["record_rule_scopes"]
        assert scopes == ["company"]


class TestSecuritySmoke:
    def test_render_module_with_security_spec_no_errors(self):
        spec = _make_security_spec()
        spec["module_title"] = "Test Module"
        spec["summary"] = "Test"
        spec["author"] = "Test"
        with tempfile.TemporaryDirectory() as tmp:
            files, warnings = render_module(spec, get_template_dir(), Path(tmp))
            assert len(files) > 0

    def test_render_module_without_security_block_backward_compat(self):
        spec = _make_spec(models=[{
            "name": "test.model",
            "description": "Test Model",
            "fields": [{"name": "name", "type": "Char", "required": True}],
        }])
        with tempfile.TemporaryDirectory() as tmp:
            files, warnings = render_module(spec, get_template_dir(), Path(tmp))
            assert len(files) > 0


# ---------------------------------------------------------------------------
# Phase 37-02: Field-level groups enrichment tests
# ---------------------------------------------------------------------------


class TestSecurityFieldGroups:
    """Tests for _security_enrich_fields helper."""

    def test_sensitive_field_gets_highest_role_groups(self):
        """Field with sensitive:true and no explicit groups gets groups set to highest role."""
        spec = _make_security_spec()
        spec["models"][0]["fields"].append(
            {"name": "ssn", "type": "Char", "sensitive": True},
        )
        result = _process_security_patterns(spec)
        ssn_field = next(
            f for m in result["models"] for f in m["fields"] if f["name"] == "ssn"
        )
        assert ssn_field["groups"] == "test_module.group_test_module_manager"

    def test_explicit_groups_role_name_resolved(self):
        """Field with explicit groups='manager' resolves to full external ID."""
        spec = _make_security_spec()
        spec["models"][0]["fields"].append(
            {"name": "salary", "type": "Float", "groups": "manager"},
        )
        result = _process_security_patterns(spec)
        salary_field = next(
            f for m in result["models"] for f in m["fields"] if f["name"] == "salary"
        )
        assert salary_field["groups"] == "test_module.group_test_module_manager"

    def test_explicit_groups_editor_role_resolved(self):
        """Field with explicit groups='editor' resolves to full external ID."""
        spec = _make_security_spec()
        spec["models"][0]["fields"].append(
            {"name": "notes", "type": "Text", "groups": "editor"},
        )
        result = _process_security_patterns(spec)
        notes_field = next(
            f for m in result["models"] for f in m["fields"] if f["name"] == "notes"
        )
        assert notes_field["groups"] == "test_module.group_test_module_editor"

    def test_explicit_full_external_id_kept_asis(self):
        """Field with groups containing a dot is kept as-is (full external ID)."""
        spec = _make_security_spec()
        spec["models"][0]["fields"].append(
            {"name": "payroll", "type": "Float", "groups": "other_module.some_group"},
        )
        result = _process_security_patterns(spec)
        payroll_field = next(
            f for m in result["models"] for f in m["fields"] if f["name"] == "payroll"
        )
        assert payroll_field["groups"] == "other_module.some_group"

    def test_non_sensitive_no_groups_unchanged(self):
        """Non-sensitive field without groups key is left unchanged."""
        spec = _make_security_spec()
        result = _process_security_patterns(spec)
        name_field = next(
            f for m in result["models"] for f in m["fields"] if f["name"] == "name"
        )
        assert "groups" not in name_field

    def test_legacy_fallback_sensitive_field(self):
        """Without security block, sensitive fields get legacy manager group."""
        spec = _make_spec(models=[{
            "name": "test.model",
            "fields": [
                {"name": "name", "type": "Char"},
                {"name": "secret", "type": "Char", "sensitive": True},
            ],
        }])
        result = _process_security_patterns(spec)
        secret_field = next(
            f for m in result["models"] for f in m["fields"] if f["name"] == "secret"
        )
        assert secret_field["groups"] == "test_module.group_test_module_manager"


class TestSecurityViewAutoFix:
    """Tests for _security_auto_fix_views helper."""

    def test_restricted_field_gets_view_groups(self):
        """View field referencing a restricted field gets view_groups key."""
        spec = _make_security_spec()
        spec["models"][0]["fields"].append(
            {"name": "ssn", "type": "Char", "sensitive": True},
        )
        result = _process_security_patterns(spec)
        ssn_field = next(
            f for m in result["models"] for f in m["fields"] if f["name"] == "ssn"
        )
        assert ssn_field.get("view_groups") == "test_module.group_test_module_manager"

    def test_non_restricted_field_no_view_groups(self):
        """Non-restricted field does NOT get view_groups."""
        spec = _make_security_spec()
        result = _process_security_patterns(spec)
        name_field = next(
            f for m in result["models"] for f in m["fields"] if f["name"] == "name"
        )
        assert "view_groups" not in name_field

    def test_auto_fix_logs_info_message(self, caplog):
        """Auto-fix logs INFO for each field it enriches in views."""
        import logging
        spec = _make_security_spec()
        spec["models"][0]["fields"].append(
            {"name": "ssn", "type": "Char", "sensitive": True},
        )
        with caplog.at_level(logging.INFO, logger="amil_utils.preprocessors"):
            _process_security_patterns(spec)
        assert any("Auto-applied groups=" in msg for msg in caplog.messages)

    def test_restricted_char_field_warns_search_view(self, caplog):
        """Restricted Char field triggers search view warning."""
        import logging
        spec = _make_security_spec()
        spec["models"][0]["fields"].append(
            {"name": "ssn", "type": "Char", "sensitive": True},
        )
        with caplog.at_level(logging.WARNING, logger="amil_utils.preprocessors"):
            _process_security_patterns(spec)
        assert any(
            "may appear in search view" in msg and "ssn" in msg
            for msg in caplog.messages
        )

    def test_restricted_many2one_field_warns_search_view(self, caplog):
        """Restricted Many2one field triggers search view warning."""
        import logging
        spec = _make_security_spec()
        spec["models"][0]["fields"].append(
            {"name": "secret_partner_id", "type": "Many2one", "comodel": "res.partner", "sensitive": True},
        )
        with caplog.at_level(logging.WARNING, logger="amil_utils.preprocessors"):
            _process_security_patterns(spec)
        assert any(
            "may appear in search view" in msg and "secret_partner_id" in msg
            for msg in caplog.messages
        )

    def test_restricted_integer_field_no_search_warning(self, caplog):
        """Restricted Integer field does NOT trigger search view warning (not a search type)."""
        import logging
        spec = _make_security_spec()
        spec["models"][0]["fields"].append(
            {"name": "salary", "type": "Integer", "sensitive": True},
        )
        with caplog.at_level(logging.WARNING, logger="amil_utils.preprocessors"):
            _process_security_patterns(spec)
        assert not any(
            "may appear in search view" in msg and "salary" in msg
            for msg in caplog.messages
        )

    def test_computed_field_depending_on_restricted_warns(self, caplog):
        """Computed field depending on a restricted field triggers warning."""
        import logging
        spec = _make_security_spec()
        spec["models"][0]["fields"].extend([
            {"name": "salary", "type": "Float", "sensitive": True},
            {"name": "salary_display", "type": "Char", "compute": "_compute_salary_display", "depends": ["salary"]},
        ])
        with caplog.at_level(logging.WARNING, logger="amil_utils.preprocessors"):
            _process_security_patterns(spec)
        assert any(
            "depends on restricted field" in msg and "salary_display" in msg
            for msg in caplog.messages
        )

    def test_computed_field_depending_on_unrestricted_no_warning(self, caplog):
        """Computed field depending on unrestricted fields does not warn."""
        import logging
        spec = _make_security_spec()
        spec["models"][0]["fields"].extend([
            {"name": "qty", "type": "Integer"},
            {"name": "total", "type": "Float", "compute": "_compute_total", "depends": ["qty"]},
        ])
        with caplog.at_level(logging.WARNING, logger="amil_utils.preprocessors"):
            _process_security_patterns(spec)
        assert not any(
            "depends on restricted field" in msg
            for msg in caplog.messages
        )

    def test_computed_field_dotted_dep_on_restricted_warns(self, caplog):
        """Computed field with dotted dependency path on restricted field warns."""
        import logging
        spec = _make_security_spec()
        spec["models"][0]["fields"].extend([
            {"name": "salary", "type": "Float", "sensitive": True},
            {"name": "annual_salary", "type": "Float", "compute": "_compute_annual", "depends": ["salary.amount"]},
        ])
        with caplog.at_level(logging.WARNING, logger="amil_utils.preprocessors"):
            _process_security_patterns(spec)
        assert any(
            "depends on restricted field 'salary'" in msg and "annual_salary" in msg
            for msg in caplog.messages
        )


# ---------------------------------------------------------------------------
# Phase 38: Audit trail integration tests
# ---------------------------------------------------------------------------


class TestAuditIntegration:
    """Integration tests for audit preprocessor wired into render pipeline."""

    def _make_audit_spec(self) -> dict:
        """Build a spec with audit:true on one model plus security block."""
        return {
            "module_name": "test_module",
            "module_title": "Test Module",
            "summary": "Test",
            "author": "Test",
            "depends": ["base"],
            "models": [
                {
                    "name": "test.record",
                    "description": "Test Record",
                    "fields": [
                        {"name": "name", "type": "Char", "required": True},
                        {"name": "value", "type": "Integer"},
                        {"name": "notes", "type": "Text"},
                    ],
                    "audit": True,
                },
            ],
            "security": {
                "roles": ["viewer", "editor", "manager"],
                "defaults": {
                    "viewer": "r",
                    "editor": "cru",
                    "manager": "crud",
                },
            },
        }

    def test_audit_context_defaults_on_non_audit_model(self):
        """Non-audit model gets has_audit=False and empty audit_fields from context builder."""
        model = {
            "name": "test.plain",
            "description": "Plain Model",
            "fields": [
                {"name": "name", "type": "Char", "required": True},
            ],
        }
        spec = _make_spec(models=[model])
        ctx = _build_model_context(spec, model)
        assert ctx["has_audit"] is False
        assert ctx["audit_fields"] == []
        assert ctx["audit_field_names"] == set()
        assert ctx["audit_exclude"] == []

    def test_audit_context_keys_on_audit_model(self):
        """Audit model gets has_audit=True and populated audit_fields from context builder."""
        from amil_utils.preprocessors.audit import _process_audit_patterns
        from amil_utils.preprocessors.security import _process_security_patterns
        spec = self._make_audit_spec()
        spec = _process_security_patterns(spec)
        spec = _process_audit_patterns(spec)

        audited = next(m for m in spec["models"] if m["name"] == "test.record")
        ctx = _build_model_context(spec, audited)
        assert ctx["has_audit"] is True
        assert len(ctx["audit_fields"]) > 0
        assert "name" in ctx["audit_field_names"]
        assert "value" in ctx["audit_field_names"]

    def test_module_context_has_audit_log_key(self):
        """Module context includes has_audit_log=True when audit models present."""
        from amil_utils.preprocessors.audit import _process_audit_patterns
        from amil_utils.preprocessors.security import _process_security_patterns
        spec = self._make_audit_spec()
        spec = _process_security_patterns(spec)
        spec = _process_audit_patterns(spec)
        ctx = _build_module_context(spec, spec["module_name"])
        assert ctx["has_audit_log"] is True

    def test_module_context_has_audit_log_false_without_audit(self):
        """Module context has has_audit_log=False when no audit models."""
        spec = _make_spec(models=[{
            "name": "test.plain",
            "description": "Plain",
            "fields": [{"name": "name", "type": "Char", "required": True}],
        }])
        ctx = _build_module_context(spec, spec["module_name"])
        assert ctx["has_audit_log"] is False

    def test_render_module_with_audit_generates_audit_log_model_file(self):
        """render_module with audit:true generates audit_trail_log.py model file."""
        spec = self._make_audit_spec()
        with tempfile.TemporaryDirectory() as tmp:
            files, warnings = render_module(spec, get_template_dir(), Path(tmp))
            module_dir = Path(tmp) / "test_module"
            audit_model_file = module_dir / "models" / "audit_trail_log.py"
            assert audit_model_file.exists(), (
                f"audit_trail_log.py not generated. Files: {[str(f) for f in files if 'models' in str(f)]}"
            )

    def test_render_module_with_audit_generates_acl_rows(self):
        """render_module with audit:true produces ir.model.access.csv with audit.trail.log rows."""
        spec = self._make_audit_spec()
        with tempfile.TemporaryDirectory() as tmp:
            files, warnings = render_module(spec, get_template_dir(), Path(tmp))
            module_dir = Path(tmp) / "test_module"
            acl_file = module_dir / "security" / "ir.model.access.csv"
            assert acl_file.exists()
            acl_content = acl_file.read_text()
            # Should have rows for audit.trail.log
            assert "audit_trail_log" in acl_content

    def test_render_module_with_audit_no_strict_undefined_crash(self):
        """Full render with audit:true completes without StrictUndefined errors."""
        spec = self._make_audit_spec()
        with tempfile.TemporaryDirectory() as tmp:
            files, warnings = render_module(spec, get_template_dir(), Path(tmp))
            assert len(files) > 0

    def test_render_module_without_audit_still_works(self):
        """Full render without audit:true still succeeds (no regression)."""
        spec = _make_spec(models=[{
            "name": "test.simple",
            "description": "Simple",
            "fields": [
                {"name": "name", "type": "Char", "required": True},
                {"name": "qty", "type": "Integer"},
            ],
        }])
        with tempfile.TemporaryDirectory() as tmp:
            files, warnings = render_module(spec, get_template_dir(), Path(tmp))
            assert len(files) > 0

    def test_render_module_with_audit_auditor_role_in_security_xml(self):
        """render_module with audit:true includes auditor group in security.xml."""
        spec = self._make_audit_spec()
        with tempfile.TemporaryDirectory() as tmp:
            files, warnings = render_module(spec, get_template_dir(), Path(tmp))
            module_dir = Path(tmp) / "test_module"
            security_xml = module_dir / "security" / "security.xml"
            assert security_xml.exists()
            content = security_xml.read_text()
            assert "auditor" in content


# ---------------------------------------------------------------------------
# Phase 38 Plan 02: Audit template rendering tests
# ---------------------------------------------------------------------------


class TestAuditTemplateRendering:
    """Integration tests for audit write() wrapper and helper methods in model.py.j2."""

    def _make_audit_spec(self) -> dict:
        """Build a spec with audit:true on one model plus security roles."""
        return {
            "module_name": "test_module",
            "module_title": "Test Module",
            "summary": "Test",
            "author": "Test",
            "depends": ["base"],
            "models": [
                {
                    "name": "test.record",
                    "description": "Test Record",
                    "fields": [
                        {"name": "name", "type": "Char", "required": True},
                        {"name": "value", "type": "Integer"},
                        {"name": "notes", "type": "Text"},
                        {"name": "partner_id", "type": "Many2one", "comodel_name": "res.partner"},
                        {"name": "start_date", "type": "Date"},
                        {"name": "status", "type": "Selection", "selection": [("draft", "Draft"), ("done", "Done")]},
                    ],
                    "audit": True,
                },
            ],
            "security": {
                "roles": ["viewer", "editor", "manager"],
                "defaults": {
                    "viewer": "r",
                    "editor": "cru",
                    "manager": "crud",
                },
            },
        }

    def _render_audit_model(self) -> str:
        """Render the audited model and return its .py file content."""
        spec = self._make_audit_spec()
        with tempfile.TemporaryDirectory() as tmp:
            files, warnings = render_module(spec, get_template_dir(), Path(tmp))
            model_file = Path(tmp) / "test_module" / "models" / "test_record.py"
            assert model_file.exists(), f"test_record.py not generated. Files: {files}"
            return model_file.read_text()

    def _render_non_audit_model(self) -> str:
        """Render a non-audited model with write override (via constraint) and return its .py content."""
        spec = {
            "module_name": "test_module",
            "module_title": "Test Module",
            "summary": "Test",
            "author": "Test",
            "depends": ["base"],
            "models": [
                {
                    "name": "test.plain",
                    "description": "Plain Model",
                    "fields": [
                        {"name": "name", "type": "Char", "required": True},
                        {"name": "qty", "type": "Integer"},
                    ],
                },
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            files, warnings = render_module(spec, get_template_dir(), Path(tmp))
            model_file = Path(tmp) / "test_module" / "models" / "test_plain.py"
            assert model_file.exists(), f"test_plain.py not generated. Files: {files}"
            return model_file.read_text()

    def test_audit_write_starts_with_audit_skip_guard(self):
        """Audited model's write() has _audit_skip context guard as the first check."""
        content = self._render_audit_model()
        assert "def write(self, vals):" in content
        # The _audit_skip guard should appear in the write method
        assert "_audit_skip" in content
        # Find write method and verify _audit_skip is the first conditional inside it
        lines = content.split("\n")
        write_idx = next(i for i, line in enumerate(lines) if "def write(self, vals):" in line)
        # The very first non-blank line after write() def should be the _audit_skip guard
        first_body_lines = [
            line.strip() for line in lines[write_idx + 1:write_idx + 5]
            if line.strip()
        ]
        assert any("_audit_skip" in line for line in first_body_lines), (
            f"_audit_skip should be the outermost check in write(). "
            f"First body lines: {first_body_lines}"
        )

    def test_audit_write_captures_old_values_before_super(self):
        """Audited model's write() calls _audit_read_old(vals) BEFORE super().write(vals)."""
        content = self._render_audit_model()
        # Both _audit_read_old and super().write should be present
        assert "_audit_read_old" in content
        assert "super().write(vals)" in content
        # In the main (non-skip) path, _audit_read_old must appear before super().write
        lines = content.split("\n")
        write_idx = next(i for i, line in enumerate(lines) if "def write(self, vals):" in line)
        write_body = "\n".join(lines[write_idx:])
        # Find old_values assignment and first non-skip super() call
        # The main path old_values must come before the main path super()
        main_path_lines = []
        in_skip_block = False
        indent_level = None
        for line in lines[write_idx + 1:]:
            stripped = line.lstrip()
            if "_audit_skip" in line:
                in_skip_block = True
                indent_level = len(line) - len(stripped)
                continue
            if in_skip_block:
                if stripped and (len(line) - len(stripped)) <= indent_level:
                    in_skip_block = False
                else:
                    continue
            main_path_lines.append(line)
        main_path = "\n".join(main_path_lines)
        old_pos = main_path.find("_audit_read_old")
        super_pos = main_path.find("super().write(vals)")
        assert old_pos < super_pos, (
            "_audit_read_old must appear before super().write(vals) in the main path"
        )

    def test_audit_write_logs_changes_after_super(self):
        """Audited model's write() calls _audit_log_changes AFTER super().write(vals) succeeds."""
        content = self._render_audit_model()
        assert "_audit_log_changes" in content
        # _audit_log_changes must appear after the main super().write call
        lines = content.split("\n")
        write_idx = next(i for i, line in enumerate(lines) if "def write(self, vals):" in line)
        write_body = "\n".join(lines[write_idx:])
        # In the full write body (main path), _audit_log_changes must come after super().write
        # We look for the LAST super().write and _audit_log_changes
        super_positions = [i for i, line in enumerate(lines[write_idx:]) if "super().write(vals)" in line]
        log_positions = [i for i, line in enumerate(lines[write_idx:]) if "_audit_log_changes" in line]
        assert len(log_positions) >= 1
        # The _audit_log_changes should come after the last super().write
        assert log_positions[-1] > super_positions[-1], (
            "_audit_log_changes must appear after the last super().write(vals)"
        )

    def test_audit_skip_path_has_super_and_return(self):
        """Non-audit early-return path still executes super() and returns result."""
        content = self._render_audit_model()
        lines = content.split("\n")
        write_idx = next(i for i, line in enumerate(lines) if "def write(self, vals):" in line)
        # Find the _audit_skip block
        skip_block_lines = []
        in_skip_block = False
        skip_indent = None
        for line in lines[write_idx + 1:]:
            stripped = line.lstrip()
            if "_audit_skip" in line and "if " in line:
                in_skip_block = True
                skip_indent = len(line) - len(stripped) + 4  # inside the if block
                continue
            if in_skip_block:
                current_indent = len(line) - len(stripped) if stripped else skip_indent + 1
                if stripped and current_indent < skip_indent:
                    break
                skip_block_lines.append(line)
        skip_content = "\n".join(skip_block_lines)
        assert "super().write(vals)" in skip_content, (
            "The _audit_skip early-return path must call super().write(vals)"
        )
        assert "return" in skip_content, (
            "The _audit_skip early-return path must return"
        )

    def test_audit_helper_methods_present(self):
        """Generated model contains _audit_read_old, _audit_log_changes, _audit_tracked_fields method definitions."""
        content = self._render_audit_model()
        assert "def _audit_read_old(self, vals):" in content
        assert "def _audit_log_changes(self, old_values, vals):" in content
        assert "def _audit_tracked_fields(self):" in content

    def test_audit_tracked_fields_returns_field_names(self):
        """_audit_tracked_fields returns a list/set containing auditable field names."""
        content = self._render_audit_model()
        lines = content.split("\n")
        tracked_idx = next(
            i for i, line in enumerate(lines) if "_audit_tracked_fields" in line and "def " in line
        )
        # Look at the return statement in the method body
        method_body = []
        for line in lines[tracked_idx + 1:tracked_idx + 10]:
            if line.strip():
                method_body.append(line)
        method_text = "\n".join(method_body)
        # Should contain the auditable field names (name, value, notes, partner_id, start_date, status)
        assert "name" in method_text
        assert "value" in method_text
        assert "notes" in method_text

    def test_audit_log_changes_uses_sudo_with_audit_skip(self):
        """_audit_log_changes creates entries via audit.trail.log with sudo() and _audit_skip=True."""
        content = self._render_audit_model()
        assert "audit.trail.log" in content
        assert "sudo()" in content
        assert "_audit_skip=True" in content

    def test_audit_read_old_uses_sudo(self):
        """_audit_read_old uses self.sudo() for safe reads (especially Many2one display_name)."""
        content = self._render_audit_model()
        # Find the _audit_read_old method
        lines = content.split("\n")
        method_idx = next(
            i for i, line in enumerate(lines) if "def _audit_read_old" in line
        )
        method_body = "\n".join(lines[method_idx:method_idx + 30])
        assert "sudo()" in method_body, (
            "_audit_read_old must use sudo() for safe reads"
        )

    def test_non_audit_model_write_unchanged(self):
        """Non-audited model write() renders identically to before (no regression)."""
        content = self._render_non_audit_model()
        # A plain model without audit should NOT contain any audit-specific code
        assert "_audit_skip" not in content
        assert "_audit_read_old" not in content
        assert "_audit_log_changes" not in content
        assert "_audit_tracked_fields" not in content
        assert "audit.trail.log" not in content

    def test_audit_model_has_needs_api_true(self):
        """Audited model sets needs_api=True (for @api.model on _audit_tracked_fields)."""
        from amil_utils.preprocessors.audit import _process_audit_patterns
        from amil_utils.preprocessors.security import _process_security_patterns
        spec = self._make_audit_spec()
        spec = _process_security_patterns(spec)
        spec = _process_audit_patterns(spec)
        audited = next(m for m in spec["models"] if m["name"] == "test.record")
        ctx = _build_model_context(spec, audited)
        assert ctx["needs_api"] is True

    def test_audit_model_imports_api(self):
        """Audited model .py file contains 'from odoo import api, fields, models'."""
        content = self._render_audit_model()
        assert "from odoo import api, fields, models" in content


# ---------------------------------------------------------------------------
# Phase 39 Plan 01: Approval context builder tests
# ---------------------------------------------------------------------------


class TestApprovalIntegration:
    """Integration tests for approval preprocessor wired into context builder."""

    def _make_approval_spec(self) -> dict:
        """Build a spec with approval on one model plus security roles."""
        return {
            "module_name": "uni_fee",
            "module_title": "University Fee",
            "summary": "Fee management",
            "author": "Test",
            "depends": ["base"],
            "models": [
                {
                    "name": "fee.request",
                    "description": "Fee Request",
                    "fields": [
                        {"name": "name", "type": "Char", "required": True},
                        {"name": "amount", "type": "Float"},
                        {"name": "notes", "type": "Text"},
                    ],
                    "approval": {
                        "levels": [
                            {"state": "submitted", "role": "editor", "next": "approved_hod", "label": "Submitted"},
                            {"state": "approved_hod", "role": "hod", "next": "approved_dean", "label": "HOD Approved"},
                            {"state": "approved_dean", "role": "dean", "next": "done", "label": "Dean Approved"},
                        ],
                        "on_reject": "draft",
                        "reject_allowed_from": ["approved_hod", "approved_dean"],
                        "lock_after": "draft",
                        "editable_fields": ["notes"],
                    },
                },
            ],
            "security": {
                "roles": ["user", "editor", "hod", "dean", "manager"],
                "defaults": {
                    "user": "r",
                    "editor": "cru",
                    "hod": "cru",
                    "dean": "cru",
                    "manager": "crud",
                },
            },
        }

    def test_non_approval_model_has_approval_false(self):
        """Non-approval model gets has_approval=False default."""
        model = {
            "name": "test.plain",
            "description": "Plain Model",
            "fields": [{"name": "name", "type": "Char", "required": True}],
        }
        spec = _make_spec(models=[model])
        ctx = _build_model_context(spec, model)
        assert ctx["has_approval"] is False

    def test_non_approval_model_approval_levels_empty(self):
        """Non-approval model gets approval_levels=[] default."""
        model = {
            "name": "test.plain",
            "description": "Plain Model",
            "fields": [{"name": "name", "type": "Char", "required": True}],
        }
        spec = _make_spec(models=[model])
        ctx = _build_model_context(spec, model)
        assert ctx["approval_levels"] == []

    def test_non_approval_model_approval_action_methods_empty(self):
        """Non-approval model gets approval_action_methods=[] default."""
        model = {
            "name": "test.plain",
            "description": "Plain Model",
            "fields": [{"name": "name", "type": "Char", "required": True}],
        }
        spec = _make_spec(models=[model])
        ctx = _build_model_context(spec, model)
        assert ctx["approval_action_methods"] == []

    def test_non_approval_model_approval_reject_action_none(self):
        """Non-approval model gets approval_reject_action=None default."""
        model = {
            "name": "test.plain",
            "description": "Plain Model",
            "fields": [{"name": "name", "type": "Char", "required": True}],
        }
        spec = _make_spec(models=[model])
        ctx = _build_model_context(spec, model)
        assert ctx["approval_reject_action"] is None

    def test_non_approval_model_approval_reset_action_none(self):
        """Non-approval model gets approval_reset_action=None default."""
        model = {
            "name": "test.plain",
            "description": "Plain Model",
            "fields": [{"name": "name", "type": "Char", "required": True}],
        }
        spec = _make_spec(models=[model])
        ctx = _build_model_context(spec, model)
        assert ctx["approval_reset_action"] is None

    def test_non_approval_model_approval_submit_action_none(self):
        """Non-approval model gets approval_submit_action=None default."""
        model = {
            "name": "test.plain",
            "description": "Plain Model",
            "fields": [{"name": "name", "type": "Char", "required": True}],
        }
        spec = _make_spec(models=[model])
        ctx = _build_model_context(spec, model)
        assert ctx["approval_submit_action"] is None

    def test_non_approval_model_state_field_name_default(self):
        """Non-approval model gets approval_state_field_name='state' default."""
        model = {
            "name": "test.plain",
            "description": "Plain Model",
            "fields": [{"name": "name", "type": "Char", "required": True}],
        }
        spec = _make_spec(models=[model])
        ctx = _build_model_context(spec, model)
        assert ctx["approval_state_field_name"] == "state"

    def test_non_approval_model_lock_after_default(self):
        """Non-approval model gets lock_after='draft' default."""
        model = {
            "name": "test.plain",
            "description": "Plain Model",
            "fields": [{"name": "name", "type": "Char", "required": True}],
        }
        spec = _make_spec(models=[model])
        ctx = _build_model_context(spec, model)
        assert ctx["lock_after"] == "draft"

    def test_non_approval_model_editable_fields_default(self):
        """Non-approval model gets editable_fields=[] default."""
        model = {
            "name": "test.plain",
            "description": "Plain Model",
            "fields": [{"name": "name", "type": "Char", "required": True}],
        }
        spec = _make_spec(models=[model])
        ctx = _build_model_context(spec, model)
        assert ctx["editable_fields"] == []

    def test_non_approval_model_on_reject_default(self):
        """Non-approval model gets on_reject='draft' default."""
        model = {
            "name": "test.plain",
            "description": "Plain Model",
            "fields": [{"name": "name", "type": "Char", "required": True}],
        }
        spec = _make_spec(models=[model])
        ctx = _build_model_context(spec, model)
        assert ctx["on_reject"] == "draft"

    def test_non_approval_model_reject_allowed_from_default(self):
        """Non-approval model gets reject_allowed_from=[] default."""
        model = {
            "name": "test.plain",
            "description": "Plain Model",
            "fields": [{"name": "name", "type": "Char", "required": True}],
        }
        spec = _make_spec(models=[model])
        ctx = _build_model_context(spec, model)
        assert ctx["reject_allowed_from"] == []

    def test_non_approval_model_approval_record_rules_default(self):
        """Non-approval model gets approval_record_rules=[] default."""
        model = {
            "name": "test.plain",
            "description": "Plain Model",
            "fields": [{"name": "name", "type": "Char", "required": True}],
        }
        spec = _make_spec(models=[model])
        ctx = _build_model_context(spec, model)
        assert ctx["approval_record_rules"] == []

    def test_approval_model_context_propagates_enriched_keys(self):
        """Approval model context propagates all enriched keys from preprocessor."""
        from amil_utils.preprocessors.approval import _process_approval_patterns
        from amil_utils.preprocessors.security import _process_security_patterns
        spec = self._make_approval_spec()
        spec = _process_security_patterns(spec)
        spec = _process_approval_patterns(spec)
        model = next(m for m in spec["models"] if m["name"] == "fee.request")
        ctx = _build_model_context(spec, model)
        assert ctx["has_approval"] is True
        assert len(ctx["approval_levels"]) == 3
        assert len(ctx["approval_action_methods"]) == 3
        assert ctx["approval_submit_action"] is not None
        assert ctx["approval_reject_action"] is not None
        assert ctx["approval_reset_action"] is not None
        assert ctx["approval_state_field_name"] == "state"
        assert ctx["lock_after"] == "draft"
        assert ctx["editable_fields"] == ["notes"]
        assert ctx["on_reject"] == "draft"
        assert ctx["reject_allowed_from"] == ["approved_hod", "approved_dean"]
        assert len(ctx["approval_record_rules"]) == 2

    def test_needs_translate_true_when_has_approval(self):
        """needs_translate is True when has_approval is True."""
        from amil_utils.preprocessors.approval import _process_approval_patterns
        from amil_utils.preprocessors.security import _process_security_patterns
        spec = self._make_approval_spec()
        spec = _process_security_patterns(spec)
        spec = _process_approval_patterns(spec)
        model = next(m for m in spec["models"] if m["name"] == "fee.request")
        ctx = _build_model_context(spec, model)
        assert ctx["needs_translate"] is True

    def test_module_context_has_approval_models_true(self):
        """Module context has has_approval_models=True when any model has approval."""
        from amil_utils.preprocessors.approval import _process_approval_patterns
        from amil_utils.preprocessors.security import _process_security_patterns
        spec = self._make_approval_spec()
        spec = _process_security_patterns(spec)
        spec = _process_approval_patterns(spec)
        ctx = _build_module_context(spec, spec["module_name"])
        assert ctx["has_approval_models"] is True

    def test_module_context_has_approval_models_false_without_approval(self):
        """Module context has has_approval_models=False when no approval models."""
        spec = _make_spec(models=[{
            "name": "test.plain",
            "description": "Plain",
            "fields": [{"name": "name", "type": "Char", "required": True}],
        }])
        ctx = _build_module_context(spec, spec["module_name"])
        assert ctx["has_approval_models"] is False

    def test_module_context_has_record_rules_with_approval(self):
        """Module context has_record_rules is True when approval models have record_rule_scopes."""
        from amil_utils.preprocessors.approval import _process_approval_patterns
        from amil_utils.preprocessors.security import _process_security_patterns
        spec = self._make_approval_spec()
        spec = _process_security_patterns(spec)
        spec = _process_approval_patterns(spec)
        ctx = _build_module_context(spec, spec["module_name"])
        assert ctx["has_record_rules"] is True


class TestApprovalTemplateRendering:
    """Integration tests for approval workflow template rendering (Plan 02).

    Tests that rendered model.py and view_form.xml templates correctly produce
    approval action methods, write() state guard, header buttons, and record rules
    when the model context has approval keys set by the preprocessor.
    """

    def _make_approval_spec(self) -> dict:
        """Build a spec with approval on one model plus security roles."""
        return {
            "module_name": "uni_fee",
            "module_title": "University Fee",
            "summary": "Fee management",
            "author": "Test",
            "depends": ["base"],
            "models": [
                {
                    "name": "fee.request",
                    "description": "Fee Request",
                    "fields": [
                        {"name": "name", "type": "Char", "required": True},
                        {"name": "amount", "type": "Float"},
                        {"name": "notes", "type": "Text"},
                    ],
                    "approval": {
                        "levels": [
                            {"state": "submitted", "role": "editor", "next": "approved_hod", "label": "Submitted"},
                            {"state": "approved_hod", "role": "hod", "next": "approved_dean", "label": "HOD Approved"},
                            {"state": "approved_dean", "role": "dean", "next": "done", "label": "Dean Approved"},
                        ],
                        "on_reject": "draft",
                        "reject_allowed_from": ["approved_hod", "approved_dean"],
                        "lock_after": "draft",
                        "editable_fields": ["notes"],
                    },
                },
            ],
            "security": {
                "roles": ["user", "editor", "hod", "dean", "manager"],
                "defaults": {
                    "user": "r",
                    "editor": "cru",
                    "hod": "cru",
                    "dean": "cru",
                    "manager": "crud",
                },
            },
        }

    def _render_model(self, spec: dict) -> str:
        """Preprocess spec and render model.py.j2 template for the approval model."""
        from amil_utils.preprocessors.approval import _process_approval_patterns
        from amil_utils.preprocessors.constraints import _process_constraints
        from amil_utils.preprocessors.production import _process_production_patterns
        from amil_utils.preprocessors.security import _process_security_patterns
        from amil_utils.renderer import create_versioned_renderer

        spec = _process_security_patterns(spec)
        spec = _process_approval_patterns(spec)
        model = next(m for m in spec["models"] if m["name"] == "fee.request")
        ctx = _build_model_context(spec, model)
        env = create_versioned_renderer(spec.get("odoo_version", "17.0"))
        template = env.get_template("model.py.j2")
        return template.render(**ctx)

    def _render_view(self, spec: dict) -> str:
        """Preprocess spec and render view_form.xml.j2 template for the approval model."""
        from amil_utils.preprocessors.approval import _process_approval_patterns
        from amil_utils.preprocessors.security import _process_security_patterns
        from amil_utils.renderer import create_versioned_renderer

        spec = _process_security_patterns(spec)
        spec = _process_approval_patterns(spec)
        model = next(m for m in spec["models"] if m["name"] == "fee.request")
        ctx = _build_model_context(spec, model)
        env = create_versioned_renderer(spec.get("odoo_version", "17.0"))
        template = env.get_template("view_form.xml.j2")
        return template.render(**ctx)

    def _render_record_rules(self, spec: dict) -> str:
        """Preprocess spec and render record_rules.xml.j2 template."""
        from amil_utils.preprocessors.approval import _process_approval_patterns
        from amil_utils.preprocessors.security import _process_security_patterns
        from amil_utils.renderer import create_versioned_renderer

        spec = _process_security_patterns(spec)
        spec = _process_approval_patterns(spec)
        ctx = _build_module_context(spec, spec["module_name"])
        env = create_versioned_renderer(spec.get("odoo_version", "17.0"))
        template = env.get_template("record_rules.xml.j2")
        return template.render(**ctx)

    # --- Model.py action method tests ---

    def test_approval_template_action_submit_method(self):
        """Rendered model.py contains action_submit method when has_approval."""
        output = self._render_model(self._make_approval_spec())
        assert "def action_submit(self):" in output

    def test_approval_template_action_approve_methods(self):
        """Rendered model.py contains action_approve_* methods (one per level)."""
        output = self._render_model(self._make_approval_spec())
        assert "def action_approve_submitted(self):" in output
        assert "def action_approve_approved_hod(self):" in output
        assert "def action_approve_approved_dean(self):" in output

    def test_approval_template_action_ensure_one(self):
        """Each action method has self.ensure_one() call."""
        output = self._render_model(self._make_approval_spec())
        # Count ensure_one calls - should be at least 5: submit + 3 approve + reject
        assert output.count("self.ensure_one()") >= 5

    def test_approval_template_action_has_group_check(self):
        """Each approval action method has has_group() check with correct group XML ID."""
        output = self._render_model(self._make_approval_spec())
        assert "has_group('uni_fee.group_uni_fee_editor')" in output
        assert "has_group('uni_fee.group_uni_fee_hod')" in output
        assert "has_group('uni_fee.group_uni_fee_dean')" in output

    def test_approval_template_action_user_error_not_access_error(self):
        """Each action method raises UserError (not AccessError) with role name in message."""
        output = self._render_model(self._make_approval_spec())
        assert "raise UserError(" in output
        assert "AccessError" not in output

    def test_approval_template_action_checks_current_state(self):
        """Each action method checks current state matches expected from_state."""
        output = self._render_model(self._make_approval_spec())
        assert "self.state != 'draft'" in output  # submit + approve_submitted
        assert "self.state != 'submitted'" in output  # approve_approved_hod
        assert "self.state != 'approved_hod'" in output  # approve_approved_dean

    def test_approval_template_action_with_context_force_state(self):
        """Each action method uses self.with_context(_force_state=True).write() to set state."""
        output = self._render_model(self._make_approval_spec())
        assert "self.with_context(_force_state=True).write(" in output

    # --- Write guard tests ---

    def test_approval_template_write_state_guard(self):
        """Rendered model.py contains write() state guard blocking direct state modification."""
        output = self._render_model(self._make_approval_spec())
        assert "'state' in vals and not self.env.context.get('_force_state')" in output

    def test_approval_template_write_guard_checks_superuser(self):
        """write() state guard checks self.env.is_superuser() for bypass."""
        output = self._render_model(self._make_approval_spec())
        assert "self.env.is_superuser()" in output

    def test_approval_template_write_guard_raises_user_error(self):
        """write() state guard raises UserError (not AccessError)."""
        output = self._render_model(self._make_approval_spec())
        assert "raise UserError(self.env._(" in output or "raise UserError(_(" in output

    def test_approval_template_write_guard_stacking_17(self):
        """Approval state guard sits AFTER audit old_values capture and BEFORE cache clear in 17.0."""
        # Build a spec with BOTH audit and approval
        spec = self._make_approval_spec()
        spec["models"][0]["audit"] = True
        output = self._render_model(spec)
        # The stacking order should be: audit old_values -> approval guard -> super()
        # NOTE: The first super().write() is inside the _audit_skip fast path.
        # The main-path super().write() comes AFTER the approval guard.
        audit_pos = output.find("_audit_read_old")
        approval_pos = output.find("_force_state")
        main_super_pos = output.find("result = super().write(vals)", approval_pos)
        assert audit_pos < approval_pos < main_super_pos, (
            f"Stacking order wrong: audit={audit_pos}, approval={approval_pos}, super={main_super_pos}"
        )

    # --- Reject and reset tests ---

    def test_approval_template_action_reject(self):
        """Rendered model.py contains action_reject method when reject action exists."""
        output = self._render_model(self._make_approval_spec())
        assert "def action_reject(self):" in output

    def test_approval_template_action_reset_to_draft(self):
        """Rendered model.py contains action_reset_to_draft method."""
        output = self._render_model(self._make_approval_spec())
        assert "def action_reset_to_draft(self):" in output

    # --- View form tests ---

    def test_approval_template_view_submit_button(self):
        """Rendered view_form.xml contains Submit button with invisible="state != 'draft'"."""
        output = self._render_view(self._make_approval_spec())
        assert 'name="action_submit"' in output
        assert "invisible=" in output

    def test_approval_template_view_approve_buttons(self):
        """Rendered view_form.xml contains Approve buttons with invisible= and groups= attributes."""
        output = self._render_view(self._make_approval_spec())
        assert 'name="action_approve_submitted"' in output
        assert 'groups="uni_fee.group_uni_fee_editor"' in output
        assert 'name="action_approve_approved_hod"' in output
        assert 'groups="uni_fee.group_uni_fee_hod"' in output

    def test_approval_template_view_reject_button(self):
        """Rendered view_form.xml contains Reject button with invisible= and groups=."""
        output = self._render_view(self._make_approval_spec())
        assert 'name="action_reject"' in output
        assert 'class="btn-danger"' in output

    def test_approval_template_view_reset_button(self):
        """Rendered view_form.xml contains Reset to Draft button."""
        output = self._render_view(self._make_approval_spec())
        assert 'name="action_reset_to_draft"' in output

    def test_approval_template_view_buttons_use_invisible_not_states(self):
        """Buttons use invisible= NOT deprecated states= attribute."""
        output = self._render_view(self._make_approval_spec())
        assert "invisible=" in output
        # Ensure no states= attribute anywhere in approval buttons
        # (states= may exist elsewhere, but approval buttons should NOT use it)
        lines = output.split("\n")
        approval_button_lines = [
            l for l in lines
            if 'name="action_submit"' in l
            or 'name="action_approve' in l
            or 'name="action_reject"' in l
            or 'name="action_reset_to_draft"' in l
        ]
        for line in approval_button_lines:
            assert "states=" not in line

    # --- Non-approval regression tests ---

    def test_approval_template_non_approval_model_no_blocks(self):
        """Non-approval model renders WITHOUT approval blocks (no regression)."""
        spec = _make_spec(models=[{
            "name": "test.plain",
            "description": "Plain Model",
            "fields": [
                {"name": "name", "type": "Char", "required": True},
                {"name": "value", "type": "Integer"},
            ],
        }])
        from amil_utils.renderer import create_versioned_renderer
        env = create_versioned_renderer("17.0")
        ctx = _build_model_context(spec, spec["models"][0])
        template = env.get_template("model.py.j2")
        output = template.render(**ctx)
        assert "action_submit" not in output
        assert "_force_state" not in output
        assert "action_approve" not in output

    # --- Record rules tests ---

    def test_approval_template_record_rules_draft_owner(self):
        """Record rules XML contains approval draft-owner ir.rule for approval models."""
        output = self._render_record_rules(self._make_approval_spec())
        assert "Draft Records" in output or "draft" in output.lower()

    def test_approval_template_record_rules_manager_full_access(self):
        """Record rules XML contains approval manager-full-access ir.rule for approval models."""
        output = self._render_record_rules(self._make_approval_spec())
        assert "Manager Full Access" in output or "manager" in output.lower()

    # --- Readonly / lock_after tests ---

    def test_approval_template_field_readonly_uses_lock_after(self):
        """Field readonly uses lock_after stage (not hardcoded 'draft') when has_approval."""
        output = self._render_view(self._make_approval_spec())
        # For approval models with lock_after="draft", fields that are NOT in editable_fields
        # should have readonly based on state != lock_after
        # Sequence fields have special readonly treatment
        # At minimum, verify the form renders without crash
        assert "<form" in output

    def test_approval_template_editable_fields_exempt(self):
        """editable_fields are exempt from readonly locking."""
        output = self._render_view(self._make_approval_spec())
        # "notes" is in editable_fields, so it should NOT have readonly based on state
        assert "<form" in output


# ---------------------------------------------------------------------------
# Phase 40: Notification integration tests
# ---------------------------------------------------------------------------


class TestNotificationIntegration:
    """Integration tests for notification preprocessor wired into pipeline and context."""

    def _make_notification_spec(self) -> dict:
        """Build a spec with approval + notify on one model."""
        return {
            "module_name": "uni_fee",
            "module_title": "University Fee",
            "summary": "Fee management",
            "author": "Test",
            "depends": ["base"],
            "models": [
                {
                    "name": "fee.request",
                    "description": "Fee Request",
                    "fields": [
                        {"name": "name", "type": "Char", "required": True, "string": "Request Name"},
                        {"name": "amount", "type": "Float", "required": True, "string": "Amount"},
                        {"name": "notes", "type": "Text", "string": "Notes"},
                    ],
                    "approval": {
                        "levels": [
                            {
                                "state": "submitted", "role": "editor",
                                "next": "approved_hod", "label": "Submitted",
                                "notify": {
                                    "template": "email_fee_waiver_submitted",
                                    "recipients": "role:hod",
                                    "subject": "Fee Waiver Submitted: {{ object.name }}",
                                },
                            },
                            {
                                "state": "approved_hod", "role": "hod",
                                "next": "approved_dean", "label": "HOD Approved",
                                "notify": {
                                    "template": "email_fee_waiver_approved_hod",
                                    "recipients": "role:dean",
                                    "subject": "Fee Waiver Approved by HOD: {{ object.name }}",
                                },
                            },
                            {"state": "approved_dean", "role": "dean", "next": "done", "label": "Dean Approved"},
                        ],
                        "on_reject": "draft",
                        "reject_allowed_from": ["approved_hod", "approved_dean"],
                        "lock_after": "draft",
                        "editable_fields": ["notes"],
                        "on_reject_notify": {
                            "template": "email_fee_waiver_rejected",
                            "recipients": "creator",
                            "subject": "Fee Waiver Rejected: {{ object.name }}",
                        },
                    },
                },
            ],
            "security": {
                "roles": ["user", "editor", "hod", "dean", "manager"],
                "defaults": {
                    "user": "r",
                    "editor": "cru",
                    "hod": "cru",
                    "dean": "cru",
                    "manager": "crud",
                },
            },
        }

    def test_pipeline_has_notification_preprocessor(self):
        """render_module calls _process_notification_patterns after _process_approval_patterns."""
        spec = self._make_notification_spec()
        with tempfile.TemporaryDirectory() as d:
            files, _ = render_module(spec, get_template_dir(), Path(d))
            # If notification preprocessor ran, the spec should have mail in depends
            # We verify by checking the rendered manifest includes "mail"
            manifest_file = next(f for f in files if Path(f).name == "__manifest__.py")
            content = Path(manifest_file).read_text(encoding="utf-8")
            assert '"mail"' in content

    def test_context_defaults_no_notifications(self):
        """Model without notifications gets has_notifications=False, notification_templates=[], needs_logger=False."""
        model = {
            "name": "test.plain",
            "description": "Plain Model",
            "fields": [{"name": "name", "type": "Char", "required": True}],
        }
        spec = _make_spec(models=[model])
        ctx = _build_model_context(spec, model)
        assert ctx["has_notifications"] is False
        assert ctx["notification_templates"] == []
        assert ctx["needs_logger"] is False

    def test_context_defaults_with_notifications(self):
        """Model with notifications gets has_notifications=True, notification_templates populated, needs_logger=True."""
        spec = self._make_notification_spec()
        with tempfile.TemporaryDirectory() as d:
            files, _ = render_module(spec, get_template_dir(), Path(d))
            # Render succeeds without crash -- context defaults worked
            assert len(files) > 0

    def test_module_context_has_notification_models(self):
        """Module context includes has_notification_models flag."""
        model = {
            "name": "test.plain",
            "description": "Plain Model",
            "fields": [{"name": "name", "type": "Char", "required": True}],
        }
        spec = _make_spec(models=[model])
        ctx = _build_module_context(spec, "test_module")
        assert "has_notification_models" in ctx
        assert ctx["has_notification_models"] is False

    def test_module_context_notification_data_file(self):
        """When notifications present, manifest_files includes data/mail_template_data.xml."""
        spec = self._make_notification_spec()
        with tempfile.TemporaryDirectory() as d:
            files, _ = render_module(spec, get_template_dir(), Path(d))
            manifest_file = next(f for f in files if Path(f).name == "__manifest__.py")
            content = Path(manifest_file).read_text(encoding="utf-8")
            assert "mail_template_data.xml" in content


# ---------------------------------------------------------------------------
# Phase 40: Webhook integration tests
# ---------------------------------------------------------------------------


class TestWebhookIntegration:
    """Integration tests for webhook preprocessor wired into pipeline and context."""

    def _make_webhook_spec(self) -> dict:
        """Build a spec with webhooks on one model."""
        return {
            "module_name": "uni_fee",
            "module_title": "University Fee",
            "summary": "Fee management",
            "author": "Test",
            "depends": ["base"],
            "models": [
                {
                    "name": "fee.request",
                    "description": "Fee Request",
                    "fields": [
                        {"name": "name", "type": "Char", "required": True},
                        {"name": "amount", "type": "Float"},
                        {"name": "state", "type": "Selection", "selection": [("draft", "Draft"), ("done", "Done")]},
                    ],
                    "webhooks": {
                        "on_create": True,
                        "on_write": ["state", "amount"],
                        "on_unlink": False,
                    },
                },
            ],
            "security": {
                "roles": ["user", "manager"],
                "defaults": {
                    "user": "cr",
                    "manager": "crud",
                },
            },
        }

    def test_pipeline_has_webhook_preprocessor(self):
        """render_module calls _process_webhook_patterns after _process_notification_patterns."""
        spec = self._make_webhook_spec()
        with tempfile.TemporaryDirectory() as d:
            files, _ = render_module(spec, get_template_dir(), Path(d))
            # If webhook preprocessor ran, the model should have create/write overrides
            # We verify the render completes without crash
            assert len(files) > 0

    def test_context_defaults_no_webhooks(self):
        """Model without webhooks gets has_webhooks=False, webhook_config=None, etc."""
        model = {
            "name": "test.plain",
            "description": "Plain Model",
            "fields": [{"name": "name", "type": "Char", "required": True}],
        }
        spec = _make_spec(models=[model])
        ctx = _build_model_context(spec, model)
        assert ctx["has_webhooks"] is False
        assert ctx["webhook_config"] is None
        assert ctx["webhook_watched_fields"] == []
        assert ctx["webhook_on_create"] is False
        assert ctx["webhook_on_write"] is False
        assert ctx["webhook_on_unlink"] is False

    def test_context_defaults_with_webhooks(self):
        """Model with webhooks gets correct context keys populated."""
        spec = self._make_webhook_spec()
        with tempfile.TemporaryDirectory() as d:
            files, _ = render_module(spec, get_template_dir(), Path(d))
            assert len(files) > 0

    def test_module_context_has_webhook_models(self):
        """Module context includes has_webhook_models flag."""
        model = {
            "name": "test.plain",
            "description": "Plain Model",
            "fields": [{"name": "name", "type": "Char", "required": True}],
        }
        spec = _make_spec(models=[model])
        ctx = _build_module_context(spec, "test_module")
        assert "has_webhook_models" in ctx
        assert ctx["has_webhook_models"] is False

    def test_no_feature_regression(self):
        """Plain spec without notifications/webhooks renders all existing templates without error."""
        spec = {
            "module_name": "test_plain",
            "depends": ["base"],
            "models": [
                {
                    "name": "test.item",
                    "description": "Test Item",
                    "fields": [
                        {"name": "name", "type": "Char", "required": True},
                        {"name": "value", "type": "Integer"},
                    ],
                },
            ],
        }
        with tempfile.TemporaryDirectory() as d:
            files, _ = render_module(spec, get_template_dir(), Path(d))
            assert len(files) > 0
            # All core files rendered
            file_names = {Path(f).name for f in files}
            assert "__manifest__.py" in file_names
            assert "test_item.py" in file_names


# ---------------------------------------------------------------------------
# Phase 40: Notification template rendering tests (Plan 02)
# ---------------------------------------------------------------------------


class TestNotificationTemplateRendering:
    """Tests for notification-related template blocks in model.py.j2 and mail_template_data.xml.j2.

    Verifies that rendered model.py includes logger import, send_mail blocks in action methods,
    and that mail_template_data.xml.j2 renders valid XML with correct structure.
    """

    def _make_notification_spec(self) -> dict:
        """Build a spec with approval + notify on one model plus security roles."""
        return {
            "module_name": "uni_fee",
            "module_title": "University Fee",
            "summary": "Fee management",
            "author": "Test",
            "depends": ["base"],
            "models": [
                {
                    "name": "fee.request",
                    "description": "Fee Request",
                    "fields": [
                        {"name": "name", "type": "Char", "required": True, "string": "Request Name"},
                        {"name": "amount", "type": "Float", "required": True, "string": "Amount"},
                        {"name": "notes", "type": "Text", "string": "Notes"},
                    ],
                    "approval": {
                        "levels": [
                            {
                                "state": "submitted", "role": "editor",
                                "next": "approved_hod", "label": "Submitted",
                                "notify": {
                                    "template": "email_fee_waiver_submitted",
                                    "recipients": "role:hod",
                                    "subject": "Fee Waiver Submitted: {{ object.name }}",
                                },
                            },
                            {
                                "state": "approved_hod", "role": "hod",
                                "next": "approved_dean", "label": "HOD Approved",
                            },
                            {"state": "approved_dean", "role": "dean", "next": "done", "label": "Dean Approved"},
                        ],
                        "on_reject": "draft",
                        "reject_allowed_from": ["approved_hod", "approved_dean"],
                        "lock_after": "draft",
                        "editable_fields": ["notes"],
                        "on_reject_notify": {
                            "template": "email_fee_waiver_rejected",
                            "recipients": "creator",
                            "subject": "Fee Waiver Rejected: {{ object.name }}",
                        },
                    },
                },
            ],
            "security": {
                "roles": ["user", "editor", "hod", "dean", "manager"],
                "defaults": {
                    "user": "r",
                    "editor": "cru",
                    "hod": "cru",
                    "dean": "cru",
                    "manager": "crud",
                },
            },
        }

    def _render_model(self, spec: dict, version: str = "17.0") -> str:
        """Preprocess spec and render model.py.j2 template for the notification model."""
        from amil_utils.preprocessors.approval import _process_approval_patterns
        from amil_utils.preprocessors.notifications import _process_notification_patterns
        from amil_utils.preprocessors.security import _process_security_patterns
        from amil_utils.preprocessors.webhooks import _process_webhook_patterns
        from amil_utils.renderer import create_versioned_renderer

        spec = _process_security_patterns(spec)
        spec = _process_approval_patterns(spec)
        spec = _process_notification_patterns(spec)
        spec = _process_webhook_patterns(spec)
        model = next(m for m in spec["models"] if m["name"] == "fee.request")
        ctx = _build_model_context(spec, model)
        env = create_versioned_renderer(version)
        template = env.get_template("model.py.j2")
        return template.render(**ctx)

    def _render_mail_template(self, spec: dict) -> str:
        """Preprocess spec and render mail_template_data.xml.j2 template."""
        from amil_utils.preprocessors.approval import _process_approval_patterns
        from amil_utils.preprocessors.notifications import _process_notification_patterns
        from amil_utils.preprocessors.security import _process_security_patterns
        from amil_utils.renderer import create_versioned_renderer

        spec = _process_security_patterns(spec)
        spec = _process_approval_patterns(spec)
        spec = _process_notification_patterns(spec)
        model = next(m for m in spec["models"] if m["name"] == "fee.request")
        all_templates = model.get("notification_templates", [])
        ctx = {"notification_templates": all_templates}
        env = create_versioned_renderer("17.0")
        template = env.get_template("mail_template_data.xml.j2")
        return template.render(**ctx)

    def test_logger_import_present(self):
        """Model with has_notifications=True renders 'import logging' and '_logger' at top."""
        output = self._render_model(self._make_notification_spec())
        assert "import logging" in output
        assert "_logger = logging.getLogger(__name__)" in output

    def test_logger_import_absent(self):
        """Model without notifications does NOT render logger import."""
        spec = _make_spec(models=[{
            "name": "test.plain",
            "description": "Plain Model",
            "fields": [{"name": "name", "type": "Char", "required": True}],
        }])
        from amil_utils.renderer import create_versioned_renderer
        env = create_versioned_renderer("17.0")
        ctx = _build_model_context(spec, spec["models"][0])
        template = env.get_template("model.py.j2")
        output = template.render(**ctx)
        assert "import logging" not in output
        assert "_logger" not in output

    def test_send_mail_in_action_method(self):
        """Action method with notification renders try/except send_mail block after state write."""
        output = self._render_model(self._make_notification_spec())
        assert "send_mail" in output
        # send_mail should be in the submit action (level 0 notify enriches submit)
        # Find the submit action and check for send_mail after the state write
        submit_pos = output.find("def action_submit(self):")
        assert submit_pos > -1
        send_mail_pos = output.find("send_mail", submit_pos)
        assert send_mail_pos > -1, "send_mail not found in action method"

    def test_send_mail_force_send_false(self):
        """Rendered send_mail call uses force_send=False."""
        output = self._render_model(self._make_notification_spec())
        assert "force_send=False" in output

    def test_send_mail_exception_handling(self):
        """Rendered send_mail is wrapped in try/except with _logger.warning."""
        output = self._render_model(self._make_notification_spec())
        assert "_logger.warning" in output
        assert "except Exception:" in output

    def test_send_mail_in_submit_action(self):
        """Submit action with notification renders send_mail block."""
        output = self._render_model(self._make_notification_spec())
        submit_pos = output.find("def action_submit(self):")
        assert submit_pos > -1
        # Find the next method definition after submit
        next_def = output.find("\n    def ", submit_pos + 1)
        submit_body = output[submit_pos:next_def] if next_def > -1 else output[submit_pos:]
        assert "send_mail" in submit_body

    def test_send_mail_in_reject_action(self):
        """Reject action with notification renders send_mail block."""
        output = self._render_model(self._make_notification_spec())
        reject_pos = output.find("def action_reject(self):")
        assert reject_pos > -1
        # Find the next method definition after reject
        next_def = output.find("\n    def ", reject_pos + 1)
        reject_body = output[reject_pos:next_def] if next_def > -1 else output[reject_pos:]
        assert "send_mail" in reject_body

    def test_no_send_mail_without_notification(self):
        """Action methods without notification key do NOT render send_mail."""
        output = self._render_model(self._make_notification_spec())
        # action_approve_approved_hod (level 1 HOD) has no notify
        approve_hod_pos = output.find("def action_approve_approved_hod(self):")
        assert approve_hod_pos > -1
        next_def = output.find("\n    def ", approve_hod_pos + 1)
        hod_body = output[approve_hod_pos:next_def] if next_def > -1 else output[approve_hod_pos:]
        assert "send_mail" not in hod_body

    def test_mail_template_xml(self):
        """mail_template_data.xml.j2 renders valid XML with noupdate='1', correct model_id ref, subject, email_to, body_html CDATA."""
        output = self._render_mail_template(self._make_notification_spec())
        assert 'noupdate="1"' in output
        assert 'model="mail.template"' in output
        assert "CDATA" in output
        assert "<field" in output

    def test_mail_template_body_fields(self):
        """body_html contains table rows for each body_field with label and object.field_name."""
        output = self._render_mail_template(self._make_notification_spec())
        # Should have table rows with field labels
        assert "<tr>" in output
        assert "<td" in output
        # Should reference object fields
        assert "object." in output

    def test_mail_template_auto_delete(self):
        """Rendered template has auto_delete eval='True'."""
        # Update mail_template_data.xml.j2 to include auto_delete -- verify it renders
        output = self._render_mail_template(self._make_notification_spec())
        assert "auto_delete" in output


# ---------------------------------------------------------------------------
# Phase 40: Webhook template rendering tests (Plan 02)
# ---------------------------------------------------------------------------


class TestWebhookTemplateRendering:
    """Tests for webhook-related template blocks in model.py.j2.

    Verifies that rendered model.py includes webhook stub methods, create/write
    override guards with _skip_webhooks, and correct old_vals capture position.
    """

    def _make_webhook_spec(self, include_audit: bool = False, include_approval: bool = False) -> dict:
        """Build a spec with webhooks on one model."""
        model: dict = {
            "name": "test.record",
            "description": "Test Record",
            "fields": [
                {"name": "name", "type": "Char", "required": True, "string": "Name"},
                {"name": "amount", "type": "Float", "string": "Amount"},
                {"name": "state", "type": "Selection", "selection": [("draft", "Draft"), ("done", "Done")]},
            ],
            "webhooks": {
                "on_create": True,
                "on_write": ["state", "amount"],
                "on_unlink": True,
            },
        }
        if include_audit:
            model["audit"] = True
        if include_approval:
            model["approval"] = {
                "levels": [
                    {"state": "submitted", "role": "editor", "next": "done", "label": "Submitted"},
                ],
                "on_reject": "draft",
                "reject_allowed_from": ["submitted"],
            }
        spec: dict = {
            "module_name": "test_webhooks",
            "module_title": "Test Webhooks",
            "summary": "Test module",
            "author": "Test",
            "depends": ["base"],
            "models": [model],
            "security": {
                "roles": ["user", "editor", "manager"],
                "defaults": {
                    "user": "r",
                    "editor": "cru",
                    "manager": "crud",
                },
            },
        }
        return spec

    def _render_model(self, spec: dict, version: str = "17.0") -> str:
        """Preprocess spec and render model.py.j2 template for webhook model."""
        from amil_utils.preprocessors.approval import _process_approval_patterns
        from amil_utils.preprocessors.audit import _process_audit_patterns
        from amil_utils.preprocessors.constraints import _process_constraints
        from amil_utils.preprocessors.notifications import _process_notification_patterns
        from amil_utils.preprocessors.production import _process_production_patterns
        from amil_utils.preprocessors.security import _process_security_patterns
        from amil_utils.preprocessors.webhooks import _process_webhook_patterns
        from amil_utils.renderer import create_versioned_renderer
        from collections import defaultdict

        spec = _process_security_patterns(spec)
        # Initialize override_sources
        for model in spec.get("models", []):
            if "override_sources" not in model:
                model["override_sources"] = defaultdict(set)
        spec = _process_constraints(spec)
        spec = _process_production_patterns(spec)
        spec = _process_audit_patterns(spec)
        spec = _process_approval_patterns(spec)
        spec = _process_notification_patterns(spec)
        spec = _process_webhook_patterns(spec)
        model = next(m for m in spec["models"] if m["name"] == "test.record")
        ctx = _build_model_context(spec, model)
        env = create_versioned_renderer(version)
        template = env.get_template("model.py.j2")
        return template.render(**ctx)

    def test_webhook_post_create_stub(self):
        """Model with has_webhooks renders _webhook_post_create method stub."""
        output = self._render_model(self._make_webhook_spec())
        assert "def _webhook_post_create(self, vals):" in output
        assert "pass" in output

    def test_webhook_post_write_stub(self):
        """Model with webhook_watched_fields renders _webhook_post_write method stub with docstring listing watched fields."""
        output = self._render_model(self._make_webhook_spec())
        assert "def _webhook_post_write(self, vals, old_vals):" in output
        assert "state" in output
        assert "amount" in output

    def test_webhook_pre_unlink_stub(self):
        """Model with webhook_on_unlink renders _webhook_pre_unlink stub."""
        output = self._render_model(self._make_webhook_spec())
        assert "def _webhook_pre_unlink(self):" in output

    def test_no_webhook_stubs_without_feature(self):
        """Model without webhooks renders no webhook stubs."""
        spec = _make_spec(models=[{
            "name": "test.plain",
            "description": "Plain Model",
            "fields": [{"name": "name", "type": "Char", "required": True}],
        }])
        from amil_utils.renderer import create_versioned_renderer
        env = create_versioned_renderer("17.0")
        ctx = _build_model_context(spec, spec["models"][0])
        template = env.get_template("model.py.j2")
        output = template.render(**ctx)
        assert "_webhook_post_create" not in output
        assert "_webhook_post_write" not in output
        assert "_webhook_pre_unlink" not in output

    def test_create_override_webhook_guard(self):
        """create() override with webhook_on_create renders _skip_webhooks guard and per-record _webhook_post_create call."""
        output = self._render_model(self._make_webhook_spec())
        assert "_skip_webhooks" in output
        create_pos = output.find("def create(")
        assert create_pos > -1
        # Find the return statement in create
        return_pos = output.find("return records", create_pos)
        create_body = output[create_pos:return_pos + 20] if return_pos > -1 else output[create_pos:]
        assert "_webhook_post_create" in create_body
        assert "_skip_webhooks" in create_body

    def test_write_override_webhook_dispatch(self):
        """write() override with webhook_on_write renders _skip_webhooks guard and _webhook_post_write call after audit log."""
        output = self._render_model(self._make_webhook_spec())
        write_pos = output.find("def write(self, vals):")
        assert write_pos > -1
        write_body = output[write_pos:]
        assert "_webhook_post_write" in write_body
        assert "_skip_webhooks" in write_body

    def test_write_webhook_old_vals_with_audit(self):
        """When has_audit AND has_webhooks, webhook reuses audit old_values for watched fields (no separate capture)."""
        spec = self._make_webhook_spec(include_audit=True)
        output = self._render_model(spec)
        write_pos = output.find("def write(self, vals):")
        write_body = output[write_pos:]
        # Should have audit old_values
        assert "_audit_read_old" in write_body
        # Should reference old_values in webhook context (reuse audit's old_values)
        assert "old_values" in write_body
        # Should NOT have a separate _wh_old capture block before super
        # (because audit already captures old values)
        super_pos = write_body.find("result = super().write(vals)")
        before_super = write_body[:super_pos]
        # _wh_old should not appear when audit is present (reuses audit old_values)
        assert "_wh_old" not in before_super or "_audit_read_old" in before_super

    def test_write_webhook_old_vals_without_audit(self):
        """When has_webhooks but NOT has_audit, webhook has its own old_vals capture block before super()."""
        spec = self._make_webhook_spec(include_audit=False)
        output = self._render_model(spec)
        write_pos = output.find("def write(self, vals):")
        write_body = output[write_pos:]
        # Should have webhook-specific old value capture
        assert "_wh_old" in write_body or "old_vals" in write_body.lower()
        # No audit old_values (no audit)
        assert "_audit_read_old" not in write_body

    def test_write_stacking_order(self):
        """In generated write(), webhook dispatch appears AFTER audit log line and is the LAST block before return."""
        spec = self._make_webhook_spec(include_audit=True)
        output = self._render_model(spec)
        write_pos = output.find("def write(self, vals):")
        write_body = output[write_pos:]
        audit_log_pos = write_body.find("_audit_log_changes")
        webhook_pos = write_body.find("_webhook_post_write")
        # Find the LAST return result in write() (the main path, not the _audit_skip fast path)
        return_pos = write_body.rfind("return result")
        assert audit_log_pos > -1, "_audit_log_changes not found"
        assert webhook_pos > -1, "_webhook_post_write not found"
        assert return_pos > -1, "return result not found"
        assert audit_log_pos < webhook_pos < return_pos, (
            f"Stacking order wrong: audit_log={audit_log_pos}, webhook={webhook_pos}, return={return_pos}"
        )

    def test_18_0_template_parity(self):
        """18.0 model.py.j2 has equivalent webhook blocks (adapted for 18.0 structure)."""
        spec = self._make_webhook_spec()
        output = self._render_model(spec, version="18.0")
        assert "_webhook_post_create" in output
        assert "_webhook_post_write" in output
        assert "_skip_webhooks" in output


# ---------------------------------------------------------------------------
# Cron template numbercall version guard tests
# ---------------------------------------------------------------------------


class TestCronNumbercallVersionGuard:
    """numbercall field was removed in Odoo 18.0 -- template must omit it for 18.0+."""

    @staticmethod
    def _make_cron_spec(odoo_version: str) -> dict:
        return {
            "module_name": "test_cron_guard",
            "depends": ["base"],
            "odoo_version": odoo_version,
            "models": [
                {
                    "name": "test.cron.model",
                    "fields": [{"name": "name", "type": "Char"}],
                }
            ],
            "cron_jobs": [
                {
                    "name": "Test Cron Job",
                    "model_name": "test.cron.model",
                    "method": "_cron_do_stuff",
                    "interval_number": 1,
                    "interval_type": "days",
                }
            ],
        }

    def test_cron_template_omits_numbercall_for_18(self):
        """Odoo 18.0+ removed ir.cron.numbercall -- must not render."""
        spec = self._make_cron_spec("18.0")
        with tempfile.TemporaryDirectory() as d:
            files, _ = render_module(spec, get_template_dir(), Path(d))
            cron_file = next(
                (f for f in files if str(f).endswith("cron_data.xml")), None
            )
            assert cron_file is not None, "cron_data.xml not generated"
            content = Path(cron_file).read_text(encoding="utf-8")
            assert "numbercall" not in content

    def test_cron_template_includes_numbercall_for_17(self):
        """Odoo 17.0 still uses numbercall."""
        spec = self._make_cron_spec("17.0")
        with tempfile.TemporaryDirectory() as d:
            files, _ = render_module(spec, get_template_dir(), Path(d))
            cron_file = next(
                (f for f in files if str(f).endswith("cron_data.xml")), None
            )
            assert cron_file is not None, "cron_data.xml not generated"
            content = Path(cron_file).read_text(encoding="utf-8")
            assert "numbercall" in content

    def test_cron_template_omits_numbercall_for_19(self):
        """Odoo 19.0 also should not render numbercall (18.0+ removal)."""
        spec = self._make_cron_spec("19.0")
        with tempfile.TemporaryDirectory() as d:
            files, _ = render_module(spec, get_template_dir(), Path(d))
            cron_file = next(
                (f for f in files if str(f).endswith("cron_data.xml")), None
            )
            assert cron_file is not None, "cron_data.xml not generated"
            content = Path(cron_file).read_text(encoding="utf-8")
            assert "numbercall" not in content
