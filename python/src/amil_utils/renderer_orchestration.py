"""Helper functions for render_module() orchestration.

Each function corresponds to one logical phase of module rendering.
Extracted from renderer.py to keep render_module() as a thin orchestrator.
"""

from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from amil_utils.hooks import RenderHook, notify_hooks
from amil_utils.manifest import (
    ArtifactEntry,
    ArtifactInfo,
    GenerationSession,
    PreprocessingInfo,
    StageResult,
    compute_file_sha256,
    compute_spec_sha256,
)
from amil_utils.preprocessors import run_preprocessors
from amil_utils.preprocessors._registry import get_registered_preprocessors
from amil_utils.preprocessors.validation import _validate_no_cycles
from amil_utils.spec_schema import validate_spec
from amil_utils.version_defaults import get_default_version
from amil_utils.context7 import build_context7_from_env, context7_enrich
from amil_utils.renderer_context import _build_module_context
from amil_utils.validation.types import Result

if TYPE_CHECKING:
    from amil_utils.manifest import GenerationManifest
    from amil_utils.verifier import VerificationWarning

_logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Phase 1: Spec validation + preprocessing
# ---------------------------------------------------------------------------

def validate_spec_phase(
    spec: dict[str, Any],
    *,
    validate_fn: Callable | None = None,
    validate_cycles_fn: Callable | None = None,
) -> dict[str, Any]:
    """Validate spec against Pydantic schema and check for cycles.

    Args:
        validate_fn: Callable to validate spec. Defaults to ``validate_spec``.
            Passed explicitly so callers' monkeypatches take effect.
        validate_cycles_fn: Callable to check for cycles. Defaults to
            ``_validate_no_cycles``.

    Returns:
        The validated and serialized spec dictionary.
    """
    _validate = validate_fn or validate_spec
    _check_cycles = validate_cycles_fn or _validate_no_cycles
    validated = _validate(spec)
    spec = validated.model_dump(exclude_none=True)
    _check_cycles(spec)
    return spec


def run_preprocessing(
    spec: dict[str, Any],
    *,
    preprocessors_fn: Callable | None = None,
) -> tuple[dict[str, Any], list[str], int]:
    """Run all registered preprocessors on the validated spec.

    Args:
        preprocessors_fn: Callable to run preprocessors. Defaults to
            ``run_preprocessors``. Passed explicitly so callers'
            monkeypatches take effect.

    Returns:
        Tuple of (processed_spec, preprocessors_run, pre_duration_ms).
    """
    _run = preprocessors_fn or run_preprocessors
    t0_pre = time.perf_counter_ns()
    spec = _run(spec)
    pre_duration_ms = (time.perf_counter_ns() - t0_pre) // 1_000_000
    preprocessors_run = [
        f"{name}:{order}" for order, name, _ in get_registered_preprocessors()
    ]
    return spec, preprocessors_run, pre_duration_ms


# ---------------------------------------------------------------------------
# Phase 2: Iterative mode detection
# ---------------------------------------------------------------------------

def detect_iterative_mode(
    spec_raw: dict[str, Any],
    module_dir: Path,
    force: bool,
    dry_run: bool,
) -> tuple[bool, frozenset[str] | None, "GenerationManifest | None", dict[str, Any]]:
    """Check whether the spec changed since last run and determine affected stages.

    Returns:
        Tuple of (iterative_mode, affected_stages, existing_manifest, diff_summary).
        If dry_run is True and spec is unchanged, returns a sentinel that the caller
        should use to short-circuit with ``([], [])``.

    Raises:
        _EarlyReturn: when the caller should return ([], []) immediately.
    """
    from amil_utils.manifest import load_manifest

    if force:
        return False, None, None, {}

    from amil_utils.iterative import (
        compute_spec_diff,
        determine_affected_stages as _determine_affected,
        load_spec_stash,
    )

    old_spec = load_spec_stash(module_dir)
    if old_spec is None:
        return False, None, None, {}

    diff_result = compute_spec_diff(old_spec, spec_raw)
    if diff_result is None:
        _logger.info(
            "Spec unchanged. Nothing to do. Use --force to regenerate."
        )
        raise _EarlyReturn()

    affected = _determine_affected(diff_result, old_spec, spec_raw)
    diff_summary = affected.diff_summary

    if dry_run:
        _logger.info(
            "Dry run: diff categories=%s, affected stages=%s",
            list(diff_summary.keys()),
            sorted(affected.stages),
        )
        raise _EarlyReturn()

    affected_stages = affected.stages
    existing_manifest = load_manifest(module_dir)
    _logger.info(
        "Iterative mode: %d categories, stages=%s",
        len(diff_summary),
        sorted(affected_stages),
    )
    return True, affected_stages, existing_manifest, diff_summary


class _EarlyReturn(Exception):
    """Sentinel exception to signal render_module() should return ([], []) immediately."""


# ---------------------------------------------------------------------------
# Phase 3: Session setup
# ---------------------------------------------------------------------------

def create_session(
    spec: dict[str, Any],
    resume_from: "GenerationManifest | None",
) -> tuple[GenerationSession, "GenerationManifest | None"]:
    """Create a GenerationSession; invalidate resume_from if spec SHA changed.

    Returns:
        Tuple of (session, possibly-invalidated resume_from).
    """
    session = GenerationSession(
        module_name=spec.get("module_name", "unknown"),
        spec_sha256=compute_spec_sha256(spec),
        odoo_version=spec.get("odoo_version", get_default_version()),
    )

    if resume_from and resume_from.spec_sha256 != session.spec_sha256:
        _logger.warning(
            "Spec changed since last run (sha256 mismatch). Running full generation."
        )
        resume_from = None

    return session, resume_from


# ---------------------------------------------------------------------------
# Phase 4: Context enrichment / Context7
# ---------------------------------------------------------------------------

def enrich_context(
    spec: dict[str, Any],
    output_dir: Path,
    *,
    no_context7: bool,
    fresh_context7: bool,
    c7_build_fn: Callable | None = None,
    c7_enrich_fn: Callable | None = None,
) -> tuple[str, Path, dict[str, Any]]:
    """Build module context and enrich it with Context7 hints.

    Args:
        c7_build_fn: Callable returning a Context7 client. Defaults to
            ``build_context7_from_env``. Passed explicitly so that callers'
            monkeypatches on the *renderer* module namespace still take effect.
        c7_enrich_fn: Callable performing Context7 enrichment. Defaults to
            ``context7_enrich``.

    Returns:
        Tuple of (module_name, module_dir, ctx).
    """
    _build = c7_build_fn or build_context7_from_env
    _enrich = c7_enrich_fn or context7_enrich

    if no_context7:
        c7_hints: dict[str, str] = {}
    else:
        _c7_client = _build()
        _c7_cache = Path(".amil-cache/context7")
        c7_hints = _enrich(
            spec, _c7_client,
            cache_dir=_c7_cache,
            fresh=fresh_context7,
            odoo_version=spec.get("odoo_version", get_default_version()),
        )

    module_name = spec["module_name"]
    module_dir = output_dir / module_name
    ctx = _build_module_context(spec, module_name)
    ctx["c7_hints"] = c7_hints
    return module_name, module_dir, ctx


# ---------------------------------------------------------------------------
# Phase 6: Stage execution loop with resume + conflict detection
# ---------------------------------------------------------------------------

def execute_stages(
    stages: list[tuple[str, Callable[[], Result]]],
    session: GenerationSession,
    module_name: str,
    module_dir: Path,
    skeleton_dir: Path,
    *,
    resume_from: "GenerationManifest | None",
    iterative_mode: bool,
    existing_manifest: "GenerationManifest | None",
    hooks: list[RenderHook] | None,
    artifacts_intact_fn: Callable,
) -> list[Path]:
    """Run each stage, handling resume, timing, and iterative conflict detection.

    Returns:
        List of created file paths.
    """
    created_files: list[Path] = []

    for stage_name, stage_fn in stages:
        # Resume -- skip completed stages with intact artifacts
        if resume_from and resume_from.stages.get(stage_name, StageResult()).status == "complete":
            if artifacts_intact_fn(resume_from, stage_name, module_dir):
                session.record_stage(stage_name, StageResult(status="skipped", reason="resumed"))
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

        # Conflict detection + stub merge for iterative mode
        if iterative_mode and existing_manifest is not None:
            _handle_iterative_conflicts(
                existing_manifest, stage_files, module_dir, skeleton_dir,
            )

        stage_result = StageResult(
            status="complete", duration_ms=duration_ms, artifacts=stage_files,
        )
        session.record_stage(stage_name, stage_result)
        created_files.extend(result.data or [])
        notify_hooks(hooks, "on_stage_complete", module_name, stage_name, stage_result, stage_files)

    return created_files


def _handle_iterative_conflicts(
    existing_manifest: "GenerationManifest",
    stage_files: list[str],
    module_dir: Path,
    skeleton_dir: Path,
) -> None:
    """Detect conflicts and auto-merge stubs for iterative mode."""
    from amil_utils.iterative import (
        detect_conflicts,
        extract_filled_stubs,
        inject_stubs_into,
    )

    conflicts = detect_conflicts(
        existing_manifest, stage_files, module_dir, skeleton_dir,
    )

    # Handle stub-mergeable files
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
            shutil.copy2(file_path, pending_path)
            _logger.info("Conflict: %s -> .amil-pending/%s", rel_path, rel_path)


def merge_iterative_session(
    session: GenerationSession,
    existing_manifest: "GenerationManifest | None",
    iterative_mode: bool,
) -> None:
    """Merge skipped stages from existing manifest into session for iterative mode."""
    if iterative_mode and existing_manifest is not None:
        for sname, sresult in existing_manifest.stages.items():
            if sname not in session._stages:
                session.record_stage(sname, StageResult(
                    status="skipped", reason="iterative-unchanged",
                ))


# ---------------------------------------------------------------------------
# Phase 7: Skeleton copy for E16 baseline
# ---------------------------------------------------------------------------

def copy_skeleton(output_dir: Path, module_name: str, module_dir: Path) -> None:
    """Copy .py files from the rendered module for E16 baseline comparison."""
    try:
        skeleton_dir = output_dir / ".amil-skeleton" / module_name
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


# ---------------------------------------------------------------------------
# Phase 8: Artifact computation + manifest + hooks
# ---------------------------------------------------------------------------

def compute_artifacts_and_notify(
    created_files: list[Path],
    module_dir: Path,
    module_name: str,
    session: GenerationSession,
    preprocessors_run: list[str],
    pre_duration_ms: int,
    spec: dict[str, Any],
    hooks: list[RenderHook] | None,
) -> None:
    """Build artifact info, finalize manifest, and fire on_render_complete hook."""
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


# ---------------------------------------------------------------------------
# Phase 9: Post-render validation: semantic + OLS
# ---------------------------------------------------------------------------

def run_post_render_validation(
    module_dir: Path,
    module_name: str,
    created_files: list[Path],
    all_warnings: list,
    *,
    skip_semantic_validation: bool,
    ols_client: Any,
) -> None:
    """Run semantic validation and optionally OLS structural validation."""
    if skip_semantic_validation or not created_files:
        return

    # Semantic validation
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

    # OLS structural validation
    if ols_client is not None:
        _run_ols_validation(ols_client, module_dir, module_name, all_warnings)


def _run_ols_validation(
    ols_client: Any,
    module_dir: Path,
    module_name: str,
    all_warnings: list,
) -> None:
    """Run odoo-ls structural validation and auto-fix loop."""
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


def save_spec_stash_safe(spec_raw: dict[str, Any], module_dir: Path) -> None:
    """Save spec stash after successful generation (non-blocking on failure)."""
    from amil_utils.iterative import save_spec_stash
    try:
        save_spec_stash(spec_raw, module_dir)
    except Exception as exc:
        _logger.warning("Failed to save spec stash: %s", exc)
