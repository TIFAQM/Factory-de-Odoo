"""Config — Planning config CRUD operations.

Ported from orchestrator/amil/bin/lib/config.cjs (246 lines, since deleted).
Provides config_ensure_section, config_set, config_get, and Odoo-key validation.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Odoo Config Validation ───────────────────────────────────────────────────

VALID_ODOO_VERSIONS = ["17.0", "18.0", "19.0"]
VALID_LOCALIZATIONS = ["pk", "sa", "ae", "none"]
VALID_LMS_INTEGRATIONS = ["canvas", "moodle", "none"]
VALID_DEPLOYMENT_TARGETS = ["single", "multi"]
VALID_NOTIFICATION_CHANNELS = ["email", "sms", "push", "in_app", "whatsapp"]


def validate_odoo_config_key(key_path: str, parsed_value: object, raw_value: object) -> str | None:
    """Validate odoo-specific config keys. Returns None if valid, error string if invalid."""
    if not key_path.startswith("odoo."):
        return None

    if key_path == "odoo.version":
        str_val = str(raw_value) if raw_value is not None else str(parsed_value)
        if str_val not in VALID_ODOO_VERSIONS:
            return f'Invalid odoo.version: "{str_val}". Must be one of: {", ".join(VALID_ODOO_VERSIONS)}'

    if key_path == "odoo.scope_levels":
        if not isinstance(parsed_value, list) or len(parsed_value) == 0:
            return "odoo.scope_levels must be a non-empty array"

    if key_path == "odoo.multi_company":
        if not isinstance(parsed_value, bool):
            return "odoo.multi_company must be a boolean"

    if key_path == "odoo.localization":
        if str(parsed_value) not in VALID_LOCALIZATIONS:
            return f'Invalid odoo.localization: "{parsed_value}". Must be one of: {", ".join(VALID_LOCALIZATIONS)}'

    if key_path == "odoo.canvas_integration":
        if str(parsed_value) not in VALID_LMS_INTEGRATIONS:
            return f'Invalid odoo.canvas_integration: "{parsed_value}". Must be one of: {", ".join(VALID_LMS_INTEGRATIONS)}'

    if key_path == "odoo.deployment_target":
        if str(parsed_value) not in VALID_DEPLOYMENT_TARGETS:
            return f'Invalid odoo.deployment_target: "{parsed_value}". Must be one of: {", ".join(VALID_DEPLOYMENT_TARGETS)}'

    if key_path == "odoo.notification_channels":
        if not isinstance(parsed_value, list):
            return "odoo.notification_channels must be an array"
        for ch in parsed_value:
            if ch not in VALID_NOTIFICATION_CHANNELS:
                return f'Invalid notification channel: "{ch}". Allowed: {", ".join(VALID_NOTIFICATION_CHANNELS)}'

    return None


# ── Internal helpers ─────────────────────────────────────────────────────────


def _parse_value(value: str) -> object:
    """Parse a string value into its Python type (bool, number, JSON, or string)."""
    if value == "true":
        return True
    if value == "false":
        return False
    if isinstance(value, str) and (value.startswith("[") or value.startswith("{")):
        try:
            return json.loads(value)
        except json.JSONDecodeError as exc:
            logger.debug("Failed to parse config value as JSON: %s", exc)
    # Strict numeric parsing — no scientific notation
    if re.match(r"^-?\d+$", str(value)):
        return int(value)
    if re.match(r"^-?\d+\.\d+$", str(value)):
        return float(value)
    return value


def _deep_set(obj: dict, key_arr: list[str], value: object) -> dict:
    """Immutably set a nested value using a key path."""
    if len(key_arr) == 1:
        return {**obj, key_arr[0]: value}
    head, *rest = key_arr
    child = obj.get(head, {})
    if not isinstance(child, dict):
        child = {}
    return {**obj, head: _deep_set(child, rest, value)}


# ── Public API ───────────────────────────────────────────────────────────────


def config_ensure_section(cwd: str | Path) -> dict:
    """Create .planning/config.json with defaults if it doesn't exist."""
    cwd = Path(cwd)
    planning = cwd / ".planning"
    config_path = planning / "config.json"

    # Ensure .planning directory exists
    planning.mkdir(parents=True, exist_ok=True)

    # Check if config already exists
    if config_path.exists():
        return {"created": False, "reason": "already_exists"}

    defaults = {
        "model_profile": "balanced",
        "commit_docs": True,
        "search_gitignored": False,
        "branching_strategy": "none",
        "phase_branch_template": "amil/phase-{phase}-{slug}",
        "milestone_branch_template": "amil/{milestone}-{slug}",
        "workflow": {
            "research": True,
            "plan_check": True,
            "verifier": True,
            "nyquist_validation": True,
        },
        "parallelization": True,
        "brave_search": False,
    }

    config_path.write_text(json.dumps(defaults, indent=2), encoding="utf-8")
    return {"created": True, "path": ".planning/config.json"}


def config_set(cwd: str | Path, key_path: str, value: str) -> dict:
    """Set a config value using dot-notation key path."""
    if not key_path:
        raise ValueError("key path is required")

    cwd = Path(cwd)
    config_path = cwd / ".planning" / "config.json"

    # Parse the value
    parsed_value = _parse_value(value)

    # Validate odoo-specific keys
    if key_path.startswith("odoo."):
        err = validate_odoo_config_key(key_path, parsed_value, value)
        if err:
            raise ValueError(err)
        # Preserve odoo.version as string
        if key_path == "odoo.version":
            parsed_value = str(value)

    # Load existing config
    config: dict = {}
    if config_path.exists():
        config = json.loads(config_path.read_text(encoding="utf-8"))

    # Set nested value
    keys = key_path.split(".")
    config = _deep_set(config, keys, parsed_value)

    # Write back
    config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    return {"updated": True, "key": key_path, "value": parsed_value}


def config_get(cwd: str | Path, key_path: str) -> object:
    """Get a config value using dot-notation key path."""
    cwd = Path(cwd)
    config_path = cwd / ".planning" / "config.json"

    if not config_path.exists():
        raise FileNotFoundError(f"No config.json found at {config_path}")

    config = json.loads(config_path.read_text(encoding="utf-8"))

    # Traverse dot-notation path
    keys = key_path.split(".")
    current: object = config
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            raise KeyError(f"Key not found: {key_path}")
        current = current[key]

    return current
