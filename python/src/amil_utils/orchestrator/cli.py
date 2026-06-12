"""Click command group for orchestrator commands.

Thin wiring layer: each command parses Click args, calls the library
function (which returns a dict), and emits JSON to stdout.

Command groups (state, phase, roadmap, etc.) live in cli_groups.py.
Shared helpers (_common, _emit) live in cli_helpers.py.
"""
from __future__ import annotations

import json

import click

from amil_utils.orchestrator.cli_helpers import _common, _emit

# ─── Root group ────────────────────────────────────────────────────


@click.group("orch")
def orch_group() -> None:
    """Orchestrator: state, phase, and project management."""


# ─── Register command groups from cli_groups ───────────────────────

from amil_utils.orchestrator.cli_groups import (  # noqa: E402
    coherence_grp,
    cycle_log_grp,
    dep_graph_grp,
    frontmatter_grp,
    init_grp,
    milestone_grp,
    module_status_grp,
    phase_grp,
    phases_grp,
    registry_grp,
    requirements_grp,
    roadmap_grp,
    state_grp,
    template_grp,
    validate_grp,
)
from amil_utils.orchestrator.cli_erp_commands import (  # noqa: E402
    decomposition_grp,
    spec_grp,
)

orch_group.add_command(state_grp)
orch_group.add_command(phase_grp)
orch_group.add_command(phases_grp)
orch_group.add_command(roadmap_grp)
orch_group.add_command(requirements_grp)
orch_group.add_command(milestone_grp)
orch_group.add_command(validate_grp)
orch_group.add_command(template_grp)
orch_group.add_command(frontmatter_grp)
orch_group.add_command(init_grp)
orch_group.add_command(dep_graph_grp)
orch_group.add_command(module_status_grp)
orch_group.add_command(registry_grp)
orch_group.add_command(cycle_log_grp)
orch_group.add_command(coherence_grp)
orch_group.add_command(decomposition_grp)
orch_group.add_command(spec_grp)


# ─── Standalone commands ──────────────────────────────────────────


@orch_group.command("find-phase")
@click.argument("phase")
@_common
def find_phase_cmd(phase: str, cwd: str, raw: bool) -> None:
    from amil_utils.orchestrator.phase import phase_find

    _emit(phase_find(cwd, phase))


@orch_group.command("phase-plan-index")
@click.argument("phase")
@_common
def phase_plan_index_cmd(phase: str, cwd: str, raw: bool) -> None:
    from amil_utils.orchestrator.phase import phase_plan_index

    _emit(phase_plan_index(cwd, phase))


@orch_group.command("generate-slug")
@click.argument("text")
@_common
def generate_slug_cmd(text: str, cwd: str, raw: bool) -> None:
    from amil_utils.orchestrator.commands import generate_slug

    _emit(generate_slug(text))


@orch_group.command("current-timestamp")
@click.argument("format", default="full")
@_common
def current_timestamp_cmd(format: str, cwd: str, raw: bool) -> None:
    from amil_utils.orchestrator.commands import current_timestamp

    _emit(current_timestamp(format))


@orch_group.command("list-todos")
@click.argument("area", required=False, default=None)
@_common
def list_todos_cmd(area: str | None, cwd: str, raw: bool) -> None:
    from amil_utils.orchestrator.commands import list_todos

    _emit(list_todos(cwd, area))


@orch_group.command("verify-path-exists")
@click.argument("target_path")
@_common
def verify_path_exists_cmd(target_path: str, cwd: str, raw: bool) -> None:
    from amil_utils.orchestrator.commands import verify_path_exists

    _emit(verify_path_exists(cwd, target_path))


@orch_group.command("resolve-model")
@click.argument("agent_type")
@_common
def resolve_model_cmd(agent_type: str, cwd: str, raw: bool) -> None:
    from amil_utils.orchestrator.commands import resolve_model

    _emit(resolve_model(cwd, agent_type))


@orch_group.command("commit")
@click.argument("message")
@click.option("--files", multiple=True)
@click.option("--amend", is_flag=True, default=False)
@_common
def commit_cmd(
    message: str,
    files: tuple[str, ...],
    amend: bool,
    cwd: str,
    raw: bool,
) -> None:
    from amil_utils.orchestrator.commands import commit

    _emit(commit(cwd, message, list(files), amend=amend))


@orch_group.command("history-digest")
@_common
def history_digest_cmd(cwd: str, raw: bool) -> None:
    from amil_utils.orchestrator.commands import history_digest

    _emit(history_digest(cwd))


@orch_group.command("summary-extract")
@click.argument("summary_path")
@click.option("--fields", default=None)
@_common
def summary_extract_cmd(
    summary_path: str, fields: str | None, cwd: str, raw: bool
) -> None:
    from amil_utils.orchestrator.commands import summary_extract

    field_list = fields.split(",") if fields else None
    _emit(summary_extract(cwd, summary_path, fields=field_list))


@orch_group.command("progress")
@click.argument("format", default="json")
@_common
def progress_cmd(format: str, cwd: str, raw: bool) -> None:
    from amil_utils.orchestrator.commands import progress_render

    _emit(progress_render(cwd, format=format))


@orch_group.command("todo")
@click.argument("subcommand")
@click.argument("filename")
@_common
def todo_cmd(subcommand: str, filename: str, cwd: str, raw: bool) -> None:
    from amil_utils.orchestrator.commands import todo_complete

    if subcommand == "complete":
        _emit(todo_complete(cwd, filename))
    else:
        _emit({"error": f"Unknown todo subcommand: {subcommand}"})


@orch_group.command("scaffold")
@click.argument("scaffold_type")
@click.option("--phase", default=None)
@click.option("--name", default=None)
@_common
def scaffold_cmd(
    scaffold_type: str,
    phase: str | None,
    name: str | None,
    cwd: str,
    raw: bool,
) -> None:
    from amil_utils.orchestrator.commands import scaffold

    _emit(scaffold(cwd, scaffold_type, phase=phase, name=name))


@orch_group.command("config-ensure-section")
@_common
def config_ensure_section_cmd(cwd: str, raw: bool) -> None:
    from amil_utils.orchestrator.config import config_ensure_section

    _emit(config_ensure_section(cwd))


@orch_group.command("config-set")
@click.argument("key_path")
@click.argument("value")
@_common
def config_set_cmd(key_path: str, value: str, cwd: str, raw: bool) -> None:
    from amil_utils.orchestrator.config import config_set

    _emit(config_set(cwd, key_path, value))


@orch_group.command("config-get")
@click.argument("key_path")
@_common
def config_get_cmd(key_path: str, cwd: str, raw: bool) -> None:
    from amil_utils.orchestrator.config import config_get

    result = config_get(cwd, key_path)
    _emit({"key": key_path, "value": result})
