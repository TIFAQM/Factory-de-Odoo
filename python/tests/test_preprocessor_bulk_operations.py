"""Tests for bulk_operations preprocessor."""
from __future__ import annotations

from typing import Any

import pytest

from amil_utils.preprocessors.bulk_operations import (
    _process_bulk_operations,
    _enrich_bulk_op,
    _build_wizard_model_dict,
    _build_wizard_line_dict,
    _BATCH_DEFAULTS,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_spec(
    models: list[dict[str, Any]] | None = None,
    bulk_operations: list[dict[str, Any]] | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    """Build a minimal spec dict for bulk operations testing."""
    base: dict[str, Any] = {
        "module_name": "test_mod",
        "depends": ["base"],
        "models": models or [],
    }
    if bulk_operations is not None:
        base["bulk_operations"] = bulk_operations
    base.update(overrides)
    return base


def _make_bulk_op(**overrides: Any) -> dict[str, Any]:
    """Build a minimal bulk operation dict."""
    base: dict[str, Any] = {
        "name": "Bulk Approve",
        "wizard_model": "bulk.approve.wizard",
        "source_model": "leave.request",
        "operation": "state_transition",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Tests: _enrich_bulk_op
# ---------------------------------------------------------------------------


class TestEnrichBulkOp:
    def test_assigns_default_batch_size(self) -> None:
        op = _make_bulk_op(operation="state_transition", batch_size=None)
        enriched = _enrich_bulk_op(op)
        assert enriched["batch_size"] == _BATCH_DEFAULTS["state_transition"]

    def test_preserves_explicit_batch_size(self) -> None:
        op = _make_bulk_op(batch_size=42)
        enriched = _enrich_bulk_op(op)
        assert enriched["batch_size"] == 42

    def test_unknown_operation_defaults_to_100(self) -> None:
        op = _make_bulk_op(operation="custom_op", batch_size=None)
        enriched = _enrich_bulk_op(op)
        assert enriched["batch_size"] == 100

    def test_computes_wizard_var(self) -> None:
        op = _make_bulk_op(wizard_model="bulk.approve.wizard")
        enriched = _enrich_bulk_op(op)
        assert enriched["wizard_var"] == "bulk_approve_wizard"

    def test_computes_source_model_var(self) -> None:
        op = _make_bulk_op(source_model="leave.request")
        enriched = _enrich_bulk_op(op)
        assert enriched["source_model_var"] == "leave_request"

    def test_computes_source_model_class(self) -> None:
        op = _make_bulk_op(source_model="leave.request")
        enriched = _enrich_bulk_op(op)
        assert enriched["source_model_class"] == "LeaveRequest"

    def test_does_not_mutate_input(self) -> None:
        op = _make_bulk_op(batch_size=None)
        original_keys = set(op.keys())
        _enrich_bulk_op(op)
        assert "wizard_var" not in op  # not mutated
        assert set(op.keys()) == original_keys


# ---------------------------------------------------------------------------
# Tests: _build_wizard_model_dict
# ---------------------------------------------------------------------------


class TestBuildWizardModelDict:
    def test_model_name(self) -> None:
        op = _make_bulk_op()
        enriched = _enrich_bulk_op(op)
        wizard = _build_wizard_model_dict(enriched)
        assert wizard["name"] == "bulk.approve.wizard"
        assert wizard["_name"] == "bulk.approve.wizard"

    def test_is_transient(self) -> None:
        op = _make_bulk_op()
        enriched = _enrich_bulk_op(op)
        wizard = _build_wizard_model_dict(enriched)
        assert wizard["is_transient"] is True
        assert wizard["is_bulk_wizard"] is True

    def test_has_state_field(self) -> None:
        op = _make_bulk_op()
        enriched = _enrich_bulk_op(op)
        wizard = _build_wizard_model_dict(enriched)
        field_names = [f["name"] for f in wizard["fields"]]
        assert "state" in field_names
        assert "record_count" in field_names
        assert "success_count" in field_names
        assert "fail_count" in field_names
        assert "error_log" in field_names

    def test_preview_line_ids_references_line_model(self) -> None:
        op = _make_bulk_op()
        enriched = _enrich_bulk_op(op)
        wizard = _build_wizard_model_dict(enriched)
        preview_field = next(f for f in wizard["fields"] if f["name"] == "preview_line_ids")
        assert preview_field["comodel_name"] == "bulk.approve.wizard.line"

    def test_wizard_fields_appended(self) -> None:
        op = _make_bulk_op(wizard_fields=[
            {"name": "target_state", "type": "Selection", "required": True},
            {"name": "note", "type": "Text"},
        ])
        enriched = _enrich_bulk_op(op)
        wizard = _build_wizard_model_dict(enriched)
        field_names = [f["name"] for f in wizard["fields"]]
        assert "target_state" in field_names
        assert "note" in field_names

    def test_wizard_field_with_comodel(self) -> None:
        op = _make_bulk_op(wizard_fields=[
            {"name": "reviewer_id", "type": "Many2one", "comodel": "res.users"},
        ])
        enriched = _enrich_bulk_op(op)
        wizard = _build_wizard_model_dict(enriched)
        reviewer = next(f for f in wizard["fields"] if f["name"] == "reviewer_id")
        assert reviewer["comodel_name"] == "res.users"

    def test_description_from_op_name(self) -> None:
        op = _make_bulk_op(name="Mass Enroll Students")
        enriched = _enrich_bulk_op(op)
        wizard = _build_wizard_model_dict(enriched)
        assert wizard["description"] == "Mass Enroll Students"


# ---------------------------------------------------------------------------
# Tests: _build_wizard_line_dict
# ---------------------------------------------------------------------------


class TestBuildWizardLineDict:
    def test_line_model_name(self) -> None:
        op = _make_bulk_op()
        enriched = _enrich_bulk_op(op)
        line = _build_wizard_line_dict(enriched)
        assert line["name"] == "bulk.approve.wizard.line"
        assert line["_name"] == "bulk.approve.wizard.line"

    def test_is_transient(self) -> None:
        op = _make_bulk_op()
        enriched = _enrich_bulk_op(op)
        line = _build_wizard_line_dict(enriched)
        assert line["is_transient"] is True
        assert line["is_bulk_wizard_line"] is True

    def test_has_wizard_id_and_selected(self) -> None:
        op = _make_bulk_op()
        enriched = _enrich_bulk_op(op)
        line = _build_wizard_line_dict(enriched)
        field_names = [f["name"] for f in line["fields"]]
        assert "wizard_id" in field_names
        assert "selected" in field_names

    def test_preview_fields_become_char_fields(self) -> None:
        op = _make_bulk_op(preview_fields=["name", "state", "date_from"])
        enriched = _enrich_bulk_op(op)
        line = _build_wizard_line_dict(enriched)
        field_names = [f["name"] for f in line["fields"]]
        assert "name" in field_names
        assert "state" in field_names
        assert "date_from" in field_names
        # Check they are Char type
        state_field = next(f for f in line["fields"] if f["name"] == "state")
        assert state_field["type"] == "Char"
        assert state_field["related"] is True

    def test_no_preview_fields(self) -> None:
        op = _make_bulk_op()
        enriched = _enrich_bulk_op(op)
        line = _build_wizard_line_dict(enriched)
        # Only wizard_id and selected
        assert len(line["fields"]) == 2


# ---------------------------------------------------------------------------
# Tests: _process_bulk_operations (main entry)
# ---------------------------------------------------------------------------


class TestProcessBulkOperations:
    def test_happy_path(self) -> None:
        source_model = {"name": "leave.request", "fields": []}
        op = _make_bulk_op(preview_fields=["name", "state"])
        spec = _make_spec(models=[source_model], bulk_operations=[op])
        result = _process_bulk_operations(spec)

        assert result is not spec  # immutability
        assert result["has_bulk_operations"] is True
        assert "bus" in result["depends"]

        # Enriched operations
        assert len(result["bulk_operations"]) == 1
        enriched_op = result["bulk_operations"][0]
        assert "wizard_var" in enriched_op
        assert "batch_size" in enriched_op

        # Wizard models
        assert len(result["bulk_wizards"]) == 2  # wizard + line
        wizard_names = [w["name"] for w in result["bulk_wizards"]]
        assert "bulk.approve.wizard" in wizard_names
        assert "bulk.approve.wizard.line" in wizard_names

    def test_empty_spec_no_bulk_ops(self) -> None:
        spec = _make_spec()
        result = _process_bulk_operations(spec)
        assert result is spec  # unchanged

    def test_empty_bulk_operations_list(self) -> None:
        spec = _make_spec(bulk_operations=[])
        result = _process_bulk_operations(spec)
        assert result is spec

    def test_bus_not_duplicated(self) -> None:
        op = _make_bulk_op()
        spec = _make_spec(bulk_operations=[op])
        spec["depends"] = ["base", "bus"]
        result = _process_bulk_operations(spec)
        assert result["depends"].count("bus") == 1

    def test_batch_size_set_on_source_model(self) -> None:
        source_model = {"name": "leave.request", "fields": []}
        op = _make_bulk_op(source_model="leave.request", batch_size=None)
        spec = _make_spec(models=[source_model], bulk_operations=[op])
        result = _process_bulk_operations(spec)
        leave_model = next(m for m in result["models"] if m["name"] == "leave.request")
        assert "bulk_post_processing_batch_size" in leave_model

    def test_no_line_model_without_preview_fields(self) -> None:
        op = _make_bulk_op()  # no preview_fields
        spec = _make_spec(bulk_operations=[op])
        result = _process_bulk_operations(spec)
        wizard_names = [w["name"] for w in result["bulk_wizards"]]
        assert "bulk.approve.wizard" in wizard_names
        assert "bulk.approve.wizard.line" not in wizard_names

    def test_multiple_bulk_operations(self) -> None:
        op1 = _make_bulk_op(
            name="Bulk Approve",
            wizard_model="bulk.approve.wizard",
            source_model="leave.request",
            preview_fields=["name"],
        )
        op2 = _make_bulk_op(
            name="Bulk Enroll",
            wizard_model="bulk.enroll.wizard",
            source_model="student.enrollment",
            operation="create_related",
        )
        spec = _make_spec(bulk_operations=[op1, op2])
        result = _process_bulk_operations(spec)
        assert len(result["bulk_operations"]) == 2
        # op1 has preview_fields -> wizard + line; op2 doesn't -> wizard only
        wizard_names = [w["name"] for w in result["bulk_wizards"]]
        assert "bulk.approve.wizard" in wizard_names
        assert "bulk.approve.wizard.line" in wizard_names
        assert "bulk.enroll.wizard" in wizard_names

    def test_does_not_mutate_original_spec(self) -> None:
        op = _make_bulk_op()
        spec = _make_spec(bulk_operations=[op])
        original_depends = list(spec["depends"])
        _process_bulk_operations(spec)
        assert spec["depends"] == original_depends
        assert "has_bulk_operations" not in spec

    def test_batch_defaults(self) -> None:
        """Each operation type has a known default batch size."""
        assert _BATCH_DEFAULTS["state_transition"] == 50
        assert _BATCH_DEFAULTS["create_related"] == 100
        assert _BATCH_DEFAULTS["update_fields"] == 200
