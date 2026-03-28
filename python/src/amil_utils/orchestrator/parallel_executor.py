"""Tier-parallel module generation using ThreadPoolExecutor.

Modules within the same dependency tier have no cross-dependencies
and can be generated concurrently. Docker mount remains sequential
(Odoo can't install multiple modules concurrently).
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)


def _execute_tier_parallel(
    *,
    cwd: Path,
    tier_modules: list[str],
    fn: Callable[[Path, str], dict[str, Any]],
    max_concurrency: int = 3,
    label: str = "Tier-parallel",
) -> list[dict[str, Any]]:
    """Execute fn for all modules in a tier concurrently.

    Each call invokes ``fn(cwd, module_name)`` which should return a dict
    with at least ``{"success": bool}``.

    Results are returned in the original submission order.

    Parameters
    ----------
    cwd:
        Project working directory.
    tier_modules:
        Module names in this tier (no cross-dependencies).
    fn:
        Callable that processes a single module.
    max_concurrency:
        Maximum simultaneous executions (default 3).
    label:
        Log prefix for info/error messages.
    """
    if not tier_modules:
        return []

    results: dict[str, dict[str, Any]] = {}

    with ThreadPoolExecutor(max_workers=max_concurrency) as pool:
        future_to_name = {
            pool.submit(fn, cwd, name): name
            for name in tier_modules
        }
        for future in as_completed(future_to_name):
            name = future_to_name[future]
            try:
                result = future.result()
                results[name] = result
                status = "OK" if result.get("success") else "FAIL"
                logger.info("%s %s: %s", label, name, status)
            except Exception as exc:
                logger.error("%s %s crashed: %s", label, name, exc)
                results[name] = {"success": False, "error": str(exc)}

    # Return in original submission order
    return [results[name] for name in tier_modules]


def generate_tier_parallel(
    *,
    cwd: Path,
    tier_modules: list[str],
    generate_fn: Callable[[Path, str], dict[str, Any]],
    max_concurrency: int = 3,
) -> list[dict[str, Any]]:
    """Generate all modules in a tier concurrently.

    Each generation calls ``generate_fn(cwd, module_name)`` which should
    return a dict with at least ``{"success": bool}``.

    Results are returned in the original submission order.
    """
    return _execute_tier_parallel(
        cwd=cwd,
        tier_modules=tier_modules,
        fn=generate_fn,
        max_concurrency=max_concurrency,
        label="Tier-parallel",
    )


def validate_tier_parallel(
    *,
    cwd: Path,
    tier_modules: list[str],
    validate_fn: Callable[[Path, str], dict[str, Any]],
    max_concurrency: int = 3,
) -> list[dict[str, Any]]:
    """Validate all modules in a tier concurrently.

    Each validation calls ``validate_fn(cwd, module_name)`` which should
    return a dict with at least ``{"success": bool}``.

    Results are returned in the original submission order.
    """
    return _execute_tier_parallel(
        cwd=cwd,
        tier_modules=tier_modules,
        fn=validate_fn,
        max_concurrency=max_concurrency,
        label="Validate-parallel",
    )
