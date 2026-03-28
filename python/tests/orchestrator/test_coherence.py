"""Tests for orchestrator coherence module."""
from __future__ import annotations

import pytest

from amil_utils.orchestrator.coherence import (
    _load_base_models,
    check_computed_depends,
    check_duplicate_models,
    check_field_renames,
    check_many2one_targets,
    check_security_groups,
    run_all_checks,
)


EMPTY_REGISTRY = {"models": {}}


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
class TestCheckMany2oneTargets:
    def test_passes_with_known_targets(self) -> None:
        spec = {"models": [{"name": "test.model", "fields": [
            {"name": "partner_id", "type": "Many2one", "comodel_name": "res.partner"},
        ]}]}
        result = check_many2one_targets(spec, EMPTY_REGISTRY)
        assert result["status"] == "pass"

    def test_fails_with_unknown_target(self) -> None:
        spec = {"models": [{"name": "test.model", "fields": [
            {"name": "custom_id", "type": "Many2one", "comodel_name": "nonexistent.model"},
        ]}]}
        result = check_many2one_targets(spec, EMPTY_REGISTRY)
        assert result["status"] == "fail"
        assert len(result["violations"]) == 1

    def test_resolves_from_registry(self) -> None:
        spec = {"models": [{"name": "test.model", "fields": [
            {"name": "custom_id", "type": "Many2one", "comodel_name": "custom.model"},
        ]}]}
        registry = {"models": {"custom.model": {"module": "custom_mod"}}}
        result = check_many2one_targets(spec, registry)
        assert result["status"] == "pass"

    def test_resolves_from_same_spec(self) -> None:
        spec = {"models": [
            {"name": "a.model", "fields": [
                {"name": "b_id", "type": "Many2one", "comodel_name": "b.model"},
            ]},
            {"name": "b.model", "fields": []},
        ]}
        result = check_many2one_targets(spec, EMPTY_REGISTRY)
        assert result["status"] == "pass"

    def test_ignores_non_relational_fields(self) -> None:
        spec = {"models": [{"name": "test.model", "fields": [
            {"name": "name", "type": "Char", "comodel_name": "nonexistent.model"},
        ]}]}
        result = check_many2one_targets(spec, EMPTY_REGISTRY)
        assert result["status"] == "pass"

    def test_many2one_to_sale_order_passes(self) -> None:
        """sale.order is in known_odoo_models.json -- should not be flagged."""
        spec = {"models": [{"name": "custom.invoice", "fields": [
            {"name": "order_id", "type": "Many2one", "comodel_name": "sale.order"},
        ]}]}
        result = check_many2one_targets(spec, EMPTY_REGISTRY)
        assert result["status"] == "pass"

    def test_many2one_to_stock_picking_passes(self) -> None:
        """stock.picking is in known_odoo_models.json -- should not be flagged."""
        spec = {"models": [{"name": "custom.delivery", "fields": [
            {"name": "picking_id", "type": "Many2one", "comodel_name": "stock.picking"},
        ]}]}
        result = check_many2one_targets(spec, EMPTY_REGISTRY)
        assert result["status"] == "pass"

    def test_many2one_to_project_task_passes(self) -> None:
        """project.task is in known_odoo_models.json -- should not be flagged."""
        spec = {"models": [{"name": "custom.work", "fields": [
            {"name": "task_id", "type": "Many2one", "comodel_name": "project.task"},
        ]}]}
        result = check_many2one_targets(spec, EMPTY_REGISTRY)
        assert result["status"] == "pass"

    def test_many2one_to_truly_unknown_model_fails(self) -> None:
        """A completely invented model should still fail."""
        spec = {"models": [{"name": "custom.invoice", "fields": [
            {"name": "widget_id", "type": "Many2one", "comodel_name": "fake.nonexistent.model"},
        ]}]}
        result = check_many2one_targets(spec, EMPTY_REGISTRY)
        assert result["status"] == "fail"


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
class TestCheckDuplicateModels:
    def test_passes_when_no_duplicates(self) -> None:
        spec = {"models": [{"name": "new.model", "fields": []}]}
        result = check_duplicate_models(spec, EMPTY_REGISTRY)
        assert result["status"] == "pass"

    def test_fails_on_cross_module_duplicate(self) -> None:
        spec = {"models": [{"name": "hr.employee", "module": "my_mod", "fields": []}]}
        registry = {"models": {"hr.employee": {"module": "hr_base"}}}
        result = check_duplicate_models(spec, registry)
        assert result["status"] == "fail"
        assert result["violations"][0]["registry_module"] == "hr_base"

    def test_allows_same_module_update(self) -> None:
        spec = {"models": [{"name": "hr.employee", "module": "hr_base", "fields": []}]}
        registry = {"models": {"hr.employee": {"module": "hr_base"}}}
        result = check_duplicate_models(spec, registry)
        assert result["status"] == "pass"


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
class TestCheckComputedDepends:
    def test_passes_with_valid_depends(self) -> None:
        spec = {"models": [{"name": "test.model", "fields": [
            {"name": "amount", "type": "Float"},
            {"name": "total", "type": "Float", "compute": "_compute_total",
             "depends": ["amount"]},
        ]}]}
        result = check_computed_depends(spec, EMPTY_REGISTRY)
        assert result["status"] == "pass"

    def test_fails_with_missing_depends(self) -> None:
        spec = {"models": [{"name": "test.model", "fields": [
            {"name": "total", "type": "Float", "compute": "_compute_total",
             "depends": ["nonexistent_field"]},
        ]}]}
        result = check_computed_depends(spec, EMPTY_REGISTRY)
        assert result["status"] == "fail"

    def test_dot_notation_validates_first_segment(self) -> None:
        spec = {"models": [{"name": "test.model", "fields": [
            {"name": "partner_id", "type": "Many2one"},
            {"name": "partner_name", "type": "Char", "compute": "_compute",
             "depends": ["partner_id.name"]},
        ]}]}
        result = check_computed_depends(spec, EMPTY_REGISTRY)
        assert result["status"] == "pass"


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
class TestCheckSecurityGroups:
    def test_passes_when_consistent(self) -> None:
        spec = {"security": {
            "roles": ["manager", "user"],
            "acl": {"manager": {"crud": "1111"}, "user": {"crud": "1100"}},
            "defaults": {},
        }}
        result = check_security_groups(spec, EMPTY_REGISTRY)
        assert result["status"] == "pass"

    def test_fails_on_acl_without_role(self) -> None:
        spec = {"security": {
            "roles": ["manager"],
            "acl": {"manager": {}, "ghost": {}},
        }}
        result = check_security_groups(spec, EMPTY_REGISTRY)
        assert result["status"] == "fail"
        assert any(v["role"] == "ghost" for v in result["violations"])

    def test_warns_role_without_acl(self) -> None:
        spec = {"security": {
            "roles": ["manager", "user"],
            "acl": {"manager": {}},
        }}
        result = check_security_groups(spec, EMPTY_REGISTRY)
        assert result["status"] == "fail"
        assert any(v["role"] == "user" for v in result["violations"])

    def test_passes_with_no_security(self) -> None:
        result = check_security_groups({}, EMPTY_REGISTRY)
        assert result["status"] == "pass"


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
class TestRunAllChecks:
    def test_all_pass(self) -> None:
        spec = {"models": [{"name": "test.model", "fields": []}]}
        result = run_all_checks(spec, EMPTY_REGISTRY)
        assert result["status"] == "pass"
        assert len(result["checks"]) == 4

    def test_one_failure_fails_overall(self) -> None:
        spec = {"models": [{"name": "test.model", "fields": [
            {"name": "x", "type": "Many2one", "comodel_name": "bad.model"},
        ]}]}
        result = run_all_checks(spec, EMPTY_REGISTRY)
        assert result["status"] == "fail"


class TestLoadBaseModels:
    def test_contains_common_models(self) -> None:
        models = _load_base_models()
        assert "res.partner" in models
        assert "res.users" in models
        assert "mail.thread" in models

    def test_contains_many_models(self) -> None:
        """Should load all 203 models from known_odoo_models.json, not just 20."""
        models = _load_base_models()
        assert len(models) >= 200

    def test_contains_sale_stock_project_models(self) -> None:
        """Models that were missing from the hardcoded set."""
        models = _load_base_models()
        assert "sale.order" in models
        assert "stock.picking" in models
        assert "project.task" in models

    def test_returns_frozenset(self) -> None:
        models = _load_base_models()
        assert isinstance(models, frozenset)


class TestCoherenceDeprecationWarnings:
    """Verify each public function emits DeprecationWarning at runtime."""

    def test_check_many2one_targets_emits_deprecation(self) -> None:
        with pytest.warns(DeprecationWarning, match="odoo-ls"):
            check_many2one_targets({"models": []}, {"models": {}})

    def test_check_duplicate_models_emits_deprecation(self) -> None:
        with pytest.warns(DeprecationWarning, match="odoo-ls"):
            check_duplicate_models({"models": []}, {"models": {}})

    def test_check_computed_depends_emits_deprecation(self) -> None:
        with pytest.warns(DeprecationWarning, match="odoo-ls"):
            check_computed_depends({"models": []}, {"models": {}})

    def test_check_security_groups_emits_deprecation(self) -> None:
        with pytest.warns(DeprecationWarning, match="odoo-ls"):
            check_security_groups({}, {"models": {}})

    def test_run_all_checks_emits_deprecation(self) -> None:
        with pytest.warns(DeprecationWarning, match="odoo-ls"):
            run_all_checks({"models": []}, {"models": {}})


class TestCheckFieldRenames:
    """Tests for check_field_renames (I1 field-level renames)."""

    def test_check_field_renames_detects_renamed_comodel(self) -> None:
        """A spec referencing hr.contract as comodel should fail for 19.0."""
        spec = {"models": [{"name": "my.model", "fields": [
            {"name": "contract_id", "type": "Many2one", "comodel_name": "hr.contract"},
        ]}]}
        result = check_field_renames(spec, "19.0")
        assert result["status"] == "fail"
        assert result["check"] == "field_renames"
        assert len(result["violations"]) >= 1
        violation = result["violations"][0]
        assert violation["model"] == "hr.contract"
        assert violation["renamed_to"] == "hr.version"

    def test_check_field_renames_clean_spec_passes(self) -> None:
        """A spec with valid model/field references should pass."""
        spec = {"models": [{"name": "my.model", "fields": [
            {"name": "partner_id", "type": "Many2one", "comodel_name": "res.partner"},
            {"name": "name", "type": "Char"},
        ]}]}
        result = check_field_renames(spec, "19.0")
        assert result["status"] == "pass"
        assert result["violations"] == []

    def test_check_field_renames_old_version_passes(self) -> None:
        """Same spec with hr.contract comodel should pass for 17.0."""
        spec = {"models": [{"name": "my.model", "fields": [
            {"name": "contract_id", "type": "Many2one", "comodel_name": "hr.contract"},
        ]}]}
        result = check_field_renames(spec, "17.0")
        assert result["status"] == "pass"
        assert result["violations"] == []
