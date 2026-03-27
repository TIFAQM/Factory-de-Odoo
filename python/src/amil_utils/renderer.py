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
            except (OSError, ValueError) as exc:
                _logger.warning("Failed to compute artifact entry for %s: %s", fpath, exc)

    manifest = session.to_manifest(
        preprocessing=PreprocessingInfo(preprocessors_run=preprocessors_run, duration_ms=pre_duration_ms),
        artifacts=ArtifactInfo(files=artifact_entries, total_files=len(artifact_entries), total_lines=total_lines),
        models_registered=[m.get("model_name", "") for m in spec.get("models", [])],
    )
    notify_hooks(hooks, "on_render_complete", module_name, manifest)

    # PIPE-04: Run semantic validation so programmatic callers get it by default
    if not skip_semantic_validation and created_files:
        try:
            from amil_utils.validation.semantic import (
                semantic_validate_full,
                semantic_validate_patterns,
            )
            from amil_utils.verifier import VerificationWarning as _VW
            if ols_client is not None:
                sem_result = semantic_validate_patterns(module_dir)
            else:
                sem_result = semantic_validate_full(module_dir)
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
