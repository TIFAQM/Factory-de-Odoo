"""Shared Click CLI helpers — _common decorator and _emit output function.

Extracted to avoid circular imports between cli.py and cli_groups.py.
"""
from __future__ import annotations

import json
from typing import Any, Callable

import click


def _common(fn):
    """Apply --cwd and --raw options to a Click command."""
    fn = click.option(
        "--raw", is_flag=True, default=False, help="Raw output mode (compact JSON)"
    )(fn)
    fn = click.option(
        "--cwd",
        default=".",
        type=click.Path(exists=True),
        help="Project root directory",
    )(fn)
    return fn


def _emit(data: dict) -> None:
    """Emit JSON output. Uses --raw flag from Click context when available."""
    ctx = click.get_current_context(silent=True)
    raw = ctx.params.get("raw", True) if ctx else True
    if raw:
        click.echo(json.dumps(data))
    else:
        click.echo(json.dumps(data, indent=2))


def _safe_emit(fn: Callable[..., dict], *args: Any, **kwargs: Any) -> None:
    """Call fn and emit result, catching common errors."""
    try:
        _emit(fn(*args, **kwargs))
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        _emit({"error": str(exc)})
