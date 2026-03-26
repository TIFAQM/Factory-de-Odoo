"""Provisional Registry — Tracks "promised" models from ungenerated modules.

Ported from orchestrator/amil/bin/lib/provisional-registry.cjs (361 lines, since deleted).

Solves the forward reference problem at 90+ modules: Module 15 can reference
module 60's model because the provisional registry knows module 60 WILL
provide that model. Models graduate to the real registry when generated.
"""
from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path

# ── Deprecation ──────────────────────────────────────────────────────────────
__deprecated__ = True
_DEPRECATION_NOTICE = (
    f"{__name__} is superseded by odoo-ls validation. "
    "Use --skip-odoo-ls flag to fall back to these checks."
)

PROV_REGISTRY_FILE = "provisional_registry.json"

STANDARD_MODELS = frozenset([
    "res.partner", "res.users", "res.company", "res.currency",
    "res.country", "res.country.state", "res.config.settings",
    "ir.cron", "ir.attachment", "ir.sequence", "ir.mail_server",
    "mail.thread", "mail.activity.mixin", "mail.message",
])


def get_prov_registry_path(cwd: Path) -> Path:
    """Return the path to the provisional registry file."""
    return Path(cwd) / ".planning" / PROV_REGISTRY_FILE


def build_from_decomposition(decomposition: dict) -> dict:
    """Build provisional registry from decomposition data."""
    registry: dict = {
        "version": 1,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "source": "decomposition",
        "modules": {},
        "models": {},
        "references": [],
    }

    for mod in decomposition.get("modules", []):
        module_name = mod["name"]
        models = mod.get("models", [])
        registry["modules"][module_name] = {
            "status": "provisional",
            "model_count": len(models),
            "depends": mod.get("depends") or mod.get("base_depends") or [],
        }

        for model in models:
            model_name = model["name"]
            fields = [
                {
                    "name": f["name"],
                    "type": f.get("type"),
                    "comodel_name": f.get("comodel_name"),
                }
                for f in model.get("fields", [])
            ]
            registry["models"][model_name] = {
                "module": module_name,
                "fields": fields,
                "confidence": "high" if len(model.get("fields", [])) > 2 else "low",
                "source": "decomposition",
            }

            # Track cross-module references
            for field in model.get("fields", []):
                if field.get("comodel_name"):
                    registry["references"].append({
                        "from_module": module_name,
                        "from_model": model_name,
                        "to_model": field["comodel_name"],
                        "field_name": field["name"],
                        "type": field.get("type"),
                    })

    return registry


def update_from_spec(registry: dict, spec: dict) -> dict:
    """Update provisional registry when a spec is approved (immutable)."""
    new_reg = copy.deepcopy(registry)
    module_name = spec["module_name"]

    existing = new_reg["modules"].get(module_name, {})
    new_reg["modules"][module_name] = {
        **existing,
        "status": "spec_approved",
        "model_count": len(spec.get("models", [])),
        "depends": spec.get("depends", []),
    }

    for model in spec.get("models", []):
        model_name = model["name"]
        fields = [
            {
                "name": f["name"],
                "type": f.get("type"),
                "comodel_name": f.get("comodel_name"),
            }
            for f in model.get("fields", [])
        ]
        new_reg["models"][model_name] = {
            "module": module_name,
            "fields": fields,
            "confidence": "high",
            "source": "spec",
        }

        # Replace references for this module/model
        new_reg["references"] = [
            r for r in new_reg["references"]
            if not (r["from_module"] == module_name and r["from_model"] == model_name)
        ]
        for field in model.get("fields", []):
            if field.get("comodel_name"):
                new_reg["references"].append({
                    "from_module": module_name,
                    "from_model": model_name,
                    "to_model": field["comodel_name"],
                    "field_name": field["name"],
                    "type": field.get("type"),
                })

    return new_reg


def mark_built(registry: dict, module_name: str) -> dict:
    """Mark a module as built (immutable). Models graduate from provisional."""
    new_reg = copy.deepcopy(registry)

    if module_name in new_reg["modules"]:
        new_reg["modules"][module_name]["status"] = "built"

    for model_name, model_data in new_reg["models"].items():
        if model_data["module"] == module_name:
            new_reg["models"][model_name] = {**model_data, "source": "built"}

    return new_reg


def resolve_reference(
    model_name: str,
    real_registry: dict | None,
    prov_registry: dict | None,
) -> dict:
    """Resolve a model reference — check base, real, and provisional registries."""
    if model_name in STANDARD_MODELS:
        return {
            "found": True,
            "source": "odoo_base",
            "module": "base",
            "confidence": "certain",
        }

    if real_registry and real_registry.get("models", {}).get(model_name):
        return {
            "found": True,
            "source": "built",
            "module": real_registry["models"][model_name]["module"],
            "confidence": "certain",
        }

    if prov_registry and prov_registry.get("models", {}).get(model_name):
        prov_model = prov_registry["models"][model_name]
        return {
            "found": True,
            "source": prov_model["source"],
            "module": prov_model["module"],
            "confidence": prov_model.get("confidence"),
        }

    return {"found": False, "source": None, "module": None, "confidence": None}


def analyze_forward_references(prov_registry: dict) -> dict:
    """Analyze all forward references to find deps, unresolved, and circular risks."""
    forward_refs: list[dict] = []
    unresolved_refs: list[dict] = []
    circular_risks: list[dict] = []

    module_refs: dict[str, set[str]] = {}

    for ref in prov_registry.get("references", []):
        source_module = ref["from_module"]
        target_model = ref["to_model"]

        target_model_data = prov_registry.get("models", {}).get(target_model)
        if not target_model_data:
            resolved = resolve_reference(target_model, None, prov_registry)
            if not resolved["found"]:
                unresolved_refs.append({
                    "from_module": source_module,
                    "from_model": ref["from_model"],
                    "to_model": target_model,
                    "field": ref.get("field_name"),
                })
            continue

        target_module = target_model_data["module"]
        if target_module == source_module:
            continue

        if target_model_data.get("source") != "built":
            forward_refs.append({
                "from_module": source_module,
                "to_module": target_module,
                "from_model": ref["from_model"],
                "to_model": target_model,
                "field": ref.get("field_name"),
            })

        if source_module not in module_refs:
            module_refs[source_module] = set()
        module_refs[source_module].add(target_module)

    # Detect circular references (A→B and B→A)
    seen_pairs: set[str] = set()
    for mod_a, refs_a in module_refs.items():
        for mod_b in refs_a:
            if mod_b in module_refs and mod_a in module_refs[mod_b]:
                pair = ":".join(sorted([mod_a, mod_b]))
                if pair not in seen_pairs:
                    seen_pairs.add(pair)
                    circular_risks.append({
                        "pair": pair,
                        "modules": [mod_a, mod_b],
                        "refs_a_to_b": [
                            r for r in forward_refs
                            if r["from_module"] == mod_a and r["to_module"] == mod_b
                        ],
                        "refs_b_to_a": [
                            r for r in forward_refs
                            if r["from_module"] == mod_b and r["to_module"] == mod_a
                        ],
                    })

    return {
        "forward_refs": forward_refs,
        "unresolved_refs": unresolved_refs,
        "circular_risks": circular_risks,
    }


def find_critical_chains(prov_registry: dict) -> list[list[str]]:
    """Find critical dependency chains (4+ modules long). Returns top 10."""
    adj: dict[str, list[str]] = {}
    modules = prov_registry.get("modules", {})
    for mod_name, mod_data in modules.items():
        adj[mod_name] = [
            d for d in (mod_data.get("depends") or []) if d in modules
        ]

    chains: list[list[str]] = []
    visited: set[str] = set()

    def dfs(node: str, chain: list[str]) -> None:
        if node in visited:
            return
        visited.add(node)
        chain.append(node)

        deps = adj.get(node, [])
        unvisited_deps = [d for d in deps if d not in visited]
        if not deps or not unvisited_deps:
            if len(chain) >= 4:
                chains.append(list(chain))
        else:
            for dep in unvisited_deps:
                dfs(dep, chain)

        chain.pop()
        visited.discard(node)

    for mod in adj:
        dfs(mod, [])

    chains.sort(key=len, reverse=True)
    return chains[:10]


def save(cwd: Path, registry: dict) -> None:
    """Save provisional registry to disk."""
    path = get_prov_registry_path(cwd)
    path.write_text(json.dumps(registry, indent=2), encoding="utf-8")


def load(cwd: Path) -> dict | None:
    """Load provisional registry from disk."""
    path = get_prov_registry_path(cwd)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
