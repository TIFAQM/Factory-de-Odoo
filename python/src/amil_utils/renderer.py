"""Jinja2 rendering engine with Odoo-specific filters for module scaffolding."""

from __future__ import annotations

import logging
import re
import shutil
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from jinja2 import FileSystemLoader, StrictUndefined
from jinja2.sandbox import SandboxedEnvironment as Environment  # CWE-1336: prevent SSTI

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

# Stage functions extracted to renderer_stages.py
from amil_utils.renderer_stages import (  # noqa: F401 — re-exported for backward compat
    render_manifest,
    render_models,
    render_extensions,
    render_views,
    render_security,
    render_wizards,
    render_tests,
    render_static,
    render_cron,
    render_reports,
    render_controllers,
    render_portal,
    render_bulk,
    render_migrations,
    render_settings,
    render_owl_components,
    render_assets,
    render_server_actions,
    render_mail_templates,
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
        except (OSError, ValueError):
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
    """Orchestrate rendering of a complete Odoo module via named stage functions.

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
    from amil_utils.renderer_orchestration import (
        _EarlyReturn,
        compute_artifacts_and_notify,
        copy_skeleton,
        create_session,
        detect_iterative_mode,
        enrich_context,
        execute_stages,
        merge_iterative_session,
        run_post_render_validation,
        run_preprocessing,
        save_spec_stash_safe,
        validate_spec_phase,
    )

    # --- Phase 1a: Capture raw spec + validate ---
    import copy
    spec_raw = copy.deepcopy(spec)
    spec = validate_spec_phase(
        spec,
        validate_fn=validate_spec,
        validate_cycles_fn=_validate_no_cycles,
    )

    # --- Phase 2: Iterative mode detection ---
    module_name_raw = spec.get("module_name", "unknown")
    module_dir_early = output_dir / module_name_raw
    try:
        iterative_mode, affected_stages, existing_manifest, diff_summary = (
            detect_iterative_mode(spec_raw, module_dir_early, force, dry_run)
        )
    except _EarlyReturn:
        return ([], [])

    # --- Phase 3: Session setup ---
    env = create_versioned_renderer(spec.get("odoo_version", get_default_version()))
    session, resume_from = create_session(spec, resume_from)

    # --- Phase 1b: Preprocessing ---
    spec, preprocessors_run, pre_duration_ms = run_preprocessing(
        spec, preprocessors_fn=run_preprocessors,
    )

    # --- Phase 4: Context enrichment / Context7 ---
    module_name, module_dir, ctx = enrich_context(
        spec, output_dir, no_context7=no_context7, fresh_context7=fresh_context7,
        c7_build_fn=build_context7_from_env, c7_enrich_fn=context7_enrich,
    )
    all_warnings: list = []
    notify_hooks(hooks, "on_preprocess_complete", module_name, spec.get("models", []), preprocessors_run)

    # --- Phase 5: Stage list building + iterative filtering ---
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

    # --- Phase 6: Stage execution loop ---
    skeleton_dir = output_dir / ".amil-skeleton" / module_name
    created_files = execute_stages(
        stages, session, module_name, module_dir, skeleton_dir,
        resume_from=resume_from, iterative_mode=iterative_mode,
        existing_manifest=existing_manifest, hooks=hooks,
        artifacts_intact_fn=_artifacts_intact,
    )

    # --- Phase 6b: Merge iterative session ---
    merge_iterative_session(session, existing_manifest, iterative_mode)

    # --- Phase 7: Skeleton copy for E16 baseline ---
    copy_skeleton(output_dir, module_name, module_dir)

    # --- Phase 8: Artifact computation + manifest + hooks ---
    compute_artifacts_and_notify(
        created_files, module_dir, module_name, session,
        preprocessors_run, pre_duration_ms, spec, hooks,
    )

    # --- Phase 9: Post-render validation ---
    run_post_render_validation(
        module_dir, module_name, created_files, all_warnings,
        skip_semantic_validation=skip_semantic_validation, ols_client=ols_client,
    )

    # --- Save spec stash ---
    save_spec_stash_safe(spec_raw, module_dir)

    return created_files, all_warnings
