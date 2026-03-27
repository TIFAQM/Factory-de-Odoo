"""Tests for audit trail preprocessor."""

from __future__ import annotations

import pytest

from amil_utils.preprocessors.audit import _process_audit_patterns


def _make_spec(
    *,
    models=None,
    security_roles=None,
    module_name="test_module",
):
    """Build a minimal spec dict for testing."""
    return {
        "module_name": module_name,
        "models": models or [],
        "security_roles": security_roles or [],
    }


def _make_model(
    *,
    name="test.record",
    description="Test Record",
    fields=None,
    audit=False,
    audit_exclude=None,
):
    """Build a minimal model dict."""
    result = {
        "name": name,
        "description": description,
        "fields": fields or [
            {"name": "name", "type": "Char"},
            {"name": "amount", "type": "Float"},
            {"name": "notes", "type": "Text"},
        ],
        "audit": audit,
    }
    if audit_exclude is not None:
        result["audit_exclude"] = audit_exclude
    return result


class TestProcessAuditPatterns:
    """Tests for _process_audit_patterns preprocessor."""

    def test_happy_path_basic_audit(self):
        """Model with audit=True gets full audit enrichment."""
        roles = [{"name": "manager", "label": "Manager", "is_highest": True}]
        model = _make_model(audit=True)
        spec = _make_spec(models=[model], security_roles=roles)

        result = _process_audit_patterns(spec)

        assert result is not spec
        enriched = result["models"][0]

        # Model-level flags
        assert enriched["has_audit"] is True
        assert enriched["has_write_override"] is True
        assert enriched["has_create_override"] is True
        assert "audit" in enriched["override_sources"]["write"]
        assert "audit" in enriched["override_sources"]["create"]

        # Audit fields computed (excludes auto-excluded types/fields)
        field_names = [f["name"] for f in enriched["audit_fields"]]
        assert "name" in field_names
        assert "amount" in field_names

        # Default config values
        assert enriched["audit_retention_days"] == 365 * 7
        assert enriched["audit_track_ip"] is True
        assert enriched["audit_track_old_values"] is True

        # audit.trail.log companion model synthesized
        log_model = result["models"][-1]
        assert log_model["name"] == "audit.trail.log"
        assert log_model["_synthesized"] is True
        assert log_model["_is_audit_log"] is True

        # Has export capabilities
        assert log_model["has_export_method"] is True
        assert "csv" in log_model["export_formats"]

        # Spec-level flag
        assert result["has_audit_log"] is True

    def test_auditor_role_injected(self):
        """If no auditor role exists, one is created."""
        roles = [{"name": "manager", "label": "Manager", "is_highest": True}]
        model = _make_model(audit=True)
        spec = _make_spec(models=[model], security_roles=roles)

        result = _process_audit_patterns(spec)

        role_names = [r["name"] for r in result["security_roles"]]
        assert "auditor" in role_names
        auditor = next(r for r in result["security_roles"] if r["name"] == "auditor")
        assert auditor["label"] == "Auditor"
        assert auditor["xml_id"] == "group_test_module_auditor"

    def test_auditor_role_not_duplicated(self):
        """If auditor role already exists, it is not duplicated."""
        roles = [
            {"name": "manager", "label": "Manager", "is_highest": True},
            {"name": "auditor", "label": "Auditor", "xml_id": "existing_auditor"},
        ]
        model = _make_model(audit=True)
        spec = _make_spec(models=[model], security_roles=roles)

        result = _process_audit_patterns(spec)

        auditor_roles = [r for r in result["security_roles"] if r["name"] == "auditor"]
        assert len(auditor_roles) == 1

    def test_audit_acl_read_only_for_auditor_and_highest(self):
        """Audit log ACL grants read-only to auditor and highest role."""
        roles = [
            {"name": "user", "label": "User", "is_highest": False},
            {"name": "manager", "label": "Manager", "is_highest": True},
        ]
        model = _make_model(audit=True)
        spec = _make_spec(models=[model], security_roles=roles)

        result = _process_audit_patterns(spec)

        log_model = result["models"][-1]
        acl = log_model["security_acl"]

        # Find ACL entries by role
        acl_by_role = {a["role"]: a for a in acl}

        # auditor gets read
        assert acl_by_role["auditor"]["perm_read"] == 1
        assert acl_by_role["auditor"]["perm_write"] == 0

        # manager (highest) gets read
        assert acl_by_role["manager"]["perm_read"] == 1
        assert acl_by_role["manager"]["perm_write"] == 0

        # user gets nothing
        assert acl_by_role["user"]["perm_read"] == 0

    def test_empty_spec_no_audited_models(self):
        """No audited models returns spec unchanged."""
        model = _make_model(audit=False)
        spec = _make_spec(models=[model])

        result = _process_audit_patterns(spec)

        assert result is spec

    def test_empty_models_list(self):
        """Empty models list returns spec unchanged."""
        spec = _make_spec(models=[])

        result = _process_audit_patterns(spec)

        assert result is spec

    def test_auto_exclude_fields(self):
        """write_date, write_uid, message_ids, activity_ids are auto-excluded."""
        fields = [
            {"name": "name", "type": "Char"},
            {"name": "write_date", "type": "Datetime"},
            {"name": "write_uid", "type": "Many2one"},
            {"name": "message_ids", "type": "One2many"},
            {"name": "activity_ids", "type": "One2many"},
        ]
        roles = [{"name": "manager", "label": "Manager", "is_highest": True}]
        model = _make_model(audit=True, fields=fields)
        spec = _make_spec(models=[model], security_roles=roles)

        result = _process_audit_patterns(spec)
        enriched = result["models"][0]

        audit_field_names = [f["name"] for f in enriched["audit_fields"]]
        assert "name" in audit_field_names
        assert "write_date" not in audit_field_names
        assert "write_uid" not in audit_field_names
        assert "message_ids" not in audit_field_names
        assert "activity_ids" not in audit_field_names

    def test_skip_types_excluded(self):
        """One2many, Many2many, Binary fields are excluded from audit."""
        fields = [
            {"name": "name", "type": "Char"},
            {"name": "tags", "type": "Many2many"},
            {"name": "children", "type": "One2many"},
            {"name": "photo", "type": "Binary"},
        ]
        roles = [{"name": "manager", "label": "Manager", "is_highest": True}]
        model = _make_model(audit=True, fields=fields)
        spec = _make_spec(models=[model], security_roles=roles)

        result = _process_audit_patterns(spec)
        enriched = result["models"][0]

        audit_field_names = [f["name"] for f in enriched["audit_fields"]]
        assert "name" in audit_field_names
        assert "tags" not in audit_field_names
        assert "children" not in audit_field_names
        assert "photo" not in audit_field_names

    def test_custom_audit_exclude(self):
        """Spec-level audit_exclude removes specific fields."""
        roles = [{"name": "manager", "label": "Manager", "is_highest": True}]
        model = _make_model(audit=True, audit_exclude=["notes"])
        spec = _make_spec(models=[model], security_roles=roles)

        result = _process_audit_patterns(spec)
        enriched = result["models"][0]

        audit_field_names = [f["name"] for f in enriched["audit_fields"]]
        assert "notes" not in audit_field_names
        assert "name" in audit_field_names

    def test_audit_config_dict(self):
        """Audit config as dict overrides defaults."""
        roles = [{"name": "manager", "label": "Manager", "is_highest": True}]
        model = _make_model(fields=[{"name": "name", "type": "Char"}])
        model["audit"] = {
            "retention_days": 90,
            "track_ip": False,
            "track_old_values": False,
        }
        spec = _make_spec(models=[model], security_roles=roles)

        result = _process_audit_patterns(spec)
        enriched = result["models"][0]

        assert enriched["audit_retention_days"] == 90
        assert enriched["audit_track_ip"] is False
        assert enriched["audit_track_old_values"] is False

    def test_immutability_original_not_mutated(self):
        """Original spec and model dicts are not mutated."""
        roles = [{"name": "manager", "label": "Manager", "is_highest": True}]
        model = _make_model(audit=True)
        spec = _make_spec(models=[model], security_roles=roles)

        original_model_keys = set(model.keys())
        original_roles_len = len(roles)

        _process_audit_patterns(spec)

        assert set(model.keys()) == original_model_keys
        assert "has_audit" not in model
        assert len(roles) == original_roles_len

    def test_non_audited_models_passed_through(self):
        """Models without audit=True are included unchanged."""
        roles = [{"name": "manager", "label": "Manager", "is_highest": True}]
        plain = _make_model(name="test.plain", audit=False)
        audited = _make_model(name="test.audited", audit=True)
        spec = _make_spec(models=[plain, audited], security_roles=roles)

        result = _process_audit_patterns(spec)

        # plain model unchanged, then audited model, then audit.trail.log
        assert result["models"][0]["name"] == "test.plain"
        assert result["models"][0].get("has_audit") is None
        assert result["models"][1]["name"] == "test.audited"
        assert result["models"][1]["has_audit"] is True
        assert result["models"][2]["name"] == "audit.trail.log"

    def test_audit_log_model_fields(self):
        """Audit log companion model has required fields."""
        roles = [{"name": "manager", "label": "Manager", "is_highest": True}]
        model = _make_model(audit=True)
        spec = _make_spec(models=[model], security_roles=roles)

        result = _process_audit_patterns(spec)
        log_model = result["models"][-1]

        field_names = [f["name"] for f in log_model["fields"]]
        assert "res_model" in field_names
        assert "res_id" in field_names
        assert "changes" in field_names
        assert "old_values" in field_names
        assert "new_values" in field_names
        assert "user_id" in field_names
        assert "operation" in field_names
        assert "timestamp" in field_names
        assert "ip_address" in field_names
        assert "field_names" in field_names

    def test_audit_exclude_sorted(self):
        """audit_exclude on enriched model is sorted."""
        roles = [{"name": "manager", "label": "Manager", "is_highest": True}]
        model = _make_model(audit=True, audit_exclude=["notes", "amount"])
        spec = _make_spec(models=[model], security_roles=roles)

        result = _process_audit_patterns(spec)
        enriched = result["models"][0]

        excludes = enriched["audit_exclude"]
        assert excludes == sorted(excludes)
