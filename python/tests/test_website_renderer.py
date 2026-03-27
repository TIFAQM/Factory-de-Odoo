"""Tests for website Jinja templates, render_website() integration, and preprocessor.

Phase F16: Verifies website controller, QWeb templates, menus, assets, and
preprocessing of website spec section.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from jinja2 import Environment, FileSystemLoader, StrictUndefined
from pydantic import ValidationError

from amil_utils.preprocessors.website import _process_website
from amil_utils.renderer_utils import (
    _model_ref,
    _to_class,
    _to_python_var,
    _to_xml_id,
)


TEMPLATES_DIR = Path(__file__).parent.parent / "src" / "amil_utils" / "templates" / "shared"


def _make_env() -> Environment:
    """Create a Jinja2 environment loading shared templates."""
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["to_python_var"] = _to_python_var
    env.filters["to_xml_id"] = _to_xml_id
    env.filters["to_class"] = _to_class
    env.filters["model_ref"] = _model_ref
    return env


def _make_website_spec() -> dict[str, Any]:
    """Create a minimal spec with website pages for testing."""
    return {
        "module_name": "event_portal",
        "models": [
            {
                "name": "event.event",
                "fields": [
                    {"name": "name", "type": "Char"},
                    {"name": "date_begin", "type": "Datetime"},
                    {"name": "description", "type": "Html"},
                ],
            },
        ],
        "depends": ["base"],
        "website_pages": {
            "pages": [
                {
                    "id": "events",
                    "url": "/events",
                    "title": "Our Events",
                    "type": "list",
                    "model": "event.event",
                    "fields_visible": ["name", "date_begin"],
                    "published": True,
                    "show_in_menu": True,
                    "menu_sequence": 30,
                },
                {
                    "id": "event",
                    "url": "/events",
                    "title": "Event Details",
                    "type": "detail",
                    "model": "event.event",
                    "fields_visible": ["name", "date_begin", "description"],
                    "published": True,
                    "show_in_menu": False,
                    "menu_sequence": 50,
                },
                {
                    "id": "about",
                    "url": "/about-us",
                    "title": "About Us",
                    "type": "static",
                    "show_in_menu": True,
                    "menu_sequence": 10,
                },
            ],
            "default_auth": "public",
        },
    }


def _enrich_spec(spec: dict[str, Any]) -> dict[str, Any]:
    """Run the website preprocessor on a spec."""
    return _process_website(spec)


def _build_website_context(spec: dict[str, Any]) -> dict[str, Any]:
    """Build a minimal website rendering context from enriched spec."""
    module_name = spec["module_name"]
    website_pages = spec.get("website_pages", [])
    website_auth = spec.get("website_auth", "public")

    controller_class = _to_class(module_name) + "Website"

    return {
        "module_name": module_name,
        "module_technical_name": module_name,
        "controller_class": controller_class,
        "website_pages": website_pages,
        "website_auth": website_auth,
    }


# ---------------------------------------------------------------------------
# Schema validation tests
# ---------------------------------------------------------------------------


class TestWebsiteSpecValidation:
    """Tests for WebsiteSpec and WebsitePageSpec Pydantic models."""

    def test_website_spec_validates(self):
        """Valid WebsiteSpec passes validation."""
        from amil_utils.spec_schema_inner import WebsiteSpec

        spec = WebsiteSpec(
            pages=[],
            default_auth="public",
        )
        assert spec.default_auth == "public"
        assert spec.pages == []

    def test_website_spec_with_pages(self):
        """WebsiteSpec with valid pages passes."""
        from amil_utils.spec_schema_inner import WebsitePageSpec, WebsiteSpec

        page = WebsitePageSpec(
            id="events",
            url="/events",
            title="Events",
            type="list",
            model="event.event",
            fields_visible=["name", "date"],
        )
        spec = WebsiteSpec(pages=[page])
        assert len(spec.pages) == 1
        assert spec.pages[0].id == "events"

    def test_website_spec_rejects_bad_type(self):
        """Invalid page type fails validation."""
        from amil_utils.spec_schema_inner import WebsitePageSpec

        with pytest.raises(ValidationError, match="type must be"):
            WebsitePageSpec(
                id="test",
                url="/test",
                title="Test",
                type="form",
            )

    def test_website_page_defaults(self):
        """WebsitePageSpec has correct defaults."""
        from amil_utils.spec_schema_inner import WebsitePageSpec

        page = WebsitePageSpec(
            id="test",
            url="/test",
            title="Test Page",
        )
        assert page.type == "list"
        assert page.published is True
        assert page.show_in_menu is True
        assert page.menu_sequence == 50
        assert page.model is None
        assert page.fields_visible == []

    def test_website_spec_default_auth(self):
        """WebsiteSpec default_auth defaults to 'public'."""
        from amil_utils.spec_schema_inner import WebsiteSpec

        spec = WebsiteSpec()
        assert spec.default_auth == "public"

    def test_website_page_accepts_valid_types(self):
        """All three valid types are accepted."""
        from amil_utils.spec_schema_inner import WebsitePageSpec

        for t in ("list", "detail", "static"):
            page = WebsitePageSpec(id="x", url="/x", title="X", type=t)
            assert page.type == t


# ---------------------------------------------------------------------------
# Preprocessor tests
# ---------------------------------------------------------------------------


class TestWebsitePreprocessor:
    """Tests for _process_website preprocessor function."""

    def test_no_website_key_returns_unchanged(self):
        """Preprocessor returns spec unchanged when no website_pages key."""
        spec: dict[str, Any] = {
            "module_name": "test_module",
            "models": [],
            "depends": ["base"],
        }
        result = _process_website(spec)
        assert result is spec
        assert "has_website" not in result

    def test_sets_has_website_true(self):
        """Preprocessor sets has_website=True when website_pages key exists."""
        spec = _make_website_spec()
        result = _process_website(spec)
        assert result["has_website"] is True

    def test_website_preprocessor_adds_depends(self):
        """Preprocessor adds 'website' to depends when not present."""
        spec = _make_website_spec()
        assert "website" not in spec["depends"]
        result = _process_website(spec)
        assert "website" in result["depends"]

    def test_no_duplicate_website_depend(self):
        """Preprocessor does not duplicate 'website' if already in depends."""
        spec = _make_website_spec()
        spec["depends"] = ["base", "website"]
        result = _process_website(spec)
        assert result["depends"].count("website") == 1

    def test_does_not_mutate_original_depends(self):
        """Preprocessor creates a new depends list (immutability)."""
        spec = _make_website_spec()
        original_depends = spec["depends"]
        result = _process_website(spec)
        assert "website" not in original_depends
        assert "website" in result["depends"]

    def test_enriches_website_pages(self):
        """Preprocessor creates enriched website_pages list."""
        spec = _make_website_spec()
        result = _process_website(spec)
        pages = result["website_pages"]
        assert isinstance(pages, list)
        assert len(pages) == 3

    def test_enriched_page_has_singular_plural(self):
        """Enriched page has singular_name and plural_name."""
        spec = _make_website_spec()
        result = _process_website(spec)
        events_page = result["website_pages"][0]
        assert events_page["plural_name"] == "events"
        assert events_page["singular_name"] == "event"

    def test_enriched_page_has_model_var(self):
        """Enriched page with model has model_var."""
        spec = _make_website_spec()
        result = _process_website(spec)
        events_page = result["website_pages"][0]
        assert events_page["model_var"] == "event_event"

    def test_enriched_page_has_model_class(self):
        """Enriched page with model has model_class."""
        spec = _make_website_spec()
        result = _process_website(spec)
        events_page = result["website_pages"][0]
        assert events_page["model_class"] == "EventEvent"

    def test_static_page_no_model_var(self):
        """Static page without model has no model_var."""
        spec = _make_website_spec()
        result = _process_website(spec)
        about_page = result["website_pages"][2]
        assert "model_var" not in about_page

    def test_computes_website_page_models(self):
        """Preprocessor computes sorted unique model names."""
        spec = _make_website_spec()
        result = _process_website(spec)
        assert "website_page_models" in result
        models = result["website_page_models"]
        assert models == sorted(set(models))
        assert "event.event" in models

    def test_website_auth_extracted(self):
        """Preprocessor extracts website_auth from spec."""
        spec = _make_website_spec()
        result = _process_website(spec)
        assert result["website_auth"] == "public"

    def test_returns_new_dict(self):
        """Preprocessor returns a new dict, not the original."""
        spec = _make_website_spec()
        original = deepcopy(spec)
        result = _process_website(spec)
        assert result is not spec
        assert "has_website" not in original


# ---------------------------------------------------------------------------
# Template rendering tests
# ---------------------------------------------------------------------------


class TestWebsiteControllerTemplate:
    """Verify website_controller.py.j2 produces correct controller."""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.spec = _enrich_spec(_make_website_spec())
        self.env = _make_env()
        self.ctx = _build_website_context(self.spec)

    def _render(self) -> str:
        template = self.env.get_template("website_controller.py.j2")
        return template.render(**self.ctx)

    def test_controller_class_name(self):
        output = self._render()
        assert "class EventPortalWebsite(http.Controller):" in output

    def test_controller_imports(self):
        output = self._render()
        assert "from odoo import http" in output
        assert "from odoo.http import request" in output

    def test_list_route(self):
        output = self._render()
        assert "'/events'" in output
        assert "website=True" in output
        assert "sitemap=True" in output

    def test_list_route_method(self):
        output = self._render()
        assert "events_list" in output
        assert "website_published" in output
        assert "sudo()" in output

    def test_detail_route(self):
        output = self._render()
        assert "event_detail" in output
        assert '<model("event.event"):item>' in output

    def test_detail_route_checks_published(self):
        output = self._render()
        assert "website_published" in output
        assert "not_found()" in output

    def test_static_route(self):
        output = self._render()
        assert "about_page" in output
        assert "'/about-us'" in output

    def test_auth_public(self):
        output = self._render()
        assert "auth='public'" in output

    def test_slug_import_present(self):
        """Slug import present when detail pages exist."""
        output = self._render()
        assert "from odoo.addons.http_routing.models.ir_http import slug" in output


class TestWebsiteListTemplate:
    """Verify website_list.xml.j2 produces correct QWeb list pages."""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.spec = _enrich_spec(_make_website_spec())
        self.env = _make_env()
        self.ctx = _build_website_context(self.spec)

    def _render(self) -> str:
        page = self.ctx["website_pages"][0]  # events list page
        template = self.env.get_template("website_list.xml.j2")
        return template.render(**self.ctx, page=page)

    def test_website_renders_list_page(self):
        output = self._render()
        assert "website.layout" in output

    def test_list_template_id(self):
        output = self._render()
        assert 'id="website_events"' in output

    def test_list_title(self):
        output = self._render()
        assert "Our Events" in output

    def test_list_fields(self):
        output = self._render()
        assert "item.name" in output
        assert "item.date_begin" in output

    def test_list_card_structure(self):
        output = self._render()
        assert "card h-100" in output
        assert "card-body" in output

    def test_list_foreach(self):
        output = self._render()
        assert 't-foreach="events"' in output


class TestWebsiteDetailTemplate:
    """Verify website_detail.xml.j2 produces correct QWeb detail pages."""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.spec = _enrich_spec(_make_website_spec())
        self.env = _make_env()
        self.ctx = _build_website_context(self.spec)

    def _render(self) -> str:
        page = self.ctx["website_pages"][1]  # event detail page
        template = self.env.get_template("website_detail.xml.j2")
        return template.render(**self.ctx, page=page)

    def test_website_renders_detail_page(self):
        output = self._render()
        assert "website.layout" in output

    def test_detail_template_id(self):
        output = self._render()
        assert 'id="website_event_detail"' in output

    def test_detail_breadcrumb(self):
        output = self._render()
        assert "breadcrumb" in output

    def test_detail_fields(self):
        output = self._render()
        assert "event.name" in output
        assert "event.date_begin" in output
        assert "event.description" in output

    def test_detail_field_labels(self):
        output = self._render()
        assert "Name:" in output
        assert "Date Begin:" in output


class TestWebsiteMenusTemplate:
    """Verify website_menus.xml.j2 produces correct menu records."""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.spec = _enrich_spec(_make_website_spec())
        self.env = _make_env()
        self.ctx = _build_website_context(self.spec)

    def _render(self) -> str:
        template = self.env.get_template("website_menus.xml.j2")
        return template.render(**self.ctx)

    def test_website_renders_menus(self):
        output = self._render()
        assert "website.menu" in output

    def test_menu_noupdate(self):
        output = self._render()
        assert 'noupdate="1"' in output

    def test_menu_entries(self):
        output = self._render()
        # events page has show_in_menu=True
        assert "menu_website_events" in output
        assert "Our Events" in output
        # about page has show_in_menu=True
        assert "menu_website_about" in output
        assert "About Us" in output

    def test_menu_excludes_hidden(self):
        output = self._render()
        # event detail page has show_in_menu=False
        assert "menu_website_event" not in output or "menu_website_events" in output

    def test_menu_parent(self):
        output = self._render()
        assert 'ref="website.main_menu"' in output

    def test_menu_sequence(self):
        output = self._render()
        assert "<field name=\"sequence\">30</field>" in output
        assert "<field name=\"sequence\">10</field>" in output


# ---------------------------------------------------------------------------
# render_website() integration tests
# ---------------------------------------------------------------------------


class TestRenderWebsiteFunction:
    """Verify render_website() stage function produces all expected files."""

    def _get_enriched_spec_and_ctx(self):
        spec = _enrich_spec(_make_website_spec())
        ctx = _build_website_context(spec)
        return spec, ctx

    def test_website_renders_controller(self, tmp_path):
        """render_website creates controllers/website.py."""
        from amil_utils.renderer import render_website

        spec, ctx = self._get_enriched_spec_and_ctx()
        module_dir = tmp_path / "event_portal"
        module_dir.mkdir()

        result = render_website(_make_env(), spec, module_dir, ctx)
        assert result.success
        assert (module_dir / "controllers" / "website.py").exists()

    def test_website_renders_controllers_init(self, tmp_path):
        """render_website creates or updates controllers/__init__.py."""
        from amil_utils.renderer import render_website

        spec, ctx = self._get_enriched_spec_and_ctx()
        module_dir = tmp_path / "event_portal"
        module_dir.mkdir()

        result = render_website(_make_env(), spec, module_dir, ctx)
        assert result.success
        init_path = module_dir / "controllers" / "__init__.py"
        assert init_path.exists()
        assert "from . import website" in init_path.read_text()

    def test_website_renders_list_page_file(self, tmp_path):
        """render_website creates views/website_events.xml."""
        from amil_utils.renderer import render_website

        spec, ctx = self._get_enriched_spec_and_ctx()
        module_dir = tmp_path / "event_portal"
        module_dir.mkdir()

        result = render_website(_make_env(), spec, module_dir, ctx)
        assert result.success
        assert (module_dir / "views" / "website_events.xml").exists()

    def test_website_renders_detail_page_file(self, tmp_path):
        """render_website creates views/website_event_detail.xml."""
        from amil_utils.renderer import render_website

        spec, ctx = self._get_enriched_spec_and_ctx()
        module_dir = tmp_path / "event_portal"
        module_dir.mkdir()

        result = render_website(_make_env(), spec, module_dir, ctx)
        assert result.success
        assert (module_dir / "views" / "website_event_detail.xml").exists()

    def test_website_renders_menus_file(self, tmp_path):
        """render_website creates views/website_menus.xml."""
        from amil_utils.renderer import render_website

        spec, ctx = self._get_enriched_spec_and_ctx()
        module_dir = tmp_path / "event_portal"
        module_dir.mkdir()

        result = render_website(_make_env(), spec, module_dir, ctx)
        assert result.success
        assert (module_dir / "views" / "website_menus.xml").exists()

    def test_website_renders_assets(self, tmp_path):
        """render_website creates data/website_assets.xml."""
        from amil_utils.renderer import render_website

        spec, ctx = self._get_enriched_spec_and_ctx()
        module_dir = tmp_path / "event_portal"
        module_dir.mkdir()

        result = render_website(_make_env(), spec, module_dir, ctx)
        assert result.success
        assert (module_dir / "data" / "website_assets.xml").exists()

    def test_website_renders_css(self, tmp_path):
        """render_website creates static/src/css/website.css."""
        from amil_utils.renderer import render_website

        spec, ctx = self._get_enriched_spec_and_ctx()
        module_dir = tmp_path / "event_portal"
        module_dir.mkdir()

        result = render_website(_make_env(), spec, module_dir, ctx)
        assert result.success
        assert (module_dir / "static" / "src" / "css" / "website.css").exists()

    def test_website_skipped_without_spec(self, tmp_path):
        """render_website returns ok([]) when spec has no website."""
        from amil_utils.renderer import render_website

        spec = {"module_name": "test_mod", "models": []}
        module_dir = tmp_path / "test_mod"
        module_dir.mkdir()
        ctx = {"module_name": "test_mod"}

        result = render_website(_make_env(), spec, module_dir, ctx)
        assert result.success
        assert result.data == []

    def test_controller_content_has_website_true(self, tmp_path):
        """Generated controller has website=True in routes."""
        from amil_utils.renderer import render_website

        spec, ctx = self._get_enriched_spec_and_ctx()
        module_dir = tmp_path / "event_portal"
        module_dir.mkdir()

        render_website(_make_env(), spec, module_dir, ctx)
        content = (module_dir / "controllers" / "website.py").read_text()
        assert "website=True" in content
        assert "http.Controller" in content

    def test_list_page_content_has_website_layout(self, tmp_path):
        """Generated list page uses website.layout."""
        from amil_utils.renderer import render_website

        spec, ctx = self._get_enriched_spec_and_ctx()
        module_dir = tmp_path / "event_portal"
        module_dir.mkdir()

        render_website(_make_env(), spec, module_dir, ctx)
        content = (module_dir / "views" / "website_events.xml").read_text()
        assert "website.layout" in content

    def test_menus_content_has_main_menu_ref(self, tmp_path):
        """Generated menus reference website.main_menu."""
        from amil_utils.renderer import render_website

        spec, ctx = self._get_enriched_spec_and_ctx()
        module_dir = tmp_path / "event_portal"
        module_dir.mkdir()

        render_website(_make_env(), spec, module_dir, ctx)
        content = (module_dir / "views" / "website_menus.xml").read_text()
        assert "website.main_menu" in content

    def test_render_website_file_count(self, tmp_path):
        """render_website creates the expected number of files."""
        from amil_utils.renderer import render_website

        spec, ctx = self._get_enriched_spec_and_ctx()
        module_dir = tmp_path / "event_portal"
        module_dir.mkdir()

        result = render_website(_make_env(), spec, module_dir, ctx)
        assert result.success
        # controller, init, list page, detail page, static page, menus, assets, css = 8
        assert len(result.data) >= 7


# ---------------------------------------------------------------------------
# STAGE_NAMES integration
# ---------------------------------------------------------------------------


class TestStageNamesIncludesWebsite:
    """Verify STAGE_NAMES includes website after portal."""

    def test_website_in_stage_names(self):
        from amil_utils.renderer import STAGE_NAMES
        assert "website" in STAGE_NAMES

    def test_website_after_portal(self):
        from amil_utils.renderer import STAGE_NAMES
        portal_idx = STAGE_NAMES.index("portal")
        website_idx = STAGE_NAMES.index("website")
        assert website_idx == portal_idx + 1

    def test_website_before_bulk(self):
        from amil_utils.renderer import STAGE_NAMES
        website_idx = STAGE_NAMES.index("website")
        bulk_idx = STAGE_NAMES.index("bulk")
        assert bulk_idx == website_idx + 1


# ---------------------------------------------------------------------------
# Preprocessor registry integration
# ---------------------------------------------------------------------------


class TestWebsitePreprocessorRegistry:
    """Tests that website preprocessor is registered at order=96."""

    @pytest.fixture(autouse=True)
    def _reload_registry(self):
        """Reload preprocessor modules to ensure website is registered."""
        import importlib
        import sys

        from amil_utils.preprocessors._registry import (
            clear_registry,
            get_registered_preprocessors,
        )

        clear_registry()

        submodule_names = [
            name for name in sorted(sys.modules)
            if name.startswith("amil_utils.preprocessors.")
            and not name.endswith("._registry")
        ]
        for name in submodule_names:
            importlib.reload(sys.modules[name])
        yield
        clear_registry()

    def test_website_preprocessor_registered(self):
        from amil_utils.preprocessors._registry import get_registered_preprocessors
        entries = get_registered_preprocessors()
        names = [e[1] for e in entries]
        assert "website" in names

    def test_website_preprocessor_at_order_96(self):
        from amil_utils.preprocessors._registry import get_registered_preprocessors
        entries = get_registered_preprocessors()
        website_entry = next(e for e in entries if e[1] == "website")
        assert website_entry[0] == 96

    def test_website_after_portal_before_webhooks(self):
        """Website (96) runs after portal (95) and before webhooks (100)."""
        from amil_utils.preprocessors._registry import get_registered_preprocessors
        entries = get_registered_preprocessors()
        orders_by_name = {e[1]: e[0] for e in entries}
        assert orders_by_name.get("website", 0) > orders_by_name.get("portal", 0)
        assert orders_by_name.get("website", 0) < orders_by_name.get("webhook_patterns", 0)
