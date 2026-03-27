"""Constraint enrichment for model specs."""

from __future__ import annotations

import ast
import logging
import re
from typing import Any

from amil_utils.preprocessors._registry import register_preprocessor
from amil_utils.utils.copy import deep_copy_model, merge_override_source
from amil_utils.utils.validate import validate_identifier, validate_message

logger = logging.getLogger(__name__)

# Dangerous AST node types that should never appear in generated constraint code
_DANGEROUS_NODES = (ast.Import, ast.ImportFrom)
# Dangerous function names
_DANGEROUS_CALLS = frozenset({
    "exec", "eval", "compile", "__import__", "globals", "locals",
    "getattr", "setattr", "delattr", "breakpoint",
})
# Dangerous attribute access patterns
_DANGEROUS_ATTRS = frozenset({
    "system", "popen", "run", "call", "check_output", "check_call",
    "Popen",
})


def _validate_generated_code(code: str) -> bool:
    """Validate that generated Python code does not contain dangerous constructs.

    Parses the code as an AST and walks it to reject import statements,
    exec/eval calls, subprocess usage, and os.system calls.

    Returns True if the code is safe, False if dangerous constructs are found.
    """
    if not code or not code.strip():
        return True
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if isinstance(node, _DANGEROUS_NODES):
            return False
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in _DANGEROUS_CALLS:
                return False
            if isinstance(func, ast.Attribute) and func.attr in _DANGEROUS_ATTRS:
                return False
    return True


@register_preprocessor(order=30, name="constraints")
def _process_constraints(spec: dict[str, Any]) -> dict[str, Any]:
    """Enrich model specs with constraint method metadata from constraints section.

    For each constraint:
    1. Classify by type (temporal, cross_model, capacity)
    2. Locate target model in spec
    3. Inject constraint metadata into model dict

    Returns a new spec dict with enriched models. Pure function.
    """
    constraints = spec.get("constraints", [])
    if not constraints:
        return spec

    # Build model name set for validation
    model_names = {m["name"] for m in spec.get("models", [])}

    # Group constraints by model
    model_constraints: dict[str, list[dict[str, Any]]] = {}
    for constraint in constraints:
        model_name = constraint["model"]
        if model_name not in model_names:
            logger.warning(
                "Constraint '%s' references non-existent model '%s' — skipping. "
                "Available models: %s",
                constraint.get("name", "?"),
                model_name,
                sorted(model_names),
            )
            continue
        model_constraints.setdefault(model_name, []).append(constraint)

    if not model_constraints:
        return spec

    # Enrich each constraint with preprocessed metadata
    def _enrich_constraint(c: dict[str, Any]) -> dict[str, Any]:
        enriched = {**c}
        ctype = c["type"]
        if ctype == "temporal":
            # Build check_expr with False guards
            fields = c["fields"]
            for f in fields:
                validate_identifier(f, "temporal constraint field")
            guards = " and ".join(f"rec.{f}" for f in fields)
            condition = c["condition"]
            # Defense-in-depth: reject conditions with dangerous constructs
            _DANGEROUS = ("__import__", "__class__", "__mro__",
                          "exec(", "eval(", "os.system", "subprocess")
            if any(p in condition for p in _DANGEROUS):
                logger.warning(
                    "Temporal constraint condition contains dangerous pattern — "
                    "skipping constraint '%s'", c.get("name", "?"))
                return enriched  # Return unenriched (no check_expr)
            # Prefix field references with rec.
            check_condition = condition
            for field in fields:
                # Replace bare field names with rec.field (word boundary aware)
                check_condition = re.sub(
                    rf"\b{re.escape(field)}\b",
                    f"rec.{field}",
                    check_condition,
                )
            enriched["check_expr"] = f"{guards} and {check_condition}"
        elif ctype == "cross_model":
            # Generate check_body for cross-model validation
            count_domain_field = validate_identifier(
                c["count_domain_field"], "cross_model count_domain_field",
            )
            capacity_model = validate_identifier(
                c["capacity_model"], "cross_model capacity_model",
            )
            capacity_field = validate_identifier(
                c["capacity_field"], "cross_model capacity_field",
            )
            related_model = validate_identifier(
                c["related_model"], "cross_model related_model",
            )
            message = validate_message(c["message"], "cross_model message")
            enriched["check_body"] = (
                f"course = rec.{count_domain_field}\n"
                f"count = self.env[\"{related_model}\"].search_count([\n"
                f"    (\"{count_domain_field}\", \"=\", course.id),\n"
                f"])\n"
                f"if course.{capacity_field} and count > course.{capacity_field}:\n"
                f"    raise ValidationError(\n"
                f"        _(\"{message}\",\n"
                f"          course.{capacity_field})\n"
                f"    )"
            )
            enriched["write_trigger_fields"] = c.get("trigger_fields", [])
        elif ctype == "capacity":
            # Generate check_body for capacity validation
            count_model = validate_identifier(
                c.get("count_model", ""), "capacity count_model",
            ) if c.get("count_model") else ""
            count_domain_field = validate_identifier(
                c.get("count_domain_field", ""), "capacity count_domain_field",
            ) if c.get("count_domain_field") else ""
            max_value = c.get("max_value")
            max_field = c.get("max_field")
            message = validate_message(c["message"], "capacity message")
            if max_field:
                validate_identifier(max_field, "capacity max_field")
                max_ref = f"rec.{max_field}"
            else:
                max_ref = str(max_value)
            enriched["check_body"] = (
                f"count = self.env[\"{count_model}\"].search_count([\n"
                f"    (\"{count_domain_field}\", \"=\", rec.id),\n"
                f"])\n"
                f"if count > {max_ref}:\n"
                f"    raise ValidationError(\n"
                f"        _(\"{message}\",\n"
                f"          {max_ref})\n"
                f"    )"
            )
            enriched["write_trigger_fields"] = c.get("trigger_fields", [])
        return enriched

    # Deep-copy models and enrich with constraint metadata
    new_models = []
    for model in spec.get("models", []):
        mc = model_constraints.get(model["name"])
        if not mc:
            new_models.append(model)
            continue

        enriched_constraints = []
        for c in mc:
            enriched = _enrich_constraint(c)
            code_to_validate = enriched.get("check_expr") or enriched.get("check_body", "")
            if code_to_validate and not _validate_generated_code(code_to_validate):
                logger.warning(
                    "Constraint '%s' contains dangerous code constructs — skipping.",
                    c.get("name", "unknown"),
                )
                continue
            enriched_constraints.append(enriched)

        # DEBT-09: Compute override constraints once with applies_to field,
        # instead of two identical lists. Keep backward-compatible keys.
        override_constraints = [
            {**c, "applies_to": c.get("applies_to", ["create", "write"])}
            for c in enriched_constraints
            if c["type"] in ("cross_model", "capacity")
        ]

        new_model = deep_copy_model(model)
        new_model.update({
            "complex_constraints": enriched_constraints,
            "override_constraints": override_constraints,
            # Backward-compatible keys (both reference same constraints)
            "create_constraints": override_constraints,
            "write_constraints": override_constraints,
            "has_create_override": bool(override_constraints),
            "has_write_override": bool(override_constraints),
        })

        # Override flag migration: use set[str] via override_sources
        if override_constraints:
            merge_override_source(new_model, "create", "constraints")
            merge_override_source(new_model, "write", "constraints")

        new_models.append(new_model)

    return {**spec, "models": new_models}
