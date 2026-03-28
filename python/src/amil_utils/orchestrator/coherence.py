"""Coherence — Structural validation checks for spec.json consistency.

Ported from orchestrator/amil/bin/lib/coherence.cjs (290 lines, since deleted).
4 checks: many2one_targets, duplicate_models, computed_depends, security_groups.
Each returns: {check, status, violations}
run_all_checks aggregates: {status, checks}
"""
from __future__ import annotations

import json
import warnings
from functools import lru_cache
from pathlib import Path

from amil_utils.orchestrator.dependency_graph import validate_field_reference

# ── Deprecation ──────────────────────────────────────────────────────────────
__deprecated__ = True
_DEPRECATION_NOTICE = (
    f"{__name__} is superseded by odoo-ls validation. "
    "Use --skip-odoo-ls flag to fall back to these checks."
)

# ── Constants ────────────────────────────────────────────────────────────────


@lru_cache(maxsize=1)
def _load_base_models() -> frozenset[str]:
    """Load known Odoo model names from data/known_odoo_models.json."""
    data_file = Path(__file__).resolve().parent.parent / "data" / "known_odoo_models.json"
    if not data_file.exists():
        return frozenset()
    raw = json.loads(data_file.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        # JSON has {"_meta": {...}, "models": {...}} — extract model keys
        models = raw.get("models", {})
        if isinstance(models, dict):
            return frozenset(models.keys())
        return frozenset()
    if isinstance(raw, list):
        return frozenset(raw)
    return frozenset()

_RELATIONAL_TYPES = {"Many2one", "Many2many", "One2many"}


# ── Check Functions ──────────────────────────────────────────────────────────


def check_many2one_targets(spec: dict, registry: dict) -> dict:
    """Check that all relational field targets reference known models."""
    warnings.warn(_DEPRECATION_NOTICE, DeprecationWarning, stacklevel=2)
    violations: list[dict] = []
    spec_model_names = {m["name"] for m in (spec.get("models") or [])}
    registry_model_names = set((registry.get("models") or {}).keys())

    for model in spec.get("models") or []:
        for field in model.get("fields") or []:
            if not field.get("comodel_name"):
                continue
            if field.get("type") not in _RELATIONAL_TYPES:
                continue

            target = field["comodel_name"]
            if target in spec_model_names:
                continue
            if target in registry_model_names:
                continue
            if target in _load_base_models():
                continue

            violations.append({
                "model": model["name"],
                "field": field["name"],
                "target": target,
                "reason": "target model not in registry or spec",
            })

    return {
        "check": "many2one_targets",
        "status": "pass" if not violations else "fail",
        "violations": violations,
    }


def check_duplicate_models(spec: dict, registry: dict) -> dict:
    """Check for cross-module duplicate model names."""
    warnings.warn(_DEPRECATION_NOTICE, DeprecationWarning, stacklevel=2)
    violations: list[dict] = []
    registry_models = registry.get("models") or {}

    for model in spec.get("models") or []:
        reg_model = registry_models.get(model["name"])
        if not reg_model:
            continue
        # Same module updating its own model is OK
        if reg_model.get("module") == model.get("module"):
            continue

        violations.append({
            "model": model["name"],
            "spec_module": model.get("module"),
            "registry_module": reg_model["module"],
            "reason": "model already exists in registry under different module",
        })

    return {
        "check": "duplicate_models",
        "status": "pass" if not violations else "fail",
        "violations": violations,
    }


def check_computed_depends(spec: dict, registry: dict) -> dict:
    """Check that computed field depends paths resolve to existing fields."""
    warnings.warn(_DEPRECATION_NOTICE, DeprecationWarning, stacklevel=2)
    violations: list[dict] = []
    registry_models = registry.get("models") or {}
    base_models = _load_base_models()

    for model in spec.get("models") or []:
        model_name = model["name"]
        spec_field_names = {f["name"] for f in (model.get("fields") or [])}
        reg_model = registry_models.get(model_name)
        reg_field_names = set((reg_model.get("fields") or {}).keys()) if reg_model else set()

        for field in model.get("fields") or []:
            field_name = field["name"]
            if not field.get("compute") or not field.get("depends"):
                continue

            for dep_path in field["depends"]:
                segments = dep_path.split(".")
                first_segment = segments[0]
                if first_segment not in spec_field_names and first_segment not in reg_field_names:
                    violations.append({
                        "model": model_name,
                        "field": field_name,
                        "depends_path": dep_path,
                        "reason": f"First segment '{first_segment}' not found in model fields",
                    })
                    continue

                # Validate deeper segments if path has 2+ parts
                if len(segments) > 1:
                    field_def = next(
                        (f for f in (model.get("fields") or []) if f["name"] == first_segment),
                        None,
                    )
                    if field_def and field_def.get("comodel_name"):
                        comodel = field_def["comodel_name"]
                        second_segment = segments[1]
                        comodel_fields = set()
                        reg_comodel = registry_models.get(comodel, {})
                        for f in (reg_comodel.get("fields") or []):
                            if isinstance(f, dict):
                                comodel_fields.add(f.get("name", ""))
                            elif isinstance(f, str):
                                comodel_fields.add(f)
                        if second_segment not in comodel_fields and comodel not in base_models:
                            violations.append({
                                "model": model_name,
                                "field": field_name,
                                "depends_path": dep_path,
                                "reason": f"Cannot verify '{second_segment}' on '{comodel}'",
                                "severity": "warning",
                            })

    return {
        "check": "computed_depends",
        "status": "pass" if not violations else "fail",
        "violations": violations,
    }


def check_security_groups(spec: dict, registry: dict) -> dict:
    """Check that security ACL keys match roles array."""
    warnings.warn(_DEPRECATION_NOTICE, DeprecationWarning, stacklevel=2)
    violations: list[dict] = []
    security = spec.get("security")

    if not security:
        return {"check": "security_groups", "status": "pass", "violations": []}

    defined_roles = set(security.get("roles") or [])

    # ACL keys must be in roles
    for acl_role in (security.get("acl") or {}):
        if acl_role not in defined_roles:
            violations.append({
                "role": acl_role,
                "location": "acl",
                "reason": "ACL entry references role not defined in security.roles",
            })

    # defaults keys must be in roles
    for default_role in (security.get("defaults") or {}):
        if default_role not in defined_roles:
            violations.append({
                "role": default_role,
                "location": "defaults",
                "reason": "defaults entry references role not defined in security.roles",
            })

    # Every role should have an ACL entry
    acl = security.get("acl") or {}
    for role in defined_roles:
        if role not in acl:
            violations.append({
                "role": role,
                "location": "roles",
                "reason": "role defined but has no ACL entry",
            })

    return {
        "check": "security_groups",
        "status": "pass" if not violations else "fail",
        "violations": violations,
    }


# ── Field/Model Rename Check (NOT deprecated — supplements odoo-ls) ─────────


def check_field_renames(spec: dict, odoo_version: str = "19.0") -> dict:
    """Check that spec fields don't reference renamed Odoo 19 fields/models.

    Unlike other coherence checks, this is NOT deprecated -- it supplements
    odoo-ls validation with rename-specific checks.
    """
    violations: list[dict] = []

    for model in spec.get("models") or []:
        model_name = model.get("name", "")

        # Check if the model itself was renamed
        result = validate_field_reference(model_name, "", odoo_version)
        if result is not None and result["type"] == "model_rename":
            violations.append(result)
            continue  # skip field checks for renamed model

        for field in model.get("fields") or []:
            field_name = field.get("name", "")

            # Check if the field name is a renamed field on this model
            field_result = validate_field_reference(
                model_name, field_name, odoo_version,
            )
            if field_result is not None and field_result["type"] == "field_rename":
                violations.append(field_result)

            # Check if comodel_name references a renamed model
            comodel = field.get("comodel_name")
            if comodel:
                comodel_result = validate_field_reference(
                    comodel, "", odoo_version,
                )
                if comodel_result is not None and comodel_result["type"] == "model_rename":
                    violations.append({
                        **comodel_result,
                        "field": field_name,
                        "message": (
                            f"Field '{model_name}.{field_name}' references "
                            f"model '{comodel}' which was renamed to "
                            f"'{comodel_result['renamed_to']}' in Odoo {odoo_version}"
                        ),
                    })

    return {
        "check": "field_renames",
        "status": "pass" if not violations else "fail",
        "violations": violations,
    }


# ── Aggregation ──────────────────────────────────────────────────────────────


def run_all_checks(spec: dict, registry: dict) -> dict:
    """Run all 4 checks and aggregate results."""
    warnings.warn(_DEPRECATION_NOTICE, DeprecationWarning, stacklevel=2)
    checks = [
        check_many2one_targets(spec, registry),
        check_duplicate_models(spec, registry),
        check_computed_depends(spec, registry),
        check_security_groups(spec, registry),
    ]
    all_pass = all(c["status"] == "pass" for c in checks)
    return {
        "status": "pass" if all_pass else "fail",
        "checks": checks,
    }
