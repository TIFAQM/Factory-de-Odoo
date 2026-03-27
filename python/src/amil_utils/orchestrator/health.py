"""Health — Planning directory health validation and repair.

Ported from orchestrator/amil/bin/lib/health.cjs (308 lines, since deleted).
Validates .planning/ directory structure and optionally repairs issues.

Check logic lives in health_checks.py; this module orchestrates checks and
converts HealthIssue dataclasses into the legacy dict return format.
"""
from __future__ import annotations

import logging
from pathlib import Path

from amil_utils.orchestrator.health_checks import (
    HealthIssue,
    check_config_json,
    check_orphaned_plans,
    check_phase_naming,
    check_planning_dir,
    check_project_md,
    check_roadmap_disk_consistency,
    check_roadmap_md,
    check_state_md,
    perform_repairs,
)

logger = logging.getLogger(__name__)


# ── Public API ───────────────────────────────────────────────────────────────


def validate_health(cwd: str | Path, *, repair: bool = False) -> dict:
    """Validate .planning/ directory health and optionally repair issues.

    Checks:
        E001 — .planning/ directory exists
        E002 — PROJECT.md with required sections
        E003 — ROADMAP.md exists
        E004 — STATE.md exists and references valid phases
        E005 — config.json is valid JSON
        W001-W009 — Various warnings (sections, naming, consistency)
        I001 — Orphaned plans (PLAN without SUMMARY)

    Returns:
        {"status": "healthy"|"degraded"|"broken", "errors": [...],
         "warnings": [...], "info": [...], "repairable_count": int,
         "repairs_performed": [...] | None}
    """
    cwd = Path(cwd)
    planning_dir = cwd / ".planning"
    project_path = planning_dir / "PROJECT.md"
    roadmap_path = planning_dir / "ROADMAP.md"
    state_path = planning_dir / "STATE.md"
    config_path = planning_dir / "config.json"
    phases_dir = planning_dir / "phases"

    # ── Check 1: early exit if .planning/ is missing ─────────────────────
    planning_issues = check_planning_dir(planning_dir)
    if planning_issues:
        return _build_result(planning_issues, repair_actions=[])

    # ── Checks 2-8 ──────────────────────────────────────────────────────
    all_issues: list[HealthIssue] = []
    all_repairs: list[str] = []

    all_issues.extend(check_project_md(project_path))
    all_issues.extend(check_roadmap_md(roadmap_path))

    state_issues, state_repairs = check_state_md(state_path, phases_dir)
    all_issues.extend(state_issues)
    all_repairs.extend(state_repairs)

    config_issues, config_repairs = check_config_json(config_path)
    all_issues.extend(config_issues)
    all_repairs.extend(config_repairs)

    all_issues.extend(check_phase_naming(phases_dir))
    all_issues.extend(check_orphaned_plans(phases_dir))
    all_issues.extend(check_roadmap_disk_consistency(roadmap_path, phases_dir))

    # ── Perform repairs if requested ─────────────────────────────────────
    repair_actions: list[dict] = []
    if repair and all_repairs:
        repair_actions = perform_repairs(
            all_repairs,
            config_path=config_path,
            state_path=state_path,
            cwd=cwd,
        )

    return _build_result(all_issues, repair_actions=repair_actions)


def _build_result(
    issues: list[HealthIssue],
    *,
    repair_actions: list[dict],
) -> dict:
    """Convert HealthIssue list into the legacy dict return format."""
    errors = [i.to_dict() for i in issues if i.severity == "error"]
    warnings = [i.to_dict() for i in issues if i.severity == "warning"]
    info = [i.to_dict() for i in issues if i.severity == "info"]

    if errors:
        status = "broken"
    elif warnings:
        status = "degraded"
    else:
        status = "healthy"

    repairable_count = sum(1 for e in errors if e.get("repairable")) + sum(
        1 for w in warnings if w.get("repairable")
    )

    result: dict = {
        "status": status,
        "errors": errors,
        "warnings": warnings,
        "info": info,
        "repairable_count": repairable_count,
    }
    if repair_actions:
        result["repairs_performed"] = repair_actions

    return result
