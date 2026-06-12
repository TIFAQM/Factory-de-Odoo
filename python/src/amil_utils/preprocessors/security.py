"""Security pattern processing (RBAC, ACL, record rules, field groups)."""

from __future__ import annotations

import logging
from typing import Any

from amil_utils.preprocessors._registry import register_preprocessor
from amil_utils.renderer_utils import _to_python_var

logger = logging.getLogger(__name__)


def _parse_crud(crud_str: str) -> dict[str, int]:
    """Convert a CRUD string like 'cru' to permission dict.

    Characters: c=create, r=read, u=write/update, d=delete/unlink.
    Normalizes to lowercase. Raises ValueError on invalid characters.

    Returns:
        Dict with perm_create, perm_read, perm_write, perm_unlink as 0/1.
    """
    normalized = crud_str.lower()
    valid_chars = set("crud")
    invalid = set(normalized) - valid_chars
    if invalid:
        msg = f"CRUD string '{crud_str}' contains invalid characters: {sorted(invalid)}"
        raise ValueError(msg)
    return {
        "perm_create": int("c" in normalized),
        "perm_read": int("r" in normalized),
        "perm_write": int("u" in normalized),
        "perm_unlink": int("d" in normalized),
    }


def _normalize_crud_value(value):
    """Accept {create,read,write,unlink: bool} dicts as CRUD strings.

    Spec agents naturally produce the boolean-dict shape; normalize instead
    of crashing with AttributeError deep in _parse_crud.
    """
    if isinstance(value, dict):
        letters = [("c", "create"), ("r", "read"), ("u", "write"), ("d", "unlink")]
        return "".join(l for l, k in letters if value.get(k))
    return value


def _normalize_security_cruds(security: dict[str, Any]) -> dict[str, Any]:
    """Return a security block with all CRUD values normalized to strings."""
    out = {**security}
    if isinstance(out.get("defaults"), dict):
        out["defaults"] = {k: _normalize_crud_value(v) for k, v in out["defaults"].items()}
    _PERM_KEYS = {"create", "read", "write", "unlink"}
    if isinstance(out.get("acl"), dict):
        normalized_acl = {}
        for mk, mv in out["acl"].items():
            if isinstance(mv, dict) and set(mv) <= _PERM_KEYS:
                # shallow agent shape {role: {perm: bool}} — collapse to string
                normalized_acl[mk] = _normalize_crud_value(mv)
            elif isinstance(mv, dict):
                normalized_acl[mk] = {
                    rk: _normalize_crud_value(rv) for rk, rv in mv.items()
                }
            else:
                normalized_acl[mk] = mv
        # if every value is now a plain CRUD string keyed by a ROLE name,
        # it's role-level overrides, not per-model — fold into defaults
        role_names = set(out.get("roles", []))
        if normalized_acl and set(normalized_acl) <= role_names and all(
            isinstance(v, str) for v in normalized_acl.values()
        ):
            merged = {**out.get("defaults", {}), **normalized_acl}
            out["defaults"] = merged
            out["acl"] = {}
        else:
            out["acl"] = normalized_acl
    return out


def _security_validate_spec(security: dict[str, Any]) -> None:
    """Validate that defaults keys exactly match roles array.

    Also validates CRUD strings contain only c, r, u, d.
    Raises ValueError on mismatch.
    """
    roles_set = set(security.get("roles", []))
    defaults_keys = set(security.get("defaults", {}).keys())
    if roles_set != defaults_keys:
        missing = roles_set - defaults_keys
        extra = defaults_keys - roles_set
        parts = []
        if missing:
            parts.append(f"missing from defaults: {sorted(missing)}")
        if extra:
            parts.append(f"extra in defaults: {sorted(extra)}")
        msg = f"Security defaults keys must match roles array. {'; '.join(parts)}"
        raise ValueError(msg)
    # Validate CRUD strings
    for role, crud in security.get("defaults", {}).items():
        _parse_crud(crud)
    # Validate per-model ACL overrides
    for model_name, acl in security.get("acl", {}).items():
        for role, crud in acl.items():
            _parse_crud(crud)


def _security_build_roles(
    module_name: str, security: dict[str, Any]
) -> list[dict[str, Any]]:
    """Build list of role dicts with xml_id, implied_ids chain, labels.

    Roles ordered lowest-to-highest per security['roles'] array order.
    Lowest role implies base.group_user; each subsequent role implies the previous.
    """
    roles_list = security["roles"]
    result: list[dict[str, Any]] = []
    for i, role_name in enumerate(roles_list):
        if i == 0:
            implied_ids = "base.group_user"
        else:
            prev_role = roles_list[i - 1]
            implied_ids = f"group_{module_name}_{prev_role}"
        is_highest = i == len(roles_list) - 1
        crud = security.get("defaults", {}).get(role_name, "")
        result.append({
            "name": role_name,
            "label": role_name.replace("_", " ").title(),
            "xml_id": f"group_{module_name}_{role_name}",
            "implied_ids": implied_ids,
            "is_highest": is_highest,
            "crud": crud,
            "can_create": "c" in crud,
        })
    return result


def _security_build_acl_matrix(
    spec: dict[str, Any], security: dict[str, Any]
) -> list[dict[str, Any]]:
    """Build security_acl list on each model from defaults with per-model overrides.

    Returns new model list with security_acl injected on each model.
    """
    defaults = security.get("defaults", {})
    acl_overrides = security.get("acl", {})
    roles = security["roles"]

    new_models = []
    for model in spec.get("models", []):
        model_acl_override = acl_overrides.get(model["name"], {})
        acl_entries = []
        for role in roles:
            crud_str = model_acl_override.get(role, defaults.get(role, ""))
            perms = _parse_crud(crud_str)
            acl_entries.append({
                "role": role,
                **perms,
            })
        new_models.append({**model, "security_acl": acl_entries})
    return new_models


def _security_detect_record_rule_scopes(model: dict[str, Any]) -> list[str]:
    """Auto-detect record rule scopes from model fields.

    Scopes:
    - 'ownership': if field named user_id (Many2one) or create_uid exists
    - 'department': if field named department_id exists
    - 'company': if field named company_id (Many2one) exists

    If model has 'record_rules' key with a list of strings, use that (override).
    If model has 'record_rules' key with a list of dicts, those are custom rules
    handled separately -- still auto-detect scopes.
    """
    rr = model.get("record_rules")
    if rr is not None:
        # Explicit empty list = "no record rules" (disables auto-detection)
        if not rr:
            return []
        # If it's a list of strings, it's scope overrides
        if isinstance(rr[0], str):
            return list(rr)
        # If it's a list of dicts, those are custom rules (handled in template);
        # still auto-detect standard scopes

    fields = model.get("fields", [])
    scopes: list[str] = []

    has_user_id = any(
        f.get("name") == "user_id" and f.get("type") == "Many2one"
        for f in fields
    )
    has_create_uid = any(f.get("name") == "create_uid" for f in fields)
    if has_user_id or has_create_uid:
        scopes.append("ownership")

    if any(f.get("name") == "department_id" for f in fields):
        scopes.append("department")

    if any(
        f.get("name") == "company_id" and f.get("type") == "Many2one"
        for f in fields
    ):
        scopes.append("company")

    return scopes


def _security_build_record_rule_bindings(
    spec: dict[str, Any],
    roles: list[dict[str, Any]],
    module_name: str,
) -> dict[str, Any]:
    """Build record_rule_bindings on each model for template rendering.

    For each scope, determines which role's group to bind the rule to:
    - 'ownership'/'department' -> lowest role (index 0)
    - 'company' -> global (no group binding)
    - 'approval' -> handled by approval preprocessor (pass-through)
    - Custom rules from model's record_rules list of dicts -> resolved

    Also processes custom record rules defined as dicts on models.

    Pure function.
    """
    lowest_role = roles[0] if roles else None
    highest_role = next((r for r in roles if r.get("is_highest")), roles[-1] if roles else None)
    role_lookup = {r["name"]: r for r in roles}

    new_models = []
    for model in spec.get("models", []):
        bindings: dict[str, str] = {}
        for scope in model.get("record_rule_scopes", []):
            if scope in ("ownership", "department"):
                if lowest_role:
                    bindings[scope] = lowest_role["xml_id"]
            elif scope == "company":
                bindings[scope] = "__global__"
            # 'approval' scope is handled by approval preprocessor

        enriched = {**model, "record_rule_bindings": bindings}

        # Process custom record rules (list of dicts on model)
        raw_rules = model.get("record_rules")
        if raw_rules and isinstance(raw_rules, list) and raw_rules and isinstance(raw_rules[0], dict):
            custom_rules = []
            for rule in raw_rules:
                resolved = {**rule}
                # Resolve group reference
                group_ref = rule.get("group")
                if group_ref and group_ref in role_lookup:
                    resolved["group_xml_id"] = f"{module_name}.{role_lookup[group_ref]['xml_id']}"
                elif group_ref and "." in group_ref:
                    resolved["group_xml_id"] = group_ref
                elif group_ref:
                    resolved["group_xml_id"] = f"{module_name}.group_{module_name}_{group_ref}"
                # Generate xml_id if not provided
                if "xml_id" not in resolved:
                    model_var = _to_python_var(model["name"])
                    rule_name = _to_python_var(rule.get("name", "custom"))
                    resolved["xml_id"] = f"rule_{model_var}_{rule_name}"
                custom_rules.append(resolved)
            enriched["custom_record_rules"] = custom_rules

        new_models.append(enriched)

    return {**spec, "models": new_models}


def _resolve_group_ref(
    raw: str, module_name: str, role_names: set[str]
) -> str:
    """Resolve a single group reference to a full external ID.

    Handles:
    - Full external IDs (containing '.') -- returned as-is
    - Bare role names ('manager') -- resolved to module.group_module_role
    - Unknown names -- returned as-is (template will render it literally)
    """
    if "." in raw:
        return raw
    if raw in role_names:
        return f"{module_name}.group_{module_name}_{raw}"
    return raw


def _resolve_groups_value(
    groups_val: str, module_name: str, role_names: set[str]
) -> str:
    """Resolve a comma-separated groups value, handling each ref individually."""
    parts = [p.strip() for p in groups_val.split(",") if p.strip()]
    resolved = [_resolve_group_ref(p, module_name, role_names) for p in parts]
    return ",".join(resolved)


def _security_enrich_fields(
    spec: dict[str, Any],
    roles: list[dict[str, Any]],
) -> dict[str, Any]:
    """Enrich model fields with groups= attribute for sensitive/restricted fields.

    Resolution order for each field:
    1. Explicit ``field_security`` mapping on the model (field_name -> role/ref)
    2. ``groups`` value already on the field (bare role names resolved)
    3. ``sensitive: true`` without groups -> defaults to highest role group
    4. Comma-separated groups (e.g. 'manager,auditor') -> each part resolved

    Pure function -- does NOT mutate the input spec.
    """
    module_name = spec["module_name"]
    role_names = {r["name"] for r in roles}
    highest_role = next((r for r in roles if r.get("is_highest")), roles[-1] if roles else None)

    new_models = []
    for model in spec.get("models", []):
        # Per-model field_security overrides: {field_name: "role_or_ref"}
        field_security = model.get("field_security", {})

        new_fields = []
        for field in model.get("fields", []):
            enriched = {**field}
            fname = field.get("name", "")

            # Priority 1: explicit field_security mapping
            if fname in field_security:
                enriched["groups"] = _resolve_groups_value(
                    field_security[fname], module_name, role_names
                )
            elif field.get("groups"):
                # Priority 2: resolve existing groups value
                enriched["groups"] = _resolve_groups_value(
                    field["groups"], module_name, role_names
                )
            elif field.get("sensitive") and not field.get("groups"):
                # Priority 3: sensitive fields default to highest role
                if highest_role:
                    enriched["groups"] = f"{module_name}.{highest_role['xml_id']}"

            new_fields.append(enriched)
        new_models.append({**model, "fields": new_fields})

    return {**spec, "models": new_models}


def _security_auto_fix_views(spec: dict[str, Any]) -> dict[str, Any]:
    """Auto-fix view fields referencing restricted fields by adding view_groups.

    For each model, builds a set of restricted field names (those with 'groups' key),
    then adds 'view_groups' key to each restricted field with the same groups value.
    Logs INFO for each auto-fixed field.

    Also cross-references restricted fields against:
    1. Search view candidates (Char/Many2one/Selection) -- warns if restricted field
       would appear in search view accessible to lower-privilege roles.
    2. Computed field dependencies -- warns if a non-restricted computed field depends
       on a restricted field, exposing the value through the computed chain.

    Pure function -- does NOT mutate the input spec.
    """
    new_models = []
    for model in spec.get("models", []):
        fields = model.get("fields", [])
        model_name = model.get("name", "")

        # Build restricted field lookup: name -> groups value
        restricted = {
            f["name"]: f["groups"]
            for f in fields
            if f.get("groups")
        }

        if not restricted:
            new_models.append(model)
            continue

        # Cross-reference: search view candidates
        search_field_types = ("Char", "Many2one", "Selection")
        for field in fields:
            fname = field.get("name", "")
            if fname in restricted and field.get("type") in search_field_types:
                logger.warning(
                    "Restricted field '%s' (groups='%s') is a %s field that may "
                    "appear in search view for model '%s'. Lower-privilege roles "
                    "will not see this field in search filters.",
                    fname,
                    restricted[fname],
                    field.get("type"),
                    model_name,
                )

        # Cross-reference: computed field dependencies
        for field in fields:
            fname = field.get("name", "")
            deps = field.get("depends", [])
            if fname not in restricted and deps:
                # Check if any dependency is a restricted field
                for dep in deps:
                    dep_base = dep.split(".")[0]  # handle dotted paths
                    if dep_base in restricted:
                        logger.warning(
                            "Computed field '%s' in model '%s' depends on "
                            "restricted field '%s' (groups='%s'). The computed "
                            "value may expose restricted data to lower-privilege roles.",
                            fname,
                            model_name,
                            dep_base,
                            restricted[dep_base],
                        )

        new_fields = []
        for field in fields:
            fname = field.get("name", "")
            if fname in restricted:
                enriched = {**field, "view_groups": restricted[fname]}
                logger.info(
                    "Auto-applied groups='%s' to field '%s' in views for model '%s'",
                    restricted[fname],
                    fname,
                    model_name,
                )
                new_fields.append(enriched)
            else:
                new_fields.append(field)
        new_models.append({**model, "fields": new_fields})

    return {**spec, "models": new_models}


def _inject_legacy_security(spec: dict[str, Any]) -> dict[str, Any]:
    """Inject legacy User/Manager two-tier security when no security block exists.

    Returns new spec with security_roles and enriched models.
    """
    module_name = spec["module_name"]
    legacy_security = {
        "roles": ["user", "manager"],
        "defaults": {
            "user": "cru",
            "manager": "crud",
        },
    }
    roles = _security_build_roles(module_name, legacy_security)
    new_models = _security_build_acl_matrix(spec, legacy_security)

    # Detect record rule scopes for each model
    enriched_models = []
    for model in new_models:
        scopes = _security_detect_record_rule_scopes(model)
        enriched_models.append({**model, "record_rule_scopes": scopes})

    # Phase 52: preserve existing security_roles from domain preprocessors (e.g., document_management)
    existing_roles = list(spec.get("security_roles", []))
    existing_names = {r["name"] for r in existing_roles}
    merged_roles = list(existing_roles)
    for role in roles:
        if role["name"] not in existing_names:
            merged_roles.append(role)

    result = {**spec, "security_roles": merged_roles, "models": enriched_models}
    result = _security_enrich_fields(result, merged_roles)
    result = _security_auto_fix_views(result)
    result = _security_build_record_rule_bindings(result, merged_roles, module_name)
    return result


@register_preprocessor(order=60, name="security_patterns")
def _process_security_patterns(spec: dict[str, Any]) -> dict[str, Any]:
    """Pre-process security section, building RBAC infrastructure.

    If no security block: injects legacy User/Manager two-tier system.
    If security block present: validates, builds role hierarchy, ACL matrix,
    and record rule scopes.

    Returns a new spec dict with:
    - security_roles: list of role dicts with xml_id, implied_ids, etc.
    - models enriched with security_acl and record_rule_scopes

    Pure function -- does NOT mutate the input spec.
    """
    security = spec.get("security")
    if not security:
        return _inject_legacy_security(spec)

    module_name = spec["module_name"]

    # Validate
    security = _normalize_security_cruds(security)
    _security_validate_spec(security)

    # Build roles
    roles = _security_build_roles(module_name, security)

    # Build ACL matrix
    new_models = _security_build_acl_matrix(spec, security)

    # Detect record rule scopes for each model
    enriched_models = []
    for model in new_models:
        scopes = _security_detect_record_rule_scopes(model)
        enriched_models.append({**model, "record_rule_scopes": scopes})

    # Phase 52: preserve existing security_roles from domain preprocessors (e.g., document_management)
    existing_roles = list(spec.get("security_roles", []))
    existing_names = {r["name"] for r in existing_roles}
    merged_roles = list(existing_roles)
    for role in roles:
        if role["name"] not in existing_names:
            merged_roles.append(role)

    result = {**spec, "security_roles": merged_roles, "models": enriched_models}
    result = _security_enrich_fields(result, merged_roles)
    result = _security_auto_fix_views(result)
    result = _security_build_record_rule_bindings(result, merged_roles, module_name)
    return result
