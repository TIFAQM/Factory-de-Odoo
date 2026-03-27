"""pylint-odoo invocation and JSON2 output parsing.

Runs pylint with the pylint_odoo plugin on a given module path and returns
structured Violation objects parsed from JSON2 format output.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from pathlib import Path

from amil_utils.validation.types import Result, Violation

logger = logging.getLogger(__name__)

# Disabled pylint checks:
# - import-error: odoo is not importable outside an Odoo environment
# - missing-module-docstring: not required for Odoo modules
# - missing-class-docstring: Odoo models often self-document via _description
# - too-few-public-methods: Odoo models inherit plenty of methods from base
_DISABLED_CHECKS = "import-error,missing-module-docstring,missing-class-docstring,too-few-public-methods"


def parse_pylint_output(json_str: str) -> tuple[Violation, ...]:
    """Parse pylint JSON2 output into a tuple of Violation objects.

    Args:
        json_str: Raw JSON2 string from pylint stdout.

    Returns:
        Tuple of Violation instances. Empty tuple if input is empty or invalid.
    """
    if not json_str or not json_str.strip():
        return ()

    try:
        data = json.loads(json_str)
    except json.JSONDecodeError:
        logger.warning("Failed to parse pylint JSON output")
        return ()

    messages = data.get("messages", [])
    violations = tuple(
        Violation(
            file=msg.get("path", ""),
            line=msg.get("line", 0),
            column=msg.get("column", 0),
            rule_code=msg.get("messageId", ""),
            symbol=msg.get("symbol", ""),
            severity=msg.get("type", ""),
            message=msg.get("message", ""),
        )
        for msg in messages
    )
    return violations


def run_pylint_odoo(
    module_path: Path,
    *,
    pylintrc_path: Path | None = None,
    timeout: int = 120,
) -> Result[tuple[Violation, ...]]:
    """Run pylint-odoo on a module path and return parsed violations.

    Args:
        module_path: Path to the Odoo module directory.
        pylintrc_path: Optional path to a .pylintrc-odoo config file.
        timeout: Subprocess timeout in seconds (default 120).

    Returns:
        Result.ok(violations) on success, Result.fail(message) on error/timeout.
    """
    cmd = [
        sys.executable,
        "-m",
        "pylint",
        f"--load-plugins=pylint_odoo",
        f"--output-format=json2",
        f"--disable={_DISABLED_CHECKS}",
        str(module_path),
    ]

    if pylintrc_path is not None and pylintrc_path.exists():
        cmd.insert(3, f"--rcfile={pylintrc_path}")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        # pylint returns non-zero on findings (not errors), so we don't check returncode
        return Result.ok(parse_pylint_output(result.stdout))
    except subprocess.TimeoutExpired:
        logger.warning("pylint-odoo timed out after %d seconds for %s", timeout, module_path)
        return Result.fail(f"pylint-odoo timed out after {timeout}s for {module_path}")
    except Exception as exc:
        logger.warning("pylint-odoo failed for %s: %s", module_path, exc)
        logger.debug("Full traceback:", exc_info=True)
        return Result.fail(f"pylint-odoo failed for {module_path}: {exc}")
