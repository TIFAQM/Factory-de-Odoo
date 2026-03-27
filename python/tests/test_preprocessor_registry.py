"""Tests for the preprocessor decorator-based registry.

Phase 45: Registry mechanics, auto-discovery, ordering, and count tests.
"""

from __future__ import annotations

from typing import Any

import pytest

from amil_utils.preprocessors._registry import (
    PreprocessorFn,
    clear_registry,
    get_registered_preprocessors,
    register_preprocessor,
)


@pytest.fixture(autouse=True)
def _isolated_registry():
    """Clear registry before and after each test to prevent cross-contamination."""
    clear_registry()
    yield
    clear_registry()


# ---------------------------------------------------------------------------
# Registry mechanics tests
# ---------------------------------------------------------------------------


def test_register_adds_to_registry():
    """Registering a function makes it appear in get_registered_preprocessors."""

    @register_preprocessor(order=10)
    def _dummy(spec: dict[str, Any]) -> dict[str, Any]:
        return spec

    entries = get_registered_preprocessors()
    assert len(entries) == 1
    assert entries[0][2] is _dummy


def test_ordering_by_order_param():
    """Entries are returned sorted by their order parameter."""

    @register_preprocessor(order=30)
    def _third(spec: dict[str, Any]) -> dict[str, Any]:
        return spec

    @register_preprocessor(order=10)
    def _first(spec: dict[str, Any]) -> dict[str, Any]:
        return spec

    @register_preprocessor(order=20)
    def _second(spec: dict[str, Any]) -> dict[str, Any]:
        return spec

    entries = get_registered_preprocessors()
    assert len(entries) == 3
    orders = [e[0] for e in entries]
    assert orders == [10, 20, 30]
    assert entries[0][2] is _first
    assert entries[1][2] is _second
    assert entries[2][2] is _third


def test_clear_registry():
    """clear_registry empties the registry."""

    @register_preprocessor(order=1)
    def _dummy(spec: dict[str, Any]) -> dict[str, Any]:
        return spec

    assert len(get_registered_preprocessors()) == 1
    clear_registry()
    assert len(get_registered_preprocessors()) == 0


def test_default_name_uses_function_name():
    """Without name= argument, the entry name is the function's __name__."""

    @register_preprocessor(order=1)
    def _my_preprocessor(spec: dict[str, Any]) -> dict[str, Any]:
        return spec

    entries = get_registered_preprocessors()
    assert entries[0][1] == "_my_preprocessor"


def test_custom_name_overrides():
    """Providing name= overrides the default function name."""

    @register_preprocessor(order=1, name="custom_name")
    def _my_preprocessor(spec: dict[str, Any]) -> dict[str, Any]:
        return spec

    entries = get_registered_preprocessors()
    assert entries[0][1] == "custom_name"


def test_decorator_returns_original_function():
    """The decorator is transparent -- returns the original function unchanged."""

    @register_preprocessor(order=1)
    def _my_fn(spec: dict[str, Any]) -> dict[str, Any]:
        return spec

    # _my_fn should still be the original function object
    assert callable(_my_fn)
    result = _my_fn({"key": "value"})
    assert result == {"key": "value"}


def test_duplicate_orders_both_kept():
    """Duplicate order values are both kept (no deduplication)."""

    @register_preprocessor(order=10)
    def _first(spec: dict[str, Any]) -> dict[str, Any]:
        return spec

    @register_preprocessor(order=10)
    def _second(spec: dict[str, Any]) -> dict[str, Any]:
        return spec

    entries = get_registered_preprocessors()
    assert len(entries) == 2


# ---------------------------------------------------------------------------
# Integration tests (require full package import to trigger auto-discovery)
# ---------------------------------------------------------------------------


class TestRegistryIntegration:
    """Tests that require the full preprocessors package to be imported.

    These tests need the registry populated by the real decorators.
    We override the autouse clear fixture and instead reload all modules
    to repopulate the registry cleanly.
    """

    @pytest.fixture(autouse=True)
    def _isolated_registry(self):
        """Override the module-level fixture: reload submodules to repopulate."""
        import importlib
        import sys

        clear_registry()

        # Reload all preprocessor submodules so decorators fire again
        submodule_names = [
            name for name in sorted(sys.modules)
            if name.startswith("amil_utils.preprocessors.")
            and not name.endswith("._registry")
        ]
        for name in submodule_names:
            importlib.reload(sys.modules[name])
        yield
        clear_registry()

    def test_registry_count_is_22(self):
        """After full import, exactly 22 preprocessors are registered."""
        entries = get_registered_preprocessors()
        assert len(entries) == 22, (
            f"Expected 22 registered preprocessors, got {len(entries)}: "
            f"{[(e[0], e[1]) for e in entries]}"
        )

    def test_registry_order_matches_pipeline(self):
        """The order sequence matches the expected pipeline order."""
        entries = get_registered_preprocessors()
        orders = [e[0] for e in entries]
        expected = [5, 7, 10, 12, 15, 22, 25, 27, 28, 30, 40, 50, 52, 60, 70, 80, 85, 90, 95, 96, 100, 105]
        assert orders == expected, f"Expected {expected}, got {orders}"

    def test_auto_discovery_finds_all_modules(self):
        """All non-underscore .py files in preprocessors/ are importable."""
        import pkgutil
        from pathlib import Path

        import amil_utils.preprocessors as pkg

        pkg_path = str(Path(pkg.__file__).parent)
        discovered = [
            name
            for _finder, name, _ispkg in pkgutil.iter_modules([pkg_path])
            if not name.startswith("_")
        ]
        assert len(discovered) == 22, (
            f"Expected 22 discoverable modules, got {len(discovered)}: {discovered}"
        )

    def test_run_preprocessors_callable(self):
        """run_preprocessors is importable and callable."""
        from amil_utils.preprocessors import run_preprocessors

        assert callable(run_preprocessors)
        # Minimal smoke test: empty spec passes through
        result = run_preprocessors({"module_name": "test", "models": []})
        assert result["module_name"] == "test"
