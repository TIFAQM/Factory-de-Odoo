"""Coherence — Structural validation checks for spec.json consistency.

Ported from orchestrator/amil/bin/lib/coherence.cjs (290 lines, since deleted).
4 checks: many2one_targets, duplicate_models, computed_depends, security_groups.
Each returns: {check, status, violations}
run_all_checks aggregates: {status, checks}
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

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
    violations: list[dict] = []
    registry_models = registry.get("models") or {}

    for model in spec.get("models") or []:
        spec_field_names = {f["name"] for f in (model.get("fields") or [])}
        reg_model = registry_models.get(model["name"])
        reg_field_names = set((reg_model.get("fields") or {}).keys()) if reg_model else set()

        for field in model.get("fields") or []:
            if not field.get("compute") or not field.get("depends"):
                continue

            for dep_path in field["depends"]:
                first_segment = dep_path.split(".")[0]
                if first_segment in spec_field_names:
                    continue
                if first_segment in reg_field_names:
                    continue

                violations.append({
                    "model": model["name"],
                    "field": field["name"],
                    "depends_path": dep_path,
                    "reason": f'field "{first_segment}" not found on model',
                })

    return {
        "check": "computed_depends",
        "status": "pass" if not violations else "fail",
        "violations": violations,
    }


def check_security_groups(spec: dict, registry: dict) -> dict:
    """Check that security ACL keys match roles array."""
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


# ── Aggregation ──────────────────────────────────────────────────────────────


def run_all_checks(spec: dict, registry: dict) -> dict:
    """Run all 4 checks and aggregate results."""
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
