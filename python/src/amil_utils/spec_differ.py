"""Spec differ module: compare two spec JSON versions and produce hierarchical change objects.

Provides:
- diff_specs(): Main entry point returning hierarchical diff with destructiveness
- format_human_summary(): Console-friendly output with +/-/~/! symbols
- SpecDiff: TypedDict for the diff result structure
- _spec_to_diffable(): Convert list-indexed spec to dict-indexed for stable paths
- _classify_destructiveness(): Classify changes by severity level

Per-stage diff analysis functions live in spec_differ_stages.py and are
re-exported here for backward compatibility.
"""

from __future__ import annotations

import copy
import logging
from typing import Any, TypedDict

from deepdiff import DeepDiff

logger = logging.getLogger("amil.spec_differ")


# ---------------------------------------------------------------------------
# Type Definitions
# ---------------------------------------------------------------------------

class FieldChange(TypedDict, total=False):
    name: str
    type: str
    changes: dict[str, dict[str, Any]]
    destructive: bool
    severity: str


class FieldChanges(TypedDict, total=False):
    added: list[dict[str, Any]]
    removed: list[FieldChange]
    modified: list[FieldChange]


class ModelChange(TypedDict, total=False):
    name: str
    fields: list[dict[str, Any]] | FieldChanges
    destructive: bool


class ChangesModels(TypedDict, total=False):
    added: list[ModelChange]
    removed: list[ModelChange]
    modified: dict[str, dict]


class Changes(TypedDict, total=False):
    models: ChangesModels
    cron_jobs: dict
    reports: dict


class SpecDiff(TypedDict, total=False):
    module: str
    old_version: str
    new_version: str
    changes: Changes
    destructive_count: int
    warnings: list[str]
    migration_required: bool


# ---------------------------------------------------------------------------
# Destructiveness Classification
# ---------------------------------------------------------------------------

# Type transitions that always cause data loss
ALWAYS_DESTRUCTIVE_TYPE_CHANGES: frozenset[tuple[str, str]] = frozenset({
    ("Text", "Char"),
    ("Text", "Integer"),
    ("Float", "Integer"),
    ("Monetary", "Integer"),
    ("Many2one", "Char"),
    ("Char", "Many2one"),
    ("Selection", "Integer"),
    ("Boolean", "Many2one"),
    ("Monetary", "Char"),
    ("Many2one", "Integer"),
    ("Many2one", "Boolean"),
    ("Many2one", "Selection"),
    ("Many2many", "Char"),
    ("One2many", "Char"),
    ("Html", "Char"),
    ("Html", "Integer"),
    ("Binary", "Char"),
})

# Type transitions that widen data (safe)
TYPE_WIDENING: frozenset[tuple[str, str]] = frozenset({
    ("Char", "Text"),
    ("Integer", "Float"),
    ("Integer", "Monetary"),
    ("Char", "Html"),
    ("Boolean", "Integer"),
})

# Type transitions that may lose precision or have edge cases
POSSIBLY_DESTRUCTIVE_TYPE_CHANGES: frozenset[tuple[str, str]] = frozenset({
    ("Float", "Monetary"),
    ("Monetary", "Float"),
})

# Presentation-only attributes excluded from schema comparison
EXCLUDED_FIELD_ATTRIBUTES: frozenset[str] = frozenset({
    "string",
    "help",
    "placeholder",
})


def _classify_destructiveness(
    change_type: str, old_val: Any, new_val: Any, attribute: str
) -> str:
    """Classify a single change by destructiveness severity.

    Args:
        change_type: Category of change (e.g., "type", "required", "field_removed",
                     "field_added", "model_removed", "selection_removed",
                     "selection_added", "attribute").
        old_val: Previous value.
        new_val: New value.
        attribute: The attribute name being changed.

    Returns:
        One of: "always_destructive", "possibly_destructive", "non_destructive"
    """
    # Field or model removed
    if change_type in ("field_removed", "model_removed"):
        return "always_destructive"

    # Field or model added
    if change_type in ("field_added", "model_added"):
        return "non_destructive"

    # Type changes
    if change_type == "type" and attribute == "type":
        pair = (old_val, new_val)
        if pair in ALWAYS_DESTRUCTIVE_TYPE_CHANGES:
            return "always_destructive"
        if pair in TYPE_WIDENING:
            return "non_destructive"
        if pair in POSSIBLY_DESTRUCTIVE_TYPE_CHANGES:
            return "possibly_destructive"
        # Unknown type transition -- assume possibly destructive
        return "possibly_destructive"

    # Required false -> true
    if change_type == "required" and attribute == "required":
        if old_val is False and new_val is True:
            return "possibly_destructive"
        return "non_destructive"

    # Selection option changes
    if change_type == "selection_removed":
        return "possibly_destructive"
    if change_type == "selection_added":
        return "non_destructive"

    # Presentation-only attributes
    if attribute in EXCLUDED_FIELD_ATTRIBUTES:
        return "non_destructive"

    # Default: non-destructive for other attribute changes
    return "non_destructive"


# ---------------------------------------------------------------------------
# Import per-stage diff functions (must be after constants/types they depend on)
# Re-exported here for backward compatibility.
# ---------------------------------------------------------------------------

from amil_utils.spec_differ_stages import (  # noqa: E402
    _diff_approval,
    _diff_constraints,
    _diff_cron_jobs,
    _diff_field_attributes,
    _diff_models,
    _diff_reports,
    _diff_security,
    _diff_webhooks,
    _selection_changes,
)


# ---------------------------------------------------------------------------
# Spec Preprocessing
# ---------------------------------------------------------------------------

def _spec_to_diffable(spec: dict) -> dict:
    """Convert list-indexed spec to dict-indexed for stable deepdiff paths.

    Transforms:
    - spec['models'] from list to dict keyed by model name
    - Each model's 'fields' from list to dict keyed by field name
    - Each model's 'constraints' from list to dict keyed by constraint name
    - Each model's 'approval.levels' from list to dict keyed by level name
    - spec['cron_jobs'] from list to dict keyed by cron name
    - spec['reports'] from list to dict keyed by report name

    This eliminates deepdiff index instability (Pitfall 1 from RESEARCH.md).
    """
    result = {k: v for k, v in spec.items() if k not in ("models", "cron_jobs", "reports")}

    # Convert models list -> dict keyed by name
    models: dict[str, dict] = {}
    for model in spec.get("models", []):
        model_data = {k: v for k, v in model.items() if k not in ("name", "fields", "constraints")}

        # Convert fields list -> dict keyed by name
        fields: dict[str, dict] = {}
        for field in model.get("fields", []):
            field_data = {k: v for k, v in field.items() if k != "name"}
            fields[field["name"]] = field_data
        model_data["fields"] = fields

        # Convert constraints list -> dict keyed by name
        constraints_list = model.get("constraints", [])
        if constraints_list:
            constraints: dict[str, dict] = {}
            for c in constraints_list:
                constraints[c["name"]] = {k: v for k, v in c.items() if k != "name"}
            model_data["constraints"] = constraints

        # Convert approval levels list -> dict keyed by name
        approval = model.get("approval")
        if approval and "levels" in approval:
            levels_dict: dict[str, dict] = {}
            for level in approval["levels"]:
                levels_dict[level["name"]] = {k: v for k, v in level.items() if k != "name"}
            model_data["approval"] = {**approval, "levels": levels_dict}

        models[model["name"]] = model_data
    result["models"] = models

    # Convert cron_jobs list -> dict keyed by name
    cron_list = spec.get("cron_jobs", [])
    if cron_list:
        cron_dict: dict[str, dict] = {}
        for cj in cron_list:
            cron_dict[cj["name"]] = {k: v for k, v in cj.items() if k != "name"}
        result["cron_jobs"] = cron_dict

    # Convert reports list -> dict keyed by name
    reports_list = spec.get("reports", [])
    if reports_list:
        reports_dict: dict[str, dict] = {}
        for r in reports_list:
            reports_dict[r["name"]] = {k: v for k, v in r.items() if k != "name"}
        result["reports"] = reports_dict

    return result


# ---------------------------------------------------------------------------
# DeepDiff Translation
# ---------------------------------------------------------------------------

def _parse_path(path_str: str) -> list[str]:
    """Parse a deepdiff path string into components.

    Example: "root['models']['fee.invoice']['fields']['amount']['type']"
    Returns: ['models', 'fee.invoice', 'fields', 'amount', 'type']
    """
    parts: list[str] = []
    # Remove 'root' prefix
    remaining = path_str
    if remaining.startswith("root"):
        remaining = remaining[4:]

    while remaining:
        if remaining.startswith("['"):
            end = remaining.index("']", 2)
            parts.append(remaining[2:end])
            remaining = remaining[end + 2:]
        elif remaining.startswith("["):
            end = remaining.index("]", 1)
            parts.append(remaining[1:end])
            remaining = remaining[end + 1:]
        else:
            break

    return parts


# ---------------------------------------------------------------------------
# Main Entry Point
# ---------------------------------------------------------------------------

def diff_specs(old_spec: dict, new_spec: dict) -> SpecDiff:
    """Compare two spec versions and produce hierarchical change objects.

    Uses deepcopy on inputs to guarantee pure function behavior.
    Converts specs to dict-indexed form for stable comparison paths.

    Args:
        old_spec: The previous spec version.
        new_spec: The new spec version.

    Returns:
        SpecDiff with keys: module, old_version, new_version, changes,
        destructive_count, warnings, migration_required.
    """
    # Deep copy inputs to guarantee pure function
    old = copy.deepcopy(old_spec)
    new = copy.deepcopy(new_spec)

    # Convert to diffable format
    old_diffable = _spec_to_diffable(old)
    new_diffable = _spec_to_diffable(new)

    # Diff models
    models_changes, destructive_count, warnings = _diff_models(old_diffable, new_diffable)

    # Diff cron jobs
    cron_changes = _diff_cron_jobs(old_diffable, new_diffable)

    # Diff reports
    report_changes = _diff_reports(old_diffable, new_diffable)

    changes: Changes = {
        "models": models_changes,
    }
    if cron_changes:
        changes["cron_jobs"] = cron_changes
    if report_changes:
        changes["reports"] = report_changes

    migration_required = destructive_count > 0
    logger.info(
        "Diff complete for module '%s': %d destructive change(s), migration %s",
        old_spec.get("module_name", "unknown"),
        destructive_count,
        "required" if migration_required else "not required",
    )

    return {
        "module": old_spec.get("module_name", "unknown"),
        "old_version": old_spec.get("version", "unknown"),
        "new_version": new_spec.get("version", "unknown"),
        "changes": changes,
        "destructive_count": destructive_count,
        "warnings": warnings,
        "migration_required": migration_required,
    }


# ---------------------------------------------------------------------------
# Human-Readable Formatting
# ---------------------------------------------------------------------------

def format_human_summary(diff_result: SpecDiff) -> str:
    """Format a diff result for console output.

    Uses symbols: + added, - removed, ~ modified, ! destructive.
    Includes warning count footer when destructive changes exist.

    Args:
        diff_result: Output from diff_specs().

    Returns:
        Formatted multi-line string.
    """
    lines: list[str] = []

    module = diff_result.get("module", "unknown")
    old_ver = diff_result.get("old_version", "?")
    new_ver = diff_result.get("new_version", "?")
    lines.append(f"{module} {old_ver} -> {new_ver}")

    changes = diff_result.get("changes", {})
    models = changes.get("models", {})

    # Added models
    for model in models.get("added", []):
        lines.append(f"  + {model['name']} (NEW MODEL)")
        for field in model.get("fields", []):
            fname = field.get("name", field) if isinstance(field, dict) else field
            ftype = field.get("type", "") if isinstance(field, dict) else ""
            lines.append(f"      + {fname} ({ftype})" if ftype else f"      + {fname}")

    # Removed models
    for model in models.get("removed", []):
        lines.append(f"  - {model['name']} (REMOVED) -- DESTRUCTIVE")

    # Modified models
    for name, model_data in models.get("modified", {}).items():
        lines.append(f"  ~ {name}:")
        fields = model_data.get("fields", {})

        for field in fields.get("added", []):
            ftype = field.get("type", "")
            lines.append(f"      + {field['name']} ({ftype})" if ftype else f"      + {field['name']}")

        for field in fields.get("removed", []):
            ftype = field.get("type", "Unknown")
            lines.append(f"      - {field['name']} ({ftype}) -- DESTRUCTIVE")

        for field in fields.get("modified", []):
            change_parts: list[str] = []
            for attr, vals in field.get("changes", {}).items():
                if isinstance(vals, dict) and "old" in vals and "new" in vals:
                    change_parts.append(f"{attr}: {vals['old']} -> {vals['new']}")

            change_str = ", ".join(change_parts)
            if field.get("destructive"):
                lines.append(f"      ! {field['name']}: {change_str} -- DESTRUCTIVE")
            else:
                lines.append(f"      ~ {field['name']}: {change_str}")

        # Security changes
        security = model_data.get("security", {})
        if security:
            for role in security.get("roles_added", []):
                lines.append(f"    SECURITY: + role: {role}")
            for role in security.get("roles_removed", []):
                lines.append(f"    SECURITY: - role: {role}")

        # Approval changes
        approval = model_data.get("approval", {})
        if approval:
            for level in approval.get("levels_added", []):
                lines.append(f"    APPROVAL: + level: {level}")
            for level in approval.get("levels_removed", []):
                lines.append(f"    APPROVAL: - level: {level}")

        # Webhook changes
        webhooks = model_data.get("webhooks", {})
        if webhooks:
            wf = webhooks.get("watched_fields", {})
            for field_name in wf.get("added", []):
                lines.append(f"    WEBHOOKS: + watched: {field_name}")
            for field_name in wf.get("removed", []):
                lines.append(f"    WEBHOOKS: - watched: {field_name}")

        # Constraint changes
        constraints = model_data.get("constraints", {})
        if constraints:
            for c in constraints.get("added", []):
                cname = c["name"] if isinstance(c, dict) else c
                lines.append(f"    CONSTRAINTS: + {cname}")
            for c in constraints.get("removed", []):
                cname = c["name"] if isinstance(c, dict) else c
                lines.append(f"    CONSTRAINTS: - {cname}")

    # Cron job changes
    cron = changes.get("cron_jobs", {})
    if cron:
        lines.append("CRON JOBS:")
        for item in cron.get("added", []):
            lines.append(f"  + {item['name']}")
        for item in cron.get("removed", []):
            lines.append(f"  - {item['name']}")
        for name, cron_changes in cron.get("modified", {}).items():
            parts = []
            for attr, vals in cron_changes.items():
                if isinstance(vals, dict) and "old" in vals and "new" in vals:
                    parts.append(f"{attr}: {vals['old']} -> {vals['new']}")
            lines.append(f"  ~ {name}: {', '.join(parts)}")

    # Report changes
    reports = changes.get("reports", {})
    if reports:
        lines.append("REPORTS:")
        for item in reports.get("added", []):
            lines.append(f"  + {item['name']}")
        for item in reports.get("removed", []):
            lines.append(f"  - {item['name']}")

    # Warning footer
    destructive_count = diff_result.get("destructive_count", 0)
    if destructive_count > 0:
        lines.append(
            f"-- {destructive_count} destructive change{'s' if destructive_count != 1 else ''}"
            " -- review migration script carefully"
        )

    return "\n".join(lines)
