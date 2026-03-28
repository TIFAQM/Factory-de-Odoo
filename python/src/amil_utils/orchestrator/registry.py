"""Registry — Model registry CRUD with atomic writes, versioning, rollback,
validation, and stats for Odoo module cross-reference tracking.

Ported from orchestrator/amil/bin/lib/registry.cjs (480 lines, since deleted).
The model registry is the central source of truth for all Odoo models
across modules.
"""
from __future__ import annotations

import copy
import json
import re
import shutil
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from amil_utils.orchestrator.module_status import read_status_file

# ── Constants ────────────────────────────────────────────────────────────────

REGISTRY_FILENAME = "model_registry.json"
REGISTRY_BAK_FILENAME = "model_registry.json.bak"

EMPTY_REGISTRY: dict = {
    "_meta": {
        "version": 0,
        "last_updated": None,
        "modules_contributing": [],
        "odoo_version": "19.0",
    },
    "models": {},
}

MODEL_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_.]+$")

RELATIONAL_TYPES: frozenset[str] = frozenset({"Many2one", "One2many", "Many2many"})


# ── Internal helpers ─────────────────────────────────────────────────────────


def _registry_path(cwd: Path) -> Path:
    return cwd / ".planning" / REGISTRY_FILENAME


def _bak_path(cwd: Path) -> Path:
    return cwd / ".planning" / REGISTRY_BAK_FILENAME


def _atomic_write_json(file_path: Path, data: dict) -> None:
    """Atomic write: backup existing → write tmp → rename."""
    bak_file = file_path.with_suffix(file_path.suffix + ".bak")
    tmp_file = file_path.parent / f"{file_path.name}.{uuid.uuid4().hex[:8]}.tmp"

    if file_path.exists():
        shutil.copy2(str(file_path), str(bak_file))

    tmp_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
    try:
        tmp_file.replace(file_path)
    except OSError:
        tmp_file.unlink(missing_ok=True)
        raise


def _empty_registry() -> dict:
    """Return a deep copy of the empty registry template."""
    return copy.deepcopy(EMPTY_REGISTRY)


# ── Public API ───────────────────────────────────────────────────────────────


def remove_module_from_registry(cwd: str | Path, module_name: str) -> dict:
    """Remove all models contributed by a module from the registry.

    Used during backward transitions to clean stale spec data.
    Returns the updated registry.
    """
    if not module_name:
        raise ValueError("module_name is required for registry removal")
    cwd = Path(cwd)
    registry = read_registry_file(cwd)

    # Build new models dict excluding entries owned by the module
    new_models = {
        key: model
        for key, model in registry["models"].items()
        if model.get("module") != module_name
    }

    # Clean up contributing lists in remaining models (remove module_name)
    cleaned_models = {}
    for key, model in new_models.items():
        contributing = model.get("contributing", [])
        if module_name in contributing:
            cleaned_models[key] = {
                **model,
                "contributing": [c for c in contributing if c != module_name],
            }
        else:
            cleaned_models[key] = {**model}

    # Clean up modules_contributing in _meta
    meta_contributing = [
        m for m in registry["_meta"].get("modules_contributing", [])
        if m != module_name
    ]

    new_registry = {
        "_meta": {
            **registry["_meta"],
            "version": registry["_meta"]["version"] + 1,
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "modules_contributing": meta_contributing,
        },
        "models": cleaned_models,
    }

    _atomic_write_json(_registry_path(cwd), new_registry)
    return new_registry


def read_registry_file(cwd: str | Path) -> dict:
    """Read model_registry.json. Recovers from .bak on parse error."""
    cwd = Path(cwd)
    reg_path = _registry_path(cwd)
    bak_file = _bak_path(cwd)

    if not reg_path.exists():
        return _empty_registry()

    try:
        raw = reg_path.read_text(encoding="utf-8")
        return json.loads(raw)
    except (OSError, json.JSONDecodeError):
        # Main file corrupted — try .bak recovery
        if bak_file.exists():
            try:
                bak_raw = bak_file.read_text(encoding="utf-8")
                return json.loads(bak_raw)
            except (OSError, json.JSONDecodeError):
                return _empty_registry()
        return _empty_registry()


def read_model_from_registry(cwd: str | Path, model_name: str) -> dict | None:
    """Read a single model by Odoo model name. Returns None if not found."""
    registry = read_registry_file(cwd)
    return registry["models"].get(model_name)


def update_registry(cwd: str | Path, manifest_path: str) -> dict:
    """Update registry from a manifest file. Returns the new registry state."""
    cwd = Path(cwd)
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    registry = read_registry_file(cwd)

    module_name = manifest.get("module", "unknown")
    manifest_models = manifest.get("models", {})

    # Build new models map (immutable)
    new_models = {**registry["models"]}
    for key, model in manifest_models.items():
        new_models[key] = {**model}

    # Build new modules_contributing (deduplicated)
    contributing = list(registry["_meta"]["modules_contributing"])
    if module_name not in contributing:
        contributing = [*contributing, module_name]

    new_registry = {
        "_meta": {
            **registry["_meta"],
            "version": registry["_meta"]["version"] + 1,
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "modules_contributing": contributing,
        },
        "models": new_models,
    }

    _atomic_write_json(_registry_path(cwd), new_registry)
    return new_registry


def rollback_registry(cwd: str | Path) -> dict | None:
    """Rollback registry to previous version from .bak file."""
    cwd = Path(cwd)
    bak_file = _bak_path(cwd)
    reg_path = _registry_path(cwd)

    if not bak_file.exists():
        return None

    try:
        bak_data = json.loads(bak_file.read_text(encoding="utf-8"))
        shutil.copy2(str(bak_file), str(reg_path))
        return bak_data
    except (OSError, json.JSONDecodeError):
        return None


def validate_registry(cwd: str | Path) -> dict:
    """Validate registry for referential integrity.

    Checks: relational target existence, One2many inverse_name,
    model name format, duplicate model names.
    """
    registry = read_registry_file(cwd)
    errors: list[str] = []
    model_names = set(registry["models"].keys())
    seen_names: dict[str, str] = {}

    for key, model in registry["models"].items():
        # Check model name format
        if not MODEL_NAME_PATTERN.match(key):
            errors.append(f'Model "{key}": name format invalid (must match {MODEL_NAME_PATTERN.pattern})')

        # Check for name mismatch
        if model.get("name") and model["name"] != key:
            errors.append(
                f'Model "{key}": name mismatch/duplicate -- model.name is "{model["name"]}" but key is "{key}"'
            )

        # Track model.name for cross-key duplicate detection
        if model.get("name"):
            if model["name"] in seen_names and seen_names[model["name"]] != key:
                errors.append(
                    f'Duplicate model name "{model["name"]}" found in keys "{seen_names[model["name"]]}" and "{key}"'
                )
            seen_names[model["name"]] = key

        # Check relational field targets
        fields = model.get("fields", {})
        for field_name, field in fields.items():
            if field.get("type") in RELATIONAL_TYPES and field.get("comodel_name"):
                if field["comodel_name"] not in model_names:
                    errors.append(
                        f'Model "{key}", field "{field_name}": {field["type"]} target '
                        f'"{field["comodel_name"]}" not found in registry'
                    )

            # One2many must have inverse_name
            if field.get("type") == "One2many" and not field.get("inverse_name"):
                errors.append(
                    f'Model "{key}", field "{field_name}": One2many missing inverse_name'
                )

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "model_count": len(model_names),
    }


def stats_registry(cwd: str | Path) -> dict:
    """Compute registry statistics."""
    registry = read_registry_file(cwd)
    models = registry["models"]

    field_count = 0
    cross_ref_count = 0

    for model in models.values():
        fields = model.get("fields", {})
        field_count += len(fields)

        for field in fields.values():
            if field.get("type") in RELATIONAL_TYPES and field.get("comodel_name"):
                target_model = models.get(field["comodel_name"])
                if target_model and target_model.get("module") != model.get("module"):
                    cross_ref_count += 1

    return {
        "model_count": len(models),
        "field_count": field_count,
        "cross_reference_count": cross_ref_count,
        "version": registry["_meta"]["version"],
    }


def tiered_registry_injection(cwd: str | Path, module_name: str) -> dict:
    """Return a filtered view of the model registry with three detail tiers.

    - Direct depends: full model data (all fields with metadata)
    - Transitive depends: field-list-only (model name + field names, no metadata)
    - Everything else: names-only (model name and module)
    """
    cwd = Path(cwd)
    registry = read_registry_file(cwd)
    status_data = read_status_file(cwd)
    mod = status_data.get("modules", {}).get(module_name)

    if not mod:
        return {"models": {}}

    direct_deps = set(mod.get("depends", []))

    # Compute transitive deps via BFS
    transitive_deps: set[str] = set()
    queue: deque[str] = deque()
    for dep in direct_deps:
        dep_mod = status_data.get("modules", {}).get(dep)
        if dep_mod and dep_mod.get("depends"):
            for td in dep_mod["depends"]:
                if td not in direct_deps:
                    queue.append(td)

    while queue:
        current = queue.popleft()
        if current in transitive_deps or current in direct_deps:
            continue
        transitive_deps.add(current)
        current_mod = status_data.get("modules", {}).get(current)
        if current_mod and current_mod.get("depends"):
            for td in current_mod["depends"]:
                if td not in direct_deps and td not in transitive_deps:
                    queue.append(td)

    result: dict = {"models": {}}
    for model_name, model in registry["models"].items():
        model_module = model.get("module")
        if model_module in direct_deps:
            # Full model: all fields with metadata
            result["models"][model_name] = {**model, "fields": {**model.get("fields", {})}}
        elif model_module in transitive_deps:
            # Field-list-only: field names without metadata
            result["models"][model_name] = {
                "name": model.get("name"),
                "module": model.get("module"),
                "fields": {f: {"name": f} for f in model.get("fields", {})},
            }
        else:
            # Names-only
            result["models"][model_name] = {
                "name": model.get("name"),
                "module": model.get("module"),
            }

    return result


def spec_to_manifest(spec: dict) -> dict:
    """Convert spec.json format to registry manifest format.

    Spec: {module_name, models: [{name, fields: [{name, type, ...}]}]}
    Manifest: {module, models: {"model.name": {name, module, fields: {field_name: {...}}}}}
    """
    module_name = spec.get("module_name", "unknown")
    models: dict = {}

    for model in spec.get("models", []):
        model_name = model.get("name")
        if not model_name:
            continue

        fields: dict = {}
        for field in model.get("fields", []):
            if not field.get("name"):
                continue
            fields[field["name"]] = {**field}

        models[model_name] = {
            "name": model_name,
            "module": module_name,
            "description": model.get("description", ""),
            "fields": fields,
            "_inherit": model.get("_inherit", []),
        }

    return {"module": module_name, "models": models}


def update_from_spec(cwd: str | Path, spec: dict) -> dict:
    """Update registry from a spec.json object. Returns the new registry state."""
    cwd = Path(cwd)
    manifest = spec_to_manifest(spec)
    registry = read_registry_file(cwd)

    module_name = manifest.get("module", "unknown")
    manifest_models = manifest.get("models", {})

    new_models = {**registry["models"]}
    for key, model in manifest_models.items():
        new_models[key] = {**model}

    contributing = list(registry["_meta"]["modules_contributing"])
    if module_name not in contributing:
        contributing = [*contributing, module_name]

    new_registry = {
        "_meta": {
            **registry["_meta"],
            "version": registry["_meta"]["version"] + 1,
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "modules_contributing": contributing,
        },
        "models": new_models,
    }

    # F7: Track security group XML IDs
    security = spec.get("security", {})
    security_groups = {}
    for role in (security.get("roles") or []):
        if isinstance(role, str):
            security_groups[role] = f"{module_name}.group_{role}"
        elif isinstance(role, dict):
            role_name = role.get("name", "")
            security_groups[role_name] = role.get("xml_id", f"{module_name}.group_{role_name}")
    if security_groups:
        new_registry["security_groups"] = {
            **new_registry.get("security_groups", {}),
            module_name: security_groups,
        }

    # F8: Track view XML IDs for cross-module inheritance
    view_xml_ids = []
    for model in spec.get("models", []):
        model_var = model["name"].replace(".", "_")
        view_xml_ids.extend([
            f"{module_name}.view_{model_var}_form",
            f"{module_name}.view_{model_var}_list",
            f"{module_name}.view_{model_var}_search",
        ])
    if view_xml_ids:
        new_registry["view_xml_ids"] = {
            **new_registry.get("view_xml_ids", {}),
            module_name: view_xml_ids,
        }

    _atomic_write_json(_registry_path(cwd), new_registry)
    return new_registry
