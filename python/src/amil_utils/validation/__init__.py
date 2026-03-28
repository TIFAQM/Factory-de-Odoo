"""Validation infrastructure for Odoo module quality checks."""

from amil_utils.validation.module_name import (  # noqa: F401
    MODULE_NAME_RE,
    validate_module_name,
)
from amil_utils.validation.docker_runner import (  # noqa: F401
    check_docker_available,
    docker_install_module,
    docker_run_tests,
    get_compose_file,
)
from amil_utils.validation.error_patterns import (  # noqa: F401
    diagnose_errors,
    load_error_patterns,
)
from amil_utils.validation.log_parser import (  # noqa: F401
    extract_traceback,
    parse_install_log,
    parse_test_log,
)
from amil_utils.validation.pylint_runner import (  # noqa: F401
    parse_pylint_output,
    run_pylint_odoo,
)
from amil_utils.validation.report import (  # noqa: F401
    format_report_json,
    format_report_markdown,
)
from amil_utils.validation.odoo_ls_config import (  # noqa: F401
    find_python_path,
    generate_odools_toml,
)
from amil_utils.validation.semantic import (  # noqa: F401
    SemanticValidationResult,
    ValidationIssue,
    print_validation_report,
    semantic_validate,
    semantic_validate_full,
    semantic_validate_patterns,
)
from amil_utils.validation.types import (  # noqa: F401
    InstallResult,
    Result,
    TestResult,
    ValidationReport,
    Violation,
)

__all__ = [
    "MODULE_NAME_RE",
    "validate_module_name",
    "find_python_path",
    "generate_odools_toml",
    "SemanticValidationResult",
    "ValidationIssue",
    "InstallResult",
    "Result",
    "TestResult",
    "ValidationReport",
    "Violation",
    "check_docker_available",
    "diagnose_errors",
    "docker_install_module",
    "docker_run_tests",
    "extract_traceback",
    "format_report_json",
    "format_report_markdown",
    "get_compose_file",
    "load_error_patterns",
    "parse_install_log",
    "parse_pylint_output",
    "parse_test_log",
    "print_validation_report",
    "run_pylint_odoo",
    "semantic_validate",
    "semantic_validate_full",
    "semantic_validate_patterns",
]
