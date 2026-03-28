## Compact Instructions

Your context window will be automatically compacted as it approaches its limit,
allowing you to continue working indefinitely from where you left off.
Therefore, do not stop tasks early due to token budget concerns.
As you approach your token budget limit, save your current progress and state
to memory before the context window refreshes. Always be as persistent and
autonomous as possible and complete tasks fully, even if the end of your budget
is approaching. Never artificially stop any task early regardless of context remaining.

Auto-compact triggers at ~95% capacity. Manual `/compact` at logical breakpoints
is preferred to prevent mid-task disruption.

---

## Factory de Odoo

### Project
- **Repo:** https://github.com/Inshal5Rauf1/Factory-de-Odoo
- **Purpose:** PRD-to-ERP pipeline for generating 90+ Odoo modules
- **Branch:** `factory-upgrades` (based off `main`)

### Architecture
```
agents/                — 28 AI agent definitions (19 orchestrator + 9 pipeline)
amil/                  — Workflows, references, templates, knowledge base
commands/amil/         — 46 slash commands (/amil:* prefix)
hooks/                 — 3 event hooks
python/src/amil_utils/ — Python library: rendering, validation, orchestrator, search
```
All orchestrator logic lives in `amil_utils.orchestrator` (Python 3.12). Zero Node.js runtime dependency.

### Key Paths
| Component | Path |
|-----------|------|
| Orchestrator CLI | `amil-utils orch <command>` |
| Orchestrator src | `python/src/amil_utils/orchestrator/` |
| Orchestrator tests | `python/tests/orchestrator/` |
| Pipeline src | `python/src/amil_utils/` |
| Pipeline tests | `python/tests/` |
| Commands | `commands/amil/` |
| Workflows | `amil/workflows/` |
| Hooks | `hooks/amil-*.py` |
| Knowledge base | `amil/knowledge/` |
| Templates (Jinja2) | `python/src/amil_utils/templates/` |
| Templates (doc) | `amil/templates/` |
| Auto-fix | `python/src/amil_utils/auto_fix.py` |
| MCP server | `python/src/amil_utils/mcp/server.py` |

### Module Lifecycle
```
planned -> spec_approved -> generated -> checked -> shipped
```
Modules progress sequentially. Only one module generates at a time.

### Pipeline Agents
| Agent | Role |
|-------|------|
| `amil-scaffold` | Initial module structure |
| `amil-model-gen` | Python model classes |
| `amil-view-gen` | XML views |
| `amil-security-gen` | ACLs, record rules |
| `amil-test-gen` | Test cases |
| `amil-logic-writer` | Business logic |
| `amil-validator` | pylint-odoo + Docker validation |
| `amil-search` | ChromaDB semantic search |
| `amil-extend` | Fork-and-extend modules |

### Odoo Patterns
- `self.env._()` not standalone `_()` in model methods (W8161)
- Manifest load order: security → data → wizard views → model views → dashboard → menu
- Chatter uses `{% if chatter %}` flag, not `'mail' in depends`
- Python 3.12 (works across Odoo 17.0-19.0)

### Rules
1. **Sequential generation only** — one module at a time through the belt
2. **All state changes through CLI subcommands** — never edit STATE.md manually
3. **Atomic writes for JSON state** — write complete files, never partial updates
4. **Immutable data patterns** — create new objects, never mutate existing
5. **Python-first** — all logic in `amil_utils.orchestrator`
6. **Zero Node.js runtime dependency** — Python 3.12 only
7. **80%+ test coverage** — enforced across all components
8. **Pipeline is a pure library** — no user-facing commands

### Development
```bash
cd python
uv run pytest tests/ -q                    # Full suite (~4,017 tests)
uv run pytest tests/orchestrator/ -q       # Orchestrator only (~698 tests)
uv run pytest tests/ -m "not docker" -q    # Skip Docker tests
```
