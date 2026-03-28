<p align="center">
  <img src="assets/factory-logo.svg?v=2" alt="Factory de Odoo" width="500"/>
</p>

<h1 align="center">Factory de Odoo</h1>

<p align="center">
  <strong>PRD-to-ERP in One Command</strong><br/>
  Describe your business in plain English. Get a full suite of production-grade Odoo modules — models, views, security, tests, and i18n — with cross-module coherence guaranteed.
</p>

<p align="center">
  <a href="#quick-start">Quick Start</a> &bull;
  <a href="#how-it-works">How It Works</a> &bull;
  <a href="#architecture">Architecture</a> &bull;
  <a href="#whats-new">What's New</a> &bull;
  <a href="#commands">Commands</a> &bull;
  <a href="#testing">Testing</a> &bull;
  <a href="#contributing">Contributing</a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Odoo-17.0%20%7C%2018.0%20%7C%2019.0-875A7B?logo=odoo&logoColor=white" alt="Odoo Version"/>
  <img src="https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white" alt="Python"/>
  <img src="https://img.shields.io/badge/Tests-4%2C093%20passing-brightgreen" alt="Tests"/>
  <img src="https://img.shields.io/badge/Coverage-80%25%2B-brightgreen" alt="Coverage"/>
  <img src="https://img.shields.io/badge/License-MIT%20%2F%20LGPL--3-blue" alt="License"/>
</p>

---

## What Is This?

**Factory de Odoo** turns a Product Requirements Document into a complete, multi-module Odoo ERP — not one module at a time, but an entire coherent system where every model reference, security group, and menu hierarchy is verified across module boundaries.

Think of it as a **compiler for ERPs**: you write the spec, the factory assembles production-ready Odoo modules with validated cross-references, security rules, and tests that actually pass.

| Component | Role | Tech |
|-----------|------|------|
| **Orchestrator** (`python/src/amil_utils/orchestrator/`) | Decomposes an ERP PRD into 20+ modules, tracks cross-module state, drives sequential generation | Python 3.12 (`amil_utils.orchestrator`), 713 tests |
| **Pipeline** (`python/src/amil_utils/`) | Pure library — renders individual Odoo modules from JSON specs using 9 AI agents, 73 Jinja2 templates, and Docker validation | Python 3.12, 3,380 tests |

**Combined:** 4,093 tests &bull; 37,900+ Python LOC &bull; 29 AI agents &bull; 73 Jinja2 templates &bull; 46 slash commands &bull; 15 knowledge files

---

## What's New

### Odoo 19 Deep Integration (v4)

Factory de Odoo now has **the most comprehensive Odoo 19.0 support of any module generator in the ecosystem** — and it's not even close. Here's what landed:

**Version-Aware Validation** — The dependency graph now knows about Odoo 19's massive rename wave (130 models renamed, 51 fields renamed, 25+ modules merged). Generate a module that depends on `hr_contract`? The system warns you: *"Module 'hr_contract' was renamed to 'hr' in Odoo 19.0"* — before you waste 5 minutes waiting for Docker to tell you the same thing.

**Field-Level Rename Detection** — Not just modules. If your spec references `res.users.groups_id`, the coherence checker catches it: *"Field 'groups_id' was renamed to 'group_ids' in Odoo 19.0."* This catches bugs that would survive all the way to production in any other tool.

**`@api.private` Security Hardening** — Every internal helper method in generated Odoo 19 modules now gets the `@api.private` decorator automatically. This prevents accidental RPC exposure of business logic — a security win with zero effort from the developer.

**`Domain` Class Support** — Templates are ready for Odoo 19's new `from odoo.fields import Domain` with Python operators (`|`, `&`, `~`), replacing the deprecated `expression.OR()`/`expression.AND()` pattern.

**`models.Constraint()` Class** — The 19.0 template uses the new object-oriented constraint syntax instead of the deprecated `_sql_constraints` tuples. Modules generated for 17.0/18.0 get a `MIGRATION_NOTES.md` explaining exactly what to change when upgrading.

**Parallel Docker Validation** — Validate up to 3 modules simultaneously with memory-aware concurrency. Each validation stack gets a UUID-suffixed Docker Compose project — zero port conflicts, zero shared state. A 20-module ERP validates in ~3.5 minutes instead of 10.

**Race-Condition-Free State Management** — All shared state files (registry, module status) now use UUID-suffixed temp files for atomic writes. Concurrent CLI invocations can't corrupt your `.planning/` directory anymore.

### Semantic Validation with odoo-ls

Factory de Odoo ships with a **headless LSP client** that talks to Odoo's own language server (`odoo-ls`) over JSON-RPC. This means generated modules get the same semantic validation that Odoo developers see in their IDE — missing imports, undefined model references, invalid field types — all caught automatically before Docker even starts. The traditional `coherence.py` checks are still available as a fallback (`--skip-odoo-ls`), but odoo-ls is the primary validation path now.

Four files power this integration:
- `odoo_ls_client.py` — Headless LSP process management with thread-safe stdin/stdout framing
- `odoo_ls_config.py` — Per-project server configuration
- `odoo_ls_validator.py` — Diagnostic collection and severity mapping
- `odoo_ls_fixer.py` — Auto-remediation for common LSP diagnostics

### Website & Portal Module Generation

The latest pipeline release (F16) added a complete **website module generation subsystem** — 13 new templates covering everything from portal controllers to SEO menus:

- Portal views (list, detail, editable detail) with `website.published.mixin`
- Website controllers with proper routing decorators
- Portal access rules and security
- SEO-ready static pages and menu hierarchies
- Website assets (CSS, JS) scaffolding
- A dedicated `amil-website-architect` agent for portal architecture design

### MCP Server — Live Odoo Introspection

The built-in MCP (Model Context Protocol) server bridges your AI coding assistant directly to a running Odoo instance via XML-RPC. Query models, inspect fields, read view inheritance chains, and verify record rules — all without leaving your terminal. Rate-limited (30 req/min per tool, 100 req/min global) with HTTPS enforcement for remote instances and API key masking in all logs.

### Multi-AI Runtime Support

Factory de Odoo runs on **any AI coding assistant** — not just Claude Code. Full installer and runtime support for:
- **Claude Code** (primary)
- **Gemini CLI** (full workflow support, `AfterTool` hooks)
- **Codex** (multi-agent config, `request_user_input` mapping)
- **OpenCode** (runtime config directory detection)

All 46 slash commands, 29 agents, and 41 workflows work across runtimes.

---

## How It Works

```
                          YOU
                           |
                    "Build me a university ERP with
                     fee management, timetabling,
                     student records, and exams"
                           |
              +------------v-----------+
              |      ORCHESTRATOR      |  Decomposes PRD into 20+ modules
              |  (Cross-Module Brain)  |  Orders by dependency graph
              +------------+-----------+  Maintains model registry
                           |
           For each module, sequentially:
                           |
              +------------v-----------+
              |       PIPELINE         |  9 agents generate code
              | (Single-Module Belt)   |  73 Jinja2 templates render
              +------------+-----------+  Docker validates output
                           |
              +------------v-----------+
              |    COHERENCE CHECK     |  Many2one targets valid?
              |  (Back to Orchestrator)|  No duplicate models?
              +------------+-----------+  Renamed fields caught?
                           |
                    Production-Grade
                     Odoo Modules
```

### The Key Innovation: Cross-Module Coherence

Most code generators produce isolated modules. Factory de Odoo maintains a **Model Registry** — a central index of every model, field, and relation across all generated modules. When generating module #15, the system knows about all 14 previously generated modules and ensures:

- Every `Many2one` points to a model that actually exists
- No two modules define the same model name
- Computed field dependencies resolve across module boundaries
- Security groups referenced in ACLs are defined somewhere
- Menu hierarchies are consistent
- **Odoo 19 renames are caught at spec time** — not at Docker install time

### What No Other Tool Does

| Capability | Factory de Odoo | `odoo-bin scaffold` | `bobtemplates.odoo` | Gemini-Odoo-Generator |
|-----------|:-:|:-:|:-:|:-:|
| AI-powered generation | 29 agents | -- | -- | 1 agent |
| Cross-module coherence | Model Registry | -- | -- | -- |
| Multi-version templates | 17 / 18 / 19 | Current only | Manual | -- |
| Semantic validation (odoo-ls) | Headless LSP client | -- | -- | -- |
| Docker validation | pylint + install + tests | -- | -- | -- |
| Odoo 19 rename detection | 130 models + 51 fields | -- | -- | -- |
| OCA semantic search | ChromaDB | -- | -- | -- |
| Website/portal generation | 13 templates + architect agent | -- | -- | -- |
| MCP server (live introspection) | XML-RPC bridge, rate-limited | -- | -- | -- |
| `@api.private` auto-generation | On all internal methods | -- | -- | -- |
| Parallel validation | Up to 3 concurrent stacks | -- | -- | -- |
| Multi-AI runtime | Claude, Gemini, Codex, OpenCode | -- | -- | -- |

---

## Quick Start

### Prerequisites

| Requirement | Version | Purpose |
|-------------|---------|---------|
| **Python** | 3.12 | Orchestrator CLI and pipeline |
| **Docker + Compose v2** | Latest | Module validation |
| **uv** | Latest | Python package manager |
| **An AI coding assistant** | Claude Code, Gemini CLI, Codex, or OpenCode | Drives the commands |

> **Why Python 3.12 specifically?** Odoo 17.0-19.0 supports 3.10-3.12. Python 3.13+ introduces breaking changes in `ast` and `importlib` that cause validation failures.

### Installation

**1. Clone the repository:**

```bash
git clone https://github.com/TIFAQM/Factory-de-Odoo.git
cd Factory-de-Odoo
```

**2. Install the Python package:**

```bash
cd python
uv venv --python 3.12
uv pip install -e ".[dev]"
cd ..
```

> This creates a `.venv` in `python/`, installs `amil-utils` in editable mode with both the pipeline and orchestrator CLI, and pulls all dev dependencies (pytest, pylint-odoo, etc.).

**3. Verify installation:**

```bash
# Orchestrator CLI loads correctly
amil-utils orch --help

# Run all tests — skip Docker tests if Docker isn't running
cd python && uv run pytest tests/ -m "not docker" --tb=short -q && cd ..
```

### Configuration

When using Factory de Odoo to generate modules for your ERP project, create a `.planning/config.json` in your project root:

```json
{
  "odoo": {
    "gen_path": "/absolute/path/to/Factory-de-Odoo",
    "odoo_version": "19.0",
    "edition": "community",
    "addons_path": "./addons"
  }
}
```

Or set the environment variable:

```bash
export AMIL_GEN_PATH="/absolute/path/to/Factory-de-Odoo"
```

### Your First ERP

In your AI coding assistant, run:

```
/amil:new-erp
```

Then describe your business:

> "I need a university ERP with student registration, fee invoicing, exam scheduling, and timetable management"

The orchestrator will:
1. Decompose your description into individual modules with a dependency graph
2. Discuss each module to capture domain-specific requirements
3. Generate spec.json files with cross-module coherence checks
4. Invoke the pipeline belt to render each module sequentially
5. Validate every module with pylint-odoo + Docker install + Docker tests

For a single standalone module (no ERP decomposition):

```
/amil:generate-module
```

---

## Architecture

```
Factory-de-Odoo/
|
+-- agents/                          # 29 AI agents (20 orchestrator + 9 pipeline)
+-- amil/                            # Extension content
|   +-- workflows/                   # 41 workflow definitions
|   +-- references/                  # Reference docs
|   +-- templates/                   # Document templates
|   +-- knowledge/                   # 15 Odoo knowledge files (80+ examples)
+-- commands/amil/                   # 46 slash commands (/amil:*)
+-- hooks/                           # 3 event hooks
|
+-- python/                          # Python library
|   +-- src/amil_utils/              # Rendering engine, validation, search
|   +-- src/amil_utils/orchestrator/ # 27 Python modules, 60+ Click commands
|   +-- src/amil_utils/templates/    # 73 Jinja2 templates (17.0/18.0/19.0/shared)
|   +-- tests/                       # 4,093 tests (pytest)
+-- docker/                          # Odoo 19 + PostgreSQL 16 dev instance
```

### Orchestrator

Manages the full ERP lifecycle — PRD decomposition, cross-module state, and sequential generation. All orchestrator logic lives in the `amil_utils.orchestrator` Python package, accessible via `amil-utils orch <command>`.

**Module Lifecycle:**
```
planned --> spec_approved --> generated --> checked --> shipped
```

Backward transitions are supported with automatic cleanup — reverting `spec_approved` to `planned` automatically removes stale entries from the model registry.

**Core Library (`amil_utils.orchestrator/`):**

| Module | Purpose |
|--------|---------|
| `registry.py` | Central model/field tracking with UUID-safe atomic writes and rollback |
| `coherence.py` | Structural checks + Odoo 19 field/model rename detection |
| `module_status.py` | State machine with validated transitions and backward cleanup hooks |
| `dependency_graph.py` | Version-aware topological sort with Odoo 19 rename validation |
| `state.py` | Session state persistence with custom frontmatter preservation |
| `config.py` | Project configuration with strict type coercion |
| `phase.py` / `phase_query.py` | Phase planning with atomic removal and transaction manifests |
| `parallel_executor.py` | Tier-parallel generation AND validation (up to 3 concurrent) |
| `cli.py` / `cli_groups.py` | 60+ Click CLI commands with graceful error handling |

**PRD Decomposition** spawns 4 parallel research agents:
1. **Module Boundary Analyzer** — functional domains, model proposals
2. **OCA Registry Checker** — build/extend/skip recommendations per module
3. **Dependency Mapper** — cross-domain references, circular dependency risk
4. **Computation Chain Identifier** — data flow across models

### Pipeline

Generates a single Odoo module from a JSON specification. Pipeline is a pure library — no user-facing commands. All interaction flows through the orchestrator's `/amil:` commands.

**9 Generation Agents:**

| Agent | Role |
|-------|------|
| `amil-scaffold` | Initial module structure (manifest, dirs, init files) |
| `amil-model-gen` | Python model classes with ORM fields |
| `amil-view-gen` | XML views (form, tree, kanban, search) |
| `amil-security-gen` | ACLs, record rules, security groups (`privilege_id` for 19.0!) |
| `amil-test-gen` | Python test cases (unit + integration) |
| `amil-logic-writer` | Business logic (computed fields, onchange, constraints) |
| `amil-validator` | pylint-odoo + Docker validation |
| `amil-search` | ChromaDB semantic search across OCA repositories |
| `amil-extend` | Fork-and-extend existing modules via `_inherit` |

**Validation Pipeline (3 tiers):**
```
odoo-ls (semantic) --> pylint-odoo (lint) --> Docker Install + Tests --> Auto-Fix (up to 5 iterations)
```

The pipeline starts with **odoo-ls semantic validation** (LSP-powered, catches undefined references and type errors), then runs **pylint-odoo** for style/convention checks, and finally **Docker install + test execution** for runtime verification. Now with **parallel validation** — up to 3 modules validated concurrently with memory-aware concurrency capping.

**Auto-Fix resolves common issues automatically:**
- Missing `mail.thread` inheritance when chatter XML exists
- Unused Python imports (AST-based removal)
- XML parse errors and manifest load order issues
- Missing ACL entries and security group references
- pylint-odoo violations (W8161, W8113, W8111 with multi-pattern resilience)

**73 Jinja2 Templates** across 4 version directories:

| Directory | Contents |
|-----------|----------|
| `templates/17.0/` | Odoo 17.0-specific view and model patterns |
| `templates/18.0/` | Odoo 18.0-specific patterns |
| `templates/19.0/` | Odoo 19.0-specific patterns — `models.Constraint()`, `@api.private`, `Domain` class, `privilege_id` |
| `templates/shared/` | Cross-version templates (models, security, Docker, migration notes) |

---

## Commands

All 46 commands use the `/amil:` prefix. Run `/amil:help` for the full reference.

### Project Lifecycle

| Command | Description |
|---------|-------------|
| `/amil:new-project` | Initialize a new Amil project |
| `/amil:new-milestone` | Start a new milestone cycle |
| `/amil:complete-milestone` | Archive completed milestone |
| `/amil:audit-milestone` | Audit milestone completion |
| `/amil:plan-milestone-gaps` | Create phases for milestone gaps |

### Phase Workflow

| Command | Description |
|---------|-------------|
| `/amil:research-phase` | Research before planning |
| `/amil:discuss-phase` | Gather context through questions |
| `/amil:plan-phase` | Create detailed phase plan |
| `/amil:execute-phase` | Execute plans with wave-based parallelization |
| `/amil:validate-phase` | Validate phase consistency |
| `/amil:verify-work` | Validate completed work through conversational UAT |
| `/amil:add-tests` | Generate tests for completed phase |

### ERP Module Generation

| Command | Description |
|---------|-------------|
| `/amil:new-erp` | Initialize ERP project from PRD |
| `/amil:discuss-module` | Interactive module discussion with domain templates |
| `/amil:plan-module` | Generate `spec.json` with coherence check |
| `/amil:generate-module` | Generate module via pipeline belt |
| `/amil:validate-module` | Run pylint-odoo + Docker validation (now parallel!) |
| `/amil:search-modules` | Semantic search across OCA/GitHub |
| `/amil:research-module` | Research patterns for a module need |
| `/amil:extend-module` | Fork and extend existing module |
| `/amil:index-modules` | Build/update ChromaDB module index |

### Automation & Reporting

| Command | Description |
|---------|-------------|
| `/amil:run-prd` | Full PRD-to-ERP generation cycle |
| `/amil:batch-discuss` | Auto-discuss underspecified modules |
| `/amil:coherence-report` | Cross-module coherence analysis |
| `/amil:live-uat` | Browser-based UAT verification |
| `/amil:module-history` | Generation history timeline |
| `/amil:phases` | Generation phases and progress |

### Utility & Session

| Command | Description |
|---------|-------------|
| `/amil:progress` | Project progress dashboard |
| `/amil:health` | Diagnose planning directory health |
| `/amil:debug` | Systematic debugging with state persistence |
| `/amil:quick` | Quick single-task execution |
| `/amil:pause-work` | Create context handoff for session break |
| `/amil:resume-work` | Resume from previous session |
| `/amil:help` | Show all commands and usage |

---

## Testing

```bash
cd python

# Run all tests (~4,093 tests, ~2 min)
uv run pytest tests/ -q

# Orchestrator tests only (~713 tests)
uv run pytest tests/orchestrator/ -q

# Skip Docker-dependent tests
uv run pytest tests/ -m "not docker" -q

# Skip E2E tests (require GITHUB_TOKEN)
uv run pytest tests/ -m "not e2e" -q

# Specific test suites
uv run pytest tests/test_golden_path.py -v     # Golden path: render + Docker install
uv run pytest tests/test_integration_e2e.py -v  # Integration: schema alignment
uv run pytest tests/test_api_private.py -v      # @api.private decorator generation

# Coverage report
uv run pytest tests/ --cov=amil_utils --cov-report=html
```

**Test Markers:**

| Marker | Requires | Description |
|--------|----------|-------------|
| `@pytest.mark.docker` | Docker daemon | Container-based validation tests |
| `@pytest.mark.e2e` | `GITHUB_TOKEN` | End-to-end with GitHub API |
| `@pytest.mark.e2e_slow` | `GITHUB_TOKEN` | Full OCA index build (200+ repos) |

---

## Dev Instance

A persistent Odoo 19 CE + PostgreSQL 16 development instance for manual testing.

> **First-time setup:** Copy the example env file before starting:
> ```bash
> cp docker/dev/.env.example docker/dev/.env
> ```

```bash
cd docker

# Start the instance
bash scripts/odoo-dev.sh start
# Access at http://localhost:8069 (admin / admin)

# Stop (data preserved)
bash scripts/odoo-dev.sh stop

# Reset (destroys all data)
bash scripts/odoo-dev.sh reset
```

---

## Knowledge Base

15 domain knowledge files with 80+ WRONG/CORRECT example pairs that prevent AI hallucinations — now fully updated for Odoo 19.0 patterns:

```
amil/knowledge/
+-- MASTER.md        # Integration guide
+-- models.md        # ORM fields, computed, constraints, @api.private, Domain class
+-- views.md         # Forms, trees, kanban, search
+-- security.md      # ACLs, record rules, groups (privilege_id for 19.0)
+-- manifest.md      # __manifest__.py patterns
+-- inheritance.md   # Model/view inheritance
+-- testing.md       # Test assertions, mocking
+-- i18n.md          # Translation extraction
+-- wizards.md       # Transient models
+-- controllers.md   # HTTP controllers
+-- actions.md       # Window actions
+-- data.md          # XML/CSV data files
+-- accounting.md    # Invoicing, payments, journals
+-- inventory.md     # Warehouse, stock moves, pickings
+-- owl.md           # OWL JavaScript framework
```

Extend the knowledge base by adding `.md` files to `knowledge/custom/` — they are automatically included during generation.

---

## Environment Variables

| Variable | Required | Default | Purpose |
|----------|----------|---------|---------|
| `AMIL_GEN_PATH` | No | `.planning/config.json` | Path to pipeline directory |
| `GITHUB_TOKEN` | No | -- | GitHub API for OCA search (raises rate limit) |
| `ODOO_VERSION` | No | `19.0` | Target Odoo version |
| `ODOO_DEV_PORT` | No | `8069` | Dev instance port |

---

## Project Stats

| Metric | Value |
|--------|-------|
| Total Tests | **4,093** (713 orchestrator + 3,380 pipeline) |
| Python LOC | **37,900+** |
| Orchestrator Modules | **27** Python modules, 60+ Click CLI commands |
| Jinja2 Templates | **73** (17.0 / 18.0 / 19.0 / shared) |
| AI Agents | **29** (20 orchestrator + 9 pipeline) |
| Slash Commands | **46** (all `/amil:*` prefix) |
| Knowledge Files | **15** (80+ example pairs, Odoo 19.0 ready) |
| Odoo Versions | **3** (17.0, 18.0, 19.0) |
| Odoo 19 Renames Tracked | **130 models + 51 fields** |

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, coding standards, and contribution guidelines.

### Core Principles

1. **Sequential generation** — one module at a time through the belt
2. **Atomic writes** — UUID-safe temp files, transaction manifests for multi-step ops
3. **Immutable data** — create new objects, never mutate existing
4. **Pure Python** — zero Node.js runtime dependency, only Python 3.12
5. **80%+ test coverage** — enforced across all components
6. **Pipeline is a pure library** — no user-facing commands, all interaction through orchestrator
7. **Version-aware everything** — templates, validation, and coherence checks adapt per Odoo version

---

## License

- **Orchestrator**: MIT
- **Pipeline**: LGPL-3.0

---

<p align="center">
  Built with <a href="https://claude.ai/code">Claude Code</a>
</p>
