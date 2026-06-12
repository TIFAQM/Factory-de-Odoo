"""Tests for the file_lock context manager."""
from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from amil_utils.orchestrator.file_lock import LockTimeout, state_lock


def test_lock_creates_and_removes_lockfile(tmp_path: Path) -> None:
    target = tmp_path / "registry.json"
    with state_lock(target):
        assert (tmp_path / "registry.json.lock").exists()
    assert not (tmp_path / "registry.json.lock").exists()


def test_lock_blocks_second_acquirer(tmp_path: Path) -> None:
    target = tmp_path / "registry.json"
    order: list[str] = []

    def worker(tag: str) -> None:
        with state_lock(target, timeout=5.0):
            order.append(f"{tag}-in")
            time.sleep(0.1)
            order.append(f"{tag}-out")

    t1 = threading.Thread(target=worker, args=("a",))
    t2 = threading.Thread(target=worker, args=("b",))
    t1.start(); t2.start(); t1.join(); t2.join()
    assert order in (["a-in", "a-out", "b-in", "b-out"],
                     ["b-in", "b-out", "a-in", "a-out"])


def test_lock_timeout_raises(tmp_path: Path) -> None:
    target = tmp_path / "registry.json"
    lock = tmp_path / "registry.json.lock"
    lock.write_text("held")
    with pytest.raises(LockTimeout):
        with state_lock(target, timeout=0.2, stale_after=999.0):
            pass


def test_stale_lock_is_broken(tmp_path: Path) -> None:
    import os
    target = tmp_path / "registry.json"
    lock = tmp_path / "registry.json.lock"
    lock.write_text("stale")
    old = time.time() - 120
    os.utime(lock, (old, old))
    with state_lock(target, timeout=1.0, stale_after=30.0):
        pass  # should succeed by breaking the stale lock


def test_default_timeout_survives_orphaned_lock(tmp_path: Path) -> None:
    """Fix 1: default timeout (40s) > stale_after (30s), so a 120s-old orphaned
    lock must be broken automatically with no explicit kwargs."""
    import os
    target = tmp_path / "registry.json"
    lock = tmp_path / "registry.json.lock"
    lock.write_text("orphaned-by-sigkill")
    old = time.time() - 120  # 120s old — well past the 30s stale_after default
    os.utime(lock, (old, old))
    with state_lock(target):  # no kwargs — uses defaults timeout=40.0, stale_after=30.0
        pass  # must succeed without LockTimeout
