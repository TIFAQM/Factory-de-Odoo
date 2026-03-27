"""Core — Shared utilities, constants, and internal helpers.

Ported from orchestrator/amil/bin/lib/core.cjs (581 lines, since deleted).
Excludes parseArgs (Click handles CLI args) and output (in cli_helpers.py).
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from pathlib import Path, PurePosixPath

logger = logging.getLogger(__name__)


# ── Path helpers ─────────────────────────────────────────────────────────────


def to_posix_path(p: str | Path) -> str:
    """Normalize a path to always use forward slashes."""
    return str(PurePosixPath(Path(p)))


# ── Model Profile Table ─────────────────────────────────────────────────────

MODEL_PROFILES: dict[str, dict[str, str]] = {
    "amil-planner":              {"quality": "opus", "balanced": "opus",   "budget": "sonnet"},
    "amil-roadmapper":           {"quality": "opus", "balanced": "sonnet", "budget": "sonnet"},
    "amil-executor":             {"quality": "opus", "balanced": "sonnet", "budget": "sonnet"},
    "amil-phase-researcher":     {"quality": "opus", "balanced": "sonnet", "budget": "haiku"},
    "amil-project-researcher":   {"quality": "opus", "balanced": "sonnet", "budget": "haiku"},
    "amil-research-synthesizer": {"quality": "sonnet", "balanced": "sonnet", "budget": "haiku"},
    "amil-debugger":             {"quality": "opus", "balanced": "sonnet", "budget": "sonnet"},
    "amil-codebase-mapper":      {"quality": "sonnet", "balanced": "haiku", "budget": "haiku"},
    "amil-verifier":             {"quality": "sonnet", "balanced": "sonnet", "budget": "haiku"},
    "amil-plan-checker":         {"quality": "sonnet", "balanced": "sonnet", "budget": "haiku"},
    "amil-integration-checker":  {"quality": "sonnet", "balanced": "sonnet", "budget": "haiku"},
    "amil-nyquist-auditor":      {"quality": "sonnet", "balanced": "sonnet", "budget": "haiku"},
}


# ── File & Config utilities ──────────────────────────────────────────────────

_CONFIG_DEFAULTS: dict[str, object] = {
    "model_profile": "balanced",
    "commit_docs": True,
    "search_gitignored": False,
    "branching_strategy": "none",
    "phase_branch_template": "amil/phase-{phase}-{slug}",
    "milestone_branch_template": "amil/{milestone}-{slug}",
    "research": True,
    "plan_checker": True,
    "verifier": True,
    "nyquist_validation": True,
    "parallelization": True,
    "brave_search": False,
}


def safe_read_file(file_path: str | Path) -> str | None:
    """Read file content, returning None if the file doesn't exist."""
    try:
        return Path(file_path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _config_get(parsed: dict, key: str, nested: dict | None = None) -> object:
    """Look up a config key, optionally checking a nested section."""
    val = parsed.get(key)
    if val is not None:
        return val
    if nested and nested["section"] in parsed:
        section = parsed[nested["section"]]
        if isinstance(section, dict) and nested["field"] in section:
            return section[nested["field"]]
    return None


def load_config(cwd: str | Path) -> dict:
    """Load .planning/config.json with defaults and migration."""
    config_path = Path(cwd) / ".planning" / "config.json"
    defaults = dict(_CONFIG_DEFAULTS)

    try:
        raw = config_path.read_text(encoding="utf-8")
        parsed = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return defaults

    # Migrate deprecated "depth" key to "granularity"
    if "depth" in parsed and "granularity" not in parsed:
        depth_map = {"quick": "coarse", "standard": "standard", "comprehensive": "fine"}
        parsed["granularity"] = depth_map.get(parsed["depth"], parsed["depth"])
        del parsed["depth"]
        try:
            config_path.write_text(json.dumps(parsed, indent=2), encoding="utf-8")
        except OSError:
            pass  # best-effort migration

    g = lambda key, nested=None: _config_get(parsed, key, nested)

    parallelization_raw = g("parallelization")
    if isinstance(parallelization_raw, bool):
        parallelization = parallelization_raw
    elif isinstance(parallelization_raw, dict) and "enabled" in parallelization_raw:
        parallelization = parallelization_raw["enabled"]
    else:
        parallelization = defaults["parallelization"]

    return {
        "model_profile": g("model_profile") or defaults["model_profile"],
        "commit_docs": _coalesce(g("commit_docs", {"section": "planning", "field": "commit_docs"}), defaults["commit_docs"]),
        "search_gitignored": _coalesce(g("search_gitignored", {"section": "planning", "field": "search_gitignored"}), defaults["search_gitignored"]),
        "branching_strategy": _coalesce(g("branching_strategy", {"section": "git", "field": "branching_strategy"}), defaults["branching_strategy"]),
        "phase_branch_template": _coalesce(g("phase_branch_template", {"section": "git", "field": "phase_branch_template"}), defaults["phase_branch_template"]),
        "milestone_branch_template": _coalesce(g("milestone_branch_template", {"section": "git", "field": "milestone_branch_template"}), defaults["milestone_branch_template"]),
        "research": _coalesce(g("research", {"section": "workflow", "field": "research"}), defaults["research"]),
        "plan_checker": _coalesce(g("plan_checker", {"section": "workflow", "field": "plan_check"}), defaults["plan_checker"]),
        "verifier": _coalesce(g("verifier", {"section": "workflow", "field": "verifier"}), defaults["verifier"]),
        "nyquist_validation": _coalesce(g("nyquist_validation", {"section": "workflow", "field": "nyquist_validation"}), defaults["nyquist_validation"]),
        "parallelization": parallelization,
        "brave_search": _coalesce(g("brave_search"), defaults["brave_search"]),
        "model_overrides": parsed.get("model_overrides") or None,
        # Carry forward extra keys
        "profile": parsed.get("profile", defaults["model_profile"]),
        "granularity": parsed.get("granularity"),
    }


def _coalesce(value: object, default: object) -> object:
    """Return value if not None, else default."""
    return value if value is not None else default


# ── Git utilities ─────────────────────────────────────────────────────────────


def exec_git(cwd: str | Path, args: list[str]) -> dict[str, object]:
    """Run a git command, returning {exit_code, stdout, stderr}."""
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=30,
        )
        return {
            "exit_code": result.returncode,
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
        }
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        return {"exit_code": 1, "stdout": "", "stderr": str(exc)}


def is_git_ignored(cwd: str | Path, target_path: str) -> bool:
    """Check if a path is git-ignored."""
    result = exec_git(cwd, ["check-ignore", "-q", "--no-index", "--", target_path])
    return result["exit_code"] == 0


# ── Phase utilities ──────────────────────────────────────────────────────────

_PHASE_RE = re.compile(r"^(\d+)([A-Z])?((?:\.\d+)*)", re.IGNORECASE)


def normalize_phase_name(phase: str) -> str:
    """Normalize phase name: pad major number, uppercase letter."""
    m = _PHASE_RE.match(str(phase))
    if not m:
        return str(phase)
    padded = m.group(1).zfill(2)
    letter = m.group(2).upper() if m.group(2) else ""
    decimal = m.group(3) or ""
    return padded + letter + decimal


def compare_phase_num(a: str, b: str) -> int:
    """Compare two phase numbers numerically. Returns <0, 0, or >0."""
    pa = _PHASE_RE.match(str(a))
    pb = _PHASE_RE.match(str(b))
    if not pa or not pb:
        return (str(a) > str(b)) - (str(a) < str(b))

    int_diff = int(pa.group(1)) - int(pb.group(1))
    if int_diff != 0:
        return int_diff

    # No letter sorts before letter: 12 < 12A < 12B
    la = (pa.group(2) or "").upper()
    lb = (pb.group(2) or "").upper()
    if la != lb:
        if not la:
            return -1
        if not lb:
            return 1
        return (la > lb) - (la < lb)

    # Segment-by-segment decimal comparison
    a_dec = [int(x) for x in pa.group(3).lstrip(".").split(".")] if pa.group(3) else []
    b_dec = [int(x) for x in pb.group(3).lstrip(".").split(".")] if pb.group(3) else []

    if not a_dec and b_dec:
        return -1
    if a_dec and not b_dec:
        return 1

    max_len = max(len(a_dec), len(b_dec))
    for i in range(max_len):
        av = a_dec[i] if i < len(a_dec) else 0
        bv = b_dec[i] if i < len(b_dec) else 0
        if av != bv:
            return av - bv
    return 0


def search_phase_in_dir(
    base_dir: str | Path,
    rel_base: str,
    normalized: str,
) -> dict | None:
    """Search for a phase directory matching the normalized prefix."""
    base_dir = Path(base_dir)
    try:
        entries = sorted(
            [e.name for e in base_dir.iterdir() if e.is_dir()],
            key=lambda d: (compare_phase_num(d.split("-")[0] if "-" in d else d, "0"), d),
        )
    except OSError:
        return None

    match = next((d for d in entries if d.startswith(normalized)), None)
    if not match:
        return None

    dir_match = re.match(r"^(\d+[A-Z]?(?:\.\d+)*)-?(.*)", match, re.IGNORECASE)
    phase_number = dir_match.group(1) if dir_match else normalized
    phase_name = dir_match.group(2) if dir_match and dir_match.group(2) else None

    phase_dir = base_dir / match
    try:
        phase_files = [f.name for f in phase_dir.iterdir() if f.is_file()]
    except OSError:
        phase_files = []

    plans = sorted(f for f in phase_files if f.endswith("-PLAN.md") or f == "PLAN.md")
    summaries = sorted(f for f in phase_files if f.endswith("-SUMMARY.md") or f == "SUMMARY.md")
    has_research = any(f.endswith("-RESEARCH.md") or f == "RESEARCH.md" for f in phase_files)
    has_context = any(f.endswith("-CONTEXT.md") or f == "CONTEXT.md" for f in phase_files)
    has_verification = any(f.endswith("-VERIFICATION.md") or f == "VERIFICATION.md" for f in phase_files)

    completed_plan_ids = {
        s.replace("-SUMMARY.md", "").replace("SUMMARY.md", "") for s in summaries
    }
    incomplete_plans = [
        p for p in plans
        if p.replace("-PLAN.md", "").replace("PLAN.md", "") not in completed_plan_ids
    ]

    phase_slug = None
    if phase_name:
        phase_slug = re.sub(r"[^a-z0-9]+", "-", phase_name.lower()).strip("-") or None

    return {
        "found": True,
        "directory": to_posix_path(f"{rel_base}/{match}"),
        "phase_number": phase_number,
        "phase_name": phase_name,
        "phase_slug": phase_slug,
        "plans": plans,
        "summaries": summaries,
        "incomplete_plans": incomplete_plans,
        "has_research": has_research,
        "has_context": has_context,
        "has_verification": has_verification,
    }


def find_phase(cwd: str | Path, phase: str | None) -> dict | None:
    """Find a phase directory by number, searching current and archived milestones."""
    if not phase:
        return None

    cwd = Path(cwd)
    phases_dir = cwd / ".planning" / "phases"
    normalized = normalize_phase_name(phase)

    # Search current phases first
    current = search_phase_in_dir(phases_dir, ".planning/phases", normalized)
    if current:
        return current

    # Search archived milestone phases (newest first)
    milestones_dir = cwd / ".planning" / "milestones"
    if not milestones_dir.exists():
        return None

    try:
        archive_dirs = sorted(
            [
                e.name
                for e in milestones_dir.iterdir()
                if e.is_dir() and re.match(r"^v[\d.]+-phases$", e.name)
            ],
            reverse=True,
        )
        for archive_name in archive_dirs:
            version_match = re.match(r"^(v[\d.]+)-phases$", archive_name)
            version = version_match.group(1) if version_match else archive_name
            archive_path = milestones_dir / archive_name
            rel_base = f".planning/milestones/{archive_name}"
            result = search_phase_in_dir(archive_path, rel_base, normalized)
            if result:
                result["archived"] = version
                return result
    except OSError as exc:
        logger.debug("Failed to search archived milestones: %s", exc)

    return None


def get_archived_phase_dirs(cwd: str | Path) -> list[dict]:
    """List all phase directories from archived milestones."""
    cwd = Path(cwd)
    milestones_dir = cwd / ".planning" / "milestones"
    results: list[dict] = []

    if not milestones_dir.exists():
        return results

    try:
        phase_dirs = sorted(
            [
                e.name
                for e in milestones_dir.iterdir()
                if e.is_dir() and re.match(r"^v[\d.]+-phases$", e.name)
            ],
            reverse=True,
        )
        for archive_name in phase_dirs:
            version_match = re.match(r"^(v[\d.]+)-phases$", archive_name)
            version = version_match.group(1) if version_match else archive_name
            archive_path = milestones_dir / archive_name
            try:
                dirs = sorted(
                    [e.name for e in archive_path.iterdir() if e.is_dir()],
                    key=lambda d: (compare_phase_num(d.split("-")[0] if "-" in d else d, "0"), d),
                )
                for d in dirs:
                    results.append({
                        "name": d,
                        "milestone": version,
                        "base_path": str(Path(".planning") / "milestones" / archive_name),
                        "full_path": str(archive_path / d),
                    })
            except OSError:
                continue
    except OSError as exc:
        logger.debug("Failed to list archived phase directories: %s", exc)

    return results


# ── Roadmap & model utilities ────────────────────────────────────────────────

_GOAL_PATTERN = re.compile(r"\*\*Goal:?\*\*:?\s*([^\n]+)", re.IGNORECASE)


def get_roadmap_phase(cwd: str | Path, phase_num: str | None) -> dict | None:
    """Extract phase info from ROADMAP.md."""
    if not phase_num:
        return None
    roadmap_path = Path(cwd) / ".planning" / "ROADMAP.md"
    if not roadmap_path.exists():
        return None

    try:
        content = roadmap_path.read_text(encoding="utf-8")
        escaped = re.escape(str(phase_num))
        phase_pattern = re.compile(
            rf"#{{2,4}}\s*Phase\s+{escaped}:\s*([^\n]+)", re.IGNORECASE
        )
        header_match = phase_pattern.search(content)
        if not header_match:
            return None

        phase_name = header_match.group(1).strip()
        header_index = header_match.start()
        rest = content[header_index:]
        next_header = re.search(r"\n#{2,4}\s+Phase\s+\d", rest, re.IGNORECASE)
        section_end = header_index + next_header.start() if next_header else len(content)
        section = content[header_index:section_end].strip()

        goal_match = _GOAL_PATTERN.search(section)
        goal = goal_match.group(1).strip() if goal_match else None

        return {
            "found": True,
            "phase_number": str(phase_num),
            "phase_name": phase_name,
            "goal": goal,
            "section": section,
        }
    except OSError:
        return None


def resolve_model(cwd: str | Path, agent_type: str) -> str:
    """Resolve the model for an agent based on config profile and overrides."""
    config = load_config(cwd)

    # Check per-agent override first
    overrides = config.get("model_overrides") or {}
    override = overrides.get(agent_type)
    if override:
        return "inherit" if override == "opus" else override

    # Fall back to profile lookup
    profile = config.get("model_profile") or config.get("profile") or "balanced"
    agent_models = MODEL_PROFILES.get(agent_type)
    if not agent_models:
        return "sonnet"
    resolved = agent_models.get(profile) or agent_models.get("balanced") or "sonnet"
    return "inherit" if resolved == "opus" else resolved


# ── Misc utilities ───────────────────────────────────────────────────────────


def path_exists(cwd: str | Path, target_path: str) -> bool:
    """Check if a path exists (absolute or relative to cwd)."""
    p = Path(target_path)
    full = p if p.is_absolute() else Path(cwd) / p
    return full.exists()


def generate_slug(text: str | None) -> str | None:
    """Generate a URL-safe slug from text."""
    if not text:
        return None
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or None


def get_milestone_info(cwd: str | Path) -> dict[str, str]:
    """Parse current milestone version and name from ROADMAP.md."""
    try:
        roadmap = (Path(cwd) / ".planning" / "ROADMAP.md").read_text(encoding="utf-8")

        # First: check for list-format with 🚧 (in-progress) marker
        in_progress = re.search(r"\U0001f6a7\s*\*\*v(\d+\.\d+)\s+([^*]+)\*\*", roadmap)
        if in_progress:
            return {"version": f"v{in_progress.group(1)}", "name": in_progress.group(2).strip()}

        # Second: heading-format — strip shipped milestones in <details> blocks
        cleaned = re.sub(r"<details>[\s\S]*?</details>", "", roadmap, flags=re.IGNORECASE)
        heading = re.search(r"## .*v(\d+\.\d+)[:\s]+([^\n(]+)", cleaned)
        if heading:
            return {"version": f"v{heading.group(1)}", "name": heading.group(2).strip()}

        # Fallback: bare version
        version_match = re.search(r"v(\d+\.\d+)", cleaned)
        return {
            "version": version_match.group(0) if version_match else "v1.0",
            "name": "milestone",
        }
    except OSError:
        return {"version": "v1.0", "name": "milestone"}


def get_milestone_phase_filter(cwd: str | Path):
    """Return a filter function for phase directories belonging to current milestone."""
    milestone_phases: set[str] = set()
    try:
        roadmap = (Path(cwd) / ".planning" / "ROADMAP.md").read_text(encoding="utf-8")
        for m in re.finditer(r"#{2,4}\s*Phase\s+(\d+[A-Z]?(?:\.\d+)*)\s*:", roadmap, re.IGNORECASE):
            milestone_phases.add(m.group(1))
    except OSError as exc:
        logger.debug("Failed to read ROADMAP.md for milestone phase filter: %s", exc)

    if not milestone_phases:
        def pass_all(dir_name: str) -> bool:
            return True
        pass_all.phase_count = 0
        return pass_all

    normalized = {(n.lstrip("0") or "0").lower() for n in milestone_phases}

    def is_in_milestone(dir_name: str) -> bool:
        m = re.match(r"^0*(\d+[A-Za-z]?(?:\.\d+)*)", dir_name)
        if not m:
            return False
        return m.group(1).lower() in normalized

    is_in_milestone.phase_count = len(milestone_phases)
    return is_in_milestone


def ensure_within_cwd(cwd: str | Path, file_path: str | Path) -> Path:
    """Ensure a file path resolves within the given cwd. Raises ValueError if not."""
    cwd = Path(cwd)
    fp = Path(file_path)
    resolved = fp if fp.is_absolute() else cwd / fp
    normalized_cwd = cwd.resolve()
    normalized_target = resolved.resolve()
    if normalized_target != normalized_cwd and not str(normalized_target).startswith(
        str(normalized_cwd) + os.sep
    ):
        raise ValueError(f'Path "{file_path}" is outside the project directory')
    return normalized_target


def has_source_files(directory: str | Path, _depth: int = 0) -> bool:
    """Check if a directory contains source code files (up to 3 levels deep)."""
    if _depth > 3:
        return False
    extensions = {".ts", ".js", ".py", ".go", ".rs", ".swift", ".java"}
    ignore = {"node_modules", ".git", ".venv", "__pycache__"}
    try:
        for entry in Path(directory).iterdir():
            if entry.name in ignore:
                continue
            if entry.is_file() and entry.suffix in extensions:
                return True
            if entry.is_dir() and has_source_files(entry, _depth + 1):
                return True
    except OSError as exc:
        logger.debug("Failed to scan directory %s for source files: %s", directory, exc)
    return False


def scan_todos(cwd: str | Path, area_filter: str | None = None) -> list[dict]:
    """Scan pending todo files from .planning/todos/pending/."""
    pending_dir = Path(cwd) / ".planning" / "todos" / "pending"
    todos: list[dict] = []
    try:
        files = sorted(f.name for f in pending_dir.iterdir() if f.name.endswith(".md"))
    except OSError:
        return todos

    for file_name in files:
        try:
            content = (pending_dir / file_name).read_text(encoding="utf-8")
            created_m = re.search(r"^created:\s*(.+)$", content, re.MULTILINE)
            title_m = re.search(r"^title:\s*(.+)$", content, re.MULTILINE)
            area_m = re.search(r"^area:\s*(.+)$", content, re.MULTILINE)
            area = area_m.group(1).strip() if area_m else "general"
            if area_filter and area != area_filter:
                continue
            todos.append({
                "file": file_name,
                "created": created_m.group(1).strip() if created_m else "unknown",
                "title": title_m.group(1).strip() if title_m else "Untitled",
                "area": area,
                "path": to_posix_path(f".planning/todos/pending/{file_name}"),
            })
        except OSError:
            continue
    return todos
