"""Tests for orchestrator dependency_graph module."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import amil_utils.orchestrator.dependency_graph as dg_module
from amil_utils.orchestrator.dependency_graph import (
    _load_external_module_names,
    compute_tiers,
    dep_graph_build,
    dep_graph_can_generate,
    dep_graph_order,
    dep_graph_tiers,
    topo_sort,
    validate_external_dependency,
)


class TestTopoSort:
    def test_simple_chain(self) -> None:
        modules = {
            "a": {"depends": []},
            "b": {"depends": ["a"]},
            "c": {"depends": ["b"]},
        }
        order = topo_sort(modules)
        assert order.index("a") < order.index("b") < order.index("c")

    def test_diamond(self) -> None:
        modules = {
            "a": {"depends": []},
            "b": {"depends": ["a"]},
            "c": {"depends": ["a"]},
            "d": {"depends": ["b", "c"]},
        }
        order = topo_sort(modules)
        assert order.index("a") < order.index("b")
        assert order.index("a") < order.index("c")
        assert order.index("b") < order.index("d")

    def test_detects_cycle(self) -> None:
        modules = {
            "a": {"depends": ["b"]},
            "b": {"depends": ["a"]},
        }
        with pytest.raises(ValueError, match="Circular dependency"):
            topo_sort(modules)

    def test_empty(self) -> None:
        assert topo_sort({}) == []

    def test_no_deps(self) -> None:
        modules = {"x": {"depends": []}, "y": {"depends": []}}
        order = topo_sort(modules)
        assert set(order) == {"x", "y"}


class TestComputeTiers:
    def test_assigns_tiers(self) -> None:
        modules = {
            "base": {"depends": []},
            "core": {"depends": ["base"]},
            "app": {"depends": ["core"]},
        }
        result = compute_tiers(modules)
        assert result["depths"]["base"] == 0
        assert result["depths"]["core"] == 1
        assert result["depths"]["app"] == 2

    def test_tier_labels(self) -> None:
        modules = {
            "a": {"depends": []},
            "b": {"depends": ["a"]},
        }
        result = compute_tiers(modules)
        assert "foundation" in result["tiers"]
        assert "a" in result["tiers"]["foundation"]


class TestDepGraphBuild:
    def test_builds_from_status(self, tmp_path: Path) -> None:
        planning = tmp_path / ".planning"
        planning.mkdir()
        (planning / "module_status.json").write_text(json.dumps({
            "_meta": {"version": 1},
            "modules": {
                "mod_a": {"status": "planned", "depends": []},
                "mod_b": {"status": "planned", "depends": ["mod_a"]},
            },
            "tiers": {},
        }))
        result = dep_graph_build(tmp_path)
        assert "mod_a" in result["modules"]
        assert result["modules"]["mod_b"]["depends"] == ["mod_a"]


class TestDepGraphOrder:
    def test_returns_order(self, tmp_path: Path) -> None:
        planning = tmp_path / ".planning"
        planning.mkdir()
        (planning / "module_status.json").write_text(json.dumps({
            "_meta": {"version": 1},
            "modules": {
                "mod_a": {"status": "planned", "depends": []},
                "mod_b": {"status": "planned", "depends": ["mod_a"]},
            },
            "tiers": {},
        }))
        order = dep_graph_order(tmp_path)
        assert order.index("mod_a") < order.index("mod_b")


class TestDepGraphTiers:
    def test_returns_tiers(self, tmp_path: Path) -> None:
        planning = tmp_path / ".planning"
        planning.mkdir()
        (planning / "module_status.json").write_text(json.dumps({
            "_meta": {"version": 1},
            "modules": {
                "mod_a": {"status": "planned", "depends": []},
                "mod_b": {"status": "planned", "depends": ["mod_a"]},
            },
            "tiers": {},
        }))
        result = dep_graph_tiers(tmp_path)
        assert "tiers" in result
        assert "depths" in result


class TestTopoSortPhantomDependency:
    def test_topo_sort_rejects_phantom_dependency(self) -> None:
        """A module depending on a name not in the dict should raise ValueError."""
        modules = {
            "mod_a": {"depends": ["mod_b"]},
            # mod_b is NOT in the dict -- phantom
        }
        with pytest.raises(ValueError, match="Unknown dependency 'mod_b'"):
            topo_sort(modules)

    def test_topo_sort_lenient_mode_warns_on_phantom(self) -> None:
        """strict=False should warn instead of raising."""
        modules = {
            "mod_a": {"depends": ["mod_b"]},
        }
        result = topo_sort(modules, strict=False)
        # Should complete without error; phantom NOT in result
        assert "mod_b" not in result
        assert "mod_a" in result

    def test_topo_sort_phantom_error_includes_referrer(self) -> None:
        """The error message should include the referencing module name."""
        modules = {
            "mod_x": {"depends": ["ghost"]},
        }
        with pytest.raises(ValueError, match="referenced by mod_x"):
            topo_sort(modules)

    def test_topo_sort_strict_default_is_true(self) -> None:
        """Default strict=True should raise on phantom deps."""
        modules = {
            "mod_a": {"depends": ["missing"]},
        }
        with pytest.raises(ValueError, match="Unknown dependency 'missing'"):
            topo_sort(modules)


class TestDepGraphCanGenerate:
    def test_can_generate_when_deps_ready(self, tmp_path: Path) -> None:
        planning = tmp_path / ".planning"
        planning.mkdir()
        (planning / "module_status.json").write_text(json.dumps({
            "_meta": {"version": 1},
            "modules": {
                "mod_a": {"status": "generated", "depends": []},
                "mod_b": {"status": "planned", "depends": ["mod_a"]},
            },
            "tiers": {},
        }))
        result = dep_graph_can_generate(tmp_path, "mod_b")
        assert result["can_generate"] is True

    def test_blocked_when_deps_not_ready(self, tmp_path: Path) -> None:
        planning = tmp_path / ".planning"
        planning.mkdir()
        (planning / "module_status.json").write_text(json.dumps({
            "_meta": {"version": 1},
            "modules": {
                "mod_a": {"status": "planned", "depends": []},
                "mod_b": {"status": "planned", "depends": ["mod_a"]},
            },
            "tiers": {},
        }))
        result = dep_graph_can_generate(tmp_path, "mod_b")
        assert result["can_generate"] is False
        assert len(result["blocked_by"]) == 1


@pytest.fixture(autouse=False)
def _clear_caches():
    """Clear module-level caches before and after each version-aware test."""
    dg_module._external_modules_cache.clear()
    dg_module._renames_cache = None
    yield
    dg_module._external_modules_cache.clear()
    dg_module._renames_cache = None


class TestVersionAwareExternalModules:
    """Tests for version-aware external module list (C3)."""

    def test_hr_contract_not_in_external_modules_v19(
        self, _clear_caches: None,
    ) -> None:
        """hr_contract was renamed to hr in Odoo 19, so it should be excluded."""
        external = _load_external_module_names("19.0")
        assert "hr_contract" not in external

    def test_hr_contract_in_external_modules_v17(
        self, _clear_caches: None,
    ) -> None:
        """hr_contract exists in Odoo 17, so it should be included."""
        external = _load_external_module_names("17.0")
        assert "hr_contract" in external

    def test_bus_not_in_external_modules_v19(
        self, _clear_caches: None,
    ) -> None:
        """bus was renamed to mail in Odoo 19, so it should be excluded."""
        external = _load_external_module_names("19.0")
        assert "bus" not in external

    def test_bus_in_external_modules_v17(
        self, _clear_caches: None,
    ) -> None:
        """bus exists in Odoo 17, so it should be included."""
        external = _load_external_module_names("17.0")
        assert "bus" in external

    def test_base_always_present(self, _clear_caches: None) -> None:
        """Core modules like 'base' should always be present."""
        for version in ("17.0", "19.0"):
            external = _load_external_module_names(version)
            assert "base" in external

    def test_unknown_version_no_renames(self, _clear_caches: None) -> None:
        """Unknown version should apply no renames — all modules present."""
        external = _load_external_module_names("99.0")
        # hr_contract should still be present since no renames for 99.0
        assert "hr_contract" in external
        assert "bus" in external


class TestValidateExternalDependency:
    """Tests for validate_external_dependency."""

    def test_renamed_dep_returns_warning(self, _clear_caches: None) -> None:
        result = validate_external_dependency("hr_contract", "19.0")
        assert result is not None
        assert result["dependency"] == "hr_contract"
        assert result["renamed_to"] == "hr"
        assert result["version"] == "19.0"
        assert "renamed" in result["message"]

    def test_valid_dep_returns_none(self, _clear_caches: None) -> None:
        result = validate_external_dependency("sale", "19.0")
        assert result is None

    def test_merged_dep_returns_warning(self, _clear_caches: None) -> None:
        result = validate_external_dependency("sale_async_emails", "19.0")
        assert result is not None
        assert result["renamed_to"] == "sale"

    def test_unknown_version_returns_none(self, _clear_caches: None) -> None:
        """Unknown version has no renames, so everything should be valid."""
        result = validate_external_dependency("hr_contract", "99.0")
        assert result is None

    def test_valid_dep_for_old_version(self, _clear_caches: None) -> None:
        """hr_contract is not renamed in 17.0, should return None."""
        result = validate_external_dependency("hr_contract", "17.0")
        assert result is None


class TestTopoSortWithVersion:
    """Tests for topo_sort with odoo_version parameter."""

    def test_topo_sort_works_with_version_param(
        self, _clear_caches: None,
    ) -> None:
        """topo_sort should still produce correct order with odoo_version."""
        modules = {
            "a": {"depends": []},
            "b": {"depends": ["a"]},
            "c": {"depends": ["b"]},
        }
        order = topo_sort(modules, odoo_version="19.0")
        assert order.index("a") < order.index("b") < order.index("c")

    def test_topo_sort_default_version_is_19(
        self, _clear_caches: None,
    ) -> None:
        """Default odoo_version should be '19.0'."""
        modules = {"x": {"depends": []}}
        # Should not raise — just verify it runs with default
        order = topo_sort(modules)
        assert "x" in order

    def test_topo_sort_skips_renamed_external_dep(
        self, _clear_caches: None,
    ) -> None:
        """A module depending on 'hr' (valid in 19.0) should sort fine."""
        modules = {
            "my_mod": {"depends": ["hr"]},
        }
        order = topo_sort(modules, odoo_version="19.0")
        assert "my_mod" in order
        # hr is external, not in result
        assert "hr" not in order
