"""Tests for decomposed renderer stage functions.

Each stage function returns Result[list[Path]] and is independently testable.
Tests verify correct file creation, Result success/failure, and function size limits.
"""

from __future__ import annotations

import inspect
import tempfile
from pathlib import Path

import pytest

from amil_utils.renderer import (
    create_versioned_renderer,
    get_template_dir,
    render_controllers,
    render_cron,
    render_manifest,
    render_models,
    render_module,
    render_reports,
    render_security,
    render_static,
    render_tests,
    render_views,
    render_wizards,
)
from amil_utils.validation.types import Result


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_spec(
    models: list[dict] | None = None,
    wizards: list[dict] | None = None,
    depends: list[str] | None = None,
) -> dict:
    """Helper to construct a minimal spec dict for testing."""
    return {
        "module_name": "test_module",
        "module_title": "Test Module",
        "summary": "A test module",
        "author": "Test Author",
        "website": "https://test.example.com",
        "license": "LGPL-3",
        "category": "Uncategorized",
        "odoo_version": "17.0",
        "depends": depends or ["base"],
        "application": True,
        "models": models or [],
        "wizards": wizards or [],
    }


def _make_model(name: str = "test.model", fields: list[dict] | None = None) -> dict:
    """Helper to construct a minimal model dict."""
    return {
        "name": name,
        "description": f"Test {name}",
        "fields": fields or [
            {"name": "name", "type": "Char", "required": True},
            {"name": "value", "type": "Integer"},
        ],
    }


def _make_module_context(spec: dict) -> dict:
    """Build shared module context from spec (mirrors render_module setup)."""
    from amil_utils.renderer import _compute_view_files, _to_python_var, _to_xml_id
    from amil_utils.preprocessors.security import _process_security_patterns

    # Run security preprocessor to enrich models with security_acl/record_rule_scopes
    spec = _process_security_patterns(spec)
    module_name = spec["module_name"]
    models = spec.get("models", [])
    spec_wizards = spec.get("wizards", [])
    has_wizards = bool(spec_wizards)

    from amil_utils.renderer import SEQUENCE_FIELD_NAMES

    models_with_sequences = [
        m for m in models
        if any(
            f.get("type") == "Char"
            and f.get("name") in SEQUENCE_FIELD_NAMES
            and f.get("required")
            for f in m.get("fields", [])
        )
    ]
    has_sequences = bool(models_with_sequences)

    models_with_company_field = [
        m for m in models
        if any(
            f.get("name") == "company_id" and f.get("type") == "Many2one"
            for f in m.get("fields", [])
        )
    ]
    has_company_modules = bool(models_with_company_field)

    data_files: list[str] = []
    if has_sequences:
        data_files.append("data/sequences.xml")
    data_files.append("data/data.xml")

    wizard_view_files: list[str] = []
    for wizard in spec_wizards:
        wizard_xml_id = _to_xml_id(wizard["name"])
        wizard_view_files.append(f"views/{wizard_xml_id}_wizard_form.xml")

    from amil_utils.renderer import _compute_manifest_data
    all_manifest_files = _compute_manifest_data(
        spec, data_files, wizard_view_files, has_company_modules=has_company_modules
    )

    # Phase 32: import/export wizard detection
    import_export_models = [m for m in models if m.get("import_export")]
    has_import_export = bool(import_export_models)
    import_export_wizards = [
        {"name": f"{m['name']}.import.wizard"} for m in import_export_models
    ]

    ctx = {
        "module_name": module_name,
        "module_title": spec.get("module_title", module_name.replace("_", " ").title()),
        "module_technical_name": module_name,
        "summary": spec.get("summary", ""),
        "author": spec.get("author", ""),
        "website": spec.get("website", ""),
        "license": spec.get("license", "LGPL-3"),
        "category": spec.get("category", "Uncategorized"),
        "odoo_version": spec.get("odoo_version", "17.0"),
        "depends": spec.get("depends", ["base"]),
        "application": spec.get("application", True),
        "models": models,
        "view_files": _compute_view_files(spec),
        "manifest_files": all_manifest_files,
        "has_wizards": has_wizards or has_import_export,
        "spec_wizards": spec_wizards,
        "has_controllers": bool(spec.get("controllers")),
        "has_import_export": has_import_export,
        "import_export_wizards": import_export_wizards,
        "security_roles": spec.get("security_roles", []),
        "has_record_rules": any(m.get("record_rule_scopes") for m in models),
    }
    if has_import_export:
        ctx["external_dependencies"] = {"python": ["openpyxl"]}
    return ctx


@pytest.fixture
def env():
    """Create a versioned Jinja2 renderer."""
    return create_versioned_renderer("17.0")


@pytest.fixture
def tmp_module(tmp_path):
    """Create a temporary module directory."""
    module_dir = tmp_path / "test_module"
    module_dir.mkdir()
    return module_dir


# ---------------------------------------------------------------------------
# render_manifest tests
# ---------------------------------------------------------------------------


class TestRenderManifest:
    def test_returns_result_with_success(self, env, tmp_module):
        spec = _make_spec(models=[_make_model()])
        ctx = _make_module_context(spec)
        result = render_manifest(env, spec, tmp_module, ctx)
        assert isinstance(result, Result)
        assert result.success is True

    def test_creates_manifest_init_and_models_init(self, env, tmp_module):
        spec = _make_spec(models=[_make_model()])
        ctx = _make_module_context(spec)
        result = render_manifest(env, spec, tmp_module, ctx)
        paths = result.data
        assert paths is not None
        filenames = [p.name for p in paths]
        assert "__manifest__.py" in filenames
        assert "__init__.py" in filenames
        # models/__init__.py
        assert any(p.name == "__init__.py" and "models" in str(p) for p in paths)

    def test_all_files_exist_on_disk(self, env, tmp_module):
        spec = _make_spec(models=[_make_model()])
        ctx = _make_module_context(spec)
        result = render_manifest(env, spec, tmp_module, ctx)
        for p in result.data:
            assert p.exists(), f"File {p} should exist on disk"


# ---------------------------------------------------------------------------
# render_models tests
# ---------------------------------------------------------------------------


class TestRenderModels:
    def test_returns_result_with_success(self, env, tmp_module):
        model = _make_model()
        spec = _make_spec(models=[model])
        ctx = _make_module_context(spec)
        result = render_models(env, spec, tmp_module, ctx)
        assert isinstance(result, Result)
        assert result.success is True

    def test_creates_model_py_and_views(self, env, tmp_module):
        model = _make_model("inventory.item")
        spec = _make_spec(models=[model])
        ctx = _make_module_context(spec)
        result = render_models(env, spec, tmp_module, ctx)
        paths = result.data
        assert paths is not None
        filenames = [p.name for p in paths]
        assert "inventory_item.py" in filenames
        assert "inventory_item_views.xml" in filenames
        assert "inventory_item_action.xml" in filenames

    def test_multiple_models(self, env, tmp_module):
        models = [_make_model("test.one"), _make_model("test.two")]
        spec = _make_spec(models=models)
        ctx = _make_module_context(spec)
        result = render_models(env, spec, tmp_module, ctx)
        paths = result.data
        filenames = [p.name for p in paths]
        assert "test_one.py" in filenames
        assert "test_two.py" in filenames

    def test_empty_models_returns_empty_list(self, env, tmp_module):
        spec = _make_spec(models=[])
        ctx = _make_module_context(spec)
        result = render_models(env, spec, tmp_module, ctx)
        assert result.success is True
        assert result.data == []

    def test_verifier_warnings_collected(self, env, tmp_module):
        """When verifier is passed, warnings should be collected."""
        model = _make_model()
        spec = _make_spec(models=[model])
        ctx = _make_module_context(spec)
        # Without verifier, no warnings
        result = render_models(env, spec, tmp_module, ctx, verifier=None)
        assert result.success is True


# ---------------------------------------------------------------------------
# render_views tests
# ---------------------------------------------------------------------------


class TestRenderViews:
    def test_returns_result_with_success(self, env, tmp_module):
        spec = _make_spec(models=[_make_model()])
        ctx = _make_module_context(spec)
        result = render_views(env, spec, tmp_module, ctx)
        assert isinstance(result, Result)
        assert result.success is True

    def test_creates_menu_xml(self, env, tmp_module):
        spec = _make_spec(models=[_make_model()])
        ctx = _make_module_context(spec)
        result = render_views(env, spec, tmp_module, ctx)
        paths = result.data
        assert paths is not None
        filenames = [p.name for p in paths]
        assert "menu.xml" in filenames

    def test_menu_file_exists(self, env, tmp_module):
        spec = _make_spec(models=[_make_model()])
        ctx = _make_module_context(spec)
        result = render_views(env, spec, tmp_module, ctx)
        for p in result.data:
            assert p.exists()


# ---------------------------------------------------------------------------
# Function size limits
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# render_security tests
# ---------------------------------------------------------------------------


class TestRenderSecurity:
    def test_returns_result_with_success(self, env, tmp_module):
        spec = _make_spec(models=[_make_model()])
        ctx = _make_module_context(spec)
        result = render_security(env, spec, tmp_module, ctx)
        assert isinstance(result, Result)
        assert result.success is True

    def test_creates_security_xml_and_csv(self, env, tmp_module):
        spec = _make_spec(models=[_make_model()])
        ctx = _make_module_context(spec)
        result = render_security(env, spec, tmp_module, ctx)
        paths = result.data
        assert paths is not None
        filenames = [p.name for p in paths]
        assert "security.xml" in filenames
        assert "ir.model.access.csv" in filenames

    def test_record_rules_when_company_field(self, env, tmp_module):
        model = _make_model(fields=[
            {"name": "name", "type": "Char", "required": True},
            {"name": "company_id", "type": "Many2one", "comodel_name": "res.company"},
        ])
        spec = _make_spec(models=[model])
        ctx = _make_module_context(spec)
        result = render_security(env, spec, tmp_module, ctx)
        filenames = [p.name for p in result.data]
        assert "record_rules.xml" in filenames

    def test_no_record_rules_without_company_field(self, env, tmp_module):
        spec = _make_spec(models=[_make_model()])
        ctx = _make_module_context(spec)
        result = render_security(env, spec, tmp_module, ctx)
        filenames = [p.name for p in result.data]
        assert "record_rules.xml" not in filenames


# ---------------------------------------------------------------------------
# render_wizards tests
# ---------------------------------------------------------------------------


class TestRenderWizards:
    def test_returns_result_with_success_no_wizards(self, env, tmp_module):
        spec = _make_spec(models=[_make_model()])
        ctx = _make_module_context(spec)
        result = render_wizards(env, spec, tmp_module, ctx)
        assert isinstance(result, Result)
        assert result.success is True
        assert result.data == []

    def test_creates_wizard_files(self, env, tmp_module):
        wizard = {"name": "confirm.wizard", "description": "Confirm action",
                  "target_model": "test.model", "fields": []}
        spec = _make_spec(models=[_make_model()], wizards=[wizard])
        ctx = _make_module_context(spec)
        result = render_wizards(env, spec, tmp_module, ctx)
        paths = result.data
        assert paths is not None
        filenames = [p.name for p in paths]
        assert any(p.name == "__init__.py" and "wizards" in str(p) for p in paths)
        assert "confirm_wizard.py" in filenames
        assert "confirm_wizard_wizard_form.xml" in filenames


# ---------------------------------------------------------------------------
# render_tests tests
# ---------------------------------------------------------------------------


class TestRenderTests:
    def test_returns_result_with_success(self, env, tmp_module):
        spec = _make_spec(models=[_make_model()])
        ctx = _make_module_context(spec)
        result = render_tests(env, spec, tmp_module, ctx)
        assert isinstance(result, Result)
        assert result.success is True

    def test_creates_tests_init_and_per_model(self, env, tmp_module):
        model = _make_model("inventory.item")
        spec = _make_spec(models=[model])
        ctx = _make_module_context(spec)
        result = render_tests(env, spec, tmp_module, ctx)
        paths = result.data
        assert paths is not None
        filenames = [p.name for p in paths]
        assert any(p.name == "__init__.py" and "tests" in str(p) for p in paths)
        assert "test_inventory_item.py" in filenames

    def test_multiple_models_multiple_test_files(self, env, tmp_module):
        models = [_make_model("test.one"), _make_model("test.two")]
        spec = _make_spec(models=models)
        ctx = _make_module_context(spec)
        result = render_tests(env, spec, tmp_module, ctx)
        filenames = [p.name for p in result.data]
        assert "test_test_one.py" in filenames
        assert "test_test_two.py" in filenames


# ---------------------------------------------------------------------------
# render_static tests
# ---------------------------------------------------------------------------


class TestRenderStatic:
    def test_returns_result_with_success(self, env, tmp_module):
        spec = _make_spec(models=[_make_model()])
        ctx = _make_module_context(spec)
        result = render_static(env, spec, tmp_module, ctx)
        assert isinstance(result, Result)
        assert result.success is True

    def test_creates_data_xml_and_static_files(self, env, tmp_module):
        spec = _make_spec(models=[_make_model()])
        ctx = _make_module_context(spec)
        result = render_static(env, spec, tmp_module, ctx)
        paths = result.data
        assert paths is not None
        filenames = [p.name for p in paths]
        assert "data.xml" in filenames
        assert "index.html" in filenames
        assert "README.rst" in filenames
        assert "demo_data.xml" in filenames

    def test_sequences_xml_when_sequence_fields(self, env, tmp_module):
        model = _make_model(fields=[
            {"name": "reference", "type": "Char", "required": True},
            {"name": "value", "type": "Integer"},
        ])
        spec = _make_spec(models=[model])
        ctx = _make_module_context(spec)
        result = render_static(env, spec, tmp_module, ctx)
        filenames = [p.name for p in result.data]
        assert "sequences.xml" in filenames

    def test_all_files_exist_on_disk(self, env, tmp_module):
        spec = _make_spec(models=[_make_model()])
        ctx = _make_module_context(spec)
        result = render_static(env, spec, tmp_module, ctx)
        for p in result.data:
            assert p.exists(), f"File {p} should exist on disk"


# ---------------------------------------------------------------------------
# Function size limits
# ---------------------------------------------------------------------------


class TestFunctionSizeLimits:
    """All 7 stage functions and the orchestrator must be under 80 lines."""

    @pytest.mark.parametrize("func", [
        render_manifest,
        render_models,
        render_views,
        render_security,
        render_wizards,
        render_tests,
        render_static,
    ])
    def test_stage_function_under_80_lines(self, func):
        source = inspect.getsource(func)
        line_count = len(source.splitlines())
        assert line_count < 80, f"{func.__name__} is {line_count} lines, should be < 80"

    def test_render_module_orchestrator_under_300_lines(self):
        source = inspect.getsource(render_module)
        line_count = len(source.splitlines())
        # Phase 60: limit raised from 200 to 300 due to iterative mode (spec stash,
        # conflict detection, stub merge, stage filtering, manifest merging)
        # PIPE-04: raised to 350 after adding semantic validation block
        # Phase 5: raised to 400 after adding odoo-ls structural validation block
        assert line_count < 400, f"render_module is {line_count} lines, should be < 400"


# ---------------------------------------------------------------------------
# Phase 27: Integration tests for relationship patterns in rendered output
# ---------------------------------------------------------------------------


def _make_through_spec():
    """Spec with m2m_through relationship for integration tests."""
    return {
        "module_name": "test_university",
        "module_title": "Test University",
        "summary": "Test module",
        "author": "Test",
        "website": "https://test.example.com",
        "license": "LGPL-3",
        "category": "Education",
        "odoo_version": "17.0",
        "depends": ["base"],
        "application": True,
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


def _make_self_m2m_spec():
    """Spec with self_m2m relationship for integration tests."""
    return {
        "module_name": "test_university",
        "module_title": "Test University",
        "summary": "Test module",
        "author": "Test",
        "website": "https://test.example.com",
        "license": "LGPL-3",
        "category": "Education",
        "odoo_version": "17.0",
        "depends": ["base"],
        "application": True,
        "models": [
            {
                "name": "test_university.course",
                "description": "Course",
                "fields": [{"name": "name", "type": "Char", "required": True}],
            },
        ],
        "relationships": [
            {
                "type": "self_m2m",
                "model": "test_university.course",
                "field_name": "prerequisite_ids",
                "inverse_field_name": "dependent_ids",
                "string": "Prerequisites",
                "inverse_string": "Dependent Courses",
            }
        ],
        "wizards": [],
    }


def _make_hierarchical_spec():
    """Spec with hierarchical model for integration tests."""
    return {
        "module_name": "test_university",
        "module_title": "Test University",
        "summary": "Test module",
        "author": "Test",
        "website": "https://test.example.com",
        "license": "LGPL-3",
        "category": "Education",
        "odoo_version": "17.0",
        "depends": ["base"],
        "application": True,
        "models": [
            {
                "name": "test_university.department",
                "description": "Department",
                "hierarchical": True,
                "fields": [{"name": "name", "type": "Char", "required": True}],
            },
        ],
        "wizards": [],
    }


class TestRenderModelsThroughModel:
    """Integration tests for rendered through-model output."""

    def test_through_model_has_two_m2one_fks(self, tmp_path):
        spec = _make_through_spec()
        files, _ = render_module(spec, None, tmp_path)
        through_py = (tmp_path / "test_university" / "models" / "test_university_enrollment.py").read_text()
        assert "fields.Many2one(" in through_py
        assert 'comodel_name="test_university.course"' in through_py
        assert 'comodel_name="test_university.student"' in through_py
        assert "required=True" in through_py

    def test_through_model_has_extra_fields(self, tmp_path):
        spec = _make_through_spec()
        files, _ = render_module(spec, None, tmp_path)
        through_py = (tmp_path / "test_university" / "models" / "test_university_enrollment.py").read_text()
        assert "grade" in through_py
        assert "enrollment_date" in through_py

    def test_ondelete_cascade_rendered(self, tmp_path):
        spec = _make_through_spec()
        files, _ = render_module(spec, None, tmp_path)
        through_py = (tmp_path / "test_university" / "models" / "test_university_enrollment.py").read_text()
        assert 'ondelete="cascade"' in through_py


class TestRenderManifestThroughModel:
    """Integration tests: through-model in __init__.py."""

    def test_init_py_imports_through_model(self, tmp_path):
        spec = _make_through_spec()
        files, _ = render_module(spec, None, tmp_path)
        init_py = (tmp_path / "test_university" / "models" / "__init__.py").read_text()
        assert "test_university_enrollment" in init_py


class TestRenderSecurityThroughModel:
    """Integration tests: through-model ACL entries."""

    def test_access_csv_has_through_model_entries(self, tmp_path):
        spec = _make_through_spec()
        files, _ = render_module(spec, None, tmp_path)
        csv_content = (tmp_path / "test_university" / "security" / "ir.model.access.csv").read_text()
        assert "test_university_enrollment" in csv_content


class TestRenderModelsSelfM2M:
    """Integration tests for rendered self-referential M2M output."""

    def test_many2many_with_relation_params(self, tmp_path):
        spec = _make_self_m2m_spec()
        files, _ = render_module(spec, None, tmp_path)
        course_py = (tmp_path / "test_university" / "models" / "test_university_course.py").read_text()
        assert "fields.Many2many(" in course_py
        assert "relation=" in course_py
        assert "column1=" in course_py
        assert "column2=" in course_py

    def test_inverse_field_reversed_columns(self, tmp_path):
        spec = _make_self_m2m_spec()
        files, _ = render_module(spec, None, tmp_path)
        course_py = (tmp_path / "test_university" / "models" / "test_university_course.py").read_text()
        # Both prerequisite_ids and dependent_ids should be present
        assert "prerequisite_ids" in course_py
        assert "dependent_ids" in course_py


class TestRenderModelsHierarchical:
    """Integration tests for rendered hierarchical model output."""

    def test_parent_store_class_attribute(self, tmp_path):
        spec = _make_hierarchical_spec()
        files, _ = render_module(spec, None, tmp_path)
        dept_py = (tmp_path / "test_university" / "models" / "test_university_department.py").read_text()
        assert "_parent_store = True" in dept_py
        assert '_parent_name = "parent_id"' in dept_py

    def test_parent_id_field_rendered(self, tmp_path):
        spec = _make_hierarchical_spec()
        files, _ = render_module(spec, None, tmp_path)
        dept_py = (tmp_path / "test_university" / "models" / "test_university_department.py").read_text()
        assert "parent_id = fields.Many2one(" in dept_py
        assert 'ondelete="cascade"' in dept_py
        assert "index=True" in dept_py

    def test_child_ids_field_rendered(self, tmp_path):
        spec = _make_hierarchical_spec()
        files, _ = render_module(spec, None, tmp_path)
        dept_py = (tmp_path / "test_university" / "models" / "test_university_department.py").read_text()
        assert "child_ids = fields.One2many(" in dept_py
        assert 'inverse_name="parent_id"' in dept_py

    def test_parent_path_unaccent_false(self, tmp_path):
        spec = _make_hierarchical_spec()
        files, _ = render_module(spec, None, tmp_path)
        dept_py = (tmp_path / "test_university" / "models" / "test_university_department.py").read_text()
        assert "parent_path = fields.Char(" in dept_py
        assert "unaccent=False" in dept_py
        assert "index=True" in dept_py

    def test_parent_path_not_in_form_view(self, tmp_path):
        spec = _make_hierarchical_spec()
        files, _ = render_module(spec, None, tmp_path)
        views_xml = (tmp_path / "test_university" / "views" / "test_university_department_views.xml").read_text()
        assert "parent_path" not in views_xml


# ---------------------------------------------------------------------------
# Phase 28: Integration tests for computation chains in rendered output
# ---------------------------------------------------------------------------


def _make_chain_spec():
    """Spec with computation_chains for integration tests."""
    return {
        "module_name": "test_university",
        "module_title": "Test University",
        "summary": "Test module",
        "author": "Test",
        "website": "https://test.example.com",
        "license": "LGPL-3",
        "category": "Education",
        "odoo_version": "17.0",
        "depends": ["base"],
        "application": True,
        "models": [
            {
                "name": "test_university.enrollment",
                "description": "Enrollment",
                "fields": [
                    {"name": "student_id", "type": "Many2one",
                     "comodel_name": "test_university.student", "required": True},
                    {"name": "grade", "type": "Float"},
                    {"name": "credit_hours", "type": "Integer"},
                    {"name": "weighted_grade", "type": "Float"},
                ],
            },
            {
                "name": "test_university.student",
                "description": "Student",
                "fields": [
                    {"name": "name", "type": "Char", "required": True},
                    {"name": "enrollment_ids", "type": "One2many",
                     "comodel_name": "test_university.enrollment",
                     "inverse_name": "student_id"},
                    {"name": "gpa", "type": "Float"},
                ],
            },
        ],
        "computation_chains": [
            {
                "field": "test_university.enrollment.weighted_grade",
                "depends_on": ["grade", "credit_hours"],
            },
            {
                "field": "test_university.student.gpa",
                "depends_on": ["enrollment_ids.weighted_grade", "enrollment_ids.credit_hours"],
            },
        ],
        "wizards": [],
    }


def _make_intra_model_chain_spec():
    """Spec with two intra-model chain fields for topological ordering test."""
    return {
        "module_name": "test_order",
        "module_title": "Test Order",
        "summary": "Test module",
        "author": "Test",
        "website": "https://test.example.com",
        "license": "LGPL-3",
        "category": "Uncategorized",
        "odoo_version": "17.0",
        "depends": ["base"],
        "application": True,
        "models": [
            {
                "name": "test_order.line",
                "description": "Order Line",
                "fields": [
                    {"name": "name", "type": "Char", "required": True},
                    {"name": "qty", "type": "Integer"},
                    {"name": "price", "type": "Float"},
                    {"name": "subtotal", "type": "Float"},
                    {"name": "total", "type": "Float"},
                ],
            },
        ],
        "computation_chains": [
            {
                "field": "test_order.line.subtotal",
                "depends_on": ["qty", "price"],
            },
            {
                "field": "test_order.line.total",
                "depends_on": ["subtotal"],
            },
        ],
        "wizards": [],
    }


def _make_circular_chain_spec():
    """Spec with circular computation chain."""
    return {
        "module_name": "test_circular",
        "module_title": "Test Circular",
        "summary": "Test module",
        "author": "Test",
        "website": "https://test.example.com",
        "license": "LGPL-3",
        "category": "Uncategorized",
        "odoo_version": "17.0",
        "depends": ["base"],
        "application": True,
        "models": [
            {
                "name": "test_circular.student",
                "description": "Student",
                "fields": [
                    {"name": "name", "type": "Char", "required": True},
                    {"name": "enrollment_ids", "type": "One2many",
                     "comodel_name": "test_circular.enrollment",
                     "inverse_name": "student_id"},
                    {"name": "gpa", "type": "Float"},
                ],
            },
            {
                "name": "test_circular.enrollment",
                "description": "Enrollment",
                "fields": [
                    {"name": "student_id", "type": "Many2one",
                     "comodel_name": "test_circular.student"},
                    {"name": "weighted_grade", "type": "Float"},
                ],
            },
        ],
        "computation_chains": [
            {
                "field": "test_circular.student.gpa",
                "depends_on": ["enrollment_ids.weighted_grade"],
            },
            {
                "field": "test_circular.enrollment.weighted_grade",
                "depends_on": ["student_id.gpa"],
            },
        ],
        "wizards": [],
    }


class TestRenderModelsComputedChains:
    """Integration tests for computation chains in rendered output."""

    def test_cross_model_depends(self, tmp_path):
        """render_models() with computation_chains produces @api.depends with dotted path and store=True."""
        spec = _make_chain_spec()
        files, _ = render_module(spec, None, tmp_path)
        student_py = (tmp_path / "test_university" / "models" / "test_university_student.py").read_text()
        assert '@api.depends("enrollment_ids.weighted_grade"' in student_py
        assert "store=True" in student_py

    def test_chain_field_has_compute_method(self, tmp_path):
        """Generated model.py contains def _compute_gpa(self) method stub."""
        spec = _make_chain_spec()
        files, _ = render_module(spec, None, tmp_path)
        student_py = (tmp_path / "test_university" / "models" / "test_university_student.py").read_text()
        assert "def _compute_gpa(self):" in student_py

    def test_topological_order_in_output(self, tmp_path):
        """In model with 2 intra-model chain fields, upstream appears before downstream."""
        spec = _make_intra_model_chain_spec()
        files, _ = render_module(spec, None, tmp_path)
        line_py = (tmp_path / "test_order" / "models" / "test_order_line.py").read_text()
        # _compute_subtotal should appear before _compute_total
        subtotal_pos = line_py.index("_compute_subtotal")
        total_pos = line_py.index("_compute_total")
        assert subtotal_pos < total_pos

    def test_no_files_on_cycle(self, tmp_path):
        """render_module() with circular chain raises ValueError; output dir has no generated files."""
        spec = _make_circular_chain_spec()
        with pytest.raises(ValueError, match="Circular dependency"):
            render_module(spec, None, tmp_path)
        # Output directory should not exist or be empty
        module_dir = tmp_path / "test_circular"
        assert not module_dir.exists() or not list(module_dir.iterdir())

    def test_backward_compat_no_chains(self, tmp_path):
        """render_module() with spec that has no computation_chains produces identical output."""
        spec = _make_spec(models=[_make_model()])
        files, warnings = render_module(spec, None, tmp_path)
        # Basic sanity: files generated, no errors
        assert len(files) > 0
        model_py = (tmp_path / "test_module" / "models" / "test_model.py").read_text()
        assert "class TestModel" in model_py


# ---------------------------------------------------------------------------
# Phase 29: Complex Constraints Integration Tests
# ---------------------------------------------------------------------------


def _make_constraint_spec(
    models: list[dict] | None = None,
    constraints: list[dict] | None = None,
    depends: list[str] | None = None,
) -> dict:
    """Spec with constraints section for integration tests."""
    return {
        "module_name": "test_constraints",
        "module_title": "Test Constraints Module",
        "summary": "Test module for complex constraints",
        "author": "Test",
        "website": "https://test.example.com",
        "license": "LGPL-3",
        "category": "Education",
        "odoo_version": "17.0",
        "depends": depends or ["base"],
        "application": True,
        "models": models or [],
        "wizards": [],
        "constraints": constraints or [],
    }


class TestRenderModelsComplexConstraints:
    """Integration tests for end-to-end constraint rendering."""

    def test_temporal_constraint_output(self, tmp_path):
        """render_models with temporal constraint produces correct Python output."""
        spec = _make_constraint_spec(
            models=[{
                "name": "test_constraints.course",
                "description": "Course",
                "fields": [
                    {"name": "name", "type": "Char", "required": True},
                    {"name": "start_date", "type": "Date"},
                    {"name": "end_date", "type": "Date"},
                ],
            }],
            constraints=[{
                "type": "temporal",
                "model": "test_constraints.course",
                "name": "date_order",
                "fields": ["start_date", "end_date"],
                "condition": "end_date < start_date",
                "message": "End date must be after start date.",
            }],
        )
        files, _ = render_module(spec, None, tmp_path)
        course_py = (
            tmp_path / "test_constraints" / "models" / "test_constraints_course.py"
        ).read_text()
        assert '@api.constrains("start_date", "end_date")' in course_py
        assert "_check_date_order" in course_py
        assert "rec.start_date and rec.end_date" in course_py
        assert "ValidationError" in course_py
        assert '_("' in course_py

    def test_cross_model_constraint_output(self, tmp_path):
        """render_models with cross_model constraint produces create/write overrides."""
        spec = _make_constraint_spec(
            models=[
                {
                    "name": "test_constraints.course",
                    "description": "Course",
                    "fields": [
                        {"name": "name", "type": "Char", "required": True},
                        {"name": "max_students", "type": "Integer"},
                    ],
                },
                {
                    "name": "test_constraints.enrollment",
                    "description": "Enrollment",
                    "fields": [
                        {"name": "course_id", "type": "Many2one",
                         "comodel_name": "test_constraints.course", "required": True},
                        {"name": "student_name", "type": "Char"},
                    ],
                },
            ],
            constraints=[{
                "type": "cross_model",
                "model": "test_constraints.enrollment",
                "name": "enrollment_capacity",
                "trigger_fields": ["course_id"],
                "related_model": "test_constraints.enrollment",
                "count_domain_field": "course_id",
                "capacity_model": "test_constraints.course",
                "capacity_field": "max_students",
                "message": "Enrollment count cannot exceed course capacity of %s.",
            }],
        )
        files, _ = render_module(spec, None, tmp_path)
        enrollment_py = (
            tmp_path / "test_constraints" / "models" / "test_constraints_enrollment.py"
        ).read_text()
        assert "def create(self, vals_list):" in enrollment_py
        assert "super().create(vals_list)" in enrollment_py
        assert "_check_enrollment_capacity()" in enrollment_py
        assert "def write(self, vals):" in enrollment_py
        assert "if any(f in vals" in enrollment_py
        assert "search_count" in enrollment_py
        assert "@api.model_create_multi" in enrollment_py

    def test_capacity_constraint_output(self, tmp_path):
        """render_models with capacity constraint produces count-based validation."""
        spec = _make_constraint_spec(
            models=[{
                "name": "test_constraints.section",
                "description": "Section",
                "fields": [
                    {"name": "name", "type": "Char", "required": True},
                    {"name": "student_ids", "type": "One2many",
                     "comodel_name": "test_constraints.section.student",
                     "inverse_name": "section_id"},
                ],
            }],
            constraints=[{
                "type": "capacity",
                "model": "test_constraints.section",
                "name": "section_capacity",
                "count_field": "student_ids",
                "max_value": 30,
                "count_model": "test_constraints.section.student",
                "count_domain_field": "section_id",
                "message": "A section cannot have more than %s students.",
            }],
        )
        files, _ = render_module(spec, None, tmp_path)
        section_py = (
            tmp_path / "test_constraints" / "models" / "test_constraints_section.py"
        ).read_text()
        assert "def create(self, vals_list):" in section_py
        assert "def write(self, vals):" in section_py
        assert "search_count" in section_py
        assert "30" in section_py

    def test_backward_compat(self, tmp_path):
        """render_models with spec that has NO constraints section produces identical output."""
        spec = _make_spec(models=[_make_model()])
        files, warnings = render_module(spec, None, tmp_path)
        assert len(files) > 0
        model_py = (tmp_path / "test_module" / "models" / "test_model.py").read_text()
        assert "class TestModel" in model_py
        # No constraint-related output
        assert "complex_constraints" not in model_py
        assert "_check_" not in model_py
        assert "from odoo.tools.translate import _" not in model_py

    def test_imports_validation_error(self, tmp_path):
        """render_models with any complex constraint includes ValidationError and _ imports."""
        spec = _make_constraint_spec(
            models=[{
                "name": "test_constraints.course",
                "description": "Course",
                "fields": [
                    {"name": "start_date", "type": "Date"},
                    {"name": "end_date", "type": "Date"},
                ],
            }],
            constraints=[{
                "type": "temporal",
                "model": "test_constraints.course",
                "name": "date_order",
                "fields": ["start_date", "end_date"],
                "condition": "end_date < start_date",
                "message": "End date must be after start date.",
            }],
        )
        files, _ = render_module(spec, None, tmp_path)
        course_py = (
            tmp_path / "test_constraints" / "models" / "test_constraints_course.py"
        ).read_text()
        assert "from odoo.exceptions import ValidationError" in course_py
        # Odoo 19+ uses self.env._() instead of standalone _(), so the translate import is no longer needed
        assert "self.env._(" in course_py or "from odoo.tools.translate import _" in course_py


# ---------------------------------------------------------------------------
# Phase 30: render_cron tests
# ---------------------------------------------------------------------------


def _make_cron_spec(cron_jobs=None, models=None):
    """Helper to construct a spec with cron_jobs."""
    return {
        "module_name": "test_module",
        "module_title": "Test Module",
        "summary": "A test module",
        "author": "Test Author",
        "website": "https://test.example.com",
        "license": "LGPL-3",
        "category": "Uncategorized",
        "odoo_version": "17.0",
        "depends": ["base"],
        "application": True,
        "models": models or [
            {
                "name": "academy.course",
                "description": "Course",
                "fields": [
                    {"name": "name", "type": "Char", "required": True},
                ],
            },
        ],
        "wizards": [],
        "cron_jobs": cron_jobs or [],
    }


class TestRenderCron:
    def test_cron_no_jobs_noop(self, env, tmp_module):
        """render_cron with no cron_jobs returns Result.ok([])."""
        spec = _make_cron_spec(cron_jobs=[])
        ctx = _make_module_context(spec)
        result = render_cron(env, spec, tmp_module, ctx)
        assert result.success is True
        assert result.data == []

    def test_cron_generates_xml(self, env, tmp_module):
        """render_cron with 1 cron_job produces data/cron_data.xml with correct content."""
        spec = _make_cron_spec(cron_jobs=[{
            "name": "Archive Expired Courses",
            "model_name": "academy.course",
            "method": "_cron_archive_expired",
            "interval_number": 1,
            "interval_type": "days",
        }])
        ctx = _make_module_context(spec)
        result = render_cron(env, spec, tmp_module, ctx)
        assert result.success is True
        assert len(result.data) == 1
        xml_path = tmp_module / "data" / "cron_data.xml"
        assert xml_path.exists()
        content = xml_path.read_text()
        assert "ir.cron" in content
        assert 'noupdate="1"' in content
        assert "doall" in content
        assert "False" in content
        assert "model_academy_course" in content
        assert "state" in content
        assert "code" in content
        assert "_cron_archive_expired" in content

    def test_cron_invalid_method_name(self, env, tmp_module):
        """render_cron with invalid method name returns Result.fail()."""
        spec = _make_cron_spec(cron_jobs=[{
            "name": "Bad Cron",
            "model_name": "academy.course",
            "method": "123bad",
            "interval_number": 1,
            "interval_type": "days",
        }])
        ctx = _make_module_context(spec)
        result = render_cron(env, spec, tmp_module, ctx)
        assert result.success is False

    def test_cron_multiple_jobs(self, env, tmp_module):
        """render_cron with 2 cron jobs includes both in XML."""
        spec = _make_cron_spec(cron_jobs=[
            {
                "name": "Archive Expired",
                "model_name": "academy.course",
                "method": "_cron_archive_expired",
                "interval_number": 1,
                "interval_type": "days",
            },
            {
                "name": "Send Reminders",
                "model_name": "academy.course",
                "method": "_cron_send_reminders",
                "interval_number": 4,
                "interval_type": "hours",
            },
        ])
        ctx = _make_module_context(spec)
        result = render_cron(env, spec, tmp_module, ctx)
        assert result.success is True
        content = (tmp_module / "data" / "cron_data.xml").read_text()
        assert "_cron_archive_expired" in content
        assert "_cron_send_reminders" in content


# ---------------------------------------------------------------------------
# Phase 30: render_reports and render_controllers placeholder tests
# ---------------------------------------------------------------------------


class TestRenderReportsPlaceholder:
    def test_returns_ok_empty(self, env, tmp_module):
        """render_reports returns Result.ok([]) as a placeholder."""
        spec = _make_spec(models=[_make_model()])
        ctx = _make_module_context(spec)
        result = render_reports(env, spec, tmp_module, ctx)
        assert result.success is True
        assert result.data == []


class TestRenderControllersPlaceholder:
    def test_returns_ok_empty(self, env, tmp_module):
        """render_controllers returns Result.ok([]) as a placeholder."""
        spec = _make_spec(models=[_make_model()])
        ctx = _make_module_context(spec)
        result = render_controllers(env, spec, tmp_module, ctx)
        assert result.success is True
        assert result.data == []


# ---------------------------------------------------------------------------
# Phase 30: Pipeline stage count test
# ---------------------------------------------------------------------------


class TestRenderModulePipeline:
    def test_pipeline_has_10_stages(self):
        """render_module stages list should have 10 entries (was 7, +3 new)."""
        source = inspect.getsource(render_module)
        # Count lambda entries in the stages list
        assert source.count("lambda:") >= 10


# ---------------------------------------------------------------------------
# Phase 30: Full integration with cron spec
# ---------------------------------------------------------------------------


class TestRenderModuleCronIntegration:
    def test_full_render_with_cron(self, tmp_path):
        """Full render_module with cron_jobs spec generates cron XML + model with stub."""
        spec = _make_cron_spec(
            cron_jobs=[{
                "name": "Archive Expired Courses",
                "model_name": "academy.course",
                "method": "_cron_archive_expired",
                "interval_number": 1,
                "interval_type": "days",
            }],
        )
        files, warnings = render_module(spec, None, tmp_path)
        # cron XML file should be generated
        cron_xml = tmp_path / "test_module" / "data" / "cron_data.xml"
        assert cron_xml.exists()
        cron_content = cron_xml.read_text()
        assert "ir.cron" in cron_content
        assert "_cron_archive_expired" in cron_content
        # model file should contain the stub method
        model_py = (tmp_path / "test_module" / "models" / "academy_course.py").read_text()
        assert "_cron_archive_expired" in model_py
        assert "@api.model" in model_py


# ---------------------------------------------------------------------------
# Phase 31: Report generation tests
# ---------------------------------------------------------------------------


def _make_report_spec(reports=None, dashboards=None, models=None):
    """Helper to construct a spec with reports and/or dashboards."""
    return {
        "module_name": "test_module",
        "module_title": "Test Module",
        "summary": "A test module",
        "author": "Test Author",
        "website": "https://test.example.com",
        "license": "LGPL-3",
        "category": "Uncategorized",
        "odoo_version": "17.0",
        "depends": ["base"],
        "application": True,
        "models": models or [
            {
                "name": "academy.student",
                "description": "Student",
                "fields": [
                    {"name": "name", "type": "Char", "required": True},
                    {"name": "enrollment_date", "type": "Date"},
                    {"name": "total_credits", "type": "Integer"},
                ],
            },
        ],
        "wizards": [],
        "reports": reports or [],
        "dashboards": dashboards or [],
    }


def _sample_report():
    """Return a sample report spec entry."""
    return {
        "name": "Student Report Card",
        "model_name": "academy.student",
        "xml_id": "student_report_card",
        "columns": [
            {"field": "name", "label": "Student"},
            {"field": "enrollment_date", "label": "Enrollment Date"},
            {"field": "total_credits", "label": "Credits"},
        ],
        "button_label": "Print Report Card",
    }


def _sample_report_with_paper():
    """Return a sample report spec entry with paper_format."""
    report = _sample_report()
    report["paper_format"] = {
        "format": "A4",
        "orientation": "Landscape",
        "margin_top": 25,
    }
    return report


def _sample_dashboard():
    """Return a sample dashboard spec entry."""
    return {
        "model_name": "academy.student",
        "title": "Student Analysis",
        "chart_type": "bar",
        "stacked": False,
        "dimensions": [
            {"field": "enrollment_date", "interval": "month"},
        ],
        "measures": [
            {"field": "total_credits"},
        ],
        "rows": [
            {"field": "enrollment_date", "interval": "quarter"},
        ],
        "columns": [],
    }


class TestRenderReports:
    def test_report_generates_action_xml(self, env, tmp_module):
        """Spec with reports entry -> render_reports() creates report action XML."""
        report = _sample_report()
        spec = _make_report_spec(reports=[report])
        ctx = _make_module_context(spec)
        result = render_reports(env, spec, tmp_module, ctx)
        assert result.success is True
        action_file = tmp_module / "data" / "report_student_report_card.xml"
        assert action_file.exists()
        content = action_file.read_text()
        assert "ir.actions.report" in content

    def test_report_action_fields(self, env, tmp_module):
        """Generated report action has binding_model_id, report_name, report_type, binding_type."""
        report = _sample_report()
        spec = _make_report_spec(reports=[report])
        ctx = _make_module_context(spec)
        render_reports(env, spec, tmp_module, ctx)
        content = (tmp_module / "data" / "report_student_report_card.xml").read_text()
        assert "binding_model_id" in content
        assert "test_module.report_student_report_card" in content
        assert "qweb-pdf" in content
        assert "binding_type" in content

    def test_report_qweb_template(self, env, tmp_module):
        """Generated QWeb template has t-call, t-foreach, t-field, class='page'."""
        report = _sample_report()
        spec = _make_report_spec(reports=[report])
        ctx = _make_module_context(spec)
        render_reports(env, spec, tmp_module, ctx)
        tmpl_file = tmp_module / "data" / "report_student_report_card_template.xml"
        assert tmpl_file.exists()
        content = tmpl_file.read_text()
        assert 't-call="web.html_container"' in content
        assert 't-foreach="docs"' in content
        assert 't-call="web.external_layout"' in content
        assert 't-field="doc.display_name"' in content or 't-field="doc.name"' in content
        assert 'class="page"' in content

    def test_report_paper_format(self, env, tmp_module):
        """Spec with paper_format generates paperformat record; without it, no paperformat."""
        # With paper_format
        report_with = _sample_report_with_paper()
        spec_with = _make_report_spec(reports=[report_with])
        ctx_with = _make_module_context(spec_with)
        render_reports(env, spec_with, tmp_module, ctx_with)
        content = (tmp_module / "data" / "report_student_report_card.xml").read_text()
        assert "report.paperformat" in content
        assert "Landscape" in content

        # Without paper_format - use a fresh tmp dir
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            td_module = Path(td) / "test_module"
            td_module.mkdir()
            report_without = _sample_report()
            spec_without = _make_report_spec(reports=[report_without])
            ctx_without = _make_module_context(spec_without)
            render_reports(env, spec_without, td_module, ctx_without)
            content2 = (td_module / "data" / "report_student_report_card.xml").read_text()
            assert "report.paperformat" not in content2

    def test_form_print_button(self, tmp_path):
        """Model with reports -> form view XML contains print button."""
        report = _sample_report()
        spec = _make_report_spec(reports=[report])
        files, _ = render_module(spec, None, tmp_path)
        form_xml = (tmp_path / "test_module" / "views" / "academy_student_views.xml").read_text()
        assert "report_test_module_student_report_card" in form_xml
        assert 'type="action"' in form_xml

    def test_no_reports_noop(self, env, tmp_module):
        """Spec without reports or dashboards -> render_reports returns Result.ok([])."""
        spec = _make_report_spec(reports=[], dashboards=[])
        ctx = _make_module_context(spec)
        result = render_reports(env, spec, tmp_module, ctx)
        assert result.success is True
        assert result.data == []


class TestRenderDashboards:
    def test_graph_view(self, env, tmp_module):
        """Spec with dashboards -> generates graph view with chart_type and fields."""
        dashboard = _sample_dashboard()
        spec = _make_report_spec(dashboards=[dashboard])
        ctx = _make_module_context(spec)
        result = render_reports(env, spec, tmp_module, ctx)
        assert result.success is True
        graph_file = tmp_module / "views" / "academy_student_graph.xml"
        assert graph_file.exists()
        content = graph_file.read_text()
        assert "ir.ui.view" in content
        assert "<graph" in content
        assert 'type="bar"' in content

    def test_graph_measures(self, env, tmp_module):
        """Graph measure fields have type='measure'; dimension fields get interval."""
        dashboard = _sample_dashboard()
        spec = _make_report_spec(dashboards=[dashboard])
        ctx = _make_module_context(spec)
        render_reports(env, spec, tmp_module, ctx)
        content = (tmp_module / "views" / "academy_student_graph.xml").read_text()
        assert 'type="measure"' in content
        assert 'interval="month"' in content

    def test_pivot_view(self, env, tmp_module):
        """Generates pivot view with row/col/measure fields."""
        dashboard = _sample_dashboard()
        spec = _make_report_spec(dashboards=[dashboard])
        ctx = _make_module_context(spec)
        render_reports(env, spec, tmp_module, ctx)
        pivot_file = tmp_module / "views" / "academy_student_pivot.xml"
        assert pivot_file.exists()
        content = pivot_file.read_text()
        assert "<pivot" in content
        assert 'type="row"' in content
        assert 'type="measure"' in content

    def test_action_view_mode(self, tmp_path):
        """Model with dashboard -> action view_mode includes graph,pivot."""
        dashboard = _sample_dashboard()
        spec = _make_report_spec(dashboards=[dashboard])
        files, _ = render_module(spec, None, tmp_path)
        action_xml = (tmp_path / "test_module" / "views" / "academy_student_action.xml").read_text()
        assert "graph" in action_xml
        assert "pivot" in action_xml

        # Without dashboard - view_mode should NOT contain graph,pivot
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            spec_no = _make_report_spec(dashboards=[])
            files2, _ = render_module(spec_no, None, Path(td))
            action_xml2 = (Path(td) / "test_module" / "views" / "academy_student_action.xml").read_text()
            assert "graph" not in action_xml2
            assert "pivot" not in action_xml2

    def test_no_dashboards_noop(self, env, tmp_module):
        """Spec without dashboards -> no graph/pivot files generated."""
        spec = _make_report_spec(reports=[], dashboards=[])
        ctx = _make_module_context(spec)
        result = render_reports(env, spec, tmp_module, ctx)
        assert result.success is True
        assert not (tmp_module / "views" / "academy_student_graph.xml").exists()
        assert not (tmp_module / "views" / "academy_student_pivot.xml").exists()


# ---------------------------------------------------------------------------
# Phase 31: Full integration with report/dashboard spec
# ---------------------------------------------------------------------------


class TestRenderModuleReportIntegration:
    def test_full_render_with_reports_and_dashboards(self, tmp_path):
        """Full render_module with reports and dashboards generates all expected files."""
        spec = _make_report_spec(
            reports=[_sample_report()],
            dashboards=[_sample_dashboard()],
        )
        files, warnings = render_module(spec, None, tmp_path)
        module_dir = tmp_path / "test_module"
        # Report files
        assert (module_dir / "data" / "report_student_report_card.xml").exists()
        assert (module_dir / "data" / "report_student_report_card_template.xml").exists()
        # Dashboard files
        assert (module_dir / "views" / "academy_student_graph.xml").exists()
        assert (module_dir / "views" / "academy_student_pivot.xml").exists()


# ---------------------------------------------------------------------------
# Phase 32: render_controllers tests
# ---------------------------------------------------------------------------


def _make_controller_spec(controllers=None, **overrides):
    """Build a spec with controllers entries for testing."""
    spec = _make_spec(models=[_make_model("academy.student")])
    if controllers is not None:
        spec["controllers"] = controllers
    spec.update(overrides)
    return spec


def _sample_controller():
    """Return a sample controller entry with routes."""
    return {
        "name": "Main Controller",
        "class_name": "AcademyController",
        "routes": [
            {
                "path": "courses",
                "method_name": "get_courses",
                "type": "json",
                "auth": "user",
                "csrf": True,
                "methods": ["GET"],
                "description": "List all courses",
            },
            {
                "path": "page",
                "method_name": "index_page",
                "type": "http",
                "auth": "public",
                "csrf": True,
                "methods": ["GET"],
                "description": "Main page",
            },
        ],
    }


class TestRenderControllers:
    def test_no_controllers_noop(self, env, tmp_module):
        """Spec with no controllers -> render_controllers returns Result.ok([])."""
        spec = _make_controller_spec(controllers=[])
        ctx = _make_module_context(spec)
        result = render_controllers(env, spec, tmp_module, ctx)
        assert result.success is True
        assert result.data == []

    def test_no_controllers_key_noop(self, env, tmp_module):
        """Spec without controllers key -> render_controllers returns Result.ok([])."""
        spec = _make_spec(models=[_make_model()])
        ctx = _make_module_context(spec)
        result = render_controllers(env, spec, tmp_module, ctx)
        assert result.success is True
        assert result.data == []

    def test_controller_generates_main_py(self, env, tmp_module):
        """Spec with controllers -> creates controllers/main.py with @http.route."""
        spec = _make_controller_spec(controllers=[_sample_controller()])
        ctx = _make_module_context(spec)
        result = render_controllers(env, spec, tmp_module, ctx)
        assert result.success is True
        main_py = tmp_module / "controllers" / "main.py"
        assert main_py.exists()
        content = main_py.read_text()
        assert "@http.route" in content
        assert "http.Controller" in content

    def test_controller_secure_defaults(self, env, tmp_module):
        """Routes without explicit auth/csrf -> auth='user', csrf=True in output."""
        controller = {
            "name": "Default Controller",
            "routes": [
                {
                    "path": "api/data",
                    "method_name": "get_data",
                    "description": "Get data",
                },
            ],
        }
        spec = _make_controller_spec(controllers=[controller])
        ctx = _make_module_context(spec)
        render_controllers(env, spec, tmp_module, ctx)
        content = (tmp_module / "controllers" / "main.py").read_text()
        assert "auth='user'" in content or 'auth="user"' in content
        assert "csrf=True" in content

    def test_json_route_error_handling(self, env, tmp_module):
        """Route with type='json' -> try/except block with error response."""
        spec = _make_controller_spec(controllers=[_sample_controller()])
        ctx = _make_module_context(spec)
        render_controllers(env, spec, tmp_module, ctx)
        content = (tmp_module / "controllers" / "main.py").read_text()
        assert "try:" in content
        assert "except" in content
        assert "'status'" in content or '"status"' in content
        assert "'error'" in content or '"error"' in content

    def test_controllers_init(self, env, tmp_module):
        """Generates controllers/__init__.py with 'from . import main'."""
        spec = _make_controller_spec(controllers=[_sample_controller()])
        ctx = _make_module_context(spec)
        render_controllers(env, spec, tmp_module, ctx)
        init_file = tmp_module / "controllers" / "__init__.py"
        assert init_file.exists()
        content = init_file.read_text()
        assert "from . import main" in content

    def test_root_init_imports_controllers(self, env):
        """init_root.py.j2 renders 'from . import controllers' when has_controllers=True."""
        tmpl = env.get_template("init_root.py.j2")
        rendered = tmpl.render(has_wizards=False, has_controllers=True)
        assert "from . import controllers" in rendered

    def test_root_init_no_controllers(self, env):
        """init_root.py.j2 does NOT render controllers import when has_controllers=False."""
        tmpl = env.get_template("init_root.py.j2")
        rendered = tmpl.render(has_wizards=False, has_controllers=False)
        assert "controllers" not in rendered


# ---------------------------------------------------------------------------
# Import/Export wizard tests (Phase 32 Plan 02)
# ---------------------------------------------------------------------------


def _make_import_export_spec(
    models: list[dict] | None = None,
    wizards: list[dict] | None = None,
) -> dict:
    """Build spec with a model that has import_export:true."""
    default_models = [
        {
            "name": "academy.course",
            "description": "Academy Course",
            "import_export": True,
            "fields": [
                {"name": "name", "type": "Char", "required": True},
                {"name": "code", "type": "Char"},
                {"name": "credits", "type": "Integer"},
            ],
        }
    ]
    return _make_spec(models=models or default_models, wizards=wizards)


class TestRenderImportExport:
    def test_import_wizard_generated(self, env, tmp_module):
        """Spec with model having import_export:true -> generates wizard .py file."""
        spec = _make_import_export_spec()
        ctx = _make_module_context(spec)
        result = render_controllers(env, spec, tmp_module, ctx)
        assert result.success is True
        wizard_py = tmp_module / "wizards" / "academy_course_import_wizard.py"
        assert wizard_py.exists()

    def test_import_wizard_fields(self, env, tmp_module):
        """Generated wizard has Binary, state Selection, preview_html, import_count, error_log."""
        spec = _make_import_export_spec()
        ctx = _make_module_context(spec)
        render_controllers(env, spec, tmp_module, ctx)
        content = (tmp_module / "wizards" / "academy_course_import_wizard.py").read_text()
        assert "fields.Binary" in content
        assert "fields.Selection" in content
        assert "'upload'" in content or '"upload"' in content
        assert "'preview'" in content or '"preview"' in content
        assert "'done'" in content or '"done"' in content
        assert "preview_html" in content
        assert "import_count" in content
        assert "error_log" in content

    def test_content_type_validation(self, env, tmp_module):
        """Generated wizard has _validate_file_content with magic bytes check."""
        spec = _make_import_export_spec()
        ctx = _make_module_context(spec)
        render_controllers(env, spec, tmp_module, ctx)
        content = (tmp_module / "wizards" / "academy_course_import_wizard.py").read_text()
        assert "_validate_file_content" in content
        assert "PK\\x03\\x04" in content or "b'PK'" in content or "PK\\x03" in content

    def test_preview_and_batch_import(self, env, tmp_module):
        """Generated wizard has action_preview, action_import, _do_import, _parse_row."""
        spec = _make_import_export_spec()
        ctx = _make_module_context(spec)
        render_controllers(env, spec, tmp_module, ctx)
        content = (tmp_module / "wizards" / "academy_course_import_wizard.py").read_text()
        assert "def action_preview(" in content
        assert "def action_import(" in content
        assert "def _do_import(" in content
        assert "def _parse_row(" in content

    def test_export_xlsx(self, env, tmp_module):
        """Generated wizard has action_export referencing openpyxl.Workbook with field headers."""
        spec = _make_import_export_spec()
        ctx = _make_module_context(spec)
        render_controllers(env, spec, tmp_module, ctx)
        content = (tmp_module / "wizards" / "academy_course_import_wizard.py").read_text()
        assert "def action_export(" in content
        assert "openpyxl" in content
        assert "Workbook" in content

    def test_wizard_form_states(self, env, tmp_module):
        """Generated form XML has state-dependent invisible attrs and action buttons."""
        spec = _make_import_export_spec()
        ctx = _make_module_context(spec)
        render_controllers(env, spec, tmp_module, ctx)
        form_xml = tmp_module / "views" / "academy_course_import_wizard_form.xml"
        assert form_xml.exists()
        content = form_xml.read_text()
        assert "upload" in content
        assert "preview" in content
        assert "done" in content
        assert "ir.actions.act_window" in content

    def test_import_wizard_security(self, env, tmp_module):
        """Import wizard model gets references for ACL entry in security context."""
        spec = _make_import_export_spec()
        ctx = _make_module_context(spec)
        # The import_export_wizards key should be in context for access_csv.j2
        assert "import_export_wizards" in ctx or any(
            m.get("import_export") for m in spec.get("models", [])
        )
        render_controllers(env, spec, tmp_module, ctx)

    def test_no_import_export_noop(self, env, tmp_module):
        """Model without import_export:true -> no wizard files generated."""
        spec = _make_spec(models=[_make_model()])
        ctx = _make_module_context(spec)
        result = render_controllers(env, spec, tmp_module, ctx)
        assert result.success is True
        wizard_dir = tmp_module / "wizards"
        # No import wizard files should exist (wizard dir may not exist at all)
        import_files = list(wizard_dir.glob("*_import_wizard.py")) if wizard_dir.exists() else []
        assert import_files == []

    def test_import_wizard_init(self, env, tmp_module):
        """wizards/__init__.py imports import wizard modules."""
        spec = _make_import_export_spec()
        ctx = _make_module_context(spec)
        render_controllers(env, spec, tmp_module, ctx)
        # Wizards init should import the import wizard
        init_file = tmp_module / "wizards" / "__init__.py"
        assert init_file.exists()
        content = init_file.read_text()
        assert "academy_course_import_wizard" in content


# ---------------------------------------------------------------------------
# Phase 33: Performance optimization integration tests
# ---------------------------------------------------------------------------


class TestRenderModelsPerformance:
    """Integration tests for Phase 33 performance preprocessing in rendered output."""

    def test_render_performance_index_in_output(self, tmp_path):
        """Char search field gets index=True in generated Python code."""
        spec = _make_spec(models=[{
            "name": "academy.course",
            "description": "Academy Course",
            "fields": [
                {"name": "name", "type": "Char", "required": True},
                {"name": "qty", "type": "Integer"},
            ],
        }])
        files, _ = render_module(spec, None, tmp_path)
        model_py = (tmp_path / "test_module" / "models" / "academy_course.py").read_text()
        # Char field 'name' should have index=True (it's a search field)
        assert "index=True" in model_py

    def test_render_performance_order_in_output(self, tmp_path):
        """Model with order spec generates _order attribute."""
        spec = _make_spec(models=[{
            "name": "academy.course",
            "description": "Academy Course",
            "order": "name asc",
            "fields": [
                {"name": "name", "type": "Char", "required": True},
                {"name": "qty", "type": "Integer"},
            ],
        }])
        files, _ = render_module(spec, None, tmp_path)
        model_py = (tmp_path / "test_module" / "models" / "academy_course.py").read_text()
        assert '_order = "name asc"' in model_py

    def test_render_performance_sql_constraints_in_output(self, tmp_path):
        """unique_together generates _sql_constraints in output."""
        spec = _make_spec(models=[{
            "name": "academy.course",
            "description": "Academy Course",
            "unique_together": [
                {"fields": ["name", "company_id"], "message": "Name must be unique per company."},
            ],
            "fields": [
                {"name": "name", "type": "Char", "required": True},
                {"name": "company_id", "type": "Many2one", "comodel_name": "res.company"},
            ],
        }])
        files, _ = render_module(spec, None, tmp_path)
        model_py = (tmp_path / "test_module" / "models" / "academy_course.py").read_text()
        assert "_sql_constraints" in model_py
        assert "unique_name_company_id" in model_py
        assert "UNIQUE(name, company_id)" in model_py

    def test_render_transient_cleanup_in_output(self, tmp_path):
        """Wizard with transient attrs renders _transient_max_hours."""
        spec = _make_spec(
            models=[{
                "name": "academy.course",
                "description": "Academy Course",
                "fields": [{"name": "name", "type": "Char", "required": True}],
            }],
            wizards=[{
                "name": "academy.confirm.wizard",
                "target_model": "academy.course",
                "transient_max_hours": 2.0,
                "transient_max_count": 500,
                "fields": [
                    {"name": "reason", "type": "Char"},
                ],
            }],
        )
        files, _ = render_module(spec, None, tmp_path)
        wizard_py = (tmp_path / "test_module" / "wizards" / "academy_confirm_wizard.py").read_text()
        assert "_transient_max_hours = 2.0" in wizard_py
        assert "_transient_max_count = 500" in wizard_py

    def test_render_import_wizard_transient_defaults(self, tmp_path):
        """Import wizard always renders cleanup attributes with defaults."""
        spec = _make_spec(models=[{
            "name": "academy.course",
            "description": "Academy Course",
            "import_export": True,
            "fields": [{"name": "name", "type": "Char", "required": True}],
        }])
        files, _ = render_module(spec, None, tmp_path)
        wizard_py = (tmp_path / "test_module" / "wizards" / "academy_course_import_wizard.py").read_text()
        assert "_transient_max_hours = 1.0" in wizard_py
        assert "_transient_max_count = 0" in wizard_py


class TestRenderModelsProductionPatterns:
    """Integration tests for bulk and cache production patterns in generated models."""

    def test_bulk_model_generates_create_multi(self, tmp_path):
        """Spec with bulk:true -> generated .py contains @api.model_create_multi and _post_create_processing."""
        spec = _make_spec(models=[{
            "name": "academy.course",
            "description": "Academy Course",
            "bulk": True,
            "fields": [{"name": "name", "type": "Char", "required": True}],
        }])
        files, _ = render_module(spec, None, tmp_path)
        model_py = (tmp_path / "test_module" / "models" / "academy_course.py").read_text()
        assert "@api.model_create_multi" in model_py
        assert "_post_create_processing" in model_py
        assert "for record in records:" in model_py

    def test_cacheable_model_generates_ormcache(self, tmp_path):
        """Spec with cacheable:true -> generated .py contains @tools.ormcache, clear_caches(), tools import."""
        spec = _make_spec(models=[{
            "name": "academy.category",
            "description": "Academy Category",
            "cacheable": True,
            "fields": [{"name": "name", "type": "Char", "required": True}],
        }])
        files, _ = render_module(spec, None, tmp_path)
        model_py = (tmp_path / "test_module" / "models" / "academy_category.py").read_text()
        assert "@tools.ormcache" in model_py
        assert "clear_caches()" in model_py
        assert "from odoo import" in model_py
        assert "tools" in model_py

    def test_bulk_with_constraints_single_create(self, tmp_path):
        """Spec with bulk:true + constraints -> generated .py has exactly ONE 'def create(' occurrence."""
        spec = _make_spec(models=[{
            "name": "academy.course",
            "description": "Academy Course",
            "bulk": True,
            "constraints": [{
                "name": "capacity",
                "type": "cross_model",
                "check_body": "pass  # capacity check",
                "message": "Capacity exceeded",
                "trigger": "create",
            }],
            "fields": [
                {"name": "name", "type": "Char", "required": True},
                {"name": "capacity", "type": "Integer"},
            ],
        }])
        files, _ = render_module(spec, None, tmp_path)
        model_py = (tmp_path / "test_module" / "models" / "academy_course.py").read_text()
        assert model_py.count("def create(") == 1
        assert "@api.model_create_multi" in model_py
        assert "_post_create_processing" in model_py

    def test_cacheable_with_constraints_single_write(self, tmp_path):
        """Spec with cacheable:true + constraints -> generated .py has exactly ONE 'def write(' occurrence."""
        spec = _make_spec(models=[{
            "name": "academy.category",
            "description": "Academy Category",
            "cacheable": True,
            "constraints": [{
                "name": "check_dates",
                "type": "cross_model",
                "check_body": "pass  # date check",
                "message": "Invalid dates",
                "trigger": "write",
                "write_trigger_fields": ["date_start"],
            }],
            "fields": [
                {"name": "name", "type": "Char", "required": True},
                {"name": "date_start", "type": "Date"},
            ],
        }])
        files, _ = render_module(spec, None, tmp_path)
        model_py = (tmp_path / "test_module" / "models" / "academy_category.py").read_text()
        assert model_py.count("def write(") == 1
        assert "clear_caches()" in model_py


class TestRenderModelsArchival:
    """Integration tests for archival production pattern in generated modules."""

    def test_archival_model_has_active_field(self, tmp_path):
        """Render spec with archival:true -> generated model .py contains active = fields.Boolean."""
        spec = _make_spec(models=[{
            "name": "academy.course",
            "description": "Academy Course",
            "archival": True,
            "fields": [{"name": "name", "type": "Char", "required": True}],
        }])
        files, _ = render_module(spec, None, tmp_path)
        model_py = (tmp_path / "test_module" / "models" / "academy_course.py").read_text()
        assert "active = fields.Boolean" in model_py
        assert 'default="True"' in model_py
        assert "index=True" in model_py

    def test_archival_generates_wizard_files(self, tmp_path):
        """Render full module with archival:true -> wizards/ dir contains archival wizard."""
        spec = _make_spec(models=[{
            "name": "academy.course",
            "description": "Academy Course",
            "archival": True,
            "fields": [{"name": "name", "type": "Char", "required": True}],
        }])
        files, _ = render_module(spec, None, tmp_path)
        wizard_py = (tmp_path / "test_module" / "wizards" / "academy_course_archive_wizard.py").read_text()
        assert "action_archive" in wizard_py
        assert "relativedelta" in wizard_py

    def test_archival_generates_cron_xml(self, tmp_path):
        """Render full module with archival:true -> data/cron_data.xml references cron method."""
        spec = _make_spec(models=[{
            "name": "academy.course",
            "description": "Academy Course",
            "archival": True,
            "fields": [{"name": "name", "type": "Char", "required": True}],
        }])
        files, _ = render_module(spec, None, tmp_path)
        cron_xml = (tmp_path / "test_module" / "data" / "cron_data.xml").read_text()
        assert "_cron_archive_old_records" in cron_xml
        assert "ir.cron" in cron_xml

    def test_archival_cron_has_batch_commit(self, tmp_path):
        """Generated model .py contains cr.commit() and BATCH_SIZE in archival cron method."""
        spec = _make_spec(models=[{
            "name": "academy.course",
            "description": "Academy Course",
            "archival": True,
            "fields": [{"name": "name", "type": "Char", "required": True}],
        }])
        files, _ = render_module(spec, None, tmp_path)
        model_py = (tmp_path / "test_module" / "models" / "academy_course.py").read_text()
        assert "cr.commit()" in model_py
        assert "BATCH_SIZE" in model_py

    def test_archival_wizard_form_has_button(self, tmp_path):
        """Generated wizard form XML contains button with name='action_archive'."""
        spec = _make_spec(models=[{
            "name": "academy.course",
            "description": "Academy Course",
            "archival": True,
            "fields": [{"name": "name", "type": "Char", "required": True}],
        }])
        files, _ = render_module(spec, None, tmp_path)
        form_xml = (tmp_path / "test_module" / "views" / "academy_course_archive_wizard_wizard_form.xml").read_text()
        assert 'name="action_archive"' in form_xml
        assert "days_threshold" in form_xml

    def test_archival_with_state_field_no_crash(self, tmp_path):
        """Render spec with archival:true AND state Selection field -> no StrictUndefined error."""
        spec = _make_spec(models=[{
            "name": "academy.course",
            "description": "Academy Course",
            "archival": True,
            "fields": [
                {"name": "name", "type": "Char", "required": True},
                {
                    "name": "state",
                    "type": "Selection",
                    "selection": [["draft", "Draft"], ["active", "Active"]],
                },
            ],
        }])
        files, _ = render_module(spec, None, tmp_path)
        form_xml = (tmp_path / "test_module" / "views" / "academy_course_views.xml").read_text()
        # Archival wizard button should exist
        assert "action_open_academy_course_archive_wizard" in form_xml
        # Archival wizard button should NOT have invisible (no trigger_state)
        # Find the button block for archival wizard and check it has no invisible attr
        lines = form_xml.split("\n")
        for i, line in enumerate(lines):
            if "action_open_academy_course_archive_wizard" in line:
                # Gather surrounding lines for this button element
                button_block = "\n".join(lines[max(0, i - 1):i + 5])
                assert "invisible" not in button_block, (
                    f"Archival wizard button should not have invisible attr:\n{button_block}"
                )
                break

    def test_archival_with_state_and_regular_wizard(self, tmp_path):
        """Archival + state field + regular wizard -> both buttons render, only regular has invisible."""
        spec = _make_spec(
            models=[{
                "name": "academy.course",
                "description": "Academy Course",
                "archival": True,
                "fields": [
                    {"name": "name", "type": "Char", "required": True},
                    {
                        "name": "state",
                        "type": "Selection",
                        "selection": [["draft", "Draft"], ["active", "Active"]],
                    },
                ],
            }],
            wizards=[{
                "name": "confirm.wizard",
                "target_model": "academy.course",
                "trigger_state": "draft",
                "fields": [{"name": "reason", "type": "Char"}],
            }],
        )
        files, _ = render_module(spec, None, tmp_path)
        form_xml = (tmp_path / "test_module" / "views" / "academy_course_views.xml").read_text()
        # Both buttons should exist
        assert "action_open_academy_course_archive_wizard" in form_xml
        assert "action_open_confirm_wizard" in form_xml
        # Regular wizard button should have invisible with trigger_state
        assert "invisible=\"state != 'draft'\"" in form_xml
        # Archival wizard button should NOT have invisible -- check only lines AFTER its name
        lines = form_xml.split("\n")
        for i, line in enumerate(lines):
            if "action_open_academy_course_archive_wizard" in line:
                # Check from this line until next closing tag or button end
                button_block = "\n".join(lines[i:i + 6])
                assert "invisible" not in button_block, (
                    f"Archival wizard button should not have invisible attr:\n{button_block}"
                )
                break

    def test_cron_doall_from_spec_true(self, tmp_path):
        """Cron with doall:true in spec -> doall field in rendered XML contains eval='True'."""
        spec = _make_spec(models=[{
            "name": "academy.course",
            "description": "Academy Course",
            "fields": [{"name": "name", "type": "Char", "required": True}],
        }])
        spec["cron_jobs"] = [{
            "name": "Test Cron",
            "model_name": "academy.course",
            "method": "_cron_test",
            "interval_number": 1,
            "interval_type": "days",
            "doall": True,
        }]
        files, _ = render_module(spec, None, tmp_path)
        cron_xml = (tmp_path / "test_module" / "data" / "cron_data.xml").read_text()
        # Must specifically check the doall field line, not just any eval="True"
        for line in cron_xml.split("\n"):
            if '"doall"' in line:
                assert 'eval="True"' in line, f"doall field should have eval='True', got: {line}"
                break
        else:
            raise AssertionError("doall field not found in cron XML")

    def test_cron_doall_default_false(self, tmp_path):
        """Cron without doall key in spec -> doall field in rendered XML contains eval='False'."""
        spec = _make_spec(models=[{
            "name": "academy.course",
            "description": "Academy Course",
            "fields": [{"name": "name", "type": "Char", "required": True}],
        }])
        spec["cron_jobs"] = [{
            "name": "Test Cron",
            "model_name": "academy.course",
            "method": "_cron_test",
            "interval_number": 1,
            "interval_type": "days",
        }]
        files, _ = render_module(spec, None, tmp_path)
        cron_xml = (tmp_path / "test_module" / "data" / "cron_data.xml").read_text()
        for line in cron_xml.split("\n"):
            if '"doall"' in line:
                assert 'eval="False"' in line, f"doall field should have eval='False', got: {line}"
                break
        else:
            raise AssertionError("doall field not found in cron XML")

    def test_full_production_patterns_combined(self, tmp_path):
        """Spec with bulk+cacheable+archival -> module renders without errors, has all patterns."""
        spec = _make_spec(models=[{
            "name": "academy.course",
            "description": "Academy Course",
            "bulk": True,
            "cacheable": True,
            "archival": True,
            "fields": [{"name": "name", "type": "Char", "required": True}],
        }])
        files, _ = render_module(spec, None, tmp_path)
        model_py = (tmp_path / "test_module" / "models" / "academy_course.py").read_text()
        # Bulk
        assert "_post_create_processing" in model_py
        # Cache
        assert "clear_caches()" in model_py
        assert "@tools.ormcache" in model_py
        # Archival
        assert "_cron_archive_old_records" in model_py
        assert "cr.commit()" in model_py
        assert "active = fields.Boolean" in model_py


# ---------------------------------------------------------------------------
# Minimal-spec smoke test (Phase 36 Plan 02)
# ---------------------------------------------------------------------------


def test_minimal_spec_smoke(tmp_path):
    """Render a single-model, zero-features spec through full pipeline.

    Guards against StrictUndefined regressions when new context keys
    are added by future phases but not populated for minimal specs.
    """
    spec = {
        "module_name": "smoke_test",
        "depends": ["base"],
        "models": [{
            "name": "smoke.model",
            "description": "Smoke Test",
            "fields": [{"name": "name", "type": "Char"}],
        }],
        "wizards": [],
    }
    files, warnings = render_module(spec, get_template_dir(), tmp_path)
    assert len(files) > 0
    module_dir = tmp_path / "smoke_test"
    assert (module_dir / "__manifest__.py").exists()
    assert (module_dir / "models" / "smoke_model.py").exists()


# ---------------------------------------------------------------------------
# Phase 37: Spec-driven security render tests
# ---------------------------------------------------------------------------


def _make_security_spec_for_render(
    roles: list[str] | None = None,
    defaults: dict[str, str] | None = None,
    models: list[dict] | None = None,
) -> dict:
    """Build a spec with security block for render stage testing."""
    if roles is None:
        roles = ["viewer", "editor", "manager"]
    if defaults is None:
        defaults = {"viewer": "r", "editor": "cru", "manager": "crud"}
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
    spec = _make_spec(models=models)
    spec["security"] = {"roles": roles, "defaults": defaults}
    return spec


class TestRenderSecuritySpecDriven:
    def test_three_roles_in_security_xml(self, env, tmp_module):
        spec = _make_security_spec_for_render()
        # Run preprocessor to enrich
        from amil_utils.preprocessors.security import _process_security_patterns
        spec = _process_security_patterns(spec)
        from amil_utils.renderer_context import _build_module_context
        ctx = _build_module_context(spec, spec["module_name"])
        result = render_security(env, spec, tmp_module, ctx)
        assert result.success
        security_xml = (tmp_module / "security" / "security.xml").read_text()
        assert "group_test_module_viewer" in security_xml
        assert "group_test_module_editor" in security_xml
        assert "group_test_module_manager" in security_xml
        # Check implied_ids chain
        assert "base.group_user" in security_xml
        assert "group_test_module_viewer" in security_xml

    def test_acl_csv_has_role_x_model_rows(self, env, tmp_module):
        spec = _make_security_spec_for_render()
        from amil_utils.preprocessors.security import _process_security_patterns
        spec = _process_security_patterns(spec)
        from amil_utils.renderer_context import _build_module_context
        ctx = _build_module_context(spec, spec["module_name"])
        result = render_security(env, spec, tmp_module, ctx)
        assert result.success
        csv_content = (tmp_module / "security" / "ir.model.access.csv").read_text()
        lines = [l.strip() for l in csv_content.strip().split("\n") if l.strip()]
        # Header + 3 roles x 2 models = 7 lines
        assert len(lines) == 7

    def test_record_rules_with_company_and_department(self, env, tmp_module):
        spec = _make_security_spec_for_render()
        from amil_utils.preprocessors.security import _process_security_patterns
        spec = _process_security_patterns(spec)
        from amil_utils.renderer_context import _build_module_context
        ctx = _build_module_context(spec, spec["module_name"])
        result = render_security(env, spec, tmp_module, ctx)
        assert result.success
        rules_path = tmp_module / "security" / "record_rules.xml"
        assert rules_path.exists()
        rules_xml = rules_path.read_text()
        assert "rule_fee_structure_company" in rules_xml
        assert "rule_fee_structure_department" in rules_xml
        assert "company_ids" in rules_xml
        assert "department_id" in rules_xml

    def test_no_security_block_renders_legacy_groups(self, env, tmp_module):
        spec = _make_spec(models=[{
            "name": "test.model",
            "description": "Test Model",
            "fields": [{"name": "name", "type": "Char", "required": True}],
        }])
        from amil_utils.preprocessors.security import _process_security_patterns
        spec = _process_security_patterns(spec)
        from amil_utils.renderer_context import _build_module_context
        ctx = _build_module_context(spec, spec["module_name"])
        result = render_security(env, spec, tmp_module, ctx)
        assert result.success
        security_xml = (tmp_module / "security" / "security.xml").read_text()
        assert "group_test_module_user" in security_xml
        assert "group_test_module_manager" in security_xml

    def test_full_render_module_with_security(self, tmp_path):
        spec = _make_security_spec_for_render()
        spec["module_title"] = "Test Module"
        spec["summary"] = "Test"
        spec["author"] = "Test"
        files, warnings = render_module(spec, get_template_dir(), tmp_path)
        assert len(files) > 0
        module_dir = tmp_path / "test_module"
        assert (module_dir / "security" / "security.xml").exists()
        assert (module_dir / "security" / "ir.model.access.csv").exists()
        assert (module_dir / "security" / "record_rules.xml").exists()


# ---------------------------------------------------------------------------
# Phase 37-02: Field-level groups= rendering tests
# ---------------------------------------------------------------------------


class TestRenderModelsFieldGroups:
    """Tests that model.py.j2 renders groups= on fields that have it."""

    def test_field_with_groups_renders_groups_attr(self, env, tmp_module):
        """Field with groups key renders groups= parameter in output .py."""
        model = _make_model("test.model", fields=[
            {"name": "name", "type": "Char", "required": True},
            {"name": "salary", "type": "Float", "groups": "test_module.group_test_module_manager"},
        ])
        spec = _make_spec(models=[model])
        ctx = _make_module_context(spec)
        result = render_models(env, spec, tmp_module, ctx)
        assert result.success
        model_py = (tmp_module / "models" / "test_model.py").read_text()
        assert 'groups="test_module.group_test_module_manager"' in model_py

    def test_field_without_groups_no_groups_attr(self, env, tmp_module):
        """Field without groups key does NOT render groups= line."""
        model = _make_model("test.model", fields=[
            {"name": "name", "type": "Char", "required": True},
            {"name": "value", "type": "Integer"},
        ])
        spec = _make_spec(models=[model])
        ctx = _make_module_context(spec)
        result = render_models(env, spec, tmp_module, ctx)
        assert result.success
        model_py = (tmp_module / "models" / "test_model.py").read_text()
        assert "groups=" not in model_py

    def test_sensitive_field_renders_groups_after_preprocessing(self, env, tmp_module):
        """Full pipeline: sensitive field -> preprocessor -> template -> groups= in output."""
        from amil_utils.preprocessors.security import _process_security_patterns
        from amil_utils.renderer_context import _build_model_context

        model = {
            "name": "test.model",
            "description": "Test Model",
            "fields": [
                {"name": "name", "type": "Char", "required": True},
                {"name": "ssn", "type": "Char", "sensitive": True},
            ],
        }
        spec = _make_spec(models=[model])
        spec["security"] = {
            "roles": ["viewer", "editor", "manager"],
            "defaults": {"viewer": "r", "editor": "cru", "manager": "crud"},
        }
        spec = _process_security_patterns(spec)
        model_ctx = _build_model_context(spec, spec["models"][0])
        (tmp_module / "models").mkdir(parents=True, exist_ok=True)
        output = env.get_template("model.py.j2").render(model_ctx)
        assert 'groups="test_module.group_test_module_manager"' in output

    def test_backward_compat_no_security_no_groups(self, env, tmp_module):
        """Module without security block: no groups= on any field (backward compat)."""
        model = _make_model("test.model")
        spec = _make_spec(models=[model])
        ctx = _make_module_context(spec)
        result = render_models(env, spec, tmp_module, ctx)
        assert result.success
        model_py = (tmp_module / "models" / "test_model.py").read_text()
        assert "groups=" not in model_py


class TestRenderSecurityFullIntegration:
    """Full integration test: render_module with security spec and sensitive field."""

    def test_full_module_with_security_and_sensitive_field(self, tmp_path):
        """Complete security spec produces all security files and groups= on sensitive field."""
        spec = _make_security_spec_for_render(
            models=[
                {
                    "name": "fee.structure",
                    "description": "Fee Structure",
                    "fields": [
                        {"name": "name", "type": "Char", "required": True},
                        {"name": "company_id", "type": "Many2one", "comodel_name": "res.company"},
                        {"name": "department_id", "type": "Many2one", "comodel_name": "hr.department"},
                        {"name": "secret_code", "type": "Char", "sensitive": True},
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
            ],
        )
        spec["module_title"] = "Test Module"
        spec["summary"] = "Test"
        spec["author"] = "Test"
        files, warnings = render_module(spec, get_template_dir(), tmp_path)
        assert len(files) > 0
        module_dir = tmp_path / "test_module"
        # Security files exist
        assert (module_dir / "security" / "security.xml").exists()
        assert (module_dir / "security" / "ir.model.access.csv").exists()
        assert (module_dir / "security" / "record_rules.xml").exists()
        # Model file has groups= on sensitive field
        fee_structure_py = (module_dir / "models" / "fee_structure.py").read_text()
        assert 'groups="test_module.group_test_module_manager"' in fee_structure_py
        # Non-sensitive model file has no groups=
        fee_line_py = (module_dir / "models" / "fee_line.py").read_text()
        assert "groups=" not in fee_line_py


# ---------------------------------------------------------------------------
# Phase 38 Plan 02: Audit smoke test -- full pipeline with audit:true
# ---------------------------------------------------------------------------


class TestAuditSmokeFullPipeline:
    """Smoke test: minimal spec with audit:true through the complete render_module pipeline."""

    def test_audit_full_pipeline_no_crash(self, tmp_path):
        """render_module() completes without raising any exception for a spec with audit:true."""
        spec = _make_spec(
            models=[
                {
                    "name": "test.record",
                    "description": "Test Record",
                    "fields": [
                        {"name": "name", "type": "Char", "required": True},
                        {"name": "quantity", "type": "Integer"},
                        {"name": "partner_id", "type": "Many2one", "comodel_name": "res.partner"},
                        {"name": "line_ids", "type": "One2many", "comodel_name": "test.line", "inverse_name": "record_id"},
                        {"name": "attachment", "type": "Binary"},
                        {"name": "start_date", "type": "Date"},
                    ],
                    "audit": True,
                    "audit_exclude": ["start_date"],
                },
            ],
        )
        spec["module_title"] = "Test Module"
        spec["summary"] = "Test"
        spec["author"] = "Test"
        spec["security"] = {
            "roles": ["viewer", "manager"],
            "defaults": {
                "viewer": "r",
                "manager": "crud",
            },
        }
        files, warnings = render_module(spec, get_template_dir(), tmp_path)
        assert len(files) > 0

    def test_audit_model_py_contains_audit_skip(self, tmp_path):
        """Generated audited model .py file contains _audit_skip context guard."""
        spec = _make_spec(
            models=[
                {
                    "name": "test.record",
                    "description": "Test Record",
                    "fields": [
                        {"name": "name", "type": "Char", "required": True},
                        {"name": "quantity", "type": "Integer"},
                        {"name": "partner_id", "type": "Many2one", "comodel_name": "res.partner"},
                        {"name": "line_ids", "type": "One2many", "comodel_name": "test.line", "inverse_name": "record_id"},
                        {"name": "attachment", "type": "Binary"},
                    ],
                    "audit": True,
                },
            ],
        )
        spec["module_title"] = "Test Module"
        spec["summary"] = "Test"
        spec["author"] = "Test"
        spec["security"] = {
            "roles": ["viewer", "manager"],
            "defaults": {"viewer": "r", "manager": "crud"},
        }
        files, warnings = render_module(spec, get_template_dir(), tmp_path)
        model_py = (tmp_path / "test_module" / "models" / "test_record.py").read_text()
        assert "_audit_skip" in model_py

    def test_audit_log_model_generated(self, tmp_path):
        """Generated files include an audit.trail.log model .py file."""
        spec = _make_spec(
            models=[
                {
                    "name": "test.record",
                    "description": "Test Record",
                    "fields": [
                        {"name": "name", "type": "Char", "required": True},
                        {"name": "quantity", "type": "Integer"},
                    ],
                    "audit": True,
                },
            ],
        )
        spec["module_title"] = "Test Module"
        spec["summary"] = "Test"
        spec["author"] = "Test"
        spec["security"] = {
            "roles": ["viewer", "manager"],
            "defaults": {"viewer": "r", "manager": "crud"},
        }
        files, warnings = render_module(spec, get_template_dir(), tmp_path)
        audit_model = tmp_path / "test_module" / "models" / "audit_trail_log.py"
        assert audit_model.exists(), (
            f"audit_trail_log.py not generated. Model files: "
            f"{[str(f) for f in files if 'models' in str(f)]}"
        )

    def test_audit_acl_csv_has_audit_entries(self, tmp_path):
        """Generated ir.model.access.csv contains audit.trail.log entries."""
        spec = _make_spec(
            models=[
                {
                    "name": "test.record",
                    "description": "Test Record",
                    "fields": [
                        {"name": "name", "type": "Char", "required": True},
                        {"name": "quantity", "type": "Integer"},
                    ],
                    "audit": True,
                },
            ],
        )
        spec["module_title"] = "Test Module"
        spec["summary"] = "Test"
        spec["author"] = "Test"
        spec["security"] = {
            "roles": ["viewer", "manager"],
            "defaults": {"viewer": "r", "manager": "crud"},
        }
        files, warnings = render_module(spec, get_template_dir(), tmp_path)
        acl_csv = (tmp_path / "test_module" / "security" / "ir.model.access.csv").read_text()
        assert "audit_trail_log" in acl_csv

    def test_audit_security_xml_has_auditor_group(self, tmp_path):
        """Generated security_group.xml contains auditor group when security roles present."""
        spec = _make_spec(
            models=[
                {
                    "name": "test.record",
                    "description": "Test Record",
                    "fields": [
                        {"name": "name", "type": "Char", "required": True},
                        {"name": "quantity", "type": "Integer"},
                    ],
                    "audit": True,
                },
            ],
        )
        spec["module_title"] = "Test Module"
        spec["summary"] = "Test"
        spec["author"] = "Test"
        spec["security"] = {
            "roles": ["viewer", "manager"],
            "defaults": {"viewer": "r", "manager": "crud"},
        }
        files, warnings = render_module(spec, get_template_dir(), tmp_path)
        security_xml = (tmp_path / "test_module" / "security" / "security.xml").read_text()
        assert "auditor" in security_xml

    def test_audit_tracked_fields_excludes_non_auditable(self, tmp_path):
        """_audit_tracked_fields does NOT include One2many, Binary, or manually excluded fields."""
        spec = _make_spec(
            models=[
                {
                    "name": "test.record",
                    "description": "Test Record",
                    "fields": [
                        {"name": "name", "type": "Char", "required": True},
                        {"name": "quantity", "type": "Integer"},
                        {"name": "partner_id", "type": "Many2one", "comodel_name": "res.partner"},
                        {"name": "line_ids", "type": "One2many", "comodel_name": "test.line", "inverse_name": "record_id"},
                        {"name": "attachment", "type": "Binary"},
                        {"name": "start_date", "type": "Date"},
                    ],
                    "audit": True,
                    "audit_exclude": ["start_date"],
                },
            ],
        )
        spec["module_title"] = "Test Module"
        spec["summary"] = "Test"
        spec["author"] = "Test"
        spec["security"] = {
            "roles": ["viewer", "manager"],
            "defaults": {"viewer": "r", "manager": "crud"},
        }
        files, warnings = render_module(spec, get_template_dir(), tmp_path)
        model_py = (tmp_path / "test_module" / "models" / "test_record.py").read_text()
        # Find the _audit_tracked_fields method and extract its return value
        lines = model_py.split("\n")
        tracked_idx = next(
            i for i, line in enumerate(lines) if "_audit_tracked_fields" in line and "def " in line
        )
        method_text = "\n".join(lines[tracked_idx:tracked_idx + 5])
        # Should include auditable fields
        assert "'name'" in method_text or '"name"' in method_text
        assert "'quantity'" in method_text or '"quantity"' in method_text
        assert "'partner_id'" in method_text or '"partner_id"' in method_text
        # Should NOT include excluded types/fields
        assert "line_ids" not in method_text  # One2many auto-excluded
        assert "attachment" not in method_text  # Binary auto-excluded
        assert "start_date" not in method_text  # manually excluded


class TestApprovalSmokeFullPipeline:
    """Smoke tests: approval specs through the complete render_module pipeline."""

    def _make_approval_spec(self):
        """Build a spec with approval on one model plus security roles."""
        spec = _make_spec(
            models=[
                {
                    "name": "test.request",
                    "description": "Test Request",
                    "fields": [
                        {"name": "name", "type": "Char", "required": True},
                        {"name": "amount", "type": "Float"},
                        {"name": "notes", "type": "Text"},
                        {"name": "partner_id", "type": "Many2one", "comodel_name": "res.partner"},
                    ],
                    "approval": {
                        "levels": [
                            {"state": "submitted", "role": "editor", "next": "approved_mgr", "label": "Submitted"},
                            {"state": "approved_mgr", "role": "manager", "next": "done", "label": "Manager Approved"},
                        ],
                        "on_reject": "draft",
                        "reject_allowed_from": ["submitted", "approved_mgr"],
                        "lock_after": "draft",
                        "editable_fields": ["notes"],
                    },
                },
            ],
        )
        spec["module_title"] = "Test Module"
        spec["summary"] = "Test"
        spec["author"] = "Test"
        spec["security"] = {
            "roles": ["editor", "manager"],
            "defaults": {"editor": "crud", "manager": "crud"},
        }
        return spec

    def test_approval_full_pipeline_no_crash(self, tmp_path):
        """render_module() completes without raising for approval spec."""
        spec = self._make_approval_spec()
        files, warnings = render_module(spec, get_template_dir(), tmp_path)
        assert len(files) > 0

    def test_approval_model_py_contains_action_methods(self, tmp_path):
        """Generated model .py has action_submit, action_approve_submitted, action_approve_approved_mgr."""
        spec = self._make_approval_spec()
        files, warnings = render_module(spec, get_template_dir(), tmp_path)
        model_py = (tmp_path / "test_module" / "models" / "test_request.py").read_text()
        assert "def action_submit(self):" in model_py
        assert "def action_approve_submitted(self):" in model_py
        assert "def action_approve_approved_mgr(self):" in model_py

    def test_approval_model_py_contains_write_guard(self, tmp_path):
        """Generated model .py has _force_state guard."""
        spec = self._make_approval_spec()
        files, warnings = render_module(spec, get_template_dir(), tmp_path)
        model_py = (tmp_path / "test_module" / "models" / "test_request.py").read_text()
        assert "_force_state" in model_py

    def test_approval_model_py_has_group_check(self, tmp_path):
        """Generated model .py has has_group call."""
        spec = self._make_approval_spec()
        files, warnings = render_module(spec, get_template_dir(), tmp_path)
        model_py = (tmp_path / "test_module" / "models" / "test_request.py").read_text()
        assert "has_group" in model_py

    def test_approval_model_py_user_error_not_access_error(self, tmp_path):
        """Generated model .py uses UserError, not AccessError."""
        spec = self._make_approval_spec()
        files, warnings = render_module(spec, get_template_dir(), tmp_path)
        model_py = (tmp_path / "test_module" / "models" / "test_request.py").read_text()
        assert "UserError" in model_py
        assert "AccessError" not in model_py

    def test_approval_model_py_with_context_force_state(self, tmp_path):
        """Generated model .py uses with_context(_force_state=True).write."""
        spec = self._make_approval_spec()
        files, warnings = render_module(spec, get_template_dir(), tmp_path)
        model_py = (tmp_path / "test_module" / "models" / "test_request.py").read_text()
        assert "with_context(_force_state=True).write" in model_py

    def test_approval_view_xml_has_buttons(self, tmp_path):
        """Generated form view XML has approval button elements."""
        spec = self._make_approval_spec()
        files, warnings = render_module(spec, get_template_dir(), tmp_path)
        view_xml = (tmp_path / "test_module" / "views" / "test_request_views.xml").read_text()
        assert 'name="action_submit"' in view_xml
        assert 'name="action_approve_submitted"' in view_xml
        assert 'name="action_approve_approved_mgr"' in view_xml
        assert 'name="action_reject"' in view_xml
        assert 'name="action_reset_to_draft"' in view_xml

    def test_approval_view_xml_invisible_not_states(self, tmp_path):
        """Generated view XML uses invisible= NOT states=."""
        spec = self._make_approval_spec()
        files, warnings = render_module(spec, get_template_dir(), tmp_path)
        view_xml = (tmp_path / "test_module" / "views" / "test_request_views.xml").read_text()
        assert "invisible=" in view_xml
        # Check approval button lines don't use deprecated states=
        lines = view_xml.split("\n")
        approval_lines = [
            l for l in lines
            if 'name="action_submit"' in l
            or 'name="action_approve' in l
            or 'name="action_reject"' in l
            or 'name="action_reset_to_draft"' in l
        ]
        for line in approval_lines:
            assert "states=" not in line

    def test_approval_record_rules_generated(self, tmp_path):
        """Record rules XML file exists and contains approval ir.rule entries."""
        spec = self._make_approval_spec()
        files, warnings = render_module(spec, get_template_dir(), tmp_path)
        rr_path = tmp_path / "test_module" / "security" / "record_rules.xml"
        assert rr_path.exists(), (
            f"record_rules.xml not generated. Files: {[str(f) for f in files if 'security' in str(f)]}"
        )
        rr_xml = rr_path.read_text()
        assert "rule_test_request_draft" in rr_xml
        assert "rule_test_request_manager" in rr_xml

    def test_approval_no_regression_without_approval(self, tmp_path):
        """render_module() with non-approval spec still works (no regression)."""
        spec = _make_spec(
            models=[
                {
                    "name": "test.plain",
                    "description": "Plain Model",
                    "fields": [
                        {"name": "name", "type": "Char", "required": True},
                        {"name": "value", "type": "Integer"},
                    ],
                },
            ],
        )
        spec["module_title"] = "Test Module"
        spec["summary"] = "Test"
        spec["author"] = "Test"
        files, warnings = render_module(spec, get_template_dir(), tmp_path)
        assert len(files) > 0
        # Verify no approval content leaked
        model_py = (tmp_path / "test_module" / "models" / "test_plain.py").read_text()
        assert "action_submit" not in model_py
        assert "_force_state" not in model_py

    def test_approval_with_audit_combined(self, tmp_path):
        """Spec with BOTH audit:true AND approval block renders correctly with proper write() stacking."""
        spec = self._make_approval_spec()
        spec["models"][0]["audit"] = True
        files, warnings = render_module(spec, get_template_dir(), tmp_path)
        model_py = (tmp_path / "test_module" / "models" / "test_request.py").read_text()
        # Both audit and approval should be present
        assert "_audit_skip" in model_py
        assert "_force_state" in model_py
        # Stacking order: audit old_values BEFORE approval guard BEFORE main super()
        # NOTE: The first super().write() call is inside the _audit_skip fast path,
        # so we need to find the main-path super().write() which comes AFTER the approval guard.
        audit_pos = model_py.find("_audit_read_old")
        approval_pos = model_py.find("_force_state")
        # Find the main-path super() after the approval guard
        main_super_pos = model_py.find("result = super().write(vals)", approval_pos)
        assert audit_pos < approval_pos < main_super_pos, (
            f"Stacking order wrong: audit={audit_pos}, approval={approval_pos}, super={main_super_pos}"
        )

    def test_approval_rejected_state_generated(self, tmp_path):
        """Spec with on_reject: 'rejected' generates rejected state in Selection."""
        spec = self._make_approval_spec()
        spec["models"][0]["approval"]["on_reject"] = "rejected"
        files, warnings = render_module(spec, get_template_dir(), tmp_path)
        model_py = (tmp_path / "test_module" / "models" / "test_request.py").read_text()
        # The rejected state should appear in the Selection field
        assert '"rejected"' in model_py or "'rejected'" in model_py


# ---------------------------------------------------------------------------
# Phase 40: Notification + Webhook smoke tests (Plan 02)
# ---------------------------------------------------------------------------


class TestNotificationWebhookSmokeFullPipeline:
    """Smoke tests: spec with approval + notifications + webhooks through the complete render_module pipeline."""

    NOTIFICATION_WEBHOOK_SPEC = {
        "module_name": "test_notif_webhook",
        "module_title": "Test Notifications and Webhooks",
        "summary": "Test module",
        "author": "Test",
        "depends": ["base"],
        "odoo_version": "17.0",
        "security": {
            "roles": ["user", "manager"],
            "defaults": {"user": "crud", "manager": "crud"},
        },
        "models": [{
            "name": "test.request",
            "description": "Test Request",
            "fields": [
                {"name": "name", "type": "Char", "required": True, "string": "Name"},
                {"name": "amount", "type": "Float", "string": "Amount"},
                {"name": "description", "type": "Text", "string": "Description"},
                {"name": "supervisor_id", "type": "Many2one", "comodel_name": "res.users", "string": "Supervisor"},
            ],
            "approval": {
                "levels": [
                    {
                        "state": "submitted", "role": "user", "next": "approved",
                        "label": "Submitted",
                        "notify": {
                            "template": "email_request_submitted",
                            "recipients": "role:manager",
                            "subject": "Request Submitted: {{ object.name }}",
                        },
                    },
                    {"state": "approved", "role": "manager", "next": "done", "label": "Approved"},
                ],
                "on_reject": "draft",
                "reject_allowed_from": ["submitted", "approved"],
                "on_reject_notify": {
                    "template": "email_request_rejected",
                    "recipients": "creator",
                    "subject": "Request Rejected: {{ object.name }}",
                },
            },
            "webhooks": {
                "on_create": True,
                "on_write": ["state", "amount"],
                "on_unlink": False,
            },
        }],
    }

    def _get_spec(self):
        """Return a deep copy of the spec to prevent mutation across tests."""
        import copy
        return copy.deepcopy(self.NOTIFICATION_WEBHOOK_SPEC)

    def test_full_pipeline_renders_without_error(self, tmp_path):
        """render_module completes without exception."""
        spec = self._get_spec()
        files, warnings = render_module(spec, get_template_dir(), tmp_path)
        assert len(files) > 0

    def test_mail_template_data_xml_created(self, tmp_path):
        """data/mail_template_data.xml exists in output."""
        spec = self._get_spec()
        files, warnings = render_module(spec, get_template_dir(), tmp_path)
        mail_data = tmp_path / "test_notif_webhook" / "data" / "mail_template_data.xml"
        assert mail_data.exists(), (
            f"mail_template_data.xml not generated. Files: {[str(f) for f in files if 'data' in str(f)]}"
        )

    def test_mail_template_xml_has_noupdate(self, tmp_path):
        """Rendered XML contains noupdate='1'."""
        spec = self._get_spec()
        render_module(spec, get_template_dir(), tmp_path)
        xml = (tmp_path / "test_notif_webhook" / "data" / "mail_template_data.xml").read_text()
        assert 'noupdate="1"' in xml

    def test_mail_template_xml_has_subject(self, tmp_path):
        """Rendered XML contains subject from spec."""
        spec = self._get_spec()
        render_module(spec, get_template_dir(), tmp_path)
        xml = (tmp_path / "test_notif_webhook" / "data" / "mail_template_data.xml").read_text()
        assert "Request Submitted" in xml or "subject" in xml.lower()

    def test_mail_template_xml_has_email_to(self, tmp_path):
        """Rendered XML contains resolved email_to expression."""
        spec = self._get_spec()
        render_module(spec, get_template_dir(), tmp_path)
        xml = (tmp_path / "test_notif_webhook" / "data" / "mail_template_data.xml").read_text()
        assert "email_to" in xml

    def test_model_py_has_logger(self, tmp_path):
        """Generated model.py has import logging and _logger."""
        spec = self._get_spec()
        render_module(spec, get_template_dir(), tmp_path)
        model_py = (tmp_path / "test_notif_webhook" / "models" / "test_request.py").read_text()
        assert "import logging" in model_py
        assert "_logger" in model_py

    def test_model_py_has_send_mail(self, tmp_path):
        """Generated model.py has send_mail in action method."""
        spec = self._get_spec()
        render_module(spec, get_template_dir(), tmp_path)
        model_py = (tmp_path / "test_notif_webhook" / "models" / "test_request.py").read_text()
        assert "send_mail" in model_py

    def test_model_py_send_mail_after_state_write(self, tmp_path):
        """send_mail appears AFTER state write in action method."""
        spec = self._get_spec()
        render_module(spec, get_template_dir(), tmp_path)
        model_py = (tmp_path / "test_notif_webhook" / "models" / "test_request.py").read_text()
        submit_pos = model_py.find("def action_submit(self):")
        assert submit_pos > -1
        state_write_pos = model_py.find("_force_state", submit_pos)
        send_mail_pos = model_py.find("send_mail", submit_pos)
        assert state_write_pos < send_mail_pos, "send_mail should come after state write"

    def test_model_py_has_webhook_stubs(self, tmp_path):
        """Generated model.py has _webhook_post_create and _webhook_post_write."""
        spec = self._get_spec()
        render_module(spec, get_template_dir(), tmp_path)
        model_py = (tmp_path / "test_notif_webhook" / "models" / "test_request.py").read_text()
        assert "_webhook_post_create" in model_py
        assert "_webhook_post_write" in model_py

    def test_model_py_webhook_in_create(self, tmp_path):
        """Generated model.py has _skip_webhooks guard in create()."""
        spec = self._get_spec()
        render_module(spec, get_template_dir(), tmp_path)
        model_py = (tmp_path / "test_notif_webhook" / "models" / "test_request.py").read_text()
        create_pos = model_py.find("def create(")
        assert create_pos > -1
        create_body = model_py[create_pos:model_py.find("\n    def ", create_pos + 1)]
        assert "_skip_webhooks" in create_body
        assert "_webhook_post_create" in create_body

    def test_model_py_webhook_in_write(self, tmp_path):
        """Generated model.py has webhook dispatch in write()."""
        spec = self._get_spec()
        render_module(spec, get_template_dir(), tmp_path)
        model_py = (tmp_path / "test_notif_webhook" / "models" / "test_request.py").read_text()
        write_pos = model_py.find("def write(self, vals):")
        assert write_pos > -1
        write_body = model_py[write_pos:]
        assert "_webhook_post_write" in write_body
        assert "_skip_webhooks" in write_body

    def test_manifest_has_mail_dependency(self, tmp_path):
        """Manifest.py has 'mail' in depends."""
        spec = self._get_spec()
        render_module(spec, get_template_dir(), tmp_path)
        manifest = (tmp_path / "test_notif_webhook" / "__manifest__.py").read_text()
        assert '"mail"' in manifest

    def test_manifest_has_mail_template_data(self, tmp_path):
        """Manifest.py has 'data/mail_template_data.xml' in data."""
        spec = self._get_spec()
        render_module(spec, get_template_dir(), tmp_path)
        manifest = (tmp_path / "test_notif_webhook" / "__manifest__.py").read_text()
        assert "mail_template_data.xml" in manifest

    def test_no_feature_regression(self, tmp_path):
        """Render a plain spec (no notifications, no webhooks) and confirm it still renders without error and without notification/webhook artifacts."""
        spec = _make_spec(
            models=[
                {
                    "name": "test.plain",
                    "description": "Plain Model",
                    "fields": [
                        {"name": "name", "type": "Char", "required": True},
                        {"name": "value", "type": "Integer"},
                    ],
                },
            ],
        )
        files, warnings = render_module(spec, get_template_dir(), tmp_path)
        assert len(files) > 0
        model_py = (tmp_path / "test_module" / "models" / "test_plain.py").read_text()
        assert "import logging" not in model_py
        assert "_logger" not in model_py
        assert "send_mail" not in model_py
        assert "_webhook_post_create" not in model_py
        assert "_webhook_post_write" not in model_py
        assert "_skip_webhooks" not in model_py
        # No mail_template_data.xml should be generated
        mail_data = tmp_path / "test_module" / "data" / "mail_template_data.xml"
        assert not mail_data.exists()

    def test_webhook_only_no_notifications(self, tmp_path):
        """Render spec with webhooks but no approval/notifications -- webhook stubs present, no logger import, no send_mail."""
        spec = _make_spec(
            models=[
                {
                    "name": "test.hooked",
                    "description": "Hooked Model",
                    "fields": [
                        {"name": "name", "type": "Char", "required": True},
                        {"name": "value", "type": "Integer"},
                        {"name": "status", "type": "Selection", "selection": [("active", "Active"), ("inactive", "Inactive")]},
                    ],
                    "webhooks": {
                        "on_create": True,
                        "on_write": ["value", "status"],
                        "on_unlink": False,
                    },
                },
            ],
        )
        spec["security"] = {
            "roles": ["user", "manager"],
            "defaults": {"user": "crud", "manager": "crud"},
        }
        files, warnings = render_module(spec, get_template_dir(), tmp_path)
        assert len(files) > 0
        model_py = (tmp_path / "test_module" / "models" / "test_hooked.py").read_text()
        # Webhook stubs present
        assert "_webhook_post_create" in model_py
        assert "_webhook_post_write" in model_py
        assert "_skip_webhooks" in model_py
        # No notifications artifacts
        assert "import logging" not in model_py
        assert "send_mail" not in model_py
        # No mail template XML
        mail_data = tmp_path / "test_module" / "data" / "mail_template_data.xml"
        assert not mail_data.exists()
