"""Init Commands — Compound init commands for workflow bootstrapping.

Ported from orchestrator/amil/bin/lib/init.cjs (702 lines, since deleted).
Each function gathers context (config, models, file existence, phase state)
into a single dict that a workflow can consume.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

from amil_utils.orchestrator.core import (
    find_phase,
    generate_slug,
    get_milestone_info,
    get_roadmap_phase,
    has_source_files,
    load_config,
    path_exists,
    resolve_model,
    scan_todos,
    to_posix_path,
)

# Artifact names to discover in phase directories
_PHASE_ARTIFACT_NAMES = ["CONTEXT.md", "RESEARCH.md", "VERIFICATION.md", "UAT.md"]


def discover_phase_artifacts(cwd: str | Path, phase_dir: str) -> dict[str, str]:
    """Discover phase artifacts (CONTEXT, RESEARCH, VERIFICATION, UAT).

    Returns a dict with *_path keys for each found artifact file.
    """
    artifacts: dict[str, str] = {}
    phase_dir_full = Path(cwd) / phase_dir
    try:
        files = [f.name for f in phase_dir_full.iterdir() if f.is_file()]
    except OSError:
        return artifacts

    for artifact_name in _PHASE_ARTIFACT_NAMES:
        base_name = artifact_name.split(".")[0]  # e.g. 'CONTEXT'
        found = next(
            (f for f in files if f.endswith(f"-{artifact_name}") or f == artifact_name),
            None,
        )
        if found:
            key = base_name.lower() + "_path"
            artifacts[key] = to_posix_path(f"{phase_dir}/{found}")

    return artifacts


def _extract_phase_req_ids(cwd: str | Path, phase: str) -> str | None:
    """Extract requirement IDs from roadmap phase section."""
    roadmap_phase = get_roadmap_phase(cwd, phase)
    if not roadmap_phase or not roadmap_phase.get("section"):
        return None
    section = roadmap_phase["section"]
    req_match = re.search(
        r"^\*\*Requirements\*\*:[^\S\n]*([^\n]*)$", section, re.MULTILINE
    )
    if not req_match:
        return None
    extracted = (
        re.sub(r"[\[\]]", "", req_match.group(1))
        .split(",")
    )
    cleaned = ", ".join(s.strip() for s in extracted if s.strip())
    return cleaned if cleaned and cleaned != "TBD" else None


def init_execute_phase(cwd: str | Path, phase: str) -> dict:
    """Load context for execute-phase workflow."""
    if not phase:
        raise ValueError("phase required for init execute-phase")

    config = load_config(cwd)
    phase_info = find_phase(cwd, phase)
    milestone = get_milestone_info(cwd)
    phase_req_ids = _extract_phase_req_ids(cwd, phase)

    branch_name = _compute_branch_name(config, phase_info, milestone)

    return {
        # Models
        "executor_model": resolve_model(cwd, "amil-executor"),
        "verifier_model": resolve_model(cwd, "amil-verifier"),
        # Config flags
        "commit_docs": config.get("commit_docs"),
        "parallelization": config.get("parallelization"),
        "branching_strategy": config.get("branching_strategy"),
        "phase_branch_template": config.get("phase_branch_template"),
        "milestone_branch_template": config.get("milestone_branch_template"),
        "verifier_enabled": config.get("verifier"),
        # Phase info
        "phase_found": phase_info is not None,
        "phase_dir": (phase_info or {}).get("directory"),
        "phase_number": (phase_info or {}).get("phase_number"),
        "phase_name": (phase_info or {}).get("phase_name"),
        "phase_slug": (phase_info or {}).get("phase_slug"),
        "phase_req_ids": phase_req_ids,
        # Plan inventory
        "plans": (phase_info or {}).get("plans", []),
        "summaries": (phase_info or {}).get("summaries", []),
        "incomplete_plans": (phase_info or {}).get("incomplete_plans", []),
        "plan_count": len((phase_info or {}).get("plans", [])),
        "incomplete_count": len((phase_info or {}).get("incomplete_plans", [])),
        # Branch name
        "branch_name": branch_name,
        # Milestone info
        "milestone_version": milestone["version"],
        "milestone_name": milestone["name"],
        "milestone_slug": generate_slug(milestone["name"]),
        # File existence
        "state_exists": path_exists(cwd, ".planning/STATE.md"),
        "roadmap_exists": path_exists(cwd, ".planning/ROADMAP.md"),
        "config_exists": path_exists(cwd, ".planning/config.json"),
        # File paths
        "state_path": ".planning/STATE.md",
        "roadmap_path": ".planning/ROADMAP.md",
        "config_path": ".planning/config.json",
    }


def _compute_branch_name(
    config: dict, phase_info: dict | None, milestone: dict
) -> str | None:
    """Compute branch name from config strategy and templates."""
    strategy = config.get("branching_strategy")

    if strategy == "phase" and phase_info:
        template = config.get("phase_branch_template", "phase-{phase}-{slug}")
        return template.replace(
            "{phase}", phase_info.get("phase_number", "")
        ).replace(
            "{slug}", phase_info.get("phase_slug") or "phase"
        )

    if strategy == "milestone":
        template = config.get(
            "milestone_branch_template", "milestone-{milestone}-{slug}"
        )
        return template.replace(
            "{milestone}", milestone["version"]
        ).replace(
            "{slug}", generate_slug(milestone["name"]) or "milestone"
        )

    return None


def init_plan_phase(cwd: str | Path, phase: str) -> dict:
    """Load context for plan-phase workflow."""
    if not phase:
        raise ValueError("phase required for init plan-phase")

    config = load_config(cwd)
    phase_info = find_phase(cwd, phase)
    phase_req_ids = _extract_phase_req_ids(cwd, phase)

    result: dict = {
        # Models
        "researcher_model": resolve_model(cwd, "amil-phase-researcher"),
        "planner_model": resolve_model(cwd, "amil-planner"),
        "checker_model": resolve_model(cwd, "amil-plan-checker"),
        # Workflow flags
        "research_enabled": config.get("research"),
        "plan_checker_enabled": config.get("plan_checker"),
        "nyquist_validation_enabled": config.get("nyquist_validation"),
        "commit_docs": config.get("commit_docs"),
        # Phase info
        "phase_found": phase_info is not None,
        "phase_dir": (phase_info or {}).get("directory"),
        "phase_number": (phase_info or {}).get("phase_number"),
        "phase_name": (phase_info or {}).get("phase_name"),
        "phase_slug": (phase_info or {}).get("phase_slug"),
        "padded_phase": (
            (phase_info or {}).get("phase_number", "").zfill(2)
            if phase_info
            else None
        ),
        "phase_req_ids": phase_req_ids,
        # Existing artifacts
        "has_research": (phase_info or {}).get("has_research", False),
        "has_context": (phase_info or {}).get("has_context", False),
        "has_plans": len((phase_info or {}).get("plans", [])) > 0,
        "plan_count": len((phase_info or {}).get("plans", [])),
        # Environment
        "planning_exists": path_exists(cwd, ".planning"),
        "roadmap_exists": path_exists(cwd, ".planning/ROADMAP.md"),
        # File paths
        "state_path": ".planning/STATE.md",
        "roadmap_path": ".planning/ROADMAP.md",
        "requirements_path": ".planning/REQUIREMENTS.md",
    }

    if phase_info and phase_info.get("directory"):
        result.update(discover_phase_artifacts(cwd, phase_info["directory"]))

    return result


def init_new_project(cwd: str | Path) -> dict:
    """Load context for new-project workflow."""
    config = load_config(cwd)

    # Detect Brave Search API key availability
    home = Path.home()
    brave_key_file = home / ".amil" / "brave_api_key"
    has_brave = bool(
        os.environ.get("BRAVE_API_KEY") or brave_key_file.exists()
    )

    has_code = has_source_files(cwd)
    has_package = any(
        path_exists(cwd, f)
        for f in [
            "package.json", "requirements.txt", "Cargo.toml",
            "go.mod", "Package.swift",
        ]
    )

    return {
        # Models
        "researcher_model": resolve_model(cwd, "amil-project-researcher"),
        "synthesizer_model": resolve_model(cwd, "amil-research-synthesizer"),
        "roadmapper_model": resolve_model(cwd, "amil-roadmapper"),
        # Config
        "commit_docs": config.get("commit_docs"),
        # Existing state
        "project_exists": path_exists(cwd, ".planning/PROJECT.md"),
        "has_codebase_map": path_exists(cwd, ".planning/codebase"),
        "planning_exists": path_exists(cwd, ".planning"),
        # Brownfield detection
        "has_existing_code": has_code,
        "has_package_file": has_package,
        "is_brownfield": has_code or has_package,
        "needs_codebase_map": (has_code or has_package)
            and not path_exists(cwd, ".planning/codebase"),
        # Git state
        "has_git": path_exists(cwd, ".git"),
        # Enhanced search
        "brave_search_available": has_brave,
        # File paths
        "project_path": ".planning/PROJECT.md",
    }


def init_new_milestone(cwd: str | Path) -> dict:
    """Load context for new-milestone workflow."""
    config = load_config(cwd)
    milestone = get_milestone_info(cwd)

    return {
        # Models
        "researcher_model": resolve_model(cwd, "amil-project-researcher"),
        "synthesizer_model": resolve_model(cwd, "amil-research-synthesizer"),
        "roadmapper_model": resolve_model(cwd, "amil-roadmapper"),
        # Config
        "commit_docs": config.get("commit_docs"),
        "research_enabled": config.get("research"),
        # Current milestone
        "current_milestone": milestone["version"],
        "current_milestone_name": milestone["name"],
        # File existence
        "project_exists": path_exists(cwd, ".planning/PROJECT.md"),
        "roadmap_exists": path_exists(cwd, ".planning/ROADMAP.md"),
        "state_exists": path_exists(cwd, ".planning/STATE.md"),
        # File paths
        "project_path": ".planning/PROJECT.md",
        "roadmap_path": ".planning/ROADMAP.md",
        "state_path": ".planning/STATE.md",
    }


def init_quick(cwd: str | Path, description: str | None = None) -> dict:
    """Load context for quick task workflow."""
    config = load_config(cwd)
    now = datetime.now(tz=datetime.now().astimezone().tzinfo)
    slug = generate_slug(description)
    if slug:
        slug = slug[:40]

    # Find next quick task number
    quick_dir = Path(cwd) / ".planning" / "quick"
    next_num = 1
    try:
        existing = [
            int(f.name.split("-")[0])
            for f in quick_dir.iterdir()
            if re.match(r"^\d+-", f.name)
        ]
        if existing:
            next_num = max(existing) + 1
    except (OSError, ValueError) as exc:
        logger.debug("Failed to enumerate quick task numbers: %s", exc)

    return {
        # Models
        "planner_model": resolve_model(cwd, "amil-planner"),
        "executor_model": resolve_model(cwd, "amil-executor"),
        "checker_model": resolve_model(cwd, "amil-plan-checker"),
        "verifier_model": resolve_model(cwd, "amil-verifier"),
        # Config
        "commit_docs": config.get("commit_docs"),
        # Quick task info
        "next_num": next_num,
        "slug": slug,
        "description": description,
        # Timestamps
        "date": now.strftime("%Y-%m-%d"),
        "timestamp": now.isoformat(),
        # Paths
        "quick_dir": ".planning/quick",
        "task_dir": f".planning/quick/{next_num}-{slug}" if slug else None,
        # File existence
        "roadmap_exists": path_exists(cwd, ".planning/ROADMAP.md"),
        "planning_exists": path_exists(cwd, ".planning"),
    }


def init_resume(cwd: str | Path) -> dict:
    """Load context for resume-work workflow."""
    config = load_config(cwd)

    interrupted_agent_id: str | None = None
    try:
        agent_file = Path(cwd) / ".planning" / "current-agent-id.txt"
        interrupted_agent_id = agent_file.read_text(encoding="utf-8").strip()
    except OSError as exc:
        logger.debug("Failed to read interrupted agent ID: %s", exc)

    return {
        # File existence
        "state_exists": path_exists(cwd, ".planning/STATE.md"),
        "roadmap_exists": path_exists(cwd, ".planning/ROADMAP.md"),
        "project_exists": path_exists(cwd, ".planning/PROJECT.md"),
        "planning_exists": path_exists(cwd, ".planning"),
        # File paths
        "state_path": ".planning/STATE.md",
        "roadmap_path": ".planning/ROADMAP.md",
        "project_path": ".planning/PROJECT.md",
        # Agent state
        "has_interrupted_agent": interrupted_agent_id is not None,
        "interrupted_agent_id": interrupted_agent_id,
        # Config
        "commit_docs": config.get("commit_docs"),
    }


def init_verify_work(cwd: str | Path, phase: str) -> dict:
    """Load context for verify-work workflow."""
    if not phase:
        raise ValueError("phase required for init verify-work")

    config = load_config(cwd)
    phase_info = find_phase(cwd, phase)

    return {
        # Models
        "planner_model": resolve_model(cwd, "amil-planner"),
        "checker_model": resolve_model(cwd, "amil-plan-checker"),
        # Config
        "commit_docs": config.get("commit_docs"),
        # Phase info
        "phase_found": phase_info is not None,
        "phase_dir": (phase_info or {}).get("directory"),
        "phase_number": (phase_info or {}).get("phase_number"),
        "phase_name": (phase_info or {}).get("phase_name"),
        # Existing artifacts
        "has_verification": (phase_info or {}).get("has_verification", False),
    }


def init_phase_op(cwd: str | Path, phase: str | None = None) -> dict:
    """Load context for general phase operations."""
    config = load_config(cwd)
    phase_info = find_phase(cwd, phase) if phase else None

    # Fallback to ROADMAP.md if no directory exists
    if not phase_info and phase:
        roadmap_phase = get_roadmap_phase(cwd, phase)
        if roadmap_phase and roadmap_phase.get("found"):
            phase_name = roadmap_phase.get("phase_name")
            phase_info = {
                "found": True,
                "directory": None,
                "phase_number": roadmap_phase.get("phase_number"),
                "phase_name": phase_name,
                "phase_slug": generate_slug(phase_name) if phase_name else None,
                "plans": [],
                "summaries": [],
                "incomplete_plans": [],
                "has_research": False,
                "has_context": False,
                "has_verification": False,
            }

    result: dict = {
        # Config
        "commit_docs": config.get("commit_docs"),
        "brave_search": config.get("brave_search"),
        # Phase info
        "phase_found": phase_info is not None,
        "phase_dir": (phase_info or {}).get("directory"),
        "phase_number": (phase_info or {}).get("phase_number"),
        "phase_name": (phase_info or {}).get("phase_name"),
        "phase_slug": (phase_info or {}).get("phase_slug"),
        "padded_phase": (
            (phase_info or {}).get("phase_number", "").zfill(2)
            if phase_info
            else None
        ),
        # Existing artifacts
        "has_research": (phase_info or {}).get("has_research", False),
        "has_context": (phase_info or {}).get("has_context", False),
        "has_plans": len((phase_info or {}).get("plans", [])) > 0,
        "has_verification": (phase_info or {}).get("has_verification", False),
        "plan_count": len((phase_info or {}).get("plans", [])),
        # File existence
        "roadmap_exists": path_exists(cwd, ".planning/ROADMAP.md"),
        "planning_exists": path_exists(cwd, ".planning"),
        # File paths
        "state_path": ".planning/STATE.md",
        "roadmap_path": ".planning/ROADMAP.md",
        "requirements_path": ".planning/REQUIREMENTS.md",
    }

    if phase_info and phase_info.get("directory"):
        result.update(discover_phase_artifacts(cwd, phase_info["directory"]))

    return result


def init_todos(cwd: str | Path, area: str | None = None) -> dict:
    """Load context for todo management."""
    config = load_config(cwd)
    now = datetime.now(tz=datetime.now().astimezone().tzinfo)
    todos = scan_todos(cwd, area)

    return {
        # Config
        "commit_docs": config.get("commit_docs"),
        # Timestamps
        "date": now.strftime("%Y-%m-%d"),
        "timestamp": now.isoformat(),
        # Todo inventory
        "todo_count": len(todos),
        "todos": todos,
        "area_filter": area,
        # Paths
        "pending_dir": ".planning/todos/pending",
        "completed_dir": ".planning/todos/completed",
        # File existence
        "planning_exists": path_exists(cwd, ".planning"),
        "todos_dir_exists": path_exists(cwd, ".planning/todos"),
        "pending_dir_exists": path_exists(cwd, ".planning/todos/pending"),
    }


def init_milestone_op(cwd: str | Path) -> dict:
    """Load context for milestone operations."""
    config = load_config(cwd)
    milestone = get_milestone_info(cwd)

    phases_dir = Path(cwd) / ".planning" / "phases"
    phase_count = 0
    completed_phases = 0

    try:
        dirs = [e.name for e in phases_dir.iterdir() if e.is_dir()]
        phase_count = len(dirs)
        for dir_name in dirs:
            try:
                phase_files = [
                    f.name for f in (phases_dir / dir_name).iterdir() if f.is_file()
                ]
                if any(
                    f.endswith("-SUMMARY.md") or f == "SUMMARY.md"
                    for f in phase_files
                ):
                    completed_phases += 1
            except OSError:
                continue
    except OSError as exc:
        logger.debug("Failed to read phases directory for milestone: %s", exc)

    # Check archive
    archive_dir = Path(cwd) / ".planning" / "archive"
    archived_milestones: list[str] = []
    try:
        archived_milestones = [
            e.name for e in archive_dir.iterdir() if e.is_dir()
        ]
    except OSError as exc:
        logger.debug("Failed to read archive directory: %s", exc)

    return {
        # Config
        "commit_docs": config.get("commit_docs"),
        # Current milestone
        "milestone_version": milestone["version"],
        "milestone_name": milestone["name"],
        "milestone_slug": generate_slug(milestone["name"]),
        # Phase counts
        "phase_count": phase_count,
        "completed_phases": completed_phases,
        "all_phases_complete": phase_count > 0 and phase_count == completed_phases,
        # Archive
        "archived_milestones": archived_milestones,
        "archive_count": len(archived_milestones),
        # File existence
        "project_exists": path_exists(cwd, ".planning/PROJECT.md"),
        "roadmap_exists": path_exists(cwd, ".planning/ROADMAP.md"),
        "state_exists": path_exists(cwd, ".planning/STATE.md"),
        "archive_exists": path_exists(cwd, ".planning/archive"),
        "phases_dir_exists": path_exists(cwd, ".planning/phases"),
    }


def init_map_codebase(cwd: str | Path) -> dict:
    """Load context for map-codebase workflow."""
    config = load_config(cwd)
    codebase_dir = Path(cwd) / ".planning" / "codebase"

    existing_maps: list[str] = []
    try:
        existing_maps = [
            f.name for f in codebase_dir.iterdir() if f.name.endswith(".md")
        ]
    except OSError as exc:
        logger.debug("Failed to read codebase maps directory: %s", exc)

    return {
        # Models
        "mapper_model": resolve_model(cwd, "amil-codebase-mapper"),
        # Config
        "commit_docs": config.get("commit_docs"),
        "search_gitignored": config.get("search_gitignored"),
        "parallelization": config.get("parallelization"),
        # Paths
        "codebase_dir": ".planning/codebase",
        # Existing maps
        "existing_maps": existing_maps,
        "has_maps": len(existing_maps) > 0,
        # File existence
        "planning_exists": path_exists(cwd, ".planning"),
        "codebase_dir_exists": path_exists(cwd, ".planning/codebase"),
    }


def init_progress(cwd: str | Path) -> dict:
    """Load context for progress display."""
    config = load_config(cwd)
    milestone = get_milestone_info(cwd)

    phases_dir = Path(cwd) / ".planning" / "phases"
    phases: list[dict] = []
    current_phase = None
    next_phase = None

    try:
        dirs = sorted(e.name for e in phases_dir.iterdir() if e.is_dir())

        for dir_name in dirs:
            match = re.match(r"^(\d+(?:\.\d+)*)-?(.*)", dir_name)
            phase_number = match.group(1) if match else dir_name
            phase_name = match.group(2) if match and match.group(2) else None

            phase_path = phases_dir / dir_name
            phase_files = [f.name for f in phase_path.iterdir() if f.is_file()]
            plans = [
                f for f in phase_files
                if f.endswith("-PLAN.md") or f == "PLAN.md"
            ]
            summaries = [
                f for f in phase_files
                if f.endswith("-SUMMARY.md") or f == "SUMMARY.md"
            ]
            has_research = any(
                f.endswith("-RESEARCH.md") or f == "RESEARCH.md"
                for f in phase_files
            )

            if len(summaries) >= len(plans) and len(plans) > 0:
                status = "complete"
            elif len(plans) > 0:
                status = "in_progress"
            elif has_research:
                status = "researched"
            else:
                status = "pending"

            info = {
                "number": phase_number,
                "name": phase_name,
                "directory": f".planning/phases/{dir_name}",
                "status": status,
                "plan_count": len(plans),
                "summary_count": len(summaries),
                "has_research": has_research,
            }
            phases.append(info)

            if not current_phase and status in ("in_progress", "researched"):
                current_phase = info
            if not next_phase and status == "pending":
                next_phase = info
    except OSError as exc:
        logger.debug("Failed to read phases for autonomous context: %s", exc)

    # Check for paused work
    paused_at = None
    try:
        state = (Path(cwd) / ".planning" / "STATE.md").read_text(encoding="utf-8")
        pause_match = re.search(r"\*\*Paused At:\*\*\s*(.+)", state)
        if pause_match:
            paused_at = pause_match.group(1).strip()
    except OSError as exc:
        logger.debug("Failed to read STATE.md for paused-at check: %s", exc)

    return {
        # Models
        "executor_model": resolve_model(cwd, "amil-executor"),
        "planner_model": resolve_model(cwd, "amil-planner"),
        # Config
        "commit_docs": config.get("commit_docs"),
        # Milestone
        "milestone_version": milestone["version"],
        "milestone_name": milestone["name"],
        # Phase overview
        "phases": phases,
        "phase_count": len(phases),
        "completed_count": sum(1 for p in phases if p["status"] == "complete"),
        "in_progress_count": sum(1 for p in phases if p["status"] == "in_progress"),
        # Current state
        "current_phase": current_phase,
        "next_phase": next_phase,
        "paused_at": paused_at,
        "has_work_in_progress": current_phase is not None,
        # File existence
        "project_exists": path_exists(cwd, ".planning/PROJECT.md"),
        "roadmap_exists": path_exists(cwd, ".planning/ROADMAP.md"),
        "state_exists": path_exists(cwd, ".planning/STATE.md"),
        # File paths
        "state_path": ".planning/STATE.md",
        "roadmap_path": ".planning/ROADMAP.md",
        "project_path": ".planning/PROJECT.md",
        "config_path": ".planning/config.json",
    }
