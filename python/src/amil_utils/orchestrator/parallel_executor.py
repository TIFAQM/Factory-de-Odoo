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

    Parameters
    ----------
    cwd:
        Project working directory.
    tier_modules:
        Module names in this tier (no cross-dependencies).
    generate_fn:
        Callable that generates a single module.
    max_concurrency:
        Maximum simultaneous generations (default 3).
    """
    if not tier_modules:
        return []

    results: dict[str, dict[str, Any]] = {}

    with ThreadPoolExecutor(max_workers=max_concurrency) as pool:
        future_to_name = {
            pool.submit(generate_fn, cwd, name): name
            for name in tier_modules
        }
        for future in as_completed(future_to_name):
            name = future_to_name[future]
            try:
                result = future.result()
                results[name] = result
                status = "OK" if result.get("success") else "FAIL"
                logger.info("Tier-parallel %s: %s", name, status)
            except Exception as exc:
                logger.error("Tier-parallel %s crashed: %s", name, exc)
                results[name] = {"success": False, "error": str(exc)}

    # Return in original submission order
    return [results[name] for name in tier_modules]
