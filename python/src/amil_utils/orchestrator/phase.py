"""Phase — Phase CRUD and lifecycle operations.

Ported from orchestrator/amil/bin/lib/phase.cjs (711 lines, since deleted).
and orchestrator/amil/bin/lib/phase-complete.cjs (206 lines).

Query operations (phases_list, phase_find, phase_plan_index, phase_next_decimal)
live in phase_query.py. Re-exported here for backward compatibility.
"""
from __future__ import annotations

import json
import logging
import re
import shutil
from datetime import date
from functools import cmp_to_key
from pathlib import Path

logger = logging.getLogger(__name__)

from amil_utils.orchestrator.core import (
    compare_phase_num,
    find_phase,
    generate_slug,
    get_milestone_phase_filter,
    normalize_phase_name,
)
from amil_utils.orchestrator.phase_query import (  # noqa: F401 — re-exports
    phase_find,
    phase_next_decimal,
    phase_plan_index,
    phases_list,
)
from amil_utils.orchestrator.state import write_state_md


# ── Mutation operations ──────────────────────────────────────────────────────


def phase_add(cwd: str | Path, description: str) -> dict:
    """Add a new phase to the end of the milestone."""
    if not description:
        raise ValueError("description required for phase add")

    cwd = Path(cwd)
    roadmap_path = cwd / ".planning" / "ROADMAP.md"
    if not roadmap_path.exists():
        raise ValueError("ROADMAP.md not found")

    content = roadmap_path.read_text(encoding="utf-8")
    slug = generate_slug(description)

    # Find highest integer phase number
    max_phase = 0
    for m in re.finditer(r"#{2,4}\s*Phase\s+(\d+)[A-Z]?(?:\.\d+)*:", content, re.IGNORECASE):
        num = int(m.group(1))
        if num > max_phase:
            max_phase = num

    new_num = max_phase + 1
    padded = str(new_num).zfill(2)
    dir_name = f"{padded}-{slug}"
    dir_path = cwd / ".planning" / "phases" / dir_name

    dir_path.mkdir(parents=True, exist_ok=True)
    (dir_path / ".gitkeep").write_text("")

    phase_entry = (
        f"\n### Phase {new_num}: {description}\n\n"
        f"**Goal:** [To be planned]\n"
        f"**Requirements**: TBD\n"
        f"**Depends on:** Phase {max_phase}\n"
        f"**Plans:** 0 plans\n\n"
        f"Plans:\n"
        f"- [ ] TBD (run /amil:plan-phase {new_num} to break down)\n"
    )

    last_sep = content.rfind("\n---")
    if last_sep > 0:
        updated = content[:last_sep] + phase_entry + content[last_sep:]
    else:
        updated = content + phase_entry

    roadmap_path.write_text(updated, encoding="utf-8")

    return {
        "phase_number": new_num,
        "padded": padded,
        "name": description,
        "slug": slug,
        "directory": f".planning/phases/{dir_name}",
    }


def phase_insert(cwd: str | Path, after_phase: str, description: str) -> dict:
    """Insert an urgent phase after an existing phase (decimal numbering)."""
    if not after_phase or not description:
        raise ValueError("after-phase and description required for phase insert")

    cwd = Path(cwd)
    roadmap_path = cwd / ".planning" / "ROADMAP.md"
    if not roadmap_path.exists():
        raise ValueError("ROADMAP.md not found")

    content = roadmap_path.read_text(encoding="utf-8")
    slug = generate_slug(description)

    normalized_after = normalize_phase_name(after_phase)
    unpadded = normalized_after.lstrip("0") or "0"
    after_escaped = re.escape(unpadded)
    target_pattern = re.compile(rf"#{{2,4}}\s*Phase\s+0*{after_escaped}:", re.IGNORECASE)
    if not target_pattern.search(content):
        raise ValueError(f"Phase {after_phase} not found in ROADMAP.md")

    # Calculate next decimal
    phases_dir = cwd / ".planning" / "phases"
    existing_decimals: list[int] = []
    try:
        dec_pattern = re.compile(rf"^{re.escape(normalized_after)}\.(\d+)")
        for e in phases_dir.iterdir():
            if e.is_dir():
                dm = dec_pattern.match(e.name)
                if dm:
                    existing_decimals.append(int(dm.group(1)))
    except OSError as exc:
        logger.debug("Failed to scan for existing decimal phases: %s", exc)

    next_dec = 1 if not existing_decimals else max(existing_decimals) + 1
    decimal_phase = f"{normalized_after}.{next_dec}"
    dir_name = f"{decimal_phase}-{slug}"
    dir_path = cwd / ".planning" / "phases" / dir_name

    dir_path.mkdir(parents=True, exist_ok=True)
    (dir_path / ".gitkeep").write_text("")

    phase_entry = (
        f"\n### Phase {decimal_phase}: {description} (INSERTED)\n\n"
        f"**Goal:** [Urgent work - to be planned]\n"
        f"**Requirements**: TBD\n"
        f"**Depends on:** Phase {after_phase}\n"
        f"**Plans:** 0 plans\n\n"
        f"Plans:\n"
        f"- [ ] TBD (run /amil:plan-phase {decimal_phase} to break down)\n"
    )

    # Insert after the target phase section
    header_pattern = re.compile(
        rf"(#{{2,4}}\s*Phase\s+0*{after_escaped}:[^\n]*\n)", re.IGNORECASE
    )
    header_match = header_pattern.search(content)
    if not header_match:
        raise ValueError(f"Could not find Phase {after_phase} header")

    header_idx = header_match.start()
    after_header = content[header_idx + len(header_match.group(0)):]
    next_phase_match = re.search(r"\n#{2,4}\s+Phase\s+\d", after_header, re.IGNORECASE)

    if next_phase_match:
        insert_idx = header_idx + len(header_match.group(0)) + next_phase_match.start()
    else:
        insert_idx = len(content)

    updated = content[:insert_idx] + phase_entry + content[insert_idx:]
    roadmap_path.write_text(updated, encoding="utf-8")

    return {
        "phase_number": decimal_phase,
        "after_phase": after_phase,
        "name": description,
        "slug": slug,
        "directory": f".planning/phases/{dir_name}",
    }


_REMOVAL_MANIFEST_NAME = ".removal_manifest.json"


def _write_manifest(phases_dir: Path, manifest: dict) -> Path:
    """Write transaction manifest for atomic phase removal."""
    manifest_path = phases_dir / _REMOVAL_MANIFEST_NAME
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest_path


def _update_manifest_op(manifest_path: Path, op_index: int, status: str) -> None:
    """Update a single operation's status in the manifest."""
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    updated_ops = [
        {**op, "status": status} if i == op_index else op
        for i, op in enumerate(data["operations"])
    ]
    updated_data = {**data, "operations": updated_ops}
    manifest_path.write_text(json.dumps(updated_data, indent=2), encoding="utf-8")


def _rollback_operations(manifest_path: Path, phases_dir: Path) -> list[dict]:
    """Reverse all completed operations from the manifest.

    Returns a list of rollback actions taken.
    """
    rollback_actions: list[dict] = []
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.debug("Failed to read manifest for rollback: %s", exc)
        return rollback_actions

    # Process operations in reverse order for proper rollback
    for op in reversed(data.get("operations", [])):
        if op.get("status") != "done":
            continue

        op_type = op.get("type")
        if op_type == "rename_dir":
            old_path = phases_dir / op["to"]
            new_path = phases_dir / op["from"]
            try:
                if old_path.exists():
                    old_path.rename(new_path)
                    rollback_actions.append({
                        "action": "rename_reversed",
                        "from": op["to"],
                        "to": op["from"],
                    })
            except OSError as exc:
                logger.debug("Rollback rename failed %s -> %s: %s", op["to"], op["from"], exc)
        elif op_type == "rename_file":
            parent = phases_dir / op["parent_dir"]
            old_path = parent / op["to"]
            new_path = parent / op["from"]
            try:
                if old_path.exists():
                    old_path.rename(new_path)
                    rollback_actions.append({
                        "action": "file_rename_reversed",
                        "from": op["to"],
                        "to": op["from"],
                    })
            except OSError as exc:
                logger.debug("Rollback file rename failed: %s", exc)
        elif op_type == "delete_dir":
            rollback_actions.append({
                "action": "delete_not_reversible",
                "directory": op.get("target"),
            })

    return rollback_actions


def phase_repair(cwd: str | Path) -> dict:
    """Check for and handle orphaned removal manifests.

    If a manifest exists, reverses any completed operations and cleans up.
    Returns {"repaired": True/False, "details": ...}
    """
    cwd = Path(cwd)
    phases_dir = cwd / ".planning" / "phases"
    manifest_path = phases_dir / _REMOVAL_MANIFEST_NAME

    if not manifest_path.exists():
        return {"repaired": False, "details": "No orphaned manifest found"}

    rollback_actions = _rollback_operations(manifest_path, phases_dir)

    try:
        manifest_path.unlink()
    except OSError as exc:
        logger.debug("Failed to remove manifest after repair: %s", exc)

    return {
        "repaired": True,
        "details": "Reversed completed operations from orphaned manifest",
        "rollback_actions": rollback_actions,
    }


def phase_remove(cwd: str | Path, target_phase: str, *, force: bool = False) -> dict:
    """Remove a phase: delete directory, renumber subsequent, update ROADMAP/STATE.

    Uses a transaction manifest so that renumbering and ROADMAP/STATE updates
    are best-effort transactional: if the process fails mid-operation,
    ``phase_repair()`` can reverse completed rename operations. The initial
    deletion of the phase directory itself is not rollbackable.
    """
    if not target_phase:
        raise ValueError("phase number required for phase remove")

    cwd = Path(cwd)
    roadmap_path = cwd / ".planning" / "ROADMAP.md"
    phases_dir = cwd / ".planning" / "phases"

    if not roadmap_path.exists():
        raise ValueError("ROADMAP.md not found")

    normalized = normalize_phase_name(target_phase)
    is_decimal = "." in target_phase

    # Find target directory
    target_dir: str | None = None
    try:
        entries = sorted(
            [e.name for e in phases_dir.iterdir() if e.is_dir()],
            key=cmp_to_key(compare_phase_num),
        )
        target_dir = next(
            (d for d in entries if d.startswith(normalized + "-") or d == normalized),
            None,
        )
    except OSError as exc:
        logger.debug("Failed to find target phase directory for removal: %s", exc)

    # Block if phase has executed work
    if target_dir and not force:
        target_path = phases_dir / target_dir
        files = [f.name for f in target_path.iterdir() if f.is_file()]
        summaries = [f for f in files if f.endswith("-SUMMARY.md") or f == "SUMMARY.md"]
        if summaries:
            raise ValueError(
                f"Phase {target_phase} has {len(summaries)} executed plan(s). "
                "Use --force to remove anyway."
            )

    # Build manifest of planned operations
    planned_ops: list[dict] = []
    if target_dir:
        planned_ops.append({"type": "delete_dir", "target": target_dir, "status": "pending"})

    # Pre-compute rename operations
    rename_ops = _plan_rename_operations(phases_dir, normalized, is_decimal)
    planned_ops.extend(rename_ops)

    planned_ops.append({"type": "roadmap_update", "status": "pending"})

    manifest = {
        "target_phase": target_phase,
        "normalized": normalized,
        "is_decimal": is_decimal,
        "operations": planned_ops,
    }
    manifest_path = _write_manifest(phases_dir, manifest)

    # Snapshot ROADMAP.md and STATE.md so we can restore on rollback
    roadmap_backup = roadmap_path.read_text(encoding="utf-8")
    state_path = cwd / ".planning" / "STATE.md"
    state_backup = None
    if state_path.exists():
        state_backup = state_path.read_text(encoding="utf-8")

    try:
        op_index = 0

        # Delete target directory
        if target_dir:
            shutil.rmtree(phases_dir / target_dir)
            _update_manifest_op(manifest_path, op_index, "done")
            op_index += 1

        # Renumber subsequent phases (execute pre-planned renames)
        renamed_dirs: list[dict] = []
        renamed_files: list[dict] = []

        for i, op in enumerate(rename_ops):
            real_index = op_index + i
            if op["type"] == "rename_dir":
                (phases_dir / op["from"]).rename(phases_dir / op["to"])
                renamed_dirs.append({"from": op["from"], "to": op["to"]})
                _update_manifest_op(manifest_path, real_index, "done")
            elif op["type"] == "rename_file":
                parent = phases_dir / op["parent_dir"]
                (parent / op["from"]).rename(parent / op["to"])
                renamed_files.append({"from": op["from"], "to": op["to"]})
                _update_manifest_op(manifest_path, real_index, "done")

        op_index += len(rename_ops)

        # Update ROADMAP.md
        _update_roadmap_after_remove(
            roadmap_path, target_phase, is_decimal,
            int(normalized) if not is_decimal else 0,
        )
        _update_manifest_op(manifest_path, op_index, "done")

        # Update STATE.md phase count
        state_updated = False
        if state_path.exists():
            state_content = state_path.read_text(encoding="utf-8")

            total_pattern = re.compile(r"(\*\*Total Phases:\*\*\s*)(\d+)")
            total_match = total_pattern.search(state_content)
            if total_match:
                old_total = int(total_match.group(2))
                state_content = total_pattern.sub(rf"\g<1>{old_total - 1}", state_content)

            of_pattern = re.compile(r"(\bof\s+)(\d+)(\s*(?:\(|phases?))", re.IGNORECASE)
            of_match = of_pattern.search(state_content)
            if of_match:
                old_total = int(of_match.group(2))
                state_content = of_pattern.sub(rf"\g<1>{old_total - 1}\3", state_content)

            write_state_md(state_path, state_content, cwd)
            state_updated = True

        # Success — remove manifest (non-fatal to avoid triggering rollback)
        try:
            manifest_path.unlink(missing_ok=True)
        except OSError as exc:
            logger.debug("Failed to delete removal manifest: %s", exc)

        return {
            "removed": target_phase,
            "directory_deleted": target_dir,
            "renamed_directories": renamed_dirs,
            "renamed_files": renamed_files,
            "roadmap_updated": True,
            "state_updated": state_updated,
        }
    except Exception:
        # Restore ROADMAP.md to pre-removal state
        try:
            roadmap_path.write_text(roadmap_backup, encoding="utf-8")
        except OSError as exc:
            logger.debug("Failed to restore ROADMAP.md during rollback: %s", exc)
        # Restore STATE.md to pre-removal state
        if state_backup is not None:
            try:
                state_path.write_text(state_backup, encoding="utf-8")
            except OSError as exc:
                logger.debug("Failed to restore STATE.md during rollback: %s", exc)
        # Rollback completed rename operations
        _rollback_operations(manifest_path, phases_dir)
        manifest_path.unlink(missing_ok=True)
        raise


def _plan_rename_operations(
    phases_dir: Path,
    normalized: str,
    is_decimal: bool,
) -> list[dict]:
    """Pre-compute all rename operations needed after phase removal.

    Returns a flat list of rename operations (dirs first, then files within
    each dir) in the order they should be executed.
    """
    ops: list[dict] = []

    try:
        entries = sorted(
            [e.name for e in phases_dir.iterdir() if e.is_dir()],
            key=cmp_to_key(compare_phase_num),
        )
    except OSError:
        return ops

    if is_decimal:
        base_int = normalized.split(".")[0]
        removed_decimal = int(normalized.split(".")[1])
        dec_pattern = re.compile(rf"^{re.escape(base_int)}\.(\d+)-(.+)$")
        to_rename = []
        for d in entries:
            dm = dec_pattern.match(d)
            if dm and int(dm.group(1)) > removed_decimal:
                to_rename.append({
                    "dir": d,
                    "old_decimal": int(dm.group(1)),
                    "slug": dm.group(2),
                })

        # Sort descending to avoid conflicts
        to_rename.sort(key=lambda x: x["old_decimal"], reverse=True)

        for item in to_rename:
            new_decimal = item["old_decimal"] - 1
            old_phase_id = f"{base_int}.{item['old_decimal']}"
            new_phase_id = f"{base_int}.{new_decimal}"
            new_dir_name = f"{base_int}.{new_decimal}-{item['slug']}"

            ops.append({
                "type": "rename_dir",
                "from": item["dir"],
                "to": new_dir_name,
                "status": "pending",
            })

            # Plan file renames within the directory
            try:
                dir_files = list((phases_dir / item["dir"]).iterdir())
                for f in dir_files:
                    if f.is_file() and old_phase_id in f.name:
                        new_name = f.name.replace(old_phase_id, new_phase_id)
                        ops.append({
                            "type": "rename_file",
                            "from": f.name,
                            "to": new_name,
                            "parent_dir": new_dir_name,
                            "status": "pending",
                        })
            except OSError:
                pass
    else:
        removed_int = int(normalized)
        to_rename = []
        for d in entries:
            dm = re.match(r"^(\d+)([A-Z])?(?:\.(\d+))?-(.+)$", d, re.IGNORECASE)
            if not dm:
                continue
            dir_int = int(dm.group(1))
            if dir_int > removed_int:
                to_rename.append({
                    "dir": d,
                    "old_int": dir_int,
                    "letter": (dm.group(2) or "").upper(),
                    "decimal": int(dm.group(3)) if dm.group(3) else None,
                    "slug": dm.group(4),
                })

        # Sort descending to avoid conflicts
        to_rename.sort(key=lambda x: (-x["old_int"], -(x["decimal"] or 0)))

        for item in to_rename:
            new_int = item["old_int"] - 1
            new_padded = str(new_int).zfill(2)
            old_padded = str(item["old_int"]).zfill(2)
            letter = item["letter"]
            dec_suffix = f".{item['decimal']}" if item["decimal"] is not None else ""
            old_prefix = f"{old_padded}{letter}{dec_suffix}"
            new_prefix = f"{new_padded}{letter}{dec_suffix}"
            new_dir_name = f"{new_prefix}-{item['slug']}"

            ops.append({
                "type": "rename_dir",
                "from": item["dir"],
                "to": new_dir_name,
                "status": "pending",
            })

            # Plan file renames within the directory
            try:
                dir_files = list((phases_dir / item["dir"]).iterdir())
                for f in dir_files:
                    if f.is_file() and f.name.startswith(old_prefix):
                        new_name = new_prefix + f.name[len(old_prefix):]
                        ops.append({
                            "type": "rename_file",
                            "from": f.name,
                            "to": new_name,
                            "parent_dir": new_dir_name,
                            "status": "pending",
                        })
            except OSError:
                pass

    return ops


def _update_roadmap_after_remove(
    roadmap_path: Path,
    target_phase: str,
    is_decimal: bool,
    removed_int: int,
) -> None:
    """Update ROADMAP.md after a phase is removed."""
    content = roadmap_path.read_text(encoding="utf-8")
    target_escaped = re.escape(target_phase)

    # Remove the target phase section
    section_re = re.compile(
        rf"\n?#{{2,4}}\s*Phase\s+{target_escaped}\s*:[\s\S]*?(?=\n#{{2,4}}\s+Phase\s+\d|$)",
        re.IGNORECASE,
    )
    content = section_re.sub("", content)

    # Remove from checkbox list
    content = re.sub(
        rf"\n?-\s*\[[ x]\]\s*.*Phase\s+{target_escaped}[:\s][^\n]*",
        "",
        content,
        flags=re.IGNORECASE,
    )

    # Remove from progress table
    content = re.sub(
        rf"\n?\|\s*{target_escaped}\.?\s[^|]*\|[^\n]*",
        "",
        content,
        flags=re.IGNORECASE,
    )

    # Renumber references for integer phases
    if not is_decimal:
        for old_num in range(99, removed_int, -1):
            new_num = old_num - 1
            old_str = str(old_num)
            new_str = str(new_num)
            old_pad = old_str.zfill(2)
            new_pad = new_str.zfill(2)

            content = re.sub(
                rf"(#{{2,4}}\s*Phase\s+){old_str}(\s*:)",
                rf"\g<1>{new_str}\2",
                content,
                flags=re.IGNORECASE,
            )
            content = re.sub(
                rf"(Phase\s+){old_str}([:\s])",
                rf"\g<1>{new_str}\2",
                content,
            )
            content = re.sub(
                rf"{old_pad}-(\d{{2}})",
                rf"{new_pad}-\1",
                content,
            )
            content = re.sub(
                rf"(\|\s*){old_str}\.\s",
                rf"\g<1>{new_str}. ",
                content,
            )
            content = re.sub(
                rf"(Depends on:\*\*\s*Phase\s+){old_str}\b",
                rf"\g<1>{new_str}",
                content,
                flags=re.IGNORECASE,
            )

    roadmap_path.write_text(content, encoding="utf-8")


# ── Lifecycle operations ──────────────────────────────────────────────────────


def phase_complete(cwd: str | Path, phase_num: str) -> dict:
    """Mark a phase as complete: update ROADMAP, REQUIREMENTS, STATE; find next."""
    if not phase_num:
        raise ValueError("phase number required for phase complete")

    cwd = Path(cwd)
    roadmap_path = cwd / ".planning" / "ROADMAP.md"
    state_path = cwd / ".planning" / "STATE.md"
    phases_dir = cwd / ".planning" / "phases"
    today = date.today().isoformat()

    phase_info = find_phase(cwd, phase_num)
    if not phase_info:
        raise ValueError(f"Phase {phase_num} not found")

    plan_count = len(phase_info["plans"])
    summary_count = len(phase_info["summaries"])
    phase_escaped = re.escape(str(phase_num))

    # Update ROADMAP.md
    if roadmap_path.exists():
        roadmap = roadmap_path.read_text(encoding="utf-8")
        roadmap = _mark_phase_complete_in_roadmap(roadmap, phase_escaped, today, summary_count, plan_count)
        roadmap_path.write_text(roadmap, encoding="utf-8")

        # Update REQUIREMENTS.md
        _update_requirements_for_phase(cwd, roadmap, phase_escaped)

    # Find next phase
    next_phase_num, next_phase_name, is_last_phase = _find_next_phase(
        cwd, phases_dir, roadmap_path, phase_num
    )

    # Update STATE.md
    if state_path.exists():
        _update_state_for_phase_complete(
            state_path, cwd, phase_num, today,
            next_phase_num, next_phase_name, is_last_phase,
        )

    return {
        "completed_phase": phase_num,
        "phase_name": phase_info.get("phase_name"),
        "plans_executed": f"{summary_count}/{plan_count}",
        "next_phase": next_phase_num,
        "next_phase_name": next_phase_name,
        "is_last_phase": is_last_phase,
        "date": today,
        "roadmap_updated": roadmap_path.exists(),
        "state_updated": state_path.exists(),
    }


def _mark_phase_complete_in_roadmap(
    content: str,
    phase_escaped: str,
    today: str,
    summary_count: int,
    plan_count: int,
) -> str:
    """Mark a phase as complete in ROADMAP.md content."""
    # Checkbox: - [ ] Phase N: → - [x] Phase N: (...completed DATE)
    content = re.sub(
        rf"(-\s*\[)[ ](\]\s*.*Phase\s+{phase_escaped}[:\s][^\n]*)",
        rf"\g<1>x\2 (completed {today})",
        content,
        flags=re.IGNORECASE,
    )

    # Progress table: update Status to Complete
    content = re.sub(
        rf"(\|\s*{phase_escaped}\.?\s[^|]*\|[^|]*\|)\s*[^|]*(\|)\s*[^|]*(\|)",
        rf"\1 Complete    \2 {today} \3",
        content,
        flags=re.IGNORECASE,
    )

    # Plan count in phase section
    content = re.sub(
        rf"(#{{2,4}}\s*Phase\s+{phase_escaped}[\s\S]*?\*\*Plans:\*\*\s*)[^\n]+",
        rf"\g<1>{summary_count}/{plan_count} plans complete",
        content,
        flags=re.IGNORECASE,
    )

    return content


def _update_requirements_for_phase(
    cwd: Path,
    roadmap_content: str,
    phase_escaped: str,
) -> None:
    """Update REQUIREMENTS.md for a completed phase's requirements."""
    req_path = cwd / ".planning" / "REQUIREMENTS.md"
    if not req_path.exists():
        return

    req_match = re.search(
        rf"Phase\s+{phase_escaped}[\s\S]*?\*\*Requirements:?\*\*:?\s*([^\n]+)",
        roadmap_content,
        re.IGNORECASE,
    )
    if not req_match:
        return

    req_ids = [
        r.strip()
        for r in re.sub(r"[\[\]]", "", req_match.group(1)).split(",")
        if r.strip()
    ]
    if not req_ids:
        return

    req_content = req_path.read_text(encoding="utf-8")
    for req_id in req_ids:
        req_esc = re.escape(req_id)
        req_content = re.sub(
            rf"(-\s*\[)[ ](\]\s*\*\*{req_esc}\*\*)",
            r"\1x\2",
            req_content,
            flags=re.IGNORECASE,
        )
        req_content = re.sub(
            rf"(\|\s*{req_esc}\s*\|[^|]+\|)\s*Pending\s*(\|)",
            r"\1 Complete \2",
            req_content,
            flags=re.IGNORECASE,
        )
    req_path.write_text(req_content, encoding="utf-8")


def _find_next_phase(
    cwd: Path,
    phases_dir: Path,
    roadmap_path: Path,
    phase_num: str,
) -> tuple[str | None, str | None, bool]:
    """Find the next phase after the given one. Returns (num, name, is_last)."""
    next_phase_num: str | None = None
    next_phase_name: str | None = None
    is_last_phase = True

    # Check filesystem first
    try:
        is_dir_in_milestone = get_milestone_phase_filter(cwd)
        entries = sorted(
            [e.name for e in phases_dir.iterdir() if e.is_dir()],
            key=cmp_to_key(compare_phase_num),
        )
        entries = [e for e in entries if is_dir_in_milestone(e)]

        for d in entries:
            dm = re.match(r"^(\d+[A-Z]?(?:\.\d+)*)-?(.*)", d, re.IGNORECASE)
            if dm and compare_phase_num(dm.group(1), phase_num) > 0:
                next_phase_num = dm.group(1)
                next_phase_name = dm.group(2) or None
                is_last_phase = False
                break
    except OSError as exc:
        logger.debug("Failed to read phases directory for next phase lookup: %s", exc)

    # Fallback: check ROADMAP.md for phases not yet scaffolded
    if is_last_phase and roadmap_path.exists():
        try:
            roadmap = roadmap_path.read_text(encoding="utf-8")
            for pm in re.finditer(
                r"#{2,4}\s*Phase\s+(\d+[A-Z]?(?:\.\d+)*)\s*:\s*([^\n]+)",
                roadmap,
                re.IGNORECASE,
            ):
                if compare_phase_num(pm.group(1), phase_num) > 0:
                    next_phase_num = pm.group(1)
                    raw = re.sub(r"\(INSERTED\)", "", pm.group(2), flags=re.IGNORECASE).strip()
                    next_phase_name = re.sub(r"\s+", "-", raw.lower())
                    is_last_phase = False
                    break
        except OSError as exc:
            logger.debug("Failed to read ROADMAP.md for next phase fallback: %s", exc)

    return next_phase_num, next_phase_name, is_last_phase


def _update_state_for_phase_complete(
    state_path: Path,
    cwd: Path,
    phase_num: str,
    today: str,
    next_phase_num: str | None,
    next_phase_name: str | None,
    is_last_phase: bool,
) -> None:
    """Update STATE.md after phase completion."""
    content = state_path.read_text(encoding="utf-8")

    content = re.sub(
        r"(\*\*Current Phase:\*\*\s*).*",
        rf"\g<1>{next_phase_num or phase_num}",
        content,
    )
    if next_phase_name:
        content = re.sub(
            r"(\*\*Current Phase Name:\*\*\s*).*",
            rf"\g<1>{next_phase_name.replace('-', ' ')}",
            content,
        )
    status = "Milestone complete" if is_last_phase else "Ready to plan"
    content = re.sub(r"(\*\*Status:\*\*\s*).*", rf"\g<1>{status}", content)
    content = re.sub(r"(\*\*Current Plan:\*\*\s*).*", r"\g<1>Not started", content)
    content = re.sub(r"(\*\*Last Activity:\*\*\s*).*", rf"\g<1>{today}", content)

    desc = f"Phase {phase_num} complete"
    if next_phase_num:
        desc += f", transitioned to Phase {next_phase_num}"
    content = re.sub(r"(\*\*Last Activity Description:\*\*\s*).*", rf"\g<1>{desc}", content)

    write_state_md(state_path, content, cwd)
