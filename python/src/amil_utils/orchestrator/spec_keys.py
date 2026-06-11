"""Spec key validation — checks spec.json has the required top-level keys.

Replaces the Node.js inline validators previously embedded in
amil/workflows/plan-module.md and generate-module.md.
"""
from __future__ import annotations

import json
from pathlib import Path

# Keys the spec-generator agent must produce (mirrors plan-module.md Step 7).
REQUIRED_SPEC_KEYS: tuple[str, ...] = (
    "module_name",
    "module_title",
    "odoo_version",
    "depends",
    "models",
    "business_rules",
    "computation_chains",
    "workflow",
    "view_hints",
    "reports",
    "notifications",
    "cron_jobs",
    "security",
    "portal",
    "controllers",
)

MINIMAL_SPEC_KEYS: tuple[str, ...] = ("module_name",)


def check_spec_keys(spec_path: str | Path, minimal: bool = False) -> dict:
    """Validate spec.json contains required top-level keys.

    Returns a dict: {valid, missing, key_count, module_name, model_count}
    or {valid: False, missing: [], error: str} on read/parse/shape failure.
    """
    path = Path(spec_path)
    try:
        spec = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"valid": False, "missing": [], "error": str(exc)}

    if not isinstance(spec, dict):
        return {"valid": False, "missing": [], "error": "spec root is not an object"}

    required = MINIMAL_SPEC_KEYS if minimal else REQUIRED_SPEC_KEYS
    missing = [k for k in required if k not in spec]
    return {
        "valid": not missing,
        "missing": missing,
        "key_count": len(spec),
        "module_name": spec.get("module_name", ""),
        "model_count": len(spec.get("models", []) or []),
    }
