# Factory de Odoo — Codebase Index (E2E)

> Generated 2026-06-11. Full structural map of the repository for fast navigation.
> Repo: https://github.com/TIFAQM/Factory-de-Odoo · Branch: master

## What This Repo Is

A **PRD-to-ERP compiler**: describe a business in plain English, get a coherent suite of
production-grade Odoo modules (17.0/18.0/19.0). Two halves:

| Half | Location | License | Role |
|------|----------|---------|------|
| **Orchestrator** | `python/src/amil_utils/orchestrator/` | MIT | Decomposes a PRD into 20+ modules, tracks cross-module state in `.planning/`, drives sequential/tier-parallel generation |
| **Pipeline** | `python/src/amil_utils/` (rest) | LGPL-3 | Pure library: renders one Odoo module from a `spec.json` via 73 Jinja2 templates, then validates (odoo-ls → pylint-odoo → Docker) |

Layering: **`/amil:*` commands → workflows → orchestrator agents → pipeline (belt) agents → `amil-utils` Python CLI**.

Stats: ~4,093 tests (713 orchestrator + 3,380 pipeline) · ~37.9K Python LOC · 29 agents ·
46 slash commands · 41 workflows · 73 templates · 15 knowledge files. Python 3.12 only, zero Node.js runtime.

---

## Top-Level Layout

```
Factory-de-Odoo/
├── agents/            29 AI agent definitions (20 orchestrator + 9 pipeline "belt")
├── amil/              Extension content: workflows/ (41), knowledge/ (15), references/, templates/, defaults.json
├── commands/amil/     46 slash commands (/amil:* prefix)
├── hooks/             3 event hooks (update check, statusline, context monitor)
├── python/            The amil-utils package (src + 4,093 tests)
├── docker/dev/        Persistent Odoo 19 CE + PostgreSQL 16 dev instance
├── scripts/           odoo-dev.sh, verify-odoo-dev.py, benchmark_odoo_ls.py
├── tools/             odoo-ls (vendored Rust LSP binary, 67MB) + odoo-source/19.0 (full checkout, 1.4GB)
├── docs/              USER-GUIDE.md, context-monitor.md, superpowers/ specs & plans
├── install.py         Multi-runtime installer (Claude Code / Gemini / Codex / OpenCode)
├── .mcp.json          "odoo-introspection" MCP server (stdio → amil_utils.mcp.server)
└── .github/workflows/ test.yml (pytest on push/PR, ~2,900 CI tests), auto-label-issues.yml
```

---

## 1. Pipeline Library — `python/src/amil_utils/` (~28.7K LOC)

**Entry point:** console script `amil-utils` → `amil_utils.cli:main` (Click).
Commands: `render-module`, `validate`, `registry`, `search`, `render`, `list-templates`.

### render-module flow
spec validation (Pydantic) → preprocessing (24 auto-discovered preprocessors) →
context7 enrichment → iterative-mode detection → 8 render stages
(manifest, models, views, security, data, tests, extensions, iterative_merges) →
optional live-Odoo verification → manifest persistence (`.amil-manifest.json`) → hooks.

### Core modules (selected)
| File | LOC | Purpose |
|------|-----|---------|
| `renderer.py` / `renderer_stages.py` / `renderer_orchestration.py` | 379/1392/537 | Jinja2 sandbox env, named render stages, phase orchestration |
| `renderer_context*.py` | ~900 | Template context builders (models, fields, views, cross-cutting features) |
| `spec_schema.py` + `spec_schema_inner.py` | 906 | Pydantic v2 spec: ModuleSpec > ModelSpec > FieldSpec, 40+ models, typo detection |
| `spec_differ*.py` | 931 | Spec diff with destructiveness classification (DeepDiff) |
| `auto_fix.py` + `auto_fix_docker.py` | 1788 | pylint-odoo auto-fix loops (W8161, W8113, W8111…) + Docker fixes (XML errors, missing ACLs, manifest load order, chatter) |
| `registry.py` | 437 | Cross-module model registry (ModelRegistry) |
| `migration_generator.py` | 793 | Pre/post migration scripts from spec diffs |
| `verifier.py` | 338 | Live-Odoo environment verifier via XML-RPC (non-blocking warnings) |
| `manifest.py`, `hooks.py`, `mermaid.py`, `i18n_extractor.py`, `edition.py` | — | Generation metadata, render hooks, diagrams, i18n, Enterprise-alternatives lookup |

### Subpackages
- **`commands/`** (~1.5K LOC) — Click command implementations (render, validate, registry, search, extend, docker, mermaid).
- **`validation/`** (~5.3K LOC) — `pylint_runner.py`, `docker_runner.py` (compose sandbox install+test), `persistent_docker.py`, `semantic_checks.py` (1,817 LOC, 40+ checks), `log_parser.py`, plus the **odoo-ls quartet**: `odoo_ls_client.py` (645 LOC headless LSP over Content-Length-framed JSON-RPC), `odoo_ls_config.py`, `odoo_ls_validator.py`, `odoo_ls_fixer.py`.
- **`preprocessors/`** (24 modules, ~3.8K LOC) — spec transformers auto-discovered via `@register_preprocessor`; includes security, computation_chains, approval, audit, webhooks, bulk_operations, document_management, pakistan_hec, academic_calendar.
- **`templates/`** (73 Jinja2) — `17.0|18.0|19.0/` (3 each: model.py.j2, action.xml.j2, view_form.xml.j2) + `shared/` (64: manifest, views, security CSV/XML, wizards, portal ×6, website ×8, OWL components ×5, tests, migrations, cron/mail data, res.config.settings, docker-compose).
- **`mcp/`** (820 LOC) — FastMCP stdio server, 9 tools (check_connection, list_models, get_model_fields, list_installed_modules, check_module_dependency, get_view_arch, get_view_inheritance_chain, get_model_relations, find_field_conflicts); rate-limited, HTTPS-enforced for remote hosts.
- **`logic_writer/`** (1.6K LOC) — TODO-stub detection (AST), per-stub context assembly, complexity classification → `.amil-stubs.json` for the amil-logic-writer agent.
- **`search/`** (1.4K LOC) — ChromaDB OCA semantic index (`build_oca_index`, `search_modules`), GitHub fork-and-extend setup. Needs `GITHUB_TOKEN`.
- **`iterative/`** (888 LOC) — spec stash/diff (`.amil-spec.json`), affected-stage mapping, three-way conflict detection, filled-stub injection on re-render.
- **`data/`** — `module_renames.json` (Odoo 19: 130 model + 51 field renames, merges), `known_odoo_models.json`, `enterprise_modules.json`, docker-compose templates, odoo.conf.

**pyproject.toml:** package `amil-utils` 0.1.0; deps jinja2, click, pylint-odoo, deepdiff, pydantic; extras mcp, chromadb/PyGithub/gitpython, dev. Coverage gate 80%. Markers: `docker`, `e2e`, `e2e_slow`, `odoo_ls`, `timeout`.

---

## 2. Orchestrator — `python/src/amil_utils/orchestrator/` (~9.3K LOC, 29 modules)

**Entry point:** `amil-utils orch <group> <cmd>` — `cli.py` registers 14 Click groups + ~10 standalone commands (60+ subcommands total). Handlers are thin: parse args → call library fn → emit JSON.

### Command groups
`state` (load/get/patch/advance-plan/decisions/blockers) · `phase` (add/insert/remove/complete, decimal numbering for urgent inserts e.g. 06.1) · `phases list` · `roadmap` · `requirements` · `milestone complete` · `validate health [--repair]` · `template` · `frontmatter` · `init` (12 workflow bootstrappers: new-project, plan-phase, execute-phase, resume…) · `dep-graph` (build/order/tiers/can-generate) · `module-status` · `registry` · `cycle-log` · `coherence check`.

### Key modules
| File | LOC | Purpose |
|------|-----|---------|
| `phase.py` / `phase_query.py` | 863/247 | Phase CRUD with atomic removal + transaction manifests; read-only queries |
| `state.py` | 689 | STATE.md ops with YAML-frontmatter sync, decisions/blockers/metrics |
| `init_commands.py` | 656 | Workflow initialization handlers |
| `cli_groups.py` / `cli_module_commands.py` / `cli_helpers.py` | 636/257/42 | Click wiring |
| `core.py` / `commands.py` | 561/562 | Shared utils (config, git exec, slugs, phase compare); standalone commands |
| `registry.py` / `provisional_registry.py` | 434/322 | Model registry CRUD, atomic writes with backup/rollback; provisional staging |
| `dependency_graph.py` | 384 | Topo-sort, cycle detection, tier grouping (foundation→core→business_logic→extended→deep), **Odoo 19 rename validation** via `data/module_renames.json` |
| `parallel_executor.py` | 113 | ThreadPoolExecutor tier-parallel generate/validate (max 3, result order preserved) |
| `coherence.py` | 294 | Structural checks (many2one targets, duplicate models, computed depends, security groups) — deprecated in favor of odoo-ls |
| `health.py` / `health_checks.py` | 126/440 | E001–E012/W001–W006 checks + repairs for `.planning/` |
| `module_status.py` | 233 | Lifecycle state machine (below) |
| `decomposition.py` | 223 | Merges 4 PRD-research agent JSON outputs into decomposition.json + roadmap |
| `milestone.py`, `roadmap.py`, `frontmatter.py`, `template.py`, `config.py`, `cycle_log.py`, `uat_checkpoint.py`, `spec_completeness.py`, `circular_dep.py` | — | Milestone archiving, ROADMAP.md parsing, YAML frontmatter, template fill, config.json, append-only cycle log, UAT gates, spec completeness, circular deps |

### Module lifecycle state machine
```
planned → spec_approved → generated → checked → shipped (terminal)
backward: spec_approved→planned, generated→spec_approved  (auto-removes registry entries)
```

### `.planning/` state directory (per ERP project)
`STATE.md` (frontmatter+body), `ROADMAP.md`, `REQUIREMENTS.md`, `PROJECT.md`, `MILESTONES.md`,
`config.json`, `module_status.json` (+tiers), `model_registry.json` (+.bak),
`ERP_CYCLE_LOG.md`, `phases/NN-slug/NN-MM-PLAN.md|SUMMARY.md`, `modules/<name>/spec.json|CONTEXT.md`,
`milestones/`, `diagrams/`, `todos/`. All JSON writes atomic (UUID temp + rename).

---

## 3. Agents — `agents/` (29)

**Orchestrator agents (20):** amil-codebase-mapper, amil-debugger, amil-erp-decomposer, amil-executor, amil-integration-checker, amil-module-questioner, amil-module-researcher, amil-phase-researcher, amil-plan-checker, amil-planner, amil-project-researcher, amil-research-synthesizer, amil-roadmapper, amil-spec-generator, amil-spec-reviewer, amil-verifier, amil-website-architect, amil-validator, amil-scaffold (dual-mode), …

**Pipeline (belt) agents (9):** amil-belt-executor (runs `amil-utils render-module`), amil-belt-verifier, amil-scaffold, amil-model-gen, amil-view-gen, amil-security-gen (`privilege_id` for 19.0), amil-test-gen, amil-logic-writer (fills `.amil-stubs.json` TODOs), amil-search (ChromaDB/OCA), amil-extend (fork + `_inherit` companion), amil-nyquist-auditor.

**PRD decomposition** spawns 4 parallel researchers: Module Boundary Analyzer, OCA Registry Checker, Dependency Mapper, Computation Chain Identifier → merged by `decomposition.py`.

---

## 4. Commands — `commands/amil/` (46, all `/amil:*`)

- **Project/milestone:** new-project, new-erp, new-milestone, complete-milestone, audit-milestone, plan-milestone-gaps
- **Phase:** add-phase, insert-phase, remove-phase, phases, plan-phase, discuss-phase, list-phase-assumptions, execute-phase, validate-phase, verify-work
- **Module:** plan-module, discuss-module, batch-discuss, generate-module, validate-module, search-modules, research-module, extend-module, index-modules, module-history
- **Quality:** add-tests, coherence-report, health, live-uat, debug
- **Session/util:** progress, pause-work, resume-work, check-todos, add-todo, set-profile, settings, update, cleanup, quick, run-prd, map-codebase, help, reapply-patches, transition

## 5. Workflows — `amil/workflows/` (41)

Mirror the commands (each command typically loads its workflow): new-project, new-erp, autonomous-erp-loop, run-prd, plan/research/execute/verify/validate/discuss/discovery-phase, execute-plan, transition, plan/generate/discuss-module, map-codebase, diagnose-issues, live-uat, verify-work, add-tests, audit-milestone, health, progress, resume-project, pause-work, todos, cleanup, settings, set-profile, update, help.

## 6. Knowledge, References, Templates — `amil/`

- **`knowledge/`** (15+ files, 80+ WRONG/CORRECT pairs): MASTER.md (integration guide), models, views, actions, security, manifest, wizards, controllers, owl, inheritance, testing, i18n, inventory, accounting, data; `custom/` for user extensions (auto-included).
- **`references/`**: model-profiles, checkpoints, continuation-format, phase-argument-parsing, decimal-phase-calculation, questioning, tdd, ui-brand, verification-patterns, git-integration, planning-config, module-questions.json.
- **`templates/`** (25+ doc templates): project, roadmap, state, config.json, phase-prompt, research, context, summary, uat, validation, discovery, debug, continue-here, milestone, requirements, codebase/, research-project/.
- **`defaults.json`**: odoo_version (19.0), edition, output_dir, license, author, website, api_keys.

---

## 7. Infrastructure

- **`install.py`** — installs `amil-utils` (pip -e) and copies agents/amil/commands/hooks into `~/.claude/` (or `CLAUDE_CONFIG_DIR`); `--global|--local|--uninstall`; multi-runtime (Claude Code, Gemini CLI, Codex, OpenCode).
- **`hooks/`** — `amil-check-update.py` (SessionStart, npm version check, cached), `amil-statusline.py` (statusline: model/task/dir/context bar; writes bridge file `/tmp/claude-ctx-{session}.json`), `amil-context-monitor.py` (PostToolUse; WARNING ≤35% / CRITICAL ≤25% remaining, debounced).
- **`scripts/`** — `odoo-dev.sh` (start/stop/status/reset/logs for dev instance), `verify-odoo-dev.py` (XML-RPC smoke test), `benchmark_odoo_ls.py` (LSP perf, GO/NO-GO: index <120s, first diagnostic <10s).
- **`docker/dev/`** — `odoo:19.0` + `postgres:16`, named volumes, localhost-only :8069, hardened (caps dropped, no-new-privileges), `.env.example`, demo data off.
- **`tools/odoo-ls/`** — vendored 12MB Rust LSP binary + typeshed stubs. **`tools/odoo-source/19.0/`** — full 1.4GB Odoo checkout (reference for validation/LSP/MCP).
- **`.github/workflows/test.yml`** — Python 3.12 + uv, `pytest -m "not docker and not e2e and not e2e_slow and not odoo_ls"` (~2,900 tests, 10-min timeout).
- **`.mcp.json`** — `odoo-introspection` stdio server → `python3 -m amil_utils.mcp.server` against `http://localhost:8069` / db `odoo_dev`.

## 8. Tests — `python/tests/`

114 top-level pipeline test files + 31 orchestrator test files (~7.9K LOC) = ~4,093 tests.
Groups: rendering, spec/diff, auto-fix, preprocessors (15 feature files), search, iterative, logic_writer, validation (pylint/docker/odoo-ls), MCP, CLI, integration/E2E (test_golden_path, test_integration_e2e, test_e2e_github), portal/website/OWL, plus orchestrator state-machine/registry/graph/concurrency suites. Fixtures in `tests/fixtures/`.

```bash
cd python
uv run pytest tests/ -q                  # all (~2 min)
uv run pytest tests/orchestrator/ -q     # orchestrator only
uv run pytest tests/ -m "not docker" -q  # no Docker
```

## 9. Conventions & Rules (from CLAUDE.md / CONVENTIONS.md)

1. Sequential generation — one module at a time through the belt (tier-parallel validation up to 3).
2. All state changes via CLI subcommands — never hand-edit STATE.md.
3. Atomic writes for JSON state; immutable data patterns (new objects, no mutation).
4. Python-first, zero Node.js; 80%+ coverage enforced; pipeline is a pure library.
5. Limits: functions ≤50 lines, Python files ≤800 lines, docs ≤500 lines, nesting ≤4.
6. Odoo patterns: `self.env._()` not `_()` (W8161); manifest load order security→data→wizard views→model views→dashboard→menu; chatter via `{% if chatter %}`; 19.0 uses `models.Constraint()`, `@api.private`, `Domain` class, `privilege_id`.

## 10. Versioning

CHANGELOG spans 1.20.4 → 1.22.4 (2026-03). Recent: context-monitor hook, Nyquist validation layer, Codex/Gemini/OpenCode runtime support, Odoo 19 deep integration (v4): rename detection, `@api.private` hardening, parallel Docker validation, race-condition-free state.
