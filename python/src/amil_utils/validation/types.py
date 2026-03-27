"""Immutable dataclasses for validation results."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class Violation:
    """A single pylint-odoo violation."""

    file: str
    line: int
    column: int
    rule_code: str
    symbol: str
    severity: str
    message: str
    suggestion: str = ""


@dataclass(frozen=True)
class OLSDiagnostic:
    """A single diagnostic from the odoo-ls language server."""

    file: str
    line: int
    column: int
    code: str
    message: str
    severity: int  # 1=Error, 2=Warning, 3=Info, 4=Hint

    @property
    def is_error(self) -> bool:
        """Return True when severity indicates an error (1)."""
        return self.severity == 1

    @property
    def is_warning(self) -> bool:
        """Return True when severity indicates a warning (2)."""
        return self.severity == 2


@dataclass(frozen=True)
class InstallResult:
    """Result of an Odoo module installation attempt."""

    success: bool
    log_output: str
    error_message: str = ""


@dataclass(frozen=True)
class TestResult:
    """Result of a single Odoo test case."""

    __test__ = False  # Prevent pytest collection warning

    test_name: str
    passed: bool
    error_message: str = ""
    duration_seconds: float = 0.0


@dataclass(frozen=True)
class ValidationReport:
    """Complete validation report for an Odoo module."""

    module_name: str
    pylint_violations: tuple[Violation, ...] = ()
    install_result: InstallResult | None = None
    test_results: tuple[TestResult, ...] = ()
    diagnosis: tuple[str, ...] = ()
    ols_diagnostics: tuple[OLSDiagnostic, ...] = ()
    docker_available: bool = True


@dataclass(frozen=True)
class Result(Generic[T]):
    """Unified result type wrapping success/failure with typed data.

    Use Result.ok(data) for success and Result.fail(*errors) for failure.
    """

    success: bool
    data: T | None = None
    errors: tuple[str, ...] = ()

    @staticmethod
    def ok(data: T) -> Result[T]:
        """Create a successful result with the given data."""
        return Result(success=True, data=data)

    @staticmethod
    def fail(*errors: str) -> Result[T]:
        """Create a failed result with one or more error messages."""
        return Result(success=False, data=None, errors=tuple(errors))
