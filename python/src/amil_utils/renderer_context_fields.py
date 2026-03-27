"""Field-processing helpers for renderer context builders.

Extracted from renderer_context.py — handles field analysis, monetary detection,
topological sort wiring, hierarchical injection, and cross-cutting context
sub-builders (approval, audit, webhook, document, performance).
"""

from __future__ import annotations

from typing import Any

from amil_utils.renderer_utils import (
    _is_monetary_field,
    _to_python_var,
    _topologically_sort_fields,
    SEQUENCE_FIELD_NAMES,
)


# NEW-07: Known external Python packages mapping (pip_name -> import_name).
EXTERNAL_PACKAGES: dict[str, str] = {
    "openpyxl": "openpyxl",
    "requests": "requests",
    "boto3": "boto3",
    "paramiko": "paramiko",
    "pdfplumber": "pdfplumber",
    "xlsxwriter": "xlsxwriter",
    "phonenumbers": "phonenumbers",
    "cryptography": "cryptography",
    "Pillow": "PIL",
    "python-dateutil": "dateutil",
    "pytz": "pytz",
    "num2words": "num2words",
    "python-barcode": "barcode",
}


def _detect_external_dependencies(spec: dict[str, Any]) -> list[str]:
    """Scan model fields and method code for references to known external packages.

    Returns a deduplicated sorted list of pip package names that should be
    declared in the manifest's external_dependencies.python.
    """
    # Build a reverse mapping: import_name -> pip_name
    import_to_pip = {v: k for k, v in EXTERNAL_PACKAGES.items()}

    detected: set[str] = set()
    text_corpus: list[str] = []

    for model in spec.get("models", []):
        for field in model.get("fields", []):
            # Check field compute code, defaults, etc.
            for key in ("compute", "default", "onchange"):
                val = field.get(key, "")
                if val:
                    text_corpus.append(str(val))

        # Check complex_constraints, business_rules body text
        for cc in model.get("complex_constraints", []):
            body = cc.get("check_body", "")
            if body:
                text_corpus.append(body)

    # Check module-level business_rules
    for rule in spec.get("business_rules", []):
        text_corpus.append(str(rule))

    # Scan the text corpus for import references
    full_text = " ".join(text_corpus)
    for import_name, pip_name in import_to_pip.items():
        if import_name in full_text:
            detected.add(pip_name)

    # Also check if import_export is used (needs openpyxl)
    if any(m.get("import_export") for m in spec.get("models", [])):
        detected.add("openpyxl")

    # Check if archival models exist (needs python-dateutil)
    if any(m.get("is_archival") or m.get("archival") for m in spec.get("models", [])):
        detected.add("python-dateutil")

    return sorted(detected)


def _build_field_context(model: dict[str, Any], fields: list[dict[str, Any]]) -> dict[str, Any]:
    """Build field-analysis context: computed, constrained, sequence, monetary, etc."""
    computed_fields = [f for f in fields if f.get("compute")]
    if len(computed_fields) > 1:
        computed_fields = _topologically_sort_fields(computed_fields)
    onchange_fields = [f for f in fields if f.get("onchange")]
    constrained_fields = [f for f in fields if f.get("constrains")]
    sequence_fields = [
        f for f in fields
        if f.get("type") == "Char"
        and f.get("name") in SEQUENCE_FIELD_NAMES
        and f.get("required")
    ]
    state_field = next(
        (f for f in fields
         if f.get("name") in ("state", "status") and f.get("type") == "Selection"),
        None,
    )

    # Phase 26: monetary field detection (immutable rewrite)
    has_monetary = any(_is_monetary_field(f) for f in fields)
    if has_monetary:
        fields = [
            {**f, "type": "Monetary"} if _is_monetary_field(f) and f.get("type") == "Float" else f
            for f in fields
        ]
    has_currency_id = any(f.get("name") == "currency_id" for f in fields)

    # Phase 6: multi-company field detection
    has_company_field = any(
        f.get("name") == "company_id" and f.get("type") == "Many2one"
        for f in fields
    )

    # Phase 29: complex constraints from preprocessor
    complex_constraints = model.get("complex_constraints", [])
    has_constraints = any(
        f.get("constraints") for f in fields
    ) or bool(model.get("sql_constraints")) or bool(complex_constraints)

    return {
        "fields": fields,  # may be updated with monetary rewrites
        "computed_fields": computed_fields,
        "onchange_fields": onchange_fields,
        "constrained_fields": constrained_fields,
        "sequence_fields": sequence_fields,
        "sequence_field_names": list(SEQUENCE_FIELD_NAMES),
        "state_field": state_field,
        "has_computed": bool(computed_fields),
        "has_sequence_fields": bool(sequence_fields),
        "has_company_field": has_company_field,
        "workflow_states": model.get("workflow_states", []),
        "needs_currency_id": has_monetary and not has_currency_id,
        "has_constraints": has_constraints,
        "complex_constraints": complex_constraints,
        "create_constraints": model.get("create_constraints", []),
        "write_constraints": model.get("write_constraints", []),
        "has_create_override": bool(model.get("override_sources", {}).get("create")),
        "has_write_override": bool(model.get("override_sources", {}).get("write")),
    }


def _build_workflow_context(
    spec: dict[str, Any], model: dict[str, Any], fields: list[dict[str, Any]]
) -> dict[str, Any]:
    """Build inheritance and chatter context: mail.thread injection, hierarchy."""
    explicit_inherit = model.get("inherit")
    if isinstance(explicit_inherit, list):
        inherit_list = list(explicit_inherit)
    elif explicit_inherit:
        inherit_list = [explicit_inherit]
    else:
        inherit_list = []

    module_model_names = {m["name"] for m in spec.get("models", [])}

    is_line_item = any(
        f.get("type") == "Many2one"
        and f.get("required")
        and f.get("comodel_name") in module_model_names
        and f.get("name", "").endswith("_id")
        for f in fields
    )

    chatter = model.get("chatter")
    if chatter is None:
        chatter = not is_line_item

    if isinstance(explicit_inherit, list):
        parent_is_in_module = any(inh in module_model_names for inh in explicit_inherit)
    elif explicit_inherit:
        parent_is_in_module = explicit_inherit in module_model_names
    else:
        parent_is_in_module = False

    if chatter and "mail" in spec.get("depends", []) and not parent_is_in_module:
        for mixin in ("mail.thread", "mail.activity.mixin"):
            if mixin not in inherit_list:
                inherit_list.append(mixin)

    # Phase 27: hierarchical model detection
    is_hierarchical = model.get("hierarchical", False)
    if is_hierarchical:
        field_names_set = {f.get("name") for f in fields}
        hierarchical_injections: list[dict[str, Any]] = []
        if "parent_id" not in field_names_set:
            hierarchical_injections.append({
                "name": "parent_id", "type": "Many2one",
                "comodel_name": model["name"], "string": "Parent",
                "index": True, "ondelete": "cascade",
            })
        if "child_ids" not in field_names_set:
            hierarchical_injections.append({
                "name": "child_ids", "type": "One2many",
                "comodel_name": model["name"], "inverse_name": "parent_id",
                "string": "Children",
            })
        if "parent_path" not in field_names_set:
            hierarchical_injections.append({
                "name": "parent_path", "type": "Char",
                "index": True, "internal": True,
            })
        if hierarchical_injections:
            fields = [*fields, *hierarchical_injections]

    view_fields = [f for f in fields if not f.get("internal")]

    return {
        "fields": fields,  # may be extended with hierarchy injections
        "chatter": chatter,
        "inherit_list": inherit_list,
        "is_hierarchical": is_hierarchical,
        "view_fields": view_fields,
    }


def _build_approval_context(model: dict[str, Any]) -> dict[str, Any]:
    """Build approval workflow context keys."""
    return {
        "has_approval": model.get("has_approval", False),
        "approval_levels": model.get("approval_levels", []),
        "approval_action_methods": model.get("approval_action_methods", []),
        "approval_submit_action": model.get("approval_submit_action", None),
        "approval_reject_action": model.get("approval_reject_action", None),
        "approval_reset_action": model.get("approval_reset_action", None),
        "approval_state_field_name": model.get("approval_state_field_name", "state"),
        "lock_after": model.get("lock_after", "draft"),
        "editable_fields": model.get("editable_fields", []),
        "approval_record_rules": model.get("approval_record_rules", []),
        "on_reject": model.get("on_reject", "draft"),
        "reject_allowed_from": model.get("reject_allowed_from", []),
    }


def _build_audit_context(model: dict[str, Any]) -> dict[str, Any]:
    """Build audit trail context keys."""
    audit_fields = model.get("audit_fields", [])
    return {
        "has_audit": model.get("has_audit", False),
        "audit_fields": audit_fields,
        "audit_field_names": {f["name"] for f in audit_fields},
        "audit_exclude": model.get("audit_exclude", []),
    }


def _build_webhook_context(model: dict[str, Any]) -> dict[str, Any]:
    """Build webhook and notification context keys."""
    webhook_watched_fields = model.get("webhook_watched_fields", [])
    return {
        "has_notifications": model.get("has_notifications", False),
        "notification_templates": model.get("notification_templates", []),
        "needs_logger": model.get("needs_logger", False),
        "has_webhooks": model.get("has_webhooks", False),
        "webhook_config": model.get("webhook_config", None),
        "webhook_watched_fields": webhook_watched_fields,
        "webhook_on_create": model.get("webhook_on_create", False),
        "webhook_on_write": bool(webhook_watched_fields),
        "webhook_on_unlink": model.get("webhook_on_unlink", False),
    }


def _build_document_context(
    spec: dict[str, Any], model: dict[str, Any], complex_constraints: list[dict[str, Any]]
) -> dict[str, Any]:
    """Build document verification/versioning context keys."""
    has_document_verification = model.get("has_document_verification", False)
    document_verification_actions: list[dict[str, Any]] = model.get(
        "document_verification_actions", []
    )
    if has_document_verification and not document_verification_actions:
        module_name = spec.get("module_name", "module")
        _doc_action_map = {
            "doc_action_verify": {
                "name": "doc_action_verify", "button_label": "Verify",
                "button_class": "btn-primary", "visible_when": "pending",
                "group_xml_id": f"group_{module_name}_verifier",
            },
            "doc_action_reject": {
                "name": "doc_action_reject", "button_label": "Reject",
                "button_class": "btn-danger", "visible_when": "pending",
                "group_xml_id": f"group_{module_name}_verifier",
            },
            "doc_action_reset": {
                "name": "doc_action_reset", "button_label": "Reset to Pending",
                "button_class": "btn-secondary", "visible_when": "rejected",
                "group_xml_id": f"group_{module_name}_manager",
            },
        }
        for cc in complex_constraints:
            cc_name = cc.get("name", "")
            if cc_name in _doc_action_map:
                document_verification_actions.append(_doc_action_map[cc_name])

    return {
        "has_document_verification": has_document_verification,
        "document_verification_actions": document_verification_actions,
        "has_document_versioning": model.get("has_document_versioning", False),
        "document_version_action": model.get("document_version_action", None),
    }


def _build_performance_context(
    spec: dict[str, Any], model: dict[str, Any], cron_methods: list[dict[str, Any]]
) -> dict[str, Any]:
    """Build caching, archival, bulk, reporting, and dashboard context keys."""
    is_archival = model.get("is_archival", False)
    if is_archival:
        cron_methods = [c for c in cron_methods if c.get("method") != "_cron_archive_old_records"]

    has_dashboard = any(
        d.get("model_name") == model["name"]
        for d in spec.get("dashboards", [])
    )
    has_kanban = any(
        (d.get("kanban") or d.get("kanban_fields"))
        and d.get("model_name") == model["name"]
        for d in spec.get("dashboards", [])
    )
    model_reports = [
        r for r in spec.get("reports", [])
        if r.get("model_name") == model["name"]
    ]

    return {
        "cron_methods": cron_methods,
        "model_reports": model_reports,
        "has_dashboard": has_dashboard,
        "has_kanban": has_kanban,
        "is_bulk": model.get("is_bulk", False),
        "bulk_post_processing_batch_size": model.get("bulk_post_processing_batch_size"),
        "is_cacheable": model.get("is_cacheable", False),
        "cache_lookup_field": model.get("cache_lookup_field", "name"),
        "needs_tools": model.get("needs_tools", False),
        "is_archival": is_archival,
        "archival_batch_size": model.get("archival_batch_size", 100),
        "archival_days": model.get("archival_days", 365),
    }


def _compute_needs_api_and_translate(ctx: dict[str, Any], model: dict[str, Any]) -> dict[str, Any]:
    """Compute cross-cutting needs_api and needs_translate flags from assembled context."""
    complex_constraints = ctx["complex_constraints"]
    needs_translate = bool(complex_constraints)

    if ctx.get("has_approval") or ctx.get("has_document_verification"):
        needs_translate = True

    has_temporal = any(c.get("type") == "temporal" for c in complex_constraints)
    has_domain_constraints = any(
        c.get("type", "").startswith("pk_")
        or c.get("type", "").startswith("ac_year_")
        or c.get("type", "").startswith("ac_term_")
        or c.get("type", "").startswith("doc_file_")
        for c in complex_constraints
    )
    needs_api = bool(
        ctx["computed_fields"] or ctx["onchange_fields"] or ctx["constrained_fields"]
        or ctx["sequence_fields"] or has_temporal or ctx["has_create_override"]
        or ctx["cron_methods"] or ctx.get("is_bulk") or ctx.get("is_cacheable")
        or ctx.get("is_archival") or ctx.get("has_audit") or has_domain_constraints
    )

    return {"needs_api": needs_api, "needs_translate": needs_translate}


def _auto_display_name_pattern(fields: list[dict[str, Any]]) -> tuple[str, list[str]]:
    """Determine a display_name_pattern for models without a 'name' field.

    Returns (pattern, depends_fields) or ("", []) if no suitable pattern found.

    Strategy:
    1. Look for a 'reference' field -> use "{reference}" or "[{reference}] {first_char}"
    2. Otherwise use the first Char field -> "{first_char}"
    3. Fallback -> "#{id}"
    """
    field_names = {f.get("name") for f in fields}
    has_name = "name" in field_names

    if has_name:
        return ("", [])

    # Find reference field
    reference_field = next(
        (f for f in fields if f.get("name") == "reference" and f.get("type") == "Char"),
        None,
    )

    # Find first Char field (excluding reference, internal, parent_path)
    skip_names = {"reference", "parent_path"}
    first_char = next(
        (f for f in fields
         if f.get("type") == "Char"
         and f.get("name") not in skip_names
         and not f.get("internal")),
        None,
    )

    if reference_field and first_char:
        pattern = "[{" + reference_field["name"] + "}] {" + first_char["name"] + "}"
        return (pattern, [reference_field["name"], first_char["name"]])

    if reference_field:
        pattern = "{" + reference_field["name"] + "}"
        return (pattern, [reference_field["name"]])

    if first_char:
        pattern = "{" + first_char["name"] + "}"
        return (pattern, [first_char["name"]])

    # No suitable field — skip auto display_name (Odoo's default is fine)
    return ("", [])
