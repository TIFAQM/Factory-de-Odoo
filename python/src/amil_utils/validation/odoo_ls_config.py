"""Generate odools.toml configuration for the odoo-ls language server.

The odoo-ls VS Code extension needs a ``odools.toml`` that tells it where
to find the Odoo source tree, addon paths, and which Python interpreter
to use.  This module generates that file with sane defaults for the
Factory de Odoo pipeline.

Key design decisions
--------------------
* **Explicit Python path** -- We search for ``python3.12`` / ``python3.13``
  first, *not* the generic ``python3``, because the system ``python3`` may
  resolve to 3.14+ which is outside the project's supported range.
* **addons_output_dir in addons_paths** -- Without it, odoo-ls cannot
  analyse generated modules and produces zero diagnostics.
"""
from __future__ import annotations

import shutil
from pathlib import Path


def find_python_path() -> Path | None:
    """Find a Python 3.12 or 3.13 interpreter (NOT 3.14+).

    Searches for ``python3.12``, ``python3.13`` explicitly to avoid
    picking up incompatible versions via PATH.  Falls back to generic
    ``python3`` only when neither versioned binary is found.
    """
    for name in ("python3.12", "python3.13"):
        found = shutil.which(name)
        if found:
            return Path(found)
    # Fallback: try python3 but caller should be aware this may be 3.14+
    found = shutil.which("python3")
    if found:
        return Path(found)
    return None


def generate_odools_toml(
    *,
    output_path: Path,
    odoo_source_path: Path,
    addons_output_dir: Path,
    python_path: Path | None = None,
    profile_name: str = "factory",
) -> Path:
    """Generate an ``odools.toml`` file for the odoo-ls language server.

    Parameters
    ----------
    output_path:
        Where to write the TOML file.
    odoo_source_path:
        Path to the Odoo source tree (e.g. ``/opt/odoo/19.0``).
    addons_output_dir:
        Directory where generated modules are written.
        **Must** be included in ``addons_paths`` for odoo-ls to analyse them.
    python_path:
        Explicit Python interpreter path.  When *None*, :func:`find_python_path`
        is called to auto-detect ``python3.12``.
    profile_name:
        Name for the ``[[config]]`` profile section.

    Returns
    -------
    Path
        The *output_path* that was written.
    """
    if python_path is None:
        python_path = find_python_path()
    python_line = str(python_path) if python_path else "/usr/bin/python3.12"

    odoo_addons = odoo_source_path / "addons"
    odoo_core_addons = odoo_source_path / "odoo" / "addons"

    content = (
        f'[[config]]\n'
        f'name = "{profile_name}"\n'
        f'odoo_path = "{odoo_source_path}"\n'
        f'addons_paths = [\n'
        f'    "{odoo_addons}",\n'
        f'    "{odoo_core_addons}",\n'
        f'    "{addons_output_dir}",\n'
        f']\n'
        f'python_path = "{python_line}"\n'
        f'diag_missing_imports = "All"\n'
        f'refresh_mode = "off"\n'
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding="utf-8")
    return output_path
