"""Canonical Odoo module name validation.

Single source of truth for the module name regex pattern and validation
function. All consumers should import from here (or from the validation
package re-export) rather than defining their own patterns.
"""

from __future__ import annotations

import re

# Canonical Odoo module name pattern: lowercase letter followed by lowercase
# letters, digits, or underscores. Anchored for use with both re.match()
# and re.fullmatch().
MODULE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def validate_module_name(name: str) -> str | None:
    """Validate an Odoo module name.

    Returns None if valid, or an error message string if invalid.
    """
    if not MODULE_NAME_RE.fullmatch(name):
        return (
            f"Invalid module name '{name}': must start with a lowercase letter "
            f"and contain only lowercase letters, digits, and underscores"
        )
    return None
