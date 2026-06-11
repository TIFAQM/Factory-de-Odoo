#!/usr/bin/env python3
# scripts/extract_odoo_models.py
"""Extract Odoo model/field definitions from an Odoo source checkout.

Regenerates python/src/amil_utils/data/known_odoo_models.json (full model
coverage for comodel validation / depends inference) and
standard_odoo_modules.json (stock-module list for the ERP decomposer).

Usage:
    python3 scripts/extract_odoo_models.py \
        --source tools/odoo-source/19.0 \
        --out python/src/amil_utils/data/known_odoo_models.json \
        --modules-out python/src/amil_utils/data/standard_odoo_modules.json
"""
from __future__ import annotations

import argparse
import ast
import datetime
import json
from pathlib import Path

ABSTRACT_BASES = {"AbstractModel"}
MODEL_BASES = {"Model", "TransientModel", "AbstractModel"}


def _module_name(py_file: Path, root: Path) -> str:
    """Addon directory name owning this file (child of an addons dir)."""
    rel = py_file.relative_to(root)
    parts = rel.parts
    for anchor in ("addons",):
        if anchor in parts:
            idx = len(parts) - 1 - parts[::-1].index(anchor)
            if idx + 1 < len(parts):
                return parts[idx + 1]
    return parts[0]


def _const_str(node: ast.AST) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _field_from_call(call: ast.Call) -> dict | None:
    """fields.Char(...) / fields.Many2one("res.partner", ...) -> field dict."""
    func = call.func
    if not (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)
            and func.value.id == "fields"):
        return None
    entry: dict = {"type": func.attr}
    comodel = None
    if call.args:
        comodel = _const_str(call.args[0])
    for kw in call.keywords:
        if kw.arg == "comodel_name":
            comodel = _const_str(kw.value)
    if func.attr in ("Many2one", "One2many", "Many2many") and comodel:
        entry["comodel_name"] = comodel
    return entry


def _models_in_class(cls: ast.ClassDef) -> tuple[str | None, bool, dict, list[str]]:
    """Return (_name, is_mixin, fields, inherits_models) for a models.* class.

    *inherits_models* are model names listed in ``_inherits`` (dict keys) whose
    fields should be virtually merged into this model.
    """
    is_model = is_mixin = False
    for base in cls.bases:
        if isinstance(base, ast.Attribute) and base.attr in MODEL_BASES:
            is_model = True
            is_mixin = base.attr in ABSTRACT_BASES
    if not is_model:
        return None, False, {}, []
    name = None
    fields: dict = {}
    inherits_models: list[str] = []
    for stmt in cls.body:
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 \
                and isinstance(stmt.targets[0], ast.Name):
            target = stmt.targets[0].id
            if target == "_name":
                name = _const_str(stmt.value)
            elif target == "_inherits" and isinstance(stmt.value, ast.Dict):
                # _inherits = {'hr.version': 'version_id'} — collect the model keys
                for key in stmt.value.keys:
                    val = _const_str(key)
                    if val:
                        inherits_models.append(val)
            elif isinstance(stmt.value, ast.Call):
                field = _field_from_call(stmt.value)
                if field:
                    fields[target] = field
    return name, is_mixin, fields, inherits_models


def _iter_source_files(root: Path):
    """Yield Python source files, odoo/ subtree first (so base definitions win),
    then addons/, each group sorted for determinism. Skips .git, test files."""
    def _filter(py_file: Path):
        rel_str = py_file.relative_to(root).as_posix()
        return not (
            "/.git/" in str(py_file)
            or "/tests/" in rel_str
            or rel_str.startswith("tests/")
            or py_file.name.startswith("test_")
        )

    odoo_dir = root / "odoo"
    addons_dir = root / "addons"

    if odoo_dir.is_dir():
        yield from filter(_filter, sorted(odoo_dir.rglob("*.py")))
    if addons_dir.is_dir():
        yield from filter(_filter, sorted(addons_dir.rglob("*.py")))
    # Fallback: any .py not under odoo/ or addons/ (handles arbitrary layouts)
    for py_file in sorted(root.rglob("*.py")):
        rel = py_file.relative_to(root)
        if rel.parts and rel.parts[0] not in ("odoo", "addons") and _filter(py_file):
            yield py_file


def _canonical_module(model_name: str, candidate: str) -> bool:
    """Return True if *candidate* is the canonical (owning) module for *model_name*.

    Heuristic: a module is canonical when its name matches the first dotted
    component of the model name (e.g. module 'sale' owns 'sale.order', module
    'base' owns 'res.partner' via the res.* -> base special-case).
    """
    prefix = model_name.split(".")[0]
    # Special mapping for Odoo's convention (res.* -> base, ir.* -> base)
    _NS_TO_MODULE = {"res": "base", "ir": "base"}
    expected = _NS_TO_MODULE.get(prefix, prefix)
    return candidate == expected


def extract_models(source_root: str | Path) -> dict:
    """Walk the Odoo source tree; return the known_odoo_models.json structure."""
    root = Path(source_root)
    models: dict = {}
    # _inherits_map: model_name -> list of model_names whose fields to pull in
    inherits_map: dict[str, list[str]] = {}

    for py_file in _iter_source_files(root):
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        module = _module_name(py_file, root)
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                name, is_mixin, fields, inherits_models = _models_in_class(node)
                if not name:
                    continue
                existing = models.get(name)
                if existing is None:
                    models[name] = {"module": module, "fields": fields,
                                    "is_mixin": is_mixin}
                else:
                    # Merge fields: existing fields take priority (first definition wins)
                    merged_fields = {**fields, **existing["fields"]}
                    # Prefer the canonical module for this model name
                    if _canonical_module(name, module) and not _canonical_module(name, existing["module"]):
                        existing["module"] = module
                    existing["fields"] = merged_fields
                    models[name] = existing
                if inherits_models:
                    existing_inh = inherits_map.get(name, [])
                    for inh in inherits_models:
                        if inh not in existing_inh:
                            existing_inh.append(inh)
                    inherits_map[name] = existing_inh

    # Post-process: resolve _inherits — copy delegated model fields into the
    # inheriting model (lower priority: only add fields not already present).
    # One pass is sufficient since _inherits chains are rare and shallow.
    for model_name, inh_list in inherits_map.items():
        if model_name not in models:
            continue
        target = models[model_name]
        for inh_model in inh_list:
            if inh_model not in models:
                continue
            inh_fields = models[inh_model].get("fields", {})
            # Existing fields take priority; delegated fields fill gaps
            target["fields"] = {**inh_fields, **target["fields"]}
        models[model_name] = target

    return {
        "_meta": {
            "odoo_version": "19.0",
            "schema_version": "1.0",
            "model_count": len(models),
            "description": ("Odoo 19.0 models extracted from source for comodel "
                             "validation and depends inference"),
            "last_updated": datetime.date.today().isoformat(),
            "source": "tools/odoo-source/19.0 (AST extraction)",
        },
        "models": models,
    }


def extract_standard_modules(source_root: str | Path) -> dict:
    """Read every __manifest__.py: {module: {summary, depends, application}}."""
    root = Path(source_root)
    result: dict = {}
    for manifest in sorted(root.rglob("__manifest__.py")):
        try:
            data = ast.literal_eval(manifest.read_text(encoding="utf-8",
                                                        errors="replace"))
        except (SyntaxError, ValueError):
            continue
        name = manifest.parent.name
        result[name] = {
            "summary": str(data.get("summary", data.get("name", ""))).strip(),
            "depends": list(data.get("depends", [])),
            "application": bool(data.get("application", False)),
        }
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default="tools/odoo-source/19.0")
    parser.add_argument("--out",
                        default="python/src/amil_utils/data/known_odoo_models.json")
    parser.add_argument("--modules-out",
                        default="python/src/amil_utils/data/standard_odoo_modules.json")
    args = parser.parse_args()

    data = extract_models(args.source)
    Path(args.out).write_text(json.dumps(data, indent=1, sort_keys=True))
    print(f"models: {data['_meta']['model_count']} -> {args.out}")

    mods = extract_standard_modules(args.source)
    Path(args.modules_out).write_text(json.dumps(
        {"_meta": {"odoo_version": "19.0", "module_count": len(mods)},
         "modules": mods}, indent=1, sort_keys=True))
    print(f"modules: {len(mods)} -> {args.modules_out}")


if __name__ == "__main__":
    main()
