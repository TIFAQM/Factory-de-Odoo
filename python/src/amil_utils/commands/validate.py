"""Business logic for the ``validate`` CLI command.

Pure Python -- no Click dependency.  Returns structured data so callers
can decide how to display results.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

_logger = logging.getLogger(__name__)


def _cap_concurrency(requested: int) -> int:
    """Cap concurrency based on available system memory.

    Each Docker validation stack uses ~1-1.5GB RAM. Cap at available_memory / 1.5GB.
    Falls back to requested value if psutil is unavailable.
    """
    try:
        import psutil

        mem_gb = psutil.virtual_memory().available / (1024**3)
        max_safe = max(1, int(mem_gb / 1.5))
        if requested > max_safe:
            _logger.warning(
                "Capping Docker validation concurrency from %d to %d (%.1fGB RAM available)",
                requested,
                max_safe,
                mem_gb,
            )
            return max_safe
    except ImportError:
        pass
    return requested


def execute_validate_parallel(
    module_paths: list[str],
    *,
    pylint_only: bool = False,
    auto_fix: bool = False,
    pylintrc: str | None = None,
    concurrency: int = 3,
) -> list[dict[str, Any]]:
    """Validate multiple Odoo modules in parallel.

    Each module is validated independently using ``execute_validate``.
    Results are returned in the original submission order.

    Parameters
    ----------
    module_paths:
        List of module directory paths to validate.
    pylint_only:
        If True, skip Docker steps.
    auto_fix:
        If True, attempt auto-fix loops.
    pylintrc:
        Optional path to a pylintrc file.
    concurrency:
        Maximum number of parallel validations (default 3).

    Returns
    -------
    list[dict[str, Any]]
        One result dict per module, in the same order as *module_paths*.
    """
    if not module_paths:
        return []

    concurrency = _cap_concurrency(concurrency)

    results: dict[str, dict[str, Any]] = {}

    def _validate_one(mod_path: str) -> dict[str, Any]:
        return execute_validate(
            mod_path,
            pylint_only=pylint_only,
            auto_fix=auto_fix,
            pylintrc=pylintrc,
        )

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        future_to_path = {
            pool.submit(_validate_one, p): p for p in module_paths
        }
        for future in as_completed(future_to_path):
            mod = future_to_path[future]
            try:
                result = future.result()
                results[mod] = result
                status = "CLEAN" if not result.get("has_issues") else "ISSUES"
                _logger.info("Parallel validate %s: %s", mod, status)
            except Exception as exc:
                _logger.error("Parallel validate %s crashed: %s", mod, exc)
                results[mod] = {
                    "module_name": Path(mod).name,
                    "has_issues": True,
                    "error": str(exc),
                    "violations": (),
                    "auto_fix_count": 0,
                    "auto_fix_escalation": None,
                    "install_result": None,
                    "test_results": (),
                    "diagnosis": (),
                    "docker_available": False,
                    "report": None,
                }

    # Return in original submission order
    return [results[p] for p in module_paths]


def execute_validate(
    module_path: str,
    *,
    pylint_only: bool = False,
    auto_fix: bool = False,
    pylintrc: str | None = None,
) -> dict[str, Any]:
    """Validate an Odoo module against OCA quality standards.

    Returns a result dict with keys:
        - module_name: str
        - violations: tuple -- pylint violations
        - auto_fix_count: int -- number of auto-fixed violations
        - auto_fix_escalation: str | None -- escalation message
        - install_result: object | None -- Docker install result
        - test_results: tuple -- Docker test results
        - diagnosis: tuple[str, ...] -- error diagnosis strings
        - docker_available: bool
        - report: object -- ValidationReport instance
        - has_issues: bool -- True if any issues found
        - error: str | None -- error message if something failed early
    """
    from amil_utils.auto_fix import format_escalation, run_docker_fix_loop, run_pylint_fix_loop
    from amil_utils.validation import (
        ValidationReport,
        check_docker_available,
        diagnose_errors,
        docker_install_module,
        docker_run_tests,
        format_report_json,
        format_report_markdown,
        run_pylint_odoo,
    )

    result: dict[str, Any] = {
        "module_name": "",
        "violations": (),
        "auto_fix_count": 0,
        "auto_fix_escalation": None,
        "install_result": None,
        "test_results": (),
        "diagnosis": (),
        "docker_available": True,
        "report": None,
        "has_issues": False,
        "error": None,
        "format_report_json": format_report_json,
        "format_report_markdown": format_report_markdown,
    }

    mod_path = Path(module_path).resolve()

    # Validate manifest exists
    manifest = mod_path / "__manifest__.py"
    if not manifest.exists():
        result["error"] = f"Error: No __manifest__.py found in {mod_path}"
        return result

    module_name = mod_path.name
    result["module_name"] = module_name

    # Auto-detect .pylintrc-odoo in module directory if not provided
    pylintrc_path = Path(pylintrc) if pylintrc else None
    if pylintrc_path is None:
        candidate = mod_path / ".pylintrc-odoo"
        if candidate.exists():
            pylintrc_path = candidate

    # Step 1: Run pylint-odoo (with optional auto-fix loop)
    violations: tuple = ()
    if auto_fix:
        fix_result = run_pylint_fix_loop(mod_path, pylintrc_path=pylintrc_path)
        if fix_result.success:
            total_fixed, violations = fix_result.data
        else:
            result["auto_fix_escalation"] = f"Auto-fix error: {'; '.join(fix_result.errors)}"
            total_fixed, violations = 0, ()
        result["auto_fix_count"] = total_fixed
        if violations:
            result["auto_fix_escalation"] = format_escalation(violations)
    else:
        pylint_result = run_pylint_odoo(mod_path, pylintrc_path=pylintrc_path)
        if pylint_result.success:
            violations = pylint_result.data or ()
        else:
            result["auto_fix_escalation"] = f"Pylint error: {'; '.join(pylint_result.errors)}"
            violations = ()

    result["violations"] = violations

    install_result = None
    test_results: tuple = ()
    docker_available = True
    diagnosis: tuple[str, ...] = ()
    error_logs: list[str] = []

    if not pylint_only:
        # Step 2: Check Docker and run install
        docker_available = check_docker_available()
        result["docker_available"] = docker_available
        if docker_available:
            docker_result = docker_install_module(mod_path)
            if not docker_result.success:
                result["docker_error"] = f"Docker error: {'; '.join(docker_result.errors)}"
                install_result = None
            else:
                install_result = docker_result.data
            if install_result and install_result.log_output:
                error_logs.append(install_result.log_output)

            # Step 2b: Auto-fix Docker errors if --auto-fix enabled
            if auto_fix and install_result and not install_result.success and install_result.log_output:
                docker_fix_result = run_docker_fix_loop(
                    mod_path,
                    install_result.log_output,
                    revalidate_fn=lambda: docker_install_module(mod_path),
                )
                if docker_fix_result.success:
                    any_docker_fixed, remaining_errors = docker_fix_result.data
                else:
                    any_docker_fixed, remaining_errors = False, ""
                if any_docker_fixed:
                    result["docker_fix_applied"] = True
                    retry_result = docker_install_module(mod_path)
                    if retry_result.success:
                        install_result = retry_result.data
                    else:
                        result["docker_retry_error"] = f"Docker retry error: {'; '.join(retry_result.errors)}"
                        install_result = None
                    if install_result and install_result.log_output:
                        error_logs.append(install_result.log_output)
                    if remaining_errors and "iteration cap" in remaining_errors.lower():
                        result["docker_iteration_cap"] = remaining_errors

            # Step 3: Run tests if install succeeded
            if install_result and install_result.success:
                test_run_result = docker_run_tests(mod_path)
                if test_run_result.success:
                    test_results = test_run_result.data or ()
                else:
                    result["test_error"] = f"Test run error: {'; '.join(test_run_result.errors)}"
                    test_results = ()

            # Step 4: Diagnose any error logs
            combined_logs = "\n".join(error_logs)
            if combined_logs.strip():
                diagnosis = diagnose_errors(combined_logs)

    result["install_result"] = install_result
    result["test_results"] = test_results
    result["diagnosis"] = diagnosis

    # Build report
    report = ValidationReport(
        module_name=module_name,
        pylint_violations=violations,
        install_result=install_result,
        test_results=test_results,
        diagnosis=diagnosis,
        docker_available=docker_available,
    )
    result["report"] = report

    # Determine if there are issues
    has_issues = bool(violations) or (
        install_result is not None and not install_result.success
    ) or any(not tr.passed for tr in test_results)
    result["has_issues"] = has_issues

    return result
