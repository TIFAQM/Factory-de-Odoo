"""Commands — Standalone utility commands.

Ported from orchestrator/amil/bin/lib/commands.cjs (548 lines, since deleted).
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

from amil_utils.orchestrator.core import (
    MODEL_PROFILES,
    compare_phase_num,
    find_phase,
    generate_slug as _generate_slug_internal,
    get_archived_phase_dirs,
    get_milestone_info,
    is_git_ignored,
    load_config,
    normalize_phase_name,
    resolve_model as _resolve_model_internal,
    scan_todos,
    to_posix_path,
    exec_git,
)
from amil_utils.orchestrator.frontmatter import extract_frontmatter


def generate_slug(text: str) -> dict:
    """Generate a URL-safe slug from text."""
    if not text:
        raise ValueError("text required for slug generation")

    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return {"slug": slug}


def current_timestamp(format: str | None = None) -> dict:
    """Return a formatted timestamp."""
    now = datetime.now(tz=datetime.now().astimezone().tzinfo)

    if format == "date":
        ts = now.strftime("%Y-%m-%d")
    elif format == "filename":
        ts = now.isoformat().replace(":", "-").split(".")[0]
    else:
        ts = now.isoformat()

    return {"timestamp": ts}


def list_todos(cwd: str | Path, area: str | None = None) -> dict:
    """List pending todos from .planning/todos/pending/."""
    todos = scan_todos(cwd, area)
    return {"count": len(todos), "todos": todos}


def verify_path_exists(cwd: str | Path, target_path: str) -> dict:
    """Check if a path exists, returning type information."""
    if not target_path:
        raise ValueError("path required for verification")

    p = Path(target_path)
    full = p if p.is_absolute() else Path(cwd) / p

    try:
        if full.is_dir():
            return {"exists": True, "type": "directory"}
        if full.is_file():
            return {"exists": True, "type": "file"}
        if full.exists():
            return {"exists": True, "type": "other"}
    except OSError as exc:
        logger.debug("Failed to check file existence for %s: %s", file_path, exc)
    return {"exists": False, "type": None}


def history_digest(cwd: str | Path) -> dict:
    """Aggregate a history digest from all phase summaries (archived + current)."""
    cwd = Path(cwd)
    phases_dir = cwd / ".planning" / "phases"

    phases: dict[str, dict] = {}
    decisions: list[dict] = []
    tech_stack: set[str] = set()

    all_phase_dirs: list[dict] = []

    # Archived phases first
    for a in get_archived_phase_dirs(cwd):
        all_phase_dirs.append(a)

    # Current phases
    if phases_dir.exists():
        try:
            for entry in sorted(
                e.name for e in phases_dir.iterdir() if e.is_dir()
            ):
                all_phase_dirs.append({
                    "name": entry,
                    "full_path": str(phases_dir / entry),
                    "milestone": None,
                })
        except OSError as exc:
            logger.debug("Failed to list current phase directories: %s", exc)

    if not all_phase_dirs:
        return {"phases": {}, "decisions": [], "tech_stack": []}

    for item in all_phase_dirs:
        dir_path = Path(item["full_path"])
        _collect_summaries_from_dir(
            dir_path, item["name"], phases, decisions, tech_stack
        )

    # Convert sets to lists for JSON serialization
    for p in phases.values():
        p["provides"] = list(p["provides"])
        p["affects"] = list(p["affects"])
        p["patterns"] = list(p["patterns"])

    return {
        "phases": phases,
        "decisions": decisions,
        "tech_stack": sorted(tech_stack),
    }


def _collect_summaries_from_dir(
    dir_path: Path,
    dir_name: str,
    phases: dict,
    decisions: list,
    tech_stack: set,
) -> None:
    """Collect data from summary files in a phase directory."""
    try:
        summaries = [
            f.name for f in dir_path.iterdir()
            if f.name.endswith("-SUMMARY.md") or f.name == "SUMMARY.md"
        ]
    except OSError:
        return

    for summary_name in summaries:
        try:
            content = (dir_path / summary_name).read_text(encoding="utf-8")
            fm = extract_frontmatter(content)
            phase_num = str(fm.get("phase") or dir_name.split("-")[0])

            if phase_num not in phases:
                name_parts = dir_name.split("-")[1:]
                phases[phase_num] = {
                    "name": fm.get("name") or " ".join(name_parts) or "Unknown",
                    "provides": set(),
                    "affects": set(),
                    "patterns": set(),
                }

            # Merge provides
            dep_graph = fm.get("dependency-graph") or {}
            provides = dep_graph.get("provides") if isinstance(dep_graph, dict) else None
            if not provides:
                provides = fm.get("provides")
            if isinstance(provides, list):
                phases[phase_num]["provides"].update(provides)

            # Merge affects
            affects = dep_graph.get("affects") if isinstance(dep_graph, dict) else None
            if isinstance(affects, list):
                phases[phase_num]["affects"].update(affects)

            # Merge patterns
            patterns = fm.get("patterns-established")
            if isinstance(patterns, list):
                phases[phase_num]["patterns"].update(patterns)

            # Merge decisions
            key_decisions = fm.get("key-decisions")
            if isinstance(key_decisions, list):
                for d in key_decisions:
                    decisions.append({"phase": phase_num, "decision": d})

            # Merge tech stack
            ts = fm.get("tech-stack")
            if isinstance(ts, dict) and isinstance(ts.get("added"), list):
                for t in ts["added"]:
                    tech_stack.add(t if isinstance(t, str) else t.get("name", str(t)))

        except OSError:
            continue


def resolve_model(cwd: str | Path, agent_type: str) -> dict:
    """Resolve the model for an agent type based on config profile."""
    if not agent_type:
        raise ValueError("agent-type required")

    config = load_config(cwd)
    profile = config.get("model_profile") or "balanced"
    model = _resolve_model_internal(cwd, agent_type)

    agent_models = MODEL_PROFILES.get(agent_type)
    result: dict = {"model": model, "profile": profile}
    if not agent_models:
        result["unknown_agent"] = True
    return result


def commit(
    cwd: str | Path,
    message: str,
    files: list[str] | None = None,
    *,
    amend: bool = False,
) -> dict:
    """Commit .planning docs to git."""
    if not message and not amend:
        raise ValueError("commit message required")

    config = load_config(cwd)

    # Check commit_docs config
    if not config.get("commit_docs"):
        return {"committed": False, "hash": None, "reason": "skipped_commit_docs_false"}

    # Check if .planning is gitignored
    if is_git_ignored(cwd, ".planning"):
        return {"committed": False, "hash": None, "reason": "skipped_gitignored"}

    # Stage files
    files_to_stage = files if files else [".planning/"]
    for f in files_to_stage:
        exec_git(cwd, ["add", f])

    # Commit
    commit_args = (
        ["commit", "--amend", "--no-edit"]
        if amend
        else ["commit", "-m", message]
    )
    commit_result = exec_git(cwd, commit_args)
    if commit_result["exit_code"] != 0:
        stdout = commit_result.get("stdout", "")
        stderr = commit_result.get("stderr", "")
        if "nothing to commit" in stdout or "nothing to commit" in stderr:
            return {"committed": False, "hash": None, "reason": "nothing_to_commit"}
        return {
            "committed": False,
            "hash": None,
            "reason": "commit_failed",
            "error": stderr,
        }

    # Get short hash
    hash_result = exec_git(cwd, ["rev-parse", "--short", "HEAD"])
    short_hash = hash_result["stdout"] if hash_result["exit_code"] == 0 else None
    return {"committed": True, "hash": short_hash, "reason": "committed"}


def summary_extract(
    cwd: str | Path,
    summary_path: str,
    fields: list[str] | None = None,
) -> dict:
    """Extract structured data from a summary file's frontmatter."""
    if not summary_path:
        raise ValueError("summary-path required for summary-extract")

    full_path = Path(cwd) / summary_path
    if not full_path.exists():
        return {"error": "File not found", "path": summary_path}

    content = full_path.read_text(encoding="utf-8")
    fm = extract_frontmatter(content)

    # Parse key-decisions into structured format
    raw_decisions = fm.get("key-decisions")
    parsed_decisions = _parse_decisions(raw_decisions)

    full_result: dict = {
        "path": summary_path,
        "one_liner": fm.get("one-liner"),
        "key_files": fm.get("key-files") or [],
        "tech_added": (fm.get("tech-stack") or {}).get("added", [])
            if isinstance(fm.get("tech-stack"), dict) else [],
        "patterns": fm.get("patterns-established") or [],
        "decisions": parsed_decisions,
        "requirements_completed": fm.get("requirements-completed") or [],
    }

    if fields:
        filtered: dict = {"path": summary_path}
        for field in fields:
            if field in full_result:
                filtered[field] = full_result[field]
        return filtered

    return full_result


def _parse_decisions(decisions_list: list | None) -> list[dict]:
    """Parse key-decisions list into structured summaries."""
    if not decisions_list or not isinstance(decisions_list, list):
        return []
    result = []
    for d in decisions_list:
        d_str = str(d)
        colon_idx = d_str.find(":")
        if colon_idx > 0:
            result.append({
                "summary": d_str[:colon_idx].strip(),
                "rationale": d_str[colon_idx + 1:].strip(),
            })
        else:
            result.append({"summary": d_str, "rationale": None})
    return result


def progress_render(cwd: str | Path, format: str | None = None) -> dict:
    """Render project progress as JSON, table, or bar."""
    cwd = Path(cwd)
    phases_dir = cwd / ".planning" / "phases"
    milestone = get_milestone_info(cwd)

    phases: list[dict] = []
    total_plans = 0
    total_summaries = 0

    try:
        entries = sorted(
            (e.name for e in phases_dir.iterdir() if e.is_dir()),
            key=lambda x: [compare_phase_num(x, x) or 0, x],
        )
        # Use proper comparison-based sort
        from functools import cmp_to_key
        entries = sorted(
            [e.name for e in phases_dir.iterdir() if e.is_dir()],
            key=cmp_to_key(compare_phase_num),
        )

        for dir_name in entries:
            dm = re.match(r"^(\d+(?:\.\d+)*)-?(.*)", dir_name)
            phase_num = dm.group(1) if dm else dir_name
            phase_name = dm.group(2).replace("-", " ") if dm and dm.group(2) else ""

            phase_files = [
                f.name for f in (phases_dir / dir_name).iterdir() if f.is_file()
            ]
            plans = sum(
                1 for f in phase_files
                if f.endswith("-PLAN.md") or f == "PLAN.md"
            )
            summaries = sum(
                1 for f in phase_files
                if f.endswith("-SUMMARY.md") or f == "SUMMARY.md"
            )

            total_plans += plans
            total_summaries += summaries

            if plans == 0:
                status = "Pending"
            elif summaries >= plans:
                status = "Complete"
            elif summaries > 0:
                status = "In Progress"
            else:
                status = "Planned"

            phases.append({
                "number": phase_num,
                "name": phase_name,
                "plans": plans,
                "summaries": summaries,
                "status": status,
            })
    except OSError as exc:
        logger.debug("Failed to read phases directory for progress: %s", exc)

    percent = (
        min(100, round((total_summaries / total_plans) * 100))
        if total_plans > 0
        else 0
    )

    if format == "table":
        return _render_table(milestone, phases, total_summaries, total_plans, percent)
    elif format == "bar":
        return _render_bar(total_summaries, total_plans, percent)
    else:
        return {
            "milestone_version": milestone["version"],
            "milestone_name": milestone["name"],
            "phases": phases,
            "total_plans": total_plans,
            "total_summaries": total_summaries,
            "percent": percent,
        }


def _render_table(
    milestone: dict,
    phases: list[dict],
    total_summaries: int,
    total_plans: int,
    percent: int,
) -> dict:
    """Render progress as a markdown table."""
    bar_width = 10
    filled = round((percent / 100) * bar_width)
    bar = "\u2588" * filled + "\u2591" * (bar_width - filled)
    out = f"# {milestone['version']} {milestone['name']}\n\n"
    out += f"**Progress:** [{bar}] {total_summaries}/{total_plans} plans ({percent}%)\n\n"
    out += "| Phase | Name | Plans | Status |\n"
    out += "|-------|------|-------|--------|\n"
    for p in phases:
        out += f"| {p['number']} | {p['name']} | {p['summaries']}/{p['plans']} | {p['status']} |\n"
    return {"rendered": out}


def _render_bar(
    total_summaries: int, total_plans: int, percent: int
) -> dict:
    """Render progress as a bar string."""
    bar_width = 20
    filled = round((percent / 100) * bar_width)
    bar = "\u2588" * filled + "\u2591" * (bar_width - filled)
    text = f"[{bar}] {total_summaries}/{total_plans} plans ({percent}%)"
    return {
        "bar": text,
        "percent": percent,
        "completed": total_summaries,
        "total": total_plans,
    }


def todo_complete(cwd: str | Path, filename: str) -> dict:
    """Move a todo from pending to completed."""
    if not filename:
        raise ValueError("filename required for todo complete")

    cwd = Path(cwd)
    pending_dir = cwd / ".planning" / "todos" / "pending"
    completed_dir = cwd / ".planning" / "todos" / "completed"
    source_path = pending_dir / filename

    if not source_path.exists():
        raise ValueError(f"Todo not found: {filename}")

    completed_dir.mkdir(parents=True, exist_ok=True)
    content = source_path.read_text(encoding="utf-8")
    today = date.today().isoformat()
    content = f"completed: {today}\n{content}"

    (completed_dir / filename).write_text(content, encoding="utf-8")
    source_path.unlink()

    return {"completed": True, "file": filename, "date": today}


def scaffold(
    cwd: str | Path,
    scaffold_type: str,
    *,
    phase: str | None = None,
    name: str | None = None,
) -> dict:
    """Scaffold a phase artifact (context, uat, verification, phase-dir)."""
    cwd = Path(cwd)
    padded = normalize_phase_name(phase) if phase else "00"
    today = date.today().isoformat()

    phase_info = find_phase(cwd, phase) if phase else None
    phase_dir = (
        cwd / phase_info["directory"] if phase_info and phase_info.get("directory") else None
    )

    if phase and not phase_dir and scaffold_type != "phase-dir":
        raise ValueError(f"Phase {phase} directory not found")

    if scaffold_type == "phase-dir":
        return _scaffold_phase_dir(cwd, padded, name, phase)

    display_name = name or (phase_info or {}).get("phase_name") or "Unnamed"
    templates = {
        "context": _context_template,
        "uat": _uat_template,
        "verification": _verification_template,
    }
    template_fn = templates.get(scaffold_type)
    if not template_fn:
        raise ValueError(
            f"Unknown scaffold type: {scaffold_type}. "
            "Available: context, uat, verification, phase-dir"
        )

    file_path = phase_dir / f"{padded}-{scaffold_type.upper()}.md"
    if file_path.exists():
        return {"created": False, "reason": "already_exists", "path": str(file_path)}

    content = template_fn(padded, display_name, phase or "0", today)
    file_path.write_text(content, encoding="utf-8")
    rel_path = to_posix_path(file_path.relative_to(cwd))
    return {"created": True, "path": rel_path}


def _scaffold_phase_dir(
    cwd: Path, padded: str, name: str | None, phase: str | None,
) -> dict:
    """Create a new phase directory."""
    if not phase or not name:
        raise ValueError("phase and name required for phase-dir scaffold")
    from amil_utils.orchestrator.core import generate_slug as gen_slug
    slug = gen_slug(name)
    dir_name = f"{padded}-{slug}"
    phases_parent = cwd / ".planning" / "phases"
    phases_parent.mkdir(parents=True, exist_ok=True)
    dir_path = phases_parent / dir_name
    dir_path.mkdir(parents=True, exist_ok=True)
    return {
        "created": True,
        "directory": f".planning/phases/{dir_name}",
        "path": str(dir_path),
    }


def _context_template(padded: str, name: str, phase: str, today: str) -> str:
    return (
        f"---\nphase: \"{padded}\"\nname: \"{name}\"\ncreated: {today}\n---\n\n"
        f"# Phase {phase}: {name} — Context\n\n"
        "## Decisions\n\n_Decisions will be captured during /amil:discuss-phase "
        f"{phase}_\n\n"
        "## Discretion Areas\n\n_Areas where the executor can use judgment_\n\n"
        "## Deferred Ideas\n\n_Ideas to consider later_\n"
    )


def _uat_template(padded: str, name: str, phase: str, today: str) -> str:
    return (
        f"---\nphase: \"{padded}\"\nname: \"{name}\"\ncreated: {today}\n"
        "status: pending\n---\n\n"
        f"# Phase {phase}: {name} — User Acceptance Testing\n\n"
        "## Test Results\n\n"
        "| # | Test | Status | Notes |\n|---|------|--------|-------|\n\n"
        "## Summary\n\n_Pending UAT_\n"
    )


def _verification_template(padded: str, name: str, phase: str, today: str) -> str:
    return (
        f"---\nphase: \"{padded}\"\nname: \"{name}\"\ncreated: {today}\n"
        "status: pending\n---\n\n"
        f"# Phase {phase}: {name} — Verification\n\n"
        "## Goal-Backward Verification\n\n**Phase Goal:** [From ROADMAP.md]\n\n"
        "## Checks\n\n"
        "| # | Requirement | Status | Evidence |\n|---|------------|--------|----------|\n\n"
        "## Result\n\n_Pending verification_\n"
    )
