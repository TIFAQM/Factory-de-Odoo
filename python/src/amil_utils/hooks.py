"""RenderHook Protocol and built-in hooks for the Odoo generation pipeline.

Provides the observe-only ``RenderHook`` Protocol, ``LoggingHook`` (console
output via ``click.echo``), ``ManifestHook`` (writes manifest on render
complete), ``CheckpointPause`` exception, and the ``notify_hooks`` helper.

These are **leaf modules** -- no imports from renderer.py to prevent circular
dependencies.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Protocol, runtime_checkable

import click

from amil_utils.manifest import GenerationManifest, StageResult, save_manifest

logger = logging.getLogger("amil.hooks")


# ---------------------------------------------------------------------------
# CheckpointPause Exception
# ---------------------------------------------------------------------------


class CheckpointPause(Exception):
    """Raised by checkpoint hooks to pause pipeline for human review."""

    def __init__(
        self,
        module_name: str,
        stage_name: str,
        message: str = "",
    ) -> None:
        self.module_name = module_name
        self.stage_name = stage_name
        self.message = message or (
            f"Checkpoint pause at stage '{stage_name}' for module '{module_name}'"
        )
        super().__init__(self.message)


# ---------------------------------------------------------------------------
# RenderHook Protocol
# ---------------------------------------------------------------------------

_STATUS_ICONS: dict[str, str] = {
    "complete": "[OK]",
    "skipped": "[--]",
    "failed": "[!!]",
    "pending": "[..]",
}


@runtime_checkable
class RenderHook(Protocol):
    """Observe-only hook protocol for the generation pipeline.

    Hooks CANNOT modify state -- same spec + different hooks = same output.
    """

    def on_preprocess_complete(
        self,
        module_name: str,
        models: list[dict],
        preprocessors_run: list[str],
    ) -> None: ...

    def on_stage_complete(
        self,
        module_name: str,
        stage_name: str,
        result: StageResult,
        artifacts: list[str],
    ) -> None: ...

    def on_render_complete(
        self,
        module_name: str,
        manifest: GenerationManifest,
    ) -> None: ...


# ---------------------------------------------------------------------------
# LoggingHook
# ---------------------------------------------------------------------------


class LoggingHook:
    """Prints stage progress to console via ``click.echo``."""

    def on_preprocess_complete(
        self,
        module_name: str,
        models: list[dict],
        preprocessors_run: list[str],
    ) -> None:
        count = len(preprocessors_run)
        click.echo(f"Preprocessing complete: {count} preprocessors ran")

    def on_stage_complete(
        self,
        module_name: str,
        stage_name: str,
        result: StageResult,
        artifacts: list[str],
    ) -> None:
        icon = _STATUS_ICONS.get(result.status, "[??]")
        click.echo(f"{icon} {stage_name} ({result.duration_ms}ms)")

    def on_render_complete(
        self,
        module_name: str,
        manifest: GenerationManifest,
    ) -> None:
        total_files = manifest.artifacts.total_files
        total_lines = manifest.artifacts.total_lines
        click.echo(f"Generation complete: {total_files} files, {total_lines} lines")


# ---------------------------------------------------------------------------
# ManifestHook
# ---------------------------------------------------------------------------


class ManifestHook:
    """Writes the generation manifest to disk on render complete."""

    def __init__(self, module_path: Path) -> None:
        self.module_path = module_path

    def on_preprocess_complete(
        self,
        module_name: str,
        models: list[dict],
        preprocessors_run: list[str],
    ) -> None:
        pass  # no-op

    def on_stage_complete(
        self,
        module_name: str,
        stage_name: str,
        result: StageResult,
        artifacts: list[str],
    ) -> None:
        pass  # no-op

    def on_render_complete(
        self,
        module_name: str,
        manifest: GenerationManifest,
    ) -> None:
        # PIPE-06: Let exceptions propagate — notify_hooks now re-raises for
        # critical methods, so callers see the real failure.
        save_manifest(manifest, self.module_path)


# ---------------------------------------------------------------------------
# notify_hooks Helper
# ---------------------------------------------------------------------------


def notify_hooks(
    hooks: list[RenderHook] | None,
    method: str,
    *args,
    **kwargs,
) -> None:
    """Call a method on all hooks, isolating exceptions (except CheckpointPause).

    If ``hooks`` is ``None`` or empty, returns immediately (zero overhead).
    ``CheckpointPause`` is always re-raised since it represents an
    intentional pipeline pause requested by a hook.
    """
    if not hooks:
        return
    # PIPE-06: Critical hooks must not fail silently — re-raise after logging.
    _CRITICAL_METHODS = frozenset({"on_render_complete", "on_stage_complete"})
    for hook in hooks:
        try:
            getattr(hook, method)(*args, **kwargs)
        except CheckpointPause:
            raise  # Intentional pause, propagate
        except Exception as exc:
            if method in _CRITICAL_METHODS:
                logger.error(
                    "Critical hook %s.%s failed: %s",
                    type(hook).__name__,
                    method,
                    exc,
                )
                logger.debug("Full traceback:", exc_info=True)
                raise
            logger.warning(
                "Hook %s.%s failed: %s",
                type(hook).__name__,
                method,
                exc,
            )
            logger.debug("Full traceback:", exc_info=True)
