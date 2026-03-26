"""Business logic for the ``render-module`` CLI command.

Pure Python -- no Click dependency.  Returns structured data so callers
can decide how to display results.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

_logger = logging.getLogger(__name__)


def _find_registry_path() -> Path:
    """Return the path to the model registry JSON file (relative to cwd)."""
    return Path(".planning/model_registry.json")


def execute_render_module(
    spec_file: str,
    output_dir: str,
    *,
    no_context7: bool = False,
    fresh_context7: bool = False,
    skip_validation: bool = False,
    resume: bool = False,
    force: bool = False,
    dry_run: bool = False,
    ols_client: Any = None,
) -> dict[str, Any]:
    """Render a complete Odoo module from a JSON specification file.

    Returns a result dict with keys:
        - files: list[str] -- rendered file paths
        - warnings: list[dict] -- render warnings
        - pending_conflicts: list[str] -- pending conflict file paths
        - stub_report: dict | None -- stub report summary
        - registry_update: dict | None -- registry update info
        - mermaid_paths: list[str] -- generated diagram paths
        - validation: dict | None -- semantic validation result
        - error: str | None -- error message if failed

    Args:
        ols_client: Optional OdooLSClient for odoo-ls structural validation.
            When None (the default), OLS validation is skipped.

    Raises:
        SystemExit with code 1 is NOT raised here -- callers decide on
        exit behaviour.
    """
    from pydantic import ValidationError as PydanticValidationError

    from amil_utils.renderer import get_template_dir, render_module
    from amil_utils.spec_schema import format_validation_errors
    from amil_utils.verifier import build_verifier_from_env

    result: dict[str, Any] = {
        "files": [],
        "warnings": [],
        "pending_conflicts": [],
        "stub_report": None,
        "registry_update": None,
        "mermaid_paths": [],
        "validation": None,
        "error": None,
    }

    # Load spec
    try:
        spec = json.loads(Path(spec_file).read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        result["error"] = f"Error reading spec file: {exc}"
        return result

    # Validate required spec fields
    required_fields = ["module_name"]
    missing = [f for f in required_fields if f not in spec]
    if missing:
        result["error"] = f"Missing required fields in spec: {', '.join(missing)}"
        return result

    template_dir = get_template_dir()
    output_path = Path(output_dir)

    # Phase 54: Load manifest for resume and instantiate hooks
    resume_manifest = None
    if resume:
        from amil_utils.manifest import load_manifest

        module_name = spec["module_name"]
        resume_manifest = load_manifest(output_path / module_name)
        if resume_manifest is None:
            result.setdefault("info", [])
            result["info"] = result.get("info", []) + [
                "No previous manifest found. Running full generation."
            ]

    try:
        from amil_utils.hooks import LoggingHook, ManifestHook

        module_name = spec["module_name"]
        render_hooks = [
            LoggingHook(),
            ManifestHook(module_path=output_path / module_name),
        ]

        verifier = build_verifier_from_env()
        files, warnings = render_module(
            spec,
            template_dir,
            output_path,
            verifier=verifier,
            no_context7=no_context7,
            fresh_context7=fresh_context7,
            hooks=render_hooks,
            resume_from=resume_manifest,
            force=force,
            dry_run=dry_run,
            ols_client=ols_client,
        )
        result["files"] = [str(f) for f in files]
        result["warnings"] = [
            {
                "check_type": w.check_type,
                "subject": w.subject,
                "message": w.message,
                "suggestion": w.suggestion,
            }
            for w in warnings
        ]

        # Phase 60: Pending conflicts summary
        pending_dir = output_path / module_name / ".amil-pending"
        if pending_dir.exists():
            pending_files = [
                str(f.relative_to(pending_dir))
                for f in pending_dir.rglob("*")
                if f.is_file()
            ]
            result["pending_conflicts"] = pending_files

        # Logic Writer: generate stub report
        try:
            from amil_utils.logic_writer import generate_stub_report
            from amil_utils.registry import ModelRegistry as _StubRegistry

            stub_reg: _StubRegistry | None = None
            try:
                stub_reg_path = _find_registry_path()
                stub_reg = _StubRegistry(stub_reg_path)
                stub_reg.load()
                stub_reg.load_known_models()
            except Exception:
                stub_reg = None

            stub_report = generate_stub_report(
                module_dir=output_path / module_name,
                spec=spec,
                registry=stub_reg,
            )
            result["stub_report"] = {
                "total_stubs": stub_report.total_stubs,
                "budget_count": stub_report.budget_count,
                "quality_count": stub_report.quality_count,
                "report_path": str(stub_report.report_path),
            }
        except Exception as exc:
            result["stub_report"] = {"error": str(exc)}

        # Post-render semantic validation
        if not skip_validation:
            from amil_utils.validation.semantic import semantic_validate

            validation = semantic_validate(output_path / module_name)
            result["validation"] = {
                "has_errors": validation.has_errors,
                "object": validation,
            }
            if validation.has_errors:
                result["error"] = "Semantic validation failed. Module NOT registered."
                return result

        # Post-render registry update
        try:
            from amil_utils.registry import ModelRegistry

            reg_path = _find_registry_path()
            reg = ModelRegistry(reg_path)
            reg.load()
            reg.load_known_models()

            vr = reg.validate_comodels(spec)
            reg_warnings = [str(w) for w in vr.warnings]
            reg_errors = [str(e) for e in vr.errors]

            inferred = reg.infer_depends(spec)
            reg.register_module(spec["module_name"], spec)
            reg.save()
            model_count = len(spec.get("models", []))

            result["registry_update"] = {
                "model_count": model_count,
                "module_name": spec["module_name"],
                "warnings": reg_warnings,
                "errors": reg_errors,
                "inferred_depends": list(inferred) if inferred else [],
            }

            # Auto-generate mermaid diagrams (best-effort)
            if not skip_validation:
                try:
                    from amil_utils.mermaid import generate_module_diagrams

                    docs_dir = output_path / module_name / "docs"
                    docs_dir.mkdir(parents=True, exist_ok=True)
                    generate_module_diagrams(
                        module_name=module_name,
                        spec=spec,
                        registry=reg,
                        output_dir=docs_dir,
                    )
                    result["mermaid_paths"] = [
                        str(docs_dir / "dependencies.mmd"),
                        str(docs_dir / "er_diagram.mmd"),
                    ]
                except Exception:
                    _logger.debug("Mermaid diagram generation failed", exc_info=True)
        except Exception:
            _logger.debug("Registry update failed (non-blocking)", exc_info=True)

    except PydanticValidationError as exc:
        formatted = format_validation_errors(exc, spec.get("module_name", "unknown"))
        result["error"] = formatted
    except Exception as exc:
        result["error"] = f"Error rendering module: {exc}"

    return result
