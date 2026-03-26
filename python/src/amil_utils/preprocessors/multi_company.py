"""Multi-company field injection preprocessor.

When ``multi_company: true`` is set on the module spec, this preprocessor
auto-injects a ``company_id`` Many2one field into every non-transient model
that does not already declare one.

Runs at order=7 so that:
- defaults (order=5) has already injected ``active``
- security (order=60) will later detect ``company_id`` and auto-add the
  ``"company"`` record rule scope

No template changes are needed -- the existing model.py.j2 renders
Many2one fields normally, and the security preprocessor + record_rules.xml.j2
template handle the company record rule.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from amil_utils.preprocessors._registry import register_preprocessor


def _has_company_id(fields: list[dict[str, Any]]) -> bool:
    """Check whether a field list already contains a company_id Many2one."""
    return any(
        f.get("name") == "company_id" and f.get("type") == "Many2one"
        for f in fields
    )


def _make_company_id_field() -> dict[str, Any]:
    """Create the canonical company_id field definition."""
    return {
        "name": "company_id",
        "type": "Many2one",
        "comodel_name": "res.company",
        "string": "Company",
        "required": True,
        "index": True,
        "default": "lambda self: self.env.company",
    }


@register_preprocessor(order=7, name="multi_company")
def inject_multi_company_fields(spec: dict[str, Any]) -> dict[str, Any]:
    """Inject ``company_id`` into all non-transient models when multi_company is True.

    Pure function -- returns a new spec dict without mutating the input.
    """
    if not spec.get("multi_company"):
        return spec

    spec = deepcopy(spec)
    for model in spec.get("models", []):
        # Skip transient models (wizards)
        if model.get("transient") or model.get("is_transient"):
            continue
        fields = model.get("fields", [])
        if _has_company_id(fields):
            continue
        model.setdefault("fields", []).append(_make_company_id_field())
    return spec
