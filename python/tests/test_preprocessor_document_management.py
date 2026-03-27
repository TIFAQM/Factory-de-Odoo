"""Tests for document_management preprocessor."""
from __future__ import annotations

from typing import Any

import pytest

from amil_utils.preprocessors.document_management import (
    _process_document_management,
    _build_document_type_model,
    _build_document_document_model,
    _inject_security_roles,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_spec(**overrides: Any) -> dict[str, Any]:
    """Build a minimal spec dict for document management testing."""
    base: dict[str, Any] = {
        "module_name": "test_mod",
        "models": [],
        "depends": ["base"],
    }
    base.update(overrides)
    return base


def _field_names(model: dict[str, Any]) -> list[str]:
    """Extract field names from a model dict."""
    return [f["name"] for f in model.get("fields", [])]


# ---------------------------------------------------------------------------
# Tests: _build_document_type_model
# ---------------------------------------------------------------------------


class TestBuildDocumentTypeModel:
    def test_returns_correct_name(self) -> None:
        model = _build_document_type_model()
        assert model["name"] == "document.type"

    def test_has_required_fields(self) -> None:
        model = _build_document_type_model()
        names = _field_names(model)
        assert "name" in names
        assert "code" in names
        assert "required_for" in names
        assert "max_file_size" in names

    def test_has_unique_code_constraint(self) -> None:
        model = _build_document_type_model()
        constraint_names = [c["name"] for c in model.get("sql_constraints", [])]
        assert "unique_code" in constraint_names


# ---------------------------------------------------------------------------
# Tests: _build_document_document_model
# ---------------------------------------------------------------------------


class TestBuildDocumentDocumentModel:
    def test_all_features_enabled(self) -> None:
        config: dict[str, Any] = {
            "enable_versioning": True,
            "enable_verification": True,
            "enable_classification": True,
            "enable_expiry": True,
        }
        model = _build_document_document_model(config, "test_mod")
        names = _field_names(model)
        # Core fields
        assert "name" in names
        assert "file" in names
        assert "document_type_id" in names
        # Verification fields
        assert "verification_state" in names
        assert "verified_by" in names
        # Versioning fields
        assert "version" in names
        assert "previous_version_id" in names
        assert "is_latest" in names
        # Classification fields
        assert "classification" in names
        # Expiry fields
        assert "expiry_date" in names
        assert "is_expired" in names

    def test_verification_disabled(self) -> None:
        config: dict[str, Any] = {"enable_verification": False}
        model = _build_document_document_model(config, "test_mod")
        names = _field_names(model)
        assert "verification_state" not in names
        assert "verified_by" not in names
        # Model flags
        assert model["has_document_verification"] is False

    def test_versioning_disabled(self) -> None:
        config: dict[str, Any] = {"enable_versioning": False}
        model = _build_document_document_model(config, "test_mod")
        names = _field_names(model)
        assert "version" not in names
        assert "previous_version_id" not in names
        assert model["has_document_versioning"] is False

    def test_classification_disabled(self) -> None:
        config: dict[str, Any] = {"enable_classification": False}
        model = _build_document_document_model(config, "test_mod")
        names = _field_names(model)
        assert "classification" not in names
        assert "access_groups" not in names

    def test_expiry_disabled(self) -> None:
        config: dict[str, Any] = {"enable_expiry": False}
        model = _build_document_document_model(config, "test_mod")
        names = _field_names(model)
        assert "expiry_date" not in names
        assert "is_expired" not in names

    def test_empty_config_enables_all(self) -> None:
        """Empty config should enable versioning, verification, classification, expiry."""
        model = _build_document_document_model({}, "test_mod")
        names = _field_names(model)
        assert "verification_state" in names
        assert "version" in names
        assert "classification" in names
        assert "expiry_date" in names

    def test_inherits_mail_thread(self) -> None:
        model = _build_document_document_model({}, "test_mod")
        assert "mail.thread" in model.get("inherit", [])

    def test_notes_always_present(self) -> None:
        model = _build_document_document_model({}, "test_mod")
        names = _field_names(model)
        assert "notes" in names

    def test_complex_constraints_with_verification(self) -> None:
        config: dict[str, Any] = {"enable_verification": True, "enable_versioning": True}
        model = _build_document_document_model(config, "test_mod")
        constraint_names = [c["name"] for c in model.get("complex_constraints", [])]
        assert "doc_file_validation" in constraint_names
        assert "doc_action_verify" in constraint_names
        assert "doc_action_reject" in constraint_names
        assert "doc_action_reset" in constraint_names
        assert "doc_action_upload_new_version" in constraint_names

    def test_complex_constraints_without_verification(self) -> None:
        config: dict[str, Any] = {"enable_verification": False, "enable_versioning": False}
        model = _build_document_document_model(config, "test_mod")
        constraint_names = [c["name"] for c in model.get("complex_constraints", [])]
        assert "doc_file_validation" in constraint_names
        assert "doc_action_verify" not in constraint_names
        assert "doc_action_upload_new_version" not in constraint_names


# ---------------------------------------------------------------------------
# Tests: _inject_security_roles
# ---------------------------------------------------------------------------


class TestInjectSecurityRoles:
    def test_injects_all_roles(self) -> None:
        spec = _make_spec()
        roles = _inject_security_roles(spec, "test_mod", {})
        role_names = [r["name"] for r in roles]
        assert "viewer" in role_names
        assert "uploader" in role_names
        assert "verifier" in role_names
        assert "manager" in role_names

    def test_does_not_duplicate_existing_roles(self) -> None:
        spec = _make_spec(security_roles=[
            {"name": "viewer", "label": "Existing Viewer"},
        ])
        roles = _inject_security_roles(spec, "test_mod", {})
        viewer_count = sum(1 for r in roles if r["name"] == "viewer")
        assert viewer_count == 1

    def test_does_not_mutate_input(self) -> None:
        original_roles: list[dict[str, Any]] = [
            {"name": "viewer", "label": "Existing Viewer"},
        ]
        spec = _make_spec(security_roles=original_roles)
        roles = _inject_security_roles(spec, "test_mod", {})
        assert len(original_roles) == 1  # original unchanged
        assert len(roles) > 1

    def test_verification_disabled_omits_verifier(self) -> None:
        spec = _make_spec()
        config: dict[str, Any] = {"enable_verification": False}
        roles = _inject_security_roles(spec, "test_mod", config)
        role_names = [r["name"] for r in roles]
        assert "verifier" not in role_names
        # Manager should only imply uploader (not verifier)
        manager = next(r for r in roles if r["name"] == "manager")
        assert "group_test_mod_verifier" not in manager["implied_ids"]

    def test_manager_implies_verifier_when_enabled(self) -> None:
        spec = _make_spec()
        config: dict[str, Any] = {"enable_verification": True}
        roles = _inject_security_roles(spec, "test_mod", config)
        manager = next(r for r in roles if r["name"] == "manager")
        assert "group_test_mod_verifier" in manager["implied_ids"]
        assert "group_test_mod_uploader" in manager["implied_ids"]

    def test_xml_id_format(self) -> None:
        spec = _make_spec()
        roles = _inject_security_roles(spec, "my_module", {})
        viewer = next(r for r in roles if r["name"] == "viewer")
        assert viewer["xml_id"] == "group_my_module_viewer"


# ---------------------------------------------------------------------------
# Tests: _process_document_management (main entry)
# ---------------------------------------------------------------------------


class TestProcessDocumentManagement:
    def test_happy_path(self) -> None:
        spec = _make_spec(document_management=True)
        result = _process_document_management(spec)
        assert result is not spec  # immutability
        # Two new models appended
        model_names = [m["name"] for m in result["models"]]
        assert "document.type" in model_names
        assert "document.document" in model_names
        # Mail dependency injected
        assert "mail" in result["depends"]
        # Security roles injected
        role_names = [r["name"] for r in result.get("security_roles", [])]
        assert "viewer" in role_names
        assert "manager" in role_names

    def test_empty_spec_no_flag(self) -> None:
        """No document_management flag -> spec returned unchanged."""
        spec = _make_spec()
        result = _process_document_management(spec)
        assert result is spec  # same object returned

    def test_flag_false(self) -> None:
        spec = _make_spec(document_management=False)
        result = _process_document_management(spec)
        assert result is spec

    def test_preserves_existing_models(self) -> None:
        existing = {"name": "my.model", "fields": []}
        spec = _make_spec(document_management=True, models=[existing])
        result = _process_document_management(spec)
        model_names = [m["name"] for m in result["models"]]
        assert "my.model" in model_names
        assert "document.type" in model_names

    def test_mail_not_duplicated(self) -> None:
        spec = _make_spec(document_management=True, depends=["base", "mail"])
        result = _process_document_management(spec)
        assert result["depends"].count("mail") == 1

    def test_custom_config(self) -> None:
        config: dict[str, Any] = {
            "enable_versioning": False,
            "enable_verification": False,
        }
        spec = _make_spec(document_management=True, document_config=config)
        result = _process_document_management(spec)
        doc_model = next(m for m in result["models"] if m["name"] == "document.document")
        assert doc_model["has_document_versioning"] is False
        assert doc_model["has_document_verification"] is False

    def test_default_types_adds_extra_data_file(self) -> None:
        config: dict[str, Any] = {"default_types": ["transcript", "certificate"]}
        spec = _make_spec(document_management=True, document_config=config)
        result = _process_document_management(spec)
        assert "data/document_type_data.xml" in result.get("extra_data_files", [])

    def test_no_default_types_no_extra_data(self) -> None:
        spec = _make_spec(document_management=True, document_config={})
        result = _process_document_management(spec)
        assert "extra_data_files" not in result or "data/document_type_data.xml" not in result.get("extra_data_files", [])

    def test_missing_module_name_uses_default(self) -> None:
        spec: dict[str, Any] = {
            "models": [],
            "depends": ["base"],
            "document_management": True,
        }
        result = _process_document_management(spec)
        # Should not crash; uses "module" as default
        role_names = [r["name"] for r in result.get("security_roles", [])]
        assert "viewer" in role_names
        viewer = next(r for r in result["security_roles"] if r["name"] == "viewer")
        assert viewer["xml_id"] == "group_module_viewer"
