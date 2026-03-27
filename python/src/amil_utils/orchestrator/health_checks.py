"""Health checks — Individual validation checks for .planning/ directory.

Each check function returns a list of HealthIssue dataclasses. The orchestrator
in health.py converts them to the legacy dict format for backward compatibility.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

logger = logging.getLogger(__name__)


# ── Data structures ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class HealthIssue:
    """A single health check finding."""

    severity: str  # "error", "warning", "info"
    code: str
    message: str
    fix: str
    repairable: bool = False

    def to_dict(self) -> dict:
        """Convert to legacy dict format."""
        return {
            "code": self.code,
            "message": self.message,
            "fix": self.fix,
            "repairable": self.repairable,
        }


# ── Individual check functions ───────────────────────────────────────────────


def check_planning_dir(planning_dir: Path) -> list[HealthIssue]:
    """Check 1: .planning/ directory exists (E001)."""
    if not planning_dir.exists():
        return [
            HealthIssue(
                "error", "E001",
                ".planning/ directory not found",
                "Run /amil:new-project to initialize",
            )
        ]
    return []


def check_project_md(project_path: Path) -> list[HealthIssue]:
    """Check 2: PROJECT.md exists and has required sections (E002, W001)."""
    issues: list[HealthIssue] = []
    if not project_path.exists():
        issues.append(
            HealthIssue("error", "E002", "PROJECT.md not found", "Run /amil:new-project to create")
        )
    else:
        content = project_path.read_text(encoding="utf-8")
        for section in ("## What This Is", "## Core Value", "## Requirements"):
            if section not in content:
                issues.append(
                    HealthIssue(
                        "warning", "W001",
                        f"PROJECT.md missing section: {section}",
                        "Add section manually",
                    )
                )
    return issues


def check_roadmap_md(roadmap_path: Path) -> list[HealthIssue]:
    """Check 3: ROADMAP.md exists (E003)."""
    if not roadmap_path.exists():
        return [
            HealthIssue(
                "error", "E003",
                "ROADMAP.md not found",
                "Run /amil:new-milestone to create roadmap",
            )
        ]
    return []


def check_state_md(
    state_path: Path, phases_dir: Path,
) -> tuple[list[HealthIssue], list[str]]:
    """Check 4: STATE.md exists and references valid phases (E004, W002).

    Returns (issues, repair_names) since this check can queue repairs.
    """
    issues: list[HealthIssue] = []
    repair_names: list[str] = []

    if not state_path.exists():
        issues.append(
            HealthIssue(
                "error", "E004",
                "STATE.md not found",
                "Run /amil:health --repair to regenerate",
                repairable=True,
            )
        )
        repair_names.append("regenerateState")
        return issues, repair_names

    state_content = state_path.read_text(encoding="utf-8")
    phase_refs = [
        m.group(1) for m in re.finditer(r"[Pp]hase\s+(\d+(?:\.\d+)*)", state_content)
    ]

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
                issues.append(
                    HealthIssue(
                        "warning", "W002",
                        f"STATE.md references phase {ref}, but only phases {sorted_phases} exist",
                        "Run /amil:health --repair to regenerate STATE.md",
                        repairable=True,
                    )
                )
                if "regenerateState" not in repair_names:
                    repair_names.append("regenerateState")

    return issues, repair_names


def check_config_json(
    config_path: Path,
) -> tuple[list[HealthIssue], list[str]]:
    """Check 5: config.json valid JSON + valid schema (E005, W003, W004, W008).

    Returns (issues, repair_names) since this check can queue repairs.
    """
    issues: list[HealthIssue] = []
    repair_names: list[str] = []

    if not config_path.exists():
        issues.append(
            HealthIssue(
                "warning", "W003",
                "config.json not found",
                "Run /amil:health --repair to create with defaults",
                repairable=True,
            )
        )
        repair_names.append("createConfig")
        return issues, repair_names

    # Validate JSON structure
    try:
        raw = config_path.read_text(encoding="utf-8")
        parsed = json.loads(raw)
        valid_profiles = ("quality", "balanced", "budget")
        if parsed.get("model_profile") and parsed["model_profile"] not in valid_profiles:
            issues.append(
                HealthIssue(
                    "warning", "W004",
                    f'config.json: invalid model_profile "{parsed["model_profile"]}"',
                    f"Valid values: {', '.join(valid_profiles)}",
                )
            )
    except (json.JSONDecodeError, ValueError) as err:
        issues.append(
            HealthIssue(
                "error", "E005",
                f"config.json: JSON parse error - {err}",
                "Run /amil:health --repair to reset to defaults",
                repairable=True,
            )
        )
        repair_names.append("resetConfig")
        return issues, repair_names

    # Check 5b: Nyquist validation key presence
    try:
        config_raw = config_path.read_text(encoding="utf-8")
        config_parsed = json.loads(config_raw)
        if (
            config_parsed.get("workflow")
            and config_parsed["workflow"].get("nyquist_validation") is None
        ):
            issues.append(
                HealthIssue(
                    "warning", "W008",
                    "config.json: workflow.nyquist_validation absent (defaults to enabled but agents may skip)",
                    "Run /amil:health --repair to add key",
                    repairable=True,
                )
            )
            if "addNyquistKey" not in repair_names:
                repair_names.append("addNyquistKey")
    except (json.JSONDecodeError, ValueError) as exc:
        logger.debug("Failed to parse config.json for Nyquist check: %s", exc)

    return issues, repair_names


def check_phase_naming(phases_dir: Path) -> list[HealthIssue]:
    """Check 6: Phase directory naming — NN-name format (W005)."""
    issues: list[HealthIssue] = []
    try:
        for entry in phases_dir.iterdir():
            if entry.is_dir() and not re.match(r"^\d{2}(?:\.\d+)*-[\w-]+$", entry.name):
                issues.append(
                    HealthIssue(
                        "warning", "W005",
                        f'Phase directory "{entry.name}" doesn\'t follow NN-name format',
                        "Rename to match pattern (e.g., 01-setup)",
                    )
                )
    except OSError as exc:
        logger.debug("Failed to check phase directory naming: %s", exc)
    return issues


def check_orphaned_plans(phases_dir: Path) -> list[HealthIssue]:
    """Check 7: Orphaned plans (PLAN without SUMMARY) and Nyquist validation (I001, W009)."""
    issues: list[HealthIssue] = []

    # 7a: Orphaned plans
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
                    issues.append(
                        HealthIssue(
                            "info", "I001",
                            f"{entry.name}/{plan} has no SUMMARY.md",
                            "May be in progress",
                        )
                    )
    except OSError as exc:
        logger.debug("Failed to check orphaned plans: %s", exc)

    # 7b: Nyquist VALIDATION.md consistency
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
                    issues.append(
                        HealthIssue(
                            "warning", "W009",
                            f"Phase {entry.name}: has Validation Architecture in RESEARCH.md but no VALIDATION.md",
                            "Re-run /amil:plan-phase with --research to regenerate",
                        )
                    )
    except OSError as exc:
        logger.debug("Failed to check Nyquist validation consistency: %s", exc)

    return issues


def check_roadmap_disk_consistency(
    roadmap_path: Path, phases_dir: Path,
) -> list[HealthIssue]:
    """Check 8: Roadmap/disk consistency (W006, W007)."""
    issues: list[HealthIssue] = []

    if not roadmap_path.exists() or not phases_dir.exists():
        return issues

    roadmap_content = roadmap_path.read_text(encoding="utf-8")
    roadmap_phases: set[str] = set()
    for m in re.finditer(
        r"#{2,4}\s*Phase\s+(\d+[A-Z]?(?:\.\d+)*)\s*:", roadmap_content, re.IGNORECASE
    ):
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
            issues.append(
                HealthIssue(
                    "warning", "W006",
                    f"Phase {p} in ROADMAP.md but no directory on disk",
                    "Create phase directory or remove from roadmap",
                )
            )

    for p in disk_phases_set:
        unpadded = str(int(p))
        if p not in roadmap_phases and unpadded not in roadmap_phases:
            issues.append(
                HealthIssue(
                    "warning", "W007",
                    f"Phase {p} exists on disk but not in ROADMAP.md",
                    "Add to roadmap or remove directory",
                )
            )

    return issues


# ── Repair logic ─────────────────────────────────────────────────────────────

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


def perform_repairs(
    repair_names: list[str],
    *,
    config_path: Path,
    state_path: Path,
    cwd: Path,
) -> list[dict]:
    """Execute queued repairs and return action results.

    Imports from orchestrator.core/state are deferred to avoid circular
    imports at module level.
    """
    from amil_utils.orchestrator.core import get_milestone_info
    from amil_utils.orchestrator.state import write_state_md

    repair_actions: list[dict] = []

    for repair_name in repair_names:
        try:
            if repair_name in ("createConfig", "resetConfig"):
                config_path.write_text(
                    json.dumps(_CONFIG_DEFAULTS, indent=2) + "\n",
                    encoding="utf-8",
                )
                repair_actions.append(
                    {"action": repair_name, "success": True, "path": "config.json"}
                )

            elif repair_name == "regenerateState":
                if state_path.exists():
                    timestamp = date.today().isoformat()
                    backup_path = state_path.with_name(f"STATE.md.bak-{timestamp}")
                    import shutil

                    shutil.copy2(str(state_path), str(backup_path))
                    repair_actions.append(
                        {"action": "backupState", "success": True, "path": str(backup_path.name)}
                    )

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
                repair_actions.append(
                    {"action": repair_name, "success": True, "path": "STATE.md"}
                )

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
                        repair_actions.append(
                            {"action": repair_name, "success": True, "path": "config.json"}
                        )
                    except (json.JSONDecodeError, ValueError) as err:
                        repair_actions.append(
                            {"action": repair_name, "success": False, "error": str(err)}
                        )

        except OSError as err:
            repair_actions.append(
                {"action": repair_name, "success": False, "error": str(err)}
            )

    return repair_actions
