"""Health — Planning directory health validation and repair.

Ported from orchestrator/amil/bin/lib/health.cjs (308 lines, since deleted).
Validates .planning/ directory structure and optionally repairs issues.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date
from pathlib import Path

logger = logging.getLogger(__name__)

from amil_utils.orchestrator.core import get_milestone_info
from amil_utils.orchestrator.state import write_state_md


# ── Internal helpers ─────────────────────────────────────────────────────────


def _add_issue(
    severity: str,
    code: str,
    message: str,
    fix: str,
    *,
    repairable: bool = False,
    errors: list[dict],
    warnings: list[dict],
    info: list[dict],
) -> None:
    """Append an issue to the appropriate severity list."""
    issue = {"code": code, "message": message, "fix": fix, "repairable": repairable}
    if severity == "error":
        errors.append(issue)
    elif severity == "warning":
        warnings.append(issue)
    else:
        info.append(issue)


_CONFIG_DEFAULTS: dict = {
    "model_profile": "balanced",
    "commit_docs": True,
    "search_gitignored": False,
    "branching_strategy": "none",
    "research": True,
    "plan_checker": True,
    "verifier": True,
    "parallelization": True,
}


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

    errors: list[dict] = []
    warnings: list[dict] = []
    info: list[dict] = []
    repairs: list[str] = []

    issue = lambda sev, code, msg, fix, **kw: _add_issue(
        sev, code, msg, fix, errors=errors, warnings=warnings, info=info, **kw
    )

    # ── Check 1: .planning/ exists ───────────────────────────────────────
    if not planning_dir.exists():
        issue("error", "E001", ".planning/ directory not found", "Run /amil:new-project to initialize")
        return {
            "status": "broken",
            "errors": errors,
            "warnings": warnings,
            "info": info,
            "repairable_count": 0,
        }

    # ── Check 2: PROJECT.md exists and has required sections ─────────────
    if not project_path.exists():
        issue("error", "E002", "PROJECT.md not found", "Run /amil:new-project to create")
    else:
        content = project_path.read_text(encoding="utf-8")
        for section in ("## What This Is", "## Core Value", "## Requirements"):
            if section not in content:
                issue("warning", "W001", f"PROJECT.md missing section: {section}", "Add section manually")

    # ── Check 3: ROADMAP.md exists ───────────────────────────────────────
    if not roadmap_path.exists():
        issue("error", "E003", "ROADMAP.md not found", "Run /amil:new-milestone to create roadmap")

    # ── Check 4: STATE.md exists and references valid phases ─────────────
    if not state_path.exists():
        issue(
            "error", "E004", "STATE.md not found",
            "Run /amil:health --repair to regenerate", repairable=True,
        )
        repairs.append("regenerateState")
    else:
        state_content = state_path.read_text(encoding="utf-8")
        phase_refs = [m.group(1) for m in re.finditer(r"[Pp]hase\s+(\d+(?:\.\d+)*)", state_content)]

        disk_phases: set[str] = set()
        try:
            for entry in phases_dir.iterdir():
                if entry.is_dir():
                    m = re.match(r"^(\d+(?:\.\d+)*)", entry.name)
                    if m:
                        disk_phases.add(m.group(1))
        except OSError as exc:
            logger.debug("Failed to read phases directory for state check: %s", exc)

        for ref in phase_refs:
            normalized_ref = str(int(ref)).zfill(2)
            if (
                ref not in disk_phases
                and normalized_ref not in disk_phases
                and str(int(ref)) not in disk_phases
            ):
                if disk_phases:
                    sorted_phases = ", ".join(sorted(disk_phases))
                    issue(
                        "warning", "W002",
                        f"STATE.md references phase {ref}, but only phases {sorted_phases} exist",
                        "Run /amil:health --repair to regenerate STATE.md",
                        repairable=True,
                    )
                    if "regenerateState" not in repairs:
                        repairs.append("regenerateState")

    # ── Check 5: config.json valid JSON + valid schema ───────────────────
    if not config_path.exists():
        issue(
            "warning", "W003", "config.json not found",
            "Run /amil:health --repair to create with defaults", repairable=True,
        )
        repairs.append("createConfig")
    else:
        try:
            raw = config_path.read_text(encoding="utf-8")
            parsed = json.loads(raw)
            valid_profiles = ("quality", "balanced", "budget")
            if parsed.get("model_profile") and parsed["model_profile"] not in valid_profiles:
                issue(
                    "warning", "W004",
                    f'config.json: invalid model_profile "{parsed["model_profile"]}"',
                    f"Valid values: {', '.join(valid_profiles)}",
                )
        except (json.JSONDecodeError, ValueError) as err:
            issue(
                "error", "E005", f"config.json: JSON parse error - {err}",
                "Run /amil:health --repair to reset to defaults", repairable=True,
            )
            repairs.append("resetConfig")

    # ── Check 5b: Nyquist validation key presence ────────────────────────
    if config_path.exists():
        try:
            config_raw = config_path.read_text(encoding="utf-8")
            config_parsed = json.loads(config_raw)
            if (
                config_parsed.get("workflow")
                and config_parsed["workflow"].get("nyquist_validation") is None
            ):
                issue(
                    "warning", "W008",
                    "config.json: workflow.nyquist_validation absent (defaults to enabled but agents may skip)",
                    "Run /amil:health --repair to add key", repairable=True,
                )
                if "addNyquistKey" not in repairs:
                    repairs.append("addNyquistKey")
        except (json.JSONDecodeError, ValueError) as exc:
            logger.debug("Failed to parse config.json for Nyquist check: %s", exc)

    # ── Check 6: Phase directory naming (NN-name format) ─────────────────
    try:
        for entry in phases_dir.iterdir():
            if entry.is_dir() and not re.match(r"^\d{2}(?:\.\d+)*-[\w-]+$", entry.name):
                issue(
                    "warning", "W005",
                    f'Phase directory "{entry.name}" doesn\'t follow NN-name format',
                    "Rename to match pattern (e.g., 01-setup)",
                )
    except OSError as exc:
        logger.debug("Failed to check phase directory naming: %s", exc)

    # ── Check 7: Orphaned plans (PLAN without SUMMARY) ───────────────────
    try:
        for entry in phases_dir.iterdir():
            if not entry.is_dir():
                continue
            phase_files = [f.name for f in entry.iterdir() if f.is_file()]
            plans = [f for f in phase_files if f.endswith("-PLAN.md") or f == "PLAN.md"]
            summaries = [f for f in phase_files if f.endswith("-SUMMARY.md") or f == "SUMMARY.md"]
            summary_bases = {
                s.replace("-SUMMARY.md", "").replace("SUMMARY.md", "") for s in summaries
            }

            for plan in plans:
                plan_base = plan.replace("-PLAN.md", "").replace("PLAN.md", "")
                if plan_base not in summary_bases:
                    issue("info", "I001", f"{entry.name}/{plan} has no SUMMARY.md", "May be in progress")
    except OSError as exc:
        logger.debug("Failed to check orphaned plans: %s", exc)

    # ── Check 7b: Nyquist VALIDATION.md consistency ──────────────────────
    try:
        for entry in phases_dir.iterdir():
            if not entry.is_dir():
                continue
            phase_files = [f.name for f in entry.iterdir() if f.is_file()]
            has_research = any(f.endswith("-RESEARCH.md") for f in phase_files)
            has_validation = any(f.endswith("-VALIDATION.md") for f in phase_files)
            if has_research and not has_validation:
                research_file = next(f for f in phase_files if f.endswith("-RESEARCH.md"))
                research_content = (entry / research_file).read_text(encoding="utf-8")
                if "## Validation Architecture" in research_content:
                    issue(
                        "warning", "W009",
                        f"Phase {entry.name}: has Validation Architecture in RESEARCH.md but no VALIDATION.md",
                        "Re-run /amil:plan-phase with --research to regenerate",
                    )
    except OSError as exc:
        logger.debug("Failed to check Nyquist validation consistency: %s", exc)

    # ── Check 8: Roadmap/disk consistency (only when phases dir exists) ─
    if roadmap_path.exists() and phases_dir.exists():
        roadmap_content = roadmap_path.read_text(encoding="utf-8")
        roadmap_phases: set[str] = set()
        for m in re.finditer(r"#{2,4}\s*Phase\s+(\d+[A-Z]?(?:\.\d+)*)\s*:", roadmap_content, re.IGNORECASE):
            roadmap_phases.add(m.group(1))

        disk_phases_set: set[str] = set()
        try:
            for entry in phases_dir.iterdir():
                if entry.is_dir():
                    dm = re.match(r"^(\d+[A-Z]?(?:\.\d+)*)", entry.name, re.IGNORECASE)
                    if dm:
                        disk_phases_set.add(dm.group(1))
        except OSError as exc:
            logger.debug("Failed to read phases for roadmap/disk consistency: %s", exc)

        for p in roadmap_phases:
            padded = str(int(p)).zfill(2)
            if p not in disk_phases_set and padded not in disk_phases_set:
                issue(
                    "warning", "W006",
                    f"Phase {p} in ROADMAP.md but no directory on disk",
                    "Create phase directory or remove from roadmap",
                )

        for p in disk_phases_set:
            unpadded = str(int(p))
            if p not in roadmap_phases and unpadded not in roadmap_phases:
                issue(
                    "warning", "W007",
                    f"Phase {p} exists on disk but not in ROADMAP.md",
                    "Add to roadmap or remove directory",
                )

    # ── Perform repairs if requested ─────────────────────────────────────
    repair_actions: list[dict] = []
    if repair and repairs:
        for repair_name in repairs:
            try:
                if repair_name in ("createConfig", "resetConfig"):
                    config_path.write_text(
                        json.dumps(_CONFIG_DEFAULTS, indent=2) + "\n",
                        encoding="utf-8",
                    )
                    repair_actions.append({"action": repair_name, "success": True, "path": "config.json"})

                elif repair_name == "regenerateState":
                    if state_path.exists():
                        timestamp = date.today().isoformat()
                        backup_path = state_path.with_name(f"STATE.md.bak-{timestamp}")
                        import shutil
                        shutil.copy2(str(state_path), str(backup_path))
                        repair_actions.append({"action": "backupState", "success": True, "path": str(backup_path.name)})

                    milestone = get_milestone_info(cwd)
                    state_content = (
                        f"# Session State\n\n"
                        f"## Project Reference\n\n"
                        f"See: .planning/PROJECT.md\n\n"
                        f"## Position\n\n"
                        f"**Milestone:** {milestone['version']} {milestone['name']}\n"
                        f"**Current phase:** (determining...)\n"
                        f"**Status:** Resuming\n\n"
                        f"## Session Log\n\n"
                        f"- {date.today().isoformat()}: STATE.md regenerated by /amil:health --repair\n"
                    )
                    write_state_md(state_path, state_content, cwd)
                    repair_actions.append({"action": repair_name, "success": True, "path": "STATE.md"})

                elif repair_name == "addNyquistKey":
                    if config_path.exists():
                        try:
                            cfg_raw = config_path.read_text(encoding="utf-8")
                            cfg_parsed = json.loads(cfg_raw)
                            if "workflow" not in cfg_parsed:
                                cfg_parsed["workflow"] = {}
                            if cfg_parsed["workflow"].get("nyquist_validation") is None:
                                cfg_parsed["workflow"]["nyquist_validation"] = True
                                config_path.write_text(
                                    json.dumps(cfg_parsed, indent=2) + "\n",
                                    encoding="utf-8",
                                )
                            repair_actions.append({"action": repair_name, "success": True, "path": "config.json"})
                        except (json.JSONDecodeError, ValueError) as err:
                            repair_actions.append({"action": repair_name, "success": False, "error": str(err)})

            except OSError as err:
                repair_actions.append({"action": repair_name, "success": False, "error": str(err)})

    # ── Determine overall status ─────────────────────────────────────────
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
