"""Jinja2 rendering engine with Odoo-specific filters for module scaffolding."""

from __future__ import annotations

import json
import logging
import re
import shutil
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from amil_utils.validation.types import Result
from amil_utils.version_defaults import get_default_version

from amil_utils.renderer_utils import (
    _is_monetary_field,
    _model_ref,
    _to_class,
    _to_python_var,
    _to_xml_id,
    _topologically_sort_fields,
    INDEXABLE_TYPES,
    MONETARY_FIELD_PATTERNS,
    NON_INDEXABLE_TYPES,
    SEQUENCE_FIELD_NAMES,
)

from amil_utils.preprocessors import run_preprocessors
from amil_utils.preprocessors._registry import get_registered_preprocessors
from amil_utils.preprocessors.validation import _validate_no_cycles
from amil_utils.spec_schema import validate_spec

from amil_utils.manifest import (
    ArtifactEntry,
    ArtifactInfo,
    GenerationSession,
    PreprocessingInfo,
    StageResult,
    compute_file_sha256,
    compute_spec_sha256,
    load_manifest,
)
from amil_utils.hooks import RenderHook, notify_hooks, CheckpointPause

from amil_utils.context7 import build_context7_from_env, context7_enrich

from amil_utils.renderer_context import (
    _build_extension_context,
    _build_extension_view_context,
    _build_model_context,
    _build_module_context,
    _compute_manifest_data,
    _compute_view_files,
)

if TYPE_CHECKING:
    from amil_utils.manifest import GenerationManifest
    from amil_utils.verifier import EnvironmentVerifier, VerificationWarning

_logger = logging.getLogger("amil.renderer")

STAGE_NAMES: list[str] = [
    "manifest", "models", "extensions", "views", "security", "mail_templates",
    "wizards", "tests", "static", "cron", "reports", "controllers", "portal",
    "bulk",
]


def _artifacts_intact(manifest: "GenerationManifest", stage_name: str, module_dir: Path) -> bool:
    """Check if all artifacts for a stage still exist with matching SHA256."""
    stage_result = manifest.stages.get(stage_name)
    if not stage_result or not stage_result.artifacts:
        return False
    for rel_path in stage_result.artifacts:
        full_path = module_dir / rel_path
        if not full_path.exists():
            return False
        try:
            actual_sha = compute_file_sha256(full_path)
            # Find matching artifact entry in manifest
            entry = next((e for e in manifest.artifacts.files if e.path == rel_path), None)
            if entry and actual_sha != entry.sha256:
                return False
        except Exception:
            return False
    return True






def _register_filters(env: Environment) -> Environment:
    """Register Odoo-specific Jinja2 filters on an Environment.

    Args:
        env: Jinja2 Environment to register filters on.

    Returns:
        The same Environment with filters registered.
    """
    env.filters["model_ref"] = _model_ref
    env.filters["to_class"] = _to_class
    env.filters["to_python_var"] = _to_python_var
    env.filters["to_xml_id"] = _to_xml_id
    return env


def create_versioned_renderer(version: str) -> Environment:
    """Create a Jinja2 Environment that loads version-specific then shared templates.

    Uses a FileSystemLoader with a fallback chain: version-specific directory first,
    then shared directory. Templates in the version directory override shared ones.

    Args:
        version: Odoo version string (e.g., "17.0", "18.0", "19.0").

    Returns:
        Configured Jinja2 Environment with versioned template loading.
    """
    base = Path(__file__).parent / "templates"
    version_dir = str(base / version)
    shared_dir = str(base / "shared")
    env = Environment(
        loader=FileSystemLoader([version_dir, shared_dir]),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    return _register_filters(env)


def create_renderer(template_dir: Path) -> Environment:
    """Create a Jinja2 Environment configured for Odoo module rendering.

    Uses StrictUndefined to fail loudly on missing template variables (Pitfall 1 prevention).
    Registers custom filters for Odoo-specific name conversions.

    If template_dir is the base templates directory (containing 17.0/, 18.0/, 19.0/, shared/
    subdirectories), falls back to create_versioned_renderer("19.0") for backward
    compatibility after the template reorganization in Phase 9.

    Args:
        template_dir: Path to the directory containing .j2 template files.

    Returns:
        Configured Jinja2 Environment.
    """
    # Detect if this is the base templates dir (reorganized layout)
    base_templates = Path(__file__).parent / "templates"
    if template_dir.resolve() == base_templates.resolve():
        return create_versioned_renderer(get_default_version())

    env = Environment(
        loader=FileSystemLoader(str(template_dir)),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    return _register_filters(env)


def render_template(
    env: Environment,
    template_name: str,
    output_path: Path,
    context: dict[str, Any],
) -> Path:
    """Render a single Jinja2 template to a file.

    Creates parent directories as needed.

    Args:
        env: Jinja2 Environment with loaded templates.
        template_name: Name of the template file (e.g., "manifest.py.j2").
        output_path: Destination file path for the rendered output.
        context: Dictionary of template variables.

    Returns:
        The output_path where the rendered file was written.
    """
    template = env.get_template(template_name)
    content = template.render(**context)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding="utf-8")
    return output_path


def get_template_dir() -> Path:
    """Return the path to the bundled templates directory.

    The templates are shipped alongside this module in the templates/ subdirectory.

    Returns:
        Absolute path to the templates directory.
    """
    return Path(__file__).parent / "templates"


def render_manifest(
    env: Environment,
    spec: dict[str, Any],
    module_dir: Path,
    module_context: dict[str, Any],
) -> Result[list[Path]]:
    """Render __manifest__.py, root __init__.py, and models/__init__.py.

    Args:
        env: Configured Jinja2 Environment.
        spec: Full module specification dictionary.
        module_dir: Path to the module directory.
        module_context: Shared module-level template context.

    Returns:
        Result containing list of created file Paths on success.
    """
    try:
        created: list[Path] = []
        created.append(
            render_template(env, "manifest.py.j2", module_dir / "__manifest__.py", module_context)
        )
        created.append(
            render_template(env, "init_root.py.j2", module_dir / "__init__.py", module_context)
        )
        created.append(
            render_template(env, "init_models.py.j2", module_dir / "models" / "__init__.py", module_context)
        )
        return Result.ok(created)
    except Exception as exc:
        return Result.fail(f"render_manifest failed: {exc}")


def render_models(
    env: Environment,
    spec: dict[str, Any],
    module_dir: Path,
    module_context: dict[str, Any],
    verifier: "EnvironmentVerifier | None" = None,
    warnings_out: list | None = None,
) -> Result[list[Path]]:
    """Render per-model .py files, views, and action files.

    Args:
        env: Configured Jinja2 Environment.
        spec: Full module specification dictionary.
        module_dir: Path to the module directory.
        module_context: Shared module-level template context.
        verifier: Optional EnvironmentVerifier for inline verification.
        warnings_out: Optional mutable list to collect verification warnings into.

    Returns:
        Result containing list of created file Paths on success.
    """
    try:
        models = spec.get("models", [])
        created: list[Path] = []

        for model in models:
            model_ctx = _build_model_context(spec, model)
            model_var = _to_python_var(model["name"])

            if verifier is not None:
                model_result = verifier.verify_model_spec(model)
                if model_result.success and warnings_out is not None:
                    warnings_out.extend(model_result.data or [])

            created.append(
                render_template(env, "model.py.j2", module_dir / "models" / f"{model_var}.py", model_ctx)
            )
            created.append(
                render_template(env, "view_form.xml.j2", module_dir / "views" / f"{model_var}_views.xml", model_ctx)
            )

            if verifier is not None:
                field_names = [f.get("name", "") for f in model.get("fields", [])]
                view_result = verifier.verify_view_spec(model.get("name", ""), field_names)
                if view_result.success and warnings_out is not None:
                    warnings_out.extend(view_result.data or [])

            created.append(
                render_template(env, "action.xml.j2", module_dir / "views" / f"{model_var}_action.xml", model_ctx)
            )

        return Result.ok(created)
    except Exception as exc:
        return Result.fail(f"render_models failed: {exc}")


def render_extensions(
    env: Environment,
    spec: dict[str, Any],
    module_dir: Path,
    module_context: dict[str, Any],
) -> Result[list[Path]]:
    """Render extension model .py files and view .xml files for _inherit extensions.

    Iterates over spec["extends"] to produce:
    - models/{base_model_var}.py with _inherit class
    - views/{base_model_var}_views.xml with xpath inheritance (when view_extensions exist)

    Returns Result.ok([]) when no extensions are present.

    Args:
        env: Configured Jinja2 Environment.
        spec: Full module specification dictionary (preprocessed).
        module_dir: Path to the module directory.
        module_context: Shared module-level template context.

    Returns:
        Result containing list of created file Paths on success.
    """
    extends = spec.get("extends", [])
    if not extends:
        return Result.ok([])

    try:
        created: list[Path] = []

        for ext in extends:
            base_model_var = _to_python_var(ext["base_model"])

            # Render extension model .py
            ext_ctx = _build_extension_context(spec, ext)
            created.append(
                render_template(
                    env,
                    "extension_model.py.j2",
                    module_dir / "models" / f"{base_model_var}.py",
                    ext_ctx,
                )
            )

            # Render extension views .xml (if view_extensions exist)
            view_extensions = ext.get("view_extensions", [])
            if view_extensions:
                views: list[dict[str, Any]] = []
                for ve in view_extensions:
                    view_ctx = _build_extension_view_context(spec, ext, ve)
                    views.append(view_ctx)

                created.append(
                    render_template(
                        env,
                        "extension_views.xml.j2",
                        module_dir / "views" / f"{base_model_var}_views.xml",
                        {"views": views},
                    )
                )

        return Result.ok(created)
    except Exception as exc:
        return Result.fail(f"render_extensions failed: {exc}")


def render_views(
    env: Environment,
    spec: dict[str, Any],
    module_dir: Path,
    module_context: dict[str, Any],
) -> Result[list[Path]]:
    """Render views/menu.xml for all models.

    Args:
        env: Configured Jinja2 Environment.
        spec: Full module specification dictionary.
        module_dir: Path to the module directory.
        module_context: Shared module-level template context.

    Returns:
        Result containing list of created file Paths on success.
    """
    try:
        created: list[Path] = []
        created.append(
            render_template(env, "menu.xml.j2", module_dir / "views" / "menu.xml", module_context)
        )
        return Result.ok(created)
    except Exception as exc:
        return Result.fail(f"render_views failed: {exc}")


def render_security(
    env: Environment,
    spec: dict[str, Any],
    module_dir: Path,
    module_context: dict[str, Any],
) -> Result[list[Path]]:
    """Render security files: security.xml, ir.model.access.csv, optional record_rules.xml.

    Args:
        env: Configured Jinja2 Environment.
        spec: Full module specification dictionary.
        module_dir: Path to the module directory.
        module_context: Shared module-level template context.

    Returns:
        Result containing list of created file Paths on success.
    """
    try:
        created: list[Path] = []
        created.append(
            render_template(env, "security_group.xml.j2", module_dir / "security" / "security.xml", module_context)
        )
        created.append(
            render_template(env, "access_csv.j2", module_dir / "security" / "ir.model.access.csv", module_context)
        )
        # Phase 37: render record_rules.xml when any model has record_rule_scopes
        has_record_rules = module_context.get("has_record_rules", False)
        if has_record_rules:
            created.append(render_template(
                env, "record_rules.xml.j2", module_dir / "security" / "record_rules.xml",
                module_context,
            ))
        return Result.ok(created)
    except Exception as exc:
        return Result.fail(f"render_security failed: {exc}")


def render_wizards(
    env: Environment,
    spec: dict[str, Any],
    module_dir: Path,
    module_context: dict[str, Any],
) -> Result[list[Path]]:
    """Render wizard files: wizards/__init__.py, per-wizard .py, per-wizard form XML.

    Args:
        env: Configured Jinja2 Environment.
        spec: Full module specification dictionary.
        module_dir: Path to the module directory.
        module_context: Shared module-level template context.

    Returns:
        Result containing list of created file Paths on success (empty if no wizards).
    """
    try:
        spec_wizards = spec.get("wizards", [])
        if not spec_wizards:
            return Result.ok([])
        created: list[Path] = []
        created.append(
            render_template(env, "init_wizards.py.j2", module_dir / "wizards" / "__init__.py", {**module_context})
        )
        for wizard in spec_wizards:
            wvar = _to_python_var(wizard["name"])
            wxid = _to_xml_id(wizard["name"])
            wctx = {**module_context, "wizard": wizard, "wizard_var": wvar,
                    "wizard_xml_id": wxid, "wizard_class": _to_class(wizard["name"]), "needs_api": True,
                    "transient_max_hours": wizard.get("transient_max_hours"),
                    "transient_max_count": wizard.get("transient_max_count")}
            py_template = wizard.get("template", "wizard.py.j2")
            form_template = wizard.get("form_template", "wizard_form.xml.j2")
            created.append(render_template(env, py_template, module_dir / "wizards" / f"{wvar}.py", wctx))
            created.append(render_template(
                env, form_template, module_dir / "views" / f"{wxid}_wizard_form.xml", wctx))
        return Result.ok(created)
    except Exception as exc:
        return Result.fail(f"render_wizards failed: {exc}")


def render_tests(
    env: Environment,
    spec: dict[str, Any],
    module_dir: Path,
    module_context: dict[str, Any],
) -> Result[list[Path]]:
    """Render tests/__init__.py and per-model test files.

    Args:
        env: Configured Jinja2 Environment.
        spec: Full module specification dictionary.
        module_dir: Path to the module directory.
        module_context: Shared module-level template context.

    Returns:
        Result containing list of created file Paths on success.
    """
    try:
        created: list[Path] = []
        created.append(
            render_template(env, "init_tests.py.j2", module_dir / "tests" / "__init__.py", module_context)
        )
        for model in spec.get("models", []):
            model_ctx = _build_model_context(spec, model)
            model_var = _to_python_var(model["name"])
            created.append(
                render_template(env, "test_model.py.j2", module_dir / "tests" / f"test_{model_var}.py", model_ctx)
            )
            if spec.get("has_portal"):
                created.append(
                    render_template(env, "test_portal.py.j2", module_dir / "tests" / f"test_portal_{model_var}.py", model_ctx)
                )
        return Result.ok(created)
    except Exception as exc:
        return Result.fail(f"render_tests failed: {exc}")


_PKR_CURRENCY_XML = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    '<odoo>\n'
    '    <data noupdate="0">\n'
    '        <!-- Activate Pakistani Rupee from base module -->\n'
    '        <record id="base.PKR" model="res.currency" forcecreate="false">\n'
    '            <field name="active" eval="True"/>\n'
    '        </record>\n'
    '    </data>\n'
    '</odoo>\n'
)


def _render_document_type_xml(
    doc_types: list[dict[str, Any]], module_name: str
) -> str:
    """Generate noupdate XML records for document type seed data.

    Args:
        doc_types: List of document type dicts with name, code, required_for, etc.
        module_name: Module technical name for XML ID prefix.

    Returns:
        XML string with <odoo><data noupdate="1"> records.
    """
    lines: list[str] = [
        '<?xml version="1.0" encoding="utf-8"?>',
        "<odoo>",
        '    <data noupdate="1">',
    ]
    from markupsafe import escape as xml_escape

    for dt in doc_types:
        code = xml_escape(dt.get("code", ""))
        xml_id = f"{module_name}.document_type_{code}"
        lines.append(f'        <record id="{xml_id}" model="document.type">')
        lines.append(f'            <field name="name">{xml_escape(dt.get("name", ""))}</field>')
        lines.append(f'            <field name="code">{code}</field>')
        if "required_for" in dt:
            lines.append(f'            <field name="required_for">{xml_escape(str(dt["required_for"]))}</field>')
        if "max_file_size" in dt:
            lines.append(f'            <field name="max_file_size" eval="{xml_escape(str(dt["max_file_size"]))}"/>')
        if "allowed_mime_types" in dt:
            lines.append(f'            <field name="allowed_mime_types">{xml_escape(str(dt["allowed_mime_types"]))}</field>')
        lines.append("        </record>")
    lines.append("    </data>")
    lines.append("</odoo>")
    lines.append("")
    return "\n".join(lines)


def _render_extra_data_files(spec: dict[str, Any], module_dir: Path) -> list[Path]:
    """Render extra data files injected by localization preprocessors (Phase 49)."""
    created: list[Path] = []
    for extra_file in spec.get("extra_data_files", []):
        extra_path = module_dir / extra_file
        extra_path.parent.mkdir(parents=True, exist_ok=True)
        if extra_file == "data/pk_currency_data.xml":
            extra_path.write_text(_PKR_CURRENCY_XML, encoding="utf-8")
            created.append(extra_path)
        elif extra_file == "data/document_type_data.xml":
            # Phase 52: document type seed data from preprocessor
            doc_types = spec.get("_document_type_seed_data", [])
            if not doc_types:
                # Fall back to document_config.default_types
                doc_types = spec.get("document_config", {}).get("default_types", [])
            if doc_types:
                xml_content = _render_document_type_xml(
                    doc_types, spec.get("module_name", "module")
                )
                extra_path.write_text(xml_content, encoding="utf-8")
                created.append(extra_path)
    return created


def render_static(
    env: Environment,
    spec: dict[str, Any],
    module_dir: Path,
    module_context: dict[str, Any],
) -> Result[list[Path]]:
    """Render data.xml, sequences.xml, demo data, static/index.html, and README.rst.

    Args:
        env: Configured Jinja2 Environment.
        spec: Full module specification dictionary.
        module_dir: Path to the module directory.
        module_context: Shared module-level template context.

    Returns:
        Result containing list of created file Paths on success.
    """
    try:
        models = spec.get("models", [])
        created: list[Path] = []
        # data/data.xml stub
        data_xml_path = module_dir / "data" / "data.xml"
        data_xml_path.parent.mkdir(parents=True, exist_ok=True)
        data_xml_path.write_text(
            '<?xml version="1.0" encoding="utf-8"?>\n<odoo>\n'
            "    <!-- Static data records go here -->\n</odoo>\n",
            encoding="utf-8",
        )
        created.append(data_xml_path)
        # sequences.xml if needed
        seq_models = [
            m for m in models
            if any(f.get("type") == "Char" and f.get("name") in SEQUENCE_FIELD_NAMES and f.get("required")
                   for f in m.get("fields", []))
        ]
        if seq_models:
            seq_ctx = {
                **module_context,
                "sequence_models": [
                    {"model": m, "model_var": _to_python_var(m["name"]),
                     "sequence_fields": [f for f in m.get("fields", [])
                                         if f.get("type") == "Char" and f.get("name") in SEQUENCE_FIELD_NAMES
                                         and f.get("required")]}
                    for m in seq_models
                ],
            }
            created.append(render_template(env, "sequences.xml.j2", module_dir / "data" / "sequences.xml", seq_ctx))
        # demo data
        created.append(render_template(env, "demo_data.xml.j2", module_dir / "demo" / "demo_data.xml", module_context))
        # static/description/index.html
        static_dir = module_dir / "static" / "description"
        static_dir.mkdir(parents=True, exist_ok=True)
        index_html = static_dir / "index.html"
        index_html.write_text(
            '<!DOCTYPE html>\n<html>\n<head><title>Module Description</title></head>\n'
            '<body><p>See README.rst for module documentation.</p></body>\n</html>\n',
            encoding="utf-8",
        )
        created.append(index_html)
        # README.rst
        created.append(render_template(env, "readme.rst.j2", module_dir / "README.rst", module_context))
        # Phase 49: extra data files (e.g., Pakistan PKR currency activation)
        created.extend(_render_extra_data_files(spec, module_dir))
        return Result.ok(created)
    except Exception as exc:
        return Result.fail(f"render_static failed: {exc}")


def render_cron(
    env: Environment,
    spec: dict[str, Any],
    module_dir: Path,
    module_context: dict[str, Any],
) -> "Result[list[Path]]":
    """Render ir.cron scheduled action XML from spec cron_jobs.

    Validates method names are valid Python identifiers.
    Returns Result.ok([]) when no cron_jobs are present.
    """
    cron_jobs = spec.get("cron_jobs")
    if not cron_jobs:
        return Result.ok([])
    # Validate method names
    for cron in cron_jobs:
        method = cron.get("method", "")
        if not method.isidentifier():
            return Result.fail(
                f"Invalid cron method name '{method}': must be a valid Python identifier"
            )
    cron_ctx = {**module_context, "cron_jobs": cron_jobs}
    try:
        path = render_template(env, "cron_data.xml.j2", module_dir / "data" / "cron_data.xml", cron_ctx)
        return Result.ok([path])
    except Exception as exc:
        return Result.fail(f"render_cron failed: {exc}")


def render_reports(
    env: Environment,
    spec: dict[str, Any],
    module_dir: Path,
    module_context: dict[str, Any],
) -> "Result[list[Path]]":
    """Render QWeb report templates and graph/pivot dashboard views.

    Handles two spec sections:
    - spec["reports"]: ir.actions.report + QWeb template + optional paper format
    - spec["dashboards"]: graph view + pivot view per model

    Returns Result.ok([]) when neither section is present.
    """
    reports = spec.get("reports", [])
    dashboards = spec.get("dashboards", [])
    if not reports and not dashboards:
        return Result.ok([])
    try:
        created: list[Path] = []
        for report in reports:
            report_ctx = {**module_context, "report": report}
            created.append(render_template(
                env, "report_action.xml.j2",
                module_dir / "data" / f"report_{report['xml_id']}.xml",
                report_ctx,
            ))
            created.append(render_template(
                env, "report_template.xml.j2",
                module_dir / "data" / f"report_{report['xml_id']}_template.xml",
                report_ctx,
            ))
        for dashboard in dashboards:
            model_xml = _to_xml_id(dashboard["model_name"])
            dash_ctx = {**module_context, "dashboard": dashboard, "model_xml_id": model_xml}
            created.append(render_template(
                env, "graph_view.xml.j2",
                module_dir / "views" / f"{model_xml}_graph.xml",
                dash_ctx,
            ))
            created.append(render_template(
                env, "pivot_view.xml.j2",
                module_dir / "views" / f"{model_xml}_pivot.xml",
                dash_ctx,
            ))
            if dashboard.get("kanban") or dashboard.get("kanban_fields"):
                created.append(render_template(
                    env, "view_kanban.xml.j2",
                    module_dir / "views" / f"{model_xml}_kanban.xml",
                    dash_ctx,
                ))
            if dashboard.get("cohort_date_start"):
                created.append(render_template(
                    env, "view_cohort.xml.j2",
                    module_dir / "views" / f"{model_xml}_cohort.xml",
                    dash_ctx,
                ))
        return Result.ok(created)
    except Exception as exc:
        return Result.fail(f"render_reports failed: {exc}")


def render_controllers(
    env: Environment,
    spec: dict[str, Any],
    module_dir: Path,
    module_context: dict[str, Any],
) -> "Result[list[Path]]":
    """Render HTTP controller files and import/export wizard files.

    Generates controllers/main.py with @http.route decorators and
    controllers/__init__.py for each controller definition.
    Also generates import wizard .py and form XML for models with import_export:true.
    """
    try:
        created: list[Path] = []
        module_name = module_context["module_name"]

        # --- HTTP controllers ---
        controllers = spec.get("controllers")
        if controllers:
            for controller in controllers:
                class_name = controller.get("class_name") or (
                    _to_class(module_name) + "Controller"
                )
                routes = controller.get("routes", [])
                ctrl_ctx = {
                    **module_context,
                    "controller_class": class_name,
                    "routes": routes,
                    "module_name": module_name,
                }
                created.append(render_template(
                    env, "init_controllers.py.j2",
                    module_dir / "controllers" / "__init__.py",
                    ctrl_ctx,
                ))
                created.append(render_template(
                    env, "controller.py.j2",
                    module_dir / "controllers" / "main.py",
                    ctrl_ctx,
                ))

        # --- Import/export wizards ---
        import_export_models = [
            m for m in spec.get("models", []) if m.get("import_export")
        ]
        if import_export_models:
            import_wizard_modules: list[str] = []
            for model in import_export_models:
                model_name = model["name"]
                model_var = _to_python_var(model_name)
                model_xml_id = _to_xml_id(model_name)
                model_class = _to_class(model_name) + "ImportWizard"
                model_description = model.get(
                    "description", model_name.replace(".", " ").title()
                )
                # Non-relational, non-internal fields for export headers
                export_fields = [
                    f for f in model.get("fields", [])
                    if f.get("type") not in (
                        "Many2one", "One2many", "Many2many", "Binary",
                    )
                ]
                wiz_ctx = {
                    **module_context,
                    "model_name": model_name,
                    "model_var": model_var,
                    "model_xml_id": model_xml_id,
                    "wizard_class": model_class,
                    "model_description": model_description,
                    "export_fields": export_fields,
                    "transient_max_hours": model.get("transient_max_hours", 1.0),
                    "transient_max_count": model.get("transient_max_count", 0),
                }
                wizard_filename = f"{model_var}_import_wizard"
                import_wizard_modules.append(wizard_filename)
                created.append(render_template(
                    env, "import_wizard.py.j2",
                    module_dir / "wizards" / f"{wizard_filename}.py",
                    wiz_ctx,
                ))
                created.append(render_template(
                    env, "import_wizard_form.xml.j2",
                    module_dir / "views" / f"{model_xml_id}_import_wizard_form.xml",
                    wiz_ctx,
                ))
            # Render or update wizards/__init__.py with import wizard imports
            # Combine existing spec_wizards with import wizard modules
            existing_wizard_imports = [
                _to_python_var(w["name"])
                for w in module_context.get("spec_wizards", [])
            ]
            all_wizard_imports = existing_wizard_imports + import_wizard_modules
            init_content = "\n".join(
                f"from . import {name}" for name in all_wizard_imports
            ) + "\n"
            init_path = module_dir / "wizards" / "__init__.py"
            init_path.parent.mkdir(parents=True, exist_ok=True)
            init_path.write_text(init_content, encoding="utf-8")
            created.append(init_path)

        return Result.ok(created)
    except Exception as exc:
        return Result.fail(f"render_controllers failed: {exc}")


def render_portal(
    env: Environment,
    spec: dict[str, Any],
    module_dir: Path,
    module_context: dict[str, Any],
) -> "Result[list[Path]]":
    """Render portal controller, QWeb templates, and record rules.

    Generates:
    - controllers/portal.py (CustomerPortal subclass)
    - controllers/__init__.py updated with portal import
    - views/portal_home.xml (home counter entries)
    - views/portal_{page_id}.xml per page (list/detail/editable templates)
    - security/portal_rules.xml (ownership-based record rules)

    Returns Result.ok([]) when spec has no portal section.
    """
    try:
        if not spec.get("has_portal"):
            return Result.ok([])

        created: list[Path] = []
        module_name = module_context["module_name"]
        portal_pages = spec.get("portal_pages", [])
        portal_auth = spec.get("portal_auth", "portal")

        # Build unique model metadata for domain helpers and rules
        models_seen: dict[str, dict[str, Any]] = {}
        editable_models: set[str] = set()
        for page in portal_pages:
            model = page["model"]
            if model not in models_seen:
                models_seen[model] = {
                    "model": model,
                    "model_var": _to_python_var(model),
                    "model_class": _to_class(model),
                    "ownership": page["ownership"],
                }
            if page.get("fields_editable"):
                editable_models.add(model)

        controller_class = _to_class(module_name) + "Portal"

        portal_ctx = {
            **module_context,
            "controller_class": controller_class,
            "portal_pages": portal_pages,
            "portal_auth": portal_auth,
            "portal_models": list(models_seen.values()),
            "editable_models": editable_models,
        }

        # Render controller
        created.append(render_template(
            env, "portal_controller.py.j2",
            module_dir / "controllers" / "portal.py",
            portal_ctx,
        ))

        # Update controllers/__init__.py to import portal module
        init_path = module_dir / "controllers" / "__init__.py"
        init_path.parent.mkdir(parents=True, exist_ok=True)
        existing_imports = ""
        if init_path.exists():
            existing_imports = init_path.read_text(encoding="utf-8")
        if "from . import portal" not in existing_imports:
            new_content = existing_imports.rstrip("\n")
            if new_content:
                new_content += "\n"
            new_content += "from . import portal\n"
            init_path.write_text(new_content, encoding="utf-8")
        created.append(init_path)

        # Render home counter template (one file with all home counter entries)
        home_pages = [p for p in portal_pages if p.get("show_in_home", True)]
        if home_pages:
            created.append(render_template(
                env, "portal_home_counter.xml.j2",
                module_dir / "views" / "portal_home.xml",
                portal_ctx,
            ))

        # Render per-page QWeb templates
        for page in portal_pages:
            page_ctx = {**portal_ctx, "page": page}
            if page["type"] == "list":
                # List page template
                created.append(render_template(
                    env, "portal_list.xml.j2",
                    module_dir / "views" / f"portal_{page['id']}.xml",
                    page_ctx,
                ))
                # Detail page template (if detail_route exists)
                if page.get("detail_route"):
                    created.append(render_template(
                        env, "portal_detail.xml.j2",
                        module_dir / "views" / f"portal_{page['id']}_detail.xml",
                        page_ctx,
                    ))
            elif page["type"] == "detail":
                if page.get("fields_editable"):
                    created.append(render_template(
                        env, "portal_detail_editable.xml.j2",
                        module_dir / "views" / f"portal_{page['id']}.xml",
                        page_ctx,
                    ))
                else:
                    created.append(render_template(
                        env, "portal_detail.xml.j2",
                        module_dir / "views" / f"portal_{page['id']}.xml",
                        page_ctx,
                    ))

        # Render portal record rules
        created.append(render_template(
            env, "portal_rules.xml.j2",
            module_dir / "security" / "portal_rules.xml",
            portal_ctx,
        ))

        return Result.ok(created)
    except Exception as exc:
        return Result.fail(f"render_portal failed: {exc}")


def render_bulk(
    env: Environment,
    spec: dict[str, Any],
    module_dir: Path,
    module_context: dict[str, Any],
) -> "Result[list[Path]]":
    """Render bulk wizard TransientModels, views, and JS assets.

    Generates per bulk operation:
    - wizards/{wizard_var}.py (TransientModel with state machine)
    - wizards/{wizard_var}_line.py (preview line TransientModel)
    - views/{wizard_var}_wizard_form.xml (multi-step form view)
    - static/src/js/bulk_progress.js (shared bus.bus listener)
    - wizards/__init__.py updated with imports

    Returns Result.ok([]) when spec has no bulk operations.
    """
    try:
        if not spec.get("has_bulk_operations"):
            return Result.ok([])

        created: list[Path] = []
        bulk_ops = spec.get("bulk_operations", [])

        for op in bulk_ops:
            wizard_var = _to_python_var(op["wizard_model"])

            # Build per-operation template context
            bulk_ctx = {
                **module_context,
                "op": op,
            }

            # Render wizard model
            created.append(render_template(
                env, "bulk_wizard_model.py.j2",
                module_dir / "wizards" / f"{wizard_var}.py",
                bulk_ctx,
            ))

            # Render wizard line model (if preview_fields)
            if op.get("preview_fields"):
                created.append(render_template(
                    env, "bulk_wizard_line.py.j2",
                    module_dir / "wizards" / f"{wizard_var}_line.py",
                    bulk_ctx,
                ))

            # Render wizard form view
            created.append(render_template(
                env, "bulk_wizard_views.xml.j2",
                module_dir / "views" / f"{wizard_var}_wizard_form.xml",
                bulk_ctx,
            ))

        # Render shared JS progress listener (one file for all ops)
        js_ctx = {**module_context, "bulk_operations": bulk_ops}
        js_dir = module_dir / "static" / "src" / "js"
        js_dir.mkdir(parents=True, exist_ok=True)
        created.append(render_template(
            env, "bulk_wizard_js.js.j2",
            js_dir / "bulk_progress.js",
            js_ctx,
        ))

        # Update wizards/__init__.py
        init_path = module_dir / "wizards" / "__init__.py"
        init_path.parent.mkdir(parents=True, exist_ok=True)
        existing = ""
        if init_path.exists():
            existing = init_path.read_text(encoding="utf-8")

        new_imports = []
        for op in bulk_ops:
            wiz_var = _to_python_var(op["wizard_model"])
            imp = f"from . import {wiz_var}"
            if imp not in existing:
                new_imports.append(imp)
            if op.get("preview_fields"):
                line_imp = f"from . import {wiz_var}_line"
                if line_imp not in existing:
                    new_imports.append(line_imp)

        if new_imports:
            content = existing.rstrip("\n")
            if content:
                content += "\n"
            content += "\n".join(new_imports) + "\n"
            init_path.write_text(content, encoding="utf-8")
        created.append(init_path)

        return Result.ok(created)
    except Exception as exc:
        return Result.fail(f"render_bulk failed: {exc}")


def _classify_migration_ops(
    operations: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split migration operations into pre and post lists.

    Pre-migration: rename_field, drop_column, rename_model, sql.
    Post-migration: add_column, sql.
    """
    pre_ops: list[dict[str, Any]] = []
    post_ops: list[dict[str, Any]] = []
    pre_types = {"rename_field", "drop_column", "rename_model", "sql"}
    post_types = {"add_column"}

    for op in operations:
        op_type = op.get("type", "rename_field")
        if op_type in pre_types:
            pre_ops.append(op)
        elif op_type in post_types:
            post_ops.append(op)

    return pre_ops, post_ops


def render_migrations(
    env: Environment,
    spec: dict[str, Any],
    module_dir: Path,
    module_context: dict[str, Any],
) -> "Result[list[Path]]":
    """Render migration scripts from spec migrations.

    For each migration entry, creates:
    - migrations/{to_version}/pre-migrate.py (rename/drop/sql ops)
    - migrations/{to_version}/post-migrate.py (add_column/sql ops)

    Returns Result.ok([]) when no migrations are defined.
    """
    migrations = spec.get("migrations", [])
    if not migrations:
        return Result.ok([])

    try:
        created: list[Path] = []
        for migration in migrations:
            to_version = migration.get("to_version", "")
            if not to_version:
                continue
            operations = migration.get("operations", [])
            if not operations:
                continue

            # C1: Validate identifiers to prevent SQL injection in generated scripts
            _SQL_IDENT_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_.]*$")
            for op in operations:
                for key in ("model", "old_name", "new_name"):
                    val = op.get(key, "")
                    if val and not _SQL_IDENT_RE.match(val):
                        return Result.fail(
                            f"render_migrations: invalid SQL identifier '{val}' in {key}"
                        )
                if op.get("type") == "sql":
                    _logger.warning(
                        "Migration %s contains raw SQL operation — review generated script",
                        to_version,
                    )

            pre_ops, post_ops = _classify_migration_ops(operations)
            mig_dir = module_dir / "migrations" / to_version
            mig_dir.mkdir(parents=True, exist_ok=True)

            if pre_ops:
                pre_ctx = {
                    **module_context,
                    "migration_version": to_version,
                    "pre_operations": pre_ops,
                }
                created.append(render_template(
                    env, "pre_migration.py.j2",
                    mig_dir / "pre-migrate.py",
                    pre_ctx,
                ))

            if post_ops:
                post_ctx = {
                    **module_context,
                    "migration_version": to_version,
                    "post_operations": post_ops,
                }
                created.append(render_template(
                    env, "post_migration.py.j2",
                    mig_dir / "post-migrate.py",
                    post_ctx,
                ))

        return Result.ok(created)
    except Exception as exc:
        return Result.fail(f"render_migrations failed: {exc}")



def render_settings(
    env: Environment,
    spec: dict[str, Any],
    module_dir: Path,
    module_context: dict[str, Any],
) -> "Result[list[Path]]":
    """Render res.config.settings model and view for ir.config_parameter settings.

    Generates:
    - models/res_config_settings.py (TransientModel inheriting res.config.settings)
    - views/res_config_settings_view.xml (inherited settings form view)
    - models/__init__.py updated with import

    Returns Result.ok([]) when spec has no settings.
    """
    settings = spec.get("settings", [])
    if not settings:
        return Result.ok([])

    try:
        created: list[Path] = []
        module_name = module_context["module_name"]
        module_title = module_context.get("module_title", module_name.replace("_", " ").title())

        settings_ctx = {
            **module_context,
            "settings": settings,
            "module_title": module_title,
        }

        # Render settings model
        created.append(render_template(
            env, "res_config_settings.py.j2",
            module_dir / "models" / "res_config_settings.py",
            settings_ctx,
        ))

        # Render settings view
        created.append(render_template(
            env, "res_config_settings_view.xml.j2",
            module_dir / "views" / "res_config_settings_view.xml",
            settings_ctx,
        ))

        # Update models/__init__.py to import res_config_settings
        init_path = module_dir / "models" / "__init__.py"
        init_path.parent.mkdir(parents=True, exist_ok=True)
        existing = ""
        if init_path.exists():
            existing = init_path.read_text(encoding="utf-8")
        import_line = "from . import res_config_settings"
        if import_line not in existing:
            new_content = existing.rstrip("\n")
            if new_content:
                new_content += "\n"
            new_content += import_line + "\n"
            init_path.write_text(new_content, encoding="utf-8")
        created.append(init_path)

        return Result.ok(created)
    except Exception as exc:
        return Result.fail(f"render_settings failed: {exc}")


def render_owl_components(
    env: Environment,
    spec: dict[str, Any],
    module_dir: Path,
    module_context: dict[str, Any],
) -> "Result[list[Path]]":
    """Render OWL component JS and XML files from spec owl_components.

    Generates per component:
    - static/src/js/{name}.js (OWL component class)
    - static/src/xml/{name}.xml (QWeb template)

    Returns Result.ok([]) when no owl_components are present.
    """
    components = spec.get("owl_components", [])
    if not components:
        return Result.ok([])
    try:
        created: list[Path] = []
        for comp in components:
            comp_name = comp.get("name", "")
            comp_class = _to_class(comp_name)
            comp_ctx = {
                **module_context,
                "component_class": comp_class,
                "component_name": comp_name,
                "component_css_class": f"o_{comp_name.replace('.', '_')}",
                "registry_category": comp.get("type", "field_widget"),
            }
            js_path = module_dir / "static" / "src" / "js" / f"{comp_name}.js"
            created.append(render_template(env, "owl_component.js.j2", js_path, comp_ctx))
            xml_path = module_dir / "static" / "src" / "xml" / f"{comp_name}.xml"
            created.append(render_template(env, "owl_component.xml.j2", xml_path, comp_ctx))
        return Result.ok(created)
    except Exception as exc:
        return Result.fail(f"render_owl_components failed: {exc}")


def render_assets(
    env: Environment,
    spec: dict[str, Any],
    module_dir: Path,
    module_context: dict[str, Any],
) -> "Result[list[Path]]":
    """Render asset bundle declaration XML when OWL components or static JS/CSS exist.

    Auto-generates views/assets.xml referencing all JS and CSS files
    produced by OWL components or found in static/src/.

    Returns Result.ok([]) when no assets need declaration.
    """
    components = spec.get("owl_components", [])
    has_bulk = spec.get("has_bulk_operations", False)
    if not components and not has_bulk:
        return Result.ok([])
    try:
        asset_files: list[dict[str, str]] = []
        css_files: list[dict[str, str]] = []
        for comp in components:
            comp_name = comp.get("name", "")
            asset_files.append({"path": f"static/src/js/{comp_name}.js"})
        if has_bulk:
            asset_files.append({"path": "static/src/js/bulk_progress.js"})
        assets_ctx = {
            **module_context,
            "asset_files": asset_files,
            "css_files": css_files,
        }
        out_path = module_dir / "views" / "assets.xml"
        created = [render_template(env, "assets.xml.j2", out_path, assets_ctx)]
        return Result.ok(created)
    except Exception as exc:
        return Result.fail(f"render_assets failed: {exc}")


def render_server_actions(
    env: Environment,
    spec: dict[str, Any],
    module_dir: Path,
    module_context: dict[str, Any],
) -> "Result[list[Path]]":
    """Render server action XML bindings from model-level server_actions.

    Generates per model with server_actions:
    - data/server_action_{model_var}.xml

    Validates method names are valid Python identifiers.
    Returns Result.ok([]) when no server_actions are present.
    """
    models = spec.get("models", [])
    models_with_actions = [
        m for m in models if m.get("server_actions")
    ]
    if not models_with_actions:
        return Result.ok([])
    try:
        created: list[Path] = []
        for model in models_with_actions:
            actions = model.get("server_actions", [])
            for action in actions:
                method = action.get("method", "")
                if not method.isidentifier():
                    return Result.fail(
                        f"Invalid server action method '{method}': "
                        "must be a valid Python identifier"
                    )
            model_var = _to_python_var(model["name"])
            action_ctx = {
                **module_context,
                "server_actions": actions,
                "model_xml_id": _model_ref(model["name"]),
            }
            out_path = module_dir / "data" / f"server_action_{model_var}.xml"
            created.append(render_template(
                env, "server_action.xml.j2", out_path, action_ctx,
            ))
        return Result.ok(created)
    except Exception as exc:
        return Result.fail(f"render_server_actions failed: {exc}")


def render_mail_templates(
    env: Environment,
    spec: dict[str, Any],
    module_dir: Path,
    module_context: dict[str, Any],
) -> "Result[list[Path]]":
    """Render mail_template_data.xml when notifications are present.

    Collects all notification_templates across all models into a flat list
    and renders them via mail_template_data.xml.j2.

    Returns Result.ok([]) when no notifications are present.
    """
    models = spec.get("models", [])
    notification_models = [m for m in models if m.get("has_notifications")]
    if not notification_models:
        return Result.ok([])

    try:
        all_templates: list[dict[str, Any]] = []
        for model in notification_models:
            all_templates.extend(model.get("notification_templates", []))

        if not all_templates:
            return Result.ok([])

        mail_ctx = {
            **module_context,
            "notification_templates": all_templates,
        }
        path = render_template(
            env, "mail_template_data.xml.j2",
            module_dir / "data" / "mail_template_data.xml",
            mail_ctx,
        )
        return Result.ok([path])
    except Exception as exc:
        return Result.fail(f"render_mail_templates failed: {exc}")


def render_module(
    spec: dict[str, Any],
    template_dir: Path,
    output_dir: Path,
    verifier: "EnvironmentVerifier | None" = None,
    *,
    no_context7: bool = False,
    fresh_context7: bool = False,
    hooks: list[RenderHook] | None = None,
    resume_from: "GenerationManifest | None" = None,
    force: bool = False,
    dry_run: bool = False,
    skip_semantic_validation: bool = False,
    ols_client: "OdooLSClient | None" = None,
) -> "tuple[list[Path], list[VerificationWarning]]":
    """Orchestrate rendering of a complete Odoo module via 11 named stage functions.

    Args:
        spec: Module specification dictionary with module_name, models, etc.
        template_dir: Path to Jinja2 template files (kept for backward compat).
        output_dir: Root directory where the module will be created.
        verifier: Optional EnvironmentVerifier for inline MCP-backed verification.
        hooks: Optional list of RenderHook observers. None = zero overhead.
        resume_from: Optional GenerationManifest from a previous run. Completed
            stages with intact artifacts are skipped.
        force: Force full regeneration, ignore spec stash.
        dry_run: Show what would change without writing files.
        skip_semantic_validation: Skip post-render semantic validation (default False).
        ols_client: Optional OdooLSClient for structural validation via odoo-ls.
            When None (the default), OLS validation is skipped entirely.

    Returns:
        Tuple of (created_files, verification_warnings).
    """
    # Phase 60: Capture raw spec BEFORE validation for iterative stash comparison
    import copy
    spec_raw = copy.deepcopy(spec)

    # Phase 47: Validate spec against Pydantic schema BEFORE any processing
    # PIPE-01: Keep typed ModuleSpec — defer model_dump() to preprocessor boundary.
    validated = validate_spec(spec)
    spec = validated.model_dump(exclude_none=True)

    # Phase 28: validate no circular dependencies BEFORE any preprocessing
    _validate_no_cycles(spec)

    # Phase 60: Iterative mode detection
    module_name_raw = spec.get("module_name", "unknown")
    module_dir_early = output_dir / module_name_raw
    iterative_mode = False
    affected_stages: frozenset[str] | None = None
    existing_manifest: "GenerationManifest | None" = None
    diff_summary: dict[str, Any] = {}

    if not force:
        from amil_utils.iterative import (
            compute_spec_diff,
            determine_affected_stages as _determine_affected,
            load_spec_stash,
        )
        old_spec = load_spec_stash(module_dir_early)
        if old_spec is not None:
            diff_result = compute_spec_diff(old_spec, spec_raw)
            if diff_result is None:
                _logger.info(
                    "Spec unchanged. Nothing to do. Use --force to regenerate."
                )
                return ([], [])

            affected = _determine_affected(diff_result, old_spec, spec_raw)
            diff_summary = affected.diff_summary

            if dry_run:
                _logger.info(
                    "Dry run: diff categories=%s, affected stages=%s",
                    list(diff_summary.keys()),
                    sorted(affected.stages),
                )
                return ([], [])

            iterative_mode = True
            affected_stages = affected.stages
            existing_manifest = load_manifest(module_dir_early)
            _logger.info(
                "Iterative mode: %d categories, stages=%s",
                len(diff_summary),
                sorted(affected_stages),
            )

    env = create_versioned_renderer(spec.get("odoo_version", get_default_version()))

    # Phase 54: GenerationSession replaces artifact_state tracking
    session = GenerationSession(
        module_name=spec.get("module_name", "unknown"),
        spec_sha256=compute_spec_sha256(spec),
        odoo_version=spec.get("odoo_version", get_default_version()),
    )

    # Phase 54: Resume spec SHA256 check
    if resume_from and resume_from.spec_sha256 != session.spec_sha256:
        _logger.warning(
            "Spec changed since last run (sha256 mismatch). Running full generation."
        )
        resume_from = None  # Force full re-run

    # Phase 45: single call replaces 10 individual preprocessor calls + override_sources loop
    # PIPE-01: run_preprocessors accepts both dict and ModuleSpec (converts internally).
    # Pass original dict so monkeypatched lambdas in tests still return a dict.
    t0_pre = time.perf_counter_ns()
    spec = run_preprocessors(spec)
    pre_duration_ms = (time.perf_counter_ns() - t0_pre) // 1_000_000
    preprocessors_run = [f"{name}:{order}" for order, name, _ in get_registered_preprocessors()]

    # Phase 42: Context7 documentation enrichment
    if no_context7:
        c7_hints: dict[str, str] = {}
    else:
        _c7_client = build_context7_from_env()
        _c7_cache = Path(".amil-cache/context7")
        c7_hints = context7_enrich(
            spec, _c7_client,
            cache_dir=_c7_cache,
            fresh=fresh_context7,
            odoo_version=spec.get("odoo_version", get_default_version()),
        )
    module_name = spec["module_name"]
    module_dir = output_dir / module_name
    ctx = _build_module_context(spec, module_name)
    ctx["c7_hints"] = c7_hints  # Phase 42: inject Context7 hints
    all_warnings: list = []

    # Phase 54: Notify hooks after preprocessing
    notify_hooks(hooks, "on_preprocess_complete", module_name, spec.get("models", []), preprocessors_run)

    created_files: list[Path] = []

    # Phase 54: Named stage tuples replace anonymous lambdas
    all_stages: list[tuple[str, Callable[[], Result]]] = [
        ("manifest", lambda: render_manifest(env, spec, module_dir, ctx)),
        ("models", lambda: render_models(env, spec, module_dir, ctx, verifier=verifier, warnings_out=all_warnings)),
        ("extensions", lambda: render_extensions(env, spec, module_dir, ctx)),
        ("views", lambda: render_views(env, spec, module_dir, ctx)),
        ("security", lambda: render_security(env, spec, module_dir, ctx)),
        ("mail_templates", lambda: render_mail_templates(env, spec, module_dir, ctx)),
        ("wizards", lambda: render_wizards(env, spec, module_dir, ctx)),
        ("tests", lambda: render_tests(env, spec, module_dir, ctx)),
        ("static", lambda: render_static(env, spec, module_dir, ctx)),
        ("cron", lambda: render_cron(env, spec, module_dir, ctx)),
        ("reports", lambda: render_reports(env, spec, module_dir, ctx)),
        ("controllers", lambda: render_controllers(env, spec, module_dir, ctx)),
        ("portal", lambda: render_portal(env, spec, module_dir, ctx)),
        ("bulk", lambda: render_bulk(env, spec, module_dir, ctx)),
        ("migrations", lambda: render_migrations(env, spec, module_dir, ctx)),
        ("settings", lambda: render_settings(env, spec, module_dir, ctx)),
        ("owl_components", lambda: render_owl_components(env, spec, module_dir, ctx)),
        ("assets", lambda: render_assets(env, spec, module_dir, ctx)),
        ("server_actions", lambda: render_server_actions(env, spec, module_dir, ctx)),
    ]

    # Phase 60: Filter stages in iterative mode
    if iterative_mode and affected_stages is not None:
        stages = [
            (name, fn) for name, fn in all_stages
            if name in affected_stages
        ]
        _logger.info(
            "Iterative: running %d/%d stages: %s",
            len(stages), len(all_stages),
            [name for name, _ in stages],
        )
    else:
        stages = all_stages

    # Phase 60: Load conflict detection tools when iterative mode is active
    skeleton_dir = output_dir / ".amil-skeleton" / module_name

    for stage_name, stage_fn in stages:
        # Phase 54: Resume -- skip completed stages with intact artifacts
        if resume_from and resume_from.stages.get(stage_name, StageResult()).status == "complete":
            if _artifacts_intact(resume_from, stage_name, module_dir):
                session.record_stage(stage_name, StageResult(status="skipped", reason="resumed"))
                # Collect existing files for return value
                stage_artifacts = resume_from.stages[stage_name].artifacts
                created_files.extend(module_dir / p for p in stage_artifacts)
                notify_hooks(hooks, "on_stage_complete", module_name, stage_name,
                    StageResult(status="skipped", reason="resumed"), stage_artifacts)
                continue

        t0 = time.perf_counter_ns()
        result = stage_fn()
        duration_ms = (time.perf_counter_ns() - t0) // 1_000_000

        # Compute per-stage artifacts (relative paths)
        stage_files = []
        for p in (result.data or []):
            try:
                stage_files.append(str(p.relative_to(module_dir)))
            except ValueError:
                stage_files.append(str(p))

        if not result.success:
            stage_result = StageResult(
                status="failed", duration_ms=duration_ms,
                error="; ".join(result.errors), artifacts=stage_files,
            )
            session.record_stage(stage_name, stage_result)
            notify_hooks(hooks, "on_stage_complete", module_name, stage_name, stage_result, stage_files)
            break

        # Phase 60: Conflict detection + stub merge for iterative mode
        if iterative_mode and existing_manifest is not None:
            from amil_utils.iterative import (
                detect_conflicts,
                extract_filled_stubs,
                inject_stubs_into,
            )
            conflicts = detect_conflicts(
                existing_manifest, stage_files, module_dir, skeleton_dir,
            )

            # Handle stub-mergeable files: extract old stubs, inject into new
            for rel_path in conflicts.stub_mergeable:
                file_path = module_dir / rel_path
                if file_path.exists() and file_path.suffix == ".py":
                    try:
                        current_lines = file_path.read_text(encoding="utf-8").splitlines()
                        filled = extract_filled_stubs(current_lines)
                        if filled:
                            new_content = file_path.read_text(encoding="utf-8")
                            merged = inject_stubs_into(new_content, filled)
                            file_path.write_text(merged, encoding="utf-8")
                            _logger.info("Auto-merged stubs in %s", rel_path)
                    except Exception as exc:
                        _logger.warning("Stub merge failed for %s: %s", rel_path, exc)

            # Handle conflict files: write to .amil-pending/
            pending_dir = module_dir / ".amil-pending"
            for rel_path in conflicts.conflicts:
                file_path = module_dir / rel_path
                if file_path.exists():
                    pending_path = pending_dir / rel_path
                    pending_path.parent.mkdir(parents=True, exist_ok=True)
                    # Copy the newly rendered version to pending
                    shutil.copy2(file_path, pending_path)
                    _logger.info("Conflict: %s -> .amil-pending/%s", rel_path, rel_path)

        stage_result = StageResult(
            status="complete", duration_ms=duration_ms, artifacts=stage_files,
        )
        session.record_stage(stage_name, stage_result)
        created_files.extend(result.data or [])
        notify_hooks(hooks, "on_stage_complete", module_name, stage_name, stage_result, stage_files)

    # Phase 60: Merge iterative session with existing manifest for skipped stages
    if iterative_mode and existing_manifest is not None:
        for sname, sresult in existing_manifest.stages.items():
            if sname not in session._stages:
                session.record_stage(sname, StageResult(
                    status="skipped", reason="iterative-unchanged",
                ))

    # Phase 58: Skeleton copy for E16 baseline comparison
    try:
        skeleton_dir = output_dir / ".amil-skeleton" / module_name
        # Copy only .py files from the rendered module for E16 comparison
        if module_dir.exists():
            skeleton_dir.mkdir(parents=True, exist_ok=True)
            for py_file in module_dir.rglob("*.py"):
                rel = py_file.relative_to(module_dir)
                dest = skeleton_dir / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(py_file, dest)
            _logger.info("Skeleton copy: %s -> %s", module_dir, skeleton_dir)
    except Exception as exc:
        _logger.warning("Skeleton copy failed (non-blocking): %s", exc)

    # Phase 54: Build artifact info and notify on_render_complete
    artifact_entries = []
    total_lines = 0
    for fpath in created_files:
        if fpath.exists():
            try:
                sha = compute_file_sha256(fpath)
                rel = str(fpath.relative_to(module_dir))
                artifact_entries.append(ArtifactEntry(path=rel, sha256=sha))
                if fpath.suffix in ('.py', '.xml', '.csv', '.txt', '.js', '.css', '.scss'):
                    total_lines += len(fpath.read_text(encoding="utf-8", errors="ignore").splitlines())
            except Exception:
                pass

    manifest = session.to_manifest(
        preprocessing=PreprocessingInfo(preprocessors_run=preprocessors_run, duration_ms=pre_duration_ms),
        artifacts=ArtifactInfo(files=artifact_entries, total_files=len(artifact_entries), total_lines=total_lines),
        models_registered=[m.get("model_name", "") for m in spec.get("models", [])],
    )
    notify_hooks(hooks, "on_render_complete", module_name, manifest)

    # PIPE-04: Run semantic validation so programmatic callers get it by default
    if not skip_semantic_validation and created_files:
        try:
            from amil_utils.validation.semantic import semantic_validate
            from amil_utils.verifier import VerificationWarning as _VW
            sem_result = semantic_validate(module_dir)
            if sem_result.has_errors:
                for err in sem_result.errors:
                    all_warnings.append(
                        _VW(
                            check_type=f"semantic:{err.code}",
                            subject=err.file or module_name,
                            message=err.message,
                        )
                    )
                _logger.warning(
                    "Semantic validation found %d error(s) in %s",
                    len(sem_result.errors),
                    module_name,
                )
        except Exception as exc:
            _logger.warning("Semantic validation failed: %s", exc)

    # --- Phase 5: Structural validation via odoo-ls ---
    if ols_client is not None and not skip_semantic_validation and created_files:
        try:
            from amil_utils.validation.odoo_ls_validator import (
                classify_ols_diagnostics,
            )
            from amil_utils.validation.odoo_ls_fixer import run_ols_fix_loop
            from amil_utils.verifier import VerificationWarning as _OlsVW

            _logger.info("Running odoo-ls structural validation on %s", module_dir)
            ols_diags = ols_client.validate_module(module_dir)
            classified = classify_ols_diagnostics(ols_diags)

            if classified.fixable_count > 0:
                fixed = run_ols_fix_loop(
                    lambda path: ols_client.validate_module(path),
                    module_dir,
                    max_iterations=3,
                )
                _logger.info("OLS auto-fix applied %d fixes", fixed)
                ols_diags = ols_client.validate_module(module_dir)
                classified = classify_ols_diagnostics(ols_diags)

            for d in classified.errors:
                all_warnings.append(
                    _OlsVW(
                        check_type=f"odoo-ls:{d.code}",
                        subject=d.file or module_name,
                        message=f"[odoo-ls] {d.message} (line {d.line})",
                    )
                )
            for d in classified.warnings:
                all_warnings.append(
                    _OlsVW(
                        check_type=f"odoo-ls:{d.code}",
                        subject=d.file or module_name,
                        message=f"[odoo-ls] {d.message} (line {d.line})",
                        suggestion="warning",
                    )
                )
            if classified.errors:
                _logger.warning(
                    "odoo-ls found %d errors in %s",
                    len(classified.errors),
                    module_dir.name,
                )
        except Exception as exc:
            _logger.warning("odoo-ls validation failed: %s", exc)

    # Phase 60: Save spec stash after successful generation
    from amil_utils.iterative import save_spec_stash
    try:
        save_spec_stash(spec_raw, module_dir)
    except Exception as exc:
        _logger.warning("Failed to save spec stash: %s", exc)

    return created_files, all_warnings
