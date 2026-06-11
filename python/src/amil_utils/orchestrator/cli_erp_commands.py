"""ERP-pipeline CLI commands: decomposition and spec groups.

Replaces the Node.js decomposition.cjs / spec-completeness.cjs calls that
amil/workflows/{new-erp,plan-module,generate-module,discuss-module}.md
previously embedded via `node -e`.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from amil_utils.orchestrator.cli_helpers import _common, _emit


@click.group("decomposition")
def decomposition_grp() -> None:
    """ERP decomposition commands (merge research, format, init, roadmap)."""


@decomposition_grp.command("merge")
@_common
def decomposition_merge_cmd(cwd: str, raw: bool) -> None:
    """Merge the 4 research-agent JSON files into decomposition.json."""
    from amil_utils.orchestrator.decomposition import merge_decomposition

    research_dir = Path(cwd) / ".planning" / "research"
    result = merge_decomposition(str(research_dir), cwd)
    _emit({
        "module_count": len(result.get("modules", [])),
        "warnings": result.get("warnings", []),
    })


def _load_decomposition(cwd: str) -> dict:
    """Load decomposition.json from the project's .planning/research directory."""
    path = Path(cwd) / ".planning" / "research" / "decomposition.json"
    return json.loads(path.read_text(encoding="utf-8"))


@decomposition_grp.command("format")
@_common
def decomposition_format_cmd(cwd: str, raw: bool) -> None:
    """Print the human-approval decomposition table (plain text)."""
    from amil_utils.orchestrator.decomposition import format_decomposition_table

    click.echo(format_decomposition_table(_load_decomposition(cwd)))


@decomposition_grp.command("init-modules")
@_common
def decomposition_init_modules_cmd(cwd: str, raw: bool) -> None:
    """Initialize module_status.json entries for every module in generation order."""
    from amil_utils.orchestrator.module_status import module_status_init

    decomp = _load_decomposition(cwd)
    module_map = {m["name"]: m for m in decomp.get("modules", [])}
    initialized: list[str] = []
    for name in decomp.get("generation_order", list(module_map)):
        mod = module_map.get(name)
        if not mod:
            continue
        tier = str(mod.get("tier", "core"))
        depends = list(mod.get("base_depends", [])) + list(mod.get("custom_depends", []))
        module_status_init(cwd, name, tier, depends)
        initialized.append(name)
    _emit({"initialized": initialized})


@decomposition_grp.command("roadmap")
@_common
def decomposition_roadmap_cmd(cwd: str, raw: bool) -> None:
    """Generate .planning/ROADMAP.md from decomposition.json."""
    from amil_utils.orchestrator.commands import current_timestamp
    from amil_utils.orchestrator.decomposition import generate_roadmap_markdown

    decomp = _load_decomposition(cwd)
    body = generate_roadmap_markdown(decomp)
    # current_timestamp() returns {"timestamp": "<ISO string>"} — take first 10 chars
    date = current_timestamp()["timestamp"][:10]
    header = f"# ERP Module Roadmap\n\nGenerated: {date}\n\n"
    out = Path(cwd) / ".planning" / "ROADMAP.md"
    out.write_text(header + body, encoding="utf-8")
    _emit({"written": str(out), "module_count": len(decomp.get("modules", []))})


def _normalise_for_scoring(mod: dict) -> dict:
    """Convert a decomposition module to a form score_module can consume.

    Decomposition modules store models as string names; score_module expects
    dicts with a ``"fields"`` key. This wraps strings as minimal dicts so
    the library function does not raise AttributeError.
    """
    raw_models = mod.get("models", [])
    normalised_models = [
        m if isinstance(m, dict) else {"name": m, "fields": []}
        for m in raw_models
    ]
    return {**mod, "models": normalised_models}


@click.group("spec")
def spec_grp() -> None:
    """Spec validation and completeness scoring."""


@spec_grp.command("check-keys")
@click.argument("spec_path")
@click.option(
    "--minimal",
    is_flag=True,
    default=False,
    help="Only require module_name (generate-module pre-check)",
)
@_common
def spec_check_keys_cmd(spec_path: str, minimal: bool, cwd: str, raw: bool) -> None:
    """Validate spec.json has required top-level keys. Exit 1 if invalid."""
    from amil_utils.orchestrator.spec_keys import check_spec_keys

    result = check_spec_keys(spec_path, minimal=minimal)
    _emit(result)
    if not result["valid"]:
        sys.exit(1)


@spec_grp.command("score-all")
@_common
def spec_score_all_cmd(cwd: str, raw: bool) -> None:
    """Score completeness of all planned modules; return scores + batches + summary."""
    from amil_utils.orchestrator.spec_completeness import (
        get_discussion_batches,
        get_discussion_summary,
        score_module,
    )

    # Decomposition modules store models as string names (e.g. ["hr.employee"]).
    # score_module expects models as dicts with "fields" key — normalise before scoring.
    decomp = _load_decomposition(cwd)
    scores = {
        mod["name"]: score_module(_normalise_for_scoring(mod), [])
        for mod in decomp.get("modules", [])
    }
    _emit({
        "scores": scores,
        "batches": get_discussion_batches(scores, decomp),
        "summary": get_discussion_summary(scores),
    })
