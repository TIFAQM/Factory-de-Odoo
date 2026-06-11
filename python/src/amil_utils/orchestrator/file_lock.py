"""Portable lock-file serialization for shared .planning/ JSON state.

Guards the read-modify-write cycle on model_registry.json and
module_status.json so concurrent CLI invocations cannot lose updates.
Atomic temp+rename writes (registry._atomic_write_json) remain the
crash-safety layer; this adds mutual exclusion.
"""
from __future__ import annotations

import contextlib
import os
import time
from collections.abc import Iterator
from pathlib import Path


class LockTimeout(OSError):
    """Raised when the lock cannot be acquired within the timeout."""


@contextlib.contextmanager
def state_lock(
    target: str | Path,
    timeout: float = 10.0,
    poll: float = 0.02,
    stale_after: float = 30.0,
) -> Iterator[None]:
    """Acquire `<target>.lock` exclusively; break locks older than stale_after."""
    lock_path = Path(f"{target}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    while True:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            break
        except FileExistsError:
            with contextlib.suppress(OSError):
                if time.time() - lock_path.stat().st_mtime > stale_after:
                    lock_path.unlink()
                    continue
            if time.monotonic() >= deadline:
                raise LockTimeout(f"could not acquire {lock_path}") from None
            time.sleep(poll)
    try:
        yield
    finally:
        with contextlib.suppress(OSError):
            lock_path.unlink()
