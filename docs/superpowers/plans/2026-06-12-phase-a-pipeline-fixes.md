# Phase A: Pipeline-Breaking Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the PRD-to-ERP pipeline actually runnable: replace dead Node.js calls in 4 core workflows with Python CLI commands, fix the generated-test Many2one bug, make registry/module-status writes safe under concurrency, and expand the known-Odoo-models database from 203 to full Odoo 19 coverage.

**Architecture:** New CLI command groups (`decomposition`, `spec`) plus missing `registry` subcommands expose existing library functions (`decomposition.py`, `spec_completeness.py`, `registry.py`) so workflows shell out to `amil-utils orch …` instead of `node -e`. A lock-file context manager serializes read-modify-write cycles on shared JSON state. An AST-based extractor regenerates `known_odoo_models.json` from the vendored Odoo 19 source tree.

**Tech Stack:** Python 3.12, Click, pytest, Jinja2. No new dependencies.

**Conventions that bind this plan** (from `CONVENTIONS.md` / `CLAUDE.md`): functions ≤50 lines, Python files ≤800 lines, immutable data (never mutate inputs), atomic JSON writes, all state changes via CLI subcommands, 80%+ coverage.

**Working directory for all commands:** `/home/inshal-rauf/Factory-de-Odoo` (run pytest from `python/`).

**Branch:** create `phase-a-pipeline-fixes` off `master` before Task 1:
```bash
git checkout -b phase-a-pipeline-fixes
```

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `python/src/amil_utils/orchestrator/cli_erp_commands.py` | Create | New `decomposition` and `spec` Click groups |
| `python/src/amil_utils/orchestrator/cli_module_commands.py` | Modify | Add `registry tiered-injection` + `registry update-from-spec` |
| `python/src/amil_utils/orchestrator/cli.py` | Modify | Register the two new groups |
| `python/src/amil_utils/orchestrator/spec_keys.py` | Create | Required-spec-key validation (library fn for `spec check-keys`) |
| `python/src/amil_utils/orchestrator/file_lock.py` | Create | Portable lock-file context manager |
| `python/src/amil_utils/orchestrator/registry.py` | Modify | Wrap mutations in lock |
| `python/src/amil_utils/orchestrator/module_status.py` | Modify | Wrap mutations in lock |
| `python/src/amil_utils/templates/shared/test_model.py.j2` | Modify | Replace hardcoded `base.main_company` with `_m2o()` helper |
| `scripts/extract_odoo_models.py` | Create | AST extractor → regenerate known-models JSON |
| `python/src/amil_utils/data/known_odoo_models.json` | Regenerate | Full Odoo 19 model/field coverage |
| `python/src/amil_utils/data/standard_odoo_modules.json` | Create | List of all stock Odoo 19 modules (name+summary) for the decomposer |
| `amil/workflows/new-erp.md` | Modify | De-Node Stage B validation + Stage C (merge/format/init/roadmap) |
| `amil/workflows/plan-module.md` | Modify | De-Node registry check + spec validation |
| `amil/workflows/generate-module.md` | Modify | De-Node spec/config reads + registry update |
| `amil/workflows/discuss-module.md` | Modify | Point batch scoring at Python CLI |
| `python/tests/orchestrator/test_cli_erp_commands.py` | Create | Tests for new CLI groups |
| `python/tests/orchestrator/test_file_lock.py` | Create | Lock semantics tests |
| `python/tests/orchestrator/test_concurrent_writes.py` | Modify | Upgrade last-writer-wins tests to no-lost-updates |
| `python/tests/test_render_tests_m2o.py` | Create | Render test for `_m2o` helper in generated tests |
| `python/tests/orchestrator/test_extract_odoo_models.py` | Create | Extractor unit tests against a fixture tree |

---

### Task 1: `spec_keys.py` — required-key validation library

**Files:**
- Create: `python/src/amil_utils/orchestrator/spec_keys.py`
- Test: `python/tests/orchestrator/test_spec_keys.py`

- [ ] **Step 1: Write the failing tests**

```python
# python/tests/orchestrator/test_spec_keys.py
"""Tests for spec_keys — required top-level key validation for spec.json."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from amil_utils.orchestrator.spec_keys import REQUIRED_SPEC_KEYS, check_spec_keys


def _write_spec(tmp_path: Path, data: dict) -> Path:
    p = tmp_path / "spec.json"
    p.write_text(json.dumps(data))
    return p


def test_full_spec_passes(tmp_path: Path) -> None:
    spec = {k: {} for k in REQUIRED_SPEC_KEYS}
    result = check_spec_keys(_write_spec(tmp_path, spec))
    assert result["valid"] is True
    assert result["missing"] == []
    assert result["key_count"] == len(REQUIRED_SPEC_KEYS)


def test_missing_keys_reported(tmp_path: Path) -> None:
    result = check_spec_keys(_write_spec(tmp_path, {"module_name": "uni_core"}))
    assert result["valid"] is False
    assert "models" in result["missing"]
    assert "security" in result["missing"]


def test_minimal_mode_only_needs_module_name(tmp_path: Path) -> None:
    result = check_spec_keys(
        _write_spec(tmp_path, {"module_name": "uni_core", "models": []}),
        minimal=True,
    )
    assert result["valid"] is True
    assert result["module_name"] == "uni_core"
    assert result["model_count"] == 0


def test_invalid_json_reported(tmp_path: Path) -> None:
    p = tmp_path / "spec.json"
    p.write_text("{not json")
    result = check_spec_keys(p)
    assert result["valid"] is False
    assert "error" in result


def test_missing_file_reported(tmp_path: Path) -> None:
    result = check_spec_keys(tmp_path / "nope.json")
    assert result["valid"] is False
    assert "error" in result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd python && uv run pytest tests/orchestrator/test_spec_keys.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'amil_utils.orchestrator.spec_keys'`

- [ ] **Step 3: Write the implementation**

```python
# python/src/amil_utils/orchestrator/spec_keys.py
"""Spec key validation — checks spec.json has the required top-level keys.

Replaces the Node.js inline validators previously embedded in
amil/workflows/plan-module.md and generate-module.md.
"""
from __future__ import annotations

import json
from pathlib import Path

# Keys the spec-generator agent must produce (mirrors plan-module.md Step 7).
REQUIRED_SPEC_KEYS: tuple[str, ...] = (
    "module_name",
    "module_title",
    "odoo_version",
    "depends",
    "models",
    "business_rules",
    "computation_chains",
    "workflow",
    "view_hints",
    "reports",
    "notifications",
    "cron_jobs",
    "security",
    "portal",
    "controllers",
)

MINIMAL_SPEC_KEYS: tuple[str, ...] = ("module_name",)


def check_spec_keys(spec_path: str | Path, minimal: bool = False) -> dict:
    """Validate spec.json contains required top-level keys.

    Returns a dict: {valid, missing, key_count, module_name, model_count}
    or {valid: False, error: str} on read/parse failure.
    """
    path = Path(spec_path)
    try:
        spec = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"valid": False, "missing": [], "error": str(exc)}

    if not isinstance(spec, dict):
        return {"valid": False, "missing": [], "error": "spec root is not an object"}

    required = MINIMAL_SPEC_KEYS if minimal else REQUIRED_SPEC_KEYS
    missing = [k for k in required if k not in spec]
    return {
        "valid": not missing,
        "missing": missing,
        "key_count": len(spec),
        "module_name": spec.get("module_name", ""),
        "model_count": len(spec.get("models", []) or []),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd python && uv run pytest tests/orchestrator/test_spec_keys.py -v`
Expected: 5 PASSED

- [ ] **Step 5: Commit**

```bash
git add python/src/amil_utils/orchestrator/spec_keys.py python/tests/orchestrator/test_spec_keys.py
git commit -m "feat(orch): add spec_keys library for spec.json key validation"
```

---

### Task 2: New CLI groups — `decomposition` and `spec`

**Files:**
- Create: `python/src/amil_utils/orchestrator/cli_erp_commands.py`
- Modify: `python/src/amil_utils/orchestrator/cli.py:27-59` (imports + registration)
- Test: `python/tests/orchestrator/test_cli_erp_commands.py`

The library functions already exist; this task only wires them to Click. Signatures (verified):
- `decomposition.merge_decomposition(research_dir, cwd) -> dict` (writes `.planning/research/decomposition.json`)
- `decomposition.format_decomposition_table(decomposition: dict) -> str`
- `decomposition.generate_roadmap_markdown(decomposition: dict) -> str`
- `spec_completeness.score_all_modules(...)`, `get_discussion_batches(scores, module_data)`, `get_discussion_summary(scores)`
- `module_status.module_status_init(...)` — check its exact signature with `grep -n "def module_status_init" python/src/amil_utils/orchestrator/module_status.py` before writing `decomposition init-modules`; the CLI form used by workflows is `module-status init {name} {tier} '{depends_json}'`, so mirror whatever the existing `module-status init` Click command calls.

- [ ] **Step 1: Write the failing tests**

```python
# python/tests/orchestrator/test_cli_erp_commands.py
"""Tests for the decomposition and spec CLI groups."""
from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from amil_utils.orchestrator.cli import orch_group


def _make_research(tmp_path: Path) -> Path:
    """Minimal 4-file research dir accepted by merge_decomposition."""
    research = tmp_path / ".planning" / "research"
    research.mkdir(parents=True)
    (research / "module-boundaries.json").write_text(json.dumps({
        "modules": [
            {"name": "uni_core", "models": ["uni.department"], "description": "Core"},
            {"name": "uni_fee", "models": ["uni.fee.challan"], "description": "Fees"},
        ]
    }))
    (research / "oca-analysis.json").write_text(json.dumps({
        "recommendations": [
            {"module": "uni_core", "action": "build_new"},
            {"module": "uni_fee", "action": "build_new"},
        ]
    }))
    (research / "dependency-map.json").write_text(json.dumps({
        "dependencies": [
            {"module": "uni_core", "base_depends": ["base", "mail"], "custom_depends": []},
            {"module": "uni_fee", "base_depends": ["account"], "custom_depends": ["uni_core"]},
        ]
    }))
    (research / "computation-chains.json").write_text(json.dumps({"chains": []}))
    return research


def test_decomposition_merge_and_format(tmp_path: Path) -> None:
    _make_research(tmp_path)
    runner = CliRunner()
    result = runner.invoke(orch_group, [
        "decomposition", "merge", "--cwd", str(tmp_path), "--raw",
    ])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["module_count"] == 2
    assert (tmp_path / ".planning" / "research" / "decomposition.json").exists()

    fmt = runner.invoke(orch_group, [
        "decomposition", "format", "--cwd", str(tmp_path),
    ])
    assert fmt.exit_code == 0, fmt.output
    assert "uni_core" in fmt.output
    assert "TIER" in fmt.output.upper()


def test_decomposition_roadmap_writes_file(tmp_path: Path) -> None:
    _make_research(tmp_path)
    runner = CliRunner()
    runner.invoke(orch_group, ["decomposition", "merge", "--cwd", str(tmp_path), "--raw"])
    result = runner.invoke(orch_group, [
        "decomposition", "roadmap", "--cwd", str(tmp_path), "--raw",
    ])
    assert result.exit_code == 0, result.output
    roadmap = (tmp_path / ".planning" / "ROADMAP.md").read_text()
    assert "uni_core" in roadmap
    assert roadmap.startswith("# ERP Module Roadmap")


def test_decomposition_init_modules(tmp_path: Path) -> None:
    _make_research(tmp_path)
    runner = CliRunner()
    runner.invoke(orch_group, ["decomposition", "merge", "--cwd", str(tmp_path), "--raw"])
    result = runner.invoke(orch_group, [
        "decomposition", "init-modules", "--cwd", str(tmp_path), "--raw",
    ])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["initialized"] == ["uni_core", "uni_fee"]
    status = json.loads((tmp_path / ".planning" / "module_status.json").read_text())
    assert "uni_core" in status["modules"]
    assert "uni_fee" in status["modules"]


def test_spec_check_keys_minimal(tmp_path: Path) -> None:
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({"module_name": "uni_core", "models": [{"name": "uni.x"}]}))
    runner = CliRunner()
    result = runner.invoke(orch_group, [
        "spec", "check-keys", str(spec), "--minimal", "--cwd", str(tmp_path), "--raw",
    ])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["valid"] is True
    assert payload["model_count"] == 1


def test_spec_check_keys_full_missing_fails_exit_code(tmp_path: Path) -> None:
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({"module_name": "uni_core"}))
    runner = CliRunner()
    result = runner.invoke(orch_group, [
        "spec", "check-keys", str(spec), "--cwd", str(tmp_path), "--raw",
    ])
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["valid"] is False
    assert "models" in payload["missing"]


def test_spec_score_all(tmp_path: Path) -> None:
    planning = tmp_path / ".planning"
    (planning / "modules" / "uni_core").mkdir(parents=True)
    status = {"_meta": {"version": 1}, "modules": {
        "uni_core": {"status": "planned", "tier": "foundation", "depends": []},
    }, "tiers": {}}
    (planning / "module_status.json").write_text(json.dumps(status))
    runner = CliRunner()
    result = runner.invoke(orch_group, [
        "spec", "score-all", "--cwd", str(tmp_path), "--raw",
    ])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert "scores" in payload
    assert "summary" in payload
```

NOTE for the implementer: `merge_decomposition`'s exact input schema may differ from the fixture above. Before finalizing the fixture, read `python/src/amil_utils/orchestrator/decomposition.py:27-140` and `python/tests/orchestrator/test_decomposition.py` (existing tests show the canonical 4-file fixture — copy its fixture shape verbatim). Same for `score_all_modules(...)` — read `spec_completeness.py:117-130` for its parameters (it may take `cwd` or a dict). Adjust the fixture/CLI calls so the test exercises the real contract, not an invented one. Do NOT change the library functions.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd python && uv run pytest tests/orchestrator/test_cli_erp_commands.py -v`
Expected: FAIL — `No such command 'decomposition'`

- [ ] **Step 3: Write the implementation**

```python
# python/src/amil_utils/orchestrator/cli_erp_commands.py
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


# ─── decomposition commands ───────────────────────────────────────


@click.group("decomposition")
def decomposition_grp() -> None:
    """ERP decomposition commands (merge research, format, init, roadmap)."""


@decomposition_grp.command("merge")
@_common
def decomposition_merge_cmd(cwd: str, raw: bool) -> None:
    """Merge the 4 research-agent JSON files into decomposition.json."""
    from amil_utils.orchestrator.decomposition import merge_decomposition

    research_dir = Path(cwd) / ".planning" / "research"
    result = merge_decomposition(research_dir, cwd)
    _emit({"module_count": len(result.get("modules", [])),
           "warnings": result.get("warnings", [])})


def _load_decomposition(cwd: str) -> dict:
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
    initialized = []
    for name in decomp.get("generation_order", list(module_map)):
        mod = module_map.get(name)
        if not mod:
            continue
        depends = list(mod.get("base_depends", [])) + list(mod.get("custom_depends", []))
        module_status_init(cwd, name, mod.get("tier", 1), depends)
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
    date = current_timestamp()["timestamp"][:10]
    header = f"# ERP Module Roadmap\n\nGenerated: {date}\n\n"
    out = Path(cwd) / ".planning" / "ROADMAP.md"
    out.write_text(header + body, encoding="utf-8")
    _emit({"written": str(out), "module_count": len(decomp.get("modules", []))})


# ─── spec commands ─────────────────────────────────────────────────


@click.group("spec")
def spec_grp() -> None:
    """Spec validation and completeness scoring."""


@spec_grp.command("check-keys")
@click.argument("spec_path")
@click.option("--minimal", is_flag=True, default=False,
              help="Only require module_name (generate-module pre-check)")
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
        score_all_modules,
    )

    scores, module_data = score_all_modules(cwd)
    _emit({
        "scores": scores,
        "batches": get_discussion_batches(scores, module_data),
        "summary": get_discussion_summary(scores),
    })
```

NOTE: adjust the two call sites marked by the library contracts you verified in Step 1 (`module_status_init` signature; `score_all_modules` return shape — if it returns only scores, derive `module_data` the way `spec_completeness.py`'s own callers/tests do). Keep each Click handler ≤50 lines.

Then register in `python/src/amil_utils/orchestrator/cli.py` — after line 27's import block add:

```python
from amil_utils.orchestrator.cli_erp_commands import (  # noqa: E402
    decomposition_grp,
    spec_grp,
)
```

and after `orch_group.add_command(coherence_grp)` (line 59) add:

```python
orch_group.add_command(decomposition_grp)
orch_group.add_command(spec_grp)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd python && uv run pytest tests/orchestrator/test_cli_erp_commands.py -v`
Expected: 6 PASSED

- [ ] **Step 5: Run the full orchestrator suite (regression)**

Run: `cd python && uv run pytest tests/orchestrator/ -q`
Expected: all pass (~713+ tests)

- [ ] **Step 6: Commit**

```bash
git add python/src/amil_utils/orchestrator/cli_erp_commands.py python/src/amil_utils/orchestrator/cli.py python/tests/orchestrator/test_cli_erp_commands.py
git commit -m "feat(orch): add decomposition and spec CLI groups"
```

---

### Task 3: Missing `registry` subcommands — `tiered-injection`, `update-from-spec`

**Files:**
- Modify: `python/src/amil_utils/orchestrator/cli_module_commands.py` (registry group, near line 142)
- Test: `python/tests/orchestrator/test_cli_erp_commands.py` (append)

Library functions already exist in `registry.py`: `tiered_registry_injection(cwd, module_name)` (line 284) and `update_from_spec(cwd, spec)` (line 376, takes a spec **dict**).

- [ ] **Step 1: Write the failing tests** (append to `test_cli_erp_commands.py`)

```python
def _seed_registry(tmp_path: Path) -> None:
    planning = tmp_path / ".planning"
    planning.mkdir(exist_ok=True)
    registry = {
        "_meta": {"version": 1, "last_updated": "2026-06-12T00:00:00+00:00",
                   "modules_contributing": ["uni_core"], "odoo_version": "19.0"},
        "models": {"uni.department": {"module": "uni_core", "fields": {
            "name": {"type": "Char"}}}},
    }
    (planning / "model_registry.json").write_text(json.dumps(registry))


def test_registry_tiered_injection_cli(tmp_path: Path) -> None:
    _seed_registry(tmp_path)
    status = {"_meta": {"version": 1}, "modules": {
        "uni_core": {"status": "generated", "tier": "foundation", "depends": []},
        "uni_fee": {"status": "planned", "tier": "core", "depends": ["uni_core"]},
    }, "tiers": {}}
    (tmp_path / ".planning" / "module_status.json").write_text(json.dumps(status))
    runner = CliRunner()
    result = runner.invoke(orch_group, [
        "registry", "tiered-injection", "uni_fee", "--cwd", str(tmp_path), "--raw",
    ])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert "direct" in payload


def test_registry_update_from_spec_cli(tmp_path: Path) -> None:
    _seed_registry(tmp_path)
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps({
        "module_name": "uni_fee",
        "models": [{"name": "uni.fee.challan", "fields": [
            {"name": "amount", "type": "Float"},
        ]}],
    }))
    runner = CliRunner()
    result = runner.invoke(orch_group, [
        "registry", "update-from-spec", str(spec_path), "--cwd", str(tmp_path), "--raw",
    ])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["model_count"] >= 2
    registry = json.loads((tmp_path / ".planning" / "model_registry.json").read_text())
    assert "uni.fee.challan" in registry["models"]
```

NOTE: if `tiered_registry_injection` requires module_status.json fields beyond those seeded, copy the seed shape from existing `python/tests/orchestrator/test_registry.py` fixtures.

- [ ] **Step 2: Run to verify failure**

Run: `cd python && uv run pytest tests/orchestrator/test_cli_erp_commands.py -k registry -v`
Expected: FAIL — `No such command 'tiered-injection'`

- [ ] **Step 3: Implement** (append to the registry group section of `cli_module_commands.py`)

```python
@registry_grp.command("tiered-injection")
@click.argument("module_name")
@_common
def registry_tiered_injection_cmd(module_name: str, cwd: str, raw: bool) -> None:
    from amil_utils.orchestrator.registry import tiered_registry_injection

    _emit(tiered_registry_injection(cwd, module_name))


@registry_grp.command("update-from-spec")
@click.argument("spec_path", type=click.Path(exists=True))
@_common
def registry_update_from_spec_cmd(spec_path: str, cwd: str, raw: bool) -> None:
    from amil_utils.orchestrator.registry import update_from_spec

    spec = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    result = update_from_spec(cwd, spec)
    _emit({
        "version": result["_meta"]["version"],
        "model_count": len(result["models"]),
    })
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd python && uv run pytest tests/orchestrator/test_cli_erp_commands.py -v`
Expected: all PASSED

- [ ] **Step 5: Commit**

```bash
git add python/src/amil_utils/orchestrator/cli_module_commands.py python/tests/orchestrator/test_cli_erp_commands.py
git commit -m "feat(orch): expose registry tiered-injection and update-from-spec via CLI"
```

---

### Task 4: File-lock for shared JSON state (registry + module_status)

**Files:**
- Create: `python/src/amil_utils/orchestrator/file_lock.py`
- Modify: `python/src/amil_utils/orchestrator/registry.py` (mutation fns: `remove_module_from_registry`, `update_registry`, `update_from_spec`)
- Modify: `python/src/amil_utils/orchestrator/module_status.py` (mutation fns: `module_status_init`, `module_status_transition` — confirm names via `grep -n "def " module_status.py`)
- Test: `python/tests/orchestrator/test_file_lock.py`
- Modify: `python/tests/orchestrator/test_concurrent_writes.py`

Design: O_CREAT|O_EXCL lock file (`<state-file>.lock`) with bounded retry — portable, no new deps, no fcntl. Mutations acquire the lock for their whole read-modify-write cycle, then the existing `_atomic_write_json` still does temp+rename inside. Stale-lock guard: if the lock file is older than 30s, break it.

- [ ] **Step 1: Write the failing tests**

```python
# python/tests/orchestrator/test_file_lock.py
"""Tests for the file_lock context manager."""
from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from amil_utils.orchestrator.file_lock import LockTimeout, state_lock


def test_lock_creates_and_removes_lockfile(tmp_path: Path) -> None:
    target = tmp_path / "registry.json"
    with state_lock(target):
        assert (tmp_path / "registry.json.lock").exists()
    assert not (tmp_path / "registry.json.lock").exists()


def test_lock_blocks_second_acquirer(tmp_path: Path) -> None:
    target = tmp_path / "registry.json"
    order: list[str] = []

    def worker(tag: str) -> None:
        with state_lock(target, timeout=5.0):
            order.append(f"{tag}-in")
            time.sleep(0.1)
            order.append(f"{tag}-out")

    t1 = threading.Thread(target=worker, args=("a",))
    t2 = threading.Thread(target=worker, args=("b",))
    t1.start(); t2.start(); t1.join(); t2.join()
    # critical sections must not interleave
    assert order in (["a-in", "a-out", "b-in", "b-out"],
                     ["b-in", "b-out", "a-in", "a-out"])


def test_lock_timeout_raises(tmp_path: Path) -> None:
    target = tmp_path / "registry.json"
    lock = tmp_path / "registry.json.lock"
    lock.write_text("held")
    with pytest.raises(LockTimeout):
        with state_lock(target, timeout=0.2, stale_after=999.0):
            pass


def test_stale_lock_is_broken(tmp_path: Path) -> None:
    import os
    target = tmp_path / "registry.json"
    lock = tmp_path / "registry.json.lock"
    lock.write_text("stale")
    old = time.time() - 120
    os.utime(lock, (old, old))
    with state_lock(target, timeout=1.0, stale_after=30.0):
        pass  # should succeed by breaking the stale lock
```

- [ ] **Step 2: Run to verify failure**

Run: `cd python && uv run pytest tests/orchestrator/test_file_lock.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement**

```python
# python/src/amil_utils/orchestrator/file_lock.py
"""Portable lock-file serialization for shared .planning/ JSON state.

Guards the read-modify-write cycle on model_registry.json and
module_status.json so concurrent CLI invocations cannot lose updates.
Atomic temp+rename writes (registry._atomic_write_json) remain the
crash-safety layer; this adds mutual exclusion.
"""
from __future__ import annotations

import contextlib
import os
import time
from collections.abc import Iterator
from pathlib import Path


class LockTimeout(OSError):
    """Raised when the lock cannot be acquired within the timeout."""


@contextlib.contextmanager
def state_lock(
    target: str | Path,
    timeout: float = 10.0,
    poll: float = 0.02,
    stale_after: float = 30.0,
) -> Iterator[None]:
    """Acquire `<target>.lock` exclusively; break locks older than stale_after."""
    lock_path = Path(f"{target}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    while True:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            break
        except FileExistsError:
            with contextlib.suppress(OSError):
                if time.time() - lock_path.stat().st_mtime > stale_after:
                    lock_path.unlink()
                    continue
            if time.monotonic() >= deadline:
                raise LockTimeout(f"could not acquire {lock_path}") from None
            time.sleep(poll)
    try:
        yield
    finally:
        with contextlib.suppress(OSError):
            lock_path.unlink()
```

Then in `registry.py`, wrap each mutation's body. Pattern (apply to `remove_module_from_registry`, `update_registry`, `update_from_spec` — the read of the registry MUST move inside the lock):

```python
from amil_utils.orchestrator.file_lock import state_lock

def update_from_spec(cwd: str | Path, spec: dict) -> dict:
    cwd = Path(cwd)
    with state_lock(_registry_path(cwd)):
        registry = read_registry_file(cwd)
        ... existing immutable-update logic unchanged ...
        _atomic_write_json(_registry_path(cwd), new_registry)
    return new_registry
```

If a mutation function currently exceeds 50 lines after wrapping, extract its pure-transform middle into a private `_apply_spec_to_registry(registry, spec) -> dict` helper.

Apply the same pattern to `module_status.py` mutations using `state_lock(<module_status path>)`.

- [ ] **Step 4: Upgrade the concurrency tests**

In `python/tests/orchestrator/test_concurrent_writes.py`, add (keep existing tests; update their docstrings if they assert last-writer-wins as acceptable):

```python
def test_concurrent_update_from_spec_no_lost_updates(tmp_path: Path) -> None:
    """N threads each registering a distinct module must ALL survive."""
    import threading
    from amil_utils.orchestrator.registry import read_registry_file, update_from_spec

    (tmp_path / ".planning").mkdir()
    n = 8
    def make_spec(i: int) -> dict:
        return {"module_name": f"mod_{i}",
                "models": [{"name": f"uni.model.{i}",
                             "fields": [{"name": "name", "type": "Char"}]}]}
    threads = [threading.Thread(target=update_from_spec, args=(tmp_path, make_spec(i)))
               for i in range(n)]
    for t in threads: t.start()
    for t in threads: t.join()
    registry = read_registry_file(tmp_path)
    assert len(registry["models"]) == n, sorted(registry["models"])
```

- [ ] **Step 5: Run tests**

Run: `cd python && uv run pytest tests/orchestrator/test_file_lock.py tests/orchestrator/test_concurrent_writes.py tests/orchestrator/test_registry.py tests/orchestrator/test_module_status.py -v`
Expected: all PASSED (the new no-lost-updates test would FAIL without the lock — verify by temporarily reverting registry.py if in doubt)

- [ ] **Step 6: Full orchestrator regression + commit**

```bash
cd python && uv run pytest tests/orchestrator/ -q && cd ..
git add python/src/amil_utils/orchestrator/file_lock.py python/src/amil_utils/orchestrator/registry.py python/src/amil_utils/orchestrator/module_status.py python/tests/orchestrator/test_file_lock.py python/tests/orchestrator/test_concurrent_writes.py
git commit -m "fix(orch): serialize registry/module_status mutations with lock files — no more lost updates"
```

---

### Task 5: Fix generated-test Many2one values (`test_model.py.j2`)

**Files:**
- Modify: `python/src/amil_utils/templates/shared/test_model.py.j2` (lines 37-38, 139-140, ~178-179 — three `base.main_company` sites)
- Test: Create `python/tests/test_render_tests_m2o.py`
- Check first: `grep -rn "base.main_company" python/tests/` — update any existing assertions that pin the old behavior.

- [ ] **Step 1: Write the failing test**

```python
# python/tests/test_render_tests_m2o.py
"""Generated tests must derive Many2one values from the field's comodel,
not hardcode base.main_company (audit P0-3)."""
from __future__ import annotations

from pathlib import Path

from amil_utils.renderer import render_module


SPEC = {
    "module_name": "uni_m2o_check",
    "module_title": "M2O Check",
    "odoo_version": "19.0",
    "depends": ["base"],
    "models": [{
        "name": "uni.m2o.check",
        "description": "M2O Check",
        "fields": [
            {"name": "name", "type": "Char", "required": True},
            {"name": "advisor_id", "type": "Many2one",
             "comodel_name": "uni.advisor", "required": True},
            {"name": "company_id", "type": "Many2one",
             "comodel_name": "res.company", "required": True},
        ],
    }],
    "security": {},
}


def _render(tmp_path: Path) -> str:
    result = render_module(SPEC, tmp_path)  # match render_module's real signature
    test_file = tmp_path / "uni_m2o_check" / "tests" / "test_uni_m2o_check.py"
    candidates = list((tmp_path / "uni_m2o_check" / "tests").glob("test_*.py"))
    return (test_file if test_file.exists() else candidates[0]).read_text()


def test_m2o_uses_comodel_helper(tmp_path: Path) -> None:
    content = _render(tmp_path)
    assert 'cls._m2o("uni.advisor")' in content
    assert "def _m2o(" in content


def test_res_company_still_resolves_via_helper(tmp_path: Path) -> None:
    content = _render(tmp_path)
    assert 'cls._m2o("res.company")' in content
    # the hardcoded ref must be gone
    assert 'env.ref("base.main_company")' not in content
```

NOTE: match `render_module`'s actual signature/return (read `python/src/amil_utils/renderer.py:239` area and copy how `python/tests/test_renderer.py` invokes it — including any required spec keys). The generated test filename pattern comes from the render stage; the glob fallback covers it.

- [ ] **Step 2: Run to verify failure**

Run: `cd python && uv run pytest tests/test_render_tests_m2o.py -v`
Expected: FAIL — `_m2o` not in content

- [ ] **Step 3: Modify the template**

In `test_model.py.j2`, immediately after the `setUpClass` opening (before `test_record_vals` is built — locate `def setUpClass`), add the helper as the first classmethod of the test class:

```jinja
    @classmethod
    def _m2o(cls, comodel):
        """Return a usable record id for a Many2one target.

        Searches for any existing record first (covers base/demo data),
        then falls back to creating a minimal record.
        """
        Model = cls.env[comodel]
        existing = Model.search([], limit=1)
        if existing:
            return existing.id
        vals = {}
        if "name" in Model._fields and Model._fields["name"].required:
            vals["name"] = "Test %s" % comodel
        elif "name" in Model._fields:
            vals["name"] = "Test %s" % comodel
        return Model.create(vals).id
```

Then replace ALL THREE occurrences of:

```jinja
{% elif field.type == 'Many2one' %}
            "{{ field.name }}": cls.env.ref("base.main_company").id,
```

with:

```jinja
{% elif field.type == 'Many2one' %}
            "{{ field.name }}": cls._m2o("{{ field.comodel_name or field.comodel }}"),
```

(Sites at lines 38 and 140 use `cls.env`; the third site near line 179 uses `self.env` — replace it with `self._m2o(...)` which resolves to the same classmethod.)

- [ ] **Step 4: Run tests + template regression**

Run: `cd python && uv run pytest tests/test_render_tests_m2o.py -v && uv run pytest tests/ -k "render or template" -q`
Expected: new tests PASS; fix any existing test that asserted `base.main_company` (update the assertion to the new helper call — the behavior change is the point of this task).

- [ ] **Step 5: Commit**

```bash
git add python/src/amil_utils/templates/shared/test_model.py.j2 python/tests/test_render_tests_m2o.py
git commit -m "fix(templates): generated tests derive Many2one values from comodel (P0-3)"
```

---

### Task 6: Known-models extractor + regenerated data

**Files:**
- Create: `scripts/extract_odoo_models.py`
- Test: `python/tests/orchestrator/test_extract_odoo_models.py` + fixture tree `python/tests/fixtures/odoo_source_mini/`
- Regenerate: `python/src/amil_utils/data/known_odoo_models.json`
- Create: `python/src/amil_utils/data/standard_odoo_modules.json`

Output must preserve the existing schema exactly (verified): top-level `{"_meta": {...}, "models": {"<model>": {"module": str, "fields": {"<f>": {"type": str, "comodel_name"?: str}}, "is_mixin": bool}}}`.

- [ ] **Step 1: Build the fixture tree**

```bash
mkdir -p python/tests/fixtures/odoo_source_mini/addons/sale/models
mkdir -p python/tests/fixtures/odoo_source_mini/odoo/addons/base/models
```

```python
# python/tests/fixtures/odoo_source_mini/addons/sale/models/sale_order.py
from odoo import api, fields, models


class SaleOrder(models.Model):
    _name = "sale.order"
    _description = "Sales Order"

    name = fields.Char(required=True)
    partner_id = fields.Many2one("res.partner", string="Customer")
    company_id = fields.Many2one(comodel_name="res.company")
    line_ids = fields.One2many("sale.order.line", "order_id")
    amount_total = fields.Monetary()


class SaleOrderLine(models.Model):
    _name = "sale.order.line"
    _description = "Sales Order Line"

    order_id = fields.Many2one("sale.order")
    price_unit = fields.Float()


class SaleMixin(models.AbstractModel):
    _name = "sale.mixin"
    _description = "Mixin"

    note = fields.Text()
```

```python
# python/tests/fixtures/odoo_source_mini/odoo/addons/base/models/res_partner.py
from odoo import fields, models


class ResPartner(models.Model):
    _name = "res.partner"
    _description = "Contact"

    name = fields.Char()
    email = fields.Char()
```

```python
# python/tests/fixtures/odoo_source_mini/addons/sale/__manifest__.py
{
    "name": "Sales",
    "summary": "Quotations and sales orders",
    "depends": ["base"],
}
```

- [ ] **Step 2: Write the failing tests**

```python
# python/tests/orchestrator/test_extract_odoo_models.py
"""Tests for scripts/extract_odoo_models.py against the mini fixture tree."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

FIXTURE = Path(__file__).parent.parent / "fixtures" / "odoo_source_mini"
SCRIPT = Path(__file__).parents[3] / "scripts" / "extract_odoo_models.py"

spec = importlib.util.spec_from_file_location("extract_odoo_models", SCRIPT)
mod = importlib.util.module_from_spec(spec)
sys.modules["extract_odoo_models"] = mod
spec.loader.exec_module(mod)


def test_extracts_models_with_module_and_fields() -> None:
    data = mod.extract_models(FIXTURE)
    models = data["models"]
    assert "sale.order" in models
    so = models["sale.order"]
    assert so["module"] == "sale"
    assert so["fields"]["partner_id"] == {
        "type": "Many2one", "comodel_name": "res.partner"}
    assert so["fields"]["company_id"]["comodel_name"] == "res.company"
    assert so["fields"]["amount_total"] == {"type": "Monetary"}


def test_abstract_model_marked_mixin() -> None:
    data = mod.extract_models(FIXTURE)
    assert data["models"]["sale.mixin"]["is_mixin"] is True
    assert data["models"]["sale.order"]["is_mixin"] is False


def test_base_addons_scanned_too() -> None:
    data = mod.extract_models(FIXTURE)
    assert "res.partner" in data["models"]
    assert data["models"]["res.partner"]["module"] == "base"


def test_meta_counts() -> None:
    data = mod.extract_models(FIXTURE)
    assert data["_meta"]["model_count"] == len(data["models"]) >= 4


def test_standard_modules_listing() -> None:
    mods = mod.extract_standard_modules(FIXTURE)
    assert mods["sale"]["summary"] == "Quotations and sales orders"
    assert mods["sale"]["depends"] == ["base"]
```

- [ ] **Step 3: Run to verify failure**

Run: `cd python && uv run pytest tests/orchestrator/test_extract_odoo_models.py -v`
Expected: FAIL — script missing

- [ ] **Step 4: Implement the extractor**

```python
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
    """Addon directory name owning this file (parent of models/)."""
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


def _models_in_class(cls: ast.ClassDef) -> tuple[str | None, bool, dict]:
    """Return (_name, is_mixin, fields) for a models.* class, else (None, ...)."""
    is_model = is_mixin = False
    for base in cls.bases:
        if isinstance(base, ast.Attribute) and base.attr in MODEL_BASES:
            is_model = True
            is_mixin = base.attr in ABSTRACT_BASES
    if not is_model:
        return None, False, {}
    name = None
    fields: dict = {}
    for stmt in cls.body:
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 \
                and isinstance(stmt.targets[0], ast.Name):
            target = stmt.targets[0].id
            if target == "_name":
                name = _const_str(stmt.value)
            elif isinstance(stmt.value, ast.Call):
                field = _field_from_call(stmt.value)
                if field:
                    fields[target] = field
    return name, is_mixin, fields


def extract_models(source_root: str | Path) -> dict:
    """Walk the Odoo source tree; return the known_odoo_models.json structure."""
    root = Path(source_root)
    models: dict = {}
    for py_file in sorted(root.rglob("*.py")):
        if "/tests/" in str(py_file) or py_file.name.startswith("test_"):
            continue
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        module = _module_name(py_file, root)
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                name, is_mixin, fields = _models_in_class(node)
                if not name:
                    continue
                existing = models.get(name, {"module": module, "fields": {},
                                              "is_mixin": is_mixin})
                existing["fields"] = {**fields, **existing["fields"]}
                models[name] = existing
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
```

- [ ] **Step 5: Run unit tests**

Run: `cd python && uv run pytest tests/orchestrator/test_extract_odoo_models.py -v`
Expected: 5 PASSED

- [ ] **Step 6: Regenerate the real data**

```bash
python3 scripts/extract_odoo_models.py
python3 -c "
import json
d = json.load(open('python/src/amil_utils/data/known_odoo_models.json'))
print('models:', d['_meta']['model_count'])
assert d['_meta']['model_count'] > 600, 'extraction too small — investigate'
for m in ('account.tax.group','account.payment.register','account.fiscal.year','hr.employee','fleet.vehicle','maintenance.request'):
    assert m in d['models'], f'missing {m}'
print('spot checks OK')
"
```

Expected: model_count well above 600 (Odoo 19 CE has ~800+); the six previously-missing audit models present. Check file size (`du -h`); if above ~6 MB, switch `indent=1` to compact separators `(",", ":")` and re-run.

- [ ] **Step 7: Regression — loaders still work**

Run: `cd python && uv run pytest tests/orchestrator/test_coherence.py tests/orchestrator/test_dependency_graph.py tests/ -k "known_models or comodel" -q`
Expected: PASS. If a test pinned the old `model_count: 203`, update it to assert `> 600` instead.

- [ ] **Step 8: Commit**

```bash
git add scripts/extract_odoo_models.py python/tests/orchestrator/test_extract_odoo_models.py python/tests/fixtures/odoo_source_mini python/src/amil_utils/data/known_odoo_models.json python/src/amil_utils/data/standard_odoo_modules.json
git commit -m "feat(data): AST-extract full Odoo 19 model DB + standard module list (P0-2, DC-2)"
```

---

### Task 7: De-Node `plan-module.md`

**Files:**
- Modify: `amil/workflows/plan-module.md:108-128` (registry-exists check) and `:186-200` (spec validation)

- [ ] **Step 1: Replace the registry-exists block (lines ~108-121)**

Old block starts `REGISTRY_EXISTS=$(node -e "` — replace the whole ```bash fence with:

```bash
# Check if registry exists and has content
REGISTRY_MODELS=$(amil-utils orch registry stats --raw --cwd "$(pwd)" 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('model_count',0))" 2>/dev/null || echo 0)
```

And change the following conditional text from "If registry exists and is non-empty" logic to test `[ "$REGISTRY_MODELS" -gt 0 ]`. The `tiered-injection` call two lines below stays as-is (it now exists after Task 3).

- [ ] **Step 2: Replace the spec validation block (lines ~186-200)**

Old block starts `node -e "` with the `required` key array — replace the whole fence with:

```bash
amil-utils orch spec check-keys ".planning/modules/${MODULE}/spec.json" --raw --cwd "$(pwd)"
```

Keep the surrounding prose: "If validation fails (non-zero exit): show the `missing` list from the JSON output and STOP."

- [ ] **Step 3: Verify no Node remains**

Run: `grep -n "node -e\|\.cjs\|require(" amil/workflows/plan-module.md`
Expected: no output

- [ ] **Step 4: Commit**

```bash
git add amil/workflows/plan-module.md
git commit -m "fix(workflows): plan-module uses Python CLI instead of deleted Node.js (DC-1)"
```

---

### Task 8: De-Node `generate-module.md`

**Files:**
- Modify: `amil/workflows/generate-module.md:40-95` (spec check + config reads) and `:170-180` (registry update)

- [ ] **Step 1: Replace the spec quick-check (lines ~43-50)** with:

```bash
amil-utils orch spec check-keys ".planning/modules/${MODULE}/spec.json" --minimal --raw --cwd "$(pwd)"
```

Prose: "If exit code is non-zero: show error 'Invalid spec.json' and STOP. The JSON output includes `module_name` and `model_count` for the progress display."

- [ ] **Step 2: Replace the GEN_PATH read (lines ~57-66)** with:

```bash
GEN_PATH=$(amil-utils orch config-get odoo.gen_path --raw --cwd "$(pwd)" 2>/dev/null | python3 -c "import sys,json; v=json.load(sys.stdin); print(v.get('value','') if isinstance(v,dict) else (v or ''))" 2>/dev/null || echo "")
```

(First run `amil-utils orch config-get odoo.gen_path --raw --cwd .` in a scratch project to confirm the JSON envelope key — if it emits the bare value, drop the python3 pipe. Record the verified form in the workflow.) Keep the existing `AMIL_GEN_PATH` fallback block unchanged.

- [ ] **Step 3: Replace the ADDONS_PATH read (lines ~80-93)** with:

```bash
ADDONS_PATH=$(amil-utils orch config-get odoo.addons_path --raw --cwd "$(pwd)" 2>/dev/null | python3 -c "import sys,json; v=json.load(sys.stdin); print(v.get('value','') if isinstance(v,dict) else (v or ''))" 2>/dev/null || echo "")
ADDONS_PATH="${ADDONS_PATH:-./addons}"
ADDONS_PATH=$(python3 -c "import os,sys; print(os.path.abspath(sys.argv[1]))" "$ADDONS_PATH")
mkdir -p "$ADDONS_PATH"
```

- [ ] **Step 4: Replace the registry update (lines ~172-180)** with:

```bash
amil-utils orch registry update-from-spec ".planning/modules/${MODULE}/spec.json" --raw --cwd "$(pwd)"
```

Prose: "The JSON output reports `version` and `model_count` after the merge."

- [ ] **Step 5: Verify + commit**

Run: `grep -n "node -e\|\.cjs\|require(" amil/workflows/generate-module.md`
Expected: no output

```bash
git add amil/workflows/generate-module.md
git commit -m "fix(workflows): generate-module uses Python CLI instead of deleted Node.js (DC-1)"
```

---

### Task 9: De-Node `new-erp.md`

**Files:**
- Modify: `amil/workflows/new-erp.md:240-380` (Stage B validation + entire Stage C node blocks)

- [ ] **Step 1: Replace the research-JSON validation loop (line ~247)** with:

```bash
for f in module-boundaries.json oca-analysis.json dependency-map.json computation-chains.json; do
  python3 -m json.tool ".planning/research/$f" >/dev/null 2>&1 && echo "VALID: $f" || echo "INVALID JSON: $f"
done
```

- [ ] **Step 2: Replace the Stage C library note (line ~259)** with:

```
**CLI:** `amil-utils orch decomposition <merge|format|init-modules|roadmap>` provides the merge, human-approval table, module initialization, and ROADMAP generation steps.
```

- [ ] **Step 3: Replace Step C.1 merge block** with:

```bash
amil-utils orch decomposition merge --raw --cwd "$(pwd)"
```

Keep the prose describing the 5-step merge (it documents `decomposition.py` behavior, still accurate).

- [ ] **Step 4: Replace Step C.2 format block** with:

```bash
amil-utils orch decomposition format --cwd "$(pwd)"
```

- [ ] **Step 5: Replace Step C.4 init loop (both node block AND the per-module `module-status init` loop)** with the single command:

```bash
amil-utils orch decomposition init-modules --raw --cwd "$(pwd)"
```

Prose: "This reads the approved decomposition and initializes `module_status.json` entries for every module in generation order. The JSON output lists `initialized` modules."

- [ ] **Step 6: Replace Step C.5 roadmap block** with:

```bash
amil-utils orch decomposition roadmap --raw --cwd "$(pwd)"
```

Keep the IMPORTANT note about writing to the TARGET project and the flat-format example.

- [ ] **Step 7: Verify + commit**

Run: `grep -n "node -e\|\.cjs\|require(" amil/workflows/new-erp.md`
Expected: no output

```bash
git add amil/workflows/new-erp.md
git commit -m "fix(workflows): new-erp Stage B/C use decomposition CLI instead of deleted Node.js (DC-1)"
```

---

### Task 10: De-Node `discuss-module.md` + final sweep

**Files:**
- Modify: `amil/workflows/discuss-module.md:144`

- [ ] **Step 1: Replace line 144**

Old: ``1. Score all `planned` modules using `spec-completeness.cjs:scoreAllModules()` ``
New:

```
1. Score all `planned` modules: `amil-utils orch spec score-all --raw --cwd "$(pwd)"` — returns `scores`, `batches` (grouped by discussion depth), and `summary`.
```

- [ ] **Step 2: Repo-wide sweep for dead Node references**

Run: `grep -rn "node -e\|\.cjs" amil/ commands/ agents/ docs/USER-GUIDE.md`
Expected: no hits in workflows/commands/agents. If USER-GUIDE.md or other workflow files surface additional `.cjs` references, replace them with the matching CLI command from Tasks 2-3 following the same patterns (same fix, same commit).

- [ ] **Step 3: Full test suite**

Run: `cd python && uv run pytest tests/ -m "not docker and not e2e and not e2e_slow and not odoo_ls" -q`
Expected: all pass

- [ ] **Step 4: Commit**

```bash
git add amil/workflows/discuss-module.md
git commit -m "fix(workflows): discuss-module batch scoring via spec score-all CLI (DC-1 complete)"
```

---

## Self-Review Checklist (run after Task 10)

- [ ] `grep -rn "node -e\|\.cjs" amil/ commands/` returns nothing
- [ ] `amil-utils orch decomposition --help`, `orch spec --help`, `orch registry --help` all list the new commands
- [ ] `known_odoo_models.json` model_count > 600 and the six audit models present
- [ ] Concurrency test `test_concurrent_update_from_spec_no_lost_updates` passes
- [ ] Generated test files contain `_m2o(` and never `base.main_company`
- [ ] Full suite green: `uv run pytest tests/ -m "not docker and not e2e and not e2e_slow and not odoo_ls" -q`
