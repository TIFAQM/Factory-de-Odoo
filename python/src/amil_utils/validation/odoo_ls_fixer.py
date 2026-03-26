"""Odoo-LS auto-fix patterns for fixable diagnostic codes.

Parses odoo-ls diagnostics and applies mechanical fixes:
- OLS30003: adds missing module to ``__manifest__.py`` ``depends`` list.

The :func:`run_ols_fix_loop` function iterates validate -> fix -> re-validate
up to *max_iterations* times, returning the total number of fixes applied.
"""

from __future__ import annotations

import ast
import logging
import re
from pathlib import Path

from amil_utils.validation.odoo_ls_validator import OLS_FIXABLE_CODES
from amil_utils.validation.types import OLSDiagnostic

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

_MISSING_DEP_RE = re.compile(r"[Mm]issing dependency '(\w+)'")

# ---------------------------------------------------------------------------
# Manifest serialization
# ---------------------------------------------------------------------------


def _serialize_manifest(manifest: dict) -> str:
    """Re-serialize a manifest dict to a human-readable Python literal.

    Produces a clean ``repr``-style dict that ``ast.literal_eval`` can
    round-trip.  Lists are formatted one-item-per-line when they contain
    more than two elements.
    """
    lines = ["{"]
    items = list(manifest.items())
    for idx, (key, value) in enumerate(items):
        trailing = "," if idx < len(items) - 1 else ","
        if isinstance(value, list):
            if len(value) <= 2:
                lines.append(f"    {key!r}: {value!r}{trailing}")
            else:
                lines.append(f"    {key!r}: [")
                for vi, item in enumerate(value):
                    item_trailing = "," if vi < len(value) - 1 else ","
                    lines.append(f"        {item!r}{item_trailing}")
                lines.append(f"    ]{trailing}")
        elif isinstance(value, bool):
            lines.append(f"    {key!r}: {value!r}{trailing}")
        else:
            lines.append(f"    {key!r}: {value!r}{trailing}")
    lines.append("}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Fix: OLS30003 — missing manifest dependency
# ---------------------------------------------------------------------------


def fix_missing_manifest_depends(
    module_dir: Path,
    diag: OLSDiagnostic,
) -> bool:
    """Add the missing dependency from an OLS30003 diagnostic to the manifest.

    Returns ``True`` if the manifest was modified, ``False`` otherwise
    (e.g. dependency already present, no manifest file, or regex miss).
    """
    manifest_path = module_dir / "__manifest__.py"
    if not manifest_path.exists():
        logger.debug("No __manifest__.py in %s — skipping fix", module_dir)
        return False

    match = _MISSING_DEP_RE.search(diag.message)
    if match is None:
        logger.debug(
            "Could not extract module name from OLS30003 message: %s",
            diag.message,
        )
        return False

    dep_name = match.group(1)

    try:
        manifest = ast.literal_eval(manifest_path.read_text())
    except (SyntaxError, ValueError) as exc:
        logger.warning("Failed to parse %s: %s", manifest_path, exc)
        return False

    depends = manifest.get("depends")
    if not isinstance(depends, list):
        logger.debug("No 'depends' list in manifest — skipping fix")
        return False

    if dep_name in depends:
        logger.debug("'%s' already in depends — no change needed", dep_name)
        return False

    # Create new list (immutable pattern) and write back
    new_depends = [*depends, dep_name]
    new_manifest = {**manifest, "depends": new_depends}
    manifest_path.write_text(_serialize_manifest(new_manifest))
    logger.info("Added '%s' to depends in %s", dep_name, manifest_path)
    return True


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

_FIX_DISPATCH: dict[str, type[object] | None] = {
    "OLS30003": None,  # sentinel; handled inline below
}


def dispatch_ols_fix(module_dir: Path, diag: OLSDiagnostic) -> bool:
    """Route a diagnostic to the appropriate fixer by code.

    Returns ``True`` if a fix was applied, ``False`` otherwise.
    """
    if diag.code == "OLS30003":
        return fix_missing_manifest_depends(module_dir, diag)

    logger.debug("No auto-fix registered for code %s", diag.code)
    return False


# ---------------------------------------------------------------------------
# Fix loop
# ---------------------------------------------------------------------------


def run_ols_fix_loop(
    diagnostics_fn: object,
    module_dir: Path,
    max_iterations: int = 3,
) -> int:
    """Iterate: validate, fix fixable diagnostics, re-validate.

    *diagnostics_fn* is a callable that returns a sequence of
    :class:`OLSDiagnostic` for the given *module_dir*.

    Returns the total number of fixes applied across all iterations.
    """
    total_fixes = 0

    for iteration in range(1, max_iterations + 1):
        diagnostics = diagnostics_fn(module_dir)  # type: ignore[operator]
        fixable = [d for d in diagnostics if d.code in OLS_FIXABLE_CODES]

        if not fixable:
            logger.info(
                "No fixable diagnostics at iteration %d — stopping",
                iteration,
            )
            break

        applied = 0
        for diag in fixable:
            if dispatch_ols_fix(module_dir, diag):
                applied += 1

        total_fixes += applied
        logger.info(
            "Iteration %d: applied %d fixes (%d total)",
            iteration,
            applied,
            total_fixes,
        )

        if applied == 0:
            logger.info("No fixes applied this iteration — stopping")
            break

    return total_fixes
