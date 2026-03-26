"""Tests for deprecation markers on legacy coherence modules."""
from __future__ import annotations


class TestDeprecationMarkers:
    """Verify __deprecated__ attribute exists on superseded modules."""

    def test_coherence_marked_deprecated(self) -> None:
        from amil_utils.orchestrator import coherence

        assert coherence.__deprecated__ is True
        assert "odoo-ls" in coherence._DEPRECATION_NOTICE

    def test_provisional_registry_marked_deprecated(self) -> None:
        from amil_utils.orchestrator import provisional_registry

        assert provisional_registry.__deprecated__ is True
        assert "odoo-ls" in provisional_registry._DEPRECATION_NOTICE

    def test_circular_dep_marked_deprecated(self) -> None:
        from amil_utils.orchestrator import circular_dep

        assert circular_dep.__deprecated__ is True
        assert "odoo-ls" in circular_dep._DEPRECATION_NOTICE
