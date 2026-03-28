"""Dependency Graph — Topological sort, cycle detection, tier grouping, generation blocking.

Ported from orchestrator/amil/bin/lib/dependency-graph.cjs (202 lines, since deleted).
Reads module dependency data from module_status.json and provides:
- Topological ordering for generation sequence
- Circular dependency detection with cycle path reporting
- Tier grouping based on dependency depth
- Generation readiness checking (all deps must be >= "generated")
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from amil_utils.orchestrator.module_status import read_status_file

logger = logging.getLogger(__name__)

# ── Module-level caches ────────────────────────────────────────────────────

_external_modules_cache: dict[str, frozenset[str]] = {}
_renames_cache: dict | None = None


def _load_renames_data() -> dict:
    """Load module renames/merges data, cached at module level."""
    global _renames_cache  # noqa: PLW0603
    if _renames_cache is not None:
        return _renames_cache
    data_file = Path(__file__).parent.parent / "data" / "module_renames.json"
    try:
        raw = json.loads(data_file.read_text(encoding="utf-8"))
        _renames_cache = raw if isinstance(raw, dict) else {}
    except (OSError, json.JSONDecodeError):
        _renames_cache = {}
    return _renames_cache


def _get_renamed_modules(odoo_version: str) -> frozenset[str]:
    """Return the set of module names that were renamed or merged in the given version."""
    renames_data = _load_renames_data()
    version_data = renames_data.get(odoo_version, {})
    removed: set[str] = set()
    removed.update(version_data.get("modules_renamed", {}).keys())
    removed.update(version_data.get("modules_merged", {}).keys())
    return frozenset(removed)


def _load_external_module_names(odoo_version: str = "19.0") -> frozenset[str]:
    """Load known Odoo module names that are external (not generated).

    Args:
        odoo_version: Odoo version string (e.g. "17.0", "19.0").
                      Modules renamed/merged in this version are excluded.
    """
    if odoo_version in _external_modules_cache:
        return _external_modules_cache[odoo_version]

    data_file = Path(__file__).parent.parent / "data" / "known_odoo_models.json"
    try:
        raw = json.loads(data_file.read_text(encoding="utf-8"))
        if isinstance(raw, dict) and "models" in raw:
            modules = set()
            for model_name, model_info in raw["models"].items():
                parts = model_name.split(".")
                if parts:
                    modules.add(parts[0])
                # Also extract from the explicit "module" field
                if isinstance(model_info, dict) and model_info.get("module"):
                    modules.add(model_info["module"])
            modules.update({"base", "web", "mail", "account", "stock", "hr", "sale", "purchase", "project", "crm", "website", "portal", "board", "bus"})
            # Filter out modules renamed/merged in the target version
            renamed = _get_renamed_modules(odoo_version)
            result = frozenset(modules - renamed)
            _external_modules_cache[odoo_version] = result
            return result
    except (OSError, json.JSONDecodeError):
        pass
    fallback = frozenset({"base", "web", "mail"})
    _external_modules_cache[odoo_version] = fallback
    return fallback


def validate_external_dependency(
    dep_name: str, odoo_version: str = "19.0",
) -> dict | None:
    """Check if a dependency has been renamed/merged in the target Odoo version.

    Returns None if dep is valid, or a dict with warning info if renamed/merged.
    """
    renames_data = _load_renames_data()
    version_data = renames_data.get(odoo_version, {})

    renamed_to = version_data.get("modules_renamed", {}).get(dep_name)
    if renamed_to is not None:
        return {
            "dependency": dep_name,
            "renamed_to": renamed_to,
            "version": odoo_version,
            "message": (
                f"Module '{dep_name}' was renamed to "
                f"'{renamed_to}' in Odoo {odoo_version}"
            ),
        }

    merged_into = version_data.get("modules_merged", {}).get(dep_name)
    if merged_into is not None:
        return {
            "dependency": dep_name,
            "renamed_to": merged_into,
            "version": odoo_version,
            "message": (
                f"Module '{dep_name}' was renamed to "
                f"'{merged_into}' in Odoo {odoo_version}"
            ),
        }

    return None

# ── Constants ────────────────────────────────────────────────────────────────

TIER_LABELS: list[str] = ["foundation", "core", "operations", "communication"]

GENERATED_OR_BEYOND: frozenset[str] = frozenset({"generated", "checked", "shipped"})


# ── Internal helpers ─────────────────────────────────────────────────────────


def _visit(
    name: str,
    modules: dict[str, dict],
    visited: set[str],
    visiting: set[str],
    result: list[str],
    ancestors: list[str],
    *,
    strict: bool = True,
    external_modules: frozenset[str] | None = None,
) -> None:
    """DFS visit for topological sort with cycle detection.

    Args:
        strict: If True (default), raise ValueError on unknown dependencies.
                If False, log a warning and skip the phantom.
        external_modules: Known external Odoo module names to skip silently.
    """
    if name in visited:
        return

    if name in visiting:
        cycle_start = ancestors.index(name)
        cycle_path = ancestors[cycle_start:] + [name]
        raise ValueError(f"Circular dependency detected: {' -> '.join(cycle_path)}")

    if external_modules and name not in modules and name in external_modules:
        visited.add(name)
        return

    if name not in modules:
        referrer = ancestors[-1] if ancestors else "<root>"
        if strict:
            raise ValueError(
                f"Unknown dependency '{name}' referenced by {referrer}"
            )
        else:
            logger.warning(
                "Unknown dependency '%s' referenced by %s — skipping",
                name,
                referrer,
            )
            visited.add(name)
            return

    visiting.add(name)

    mod = modules[name]
    if mod.get("depends"):
        for dep in mod["depends"]:
            _visit(
                dep, modules, visited, visiting, result, [*ancestors, name],
                strict=strict,
                external_modules=external_modules,
            )

    visiting.discard(name)
    visited.add(name)
    result.append(name)


# ── Public API ───────────────────────────────────────────────────────────────


def topo_sort(
    modules: dict[str, dict],
    *,
    strict: bool = True,
    external_modules: frozenset[str] | None = None,
    odoo_version: str = "19.0",
) -> list[str]:
    """DFS-based topological sort with cycle detection.

    Args:
        modules: Mapping of {name: {"depends": [dep1, dep2]}}.
        strict: If True (default), raise ValueError when a dependency references
                a name not present in *modules*. If False, log a warning and
                skip the phantom dependency.
        external_modules: Known external Odoo module names to skip silently.
                          Defaults to names derived from known_odoo_models.json.
        odoo_version: Odoo version string for filtering renamed/merged modules.

    Returns:
        Module names in dependency order (deps before dependents).

    Raises:
        ValueError: If a circular dependency is detected, or if strict=True
                    and an unknown dependency is encountered.
    """
    external_modules = external_modules or _load_external_module_names(odoo_version)
    visited: set[str] = set()
    visiting: set[str] = set()
    result: list[str] = []

    for name in modules:
        _visit(
            name, modules, visited, visiting, result, [],
            strict=strict,
            external_modules=external_modules,
        )

    return result


def compute_tiers(modules: dict[str, dict]) -> dict:
    """Compute tier labels based on max dependency depth.

    Returns:
        {"tiers": {label: [names]}, "depths": {name: int}, "order": [names]}
    """
    order = topo_sort(modules)
    depths: dict[str, int] = {}

    # Process in topological order so deps are computed first
    for name in order:
        mod = modules.get(name)
        deps = (mod.get("depends") or []) if mod else []
        if not deps:
            depths[name] = 0
        else:
            depths[name] = max(depths.get(d, 0) for d in deps) + 1

    # Group by tier label
    tiers: dict[str, list[str]] = {}
    for name in order:
        depth = depths[name]
        tier_index = min(depth, len(TIER_LABELS) - 1)
        tier_label = TIER_LABELS[tier_index]
        if tier_label not in tiers:
            tiers[tier_label] = []
        tiers[tier_label].append(name)

    return {"tiers": tiers, "depths": depths, "order": order}


def dep_graph_build(cwd: str | Path) -> dict:
    """Build adjacency list from module_status.json."""
    data = read_status_file(cwd)
    modules: dict[str, dict] = {}

    for name, mod in data.get("modules", {}).items():
        modules[name] = {"depends": mod.get("depends", [])}

    return {"modules": modules}


def dep_graph_order(
    cwd: str | Path, *, odoo_version: str = "19.0",
) -> list[str]:
    """Return modules in topological (generation) order."""
    data = read_status_file(cwd)
    modules: dict[str, dict] = {}

    for name, mod in data.get("modules", {}).items():
        modules[name] = {"depends": mod.get("depends", [])}

    return topo_sort(modules, odoo_version=odoo_version)


def dep_graph_tiers(cwd: str | Path) -> dict:
    """Return tier groupings based on dependency depth."""
    data = read_status_file(cwd)
    modules: dict[str, dict] = {}

    for name, mod in data.get("modules", {}).items():
        modules[name] = {"depends": mod.get("depends", [])}

    return compute_tiers(modules)


def dep_graph_can_generate(cwd: str | Path, module_name: str) -> dict:
    """Check if a module's dependencies have all reached 'generated' status or beyond."""
    if not module_name:
        raise ValueError("Usage: dep-graph can-generate <module_name>")

    data = read_status_file(cwd)
    mod = data.get("modules", {}).get(module_name)

    if not mod:
        raise ValueError(f'Module "{module_name}" not found in module_status.json')

    depends = mod.get("depends", [])
    blocked_by: list[dict] = []

    for dep in depends:
        dep_mod = data.get("modules", {}).get(dep)
        dep_status = dep_mod["status"] if dep_mod else "planned"
        if dep_status not in GENERATED_OR_BEYOND:
            blocked_by.append({"module": dep, "status": dep_status})

    return {
        "can_generate": len(blocked_by) == 0,
        "blocked_by": blocked_by,
    }
