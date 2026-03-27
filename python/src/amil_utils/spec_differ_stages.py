"""Per-stage diff analysis functions for spec differ.

Provides stage-level diff computation for:
- Models (fields, security, approval, webhooks, constraints)
- Cron jobs
- Reports

These functions are consumed by the main diff_specs() entry point
in spec_differ.py.
"""

from __future__ import annotations

import logging
from typing import Any

from amil_utils.spec_differ import (
    EXCLUDED_FIELD_ATTRIBUTES,
    ChangesModels,
    FieldChange,
    ModelChange,
    _classify_destructiveness,
)

logger = logging.getLogger("amil.spec_differ")


# ---------------------------------------------------------------------------
# Field-Level Diffing
# ---------------------------------------------------------------------------

def _selection_changes(old_selection: list, new_selection: list) -> dict:
    """Compute selection option changes.

    Returns dict with 'added' and 'removed' selection options.
    """
    old_keys = {opt[0] if isinstance(opt, list) else opt for opt in old_selection}
    new_keys = {opt[0] if isinstance(opt, list) else opt for opt in new_selection}

    added = sorted(new_keys - old_keys)
    removed = sorted(old_keys - new_keys)
    return {"added": added, "removed": removed}


def _diff_field_attributes(
    old_field: dict, new_field: dict
) -> tuple[dict[str, dict[str, Any]], bool, str]:
    """Compare two field dicts and return changes with destructiveness.

    Returns:
        Tuple of (changes_dict, is_destructive, severity)
        changes_dict maps attribute -> {old, new}
    """
    changes: dict[str, dict[str, Any]] = {}
    max_severity = "non_destructive"

    # Attributes that affect schema/behavior
    schema_attributes = {
        "type", "required", "default", "compute", "store", "related",
        "comodel_name", "inverse_name", "groups", "index", "ondelete",
        "selection",
    }

    all_keys = set(old_field.keys()) | set(new_field.keys())
    for attr in all_keys:
        if attr in EXCLUDED_FIELD_ATTRIBUTES:
            continue
        if attr not in schema_attributes:
            continue

        old_val = old_field.get(attr)
        new_val = new_field.get(attr)

        if old_val == new_val:
            continue

        # Special handling for selection values
        if attr == "selection":
            if old_val is not None and new_val is not None:
                sel_changes = _selection_changes(old_val, new_val)
                if sel_changes["removed"]:
                    severity = _classify_destructiveness(
                        "selection_removed", old_val, new_val, "selection"
                    )
                    changes["selection"] = {
                        "old": old_val,
                        "new": new_val,
                        "options_added": sel_changes["added"],
                        "options_removed": sel_changes["removed"],
                    }
                elif sel_changes["added"]:
                    severity = _classify_destructiveness(
                        "selection_added", old_val, new_val, "selection"
                    )
                    changes["selection"] = {
                        "old": old_val,
                        "new": new_val,
                        "options_added": sel_changes["added"],
                        "options_removed": sel_changes["removed"],
                    }
                else:
                    severity = "non_destructive"
                    continue
            else:
                changes["selection"] = {"old": old_val, "new": new_val}
                severity = "non_destructive"
        elif attr == "type":
            severity = _classify_destructiveness("type", old_val, new_val, "type")
            changes["type"] = {"old": old_val, "new": new_val}
        elif attr == "required":
            severity = _classify_destructiveness("required", old_val, new_val, "required")
            changes["required"] = {"old": old_val, "new": new_val}
        else:
            severity = _classify_destructiveness("attribute", old_val, new_val, attr)
            changes[attr] = {"old": old_val, "new": new_val}

        # Track max severity
        if severity == "always_destructive":
            max_severity = "always_destructive"
        elif severity == "possibly_destructive" and max_severity != "always_destructive":
            max_severity = "possibly_destructive"

    is_destructive = max_severity in ("always_destructive", "possibly_destructive")
    return changes, is_destructive, max_severity


# ---------------------------------------------------------------------------
# Model-Level Diffing
# ---------------------------------------------------------------------------

def _diff_models(old_diffable: dict, new_diffable: dict) -> tuple[ChangesModels, int, list[str]]:
    """Compute hierarchical model changes.

    Returns:
        Tuple of (changes_models, destructive_count, warnings)
    """
    old_models = old_diffable.get("models", {})
    new_models = new_diffable.get("models", {})

    added: list[ModelChange] = []
    removed: list[ModelChange] = []
    modified: dict[str, dict] = {}
    destructive_count = 0
    warnings: list[str] = []

    # Added models
    for name in sorted(set(new_models.keys()) - set(old_models.keys())):
        model_data = new_models[name]
        field_list = [
            {"name": fname, **fdata}
            for fname, fdata in model_data.get("fields", {}).items()
        ]
        added.append({"name": name, "fields": field_list, "destructive": False})

    # Removed models
    for name in sorted(set(old_models.keys()) - set(new_models.keys())):
        model_data = old_models[name]
        field_list = [
            {"name": fname, **fdata}
            for fname, fdata in model_data.get("fields", {}).items()
        ]
        removed.append({"name": name, "fields": field_list, "destructive": True})
        destructive_count += 1
        warnings.append(f"Model '{name}' removed -- ALWAYS DESTRUCTIVE")

    # Modified models
    for name in sorted(set(old_models.keys()) & set(new_models.keys())):
        old_model = old_models[name]
        new_model = new_models[name]

        model_changes: dict = {}
        has_changes = False

        # Compare fields
        old_fields = old_model.get("fields", {})
        new_fields = new_model.get("fields", {})

        added_fields: list[dict] = []
        removed_fields: list[FieldChange] = []
        modified_fields: list[FieldChange] = []

        # Added fields
        for fname in sorted(set(new_fields.keys()) - set(old_fields.keys())):
            fdata = new_fields[fname]
            added_fields.append({"name": fname, **fdata})

        # Removed fields
        for fname in sorted(set(old_fields.keys()) - set(new_fields.keys())):
            fdata = old_fields[fname]
            removed_fields.append({
                "name": fname,
                "type": fdata.get("type", "Unknown"),
                "destructive": True,
                "severity": "always_destructive",
            })
            destructive_count += 1
            warnings.append(f"Field '{name}.{fname}' removed -- ALWAYS DESTRUCTIVE")

        # Modified fields
        for fname in sorted(set(old_fields.keys()) & set(new_fields.keys())):
            changes, is_destructive, severity = _diff_field_attributes(
                old_fields[fname], new_fields[fname]
            )
            if changes:
                entry: FieldChange = {
                    "name": fname,
                    "type": new_fields[fname].get("type", old_fields[fname].get("type", "Unknown")),
                    "changes": changes,
                    "destructive": is_destructive,
                    "severity": severity,
                }
                modified_fields.append(entry)
                if is_destructive:
                    destructive_count += 1
                    label = "ALWAYS" if severity == "always_destructive" else "POSSIBLY"
                    warnings.append(
                        f"Field '{name}.{fname}' modified -- {label} DESTRUCTIVE"
                    )

        if added_fields or removed_fields or modified_fields:
            model_changes["fields"] = {
                "added": added_fields,
                "removed": removed_fields,
                "modified": modified_fields,
            }
            has_changes = True

        # Compare security
        old_security = old_model.get("security", {})
        new_security = new_model.get("security", {})
        if old_security != new_security:
            security_changes = _diff_security(old_security, new_security)
            if security_changes:
                model_changes["security"] = security_changes
                has_changes = True

        # Compare approval
        old_approval = old_model.get("approval", {})
        new_approval = new_model.get("approval", {})
        if old_approval != new_approval:
            approval_changes = _diff_approval(old_approval, new_approval)
            if approval_changes:
                model_changes["approval"] = approval_changes
                has_changes = True

        # Compare webhooks
        old_webhooks = old_model.get("webhooks", {})
        new_webhooks = new_model.get("webhooks", {})
        if old_webhooks != new_webhooks:
            webhook_changes = _diff_webhooks(old_webhooks, new_webhooks)
            if webhook_changes:
                model_changes["webhooks"] = webhook_changes
                has_changes = True

        # Compare constraints
        old_constraints = old_model.get("constraints", {})
        new_constraints = new_model.get("constraints", {})
        if old_constraints != new_constraints:
            constraint_changes = _diff_constraints(old_constraints, new_constraints)
            if constraint_changes:
                model_changes["constraints"] = constraint_changes
                has_changes = True

        if has_changes:
            modified[name] = model_changes

    return {
        "added": added,
        "removed": removed,
        "modified": modified,
    }, destructive_count, warnings


# ---------------------------------------------------------------------------
# Security Diffing
# ---------------------------------------------------------------------------

def _diff_security(old: dict, new: dict) -> dict:
    """Diff security block: roles and ACL changes."""
    result: dict = {}

    old_roles = set(old.get("roles", []))
    new_roles = set(new.get("roles", []))

    added_roles = sorted(new_roles - old_roles)
    removed_roles = sorted(old_roles - new_roles)

    if added_roles:
        result["roles_added"] = added_roles
    if removed_roles:
        result["roles_removed"] = removed_roles

    old_acl = old.get("acl", {})
    new_acl = new.get("acl", {})
    if old_acl != new_acl:
        acl_changes: dict = {}
        for role in sorted(set(old_acl.keys()) | set(new_acl.keys())):
            old_perms = old_acl.get(role)
            new_perms = new_acl.get(role)
            if old_perms != new_perms:
                acl_changes[role] = {"old": old_perms, "new": new_perms}
        if acl_changes:
            result["acl_changed"] = acl_changes

    return result


# ---------------------------------------------------------------------------
# Approval Diffing
# ---------------------------------------------------------------------------

def _diff_approval(old: dict, new: dict) -> dict:
    """Diff approval block: levels added/removed/modified."""
    result: dict = {}

    old_levels = old.get("levels", {})
    new_levels = new.get("levels", {})

    # If levels are still lists (shouldn't be after _spec_to_diffable), handle it
    if isinstance(old_levels, list):
        old_levels = {lv["name"]: {k: v for k, v in lv.items() if k != "name"} for lv in old_levels}
    if isinstance(new_levels, list):
        new_levels = {lv["name"]: {k: v for k, v in lv.items() if k != "name"} for lv in new_levels}

    added = sorted(set(new_levels.keys()) - set(old_levels.keys()))
    removed = sorted(set(old_levels.keys()) - set(new_levels.keys()))

    if added:
        result["levels_added"] = added
    if removed:
        result["levels_removed"] = removed

    # Modified levels
    modified: dict = {}
    for name in sorted(set(old_levels.keys()) & set(new_levels.keys())):
        if old_levels[name] != new_levels[name]:
            modified[name] = {"old": old_levels[name], "new": new_levels[name]}
    if modified:
        result["levels_modified"] = modified

    return result


# ---------------------------------------------------------------------------
# Webhook Diffing
# ---------------------------------------------------------------------------

def _diff_webhooks(old: dict, new: dict) -> dict:
    """Diff webhook block: watched_fields changes."""
    result: dict = {}

    old_watched = set(old.get("watched_fields", []))
    new_watched = set(new.get("watched_fields", []))

    if old_watched != new_watched:
        added = sorted(new_watched - old_watched)
        removed = sorted(old_watched - new_watched)
        result["watched_fields"] = {}
        if added:
            result["watched_fields"]["added"] = added
        if removed:
            result["watched_fields"]["removed"] = removed

    return result


# ---------------------------------------------------------------------------
# Constraint Diffing
# ---------------------------------------------------------------------------

def _diff_constraints(old: dict, new: dict) -> dict:
    """Diff constraints: added/removed/modified."""
    result: dict = {}

    added_names = sorted(set(new.keys()) - set(old.keys()))
    removed_names = sorted(set(old.keys()) - set(new.keys()))

    if added_names:
        result["added"] = [{"name": n, **new[n]} for n in added_names]
    if removed_names:
        result["removed"] = [{"name": n, **old[n]} for n in removed_names]

    # Modified
    modified: dict = {}
    for name in sorted(set(old.keys()) & set(new.keys())):
        if old[name] != new[name]:
            modified[name] = {"old": old[name], "new": new[name]}
    if modified:
        result["modified"] = modified

    return result


# ---------------------------------------------------------------------------
# Cron Job Diffing
# ---------------------------------------------------------------------------

def _diff_cron_jobs(old_diffable: dict, new_diffable: dict) -> dict:
    """Diff cron jobs: added/removed/modified."""
    old_crons = old_diffable.get("cron_jobs", {})
    new_crons = new_diffable.get("cron_jobs", {})
    result: dict = {}

    added = sorted(set(new_crons.keys()) - set(old_crons.keys()))
    removed = sorted(set(old_crons.keys()) - set(new_crons.keys()))

    if added:
        result["added"] = [{"name": n, **new_crons[n]} for n in added]
    if removed:
        result["removed"] = [{"name": n, **old_crons[n]} for n in removed]

    modified: dict = {}
    for name in sorted(set(old_crons.keys()) & set(new_crons.keys())):
        if old_crons[name] != new_crons[name]:
            changes: dict = {}
            for key in set(old_crons[name].keys()) | set(new_crons[name].keys()):
                old_val = old_crons[name].get(key)
                new_val = new_crons[name].get(key)
                if old_val != new_val:
                    changes[key] = {"old": old_val, "new": new_val}
            modified[name] = changes
    if modified:
        result["modified"] = modified

    return result


# ---------------------------------------------------------------------------
# Report Diffing
# ---------------------------------------------------------------------------

def _diff_reports(old_diffable: dict, new_diffable: dict) -> dict:
    """Diff reports: added/removed."""
    old_reports = old_diffable.get("reports", {})
    new_reports = new_diffable.get("reports", {})
    result: dict = {}

    added = sorted(set(new_reports.keys()) - set(old_reports.keys()))
    removed = sorted(set(old_reports.keys()) - set(new_reports.keys()))

    if added:
        result["added"] = [{"name": n, **new_reports[n]} for n in added]
    if removed:
        result["removed"] = [{"name": n, **old_reports[n]} for n in removed]

    return result
