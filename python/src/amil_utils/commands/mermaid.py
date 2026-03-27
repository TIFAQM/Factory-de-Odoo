"""Business logic for the ``mermaid`` CLI command.

Pure Python -- no Click dependency.  Returns structured data so callers
can decide how to display results.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from amil_utils.commands.registry_helpers import find_registry_path as _find_registry_path


def execute_mermaid(
    *,
    module: str | None = None,
    is_project: bool = False,
    diagram_type: str = "all",
    use_stdout: bool = False,
) -> dict[str, Any]:
    """Generate Mermaid dependency DAG and ER diagrams.

    Returns a result dict with keys:
        - stdout_content: list[str] -- diagram content lines (when use_stdout=True)
        - written_files: list[str] -- file paths written (when use_stdout=False)
        - error: str | None -- error message if failed
    """
    from amil_utils.mermaid import (
        generate_dependency_dag,
        generate_er_diagram,
        generate_module_diagrams,
        generate_project_diagrams,
    )
    from amil_utils.registry import ModelRegistry

    result: dict[str, Any] = {
        "stdout_content": [],
        "written_files": [],
        "error": None,
    }

    # Validate: exactly one of module or is_project must be specified
    if module and is_project:
        result["error"] = "Error: specify either --module or --project, not both."
        return result
    if not module and not is_project:
        result["error"] = "Error: specify either --module or --project."
        return result

    # Load registry
    reg_path = _find_registry_path()
    reg = ModelRegistry(reg_path)
    reg.load()
    reg.load_known_models()

    if is_project:
        if use_stdout:
            stdout_parts: list[str] = []
            project_modules = set(reg._dependency_graph.keys())
            if diagram_type in ("deps", "all"):
                from amil_utils.mermaid import (
                    _EXTERNAL_CLASSDEF,
                    _is_external_module,
                    _mermaid_id,
                )

                lines: list[str] = ["graph TD"]
                all_nodes: set[str] = set()
                all_edges: list[str] = []
                for mod, deps in reg._dependency_graph.items():
                    mod_id = _mermaid_id(mod)
                    if mod_id not in all_nodes:
                        all_nodes.add(mod_id)
                        lines.append(f'    {mod_id}["{mod}"]')
                    for dep in deps:
                        dep_id = _mermaid_id(dep)
                        if dep_id not in all_nodes:
                            all_nodes.add(dep_id)
                            if _is_external_module(dep, project_modules):
                                lines.append(f'    {dep_id}["{dep}"]:::external')
                            else:
                                lines.append(f'    {dep_id}["{dep}"]')
                        all_edges.append(f"    {mod_id} --> {dep_id}")
                lines.extend(all_edges)
                lines.append(f"    {_EXTERNAL_CLASSDEF}")
                stdout_parts.append("\n".join(lines))
            if diagram_type in ("er", "all"):
                all_models = dict(reg._models)
                er_content = generate_er_diagram("__project__", all_models, reg)
                stdout_parts.append(er_content)
            result["stdout_content"] = stdout_parts
        else:
            output_dir = Path.cwd() / ".planning" / "diagrams"
            generate_project_diagrams(reg, output_dir)
            if diagram_type in ("deps", "all"):
                result["written_files"].append(str(output_dir / "project_dependencies.mmd"))
            if diagram_type in ("er", "all"):
                result["written_files"].append(str(output_dir / "project_er.mmd"))
    else:
        # Module-level diagrams
        assert module is not None

        # Build spec from registry data
        module_models = {
            name: entry
            for name, entry in reg._models.items()
            if entry.module == module
        }
        deps = reg._dependency_graph.get(module, [])
        project_modules = set(reg._dependency_graph.keys())
        project_modules.add(module)

        if use_stdout:
            stdout_parts = []
            if diagram_type in ("deps", "all"):
                dep_graph = dict(reg._dependency_graph)
                dep_graph.setdefault(module, deps)
                dag_content = generate_dependency_dag(module, dep_graph, project_modules)
                stdout_parts.append(dag_content)
            if diagram_type in ("er", "all"):
                er_content = generate_er_diagram(module, module_models, reg)
                stdout_parts.append(er_content)
            result["stdout_content"] = stdout_parts
        else:
            # Build a spec dict for generate_module_diagrams
            models_list = []
            for model_name, entry in module_models.items():
                models_list.append({
                    "_name": model_name,
                    "fields": entry.fields,
                    "_inherit": list(entry.inherits),
                    "description": entry.description,
                })
            spec = {
                "module_name": module,
                "models": models_list,
                "depends": deps,
            }
            output_dir = Path.cwd() / module / "docs"
            generate_module_diagrams(module, spec, reg, output_dir)
            if diagram_type in ("deps", "all"):
                result["written_files"].append(str(output_dir / "dependencies.mmd"))
            if diagram_type in ("er", "all"):
                result["written_files"].append(str(output_dir / "er_diagram.mmd"))

    return result
