"""UAT Checkpoint Manager — Tracks verification checkpoints and checklists.

Ported from orchestrator/amil/bin/lib/uat-checkpoint.cjs (168 lines, since deleted).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from amil_utils.validation.module_name import MODULE_NAME_RE as _MODULE_NAME_RE


def is_checkpoint_due(
    installed_modules: list,
    last_checkpoint_at: int,
    interval: int = 10,
) -> bool:
    """Determine if a wave checkpoint is due."""
    return len(installed_modules) - last_checkpoint_at >= interval


def generate_checklist(modules: list[dict], registry: dict) -> dict:
    """Generate verification checklist for a set of modules.

    Returns {"per_module": [...], "cross_module": [...]}.
    """
    per_module: list[dict] = []
    cross_module: list[dict] = []

    for mod in modules:
        flows: list[str] = []

        for model in mod.get("models", []):
            # Workflow model → test state transitions
            workflows = mod.get("workflow") or []
            wf = next(
                (w for w in workflows if w.get("model") == model.get("name")),
                None,
            )
            if wf:
                desc = model.get("description", model.get("name", "record"))
                states = " → ".join(wf.get("states", []))
                flows.append(f"Create a {desc} → transition through states: {states}")

            # Computed fields → test computation
            computed = [
                f for f in model.get("fields", []) if f.get("compute")
            ]
            if computed:
                names = ", ".join(f["name"] for f in computed)
                flows.append(f"Enter data → verify computed fields update: {names}")

        # Reports → test generation
        reports = mod.get("reports", [])
        if reports:
            report_names = ", ".join(r.get("name", "") for r in reports)
            flows.append(f"Generate report(s): {report_names}")

        per_module.append({
            "module": mod.get("module_name") or mod.get("name", ""),
            "description": mod.get("summary") or mod.get("description", ""),
            "flows": flows if flows else [
                "Create a record → verify form and list views work"
            ],
        })

    # Detect cross-module flows via shared model references
    for mod in modules:
        for model in mod.get("models", []):
            for field in model.get("fields", []):
                comodel = field.get("comodel_name")
                if not comodel:
                    continue
                other_mod = next(
                    (
                        m for m in modules
                        if m is not mod
                        and any(
                            mm.get("name") == comodel
                            for mm in m.get("models", [])
                        )
                    ),
                    None,
                )
                if other_mod:
                    cross_module.append({
                        "modules": [
                            mod.get("module_name") or mod.get("name", ""),
                            other_mod.get("module_name") or other_mod.get("name", ""),
                        ],
                        "flow": (
                            f"Create {model.get('name', '')} with reference to "
                            f"{comodel} → verify data flows correctly"
                        ),
                    })

    return {"per_module": per_module, "cross_module": cross_module}


def record_result(
    cwd: Path,
    module_name: str,
    result: str,
    feedback: str | None = None,
) -> dict:
    """Record UAT result for a module."""
    if not _MODULE_NAME_RE.match(module_name):
        raise ValueError(
            f"Invalid module name: '{module_name}' (must match [a-z][a-z0-9_]*)"
        )

    uat_dir = Path(cwd) / ".planning" / "modules" / module_name
    uat_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).isoformat()
    data = {
        "module": module_name,
        "result": result,
        "feedback": feedback,
        "timestamp": timestamp,
    }
    result_file = uat_dir / "uat-result.json"
    result_file.write_text(json.dumps(data, indent=2), encoding="utf-8")

    # Write detailed feedback for failures
    if result == "fail" and feedback:
        feedback_file = uat_dir / "uat-feedback.md"
        content = "\n".join([
            f"# UAT Failure: {module_name}",
            "",
            f"**Date:** {timestamp}",
            "**Result:** FAIL",
            "",
            "## Feedback",
            "",
            feedback,
            "",
            "## Action Required",
            "",
            "Module will be re-generated with this feedback incorporated.",
        ])
        feedback_file.write_text(content, encoding="utf-8")

    return data


def get_uat_summary(cwd: Path, module_names: list[str]) -> dict:
    """Get UAT summary for all modules."""
    results = {"pass": 0, "minor": 0, "fail": 0, "skip": 0, "untested": 0}
    details: list[dict] = []

    for name in module_names:
        result_file = Path(cwd) / ".planning" / "modules" / name / "uat-result.json"
        if result_file.exists():
            data = json.loads(result_file.read_text(encoding="utf-8"))
            key = data.get("result", "untested")
            results[key] = results.get(key, 0) + 1
            details.append(data)
        else:
            results["untested"] += 1
            details.append({"module": name, "result": "untested"})

    return {"summary": results, "details": details}
