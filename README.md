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
  <a href="#commands">Commands</a> &bull;
  <a href="#testing">Testing</a> &bull;
  <a href="#contributing">Contributing</a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Odoo-17.0%20%7C%2018.0%20%7C%2019.0-875A7B?logo=odoo&logoColor=white" alt="Odoo Version"/>
  <img src="https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white" alt="Python"/>
  <img src="https://img.shields.io/badge/Tests-4017%20passing-brightgreen" alt="Tests"/>
  <img src="https://img.shields.io/badge/License-MIT%20%2F%20LGPL--3-blue" alt="License"/>
</p>

---

## What Is This?

**Factory de Odoo** turns a Product Requirements Document into a complete, multi-module Odoo ERP — not one module at a time, but an entire coherent system where every model reference, security group, and menu hierarchy is verified across module boundaries.

| Component | Role | Tech |
|-----------|------|------|
| **Orchestrator** (`python/src/amil_utils/orchestrator/`) | Decomposes an ERP PRD into 20+ modules, tracks cross-module state, drives sequential generation | Python 3.12 (`amil_utils.orchestrator`), 698 tests |
| **Pipeline** (`python/src/amil_utils/`) | Pure library — renders individual Odoo modules from JSON specs using 9 AI agents, 60 Jinja2 templates, and Docker validation | Python 3.12, 3,319 tests |

**Combined:** 4,017 tests &bull; 33,200+ Python LOC &bull; 27 orchestrator modules &bull; 60 Jinja2 templates &bull; 28 AI agents &bull; 46 slash commands &bull; 12 knowledge files

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
              | (Single-Module Belt)   |  60 Jinja2 templates render
              +------------+-----------+  Docker validates output
                           |
              +------------v-----------+
              |    COHERENCE CHECK     |  Many2one targets valid?
              |  (Back to Orchestrator)|  No duplicate models?
              +------------+-----------+  Security groups exist?
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
+-- agents/                          # 28 AI agents (19 orchestrator + 9 pipeline)
+-- amil/                            # Extension content
|   +-- workflows/                   # 41 workflow definitions
|   +-- references/                  # Reference docs
|   +-- templates/                   # Document templates
|   +-- knowledge/                   # 12 Odoo knowledge files (80+ examples)
+-- commands/amil/                   # 46 slash commands (/amil:*)
+-- hooks/                           # 3 event hooks
|
+-- python/                          # Python library
|   +-- src/amil_utils/              # Rendering engine, validation, search
|   +-- src/amil_utils/orchestrator/ # 27 Python modules, 60+ Click commands
|   +-- src/amil_utils/templates/    # 60 Jinja2 templates (17.0/18.0/19.0/shared)
|   +-- tests/                       # ~4,017 tests (pytest)
+-- docker/                          # Odoo 19 + PostgreSQL 16 dev instance
```

### Orchestrator

Manages the full ERP lifecycle — PRD decomposition, cross-module state, and sequential generation. All orchestrator logic lives in the `amil_utils.orchestrator` Python package, accessible via `amil-utils orch <command>`.

**Module Lifecycle:**
```
planned --> spec_approved --> generated --> checked --> shipped
```

**Core Library (`amil_utils.orchestrator/`):**

| Module | Purpose |
|--------|---------|
| `registry.py` | Central model/field tracking with atomic writes and rollback |
| `coherence.py` | 4 structural checks across module boundaries |
| `module_status.py` | State machine with validated transitions |
| `dependency_graph.py` | Topological sort for generation order |
| `state.py` | Session state persistence (STATE.md YAML frontmatter) |
| `config.py` | Project configuration management |
| `phase.py` / `phase_query.py` | Phase planning and execution tracking |
| `cli.py` / `cli_groups.py` | 60+ Click CLI commands |

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
| `amil-security-gen` | ACLs, record rules, security groups |
| `amil-test-gen` | Python test cases (unit + integration) |
| `amil-logic-writer` | Business logic (computed fields, onchange, constraints) |
| `amil-validator` | pylint-odoo + Docker validation |
| `amil-search` | ChromaDB semantic search across OCA repositories |
| `amil-extend` | Fork-and-extend existing modules via `_inherit` |

**Validation Pipeline:**
```
pylint-odoo --> Docker Install --> Docker Tests --> Auto-Fix (up to 5 iterations)
```

**Auto-Fix resolves common issues automatically:**
- Missing `mail.thread` inheritance when chatter XML exists
- Unused Python imports (AST-based removal)
- XML parse errors and manifest load order issues
- Missing ACL entries and security group references
- pylint-odoo violations (W8161, W8113, etc.)

**60 Jinja2 Templates** across 4 version directories:

| Directory | Contents |
|-----------|----------|
| `templates/17.0/` | Odoo 17.0-specific view and model patterns |
| `templates/18.0/` | Odoo 18.0-specific patterns |
| `templates/19.0/` | Odoo 19.0-specific patterns (primary target) |
| `templates/shared/` | Cross-version templates (models, security, Docker) |

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
| `/amil:validate-module` | Run pylint-odoo + Docker validation |
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

# Run all tests (~4,017 tests, ~3 min)
uv run pytest tests/ -q

# Orchestrator tests only (~698 tests)
uv run pytest tests/orchestrator/ -q

# Skip Docker-dependent tests
uv run pytest tests/ -m "not docker" -q

# Skip E2E tests (require GITHUB_TOKEN)
uv run pytest tests/ -m "not e2e" -q

# Specific test suites
uv run pytest tests/test_golden_path.py -v     # Golden path: render + Docker install
uv run pytest tests/test_integration_e2e.py -v  # Integration: schema alignment

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

A persistent Odoo 19 CE + PostgreSQL 16 development instance for manual testing:

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

12 domain knowledge files with 80+ WRONG/CORRECT example pairs that prevent AI hallucinations:

```
amil/knowledge/
+-- MASTER.md        # Integration guide
+-- models.md        # ORM fields, computed, constraints
+-- views.md         # Forms, trees, kanban, search
+-- security.md      # ACLs, record rules, groups
+-- manifest.md      # __manifest__.py patterns
+-- inheritance.md   # Model/view inheritance
+-- testing.md       # Test assertions, mocking
+-- i18n.md          # Translation extraction
+-- wizards.md       # Transient models
+-- controllers.md   # HTTP controllers
+-- actions.md       # Window actions
+-- data.md          # XML/CSV data files
```

Extend the knowledge base by adding `.md` files to `knowledge/custom/` — they are automatically included during generation.

---

## Environment Variables

| Variable | Required | Default | Purpose |
|----------|----------|---------|---------|
| `AMIL_GEN_PATH` | No | `.planning/config.json` | Path to pipeline directory |
| `GITHUB_TOKEN` | No | — | GitHub API for OCA search (raises rate limit) |
| `ODOO_VERSION` | No | `19.0` | Target Odoo version |
| `ODOO_DEV_PORT` | No | `8069` | Dev instance port |

---

## Project Stats

| Metric | Value |
|--------|-------|
| Total Tests | **4,017** (698 orchestrator + 3,319 pipeline) |
| Python LOC | **33,200+** |
| Orchestrator Modules | **27** Python modules, 60+ Click CLI commands |
| Jinja2 Templates | **60** (17.0 / 18.0 / 19.0 / shared) |
| AI Agents | **28** (19 orchestrator + 9 pipeline) |
| Slash Commands | **46** (all `/amil:*` prefix) |
| Knowledge Files | **12** (80+ example pairs) |
| Odoo Versions | **3** (17.0, 18.0, 19.0) |

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, coding standards, and contribution guidelines.

### Core Principles

1. **Sequential generation** — one module at a time through the belt
2. **Atomic writes** — write complete JSON files, never partial updates
3. **Immutable data** — create new objects, never mutate existing
4. **Pure Python** — zero Node.js runtime dependency, only Python 3.12
5. **80%+ test coverage** — enforced across all components
6. **Pipeline is a pure library** — no user-facing commands, all interaction through orchestrator

---

## License

- **Orchestrator**: MIT
- **Pipeline**: LGPL-3.0

---

<p align="center">
  Built with <a href="https://claude.ai/code">Claude Code</a>
</p>
