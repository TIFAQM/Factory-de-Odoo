"""Milestone — Milestone and requirements lifecycle operations.

Ported from orchestrator/amil/bin/lib/milestone.cjs (242 lines, since deleted).
"""
from __future__ import annotations

import logging
import re
import shutil
from datetime import date
from pathlib import Path

logger = logging.getLogger(__name__)

from amil_utils.orchestrator.core import get_milestone_phase_filter
from amil_utils.orchestrator.frontmatter import extract_frontmatter
from amil_utils.orchestrator.state import write_state_md


def requirements_mark_complete(cwd: str | Path, req_ids: list[str]) -> dict:
    """Mark requirements as complete in REQUIREMENTS.md (checkboxes + table)."""
    if not req_ids:
        raise ValueError(
            "requirement IDs required. Usage: requirements_mark_complete(cwd, ['REQ-01', 'REQ-02'])"
        )

    # Normalize: accept comma-separated within a single string
    expanded: list[str] = []
    for raw in req_ids:
        parts = re.sub(r"[\[\]]", "", raw).split(",")
        expanded.extend(p.strip() for p in parts if p.strip())

    if not expanded:
        raise ValueError("no valid requirement IDs found")

    req_path = Path(cwd) / ".planning" / "REQUIREMENTS.md"
    if not req_path.exists():
        return {
            "updated": False,
            "reason": "REQUIREMENTS.md not found",
            "ids": expanded,
            "marked_complete": [],
            "not_found": expanded,
        }

    content = req_path.read_text(encoding="utf-8")
    updated: list[str] = []
    not_found: list[str] = []

    for req_id in expanded:
        found = False
        req_escaped = re.escape(req_id)

        # Checkbox: - [ ] **REQ-ID** → - [x] **REQ-ID**
        checkbox_re = re.compile(
            rf"(-\s*\[)[ ](\]\s*\*\*{req_escaped}\*\*)", re.IGNORECASE
        )
        if checkbox_re.search(content):
            content = checkbox_re.sub(r"\1x\2", content)
            found = True

        # Traceability table: | REQ-ID | Phase N | Pending | → Complete
        table_re = re.compile(
            rf"(\|\s*{req_escaped}\s*\|[^|]+\|)\s*Pending\s*(\|)", re.IGNORECASE
        )
        if table_re.search(content):
            content = table_re.sub(r"\1 Complete \2", content)
            found = True

        if found:
            updated.append(req_id)
        else:
            not_found.append(req_id)

    if updated:
        req_path.write_text(content, encoding="utf-8")

    return {
        "updated": len(updated) > 0,
        "marked_complete": updated,
        "not_found": not_found,
        "total": len(expanded),
    }


def milestone_complete(
    cwd: str | Path,
    version: str,
    *,
    name: str | None = None,
    archive_phases: bool = False,
) -> dict:
    """Archive a completed milestone: gather stats, archive files, update STATE."""
    if not version:
        raise ValueError("version required for milestone complete (e.g., v1.0)")

    cwd = Path(cwd)
    roadmap_path = cwd / ".planning" / "ROADMAP.md"
    req_path = cwd / ".planning" / "REQUIREMENTS.md"
    state_path = cwd / ".planning" / "STATE.md"
    milestones_path = cwd / ".planning" / "MILESTONES.md"
    archive_dir = cwd / ".planning" / "milestones"
    phases_dir = cwd / ".planning" / "phases"
    today = date.today().isoformat()
    milestone_name = name or version

    archive_dir.mkdir(parents=True, exist_ok=True)

    # Scope stats to current milestone only
    is_dir_in_milestone = get_milestone_phase_filter(cwd)

    phase_count, total_plans, total_tasks, accomplishments = _gather_milestone_stats(
        phases_dir, is_dir_in_milestone
    )

    # Archive ROADMAP.md
    if roadmap_path.exists():
        content = roadmap_path.read_text(encoding="utf-8")
        (archive_dir / f"{version}-ROADMAP.md").write_text(content, encoding="utf-8")

    # Archive REQUIREMENTS.md
    if req_path.exists():
        req_content = req_path.read_text(encoding="utf-8")
        header = (
            f"# Requirements Archive: {version} {milestone_name}\n\n"
            f"**Archived:** {today}\n"
            f"**Status:** SHIPPED\n\n"
            f"For current requirements, see `.planning/REQUIREMENTS.md`.\n\n---\n\n"
        )
        (archive_dir / f"{version}-REQUIREMENTS.md").write_text(
            header + req_content, encoding="utf-8"
        )

    # Archive audit file if exists
    audit_path = cwd / ".planning" / f"{version}-MILESTONE-AUDIT.md"
    if audit_path.exists():
        audit_path.rename(archive_dir / f"{version}-MILESTONE-AUDIT.md")

    # Create/append MILESTONES.md entry
    _write_milestones_entry(
        milestones_path, version, milestone_name, today,
        phase_count, total_plans, total_tasks, accomplishments,
    )

    # Update STATE.md
    if state_path.exists():
        state_content = state_path.read_text(encoding="utf-8")
        state_content = re.sub(
            r"(\*\*Status:\*\*\s*).*",
            rf"\g<1>{version} milestone complete",
            state_content,
        )
        state_content = re.sub(
            r"(\*\*Last Activity:\*\*\s*).*",
            rf"\g<1>{today}",
            state_content,
        )
        state_content = re.sub(
            r"(\*\*Last Activity Description:\*\*\s*).*",
            rf"\g<1>{version} milestone completed and archived",
            state_content,
        )
        write_state_md(state_path, state_content, cwd)

    # Archive phase directories if requested
    phases_archived = False
    if archive_phases:
        phases_archived = _archive_phase_dirs(
            phases_dir, archive_dir, version, is_dir_in_milestone
        )

    return {
        "version": version,
        "name": milestone_name,
        "date": today,
        "phases": phase_count,
        "plans": total_plans,
        "tasks": total_tasks,
        "accomplishments": accomplishments,
        "archived": {
            "roadmap": (archive_dir / f"{version}-ROADMAP.md").exists(),
            "requirements": (archive_dir / f"{version}-REQUIREMENTS.md").exists(),
            "audit": (archive_dir / f"{version}-MILESTONE-AUDIT.md").exists(),
            "phases": phases_archived,
        },
        "milestones_updated": True,
        "state_updated": state_path.exists(),
    }


def _gather_milestone_stats(
    phases_dir: Path,
    is_dir_in_milestone,
) -> tuple[int, int, int, list[str]]:
    """Gather stats from phases belonging to the current milestone."""
    phase_count = 0
    total_plans = 0
    total_tasks = 0
    accomplishments: list[str] = []

    try:
        entries = sorted(
            [e.name for e in phases_dir.iterdir() if e.is_dir()]
        )
        for dir_name in entries:
            if not is_dir_in_milestone(dir_name):
                continue

            phase_count += 1
            phase_files = [f.name for f in (phases_dir / dir_name).iterdir() if f.is_file()]
            plans = [f for f in phase_files if f.endswith("-PLAN.md") or f == "PLAN.md"]
            summaries = [f for f in phase_files if f.endswith("-SUMMARY.md") or f == "SUMMARY.md"]
            total_plans += len(plans)

            for s in summaries:
                try:
                    content = (phases_dir / dir_name / s).read_text(encoding="utf-8")
                    fm = extract_frontmatter(content)
                    if fm.get("one-liner"):
                        accomplishments.append(fm["one-liner"])
                    task_matches = re.findall(r"##\s*Task\s*\d+", content, re.IGNORECASE)
                    total_tasks += len(task_matches)
                except OSError:
                    continue
    except OSError as exc:
        logger.debug("Failed to read phases directory for milestone stats: %s", exc)

    return phase_count, total_plans, total_tasks, accomplishments


def _write_milestones_entry(
    milestones_path: Path,
    version: str,
    milestone_name: str,
    today: str,
    phase_count: int,
    total_plans: int,
    total_tasks: int,
    accomplishments: list[str],
) -> None:
    """Create or append a milestone entry to MILESTONES.md (reverse chronological)."""
    acc_list = "\n".join(f"- {a}" for a in accomplishments) if accomplishments else "- (none recorded)"
    entry = (
        f"## {version} {milestone_name} (Shipped: {today})\n\n"
        f"**Phases completed:** {phase_count} phases, {total_plans} plans, {total_tasks} tasks\n\n"
        f"**Key accomplishments:**\n{acc_list}\n\n---\n\n"
    )

    if milestones_path.exists():
        existing = milestones_path.read_text(encoding="utf-8")
        if not existing.strip():
            milestones_path.write_text(f"# Milestones\n\n{entry}", encoding="utf-8")
        else:
            # Insert after header for reverse chronological order (newest first)
            header_match = re.match(r"^(#{1,3}\s+[^\n]*\n\n?)", existing)
            if header_match:
                header = header_match.group(1)
                rest = existing[len(header):]
                milestones_path.write_text(header + entry + rest, encoding="utf-8")
            else:
                milestones_path.write_text(entry + existing, encoding="utf-8")
    else:
        milestones_path.write_text(f"# Milestones\n\n{entry}", encoding="utf-8")


def _archive_phase_dirs(
    phases_dir: Path,
    archive_dir: Path,
    version: str,
    is_dir_in_milestone,
) -> bool:
    """Move milestone phase directories to archive."""
    try:
        phase_archive = archive_dir / f"{version}-phases"
        phase_archive.mkdir(parents=True, exist_ok=True)

        entries = [e.name for e in phases_dir.iterdir() if e.is_dir()]
        archived_count = 0
        for d in entries:
            if not is_dir_in_milestone(d):
                continue
            shutil.move(str(phases_dir / d), str(phase_archive / d))
            archived_count += 1
        return archived_count > 0
    except OSError:
        return False
