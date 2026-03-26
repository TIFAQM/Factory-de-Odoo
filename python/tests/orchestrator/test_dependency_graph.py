"""Tests for orchestrator dependency_graph module."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from amil_utils.orchestrator.dependency_graph import (
    compute_tiers,
    dep_graph_build,
    dep_graph_can_generate,
    dep_graph_order,
    dep_graph_tiers,
    topo_sort,
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
