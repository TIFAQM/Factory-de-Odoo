"""Roadmap — Roadmap parsing and update operations.

Ported from orchestrator/amil/bin/lib/roadmap.cjs (299 lines, since deleted).
Provides roadmap_get_phase, roadmap_analyze, and roadmap_update_plan_progress.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

from amil_utils.orchestrator.core import find_phase, normalize_phase_name


# ── Public API ───────────────────────────────────────────────────────────────


def roadmap_get_phase(cwd: str | Path, phase_num: str) -> dict:
    """Extract a single phase section from ROADMAP.md."""
    cwd = Path(cwd)
    roadmap_path = cwd / ".planning" / "ROADMAP.md"

    if not roadmap_path.exists():
        return {"found": False, "error": "ROADMAP.md not found"}

    content = roadmap_path.read_text(encoding="utf-8")
    escaped = re.escape(str(phase_num))

    # Match "## Phase X:", "### Phase X:", etc.
    phase_pattern = re.compile(
        rf"#{{2,4}}\s*Phase\s+{escaped}:\s*([^\n]+)", re.IGNORECASE
    )
    header_match = phase_pattern.search(content)

    if not header_match:
        return {"found": False, "phase_number": phase_num}

    phase_name = header_match.group(1).strip()
    header_index = header_match.start()

    # Find end of section
    rest = content[header_index:]
    next_header = re.search(r"\n#{2,4}\s+Phase\s+\d", rest, re.IGNORECASE)
    section_end = header_index + next_header.start() if next_header else len(content)
    section = content[header_index:section_end].strip()

    # Extract goal
    goal_match = re.search(r"\*\*Goal:\*\*\s*([^\n]+)", section, re.IGNORECASE)
    goal = goal_match.group(1).strip() if goal_match else None

    # Extract success criteria
    criteria_match = re.search(
        r"\*\*Success Criteria\*\*[^\n]*:\s*\n((?:\s*\d+\.\s*[^\n]+\n?)+)",
        section,
        re.IGNORECASE,
    )
    success_criteria = []
    if criteria_match:
        success_criteria = [
            re.sub(r"^\s*\d+\.\s*", "", line).strip()
            for line in criteria_match.group(1).strip().split("\n")
            if line.strip()
        ]

    return {
        "found": True,
        "phase_number": phase_num,
        "phase_name": phase_name,
        "goal": goal,
        "success_criteria": success_criteria,
        "section": section,
    }


def roadmap_analyze(cwd: str | Path) -> dict:
    """Analyze the full roadmap: extract phases, milestones, and progress."""
    cwd = Path(cwd)
    roadmap_path = cwd / ".planning" / "ROADMAP.md"

    if not roadmap_path.exists():
        return {
            "error": "ROADMAP.md not found",
            "milestones": [],
            "phases": [],
            "phase_count": 0,
            "current_phase": None,
        }

    content = roadmap_path.read_text(encoding="utf-8")
    phases_dir = cwd / ".planning" / "phases"

    # Extract all phase headings
    phase_pattern = re.compile(
        r"#{2,4}\s*Phase\s+(\d+[A-Z]?(?:\.\d+)*)\s*:\s*([^\n]+)", re.IGNORECASE
    )
    phases: list[dict] = []

    for match in phase_pattern.finditer(content):
        phase_num = match.group(1)
        phase_name = re.sub(r"\(INSERTED\)", "", match.group(2), flags=re.IGNORECASE).strip()

        # Extract section content
        section_start = match.start()
        rest = content[section_start:]
        next_header = re.search(r"\n#{2,4}\s+Phase\s+\d", rest, re.IGNORECASE)
        section_end = section_start + next_header.start() if next_header else len(content)
        section = content[section_start:section_end]

        goal_match = re.search(r"\*\*Goal:\*\*\s*([^\n]+)", section, re.IGNORECASE)
        goal = goal_match.group(1).strip() if goal_match else None

        depends_match = re.search(r"\*\*Depends on:\*\*\s*([^\n]+)", section, re.IGNORECASE)
        depends_on = depends_match.group(1).strip() if depends_match else None

        # Check disk status
        normalized = normalize_phase_name(phase_num)
        disk_status = "no_directory"
        plan_count = 0
        summary_count = 0
        has_context = False
        has_research = False

        try:
            entries = [e for e in phases_dir.iterdir() if e.is_dir()]
            dirs = [e.name for e in entries]
            dir_match = next(
                (d for d in dirs if d.startswith(normalized + "-") or d == normalized),
                None,
            )

            if dir_match:
                phase_files = [f.name for f in (phases_dir / dir_match).iterdir() if f.is_file()]
                plan_count = len([f for f in phase_files if f.endswith("-PLAN.md") or f == "PLAN.md"])
                summary_count = len([f for f in phase_files if f.endswith("-SUMMARY.md") or f == "SUMMARY.md"])
                has_context = any(f.endswith("-CONTEXT.md") or f == "CONTEXT.md" for f in phase_files)
                has_research = any(f.endswith("-RESEARCH.md") or f == "RESEARCH.md" for f in phase_files)

                if summary_count >= plan_count and plan_count > 0:
                    disk_status = "complete"
                elif summary_count > 0:
                    disk_status = "partial"
                elif plan_count > 0:
                    disk_status = "planned"
                elif has_research:
                    disk_status = "researched"
                elif has_context:
                    disk_status = "discussed"
                else:
                    disk_status = "empty"
        except OSError as exc:
            logger.debug("Failed to read phase directory for %s: %s", phase_num, exc)

        # Checkbox status
        checkbox_pat = re.compile(
            rf"-\s*\[(x| )\]\s*.*Phase\s+{re.escape(phase_num)}", re.IGNORECASE
        )
        checkbox_match = checkbox_pat.search(content)
        roadmap_complete = checkbox_match.group(1) == "x" if checkbox_match else False

        phases.append({
            "number": phase_num,
            "name": phase_name,
            "goal": goal,
            "depends_on": depends_on,
            "plan_count": plan_count,
            "summary_count": summary_count,
            "has_context": has_context,
            "has_research": has_research,
            "disk_status": disk_status,
            "roadmap_complete": roadmap_complete,
        })

    # Extract milestones
    milestones: list[dict] = []
    milestone_pattern = re.compile(r"##\s*(.*v(\d+\.\d+)[^(\n]*)", re.IGNORECASE)
    for m_match in milestone_pattern.finditer(content):
        milestones.append({
            "heading": m_match.group(1).strip(),
            "version": "v" + m_match.group(2),
        })

    # Progress stats
    total_plans = sum(p["plan_count"] for p in phases)
    total_summaries = sum(p["summary_count"] for p in phases)
    completed_phases = len([p for p in phases if p["disk_status"] == "complete"])

    current_phase = next(
        (p for p in phases if p["disk_status"] in ("planned", "partial")), None
    )
    next_phase = next(
        (p for p in phases if p["disk_status"] in ("empty", "no_directory", "discussed", "researched")),
        None,
    )

    # Detect checklist phases missing detailed sections
    checklist_pattern = re.compile(r"-\s*\[[ x]\]\s*(?:.*Phase\s+)?(\d+[A-Z]?(?:\.\d+)*)", re.IGNORECASE)
    checklist_phases = {m.group(1) for m in checklist_pattern.finditer(content)}
    detail_phases = {p["number"] for p in phases}
    missing_details = sorted(checklist_phases - detail_phases)

    return {
        "milestones": milestones,
        "phases": phases,
        "phase_count": len(phases),
        "completed_phases": completed_phases,
        "total_plans": total_plans,
        "total_summaries": total_summaries,
        "progress_percent": min(100, round((total_summaries / total_plans) * 100)) if total_plans > 0 else 0,
        "current_phase": current_phase["number"] if current_phase else None,
        "next_phase": next_phase["number"] if next_phase else None,
        "missing_phase_details": missing_details if missing_details else None,
    }


def roadmap_update_plan_progress(cwd: str | Path, phase_num: str) -> dict:
    """Update ROADMAP.md progress for a phase based on plan/summary counts."""
    cwd = Path(cwd)
    roadmap_path = cwd / ".planning" / "ROADMAP.md"

    phase_info = find_phase(cwd, phase_num)
    if not phase_info:
        return {"updated": False, "reason": f"Phase {phase_num} not found"}

    plan_count = len(phase_info["plans"])
    summary_count = len(phase_info["summaries"])

    if plan_count == 0:
        return {"updated": False, "reason": "No plans found", "plan_count": 0, "summary_count": 0}

    is_complete = summary_count >= plan_count

    if not roadmap_path.exists():
        return {
            "updated": False,
            "reason": "ROADMAP.md not found",
            "plan_count": plan_count,
            "summary_count": summary_count,
        }

    roadmap_content = roadmap_path.read_text(encoding="utf-8")
    escaped = re.escape(str(phase_num))

    # Update progress table row
    status = "Complete" if is_complete else ("In Progress" if summary_count > 0 else "Planned")
    from datetime import date
    today = date.today().isoformat()

    table_pattern = re.compile(
        rf"(\|\s*{escaped}\.?\s[^|]*\|)[^|]*(\|)\s*[^|]*(\|)\s*[^|]*(\|)", re.IGNORECASE
    )
    date_field = f" {today} " if is_complete else "  "
    roadmap_content = table_pattern.sub(
        rf"\g<1> {summary_count}/{plan_count} \g<2> {status:<11}\g<3>{date_field}\g<4>",
        roadmap_content,
    )

    # Update plan count in section
    plan_count_pattern = re.compile(
        rf"(#{{2,4}}\s*Phase\s+{escaped}[\s\S]*?\*\*Plans:\*\*\s*)[^\n]+", re.IGNORECASE
    )
    plan_count_text = (
        f"{summary_count}/{plan_count} plans complete"
        if is_complete
        else f"{summary_count}/{plan_count} plans executed"
    )
    roadmap_content = plan_count_pattern.sub(rf"\g<1>{plan_count_text}", roadmap_content)

    # If complete, check the checkbox
    if is_complete:
        checkbox_pattern = re.compile(
            rf"(-\s*\[)[ ](\]\s*.*Phase\s+{escaped}[:\s][^\n]*)", re.IGNORECASE
        )
        roadmap_content = checkbox_pattern.sub(
            rf"\g<1>x\g<2> (completed {today})", roadmap_content
        )

    roadmap_path.write_text(roadmap_content, encoding="utf-8")

    return {
        "updated": True,
        "phase": phase_num,
        "plan_count": plan_count,
        "summary_count": summary_count,
        "status": status,
        "complete": is_complete,
    }
