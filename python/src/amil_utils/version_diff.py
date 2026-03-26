"""Version upgrade diff tool -- renders a spec at two Odoo versions and diffs.

Given a module specification, this tool renders the module at two different
Odoo versions and produces unified diffs for every file that changed. This
helps developers understand what changes when migrating a module between
Odoo versions (e.g., 17.0 -> 19.0).
"""
from __future__ import annotations

import difflib
from pathlib import Path
from typing import Any


def compute_version_diff(
    *,
    spec: dict[str, Any],
    from_version: str,
    to_version: str,
    output_base: Path,
) -> list[dict[str, str]]:
    """Render module at two versions and return unified diffs.

    Parameters
    ----------
    spec:
        Module specification dict (module_name, models, depends, etc.).
    from_version:
        Source Odoo version (e.g., ``"17.0"``).
    to_version:
        Target Odoo version (e.g., ``"19.0"``).
    output_base:
        Temporary directory for rendering outputs. Two subdirectories
        will be created: ``from_<version>`` and ``to_<version>``.

    Returns
    -------
    list[dict[str, str]]
        Each entry has ``file`` (relative path within the module) and
        ``diff`` (unified diff string). Empty list if no differences.
    """
    from amil_utils.renderer import get_template_dir, render_module

    template_dir = get_template_dir()
    module_name = spec.get("module_name", "module")

    # Render at from_version
    from_dir = output_base / f"from_{from_version}"
    from_spec = {**spec, "odoo_version": from_version}
    render_module(
        from_spec,
        template_dir,
        from_dir,
        skip_semantic_validation=True,
    )

    # Render at to_version
    to_dir = output_base / f"to_{to_version}"
    to_spec = {**spec, "odoo_version": to_version}
    render_module(
        to_spec,
        template_dir,
        to_dir,
        skip_semantic_validation=True,
    )

    # Collect files from both renders
    from_root = from_dir / module_name
    to_root = to_dir / module_name

    from_files = _collect_files(from_root) if from_root.exists() else {}
    to_files = _collect_files(to_root) if to_root.exists() else {}

    # Diff all files present in either version
    all_paths = sorted(set(from_files.keys()) | set(to_files.keys()))
    diffs: list[dict[str, str]] = []

    for rel_path in all_paths:
        old_content = from_files.get(rel_path, "")
        new_content = to_files.get(rel_path, "")
        if old_content == new_content:
            continue
        diff_lines = difflib.unified_diff(
            old_content.splitlines(keepends=True),
            new_content.splitlines(keepends=True),
            fromfile=f"{from_version}/{rel_path}",
            tofile=f"{to_version}/{rel_path}",
        )
        diff_text = "".join(diff_lines)
        if diff_text:
            diffs.append({"file": rel_path, "diff": diff_text})

    return diffs


def _collect_files(root: Path) -> dict[str, str]:
    """Collect all text files under *root* as ``{relative_path: content}``.

    Binary files are silently skipped.
    """
    files: dict[str, str] = {}
    for f in sorted(root.rglob("*")):
        if f.is_file():
            rel = str(f.relative_to(root))
            try:
                files[rel] = f.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue  # Skip binary files
    return files
