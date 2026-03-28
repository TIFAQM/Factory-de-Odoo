"""Click CLI for amil-utils: render templates and scaffold Odoo modules."""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

_logger = logging.getLogger(__name__)
import click
from amil_utils import __version__
from amil_utils.version_defaults import get_default_version

@click.group()
@click.version_option(version=__version__)
def main() -> None:
    """amil-utils: Python utilities for the amil Amil extension."""

# -- render (single template) -----------------------------------------------

@main.command()
@click.option("--template", required=True, help="Template file name (e.g., manifest.py.j2)")
@click.option("--output", required=True, type=click.Path(), help="Output file path")
@click.option("--var", multiple=True, help="Variable in key=value format (repeatable)")
@click.option("--var-file", type=click.Path(exists=True), help="JSON file with template variables")
def render(template: str, output: str, var: tuple[str, ...], var_file: str | None) -> None:
    """Render a single Jinja2 template to a file."""
    from amil_utils.renderer import create_renderer, create_versioned_renderer, get_template_dir, render_template
    context: dict = {}
    if var_file:
        try: context = json.loads(Path(var_file).read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc: click.echo(f"Error: {exc}", err=True); sys.exit(1)
    for v in var:
        if "=" not in v: click.echo(f"Invalid --var: {v}", err=True); sys.exit(1)
        key, value = v.split("=", 1)
        if not key.isidentifier() or key.startswith("_"): click.echo(f"Bad key: {key!r}", err=True); sys.exit(1)
        try: context[key] = json.loads(value)
        except json.JSONDecodeError: context[key] = value
    ov = context.get("odoo_version")
    env = create_versioned_renderer(ov) if ov else create_renderer(get_template_dir())
    try: click.echo(str(render_template(env, template, Path(output), context)))
    except Exception as exc: click.echo(f"Error: {exc}", err=True); sys.exit(1)

# -- list-templates ----------------------------------------------------------

@main.command("list-templates")
@click.option("--version", "odoo_version", default=None, help="Odoo version (e.g., 17.0)")
def list_templates(odoo_version: str | None) -> None:
    """List all available Jinja2 templates."""
    from amil_utils.renderer import get_template_dir
    td = get_template_dir()
    if not td.is_dir(): click.echo(f"Not found: {td}", err=True); sys.exit(1)
    tmpls: list[tuple[str, Path]] = []
    if odoo_version:
        vd = td / odoo_version
        if vd.is_dir(): tmpls.extend((f"[{odoo_version}]", t) for t in sorted(vd.glob("*.j2")))
        sd = td / "shared"
        if sd.is_dir(): tmpls.extend(("[shared]", t) for t in sorted(sd.glob("*.j2")))
    else:
        for sd in sorted(td.iterdir()):
            if sd.is_dir(): tmpls.extend((f"[{sd.name}]", t) for t in sorted(sd.glob("*.j2")))
    if not tmpls: tmpls.extend(("", t) for t in sorted(td.glob("*.j2")))
    if not tmpls: click.echo("No templates found.", err=True); sys.exit(1)
    for label, t in tmpls:
        d = _tmpl_desc(t); pfx = f"{label:10s} " if label else ""
        click.echo(f"{pfx}{t.name:30s} {d}" if d else f"{pfx}{t.name}")

def _tmpl_desc(p: Path) -> str:
    try:
        ln = p.read_text(encoding="utf-8").split("\n", maxsplit=1)[0]
        if ln.startswith("{#") and ln.endswith("#}"):
            c = ln[2:-2].strip()
            for s in (" -- ", " - "):
                parts = c.split(s, maxsplit=1)
                if len(parts) == 2: return parts[1].strip()
    except OSError as exc:
        _logger.debug("Failed to read help comment from %s: %s", p, exc)
    return ""

# -- Registry group (sub-commands in commands/registry_cmds.py) --------------
class _LazyRegistryGroup(click.Group):
    _loaded = False
    def _ensure(self):
        if not self._loaded:
            from amil_utils.commands.registry_cmds import register_registry_commands
            register_registry_commands(self); self._loaded = True
    def list_commands(self, ctx): self._ensure(); return super().list_commands(ctx)
    def get_command(self, ctx, cmd_name): self._ensure(); return super().get_command(ctx, cmd_name)

@main.group(cls=_LazyRegistryGroup)
def registry() -> None:
    """Manage the cross-module model registry."""
# -- render-module -----------------------------------------------------------

@main.command("render-module")
@click.option("--spec-file", required=True, type=click.Path(exists=True))
@click.option("--output-dir", required=True, type=click.Path())
@click.option("--no-context7", is_flag=True, default=False)
@click.option("--fresh-context7", is_flag=True, default=False)
@click.option("--skip-validation", is_flag=True, default=False)
@click.option("--resume", is_flag=True, default=False)
@click.option("--force", is_flag=True, default=False)
@click.option("--dry-run", "dry_run", is_flag=True, default=False)
@click.option("--skip-odoo-ls", is_flag=True, default=False,
              help="Skip odoo-ls structural validation")
@click.option("--odoo-source-path", type=click.Path(exists=True),
              envvar="ODOO_SOURCE_PATH",
              help="Path to Odoo 19.0 source for odoo-ls validation")
@click.option("--odoo-ls-binary", type=click.Path(),
              envvar="ODOO_LS_BINARY", default="odoo_ls_server",
              help="Path to odoo_ls_server binary")
def render_module_cmd(spec_file: str, output_dir: str, no_context7: bool, fresh_context7: bool, skip_validation: bool, resume: bool, force: bool, dry_run: bool, skip_odoo_ls: bool, odoo_source_path: str | None, odoo_ls_binary: str) -> None:
    """Render a complete Odoo module from a JSON specification file."""
    from pathlib import Path as _P
    from amil_utils.commands.render import execute_render_module

    ols_client = None
    if not skip_odoo_ls and odoo_source_path:
        from amil_utils.validation.odoo_ls_config import generate_odools_toml
        from amil_utils.validation.odoo_ls_client import OdooLSClient

        config_path = generate_odools_toml(
            output_path=_P(output_dir) / ".odools.toml",
            odoo_source_path=_P(odoo_source_path),
            addons_output_dir=_P(output_dir),
        )
        ols_client = OdooLSClient(
            binary_path=_P(odoo_ls_binary),
            config_path=config_path,
            workspace_root=_P(output_dir),
        )
        ols_client.start()

    try:
        r = execute_render_module(spec_file, output_dir, no_context7=no_context7, fresh_context7=fresh_context7, skip_validation=skip_validation, resume=resume, force=force, dry_run=dry_run, ols_client=ols_client)
    finally:
        if ols_client is not None:
            ols_client.shutdown()
    for f in r["files"]: click.echo(f)
    for w in r["warnings"]:
        click.echo(f"WARN [{w['check_type']}] {w['subject']}: {w['message']}", err=True)
        if w.get("suggestion"): click.echo(f"  Suggestion: {w['suggestion']}", err=True)
    if r["pending_conflicts"]:
        click.echo(f"\nPending conflicts ({len(r['pending_conflicts'])} files):", err=True)
        for pf in r["pending_conflicts"]: click.echo(f"  {pf}", err=True)
    s = r.get("stub_report")
    if s and "error" in s: click.echo(f"WARN: Stub report failed: {s['error']}", err=True)
    elif s and s.get("total_stubs", 0) > 0: click.echo(f"Stubs: {s['total_stubs']} ({s['budget_count']} budget, {s['quality_count']} quality) -> {s['report_path']}")
    elif s: click.echo("Stubs: 0")
    if r.get("validation") and r["validation"].get("object"):
        from amil_utils.validation.semantic import print_validation_report
        print_validation_report(r["validation"]["object"])
    ru = r.get("registry_update")
    if ru:
        for vw in ru.get("warnings",[]): click.echo(f"WARNING: {vw}")
        for ve in ru.get("errors",[]): click.echo(f"ERROR: {ve}")
        if ru.get("inferred_depends"): click.echo(f"Inferred depends: {', '.join(ru['inferred_depends'])}")
        click.echo(f"Registry updated: +{ru['model_count']} models ({ru['module_name']})")
    if r.get("mermaid_paths"): click.echo(f"Mermaid: {', '.join(r['mermaid_paths'])}")
    if r.get("error"): click.echo(r["error"], err=True); sys.exit(1)

# -- export-schema / extract-i18n / check-edition ----------------------------

@main.command("export-schema")
@click.option("--output", "-o", type=click.Path(), default=None)
def export_schema(output: str | None) -> None:
    """Export the module spec JSON Schema."""
    from amil_utils.spec_schema import ModuleSpec
    s = json.dumps(ModuleSpec.model_json_schema(), indent=2)
    if output: Path(output).write_text(s, encoding="utf-8"); click.echo(f"Written to {output}")
    else: click.echo(s)

@main.command("extract-i18n")
@click.argument("module_path", type=click.Path(exists=True))
def extract_i18n(module_path: str) -> None:
    """Extract translatable strings and generate .pot file."""
    from amil_utils.i18n_extractor import extract_translatable_strings, generate_pot
    mp = Path(module_path).resolve()
    try:
        strings = extract_translatable_strings(mp); pot = generate_pot(mp.name, strings)
        (mp/"i18n").mkdir(parents=True, exist_ok=True)
        pp = mp/"i18n"/f"{mp.name}.pot"; pp.write_text(pot, encoding="utf-8")
        click.echo(f"Extracted {len(strings)} strings to {pp}")
    except Exception as exc: click.echo(f"Error: {exc}", err=True); sys.exit(1)

@main.command("check-edition")
@click.argument("spec_file", type=click.Path(exists=True))
@click.option("--json", "json_output", is_flag=True)
def check_edition(spec_file: str, json_output: bool) -> None:
    """Check for Enterprise-only dependencies."""
    from amil_utils.edition import check_enterprise_dependencies
    try: spec = json.loads(Path(spec_file).read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc: click.echo(f"Error: {exc}", err=True); sys.exit(1)
    w = check_enterprise_dependencies(spec.get("depends", ["base"]))
    if not w: click.echo("All Community-compatible."); return
    if json_output: click.echo(json.dumps(w, indent=2)); return
    for i in w:
        click.echo(f"  * {i['module']} ({i['display_name']}) [{i['category']}]")
        click.echo(f"    Alt: {i['alternative']} ({i['alternative_repo']})" if i.get("alternative") else "    No alternative.")

# -- validate ----------------------------------------------------------------

@main.command()
@click.argument("module_path", type=click.Path(exists=True), nargs=-1, required=True)
@click.option("--pylint-only", is_flag=True)
@click.option("--auto-fix", is_flag=True)
@click.option("--json", "json_output", is_flag=True)
@click.option("--pylintrc", type=click.Path(exists=True))
@click.option("--parallel", is_flag=True, default=False, help="Validate multiple modules in parallel")
@click.option("--concurrency", default=3, type=int, help="Max parallel validations (default 3)")
def validate(
    module_path: tuple[str, ...],
    pylint_only: bool,
    auto_fix: bool,
    json_output: bool,
    pylintrc: str | None,
    parallel: bool,
    concurrency: int,
) -> None:
    """Validate an Odoo module against OCA quality standards."""
    from amil_utils.commands.validate import execute_validate, execute_validate_parallel

    paths = list(module_path)

    # Parallel mode: multiple modules validated concurrently
    if parallel and len(paths) > 1:
        results = execute_validate_parallel(
            paths,
            pylint_only=pylint_only,
            auto_fix=auto_fix,
            pylintrc=pylintrc,
            concurrency=concurrency,
        )
        any_issues = False
        for r in results:
            mod_name = r.get("module_name", "?")
            if r.get("error"):
                click.echo(f"[{mod_name}] {r['error']}", err=True)
                any_issues = True
                continue
            rpt = r.get("report")
            if rpt is not None:
                label = f"[{mod_name}] "
                fmt_fn = r.get("format_report_json") if json_output else r.get("format_report_markdown")
                if json_output and fmt_fn:
                    click.echo(label + json.dumps(fmt_fn(rpt), indent=2))
                elif fmt_fn:
                    click.echo(label + fmt_fn(rpt))
            if r.get("has_issues"):
                any_issues = True
        if any_issues:
            sys.exit(1)
        return

    # Sequential mode: single module (or multiple modules one-by-one)
    any_issues = False
    for p in paths:
        r = execute_validate(p, pylint_only=pylint_only, auto_fix=auto_fix, pylintrc=pylintrc)
        if r.get("error"):
            click.echo(r["error"], err=True)
            any_issues = True
            continue
        if r.get("auto_fix_count", 0) > 0:
            click.echo(f"Auto-fix: {r['auto_fix_count']} fixed")
        for k in ("auto_fix_escalation", "docker_error", "docker_retry_error", "test_error"):
            if r.get(k):
                click.echo(r[k], err=True)
        if r.get("docker_fix_applied"):
            click.echo("Auto-fix: Docker fix(es) applied...")
        if r.get("docker_iteration_cap"):
            click.echo(r["docker_iteration_cap"])
        rpt = r["report"]
        click.echo(
            json.dumps(r["format_report_json"](rpt), indent=2)
            if json_output
            else r["format_report_markdown"](rpt)
        )
        if r["has_issues"]:
            any_issues = True
    if any_issues:
        sys.exit(1)

# -- Search helpers / commands -----------------------------------------------

def _handle_auth_failure(no_wizard: bool) -> None:
    if no_wizard: click.echo("GitHub authentication required. Run: gh auth login", err=True)
    else:
        from amil_utils.search.wizard import check_github_auth, format_auth_guidance
        click.echo(format_auth_guidance(check_github_auth()), err=True)
    sys.exit(1)

@main.command("build-index")
@click.option("--token", envvar="GITHUB_TOKEN", default=None)
@click.option("--db-path", default=None)
@click.option("--update", is_flag=True)
@click.option("--no-wizard", is_flag=True)
def build_index(token: str|None, db_path: str|None, update: bool, no_wizard: bool) -> None:
    """Build or update the ChromaDB OCA index."""
    from amil_utils.commands.search import execute_build_index
    def _p(d:int,t:int)->None: click.echo(f"\rIndexing... {d}/{t}", nl=False)
    click.echo("Building index...")
    r = execute_build_index(token=token, db_path=db_path, update=update, progress_callback=_p)
    if r["needs_auth"]: _handle_auth_failure(no_wizard)
    click.echo(f"Indexed {r['count']} modules")

@main.command("index-status")
@click.option("--db-path", default=None)
@click.option("--json", "json_output", is_flag=True)
def index_status(db_path: str|None, json_output: bool) -> None:
    """Show OCA index status."""
    from amil_utils.commands.search import execute_index_status
    r = execute_index_status(db_path=db_path)
    if json_output:
        import dataclasses; click.echo(json.dumps(dataclasses.asdict(r["status_object"]), indent=2))
    elif r["exists"]: click.echo(f"Exists: yes\nModules: {r['module_count']}\nBuilt: {r['last_built'] or '?'}\nPath: {r['db_path']}\nSize: {r['size_bytes']}B")
    else: click.echo(f"Exists: no\nPath: {r['db_path']}\nRun 'amil-utils build-index'.")

@main.command("search-modules")
@click.argument("query")
@click.option("--limit", default=5)
@click.option("--db-path", default=None)
@click.option("--json", "json_output", is_flag=True)
@click.option("--github", "github_fallback", is_flag=True)
@click.option("--no-wizard", is_flag=True)
def search_modules_cmd(query: str, limit: int, db_path: str|None, json_output: bool, github_fallback: bool, no_wizard: bool) -> None:
    """Search for Odoo modules by natural language query."""
    from amil_utils.commands.search import execute_search
    def _p(d:int,t:int)->None: click.echo(f"\rIndexing... {d}/{t}", nl=False)
    r = execute_search(query, db_path=db_path, limit=limit, github_fallback=github_fallback, no_wizard=no_wizard, progress_callback=_p)
    if r["needs_auth"]: _handle_auth_failure(no_wizard)
    if r.get("auto_built"): click.echo("Index built.\n")
    if r.get("error"): click.echo(r["error"], err=True); sys.exit(1)
    if not r["results"]: click.echo("No results."); sys.exit(1)
    click.echo(r["results_json"] if json_output else r["results_text"])

# -- extend-module -----------------------------------------------------------

@main.command("extend-module")
@click.argument("module_name")
@click.option("--repo", required=True)
@click.option("--output-dir", default=".", type=click.Path())
@click.option("--spec-file", type=click.Path(exists=True))
@click.option("--branch", default=get_default_version())
@click.option("--json", "json_output", is_flag=True)
@click.option("--no-wizard", is_flag=True)
def extend_module_cmd(module_name: str, repo: str, output_dir: str, spec_file: str|None, branch: str, json_output: bool, no_wizard: bool) -> None:
    """Clone an OCA module and create companion extension."""
    from amil_utils.commands.extend import execute_extend_module
    click.echo(f"Cloning {repo}/{module_name} ({branch})...")
    r = execute_extend_module(module_name, repo, output_dir, spec_file=spec_file, branch=branch)
    if r["needs_auth"]: _handle_auth_failure(no_wizard)
    if r.get("error"): click.echo(r["error"], err=True); sys.exit(1)
    click.echo(f"Cloned: {r['cloned_path']}\nCompanion: {r['companion_path']}")
    if r["spec_saved"]: click.echo("Spec saved.")
    click.echo(json.dumps(r["analysis_dict"], indent=2) if json_output else f"\n{r['analysis_text']}")
    click.echo(f"\nOriginal: {r['cloned_path']}\nCompanion: {r['companion_path']}")

# -- show-state / context7-status / diff-spec / gen-migration / validate-kb --

@main.command("show-state")
@click.argument("module_path", type=click.Path(exists=True))
@click.option("--json", "json_output", is_flag=True)
def show_state(module_path: str, json_output: bool) -> None:
    """Show artifact generation state."""
    from amil_utils.commands.misc import execute_show_state
    r = execute_show_state(module_path)
    if json_output and r["manifest_data"]: click.echo(json.dumps(r["manifest_data"], indent=2))
    else: click.echo(r["text"])

@main.command("context7-status")
def context7_status() -> None:
    """Check Context7 API status."""
    from amil_utils.context7 import build_context7_from_env
    c = build_context7_from_env()
    if not c.is_configured: click.echo("Not configured. Set CONTEXT7_API_KEY."); return
    click.echo("Configured.")
    lid = c.resolve_odoo_library()
    click.echo(f"Library: {lid}" if lid else "Resolution failed.")

@main.command("diff-spec")
@click.argument("old_spec", type=click.Path(exists=True))
@click.argument("new_spec", type=click.Path(exists=True))
@click.option("--json", "json_output", is_flag=True)
def diff_spec(old_spec: str, new_spec: str, json_output: bool) -> None:
    """Compare two spec versions."""
    from amil_utils.commands.misc import execute_diff_spec
    r = execute_diff_spec(old_spec, new_spec)
    if r["error"]: click.echo(f"Error: {r['error']}", err=True); sys.exit(1)
    click.echo(json.dumps(r["result"], indent=2) if json_output else f"{r['human_summary']}\n{json.dumps(r['result'], indent=2)}")
    if r["result"].get("destructive_count", 0) > 0: click.echo(f"\nWARNING: destructive changes.", err=True)

@main.command("gen-migration")
@click.argument("old_spec", type=click.Path(exists=True))
@click.argument("new_spec", type=click.Path(exists=True))
@click.option("--version", "migration_version", required=True)
@click.option("--output-dir", default=".", type=click.Path())
def gen_migration(old_spec: str, new_spec: str, migration_version: str, output_dir: str) -> None:
    """Generate migration scripts from spec diff."""
    from amil_utils.commands.misc import execute_gen_migration
    r = execute_gen_migration(old_spec, new_spec, migration_version, output_dir)
    if r["error"]: click.echo(f"Error: {r['error']}", err=True); sys.exit(1)
    if not r["migration_required"]: click.echo("No migration required."); return
    click.echo(f"Created: {r['migration_dir']}")
    if r["destructive_count"] > 0: click.echo(f"WARNING: {r['destructive_count']} destructive change(s).", err=True)

@main.command("validate-kb")
@click.option("--custom", "scope", flag_value="custom", default=True)
@click.option("--all", "scope", flag_value="all")
def validate_kb(scope: str) -> None:
    """Validate knowledge base rule files."""
    from amil_utils.commands.misc import execute_validate_kb
    r = execute_validate_kb(scope)
    if r["error"]: raise click.ClickException(r["error"])
    for line in r["output_lines"]: click.echo(line)
    if r["has_errors"]: raise SystemExit(1)

# -- mermaid -----------------------------------------------------------------

@main.command("mermaid")
@click.option("--module", default=None)
@click.option("--project", "is_project", is_flag=True, default=False)
@click.option("--type", "diagram_type", type=click.Choice(["deps","er","all"]), default="all")
@click.option("--stdout", "use_stdout", is_flag=True, default=False)
def mermaid_cmd(module: str|None, is_project: bool, diagram_type: str, use_stdout: bool) -> None:
    """Generate Mermaid diagrams."""
    from amil_utils.commands.mermaid import execute_mermaid
    r = execute_mermaid(module=module, is_project=is_project, diagram_type=diagram_type, use_stdout=use_stdout)
    if r.get("error"): click.echo(r["error"], err=True); sys.exit(1)
    for c in r.get("stdout_content", []): click.echo(c)
    for f in r.get("written_files", []): click.echo(f)

# -- resolve group -----------------------------------------------------------

@main.group("resolve")
def resolve_group() -> None:
    """Manage pending conflict files."""

@resolve_group.command("status")
@click.option("--module-dir", required=True, type=click.Path(exists=True))
def resolve_status_cmd(module_dir: str) -> None:
    """Show pending conflicts."""
    from amil_utils.commands.resolve import execute_resolve_status
    r = execute_resolve_status(module_dir)
    if not r["pending"]: click.echo("No pending conflicts."); return
    click.echo(f"Pending ({r['count']}):"); [click.echo(f"  {p}") for p in r["pending"]]

@resolve_group.command("accept-all")
@click.option("--module-dir", required=True, type=click.Path(exists=True))
def resolve_accept_all_cmd(module_dir: str) -> None:
    """Accept all pending files."""
    from amil_utils.commands.resolve import execute_resolve_accept_all
    r = execute_resolve_accept_all(module_dir)
    click.echo("None." if r["count"]==0 else f"Resolved {r['count']}.")

@resolve_group.command("accept-new")
@click.option("--module-dir", required=True, type=click.Path(exists=True))
@click.argument("file_path")
def resolve_accept_new_cmd(module_dir: str, file_path: str) -> None:
    """Accept new version of a file."""
    from amil_utils.commands.resolve import execute_resolve_accept_new
    r = execute_resolve_accept_new(module_dir, file_path)
    if r["accepted"]: click.echo(f"Accepted: {file_path}")
    else: click.echo(f"Not found: {file_path}", err=True); sys.exit(1)

@resolve_group.command("keep-mine")
@click.option("--module-dir", required=True, type=click.Path(exists=True))
@click.argument("file_path")
def resolve_keep_mine_cmd(module_dir: str, file_path: str) -> None:
    """Keep current version, discard pending."""
    from amil_utils.commands.resolve import execute_resolve_keep_mine
    r = execute_resolve_keep_mine(module_dir, file_path)
    if r["kept"]: click.echo(f"Kept: {file_path}")
    else: click.echo(f"Not found: {file_path}", err=True); sys.exit(1)

# -- factory-docker ----------------------------------------------------------

@main.command("factory-docker")
@click.option("--action", type=click.Choice(["start","stop","reset","status"]))
@click.option("--install", type=click.Path(exists=True))
@click.option("--test", type=click.Path(exists=True))
@click.option("--cross-test", multiple=True)
@click.option("--url", is_flag=True)
@click.option("--history", is_flag=True)
@click.option("--state-dir", type=click.Path(), default=".planning")
def factory_docker(action: str|None, install: str|None, test: str|None, cross_test: tuple[str,...], url: bool, history: bool, state_dir: str) -> None:
    """Manage persistent Docker factory instance."""
    from amil_utils.commands.docker import execute_factory_docker
    r = execute_factory_docker(action=action, install=install, test=test, cross_test=cross_test, url=url, history=history, state_dir=state_dir)
    if r["output"]: click.echo(r["output"])
    if r["error"]: click.echo(r["error"], err=True)
    if r["exit_code"] != 0: sys.exit(r["exit_code"])

# -- orchestrator subgroup -----------------------------------------------------

from amil_utils.orchestrator.cli import orch_group  # noqa: E402

main.add_command(orch_group)