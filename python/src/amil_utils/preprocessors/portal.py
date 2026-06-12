"""Portal preprocessor: enriches spec with portal rendering context.

Registered at order=95 (after notifications@90, before webhooks@100).
Sets has_portal, portal_pages, portal_auth, portal_page_models, and
auto-adds "portal" to depends.
"""

from __future__ import annotations

import re
from typing import Any

from amil_utils.preprocessors._registry import register_preprocessor
from amil_utils.renderer_utils import _to_class, _to_python_var


def _singular(word: str) -> str:
    """Naive singularization: strip trailing 's' or 'es'."""
    if word.endswith("ies"):
        return word[:-3] + "y"
    if word.endswith("ses") or word.endswith("xes") or word.endswith("zes"):
        return word[:-2]
    if word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _derive_names_from_route(route: str) -> tuple[str, str]:
    """Derive plural_name and singular_name from a route path.

    Example: "/my/enrollments" -> ("enrollments", "enrollment")
    Example: "/my/profile" -> ("profile", "profile")
    """
    # Extract the last non-empty path segment
    parts = [p for p in route.strip("/").split("/") if p and not p.startswith("<")]
    if not parts:
        return ("page", "page")
    last = parts[-1]
    # Remove any angle-bracket parameter segments
    last = re.sub(r"<[^>]+>", "", last).strip("/")
    if not last:
        last = parts[-2] if len(parts) >= 2 else "page"
    plural = last
    singular = _singular(plural)
    return (plural, singular)


def _enrich_page(page: dict[str, Any]) -> dict[str, Any]:
    """Enrich a single portal page dict with computed metadata."""
    model = page.get("model", "")
    route = page.get("route", "")
    plural_name, singular_name = _derive_names_from_route(route)

    return {
        **page,
        "model_var": _to_python_var(model),
        "model_class": _to_class(model),
        "singular_name": singular_name,
        "plural_name": plural_name,
    }


@register_preprocessor(order=95, name="portal")
def _process_portal(spec: dict[str, Any]) -> dict[str, Any]:
    """Enrich spec with portal rendering context.

    If no ``portal`` key in spec, returns spec unchanged.
    Otherwise sets:
    - has_portal: True
    - portal_pages: enriched page dicts with model_var, model_class, etc.
    - portal_auth: auth strategy from portal section (default "portal")
    - portal_page_models: sorted unique model names from all pages
    - depends: updated with "portal" if not already present
    """
    portal = spec.get("portal")
    if not portal:
        return spec

    # Handle both dict and Pydantic model
    if hasattr(portal, "model_dump"):
        portal_dict = portal.model_dump()
    elif isinstance(portal, dict):
        portal_dict = portal
    else:
        return spec

    pages = portal_dict.get("pages", [])
    if not pages:
        # An empty portal block (or one Pydantic filled with defaults) means
        # "no portal" — don't emit portal_rules/views over zero pages.
        return spec
    enriched_pages = [_enrich_page(p) for p in pages]

    portal_auth = portal_dict.get("auth", "portal")

    # Compute unique sorted model names
    portal_page_models = sorted({p.get("model", "") for p in pages if p.get("model")})

    # Auto-add "portal" to depends (immutable -- new list)
    old_depends = spec.get("depends", ["base"])
    if "portal" in old_depends:
        new_depends = list(old_depends)
    else:
        new_depends = [*old_depends, "portal"]

    return {
        **spec,
        "has_portal": True,
        "portal_pages": enriched_pages,
        "portal_auth": portal_auth,
        "portal_page_models": portal_page_models,
        "depends": new_depends,
    }
