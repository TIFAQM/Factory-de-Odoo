"""Context builders for Jinja2 rendering of Odoo module templates.

Extracted from renderer.py -- builds model-level and module-level template
contexts consumed by the render stage functions.
"""

from __future__ import annotations

import re
from typing import Any

from amil_utils.renderer_utils import (
    _to_class,
    _to_python_var,
    _to_xml_id,
    SEQUENCE_FIELD_NAMES,
)

# Field-processing helpers (extracted to renderer_context_fields.py)
from amil_utils.renderer_context_fields import (  # noqa: F401 — re-exported for backward compat
    EXTERNAL_PACKAGES,
    _auto_display_name_pattern,
    _build_approval_context,
    _build_audit_context,
    _build_document_context,
    _build_field_context,
    _build_performance_context,
    _build_webhook_context,
    _build_workflow_context,
    _compute_needs_api_and_translate,
    _detect_external_dependencies,
)

# PIPE-07: Module-level constant for Odoo version-conditional model renames.
_VERSION_GATES: dict[str, dict[str, str]] = {
    "18.0": {
        "mail.channel": "discuss.channel",
        "mail.channel_all_employees": "discuss.channel_general",
    },
    "19.0": {
        "mail.channel": "discuss.channel",
        "mail.channel_all_employees": "discuss.channel_general",
        # Add any 19.0-specific renames here
    },
}


def _build_base_context(spec: dict[str, Any], model: dict[str, Any]) -> dict[str, Any]:
    """Build base context: module metadata, model identity, and basic field lists."""
    module_name = spec.get("module_name", "")
    fields = model.get("fields", [])

    # BUG-04: Wire sensitive flag to groups attribute
    for f in fields:
        if f.get("sensitive") and not f.get("groups"):
            f["groups"] = f"{module_name}.group_{module_name}_manager"

    return {
        "module_name": spec["module_name"],
        "module_title": spec.get("module_title", spec["module_name"].replace("_", " ").title()),
        "summary": spec.get("summary", ""),
        "author": spec.get("author", ""),
        "website": spec.get("website", ""),
        "license": spec.get("license", "LGPL-3"),
        "category": spec.get("category", "Uncategorized"),
        "odoo_version": spec.get("odoo_version", "19.0"),
        "depends": spec.get("depends", ["base"]),
        "application": spec.get("application", True),
        "models": spec.get("models", []),
        "model_name": model["name"],
        "model_description": model.get("description", model["name"]),
        "model_var": _to_python_var(model["name"]),
        "model_xml_id": _to_xml_id(model["name"]),
        "fields": fields,
        "required_fields": [f for f in fields if f.get("required")],
        "sql_constraints": model.get("sql_constraints", []),
        "inherit": model.get("inherit"),
        "wizards": spec.get("wizards", []),
        # Phase 33 keys
        "model_order": model.get("model_order", ""),
        "is_transient": model.get("transient", False),
        "transient_max_hours": model.get("transient_max_hours"),
        "transient_max_count": model.get("transient_max_count"),
        # Integration keys
        "model_workflow": next(
            (w for w in spec.get("workflow", [])
             if isinstance(w, dict) and w.get("model") == model["name"]),
            None,
        ),
        "composite_indexes": model.get("composite_indexes", []),
        "expected_examples": model.get("expected_examples", []),
    }


def _build_model_context(spec: dict[str, Any], model: dict[str, Any]) -> dict[str, Any]:
    """Build the template context for a single model from the module spec.

    PIPE-02: Delegates to focused sub-builders, then merges results.
    Order matters: field_ctx may update 'fields', workflow_ctx may extend it further.
    """
    ctx: dict[str, Any] = {}

    ctx.update(_build_base_context(spec, model))
    ctx.update(_build_field_context(model, ctx["fields"]))
    # workflow_ctx may extend fields (hierarchy injections) — use updated fields
    ctx.update(_build_workflow_context(spec, model, ctx["fields"]))
    ctx.update(_build_approval_context(model))
    ctx.update(_build_audit_context(model))
    ctx.update(_build_webhook_context(model))
    ctx.update(_build_document_context(spec, model, ctx["complex_constraints"]))

    cron_methods = [
        c for c in spec.get("cron_jobs", [])
        if c.get("model_name") == model["name"]
    ]
    ctx.update(_build_performance_context(spec, model, cron_methods))
    ctx.update(_compute_needs_api_and_translate(ctx, model))

    # TMPL-01: Related count stat buttons
    related_counts = model.get("related_counts", [])
    for rc in related_counts:
        ctx["computed_fields"].append({
            "name": rc["field"],
            "type": "Integer",
            "compute": f"_compute_{rc['field']}",
            "string": rc.get("label") or rc["field"].replace("_count", "").replace("_", " ").title(),
            "store": False,
        })
    ctx["related_counts"] = related_counts

    # TMPL-05: display_name_pattern -> _compute_display_name
    if pattern := model.get("display_name_pattern"):
        depends_fields = re.findall(r'\{(\w+)\}', pattern)
        ctx["display_name_pattern"] = pattern
        ctx["display_name_depends"] = depends_fields
    elif spec.get("odoo_version", "19.0") >= "19.0":
        # NEW-04: Auto _compute_display_name for nameless models (Odoo 19.0+)
        auto_pattern, auto_depends = _auto_display_name_pattern(ctx["fields"])
        if auto_pattern:
            ctx["display_name_pattern"] = auto_pattern
            ctx["display_name_depends"] = auto_depends

    return ctx


def _build_extension_context(
    spec: dict[str, Any], extension: dict[str, Any]
) -> dict[str, Any]:
    """Build template context for a single extension model (_inherit).

    Args:
        spec: Full module specification dictionary (preprocessed).
        extension: Single extension dict from spec["extends"].

    Returns:
        Context dictionary suitable for rendering extension_model.py.j2.
    """
    base_model = extension["base_model"]
    base_model_var = _to_python_var(base_model)
    class_name = _to_class(base_model)
    module_name = spec.get("module_name", "")

    fields = extension.get("add_fields", [])
    computed_fields = extension.get("add_computed", [])
    methods = extension.get("add_methods", [])

    # Build SQL constraints from add_constraints
    sql_constraints: list[dict[str, Any]] = []
    for constraint in extension.get("add_constraints", []):
        c_type = constraint.get("type", "check")
        c_fields = constraint.get("fields", [])
        c_name = constraint.get("name", "")
        c_rule = constraint.get("rule", "")

        if c_type == "unique":
            definition = f"UNIQUE({', '.join(c_fields)})"
        else:
            definition = f"CHECK({', '.join(c_fields)})"

        sql_constraints.append({
            "name": c_name,
            "definition": definition,
            "message": c_rule,
        })

    needs_api = bool(computed_fields or methods)

    return {
        "module_name": module_name,
        "base_model": base_model,
        "base_model_var": base_model_var,
        "class_name": class_name,
        "fields": fields,
        "computed_fields": computed_fields,
        "sql_constraints": sql_constraints,
        "methods": methods,
        "needs_api": needs_api,
    }


def _build_extension_view_context(
    spec: dict[str, Any],
    extension: dict[str, Any],
    view_ext: dict[str, Any],
) -> dict[str, Any]:
    """Build template context for a single extension view (xpath inheritance).

    Args:
        spec: Full module specification dictionary (preprocessed).
        extension: Single extension dict from spec["extends"].
        view_ext: Single view_extension dict from extension["view_extensions"].

    Returns:
        Context dictionary suitable for rendering extension_views.xml.j2.
    """
    base_model = extension["base_model"]
    base_model_var = _to_python_var(base_model)
    module_name = spec.get("module_name", "")
    base_view = view_ext.get("base_view", "")

    # Infer view_type from base_view suffix: "_form" -> "form", "_tree" -> "tree"
    view_type = "form"  # default
    for suffix in ("_form", "_tree", "_search", "_kanban", "_graph", "_pivot"):
        if suffix in base_view:
            view_type = suffix.lstrip("_")
            break

    view_record_id = f"view_{base_model_var}_{view_type}_inherit_{module_name}"
    view_name = f"{base_model}.{view_type}.inherit.{module_name}"
    inherit_id_ref = base_view

    # Process insertions
    insertions: list[dict[str, Any]] = []
    for ins in view_ext.get("insertions", []):
        if hasattr(ins, "model_dump"):
            ins_dict = ins.model_dump(exclude_none=True)
        else:
            ins_dict = dict(ins)
        insertions.append({
            "xpath": ins_dict.get("xpath", ""),
            "position": ins_dict.get("position", "after"),
            "fields": ins_dict.get("fields", []),
            "content": ins_dict.get("content"),
            "page_name": ins_dict.get("page_name"),
            "page_string": ins_dict.get("page_string"),
        })

    return {
        "module_name": module_name,
        "base_model": base_model,
        "model_name": base_model,
        "view_record_id": view_record_id,
        "view_name": view_name,
        "inherit_id_ref": inherit_id_ref,
        "insertions": insertions,
    }


def _compute_manifest_data(
    spec: dict[str, Any],
    data_files: list[str],
    wizard_view_files: list[str],
    has_company_modules: bool = False,
) -> list[str]:
    """Compute the canonical manifest data file list.

    Canonical load order:
    1. security/security.xml (groups, categories)
    2. security/ir.model.access.csv (ACLs reference groups)
    3. security/record_rules.xml (only if has_company_modules)
    4. data files (sequences, data, cron, reports, mail templates)
    5. wizard view files (define wizard actions -- BEFORE model views)
    6. per-model view files (*_views.xml, *_action.xml -- may reference wizard actions)
    7. dashboard view files (graph, pivot, kanban, cohort)
    8. views/menu.xml (references all actions -- LAST)

    Args:
        spec: Full module specification dictionary.
        data_files: List of data file paths relative to module root (e.g., ["data/sequences.xml"]).
        wizard_view_files: List of wizard view file paths (e.g., ["views/confirm_wizard_wizard_form.xml"]).
        has_company_modules: Whether any model has a company_id Many2one field.

    Returns:
        Ordered list of file paths for the manifest data section.
    """
    manifest_files: list[str] = [
        "security/security.xml",
        "security/ir.model.access.csv",
    ]
    if has_company_modules:
        manifest_files.append("security/record_rules.xml")

    manifest_files.extend(data_files)

    # Phase 40: mail template data file (after data files, before views)
    if any(m.get("has_notifications") for m in spec.get("models", [])):
        manifest_files.append("data/mail_template_data.xml")

    # Wizard views define ir.actions.act_window records that model views
    # may reference via %(module.wizard_action_id)d in button definitions.
    # They MUST be loaded before model views to avoid External ID errors.
    manifest_files.extend(wizard_view_files)

    for model in spec.get("models", []):
        model_var = _to_python_var(model["name"])
        manifest_files.append(f"views/{model_var}_views.xml")
        manifest_files.append(f"views/{model_var}_action.xml")

    # Phase 31: dashboard view files (after model views, before menu)
    dashboard_models_seen: set[str] = set()
    for dashboard in spec.get("dashboards", []):
        model_xml = _to_xml_id(dashboard["model_name"])
        if model_xml not in dashboard_models_seen:
            dashboard_models_seen.add(model_xml)
            manifest_files.append(f"views/{model_xml}_graph.xml")
            manifest_files.append(f"views/{model_xml}_pivot.xml")
            # Phase 63: kanban/cohort views (conditionally rendered by renderer.py)
            if dashboard.get("kanban") or dashboard.get("kanban_fields"):
                manifest_files.append(f"views/{model_xml}_kanban.xml")
            if dashboard.get("cohort_date_start"):
                manifest_files.append(f"views/{model_xml}_cohort.xml")

    manifest_files.append("views/menu.xml")

    return manifest_files


def _compute_view_files(spec: dict[str, Any]) -> list[str]:
    """Compute the list of view file paths for the manifest data section.

    Args:
        spec: Full module specification dictionary.

    Returns:
        List of view file relative paths (e.g., ["item_views.xml", ...]).
    """
    view_files = []
    for model in spec.get("models", []):
        model_var = _to_python_var(model["name"])
        view_files.append(f"{model_var}_views.xml")
        view_files.append(f"{model_var}_action.xml")
    view_files.append("menu.xml")
    return view_files


def _build_module_context(spec: dict[str, Any], module_name: str) -> dict[str, Any]:
    """Build the shared module-level template context from the spec."""
    models = spec.get("models", [])
    spec_wizards = spec.get("wizards", [])
    has_seq = any(
        any(f.get("type") == "Char" and f.get("name") in SEQUENCE_FIELD_NAMES and f.get("required")
            for f in m.get("fields", []))
        for m in models
    )
    has_company = any(
        any(f.get("name") == "company_id" and f.get("type") == "Many2one" for f in m.get("fields", []))
        for m in models
    )
    data_files: list[str] = []
    if has_seq:
        data_files.append("data/sequences.xml")
    data_files.append("data/data.xml")
    # Phase 30: cron data file
    if spec.get("cron_jobs"):
        data_files.append("data/cron_data.xml")
    # Phase 31: report data files
    for report in spec.get("reports", []):
        data_files.append(f"data/report_{report['xml_id']}.xml")
        data_files.append(f"data/report_{report['xml_id']}_template.xml")
    # Phase 49: extra data files from localization preprocessors
    data_files.extend(spec.get("extra_data_files", []))
    wiz_files = [f"views/{_to_xml_id(w['name'])}_wizard_form.xml" for w in spec_wizards]
    # Phase 32: import/export wizard detection
    import_export_models = [m for m in models if m.get("import_export")]
    has_import_export = bool(import_export_models)
    # Add import wizard form view files to manifest
    for m in import_export_models:
        wiz_files.append(f"views/{_to_xml_id(m['name'])}_import_wizard_form.xml")
    # Build import_export_wizards list for ACL generation
    import_export_wizards = [
        {"name": f"{m['name']}.import.wizard"} for m in import_export_models
    ]
    has_record_rules = any(m.get("record_rule_scopes") for m in models)
    manifest_files = _compute_manifest_data(
        spec, data_files, wiz_files,
        has_company_modules=has_company or has_record_rules,
    )
    # Phase 59: extension model files for init_models.py.j2
    extension_model_files = spec.get("extension_model_files", [])
    has_extensions = spec.get("has_extensions", False)

    # Phase 59: add extension view files to manifest_files
    if has_extensions:
        for ext in spec.get("extends", []):
            ext_base_var = _to_python_var(ext.get("base_model", ""))
            if ext.get("view_extensions"):
                manifest_files.append(f"views/{ext_base_var}_views.xml")

    # Phase 63: bulk operation manifest files
    has_bulk_operations = spec.get("has_bulk_operations", False)
    if has_bulk_operations:
        for bop in spec.get("bulk_operations", []):
            wiz_var = _to_python_var(bop["wizard_model"])
            manifest_files.append(f"views/{wiz_var}_wizard_form.xml")
        pass  # JS handled via manifest_assets below

    # Phase 62: portal manifest files
    has_portal = spec.get("has_portal", False)
    if has_portal:
        portal_pages = spec.get("portal_pages", [])
        portal_view_files: set[str] = set()
        for p in portal_pages:
            if p.get("show_in_home", True):
                portal_view_files.add("views/portal_home.xml")
            portal_view_files.add(f"views/portal_{p['id']}.xml")
        portal_view_files.add("security/portal_rules.xml")
        manifest_files.extend(sorted(portal_view_files))

    # Build asset bundle declarations (JS/CSS loaded via web.assets_backend)
    manifest_assets: list[dict[str, str]] = []
    if has_bulk_operations:
        manifest_assets.append({
            "bundle": "web.assets_backend",
            "path": "static/src/js/bulk_progress.js",
        })

    ctx: dict[str, Any] = {
        "module_name": module_name,
        "module_title": spec.get("module_title", module_name.replace("_", " ").title()),
        "module_technical_name": module_name,
        "summary": spec.get("summary", ""),
        "author": spec.get("author", ""),
        "website": spec.get("website", ""),
        "license": spec.get("license", "LGPL-3"),
        "category": spec.get("category", "Uncategorized"),
        "odoo_version": spec.get("odoo_version", "19.0"),
        "depends": spec.get("depends", ["base"]),
        "application": spec.get("application", True),
        "models": models,
        "view_files": _compute_view_files(spec),
        "manifest_files": manifest_files,
        "manifest_assets": manifest_assets,
        "has_wizards": bool(spec_wizards) or has_import_export,
        "spec_wizards": spec_wizards,
        "has_controllers": bool(spec.get("controllers")) or has_portal,
        "has_import_export": has_import_export,
        "import_export_wizards": import_export_wizards,
        "security_roles": spec.get("security_roles", []),
        "has_record_rules": has_record_rules,
        # Phase 38 keys
        "has_audit_log": spec.get("has_audit_log", False),
        # Phase 39 keys
        "has_approval_models": any(m.get("has_approval") for m in models),
        # Phase 40 keys
        "has_notification_models": any(m.get("has_notifications") for m in models),
        "has_webhook_models": any(m.get("has_webhooks") for m in models),
        # Phase 42: Context7 documentation hints (StrictUndefined-safe default)
        "c7_hints": {},
        # Phase 52 keys
        "has_document_models": any(m.get("has_document_verification") for m in models),
        # Phase 59: extension keys
        "extension_model_files": extension_model_files,
        "has_extensions": has_extensions,
        # Phase 62: portal keys
        "has_portal": has_portal,
        # Phase 63: bulk operation keys
        "has_bulk_operations": has_bulk_operations,
        # Integration keys (amil schema alignment)
        "workflows": spec.get("workflow", []),
        "business_rules": spec.get("business_rules", []),
        "view_hints": spec.get("view_hints", []),
    }
    # NEW-08: settings manifest files
    has_settings = bool(spec.get("settings"))
    if has_settings:
        manifest_files.append("views/res_config_settings_view.xml")
    ctx["has_settings"] = has_settings

    # Phase 52: VERSION_GATES for Odoo version-conditional template rendering (DOMN-04)
    ctx["version_gates"] = _VERSION_GATES

    # NEW-07: Auto-detect external dependencies from model code
    ext_deps = _detect_external_dependencies(spec)
    if ext_deps:
        ctx["external_dependencies"] = {"python": ext_deps}
    elif has_import_export:
        ctx["external_dependencies"] = {"python": ["openpyxl"]}
    return ctx
