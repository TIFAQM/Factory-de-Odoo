"""Tests for OLSDiagnostic type."""

from __future__ import annotations

import dataclasses

import pytest

from amil_utils.validation.types import OLSDiagnostic, ValidationReport


class TestOLSDiagnostic:
    """Tests for the OLSDiagnostic dataclass."""

    def test_create(self) -> None:
        """OLSDiagnostic with all fields creates correctly."""
        d = OLSDiagnostic(
            file="models/employee.py",
            line=10,
            column=4,
            code="OLS30001",
            message="Unknown model",
            severity=1,
        )
        assert d.code == "OLS30001"
        assert d.severity == 1

    def test_is_error(self) -> None:
        """Severity 1 is an error."""
        d = OLSDiagnostic(
            file="x.py",
            line=1,
            column=0,
            code="OLS30001",
            message="err",
            severity=1,
        )
        assert d.is_error is True
        assert d.is_warning is False

    def test_is_warning(self) -> None:
        """Severity 2 is a warning."""
        d = OLSDiagnostic(
            file="x.py",
            line=1,
            column=0,
            code="OLS10001",
            message="warn",
            severity=2,
        )
        assert d.is_error is False
        assert d.is_warning is True

    def test_is_info(self) -> None:
        """Severity 3 is info (neither error nor warning)."""
        d = OLSDiagnostic(
            file="x.py",
            line=1,
            column=0,
            code="OLS99999",
            message="info",
            severity=3,
        )
        assert d.is_error is False
        assert d.is_warning is False

    def test_frozen(self) -> None:
        """OLSDiagnostic is frozen -- assigning raises FrozenInstanceError."""
        d = OLSDiagnostic(
            file="x.py",
            line=1,
            column=0,
            code="OLS30001",
            message="test",
            severity=1,
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            d.code = "changed"  # type: ignore[misc]


class TestValidationReportOLS:
    """Tests for OLS diagnostics on ValidationReport."""

    def test_ols_diagnostics_default_empty(self) -> None:
        """ols_diagnostics defaults to empty tuple."""
        r = ValidationReport(module_name="test")
        assert r.ols_diagnostics == ()

    def test_ols_diagnostics_stored(self) -> None:
        """ols_diagnostics stores provided diagnostics."""
        d = OLSDiagnostic(
            file="x.py",
            line=1,
            column=0,
            code="OLS30001",
            message="test",
            severity=1,
        )
        r = ValidationReport(module_name="test", ols_diagnostics=(d,))
        assert len(r.ols_diagnostics) == 1
        assert r.ols_diagnostics[0].code == "OLS30001"
