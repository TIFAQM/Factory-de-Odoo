"""Module-level CLI commands for the orchestrator.

Extracted from cli_groups.py to keep file sizes manageable.
Contains: dep-graph, module-status, registry, cycle-log, and coherence
command groups.
"""
from __future__ import annotations

import json
from pathlib import Path

import click

from amil_utils.orchestrator.cli_helpers import _common, _emit


# ─── Dep-graph commands ───────────────────────────────────────────


@click.group("dep-graph")
def dep_graph_grp() -> None:
    """Dependency graph commands."""


@dep_graph_grp.command("build")
@_common
def dep_graph_build_cmd(cwd: str, raw: bool) -> None:
    from amil_utils.orchestrator.dependency_graph import dep_graph_build

    _emit(dep_graph_build(cwd))


@dep_graph_grp.command("order")
@_common
def dep_graph_order_cmd(cwd: str, raw: bool) -> None:
    from amil_utils.orchestrator.dependency_graph import dep_graph_order

    _emit({"order": dep_graph_order(cwd)})


@dep_graph_grp.command("tiers")
@_common
def dep_graph_tiers_cmd(cwd: str, raw: bool) -> None:
    from amil_utils.orchestrator.dependency_graph import dep_graph_tiers

    _emit(dep_graph_tiers(cwd))


@dep_graph_grp.command("can-generate")
@click.argument("module_name")
@_common
def dep_graph_can_generate_cmd(module_name: str, cwd: str, raw: bool) -> None:
    from amil_utils.orchestrator.dependency_graph import dep_graph_can_generate

    _emit(dep_graph_can_generate(cwd, module_name))


# ─── Module-status commands ───────────────────────────────────────


@click.group("module-status")
def module_status_grp() -> None:
    """Module lifecycle status commands."""


@module_status_grp.command("read")
@_common
def module_status_read_cmd(cwd: str, raw: bool) -> None:
    from amil_utils.orchestrator.module_status import module_status_read

    _emit(module_status_read(cwd))


@module_status_grp.command("get")
@click.argument("module_name")
@_common
def module_status_get_cmd(module_name: str, cwd: str, raw: bool) -> None:
    from amil_utils.orchestrator.module_status import module_status_get

    _emit(module_status_get(cwd, module_name))


@module_status_grp.command("init")
@click.argument("module_name")
@click.argument("tier")
@click.argument("depends", required=False, default="")
@_common
def module_status_init_cmd(
    module_name: str, tier: str, depends: str, cwd: str, raw: bool
) -> None:
    from amil_utils.orchestrator.module_status import module_status_init

    _emit(module_status_init(cwd, module_name, tier, depends))


@module_status_grp.command("transition")
@click.argument("module_name")
@click.argument("new_state")
@_common
def module_status_transition_cmd(
    module_name: str, new_state: str, cwd: str, raw: bool
) -> None:
    from amil_utils.orchestrator.module_status import module_status_transition

    _emit(module_status_transition(cwd, module_name, new_state))


@module_status_grp.command("tiers")
@_common
def module_status_tiers_cmd(cwd: str, raw: bool) -> None:
    from amil_utils.orchestrator.module_status import tier_status

    _emit(tier_status(cwd))


# ─── Registry commands ────────────────────────────────────────────


@click.group("registry")
def registry_grp() -> None:
    """Model registry commands."""


@registry_grp.command("read")
@_common
def registry_read_cmd(cwd: str, raw: bool) -> None:
    from amil_utils.orchestrator.registry import read_registry_file

    _emit(read_registry_file(cwd))


@registry_grp.command("read-model")
@click.argument("model_name")
@_common
def registry_read_model_cmd(model_name: str, cwd: str, raw: bool) -> None:
    from amil_utils.orchestrator.registry import read_model_from_registry

    result = read_model_from_registry(cwd, model_name)
    _emit(result if result else {"found": False, "model": model_name})


@registry_grp.command("update")
@click.argument("manifest_path")
@_common
def registry_update_cmd(manifest_path: str, cwd: str, raw: bool) -> None:
    from amil_utils.orchestrator.registry import update_registry

    _emit(update_registry(cwd, manifest_path))


@registry_grp.command("rollback")
@_common
def registry_rollback_cmd(cwd: str, raw: bool) -> None:
    from amil_utils.orchestrator.registry import rollback_registry

    result = rollback_registry(cwd)
    _emit(result if result else {"rolled_back": False, "reason": "no_backup"})


@registry_grp.command("validate")
@_common
def registry_validate_cmd(cwd: str, raw: bool) -> None:
    from amil_utils.orchestrator.registry import validate_registry

    _emit(validate_registry(cwd))


@registry_grp.command("stats")
@_common
def registry_stats_cmd(cwd: str, raw: bool) -> None:
    from amil_utils.orchestrator.registry import stats_registry

    _emit(stats_registry(cwd))


# ─── Cycle-log commands ───────────────────────────────────────────


@click.group("cycle-log")
def cycle_log_grp() -> None:
    """Module generation cycle log commands."""


@cycle_log_grp.command("init")
@click.argument("project_name", default="ERP Project")
@_common
def cycle_log_init_cmd(project_name: str, cwd: str, raw: bool) -> None:
    from amil_utils.orchestrator.cycle_log import init_log

    path = init_log(Path(cwd), project_name)
    _emit({"created": True, "path": str(path)})


@cycle_log_grp.command("append")
@click.argument("entry_json")
@_common
def cycle_log_append_cmd(entry_json: str, cwd: str, raw: bool) -> None:
    from amil_utils.orchestrator.cycle_log import append_entry

    append_entry(Path(cwd), json.loads(entry_json))
    _emit({"appended": True})


@cycle_log_grp.command("blocked")
@click.argument("module_name")
@click.argument("reason")
@_common
def cycle_log_blocked_cmd(
    module_name: str, reason: str, cwd: str, raw: bool
) -> None:
    from amil_utils.orchestrator.cycle_log import append_blocked_module

    append_blocked_module(Path(cwd), module_name, reason)
    _emit({"blocked": True, "module": module_name})


@cycle_log_grp.command("coherence")
@click.argument("event_json")
@_common
def cycle_log_coherence_cmd(event_json: str, cwd: str, raw: bool) -> None:
    from amil_utils.orchestrator.cycle_log import append_coherence_event

    append_coherence_event(Path(cwd), json.loads(event_json))
    _emit({"recorded": True})


@cycle_log_grp.command("finalize")
@click.argument("summary_json")
@_common
def cycle_log_finalize_cmd(summary_json: str, cwd: str, raw: bool) -> None:
    from amil_utils.orchestrator.cycle_log import finalize_log

    finalize_log(Path(cwd), json.loads(summary_json))
    _emit({"finalized": True})


# ─── Coherence commands ───────────────────────────────────────────


@click.group("coherence")
def coherence_grp() -> None:
    """Cross-module coherence commands."""


@coherence_grp.command("check")
@click.option("--spec", required=True, help="Path to spec JSON")
@click.option("--registry", "registry_path", default=None, help="Registry path override")
@_common
def coherence_check_cmd(
    spec: str, registry_path: str | None, cwd: str, raw: bool
) -> None:
    from amil_utils.orchestrator.coherence import run_all_checks
    from amil_utils.orchestrator.registry import read_registry_file

    spec_data = json.loads(Path(cwd).joinpath(spec).read_text(encoding="utf-8"))
    reg = read_registry_file(cwd)
    _emit(run_all_checks(spec_data, reg))
