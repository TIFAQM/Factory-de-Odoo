"""Tests for I3: @api.private decorator generation in Odoo 19.0 templates.

Verifies that internal helper methods in 19.0 rendered models get @api.private,
that 17.0/18.0 templates do NOT include @api.private, and that ORM-decorated
methods are not double-decorated.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from amil_utils.renderer import (
    _build_model_context,
    get_template_dir,
    render_module,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_spec(
    models: list[dict] | None = None,
    odoo_version: str = "19.0",
    **kwargs,
) -> dict:
    """Build a minimal spec dict for testing."""
    spec = {
        "module_name": "test_module",
        "depends": ["base"],
        "odoo_version": odoo_version,
        "models": models or [],
        "wizards": [],
    }
    spec.update(kwargs)
    return spec


def _render_model_file(spec: dict, model_name: str, tmp_path: Path) -> str:
    """Render a full module and return the content of the specified model's .py file."""
    files, _ = render_module(spec, get_template_dir(), tmp_path)
    model_var = model_name.replace(".", "_")
    model_file = tmp_path / spec["module_name"] / "models" / f"{model_var}.py"
    assert model_file.exists(), (
        f"{model_var}.py not generated. Files: {[str(f) for f in files]}"
    )
    return model_file.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# @api.private on audit helper methods (19.0)
# ---------------------------------------------------------------------------


class TestApiPrivateAuditHelpers:
    """Verify @api.private on _audit_read_old and _audit_log_changes in 19.0."""

    def _make_audit_spec(self, odoo_version: str = "19.0") -> dict:
        return {
            "module_name": "test_module",
            "module_title": "Test Module",
            "summary": "Test",
            "author": "Test",
            "depends": ["base"],
            "odoo_version": odoo_version,
            "models": [
                {
                    "name": "test.record",
                    "description": "Test Record",
                    "fields": [
                        {"name": "name", "type": "Char", "required": True},
                        {"name": "value", "type": "Integer"},
                    ],
                    "audit": True,
                },
            ],
            "security": {
                "roles": ["viewer", "manager"],
                "defaults": {
                    "viewer": "r",
                    "manager": "crud",
                },
            },
        }

    def test_audit_read_old_has_api_private_19(self, tmp_path):
        """_audit_read_old gets @api.private in 19.0 rendered output."""
        spec = self._make_audit_spec("19.0")
        content = _render_model_file(spec, "test.record", tmp_path)
        # Find the _audit_read_old method and verify @api.private precedes it
        lines = content.split("\n")
        for i, line in enumerate(lines):
            if "def _audit_read_old(" in line:
                preceding = lines[i - 1].strip()
                assert preceding == "@api.private", (
                    f"Expected @api.private before _audit_read_old, got: {preceding!r}"
                )
                return
        pytest.fail("_audit_read_old method not found in rendered output")

    def test_audit_log_changes_has_api_private_19(self, tmp_path):
        """_audit_log_changes gets @api.private in 19.0 rendered output."""
        spec = self._make_audit_spec("19.0")
        content = _render_model_file(spec, "test.record", tmp_path)
        lines = content.split("\n")
        for i, line in enumerate(lines):
            if "def _audit_log_changes(" in line:
                preceding = lines[i - 1].strip()
                assert preceding == "@api.private", (
                    f"Expected @api.private before _audit_log_changes, got: {preceding!r}"
                )
                return
        pytest.fail("_audit_log_changes method not found in rendered output")

    def test_audit_tracked_fields_no_api_private_19(self, tmp_path):
        """_audit_tracked_fields has @api.model, should NOT get @api.private."""
        spec = self._make_audit_spec("19.0")
        content = _render_model_file(spec, "test.record", tmp_path)
        lines = content.split("\n")
        for i, line in enumerate(lines):
            if "def _audit_tracked_fields(" in line:
                preceding = lines[i - 1].strip()
                assert preceding == "@api.model", (
                    f"Expected @api.model before _audit_tracked_fields, got: {preceding!r}"
                )
                return
        pytest.fail("_audit_tracked_fields method not found in rendered output")


# ---------------------------------------------------------------------------
# @api.private on webhook helper methods (19.0)
# ---------------------------------------------------------------------------


class TestApiPrivateWebhookHelpers:
    """Verify @api.private on _webhook_* methods in 19.0."""

    def _make_webhook_spec(self, odoo_version: str = "19.0") -> dict:
        return {
            "module_name": "test_module",
            "module_title": "Test Module",
            "summary": "Test",
            "author": "Test",
            "depends": ["base"],
            "odoo_version": odoo_version,
            "models": [
                {
                    "name": "test.order",
                    "description": "Test Order",
                    "fields": [
                        {"name": "name", "type": "Char", "required": True},
                        {"name": "status", "type": "Selection", "selection": [
                            ("draft", "Draft"), ("done", "Done"),
                        ]},
                    ],
                    "has_webhooks": True,
                    "webhook_on_create": True,
                    "webhook_watched_fields": ["status"],
                    "webhook_on_unlink": True,
                },
            ],
            "security": {
                "roles": ["user", "manager"],
                "defaults": {
                    "user": "cr",
                    "manager": "crud",
                },
            },
        }

    def test_webhook_post_create_has_api_private_19(self, tmp_path):
        """_webhook_post_create gets @api.private in 19.0 rendered output."""
        spec = self._make_webhook_spec("19.0")
        content = _render_model_file(spec, "test.order", tmp_path)
        lines = content.split("\n")
        for i, line in enumerate(lines):
            if "def _webhook_post_create(" in line:
                preceding = lines[i - 1].strip()
                assert preceding == "@api.private", (
                    f"Expected @api.private before _webhook_post_create, got: {preceding!r}"
                )
                return
        pytest.fail("_webhook_post_create method not found in rendered output")

    def test_webhook_post_write_has_api_private_19(self, tmp_path):
        """_webhook_post_write gets @api.private in 19.0 rendered output."""
        spec = self._make_webhook_spec("19.0")
        content = _render_model_file(spec, "test.order", tmp_path)
        lines = content.split("\n")
        for i, line in enumerate(lines):
            if "def _webhook_post_write(" in line:
                preceding = lines[i - 1].strip()
                assert preceding == "@api.private", (
                    f"Expected @api.private before _webhook_post_write, got: {preceding!r}"
                )
                return
        pytest.fail("_webhook_post_write method not found in rendered output")

    def test_webhook_pre_unlink_has_api_private_19(self, tmp_path):
        """_webhook_pre_unlink gets @api.private in 19.0 rendered output."""
        spec = self._make_webhook_spec("19.0")
        content = _render_model_file(spec, "test.order", tmp_path)
        lines = content.split("\n")
        for i, line in enumerate(lines):
            if "def _webhook_pre_unlink(" in line:
                preceding = lines[i - 1].strip()
                assert preceding == "@api.private", (
                    f"Expected @api.private before _webhook_pre_unlink, got: {preceding!r}"
                )
                return
        pytest.fail("_webhook_pre_unlink method not found in rendered output")


# ---------------------------------------------------------------------------
# @api.private on _post_create_processing (19.0)
# ---------------------------------------------------------------------------


class TestApiPrivateBulkHelpers:
    """Verify @api.private on _post_create_processing in 19.0."""

    def _make_bulk_spec(self, odoo_version: str = "19.0") -> dict:
        return {
            "module_name": "test_module",
            "module_title": "Test Module",
            "summary": "Test",
            "author": "Test",
            "depends": ["base"],
            "odoo_version": odoo_version,
            "models": [
                {
                    "name": "test.item",
                    "description": "Test Item",
                    "fields": [
                        {"name": "name", "type": "Char", "required": True},
                    ],
                    "is_bulk": True,
                },
            ],
            "security": {
                "roles": ["user", "manager"],
                "defaults": {
                    "user": "cr",
                    "manager": "crud",
                },
            },
        }

    def test_post_create_processing_has_api_private_19(self, tmp_path):
        """_post_create_processing gets @api.private in 19.0 rendered output."""
        spec = self._make_bulk_spec("19.0")
        content = _render_model_file(spec, "test.item", tmp_path)
        lines = content.split("\n")
        for i, line in enumerate(lines):
            if "def _post_create_processing(" in line:
                preceding = lines[i - 1].strip()
                assert preceding == "@api.private", (
                    f"Expected @api.private before _post_create_processing, got: {preceding!r}"
                )
                return
        pytest.fail("_post_create_processing method not found in rendered output")


# ---------------------------------------------------------------------------
# @api.private does NOT appear in 17.0
# ---------------------------------------------------------------------------


class TestApiPrivateNotIn17:
    """Verify that @api.private does NOT appear in 17.0 rendered output."""

    def test_17_audit_no_api_private(self, tmp_path):
        """17.0 audit model does NOT have @api.private."""
        spec = {
            "module_name": "test_module",
            "module_title": "Test Module",
            "summary": "Test",
            "author": "Test",
            "depends": ["base"],
            "odoo_version": "17.0",
            "models": [
                {
                    "name": "test.record",
                    "description": "Test Record",
                    "fields": [
                        {"name": "name", "type": "Char", "required": True},
                        {"name": "value", "type": "Integer"},
                    ],
                    "audit": True,
                },
            ],
            "security": {
                "roles": ["viewer", "manager"],
                "defaults": {
                    "viewer": "r",
                    "manager": "crud",
                },
            },
        }
        content = _render_model_file(spec, "test.record", tmp_path)
        assert "@api.private" not in content, (
            "@api.private should not appear in 17.0 rendered model"
        )

    def test_17_bulk_no_api_private(self, tmp_path):
        """17.0 bulk model does NOT have @api.private."""
        spec = {
            "module_name": "test_module",
            "module_title": "Test Module",
            "summary": "Test",
            "author": "Test",
            "depends": ["base"],
            "odoo_version": "17.0",
            "models": [
                {
                    "name": "test.item",
                    "description": "Test Item",
                    "fields": [
                        {"name": "name", "type": "Char", "required": True},
                    ],
                    "is_bulk": True,
                },
            ],
            "security": {
                "roles": ["user", "manager"],
                "defaults": {
                    "user": "cr",
                    "manager": "crud",
                },
            },
        }
        content = _render_model_file(spec, "test.item", tmp_path)
        assert "@api.private" not in content, (
            "@api.private should not appear in 17.0 rendered model"
        )


# ---------------------------------------------------------------------------
# ORM-decorated methods NOT double-decorated
# ---------------------------------------------------------------------------


class TestNoDoubleDecoration:
    """Verify ORM-decorated methods do not get @api.private."""

    def test_computed_field_no_api_private(self, tmp_path):
        """Computed field methods with @api.depends should NOT also have @api.private."""
        spec = _make_spec(
            models=[{
                "name": "test.calc",
                "description": "Test Calc",
                "fields": [
                    {"name": "qty", "type": "Integer"},
                    {"name": "total", "type": "Float", "compute": "_compute_total",
                     "depends": ["qty"]},
                ],
            }],
            odoo_version="19.0",
            security={
                "roles": ["user", "manager"],
                "defaults": {"user": "cr", "manager": "crud"},
            },
        )
        content = _render_model_file(spec, "test.calc", tmp_path)
        lines = content.split("\n")
        for i, line in enumerate(lines):
            if "def _compute_total(" in line:
                preceding = lines[i - 1].strip()
                assert "@api.depends" in preceding, (
                    f"Expected @api.depends before _compute_total, got: {preceding!r}"
                )
                # Check line before decorator is NOT @api.private
                if i >= 2:
                    two_before = lines[i - 2].strip()
                    assert two_before != "@api.private", (
                        "_compute_total should NOT be double-decorated with @api.private"
                    )
                return
        pytest.fail("_compute_total method not found in rendered output")

    def test_onchange_no_api_private(self, tmp_path):
        """Onchange methods with @api.onchange should NOT also have @api.private."""
        spec = _make_spec(
            models=[{
                "name": "test.form",
                "description": "Test Form",
                "fields": [
                    {"name": "partner_id", "type": "Many2one",
                     "comodel_name": "res.partner", "onchange": "partner_id"},
                ],
            }],
            odoo_version="19.0",
            security={
                "roles": ["user", "manager"],
                "defaults": {"user": "cr", "manager": "crud"},
            },
        )
        content = _render_model_file(spec, "test.form", tmp_path)
        lines = content.split("\n")
        for i, line in enumerate(lines):
            if "def _onchange_partner_id(" in line:
                preceding = lines[i - 1].strip()
                assert "@api.onchange" in preceding, (
                    f"Expected @api.onchange before _onchange_partner_id, got: {preceding!r}"
                )
                if i >= 2:
                    two_before = lines[i - 2].strip()
                    assert two_before != "@api.private", (
                        "_onchange_partner_id should NOT be double-decorated with @api.private"
                    )
                return
        pytest.fail("_onchange_partner_id method not found in rendered output")

    def test_constrains_no_api_private(self, tmp_path):
        """Constraint methods with @api.constrains should NOT also have @api.private."""
        spec = _make_spec(
            models=[{
                "name": "test.validated",
                "description": "Test Validated",
                "fields": [
                    {"name": "amount", "type": "Float",
                     "constrains": ["amount"]},
                ],
            }],
            odoo_version="19.0",
            security={
                "roles": ["user", "manager"],
                "defaults": {"user": "cr", "manager": "crud"},
            },
        )
        content = _render_model_file(spec, "test.validated", tmp_path)
        lines = content.split("\n")
        for i, line in enumerate(lines):
            if "def _check_amount(" in line:
                preceding = lines[i - 1].strip()
                assert "@api.constrains" in preceding, (
                    f"Expected @api.constrains before _check_amount, got: {preceding!r}"
                )
                if i >= 2:
                    two_before = lines[i - 2].strip()
                    assert two_before != "@api.private", (
                        "_check_amount should NOT be double-decorated with @api.private"
                    )
                return
        pytest.fail("_check_amount method not found in rendered output")


# ---------------------------------------------------------------------------
# api import presence
# ---------------------------------------------------------------------------


class TestApiImportPresent:
    """Verify 'from odoo import api' is in 19.0 template when @api.private is used."""

    def test_api_import_with_audit(self, tmp_path):
        """19.0 model with audit has 'api' in the import line."""
        spec = {
            "module_name": "test_module",
            "module_title": "Test Module",
            "summary": "Test",
            "author": "Test",
            "depends": ["base"],
            "odoo_version": "19.0",
            "models": [
                {
                    "name": "test.record",
                    "description": "Test Record",
                    "fields": [
                        {"name": "name", "type": "Char", "required": True},
                    ],
                    "audit": True,
                },
            ],
            "security": {
                "roles": ["viewer", "manager"],
                "defaults": {"viewer": "r", "manager": "crud"},
            },
        }
        content = _render_model_file(spec, "test.record", tmp_path)
        assert "from odoo import api," in content or "from odoo import api, " in content, (
            "api import should be present when @api.private is used"
        )

    def test_api_import_with_webhooks(self, tmp_path):
        """19.0 model with webhooks has 'api' in the import line."""
        spec = {
            "module_name": "test_module",
            "module_title": "Test Module",
            "summary": "Test",
            "author": "Test",
            "depends": ["base"],
            "odoo_version": "19.0",
            "models": [
                {
                    "name": "test.order",
                    "description": "Test Order",
                    "fields": [
                        {"name": "name", "type": "Char", "required": True},
                    ],
                    "has_webhooks": True,
                    "webhook_on_create": True,
                },
            ],
            "security": {
                "roles": ["user", "manager"],
                "defaults": {"user": "cr", "manager": "crud"},
            },
        }
        content = _render_model_file(spec, "test.order", tmp_path)
        assert "from odoo import api," in content or "from odoo import api, " in content, (
            "api import should be present when @api.private is used on webhook methods"
        )


# ---------------------------------------------------------------------------
# Knowledge base content
# ---------------------------------------------------------------------------


class TestKnowledgeBaseApiPrivate:
    """Verify the knowledge base file contains @api.private documentation."""

    def test_knowledge_base_has_api_private_section(self):
        """models.md knowledge base contains @api.private section."""
        kb_path = Path("/home/inshal-rauf/Factory-de-Odoo/amil/knowledge/models.md")
        content = kb_path.read_text(encoding="utf-8")
        assert "### @api.private (Odoo 19.0+)" in content
        assert "@api.private" in content
        assert "non-RPC-callable" in content
        assert "Do NOT use" in content
