"""Tests for renderer_context module — context builders for Jinja2 rendering."""
from __future__ import annotations

import pytest

from amil_utils.renderer_context import (
    _auto_display_name_pattern,
    _build_approval_context,
    _build_audit_context,
    _build_base_context,
    _build_document_context,
    _build_extension_context,
    _build_extension_view_context,
    _build_field_context,
    _build_model_context,
    _build_module_context,
    _build_performance_context,
    _build_webhook_context,
    _build_workflow_context,
    _compute_manifest_data,
    _compute_needs_api_and_translate,
    _compute_view_files,
    _detect_external_dependencies,
    EXTERNAL_PACKAGES,
)


# ---------------------------------------------------------------------------
# Helpers: Minimal spec / model factories
# ---------------------------------------------------------------------------

def _minimal_spec(**overrides):
    base = {
        "module_name": "test_mod",
        "models": [],
        "depends": ["base"],
    }
    return {**base, **overrides}


def _minimal_model(**overrides):
    base = {
        "name": "test.model",
        "fields": [],
    }
    return {**base, **overrides}


def _char_field(name, **kw):
    return {"name": name, "type": "Char", **kw}


def _float_field(name, **kw):
    return {"name": name, "type": "Float", **kw}


def _m2o_field(name, comodel, **kw):
    return {"name": name, "type": "Many2one", "comodel_name": comodel, **kw}


# ===========================================================================
# _detect_external_dependencies
# ===========================================================================


class TestDetectExternalDependencies:
    def test_no_models_returns_empty(self):
        spec = _minimal_spec()
        assert _detect_external_dependencies(spec) == []

    def test_detects_from_compute_code(self):
        spec = _minimal_spec(models=[
            _minimal_model(fields=[
                {"name": "data", "type": "Text", "compute": "import openpyxl"},
            ]),
        ])
        result = _detect_external_dependencies(spec)
        assert "openpyxl" in result

    def test_detects_from_complex_constraint_body(self):
        spec = _minimal_spec(models=[
            _minimal_model(
                fields=[],
                complex_constraints=[{"check_body": "requests.get(url)"}],
            ),
        ])
        result = _detect_external_dependencies(spec)
        assert "requests" in result

    def test_detects_from_business_rules(self):
        spec = _minimal_spec(
            business_rules=["use boto3 to upload files"],
        )
        result = _detect_external_dependencies(spec)
        assert "boto3" in result

    def test_import_export_adds_openpyxl(self):
        spec = _minimal_spec(models=[
            _minimal_model(import_export=True),
        ])
        result = _detect_external_dependencies(spec)
        assert "openpyxl" in result

    def test_archival_adds_dateutil(self):
        spec = _minimal_spec(models=[
            _minimal_model(is_archival=True),
        ])
        result = _detect_external_dependencies(spec)
        assert "python-dateutil" in result

    def test_result_is_sorted_and_deduplicated(self):
        spec = _minimal_spec(
            models=[_minimal_model(import_export=True)],
            business_rules=["use openpyxl again", "use boto3"],
        )
        result = _detect_external_dependencies(spec)
        assert result == sorted(set(result))


# ===========================================================================
# _build_base_context
# ===========================================================================


class TestBuildBaseContext:
    def test_happy_path_minimal(self):
        spec = _minimal_spec()
        model = _minimal_model()
        ctx = _build_base_context(spec, model)

        assert ctx["module_name"] == "test_mod"
        assert ctx["model_name"] == "test.model"
        assert ctx["model_var"] == "test_model"
        assert ctx["model_xml_id"] == "test_model"
        assert ctx["fields"] == []
        assert ctx["license"] == "LGPL-3"
        assert ctx["odoo_version"] == "19.0"

    def test_sensitive_field_gets_groups(self):
        spec = _minimal_spec()
        model = _minimal_model(fields=[
            {"name": "secret", "type": "Char", "sensitive": True},
        ])
        ctx = _build_base_context(spec, model)
        secret = ctx["fields"][0]
        assert secret["groups"] == "test_mod.group_test_mod_manager"

    def test_sensitive_field_preserves_existing_groups(self):
        spec = _minimal_spec()
        model = _minimal_model(fields=[
            {"name": "secret", "type": "Char", "sensitive": True, "groups": "base.group_system"},
        ])
        ctx = _build_base_context(spec, model)
        assert ctx["fields"][0]["groups"] == "base.group_system"

    def test_module_title_defaults_from_name(self):
        spec = _minimal_spec(module_name="hr_payroll")
        model = _minimal_model()
        ctx = _build_base_context(spec, model)
        assert ctx["module_title"] == "Hr Payroll"

    def test_required_fields_filtered(self):
        spec = _minimal_spec()
        model = _minimal_model(fields=[
            _char_field("name", required=True),
            _char_field("note"),
        ])
        ctx = _build_base_context(spec, model)
        assert len(ctx["required_fields"]) == 1
        assert ctx["required_fields"][0]["name"] == "name"

    def test_wizard_and_workflow_keys(self):
        wiz = {"name": "confirm.wizard"}
        wf = {"model": "test.model", "states": ["draft", "done"]}
        spec = _minimal_spec(wizards=[wiz], workflow=[wf])
        model = _minimal_model()
        ctx = _build_base_context(spec, model)
        assert ctx["wizards"] == [wiz]
        assert ctx["model_workflow"] == wf

    def test_model_workflow_none_when_no_match(self):
        spec = _minimal_spec(workflow=[{"model": "other.model"}])
        model = _minimal_model()
        ctx = _build_base_context(spec, model)
        assert ctx["model_workflow"] is None


# ===========================================================================
# _build_field_context
# ===========================================================================


class TestBuildFieldContext:
    def test_empty_fields(self):
        model = _minimal_model()
        ctx = _build_field_context(model, [])
        assert ctx["computed_fields"] == []
        assert ctx["onchange_fields"] == []
        assert ctx["constrained_fields"] == []
        assert ctx["sequence_fields"] == []
        assert ctx["state_field"] is None
        assert ctx["has_computed"] is False
        assert ctx["has_sequence_fields"] is False

    def test_computed_fields_detected(self):
        fields = [
            {"name": "total", "type": "Float", "compute": "_compute_total"},
        ]
        ctx = _build_field_context(_minimal_model(fields=fields), fields)
        assert len(ctx["computed_fields"]) == 1
        assert ctx["has_computed"] is True

    def test_sequence_fields_detected(self):
        fields = [
            {"name": "reference", "type": "Char", "required": True},
        ]
        ctx = _build_field_context(_minimal_model(fields=fields), fields)
        assert len(ctx["sequence_fields"]) == 1
        assert ctx["has_sequence_fields"] is True

    def test_sequence_field_requires_required_flag(self):
        fields = [
            {"name": "reference", "type": "Char"},  # not required
        ]
        ctx = _build_field_context(_minimal_model(fields=fields), fields)
        assert ctx["sequence_fields"] == []

    def test_state_field_detected(self):
        fields = [
            {"name": "state", "type": "Selection"},
        ]
        ctx = _build_field_context(_minimal_model(fields=fields), fields)
        assert ctx["state_field"] is not None
        assert ctx["state_field"]["name"] == "state"

    def test_monetary_float_rewrite(self):
        fields = [
            _float_field("total_amount"),
        ]
        ctx = _build_field_context(_minimal_model(fields=fields), fields)
        assert ctx["fields"][0]["type"] == "Monetary"
        assert ctx["needs_currency_id"] is True

    def test_monetary_with_existing_currency_id(self):
        fields = [
            _float_field("total_amount"),
            _m2o_field("currency_id", "res.currency"),
        ]
        ctx = _build_field_context(_minimal_model(fields=fields), fields)
        assert ctx["needs_currency_id"] is False

    def test_has_company_field(self):
        fields = [_m2o_field("company_id", "res.company")]
        ctx = _build_field_context(_minimal_model(fields=fields), fields)
        assert ctx["has_company_field"] is True

    def test_complex_constraints_from_model(self):
        model = _minimal_model(complex_constraints=[{"name": "check_date"}])
        ctx = _build_field_context(model, [])
        assert ctx["complex_constraints"] == [{"name": "check_date"}]
        assert ctx["has_constraints"] is True


# ===========================================================================
# _build_workflow_context
# ===========================================================================


class TestBuildWorkflowContext:
    def test_no_chatter_for_line_items(self):
        parent = _minimal_model(name="sale.order")
        spec = _minimal_spec(models=[parent, _minimal_model(name="sale.order.line")])
        line_model = _minimal_model(
            name="sale.order.line",
            fields=[_m2o_field("order_id", "sale.order", required=True)],
        )
        ctx = _build_workflow_context(spec, line_model, line_model["fields"])
        assert ctx["chatter"] is False

    def test_chatter_default_true_for_non_line_items(self):
        spec = _minimal_spec(models=[_minimal_model()])
        model = _minimal_model(fields=[_char_field("name")])
        ctx = _build_workflow_context(spec, model, model["fields"])
        assert ctx["chatter"] is True

    def test_explicit_chatter_override(self):
        spec = _minimal_spec(models=[_minimal_model()])
        model = _minimal_model(chatter=False, fields=[])
        ctx = _build_workflow_context(spec, model, model["fields"])
        assert ctx["chatter"] is False

    def test_inherit_list_from_string(self):
        spec = _minimal_spec()
        model = _minimal_model(inherit="base.model")
        ctx = _build_workflow_context(spec, model, [])
        assert "base.model" in ctx["inherit_list"]

    def test_inherit_list_from_list(self):
        spec = _minimal_spec()
        model = _minimal_model(inherit=["a.model", "b.model"])
        ctx = _build_workflow_context(spec, model, [])
        assert ctx["inherit_list"] == ["a.model", "b.model"]

    def test_mail_thread_injected_when_chatter_and_mail_dep(self):
        spec = _minimal_spec(depends=["base", "mail"])
        model = _minimal_model(chatter=True)
        ctx = _build_workflow_context(spec, model, [])
        assert "mail.thread" in ctx["inherit_list"]
        assert "mail.activity.mixin" in ctx["inherit_list"]

    def test_hierarchical_field_injection(self):
        spec = _minimal_spec()
        model = _minimal_model(hierarchical=True)
        ctx = _build_workflow_context(spec, model, [])
        injected_names = {f["name"] for f in ctx["fields"]}
        assert "parent_id" in injected_names
        assert "child_ids" in injected_names
        assert "parent_path" in injected_names
        assert ctx["is_hierarchical"] is True

    def test_hierarchical_no_duplicate_injection(self):
        spec = _minimal_spec()
        fields = [
            _m2o_field("parent_id", "test.model"),
        ]
        model = _minimal_model(hierarchical=True, fields=fields)
        ctx = _build_workflow_context(spec, model, fields)
        parent_count = sum(1 for f in ctx["fields"] if f["name"] == "parent_id")
        assert parent_count == 1

    def test_view_fields_excludes_internal(self):
        spec = _minimal_spec()
        fields = [
            _char_field("name"),
            _char_field("parent_path", internal=True),
        ]
        model = _minimal_model(fields=fields)
        ctx = _build_workflow_context(spec, model, fields)
        view_names = {f["name"] for f in ctx["view_fields"]}
        assert "name" in view_names
        assert "parent_path" not in view_names


# ===========================================================================
# _build_approval_context
# ===========================================================================


class TestBuildApprovalContext:
    def test_defaults(self):
        model = _minimal_model()
        ctx = _build_approval_context(model)
        assert ctx["has_approval"] is False
        assert ctx["approval_levels"] == []
        assert ctx["approval_state_field_name"] == "state"
        assert ctx["lock_after"] == "draft"
        assert ctx["on_reject"] == "draft"

    def test_with_approval_data(self):
        model = _minimal_model(
            has_approval=True,
            approval_levels=[{"name": "manager"}],
            approval_state_field_name="approval_state",
        )
        ctx = _build_approval_context(model)
        assert ctx["has_approval"] is True
        assert len(ctx["approval_levels"]) == 1
        assert ctx["approval_state_field_name"] == "approval_state"


# ===========================================================================
# _build_audit_context
# ===========================================================================


class TestBuildAuditContext:
    def test_defaults(self):
        ctx = _build_audit_context(_minimal_model())
        assert ctx["has_audit"] is False
        assert ctx["audit_fields"] == []
        assert ctx["audit_field_names"] == set()
        assert ctx["audit_exclude"] == []

    def test_with_audit_fields(self):
        model = _minimal_model(
            has_audit=True,
            audit_fields=[{"name": "amount"}, {"name": "state"}],
        )
        ctx = _build_audit_context(model)
        assert ctx["has_audit"] is True
        assert ctx["audit_field_names"] == {"amount", "state"}


# ===========================================================================
# _build_webhook_context
# ===========================================================================


class TestBuildWebhookContext:
    def test_defaults(self):
        ctx = _build_webhook_context(_minimal_model())
        assert ctx["has_webhooks"] is False
        assert ctx["webhook_on_create"] is False
        assert ctx["webhook_on_write"] is False
        assert ctx["webhook_on_unlink"] is False

    def test_with_webhook_config(self):
        model = _minimal_model(
            has_webhooks=True,
            webhook_config={"url": "https://example.com"},
            webhook_watched_fields=["state", "amount"],
            webhook_on_create=True,
            webhook_on_unlink=True,
        )
        ctx = _build_webhook_context(model)
        assert ctx["has_webhooks"] is True
        assert ctx["webhook_on_write"] is True  # inferred from watched_fields
        assert ctx["webhook_on_create"] is True
        assert ctx["webhook_on_unlink"] is True

    def test_webhook_on_write_false_without_watched_fields(self):
        model = _minimal_model(has_webhooks=True, webhook_watched_fields=[])
        ctx = _build_webhook_context(model)
        assert ctx["webhook_on_write"] is False


# ===========================================================================
# _build_document_context
# ===========================================================================


class TestBuildDocumentContext:
    def test_defaults(self):
        ctx = _build_document_context(_minimal_spec(), _minimal_model(), [])
        assert ctx["has_document_verification"] is False
        assert ctx["document_verification_actions"] == []
        assert ctx["has_document_versioning"] is False

    def test_auto_generates_verification_actions(self):
        model = _minimal_model(has_document_verification=True)
        constraints = [
            {"name": "doc_action_verify"},
            {"name": "doc_action_reject"},
        ]
        ctx = _build_document_context(_minimal_spec(), model, constraints)
        assert ctx["has_document_verification"] is True
        assert len(ctx["document_verification_actions"]) == 2
        action_names = {a["name"] for a in ctx["document_verification_actions"]}
        assert "doc_action_verify" in action_names
        assert "doc_action_reject" in action_names

    def test_uses_existing_actions(self):
        existing = [{"name": "custom", "button_label": "Custom"}]
        model = _minimal_model(
            has_document_verification=True,
            document_verification_actions=existing,
        )
        ctx = _build_document_context(_minimal_spec(), model, [])
        assert ctx["document_verification_actions"] == existing


# ===========================================================================
# _build_performance_context
# ===========================================================================


class TestBuildPerformanceContext:
    def test_defaults(self):
        spec = _minimal_spec()
        model = _minimal_model()
        ctx = _build_performance_context(spec, model, [])
        assert ctx["is_bulk"] is False
        assert ctx["is_cacheable"] is False
        assert ctx["is_archival"] is False
        assert ctx["model_reports"] == []
        assert ctx["has_dashboard"] is False

    def test_archival_removes_archive_cron(self):
        model = _minimal_model(is_archival=True)
        cron = [{"method": "_cron_archive_old_records"}, {"method": "_cron_other"}]
        ctx = _build_performance_context(_minimal_spec(), model, cron)
        assert len(ctx["cron_methods"]) == 1
        assert ctx["cron_methods"][0]["method"] == "_cron_other"

    def test_dashboard_detection(self):
        spec = _minimal_spec(dashboards=[{"model_name": "test.model"}])
        model = _minimal_model()
        ctx = _build_performance_context(spec, model, [])
        assert ctx["has_dashboard"] is True

    def test_kanban_detection(self):
        spec = _minimal_spec(dashboards=[
            {"model_name": "test.model", "kanban": True},
        ])
        model = _minimal_model()
        ctx = _build_performance_context(spec, model, [])
        assert ctx["has_kanban"] is True

    def test_reports_filtered_by_model(self):
        spec = _minimal_spec(reports=[
            {"model_name": "test.model", "xml_id": "r1"},
            {"model_name": "other.model", "xml_id": "r2"},
        ])
        model = _minimal_model()
        ctx = _build_performance_context(spec, model, [])
        assert len(ctx["model_reports"]) == 1
        assert ctx["model_reports"][0]["xml_id"] == "r1"


# ===========================================================================
# _compute_needs_api_and_translate
# ===========================================================================


class TestComputeNeedsApiAndTranslate:
    def _base_ctx(self, **overrides):
        ctx = {
            "complex_constraints": [],
            "computed_fields": [],
            "onchange_fields": [],
            "constrained_fields": [],
            "sequence_fields": [],
            "has_create_override": False,
            "cron_methods": [],
        }
        return {**ctx, **overrides}

    def test_no_needs(self):
        ctx = self._base_ctx()
        result = _compute_needs_api_and_translate(ctx, _minimal_model())
        assert result["needs_api"] is False
        assert result["needs_translate"] is False

    def test_computed_fields_need_api(self):
        ctx = self._base_ctx(computed_fields=[{"name": "total"}])
        result = _compute_needs_api_and_translate(ctx, _minimal_model())
        assert result["needs_api"] is True

    def test_complex_constraints_need_translate(self):
        ctx = self._base_ctx(complex_constraints=[{"type": "temporal"}])
        result = _compute_needs_api_and_translate(ctx, _minimal_model())
        assert result["needs_translate"] is True
        assert result["needs_api"] is True  # temporal constraint

    def test_approval_needs_translate(self):
        ctx = self._base_ctx(has_approval=True)
        result = _compute_needs_api_and_translate(ctx, _minimal_model())
        assert result["needs_translate"] is True

    def test_cron_methods_need_api(self):
        ctx = self._base_ctx(cron_methods=[{"method": "_cron_cleanup"}])
        result = _compute_needs_api_and_translate(ctx, _minimal_model())
        assert result["needs_api"] is True


# ===========================================================================
# _auto_display_name_pattern
# ===========================================================================


class TestAutoDisplayNamePattern:
    def test_has_name_field_returns_empty(self):
        fields = [_char_field("name")]
        pattern, deps = _auto_display_name_pattern(fields)
        assert pattern == ""
        assert deps == []

    def test_reference_and_char_field(self):
        fields = [
            _char_field("reference"),
            _char_field("title"),
        ]
        pattern, deps = _auto_display_name_pattern(fields)
        assert pattern == "[{reference}] {title}"
        assert deps == ["reference", "title"]

    def test_reference_only(self):
        fields = [_char_field("reference")]
        pattern, deps = _auto_display_name_pattern(fields)
        assert pattern == "{reference}"
        assert deps == ["reference"]

    def test_first_char_field_no_reference(self):
        fields = [_char_field("title")]
        pattern, deps = _auto_display_name_pattern(fields)
        assert pattern == "{title}"
        assert deps == ["title"]

    def test_no_suitable_fields_returns_empty(self):
        fields = [
            {"name": "amount", "type": "Float"},
            {"name": "partner_id", "type": "Many2one"},
        ]
        pattern, deps = _auto_display_name_pattern(fields)
        assert pattern == ""
        assert deps == []

    def test_skips_internal_char_fields(self):
        fields = [
            _char_field("parent_path", internal=True),
            _char_field("description"),
        ]
        pattern, deps = _auto_display_name_pattern(fields)
        assert pattern == "{description}"
        assert deps == ["description"]


# ===========================================================================
# _build_model_context (integration of sub-builders)
# ===========================================================================


class TestBuildModelContext:
    def test_minimal_model(self):
        spec = _minimal_spec(models=[_minimal_model()])
        model = _minimal_model()
        ctx = _build_model_context(spec, model)

        assert ctx["module_name"] == "test_mod"
        assert ctx["model_name"] == "test.model"
        assert "needs_api" in ctx
        assert "needs_translate" in ctx

    def test_display_name_pattern_explicit(self):
        spec = _minimal_spec(models=[_minimal_model()])
        model = _minimal_model(display_name_pattern="[{ref}] {name}")
        ctx = _build_model_context(spec, model)
        assert ctx["display_name_pattern"] == "[{ref}] {name}"
        assert ctx["display_name_depends"] == ["ref", "name"]

    def test_related_counts_appended(self):
        spec = _minimal_spec(models=[_minimal_model()])
        model = _minimal_model(
            related_counts=[{"field": "task_count", "label": "Tasks"}],
        )
        ctx = _build_model_context(spec, model)
        assert any(f["name"] == "task_count" for f in ctx["computed_fields"])
        assert ctx["related_counts"] == model["related_counts"]


# ===========================================================================
# _build_extension_context
# ===========================================================================


class TestBuildExtensionContext:
    def test_happy_path(self):
        ext = {
            "base_model": "res.partner",
            "add_fields": [_char_field("custom_ref")],
            "add_computed": [],
            "add_methods": [],
        }
        ctx = _build_extension_context(_minimal_spec(), ext)
        assert ctx["base_model"] == "res.partner"
        assert ctx["class_name"] == "ResPartner"
        assert ctx["base_model_var"] == "res_partner"
        assert ctx["needs_api"] is False

    def test_needs_api_with_computed(self):
        ext = {
            "base_model": "res.partner",
            "add_fields": [],
            "add_computed": [{"name": "full_name", "compute": "_compute_full_name"}],
            "add_methods": [],
        }
        ctx = _build_extension_context(_minimal_spec(), ext)
        assert ctx["needs_api"] is True

    def test_sql_constraints_unique(self):
        ext = {
            "base_model": "res.partner",
            "add_fields": [],
            "add_computed": [],
            "add_methods": [],
            "add_constraints": [
                {"type": "unique", "fields": ["vat", "company_id"],
                 "name": "unique_vat", "rule": "VAT must be unique"},
            ],
        }
        ctx = _build_extension_context(_minimal_spec(), ext)
        assert len(ctx["sql_constraints"]) == 1
        assert "UNIQUE" in ctx["sql_constraints"][0]["definition"]

    def test_sql_constraints_check(self):
        ext = {
            "base_model": "res.partner",
            "add_fields": [],
            "add_computed": [],
            "add_methods": [],
            "add_constraints": [
                {"type": "check", "fields": ["age > 0"],
                 "name": "check_age", "rule": "Age must be positive"},
            ],
        }
        ctx = _build_extension_context(_minimal_spec(), ext)
        assert "CHECK" in ctx["sql_constraints"][0]["definition"]


# ===========================================================================
# _build_extension_view_context
# ===========================================================================


class TestBuildExtensionViewContext:
    def test_happy_path(self):
        ext = {"base_model": "res.partner"}
        view_ext = {
            "base_view": "base.view_partner_form",
            "insertions": [
                {"xpath": "//field[@name='phone']", "position": "after",
                 "fields": [_char_field("custom_phone")]},
            ],
        }
        ctx = _build_extension_view_context(_minimal_spec(), ext, view_ext)
        assert ctx["base_model"] == "res.partner"
        assert ctx["inherit_id_ref"] == "base.view_partner_form"
        assert "form" in ctx["view_record_id"]
        assert len(ctx["insertions"]) == 1

    def test_view_type_from_tree_suffix(self):
        ext = {"base_model": "res.partner"}
        view_ext = {"base_view": "base.view_partner_tree", "insertions": []}
        ctx = _build_extension_view_context(_minimal_spec(), ext, view_ext)
        assert "tree" in ctx["view_record_id"]

    def test_view_type_default_form(self):
        ext = {"base_model": "res.partner"}
        view_ext = {"base_view": "base.view_partner_custom", "insertions": []}
        ctx = _build_extension_view_context(_minimal_spec(), ext, view_ext)
        assert "form" in ctx["view_record_id"]

    def test_empty_insertions(self):
        ext = {"base_model": "res.partner"}
        view_ext = {"base_view": "base.view_partner_form", "insertions": []}
        ctx = _build_extension_view_context(_minimal_spec(), ext, view_ext)
        assert ctx["insertions"] == []


# ===========================================================================
# _compute_manifest_data
# ===========================================================================


class TestComputeManifestData:
    def test_basic_order(self):
        spec = _minimal_spec(models=[_minimal_model()])
        result = _compute_manifest_data(spec, [], [])
        assert result[0] == "security/security.xml"
        assert result[1] == "security/ir.model.access.csv"
        assert result[-1] == "views/menu.xml"

    def test_company_adds_record_rules(self):
        spec = _minimal_spec(models=[_minimal_model()])
        result = _compute_manifest_data(spec, [], [], has_company_modules=True)
        assert "security/record_rules.xml" in result

    def test_data_files_before_views(self):
        spec = _minimal_spec(models=[_minimal_model()])
        result = _compute_manifest_data(spec, ["data/sequences.xml"], [])
        seq_idx = result.index("data/sequences.xml")
        view_idx = result.index("views/test_model_views.xml")
        assert seq_idx < view_idx

    def test_wizard_files_before_model_views(self):
        spec = _minimal_spec(models=[_minimal_model()])
        wiz_files = ["views/confirm_wizard_wizard_form.xml"]
        result = _compute_manifest_data(spec, [], wiz_files)
        wiz_idx = result.index("views/confirm_wizard_wizard_form.xml")
        view_idx = result.index("views/test_model_views.xml")
        assert wiz_idx < view_idx

    def test_notification_adds_mail_template(self):
        model = _minimal_model(has_notifications=True)
        spec = _minimal_spec(models=[model])
        result = _compute_manifest_data(spec, [], [])
        assert "data/mail_template_data.xml" in result

    def test_dashboard_files(self):
        spec = _minimal_spec(
            models=[_minimal_model()],
            dashboards=[{"model_name": "test.model"}],
        )
        result = _compute_manifest_data(spec, [], [])
        assert "views/test_model_graph.xml" in result
        assert "views/test_model_pivot.xml" in result


# ===========================================================================
# _compute_view_files
# ===========================================================================


class TestComputeViewFiles:
    def test_single_model(self):
        spec = _minimal_spec(models=[_minimal_model()])
        result = _compute_view_files(spec)
        assert "test_model_views.xml" in result
        assert "test_model_action.xml" in result
        assert result[-1] == "menu.xml"

    def test_no_models(self):
        spec = _minimal_spec()
        result = _compute_view_files(spec)
        assert result == ["menu.xml"]


# ===========================================================================
# _build_module_context
# ===========================================================================


class TestBuildModuleContext:
    def test_minimal(self):
        spec = _minimal_spec(models=[_minimal_model()])
        ctx = _build_module_context(spec, "test_mod")
        assert ctx["module_name"] == "test_mod"
        assert ctx["has_wizards"] is False
        assert ctx["has_controllers"] is False
        assert "manifest_files" in ctx
        assert "view_files" in ctx

    def test_with_wizards(self):
        spec = _minimal_spec(
            models=[_minimal_model()],
            wizards=[{"name": "confirm.wizard"}],
        )
        ctx = _build_module_context(spec, "test_mod")
        assert ctx["has_wizards"] is True

    def test_import_export_detected(self):
        spec = _minimal_spec(models=[_minimal_model(import_export=True)])
        ctx = _build_module_context(spec, "test_mod")
        assert ctx["has_import_export"] is True
        assert ctx["has_wizards"] is True
        assert "openpyxl" in ctx["external_dependencies"]["python"]

    def test_sequence_data_file(self):
        model = _minimal_model(fields=[
            _char_field("reference", required=True),
        ])
        spec = _minimal_spec(models=[model])
        ctx = _build_module_context(spec, "test_mod")
        assert "data/sequences.xml" in ctx["manifest_files"]

    def test_version_gates_present(self):
        spec = _minimal_spec(models=[_minimal_model()])
        ctx = _build_module_context(spec, "test_mod")
        assert "version_gates" in ctx
        assert "18.0" in ctx["version_gates"]

    def test_settings_adds_view(self):
        spec = _minimal_spec(
            models=[_minimal_model()],
            settings={"key": "val"},
        )
        ctx = _build_module_context(spec, "test_mod")
        assert ctx["has_settings"] is True
        assert "views/res_config_settings_view.xml" in ctx["manifest_files"]

    def test_portal_adds_view_files(self):
        spec = _minimal_spec(
            models=[_minimal_model()],
            has_portal=True,
            portal_pages=[{"id": "my_page", "show_in_home": True}],
        )
        ctx = _build_module_context(spec, "test_mod")
        assert ctx["has_portal"] is True
        assert ctx["has_controllers"] is True
