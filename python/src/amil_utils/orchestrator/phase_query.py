"""Phase query operations — read-only phase listing, search, and plan indexing.

Extracted from phase.py to keep both files under 800 lines.
"""
from __future__ import annotations

import logging
import re
from functools import cmp_to_key
from pathlib import Path

logger = logging.getLogger(__name__)

from amil_utils.orchestrator.core import (
    compare_phase_num,
    find_phase,
    get_archived_phase_dirs,
    normalize_phase_name,
)
from amil_utils.orchestrator.frontmatter import extract_frontmatter


# ── Query operations ─────────────────────────────────────────────────────────


def phases_list(
    cwd: str | Path,
    *,
    file_type: str | None = None,
    phase: str | None = None,
    include_archived: bool = False,
) -> dict:
    """List phase directories, optionally filtered by type or phase number."""
    phases_dir = Path(cwd) / ".planning" / "phases"

    if not phases_dir.exists():
        if file_type:
            return {"files": [], "count": 0}
        return {"directories": [], "count": 0}

    entries = [e.name for e in phases_dir.iterdir() if e.is_dir()]

    if include_archived:
        for a in get_archived_phase_dirs(cwd):
            entries.append(f"{a['name']} [{a['milestone']}]")

    entries.sort(key=cmp_to_key(compare_phase_num))

    # Filter by phase number
    if phase:
        normalized = normalize_phase_name(phase)
        match = next((d for d in entries if d.startswith(normalized)), None)
        if not match:
            return {
                "files": [],
                "count": 0,
                "phase_dir": None,
                "error": "Phase not found",
            }
        entries = [match]

    # List files of a specific type
    if file_type:
        files: list[str] = []
        for dir_name in entries:
            dir_path = phases_dir / dir_name
            try:
                dir_files = [f.name for f in dir_path.iterdir() if f.is_file()]
            except OSError:
                continue

            if file_type == "plans":
                filtered = [f for f in dir_files if f.endswith("-PLAN.md") or f == "PLAN.md"]
            elif file_type == "summaries":
                filtered = [f for f in dir_files if f.endswith("-SUMMARY.md") or f == "SUMMARY.md"]
            else:
                filtered = dir_files

            files.extend(sorted(filtered))

        return {
            "files": files,
            "count": len(files),
            "phase_dir": re.sub(r"^\d+(?:\.\d+)*-?", "", entries[0]) if phase and entries else None,
        }

    return {"directories": entries, "count": len(entries)}


def phase_next_decimal(cwd: str | Path, base_phase: str) -> dict:
    """Calculate the next decimal phase number (e.g., 06.1, 06.2)."""
    phases_dir = Path(cwd) / ".planning" / "phases"
    normalized = normalize_phase_name(base_phase)

    if not phases_dir.exists():
        return {
            "found": False,
            "base_phase": normalized,
            "next": f"{normalized}.1",
            "existing": [],
        }

    entries = [e.name for e in phases_dir.iterdir() if e.is_dir()]

    base_exists = any(d.startswith(normalized + "-") or d == normalized for d in entries)

    decimal_pattern = re.compile(rf"^{re.escape(normalized)}\.(\d+)")
    existing_decimals: list[str] = []
    for d in entries:
        m = decimal_pattern.match(d)
        if m:
            existing_decimals.append(f"{normalized}.{m.group(1)}")

    existing_decimals.sort(key=cmp_to_key(compare_phase_num))

    if not existing_decimals:
        next_decimal = f"{normalized}.1"
    else:
        last_num = int(existing_decimals[-1].split(".")[1])
        next_decimal = f"{normalized}.{last_num + 1}"

    return {
        "found": base_exists,
        "base_phase": normalized,
        "next": next_decimal,
        "existing": existing_decimals,
    }


def phase_find(cwd: str | Path, phase: str) -> dict:
    """Find a phase directory by number."""
    not_found = {
        "found": False,
        "directory": None,
        "phase_number": None,
        "phase_name": None,
        "plans": [],
        "summaries": [],
    }

    if not phase:
        return not_found

    result = find_phase(cwd, phase)
    return result if result else not_found


def _extract_objective(content: str) -> str | None:
    """Extract the first line from an <objective> block."""
    m = re.search(r"<objective>\s*\n?\s*(.+)", content)
    return m.group(1).strip() if m else None


def phase_plan_index(cwd: str | Path, phase: str) -> dict:
    """Build a plan index for a phase: waves, tasks, checkpoints."""
    phases_dir = Path(cwd) / ".planning" / "phases"
    normalized = normalize_phase_name(phase)

    # Find phase directory
    phase_dir: Path | None = None
    try:
        entries = sorted(
            [e.name for e in phases_dir.iterdir() if e.is_dir()],
            key=cmp_to_key(compare_phase_num),
        )
        match = next((d for d in entries if d.startswith(normalized)), None)
        if match:
            phase_dir = phases_dir / match
    except OSError as exc:
        logger.debug("Failed to read phases directory for plan index: %s", exc)

    if phase_dir is None:
        return {
            "phase": normalized,
            "error": "Phase not found",
            "plans": [],
            "waves": {},
            "incomplete": [],
            "has_checkpoints": False,
        }

    phase_files = [f.name for f in phase_dir.iterdir() if f.is_file()]
    plan_files = sorted(f for f in phase_files if f.endswith("-PLAN.md") or f == "PLAN.md")
    summary_files = [f for f in phase_files if f.endswith("-SUMMARY.md") or f == "SUMMARY.md"]

    completed_ids = {s.replace("-SUMMARY.md", "").replace("SUMMARY.md", "") for s in summary_files}

    plans: list[dict] = []
    waves: dict[str, list[str]] = {}
    incomplete: list[str] = []
    has_checkpoints = False

    for plan_file in plan_files:
        plan_id = plan_file.replace("-PLAN.md", "").replace("PLAN.md", "")
        content = (phase_dir / plan_file).read_text(encoding="utf-8")
        fm = extract_frontmatter(content)

        # Count tasks: XML <task> tags (canonical) or ## Task N (legacy)
        xml_tasks = re.findall(r"<task[\s>]", content, re.IGNORECASE)
        md_tasks = re.findall(r"##\s*Task\s*\d+", content, re.IGNORECASE)
        task_count = len(xml_tasks) or len(md_tasks)

        wave = 1
        try:
            wave = int(fm.get("wave", 1))
        except (ValueError, TypeError) as exc:
            logger.debug("Failed to parse wave number from frontmatter: %s", exc)

        autonomous = True
        auto_val = fm.get("autonomous")
        if auto_val is not None:
            autonomous = auto_val in ("true", True, "True")

        if not autonomous:
            has_checkpoints = True

        files_modified: list = []
        fm_files = fm.get("files_modified") or fm.get("files-modified")
        if fm_files:
            files_modified = fm_files if isinstance(fm_files, list) else [fm_files]

        has_summary = plan_id in completed_ids
        if not has_summary:
            incomplete.append(plan_id)

        plans.append({
            "id": plan_id,
            "wave": wave,
            "autonomous": autonomous,
            "objective": _extract_objective(content) or fm.get("objective"),
            "files_modified": files_modified,
            "task_count": task_count,
            "has_summary": has_summary,
        })

        wave_key = str(wave)
        if wave_key not in waves:
            waves[wave_key] = []
        waves[wave_key].append(plan_id)

    return {
        "phase": normalized,
        "plans": plans,
        "waves": waves,
        "incomplete": incomplete,
        "has_checkpoints": has_checkpoints,
    }
