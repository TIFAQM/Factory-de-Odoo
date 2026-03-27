"""State — STATE.md operations and progression engine.

Ported from orchestrator/amil/bin/lib/state.cjs (691 lines, since deleted).
Manages STATE.md: field extraction, replacement, plan advancement,
decisions, blockers, session tracking, metrics, frontmatter sync.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

from amil_utils.orchestrator.core import (
    get_milestone_info,
    get_milestone_phase_filter,
    load_config,
)
from amil_utils.orchestrator.frontmatter import (
    extract_frontmatter,
    reconstruct_frontmatter,
)


# ── Field extraction/replacement helpers ─────────────────────────────────────


def _state_field_patterns(field_name: str) -> tuple[re.Pattern, re.Pattern]:
    """Build bold and plain regex patterns for a STATE.md field."""
    escaped = re.escape(field_name)
    bold = re.compile(rf"(\*\*{escaped}:\*\*\s*)(.*)", re.IGNORECASE)
    plain = re.compile(rf"(^{escaped}:\s*)(.*)", re.IGNORECASE | re.MULTILINE)
    return bold, plain


def state_extract_field(content: str, field_name: str) -> str | None:
    """Extract a field value from STATE.md content (bold or plain format)."""
    bold, plain = _state_field_patterns(field_name)
    bold_match = bold.search(content)
    if bold_match:
        return bold_match.group(2).strip()
    plain_match = plain.search(content)
    return plain_match.group(2).strip() if plain_match else None


def state_replace_field(content: str, field_name: str, new_value: str) -> str | None:
    """Replace a field value in STATE.md content. Returns new content or None."""
    bold, plain = _state_field_patterns(field_name)
    if bold.search(content):
        return bold.sub(lambda m: f"{m.group(1)}{new_value}", content)
    if plain.search(content):
        return plain.sub(lambda m: f"{m.group(1)}{new_value}", content)
    return None


# ── Frontmatter sync ────────────────────────────────────────────────────────


def _strip_frontmatter(content: str) -> str:
    """Remove YAML frontmatter block from content."""
    return re.sub(r"^---\n[\s\S]*?\n---\n*", "", content)


def build_state_frontmatter(body_content: str, cwd: str | Path | None = None) -> dict:
    """Build machine-readable frontmatter from STATE.md body."""
    current_phase = state_extract_field(body_content, "Current Phase")
    current_phase_name = state_extract_field(body_content, "Current Phase Name")
    current_plan = state_extract_field(body_content, "Current Plan")
    total_phases_raw = state_extract_field(body_content, "Total Phases")
    total_plans_raw = state_extract_field(body_content, "Total Plans in Phase")
    status = state_extract_field(body_content, "Status")
    progress_raw = state_extract_field(body_content, "Progress")
    last_activity = state_extract_field(body_content, "Last Activity")
    stopped_at = (
        state_extract_field(body_content, "Stopped At")
        or state_extract_field(body_content, "Stopped at")
    )
    paused_at = state_extract_field(body_content, "Paused At")

    milestone = None
    milestone_name = None
    if cwd:
        try:
            info = get_milestone_info(cwd)
            milestone = info["version"]
            milestone_name = info["name"]
        except Exception as exc:
            logger.debug("Failed to read milestone info: %s", exc)

    total_phases = int(total_phases_raw) if total_phases_raw else None
    completed_phases = None
    total_plans = int(total_plans_raw) if total_plans_raw else None
    completed_plans = None

    if cwd:
        try:
            phases_dir = Path(cwd) / ".planning" / "phases"
            if phases_dir.exists():
                is_dir_in_milestone = get_milestone_phase_filter(cwd)
                phase_dirs = sorted(
                    d.name for d in phases_dir.iterdir()
                    if d.is_dir() and is_dir_in_milestone(d.name)
                )
                disk_total_plans = 0
                disk_total_summaries = 0
                disk_completed_phases = 0

                for d in phase_dirs:
                    files = [f.name for f in (phases_dir / d).iterdir() if f.is_file()]
                    plans = len([f for f in files if f.endswith("-PLAN.md")])
                    summaries = len([f for f in files if f.endswith("-SUMMARY.md")])
                    disk_total_plans += plans
                    disk_total_summaries += summaries
                    if plans > 0 and summaries >= plans:
                        disk_completed_phases += 1

                phase_count = getattr(is_dir_in_milestone, "phase_count", 0)
                total_phases = (
                    max(len(phase_dirs), phase_count)
                    if phase_count > 0
                    else len(phase_dirs)
                )
                completed_phases = disk_completed_phases
                total_plans = disk_total_plans
                completed_plans = disk_total_summaries
        except Exception as exc:
            logger.debug("Failed to read phase directories: %s", exc)

    progress_percent = None
    if progress_raw:
        pct_match = re.search(r"(\d+)%", progress_raw)
        if pct_match:
            progress_percent = int(pct_match.group(1))

    # Normalize status
    normalized_status = status or "unknown"
    status_lower = (status or "").lower()
    if "paused" in status_lower or "stopped" in status_lower or paused_at:
        normalized_status = "paused"
    elif "executing" in status_lower or "in progress" in status_lower:
        normalized_status = "executing"
    elif "planning" in status_lower or "ready to plan" in status_lower:
        normalized_status = "planning"
    elif "discussing" in status_lower:
        normalized_status = "discussing"
    elif "verif" in status_lower:
        normalized_status = "verifying"
    elif "complete" in status_lower or "done" in status_lower:
        normalized_status = "completed"
    elif "ready to execute" in status_lower:
        normalized_status = "executing"

    fm: dict = {"amil_state_version": "1.0"}

    if milestone:
        fm["milestone"] = milestone
    if milestone_name:
        fm["milestone_name"] = milestone_name
    if current_phase:
        fm["current_phase"] = current_phase
    if current_phase_name:
        fm["current_phase_name"] = current_phase_name
    if current_plan:
        fm["current_plan"] = current_plan
    fm["status"] = normalized_status
    if stopped_at:
        fm["stopped_at"] = stopped_at
    if paused_at:
        fm["paused_at"] = paused_at
    fm["last_updated"] = datetime.now(timezone.utc).isoformat()
    if last_activity:
        fm["last_activity"] = last_activity

    progress: dict = {}
    if total_phases is not None:
        progress["total_phases"] = total_phases
    if completed_phases is not None:
        progress["completed_phases"] = completed_phases
    if total_plans is not None:
        progress["total_plans"] = total_plans
    if completed_plans is not None:
        progress["completed_plans"] = completed_plans
    if progress_percent is not None:
        progress["percent"] = progress_percent
    if progress:
        fm["progress"] = progress

    return fm


def sync_state_frontmatter(content: str, cwd: str | Path | None = None) -> str:
    """Sync YAML frontmatter with STATE.md body content."""
    body = _strip_frontmatter(content)
    fm = build_state_frontmatter(body, cwd)
    yaml_str = reconstruct_frontmatter(fm)
    return f"---\n{yaml_str}\n---\n\n{body}"


def write_state_md(state_path: str | Path, content: str, cwd: str | Path) -> None:
    """Write STATE.md with synchronized YAML frontmatter."""
    synced = sync_state_frontmatter(content, cwd)
    Path(state_path).write_text(synced, encoding="utf-8")


# ── Public API ───────────────────────────────────────────────────────────────


def state_load(cwd: str | Path) -> dict:
    """Load config and state existence checks."""
    cwd = Path(cwd)
    config = load_config(cwd)
    planning = cwd / ".planning"

    state_raw = ""
    try:
        state_raw = (planning / "STATE.md").read_text(encoding="utf-8")
    except OSError as exc:
        logger.debug("Failed to read STATE.md: %s", exc)

    return {
        "config": config,
        "state_raw": state_raw,
        "state_exists": len(state_raw) > 0,
        "roadmap_exists": (planning / "ROADMAP.md").exists(),
        "config_exists": (planning / "config.json").exists(),
    }


def state_get(cwd: str | Path, section: str | None = None) -> dict:
    """Get full state content, a specific field, or a section."""
    state_path = Path(cwd) / ".planning" / "STATE.md"
    try:
        content = state_path.read_text(encoding="utf-8")
    except OSError:
        return {"error": "STATE.md not found"}

    if not section:
        return {"content": content}

    # Try field extraction
    field_value = state_extract_field(content, section)
    if field_value is not None:
        return {section: field_value}

    # Try ## Section
    section_escaped = re.escape(section)
    section_pattern = re.compile(
        rf"##\s*{section_escaped}\s*\n([\s\S]*?)(?=\n##|$)", re.IGNORECASE
    )
    section_match = section_pattern.search(content)
    if section_match:
        return {section: section_match.group(1).strip()}

    return {"error": f'Section or field "{section}" not found'}


def state_patch(cwd: str | Path, patches: dict[str, str]) -> dict:
    """Batch update multiple fields in STATE.md."""
    state_path = Path(cwd) / ".planning" / "STATE.md"
    try:
        content = state_path.read_text(encoding="utf-8")
    except OSError:
        return {"error": "STATE.md not found"}

    results: dict = {"updated": [], "failed": []}
    for field, value in patches.items():
        replaced = state_replace_field(content, field, value)
        if replaced is not None:
            content = replaced
            results["updated"].append(field)
        else:
            results["failed"].append(field)

    if results["updated"]:
        write_state_md(state_path, content, cwd)

    return results


def state_update(cwd: str | Path, field: str, value: str) -> dict:
    """Update a single field in STATE.md."""
    state_path = Path(cwd) / ".planning" / "STATE.md"
    try:
        content = state_path.read_text(encoding="utf-8")
    except OSError:
        return {"updated": False, "reason": "STATE.md not found"}

    replaced = state_replace_field(content, field, value)
    if replaced is not None:
        write_state_md(state_path, replaced, cwd)
        return {"updated": True}
    return {"updated": False, "reason": f'Field "{field}" not found in STATE.md'}


def state_advance_plan(cwd: str | Path) -> dict:
    """Advance plan counter or trigger verification if at last plan."""
    state_path = Path(cwd) / ".planning" / "STATE.md"
    if not state_path.exists():
        return {"error": "STATE.md not found"}

    content = state_path.read_text(encoding="utf-8")
    current_plan_raw = state_extract_field(content, "Current Plan")
    total_plans_raw = state_extract_field(content, "Total Plans in Phase")
    today = date.today().isoformat()

    try:
        current_plan = int(current_plan_raw)
        total_plans = int(total_plans_raw)
    except (TypeError, ValueError):
        return {"error": "Cannot parse Current Plan or Total Plans in Phase from STATE.md"}

    if current_plan >= total_plans:
        content = state_replace_field(content, "Status", "Phase complete — ready for verification") or content
        content = state_replace_field(content, "Last Activity", today) or content
        write_state_md(state_path, content, cwd)
        return {
            "advanced": False,
            "reason": "last_plan",
            "current_plan": current_plan,
            "total_plans": total_plans,
            "status": "ready_for_verification",
        }

    new_plan = current_plan + 1
    content = state_replace_field(content, "Current Plan", str(new_plan)) or content
    content = state_replace_field(content, "Status", "Ready to execute") or content
    content = state_replace_field(content, "Last Activity", today) or content
    write_state_md(state_path, content, cwd)
    return {
        "advanced": True,
        "previous_plan": current_plan,
        "current_plan": new_plan,
        "total_plans": total_plans,
    }


def state_record_metric(
    cwd: str | Path,
    *,
    phase: str,
    plan: str,
    duration: str,
    tasks: str | None = None,
    files: str | None = None,
) -> dict:
    """Add a row to the Performance Metrics table."""
    state_path = Path(cwd) / ".planning" / "STATE.md"
    if not state_path.exists():
        return {"error": "STATE.md not found"}

    if not phase or not plan or not duration:
        return {"error": "phase, plan, and duration required"}

    content = state_path.read_text(encoding="utf-8")
    metrics_pattern = re.compile(
        r"(##\s*Performance Metrics[\s\S]*?\n\|[^\n]+\n\|[-|\s]+\n)([\s\S]*?)(?=\n##|\n$|$)",
        re.IGNORECASE,
    )
    metrics_match = metrics_pattern.search(content)

    if metrics_match:
        table_body = metrics_match.group(2).rstrip()
        new_row = f"| Phase {phase} P{plan} | {duration} | {tasks or '-'} tasks | {files or '-'} files |"

        if not table_body.strip() or "None yet" in table_body:
            table_body = new_row
        else:
            table_body = table_body + "\n" + new_row

        content = metrics_pattern.sub(
            lambda m: f"{m.group(1)}{table_body}\n", content
        )
        write_state_md(state_path, content, cwd)
        return {"recorded": True, "phase": phase, "plan": plan, "duration": duration}

    return {"recorded": False, "reason": "Performance Metrics section not found in STATE.md"}


def state_update_progress(cwd: str | Path) -> dict:
    """Recalculate progress from disk and update the Progress field."""
    state_path = Path(cwd) / ".planning" / "STATE.md"
    if not state_path.exists():
        return {"error": "STATE.md not found"}

    content = state_path.read_text(encoding="utf-8")

    # Count plans and summaries across phases
    phases_dir = Path(cwd) / ".planning" / "phases"
    total_plans = 0
    total_summaries = 0

    if phases_dir.exists():
        for d in phases_dir.iterdir():
            if not d.is_dir():
                continue
            files = [f.name for f in d.iterdir() if f.is_file()]
            total_plans += len([f for f in files if re.search(r"-PLAN\.md$", f, re.IGNORECASE)])
            total_summaries += len([f for f in files if re.search(r"-SUMMARY\.md$", f, re.IGNORECASE)])

    percent = min(100, round(total_summaries / total_plans * 100)) if total_plans > 0 else 0
    bar_width = 10
    filled = round(percent / 100 * bar_width)
    bar = "\u2588" * filled + "\u2591" * (bar_width - filled)
    progress_str = f"[{bar}] {percent}%"

    bold_pattern = re.compile(r"(\*\*Progress:\*\*\s*)(.*)", re.IGNORECASE)
    plain_pattern = re.compile(r"^(Progress:\s*)(.*)", re.IGNORECASE | re.MULTILINE)

    if bold_pattern.search(content):
        content = bold_pattern.sub(lambda m: f"{m.group(1)}{progress_str}", content)
        write_state_md(state_path, content, cwd)
        return {"updated": True, "percent": percent, "completed": total_summaries, "total": total_plans, "bar": progress_str}

    if plain_pattern.search(content):
        content = plain_pattern.sub(lambda m: f"{m.group(1)}{progress_str}", content)
        write_state_md(state_path, content, cwd)
        return {"updated": True, "percent": percent, "completed": total_summaries, "total": total_plans, "bar": progress_str}

    return {"updated": False, "reason": "Progress field not found in STATE.md"}


def state_add_decision(
    cwd: str | Path,
    *,
    phase: str | None = None,
    summary: str,
    rationale: str | None = None,
) -> dict:
    """Add a decision entry to the Decisions section."""
    state_path = Path(cwd) / ".planning" / "STATE.md"
    if not state_path.exists():
        return {"error": "STATE.md not found"}

    if not summary:
        return {"error": "summary required"}

    content = state_path.read_text(encoding="utf-8")
    entry = f"- [Phase {phase or '?'}]: {summary}"
    if rationale:
        entry += f" — {rationale}"

    section_pattern = re.compile(
        r"(###?\s*(?:Decisions|Decisions Made|Accumulated.*Decisions)\s*\n)([\s\S]*?)(?=\n###?|\n##[^#]|$)",
        re.IGNORECASE,
    )
    match = section_pattern.search(content)

    if match:
        section_body = match.group(2)
        section_body = re.sub(r"None yet\.?\s*\n?", "", section_body, flags=re.IGNORECASE)
        section_body = re.sub(r"No decisions yet\.?\s*\n?", "", section_body, flags=re.IGNORECASE)
        section_body = section_body.rstrip() + "\n" + entry + "\n"
        content = section_pattern.sub(
            lambda m: f"{m.group(1)}{section_body}", content
        )
        write_state_md(state_path, content, cwd)
        return {"added": True, "decision": entry}

    return {"added": False, "reason": "Decisions section not found in STATE.md"}


def state_add_blocker(cwd: str | Path, text: str) -> dict:
    """Add a blocker entry to the Blockers section."""
    state_path = Path(cwd) / ".planning" / "STATE.md"
    if not state_path.exists():
        return {"error": "STATE.md not found"}

    if not text:
        return {"error": "text required"}

    content = state_path.read_text(encoding="utf-8")
    entry = f"- {text}"

    section_pattern = re.compile(
        r"(###?\s*(?:Blockers|Blockers/Concerns|Concerns)\s*\n)([\s\S]*?)(?=\n###?|\n##[^#]|$)",
        re.IGNORECASE,
    )
    match = section_pattern.search(content)

    if match:
        section_body = match.group(2)
        section_body = re.sub(r"None\.?\s*\n?", "", section_body, flags=re.IGNORECASE)
        section_body = re.sub(r"None yet\.?\s*\n?", "", section_body, flags=re.IGNORECASE)
        section_body = section_body.rstrip() + "\n" + entry + "\n"
        content = section_pattern.sub(
            lambda m: f"{m.group(1)}{section_body}", content
        )
        write_state_md(state_path, content, cwd)
        return {"added": True, "blocker": text}

    return {"added": False, "reason": "Blockers section not found in STATE.md"}


def state_resolve_blocker(cwd: str | Path, text: str) -> dict:
    """Remove a blocker that matches the given text (case-insensitive)."""
    state_path = Path(cwd) / ".planning" / "STATE.md"
    if not state_path.exists():
        return {"error": "STATE.md not found"}

    if not text:
        return {"error": "text required"}

    content = state_path.read_text(encoding="utf-8")
    section_pattern = re.compile(
        r"(###?\s*(?:Blockers|Blockers/Concerns|Concerns)\s*\n)([\s\S]*?)(?=\n###?|\n##[^#]|$)",
        re.IGNORECASE,
    )
    match = section_pattern.search(content)

    if match:
        section_body = match.group(2)
        lines = section_body.split("\n")
        filtered = [
            line for line in lines
            if not (line.startswith("- ") and text.lower() in line.lower())
        ]
        new_body = "\n".join(filtered)
        if not new_body.strip() or "- " not in new_body:
            new_body = "None\n"
        content = section_pattern.sub(
            lambda m: f"{m.group(1)}{new_body}", content
        )
        write_state_md(state_path, content, cwd)
        return {"resolved": True, "blocker": text}

    return {"resolved": False, "reason": "Blockers section not found in STATE.md"}


def state_record_session(
    cwd: str | Path,
    *,
    stopped_at: str | None = None,
    resume_file: str | None = None,
) -> dict:
    """Update session tracking fields in STATE.md."""
    state_path = Path(cwd) / ".planning" / "STATE.md"
    if not state_path.exists():
        return {"error": "STATE.md not found"}

    content = state_path.read_text(encoding="utf-8")
    now = datetime.now(timezone.utc).isoformat()
    updated: list[str] = []

    result = state_replace_field(content, "Last session", now)
    if result:
        content = result
        updated.append("Last session")
    result = state_replace_field(content, "Last Date", now)
    if result:
        content = result
        updated.append("Last Date")

    if stopped_at:
        result = state_replace_field(content, "Stopped At", stopped_at)
        if not result:
            result = state_replace_field(content, "Stopped at", stopped_at)
        if result:
            content = result
            updated.append("Stopped At")

    resume = resume_file or "None"
    result = state_replace_field(content, "Resume File", resume)
    if not result:
        result = state_replace_field(content, "Resume file", resume)
    if result:
        content = result
        updated.append("Resume File")

    if updated:
        write_state_md(state_path, content, cwd)
        return {"recorded": True, "updated": updated}

    return {"recorded": False, "reason": "No session fields found in STATE.md"}


def state_snapshot(cwd: str | Path) -> dict:
    """Extract all fields into a structured JSON snapshot."""
    state_path = Path(cwd) / ".planning" / "STATE.md"
    if not state_path.exists():
        return {"error": "STATE.md not found"}

    content = state_path.read_text(encoding="utf-8")

    current_phase = state_extract_field(content, "Current Phase")
    current_phase_name = state_extract_field(content, "Current Phase Name")
    total_phases_raw = state_extract_field(content, "Total Phases")
    current_plan = state_extract_field(content, "Current Plan")
    total_plans_raw = state_extract_field(content, "Total Plans in Phase")
    status = state_extract_field(content, "Status")
    progress_raw = state_extract_field(content, "Progress")
    last_activity = state_extract_field(content, "Last Activity")
    last_activity_desc = state_extract_field(content, "Last Activity Description")
    paused_at = state_extract_field(content, "Paused At")

    total_phases = int(total_phases_raw) if total_phases_raw else None
    total_plans_in_phase = int(total_plans_raw) if total_plans_raw else None
    progress_percent = None
    if progress_raw:
        pct_match = re.search(r"(\d+)%", progress_raw)
        if pct_match:
            progress_percent = int(pct_match.group(1))

    # Extract decisions from bullet list
    decisions: list[dict] = []
    decisions_table = re.search(
        r"##\s*Decisions Made[\s\S]*?\n\|[^\n]+\n\|[-|\s]+\n([\s\S]*?)(?=\n##|\n$|$)",
        content,
        re.IGNORECASE,
    )
    if decisions_table:
        table_body = decisions_table.group(1)
        rows = [r for r in table_body.strip().split("\n") if "|" in r]
        for row in rows:
            cells = [c.strip() for c in row.split("|") if c.strip()]
            if len(cells) >= 3:
                decisions.append({
                    "phase": cells[0],
                    "summary": cells[1],
                    "rationale": cells[2],
                })

    # Extract blockers
    blockers: list[str] = []
    blockers_match = re.search(r"##\s*Blockers\s*\n([\s\S]*?)(?=\n##|$)", content, re.IGNORECASE)
    if blockers_match:
        items = re.findall(r"^-\s+(.+)$", blockers_match.group(1), re.MULTILINE)
        blockers = [item.strip() for item in items]

    # Extract session info
    session: dict = {"last_date": None, "stopped_at": None, "resume_file": None}
    session_match = re.search(r"##\s*Session\s*\n([\s\S]*?)(?=\n##|$)", content, re.IGNORECASE)
    if session_match:
        section = session_match.group(1)
        last_date_m = re.search(r"\*\*Last Date:\*\*\s*(.+)", section, re.IGNORECASE)
        if not last_date_m:
            last_date_m = re.search(r"^Last Date:\s*(.+)", section, re.IGNORECASE | re.MULTILINE)
        stopped_at_m = re.search(r"\*\*Stopped At:\*\*\s*(.+)", section, re.IGNORECASE)
        if not stopped_at_m:
            stopped_at_m = re.search(r"^Stopped At:\s*(.+)", section, re.IGNORECASE | re.MULTILINE)
        resume_file_m = re.search(r"\*\*Resume File:\*\*\s*(.+)", section, re.IGNORECASE)
        if not resume_file_m:
            resume_file_m = re.search(r"^Resume File:\s*(.+)", section, re.IGNORECASE | re.MULTILINE)

        if last_date_m:
            session["last_date"] = last_date_m.group(1).strip()
        if stopped_at_m:
            session["stopped_at"] = stopped_at_m.group(1).strip()
        if resume_file_m:
            session["resume_file"] = resume_file_m.group(1).strip()

    return {
        "current_phase": current_phase,
        "current_phase_name": current_phase_name,
        "total_phases": total_phases,
        "current_plan": current_plan,
        "total_plans_in_phase": total_plans_in_phase,
        "status": status,
        "progress_percent": progress_percent,
        "last_activity": last_activity,
        "last_activity_desc": last_activity_desc,
        "decisions": decisions,
        "blockers": blockers,
        "paused_at": paused_at,
        "session": session,
    }


def state_json(cwd: str | Path) -> dict:
    """Extract frontmatter from STATE.md as JSON."""
    state_path = Path(cwd) / ".planning" / "STATE.md"
    if not state_path.exists():
        return {"error": "STATE.md not found"}

    content = state_path.read_text(encoding="utf-8")
    fm = extract_frontmatter(content)

    if not fm:
        body = _strip_frontmatter(content)
        return build_state_frontmatter(body, cwd)

    return fm
