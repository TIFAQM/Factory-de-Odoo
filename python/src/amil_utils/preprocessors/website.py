"""Website preprocessor: enriches spec with public website rendering context.

Registered at order=96 (after portal@95, before webhooks@100).
Sets has_website, website_pages, website_auth, website_page_models, and
auto-adds "website" to depends.
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


def _derive_names_from_url(url: str) -> tuple[str, str]:
    """Derive plural_name and singular_name from a URL path.

    Example: "/events" -> ("events", "event")
    Example: "/about" -> ("about", "about")
    """
    parts = [p for p in url.strip("/").split("/") if p and not p.startswith("<")]
    if not parts:
        return ("page", "page")
    last = parts[-1]
    last = re.sub(r"<[^>]+>", "", last).strip("/")
    if not last:
        last = parts[-2] if len(parts) >= 2 else "page"
    plural = last
    singular = _singular(plural)
    return (plural, singular)


def _enrich_page(page: dict[str, Any]) -> dict[str, Any]:
    """Enrich a single website page dict with computed metadata."""
    model = page.get("model", "")
    url = page.get("url", "")
    plural_name, singular_name = _derive_names_from_url(url)

    enriched: dict[str, Any] = {
        **page,
        "singular_name": singular_name,
        "plural_name": plural_name,
        "fields_visible": page.get("fields_visible", []),
    }

    if model:
        enriched["model_var"] = _to_python_var(model)
        enriched["model_class"] = _to_class(model)

    return enriched


@register_preprocessor(order=96, name="website")
def _process_website(spec: dict[str, Any]) -> dict[str, Any]:
    """Enrich spec with website rendering context.

    If no ``website_pages`` key in spec, returns spec unchanged.
    Otherwise sets:
    - has_website: True
    - website_pages: enriched page dicts with model_var, model_class, etc.
    - website_auth: auth strategy from website section (default "public")
    - website_page_models: sorted unique model names from all pages
    - depends: updated with "website" if not already present
    """
    website = spec.get("website_pages")
    if not website:
        return spec

    # Handle both dict and Pydantic model
    if hasattr(website, "model_dump"):
        website_dict = website.model_dump()
    elif isinstance(website, dict):
        website_dict = website
    else:
        return spec

    pages = website_dict.get("pages", [])
    enriched_pages = [_enrich_page(p) for p in pages]

    website_auth = website_dict.get("default_auth", "public")

    # Compute unique sorted model names
    website_page_models = sorted(
        {p.get("model", "") for p in pages if p.get("model")}
    )

    # Auto-add "website" to depends (immutable -- new list)
    old_depends = spec.get("depends", ["base"])
    if "website" in old_depends:
        new_depends = list(old_depends)
    else:
        new_depends = [*old_depends, "website"]

    return {
        **spec,
        "has_website": True,
        "website_pages": enriched_pages,
        "website_auth": website_auth,
        "website_page_models": website_page_models,
        "depends": new_depends,
    }
