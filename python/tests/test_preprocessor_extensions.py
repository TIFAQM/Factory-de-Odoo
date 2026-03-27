"""Tests for extensions preprocessor."""
from __future__ import annotations

from typing import Any

import pytest

from amil_utils.preprocessors.extensions import _process_extensions


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_spec(
    extends: list[dict[str, Any]] | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    """Build a minimal spec dict for extensions testing."""
    base: dict[str, Any] = {
        "module_name": "test_mod",
        "depends": ["base"],
        "models": [],
    }
    if extends is not None:
        base["extends"] = extends
    base.update(overrides)
    return base


def _make_extension(
    base_module: str = "hr",
    base_model: str = "hr.employee",
    add_fields: list[dict[str, Any]] | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    """Build a minimal extension entry."""
    ext: dict[str, Any] = {
        "base_module": base_module,
        "base_model": base_model,
        "add_fields": add_fields or [],
    }
    ext.update(overrides)
    return ext


# ---------------------------------------------------------------------------
# Tests: _process_extensions (main entry)
# ---------------------------------------------------------------------------


class TestProcessExtensions:
    def test_happy_path(self) -> None:
        ext = _make_extension(
            base_module="hr",
            base_model="hr.employee",
            add_fields=[
                {"name": "badge_id", "type": "Char", "required": True},
            ],
        )
        spec = _make_spec(extends=[ext])
        result = _process_extensions(spec)

        assert result is not spec  # immutability
        assert result["has_extensions"] is True
        # base_module injected into depends
        assert "hr" in result["depends"]
        # Extension model files computed
        assert "hr_employee" in result["extension_model_files"]
        # Metadata computed on extension
        ext_result = result["extends"][0]
        assert ext_result["base_model_var"] == "hr_employee"
        assert ext_result["class_name"] == "HrEmployee"
        assert ext_result["file_name"] == "hr_employee"

    def test_empty_spec_no_extends(self) -> None:
        spec = _make_spec()
        result = _process_extensions(spec)
        assert result is spec  # unchanged

    def test_extends_empty_list(self) -> None:
        spec = _make_spec(extends=[])
        result = _process_extensions(spec)
        assert result is spec

    def test_selection_values_normalized_to_selection(self) -> None:
        ext = _make_extension(
            add_fields=[
                {
                    "name": "priority",
                    "type": "Selection",
                    "values": [("low", "Low"), ("high", "High")],
                },
            ],
        )
        spec = _make_spec(extends=[ext])
        result = _process_extensions(spec)
        field = result["extends"][0]["add_fields"][0]
        assert "selection" in field
        assert field["selection"] == [("low", "Low"), ("high", "High")]

    def test_comodel_normalized_to_comodel_name(self) -> None:
        ext = _make_extension(
            add_fields=[
                {"name": "department_id", "type": "Many2one", "comodel": "hr.department"},
            ],
        )
        spec = _make_spec(extends=[ext])
        result = _process_extensions(spec)
        field = result["extends"][0]["add_fields"][0]
        assert "comodel_name" in field
        assert field["comodel_name"] == "hr.department"

    def test_base_module_not_duplicated_in_depends(self) -> None:
        ext = _make_extension(base_module="hr")
        spec = _make_spec(extends=[ext], depends=["base", "hr"])
        result = _process_extensions(spec)
        assert result["depends"].count("hr") == 1

    def test_multiple_extensions(self) -> None:
        ext1 = _make_extension(base_module="hr", base_model="hr.employee")
        ext2 = _make_extension(base_module="sale", base_model="sale.order")
        spec = _make_spec(extends=[ext1, ext2])
        result = _process_extensions(spec)
        assert "hr" in result["depends"]
        assert "sale" in result["depends"]
        assert len(result["extends"]) == 2
        assert "hr_employee" in result["extension_model_files"]
        assert "sale_order" in result["extension_model_files"]

    def test_empty_base_module_not_injected(self) -> None:
        ext = _make_extension(base_module="", base_model="res.partner")
        spec = _make_spec(extends=[ext])
        result = _process_extensions(spec)
        # Empty string should not be added to depends
        assert "" not in result["depends"]

    def test_missing_optional_fields_no_crash(self) -> None:
        """Extension with no add_fields, add_computed, etc. should not crash."""
        ext: dict[str, Any] = {
            "base_module": "hr",
            "base_model": "hr.employee",
        }
        spec = _make_spec(extends=[ext])
        result = _process_extensions(spec)
        assert result["has_extensions"] is True
        ext_result = result["extends"][0]
        assert ext_result["add_fields"] == []
        assert ext_result["add_computed"] == []
        assert ext_result["add_constraints"] == []
        assert ext_result["add_methods"] == []
        assert ext_result["view_extensions"] == []

    def test_add_computed_normalized(self) -> None:
        ext = _make_extension(
            add_computed=[
                {"name": "full_name", "type": "Char", "depends": ["first_name", "last_name"]},
            ],
        )
        spec = _make_spec(extends=[ext])
        result = _process_extensions(spec)
        assert len(result["extends"][0]["add_computed"]) == 1

    def test_add_methods_normalized(self) -> None:
        ext = _make_extension(
            add_methods=[
                {"name": "action_approve", "body": "self.write({'state': 'approved'})"},
            ],
        )
        spec = _make_spec(extends=[ext])
        result = _process_extensions(spec)
        assert len(result["extends"][0]["add_methods"]) == 1

    def test_view_extensions_normalized(self) -> None:
        ext = _make_extension(
            view_extensions=[
                {"view_type": "form", "xpath": "//field[@name='name']", "position": "after"},
            ],
        )
        spec = _make_spec(extends=[ext])
        result = _process_extensions(spec)
        assert len(result["extends"][0]["view_extensions"]) == 1

    def test_does_not_mutate_original_spec(self) -> None:
        ext = _make_extension(base_module="hr", base_model="hr.employee")
        spec = _make_spec(extends=[ext])
        original_depends = list(spec["depends"])
        _process_extensions(spec)
        assert spec["depends"] == original_depends
        assert "has_extensions" not in spec

    def test_class_name_computation(self) -> None:
        """Verify class name computation for multi-dot models."""
        ext = _make_extension(base_model="academy.student.enrollment")
        spec = _make_spec(extends=[ext])
        result = _process_extensions(spec)
        ext_result = result["extends"][0]
        assert ext_result["class_name"] == "AcademyStudentEnrollment"
        assert ext_result["base_model_var"] == "academy_student_enrollment"
