"""Module Status — Lifecycle state machine for Odoo module tracking.

Ported from orchestrator/amil/bin/lib/module-status.cjs (206 lines, since deleted).
Manages module lifecycle: planned -> spec_approved -> generated -> checked -> shipped.
"""
from __future__ import annotations

import copy
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

VALID_TRANSITIONS: dict[str, list[str]] = {
    "planned": ["spec_approved"],
    "spec_approved": ["generated", "planned"],
    "generated": ["checked", "spec_approved"],
    "checked": ["shipped"],
    "shipped": [],
}

_EMPTY_MODULE_STATUS: dict = {
    "_meta": {"version": 0, "last_updated": None},
    "modules": {},
    "tiers": {},
}

_BACKWARD_TRANSITIONS: frozenset[tuple[str, str]] = frozenset({
    ("spec_approved", "planned"),
    ("generated", "spec_approved"),
})


# ── Internal helpers ─────────────────────────────────────────────────────────


def _status_file_path(cwd: Path) -> Path:
    return cwd / ".planning" / "module_status.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_status_file(cwd: str | Path) -> dict:
    """Read module_status.json or return empty structure."""
    file_path = _status_file_path(Path(cwd))
    try:
        raw = file_path.read_text(encoding="utf-8")
        return json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return copy.deepcopy(_EMPTY_MODULE_STATUS)


def _atomic_write_json(file_path: Path, data: dict) -> None:
    """Write JSON atomically via tmp + rename."""
    file_path.parent.mkdir(parents=True, exist_ok=True)
    backup_path = file_path.with_suffix(file_path.suffix + ".bak")
    if file_path.exists():
        import shutil
        shutil.copy2(str(file_path), str(backup_path))
    tmp_path = file_path.parent / f"{file_path.name}.{uuid.uuid4().hex[:8]}.tmp"
    tmp_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    try:
        tmp_path.replace(file_path)
    except OSError:
        tmp_path.unlink(missing_ok=True)
        raise


def _write_status_file(cwd: Path, data: dict) -> dict:
    """Write status file with version bump and timestamp."""
    updated = {
        **data,
        "_meta": {
            **data.get("_meta", {}),
            "version": (data.get("_meta", {}).get("version") or 0) + 1,
            "last_updated": _now_iso(),
        },
    }
    _atomic_write_json(_status_file_path(cwd), updated)
    return updated


# ── Public API ───────────────────────────────────────────────────────────────


def module_status_read(cwd: str | Path) -> dict:
    """Read full module_status.json."""
    return read_status_file(cwd)


def module_status_get(cwd: str | Path, module_name: str) -> dict:
    """Get single module status. Defaults to 'planned' if not found."""
    data = read_status_file(cwd)
    mod = data["modules"].get(module_name)
    if mod:
        return {"name": module_name, **mod}
    return {"name": module_name, "status": "planned", "tier": None, "depends": [], "updated": None}


def module_status_init(
    cwd: str | Path,
    module_name: str,
    tier: str,
    depends: list[str] | None = None,
) -> dict:
    """Initialize a new module with status 'planned'."""
    cwd = Path(cwd)
    data = read_status_file(cwd)

    if module_name in data["modules"]:
        raise ValueError(f'Module "{module_name}" already exists')

    now = _now_iso()
    new_modules = {
        **data["modules"],
        module_name: {
            "status": "planned",
            "tier": tier,
            "depends": depends or [],
            "updated": now,
            "artifacts_dir": f".planning/modules/{module_name}/",
        },
    }

    new_data = {**data, "modules": new_modules}
    written = _write_status_file(cwd, new_data)

    # Create artifact directory with CONTEXT.md placeholder
    artifacts_dir = cwd / ".planning" / "modules" / module_name
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    context_path = artifacts_dir / "CONTEXT.md"
    context_path.write_text(f"# {module_name} Context\n", encoding="utf-8")

    return written


def module_status_transition(
    cwd: str | Path,
    module_name: str,
    new_status: str,
) -> dict:
    """Transition module status with validation."""
    cwd = Path(cwd)
    data = read_status_file(cwd)
    mod = data["modules"].get(module_name)
    current_status = mod["status"] if mod else "planned"

    allowed = VALID_TRANSITIONS.get(current_status, [])
    if new_status not in allowed:
        allowed_str = ", ".join(allowed) if allowed else "none"
        raise ValueError(
            f'Invalid transition: {module_name} cannot go from '
            f'"{current_status}" to "{new_status}". Allowed: {allowed_str}'
        )

    # Backward transition cleanup
    if (current_status, new_status) in _BACKWARD_TRANSITIONS:
        logger.warning(
            "Module '%s' transitioning backward: %s → %s",
            module_name, current_status, new_status,
        )
        if current_status == "spec_approved" and new_status == "planned":
            from amil_utils.orchestrator.registry import remove_module_from_registry
            remove_module_from_registry(cwd, module_name)
        elif current_status == "generated" and new_status == "spec_approved":
            # No cleanup needed: generated files remain on disk and will be
            # overwritten during re-generation. Registry entries stay valid
            # since the spec hasn't changed (only regeneration is needed).
            pass

    now = _now_iso()
    updated_module = {
        **(mod or {"tier": None, "depends": []}),
        "status": new_status,
        "updated": now,
    }
    new_modules = {**data["modules"], module_name: updated_module}
    new_data = {**data, "modules": new_modules}
    return _write_status_file(cwd, new_data)


def get_generation_queue(cwd: str | Path) -> list[str]:
    """Return module names that are spec_approved but not yet generated.

    Modules in this queue are eligible for generation. Returns names in the
    order they appear in module_status.json. For dependency-ordered generation,
    the caller should cross-reference with ``dep_graph_order()`` or
    ``topo_sort()``.

    Args:
        cwd: Project root directory containing ``.planning/module_status.json``.

    Returns:
        List of module names with status ``"spec_approved"``, or an empty list
        if the status file is missing or contains no eligible modules.
    """
    data = read_status_file(cwd)
    modules = data.get("modules", {})
    return [
        name
        for name, info in modules.items()
        if info.get("status") == "spec_approved"
    ]


def tier_status(cwd: str | Path) -> dict:
    """Compute tier summary: group modules by tier, detect completion."""
    data = read_status_file(cwd)
    tier_map: dict[str, dict] = {}

    for name, mod in data["modules"].items():
        tier = mod.get("tier") or "unknown"
        if tier not in tier_map:
            tier_map[tier] = {"modules": [], "status": "incomplete", "counts": {}}
        tier_map[tier]["modules"].append(name)
        s = mod.get("status") or "planned"
        tier_map[tier]["counts"][s] = tier_map[tier]["counts"].get(s, 0) + 1

    # Determine tier completion
    for tier_info in tier_map.values():
        total = sum(tier_info["counts"].values())
        shipped = tier_info["counts"].get("shipped", 0)
        all_shipped = total > 0 and shipped == total
        tier_info["status"] = "complete" if all_shipped else "incomplete"

    return {"tiers": tier_map}
