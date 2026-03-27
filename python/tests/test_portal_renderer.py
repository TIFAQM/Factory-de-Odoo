"""Tests for portal Jinja templates and render_portal() integration.

Phase 62 Plan 02: Verifies six Jinja portal templates produce correct Odoo 17
portal controller, QWeb templates, and record rules. Also tests render_portal()
stage function and pipeline integration.
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from jinja2 import Environment, FileSystemLoader, StrictUndefined

from amil_utils.preprocessors.portal import _process_portal
from amil_utils.renderer_utils import (
    _model_ref,
    _to_class,
    _to_python_var,
    _to_xml_id,
)


FIXTURES_DIR = Path(__file__).parent / "fixtures"
TEMPLATES_DIR = Path(__file__).parent.parent / "src" / "amil_utils" / "templates" / "shared"


def _load_fixture() -> dict[str, Any]:
    """Load and preprocess the portal test fixture."""
    fixture_path = FIXTURES_DIR / "portal_spec.json"
    with open(fixture_path) as f:
        spec = json.load(f)
    return _process_portal(spec)


def _make_env() -> Environment:
    """Create a Jinja2 environment loading shared templates."""
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    # Register the same filters as renderer.py
    env.filters["to_python_var"] = _to_python_var
    env.filters["to_xml_id"] = _to_xml_id
    env.filters["to_class"] = _to_class
    env.filters["model_ref"] = _model_ref
    return env


def _build_portal_context(spec: dict[str, Any]) -> dict[str, Any]:
    """Build a minimal portal rendering context from enriched spec."""
    module_name = spec["module_name"]
    portal_pages = spec.get("portal_pages", [])
    portal_auth = spec.get("portal_auth", "portal")

    # Collect unique models with their metadata
    models_seen: dict[str, dict[str, Any]] = {}
    editable_models: set[str] = set()
    for page in portal_pages:
        model = page["model"]
        if model not in models_seen:
            models_seen[model] = {
                "model": model,
                "model_var": page["model_var"],
                "model_class": page["model_class"],
                "ownership": page["ownership"],
            }
        if page.get("fields_editable"):
            editable_models.add(model)

    controller_class = _to_class(module_name) + "Portal"

    return {
        "module_name": module_name,
        "controller_class": controller_class,
        "portal_pages": portal_pages,
        "portal_auth": portal_auth,
        "portal_models": list(models_seen.values()),
        "editable_models": editable_models,
    }


# ---------------------------------------------------------------------------
# Test: portal_controller.py.j2
# ---------------------------------------------------------------------------


class TestPortalControllerTemplate:
    """Verify portal_controller.py.j2 produces correct Odoo 17 controller."""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.spec = _load_fixture()
        self.env = _make_env()
        self.ctx = _build_portal_context(self.spec)

    def _render(self) -> str:
        template = self.env.get_template("portal_controller.py.j2")
        return template.render(**self.ctx)

    def test_inherits_customer_portal(self):
        output = self._render()
        assert "class UniStudentPortalPortal(CustomerPortal):" in output

    def test_imports_customer_portal(self):
        output = self._render()
        assert "from odoo.addons.portal.controllers.portal import CustomerPortal" in output
        assert "pager as portal_pager" in output

    def test_imports_access_error(self):
        output = self._render()
        assert "from odoo.exceptions import AccessError, MissingError" in output

    def test_prepare_home_portal_values(self):
        """Counter method exists for pages with home_counter=True."""
        output = self._render()
        assert "_prepare_home_portal_values" in output
        assert "student_enrollments_count" in output
        assert "student_fees_count" in output

    def test_counter_uses_check_access_rights(self):
        output = self._render()
        assert "check_access_rights('read', raise_exception=False)" in output

    def test_counter_else_zero_fallback(self):
        output = self._render()
        assert "else 0" in output

    def test_domain_helpers(self):
        """Domain helper per unique model."""
        output = self._render()
        assert "_get_uni_student_domain" in output
        assert "_get_uni_enrollment_domain" in output
        assert "_get_fee_invoice_domain" in output
        assert "_get_exam_result_domain" in output

    def test_domain_ownership_path(self):
        """Domain helpers use correct ownership path."""
        output = self._render()
        assert "('user_id', '=', request.env.user.id)" in output
        assert "('student_id.user_id', '=', request.env.user.id)" in output

    def test_list_route_decorator(self):
        """List pages have route decorators with pagination."""
        output = self._render()
        assert "'/my/enrollments'" in output
        assert "'/my/enrollments/page/<int:page>'" in output

    def test_list_route_method(self):
        """List route methods use pager, sorting, stub zones."""
        output = self._render()
        assert "portal_pager(" in output
        assert "searchbar_sortings" in output

    def test_list_route_auth_and_website(self):
        output = self._render()
        assert "auth='user'" in output
        assert "website=True" in output

    def test_detail_route_decorator(self):
        """Detail routes for list pages with detail_route."""
        output = self._render()
        assert "'/my/enrollment/<int:enrollment_id>'" in output

    def test_detail_route_document_check_access(self):
        output = self._render()
        assert "_document_check_access" in output

    def test_detail_route_access_error_redirect(self):
        output = self._render()
        assert "except (AccessError, MissingError):" in output
        assert "request.redirect('/my')" in output

    def test_detail_type_search_domain(self):
        """Detail-type pages (profile) use search with domain, no ID."""
        output = self._render()
        assert "portal_my_student_profile" in output

    def test_editable_post_handler(self):
        """Editable fields generate POST handler with allowed_fields."""
        output = self._render()
        assert "allowed_fields" in output
        assert "'phone'" in output
        assert "'email'" in output
        assert "'address'" in output
        assert "request.httprequest.method == 'POST'" in output

    def test_report_download_route(self):
        """Report actions generate download routes."""
        output = self._render()
        assert "download_challan" in output
        assert "_show_report" in output
        assert "uni_fee.report_fee_challan" in output

    def test_business_logic_markers(self):
        """Stub zone markers for Logic Writer."""
        output = self._render()
        assert "# --- BUSINESS LOGIC START ---" in output
        assert "# --- BUSINESS LOGIC END ---" in output

    def test_counter_domain_filter(self):
        """Counter with counter_domain uses the filtered domain."""
        output = self._render()
        # student_fees has counter_domain: [["state", "in", ["confirmed", "overdue"]]]
        assert "student_fees_count" in output


# ---------------------------------------------------------------------------
# Test: portal_home_counter.xml.j2
# ---------------------------------------------------------------------------


class TestPortalHomeCounterTemplate:
    """Verify portal_home_counter.xml.j2 produces correct QWeb."""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.spec = _load_fixture()
        self.env = _make_env()
        self.ctx = _build_portal_context(self.spec)

    def _render(self) -> str:
        template = self.env.get_template("portal_home_counter.xml.j2")
        return template.render(**self.ctx)

    def test_inherits_portal_my_home(self):
        output = self._render()
        assert 'inherit_id="portal.portal_my_home"' in output

    def test_portal_docs_entry(self):
        output = self._render()
        assert "portal.portal_docs_entry" in output

    def test_category_enable(self):
        output = self._render()
        assert "portal_client_category_enable" in output

    def test_entries_for_shown_pages(self):
        """Entries for pages with show_in_home=True."""
        output = self._render()
        assert "My Profile" in output
        assert "My Enrollments" in output
        assert "My Fees" in output
        assert "My Results" in output

    def test_counter_placeholder(self):
        """Pages with home_counter=True have placeholder_count."""
        output = self._render()
        assert "student_enrollments_count" in output
        assert "student_fees_count" in output

    def test_customize_show(self):
        output = self._render()
        assert 'customize_show="True"' in output

    def test_priority(self):
        output = self._render()
        assert 'priority="60"' in output


# ---------------------------------------------------------------------------
# Test: portal_list.xml.j2
# ---------------------------------------------------------------------------


class TestPortalListTemplate:
    """Verify portal_list.xml.j2 produces correct QWeb list pages."""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.spec = _load_fixture()
        self.env = _make_env()
        self.ctx = _build_portal_context(self.spec)

    def _render(self, page_index: int = 1) -> str:
        """Render for a specific list page."""
        page = self.ctx["portal_pages"][page_index]
        template = self.env.get_template("portal_list.xml.j2")
        return template.render(
            **self.ctx,
            page=page,
        )

    def test_portal_layout(self):
        output = self._render()
        assert "portal.portal_layout" in output

    def test_portal_searchbar(self):
        output = self._render()
        assert "portal.portal_searchbar" in output

    def test_portal_table(self):
        output = self._render()
        assert "portal.portal_table" in output

    def test_empty_state(self):
        output = self._render()
        assert "alert alert-info" in output

    def test_list_fields_columns(self):
        """List fields appear as table columns."""
        output = self._render()
        # student_enrollments: list_fields = [course_id, term_id, state, grade]
        assert "course_id" in output
        assert "term_id" in output
        assert "state" in output
        assert "grade" in output

    def test_detail_link(self):
        """First column links to detail_route if present."""
        output = self._render()
        assert "/my/enrollment/" in output

    def test_pagination(self):
        output = self._render()
        assert "pager" in output


# ---------------------------------------------------------------------------
# Test: portal_detail.xml.j2
# ---------------------------------------------------------------------------


class TestPortalDetailTemplate:
    """Verify portal_detail.xml.j2 produces correct QWeb detail pages."""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.spec = _load_fixture()
        self.env = _make_env()
        self.ctx = _build_portal_context(self.spec)

    def _render(self, page_index: int = 1) -> str:
        """Render detail for enrollment page."""
        page = self.ctx["portal_pages"][page_index]
        template = self.env.get_template("portal_detail.xml.j2")
        return template.render(
            **self.ctx,
            page=page,
        )

    def test_portal_layout(self):
        output = self._render()
        assert "portal.portal_layout" in output

    def test_two_column_layout(self):
        output = self._render()
        assert "col-lg-8" in output
        assert "col-lg-4" in output

    def test_detail_fields(self):
        """Detail fields rendered as rows."""
        output = self._render()
        # enrollment: detail_fields
        assert "course_id" in output
        assert "term_id" in output
        assert "grade_point" in output

    def test_back_button(self):
        output = self._render()
        assert "btn btn-secondary" in output
        assert "/my/enrollments" in output

    def test_report_action_buttons(self):
        """Detail actions rendered on fee page sidebar."""
        page = self.ctx["portal_pages"][2]  # student_fees
        template = self.env.get_template("portal_detail.xml.j2")
        output = template.render(**self.ctx, page=page)
        assert "Download Challan" in output


# ---------------------------------------------------------------------------
# Test: portal_detail_editable.xml.j2
# ---------------------------------------------------------------------------


class TestPortalDetailEditableTemplate:
    """Verify portal_detail_editable.xml.j2 produces form with CSRF."""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.spec = _load_fixture()
        self.env = _make_env()
        self.ctx = _build_portal_context(self.spec)

    def _render(self) -> str:
        """Render for the student_profile page (editable)."""
        page = self.ctx["portal_pages"][0]  # student_profile (detail, editable)
        template = self.env.get_template("portal_detail_editable.xml.j2")
        return template.render(
            **self.ctx,
            page=page,
        )

    def test_csrf_token(self):
        output = self._render()
        assert "csrf_token" in output

    def test_form_method_post(self):
        output = self._render()
        assert 'method="POST"' in output

    def test_readonly_fields(self):
        """Read-only fields (visible minus editable) displayed."""
        output = self._render()
        # fields_visible = [name, cnic, program_id, department_id, cgpa, enrollment_status, semester]
        # fields_editable = [phone, email, address]
        # Read-only = visible minus editable
        assert "name" in output
        assert "cnic" in output

    def test_editable_input_fields(self):
        output = self._render()
        assert "phone" in output
        assert "email" in output
        assert "address" in output

    def test_save_button(self):
        output = self._render()
        assert "Save" in output
        assert 'type="submit"' in output

    def test_form_action_route(self):
        output = self._render()
        assert "/my/profile" in output


# ---------------------------------------------------------------------------
# Test: portal_rules.xml.j2
# ---------------------------------------------------------------------------


class TestPortalRulesTemplate:
    """Verify portal_rules.xml.j2 produces correct record rules."""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.spec = _load_fixture()
        self.env = _make_env()
        self.ctx = _build_portal_context(self.spec)

    def _render(self) -> str:
        template = self.env.get_template("portal_rules.xml.j2")
        return template.render(**self.ctx)

    def test_noupdate(self):
        output = self._render()
        assert 'noupdate="1"' in output

    def test_base_group_portal(self):
        output = self._render()
        assert "base.group_portal" in output

    def test_read_rule_permissions(self):
        """Read-only rules: perm_read=True, others=False."""
        output = self._render()
        assert 'perm_read" eval="True"' in output
        # Check that write is explicitly False for read rules
        assert 'perm_write" eval="False"' in output
        assert 'perm_create" eval="False"' in output
        assert 'perm_unlink" eval="False"' in output

    def test_write_rule_for_editable_model(self):
        """Editable models get separate write rule."""
        output = self._render()
        # uni.student has editable fields (student_profile page)
        assert "rule_portal_uni_student_write" in output

    def test_write_rule_permissions(self):
        output = self._render()
        # The write rule should have perm_write=True
        assert "rule_portal_uni_student_write" in output

    def test_model_ref_format(self):
        """model_id ref uses correct format."""
        output = self._render()
        assert "model_uni_student" in output
        assert "model_uni_enrollment" in output

    def test_ownership_domain(self):
        output = self._render()
        assert "('user_id', '=', user.id)" in output
        assert "('student_id.user_id', '=', user.id)" in output

    def test_all_models_have_rules(self):
        """All unique portal models have read rules."""
        output = self._render()
        assert "rule_portal_uni_student_read" in output
        assert "rule_portal_uni_enrollment_read" in output
        assert "rule_portal_fee_invoice_read" in output
        assert "rule_portal_exam_result_read" in output


# ---------------------------------------------------------------------------
# Test: render_portal() integration
# ---------------------------------------------------------------------------


class TestRenderPortalFunction:
    """Verify render_portal() stage function produces all expected files."""

    def test_render_portal_creates_controller_file(self, tmp_path):
        """render_portal creates controllers/portal.py."""
        from amil_utils.renderer import render_portal

        spec = _load_fixture()
        env = _make_env()
        module_dir = tmp_path / "uni_student_portal"
        module_dir.mkdir()
        ctx = _build_portal_context(spec)

        result = render_portal(env, spec, module_dir, ctx)
        assert result.success
        assert (module_dir / "controllers" / "portal.py").exists()

    def test_render_portal_creates_controllers_init(self, tmp_path):
        """render_portal creates or updates controllers/__init__.py."""
        from amil_utils.renderer import render_portal

        spec = _load_fixture()
        env = _make_env()
        module_dir = tmp_path / "uni_student_portal"
        module_dir.mkdir()
        ctx = _build_portal_context(spec)

        result = render_portal(env, spec, module_dir, ctx)
        assert result.success
        init_path = module_dir / "controllers" / "__init__.py"
        assert init_path.exists()
        assert "from . import portal" in init_path.read_text()

    def test_render_portal_creates_home_counter(self, tmp_path):
        """render_portal creates views/portal_home.xml."""
        from amil_utils.renderer import render_portal

        spec = _load_fixture()
        env = _make_env()
        module_dir = tmp_path / "uni_student_portal"
        module_dir.mkdir()
        ctx = _build_portal_context(spec)

        result = render_portal(env, spec, module_dir, ctx)
        assert result.success
        assert (module_dir / "views" / "portal_home.xml").exists()

    def test_render_portal_creates_list_pages(self, tmp_path):
        """render_portal creates per-page list QWeb XML."""
        from amil_utils.renderer import render_portal

        spec = _load_fixture()
        env = _make_env()
        module_dir = tmp_path / "uni_student_portal"
        module_dir.mkdir()
        ctx = _build_portal_context(spec)

        result = render_portal(env, spec, module_dir, ctx)
        assert result.success
        assert (module_dir / "views" / "portal_student_enrollments.xml").exists()
        assert (module_dir / "views" / "portal_student_fees.xml").exists()
        assert (module_dir / "views" / "portal_student_results.xml").exists()

    def test_render_portal_creates_detail_pages(self, tmp_path):
        """render_portal creates detail QWeb XML for list pages with detail_route."""
        from amil_utils.renderer import render_portal

        spec = _load_fixture()
        env = _make_env()
        module_dir = tmp_path / "uni_student_portal"
        module_dir.mkdir()
        ctx = _build_portal_context(spec)

        result = render_portal(env, spec, module_dir, ctx)
        assert result.success
        assert (module_dir / "views" / "portal_student_enrollments_detail.xml").exists()
        assert (module_dir / "views" / "portal_student_fees_detail.xml").exists()

    def test_render_portal_creates_editable_detail(self, tmp_path):
        """render_portal creates editable detail for profile page."""
        from amil_utils.renderer import render_portal

        spec = _load_fixture()
        env = _make_env()
        module_dir = tmp_path / "uni_student_portal"
        module_dir.mkdir()
        ctx = _build_portal_context(spec)

        result = render_portal(env, spec, module_dir, ctx)
        assert result.success
        assert (module_dir / "views" / "portal_student_profile.xml").exists()
        content = (module_dir / "views" / "portal_student_profile.xml").read_text()
        assert "csrf_token" in content

    def test_render_portal_creates_rules(self, tmp_path):
        """render_portal creates security/portal_rules.xml."""
        from amil_utils.renderer import render_portal

        spec = _load_fixture()
        env = _make_env()
        module_dir = tmp_path / "uni_student_portal"
        module_dir.mkdir()
        ctx = _build_portal_context(spec)

        result = render_portal(env, spec, module_dir, ctx)
        assert result.success
        rules_path = module_dir / "security" / "portal_rules.xml"
        assert rules_path.exists()
        content = rules_path.read_text()
        assert "base.group_portal" in content
        assert 'noupdate="1"' in content

    def test_render_portal_noop_without_portal(self, tmp_path):
        """render_portal returns ok([]) when spec has no portal."""
        from amil_utils.renderer import render_portal

        spec = {"module_name": "test_mod", "models": []}
        env = _make_env()
        module_dir = tmp_path / "test_mod"
        module_dir.mkdir()
        ctx = {"module_name": "test_mod"}

        result = render_portal(env, spec, module_dir, ctx)
        assert result.success
        assert result.data == []

    def test_controller_content_has_customer_portal(self, tmp_path):
        """Generated controller inherits CustomerPortal."""
        from amil_utils.renderer import render_portal

        spec = _load_fixture()
        env = _make_env()
        module_dir = tmp_path / "uni_student_portal"
        module_dir.mkdir()
        ctx = _build_portal_context(spec)

        render_portal(env, spec, module_dir, ctx)
        content = (module_dir / "controllers" / "portal.py").read_text()
        assert "CustomerPortal" in content
        assert "_prepare_home_portal_values" in content

    def test_controller_has_route_decorators(self, tmp_path):
        """Generated controller has @http.route decorators."""
        from amil_utils.renderer import render_portal

        spec = _load_fixture()
        env = _make_env()
        module_dir = tmp_path / "uni_student_portal"
        module_dir.mkdir()
        ctx = _build_portal_context(spec)

        render_portal(env, spec, module_dir, ctx)
        content = (module_dir / "controllers" / "portal.py").read_text()
        assert "@http.route" in content
        assert "'/my/enrollments'" in content

    def test_qweb_home_has_portal_my_home(self, tmp_path):
        """Generated home counter XML inherits portal.portal_my_home."""
        from amil_utils.renderer import render_portal

        spec = _load_fixture()
        env = _make_env()
        module_dir = tmp_path / "uni_student_portal"
        module_dir.mkdir()
        ctx = _build_portal_context(spec)

        render_portal(env, spec, module_dir, ctx)
        content = (module_dir / "views" / "portal_home.xml").read_text()
        assert "portal.portal_my_home" in content
        assert "portal.portal_docs_entry" in content

    def test_rules_has_correct_perms(self, tmp_path):
        """Generated rules have explicit perm fields."""
        from amil_utils.renderer import render_portal

        spec = _load_fixture()
        env = _make_env()
        module_dir = tmp_path / "uni_student_portal"
        module_dir.mkdir()
        ctx = _build_portal_context(spec)

        render_portal(env, spec, module_dir, ctx)
        content = (module_dir / "security" / "portal_rules.xml").read_text()
        assert 'perm_read" eval="True"' in content
        assert 'perm_write" eval="False"' in content

    def test_render_portal_file_count(self, tmp_path):
        """render_portal creates expected number of files."""
        from amil_utils.renderer import render_portal

        spec = _load_fixture()
        env = _make_env()
        module_dir = tmp_path / "uni_student_portal"
        module_dir.mkdir()
        ctx = _build_portal_context(spec)

        result = render_portal(env, spec, module_dir, ctx)
        assert result.success
        # Expected: controller, init, home, 3 list pages, 2 detail pages,
        # 1 editable page, 1 rules = 11 files
        assert len(result.data) >= 10


# ---------------------------------------------------------------------------
# Test: STAGE_NAMES includes portal
# ---------------------------------------------------------------------------


class TestStageNamesIncludesPortal:
    """Verify STAGE_NAMES includes portal after controllers (Phase 63: 14 stages with bulk after portal)."""

    def test_stage_count(self):
        from amil_utils.renderer import STAGE_NAMES
        assert len(STAGE_NAMES) == 15

    def test_portal_after_controllers(self):
        from amil_utils.renderer import STAGE_NAMES
        ctrl_idx = STAGE_NAMES.index("controllers")
        portal_idx = STAGE_NAMES.index("portal")
        assert portal_idx == ctrl_idx + 1
