"""Tests for tier-parallel module generation and validation."""
from __future__ import annotations

import threading
import time
from pathlib import Path

from amil_utils.orchestrator.parallel_executor import (
    generate_tier_parallel,
    validate_tier_parallel,
)


class TestGenerateTierParallel:
    def test_generates_all_modules(self, tmp_path: Path) -> None:
        """All modules in a tier should be generated."""
        results: list[str] = []

        def mock_gen(cwd: Path, name: str) -> dict:
            results.append(name)
            return {"success": True, "module": name}

        output = generate_tier_parallel(
            cwd=tmp_path,
            tier_modules=["mod_a", "mod_b", "mod_c"],
            generate_fn=mock_gen,
            max_concurrency=3,
        )
        assert len(output) == 3
        assert all(r["success"] for r in output)
        assert set(results) == {"mod_a", "mod_b", "mod_c"}

    def test_respects_max_concurrency(self, tmp_path: Path) -> None:
        """Should not exceed max_concurrency simultaneous generations."""
        active: dict[str, int] = {"count": 0, "max_seen": 0}
        lock = threading.Lock()

        def slow_gen(cwd: Path, name: str) -> dict:
            with lock:
                active["count"] += 1
                active["max_seen"] = max(active["max_seen"], active["count"])
            time.sleep(0.05)
            with lock:
                active["count"] -= 1
            return {"success": True}

        generate_tier_parallel(
            cwd=tmp_path,
            tier_modules=["a", "b", "c", "d", "e"],
            generate_fn=slow_gen,
            max_concurrency=2,
        )
        assert active["max_seen"] <= 2

    def test_one_failure_others_succeed(self, tmp_path: Path) -> None:
        """If one module fails, others should still complete."""

        def mixed_gen(cwd: Path, name: str) -> dict:
            if name == "bad":
                return {"success": False, "error": "generation failed"}
            return {"success": True}

        output = generate_tier_parallel(
            cwd=tmp_path,
            tier_modules=["good1", "bad", "good2"],
            generate_fn=mixed_gen,
            max_concurrency=3,
        )
        successes = [r for r in output if r["success"]]
        failures = [r for r in output if not r["success"]]
        assert len(successes) == 2
        assert len(failures) == 1

    def test_preserves_submission_order(self, tmp_path: Path) -> None:
        """Results should be in the same order as input modules."""

        def gen(cwd: Path, name: str) -> dict:
            return {"success": True, "module": name}

        output = generate_tier_parallel(
            cwd=tmp_path,
            tier_modules=["z_last", "a_first", "m_middle"],
            generate_fn=gen,
            max_concurrency=3,
        )
        assert [r["module"] for r in output] == ["z_last", "a_first", "m_middle"]

    def test_empty_tier(self, tmp_path: Path) -> None:
        """Empty tier list should return empty results."""
        output = generate_tier_parallel(
            cwd=tmp_path,
            tier_modules=[],
            generate_fn=lambda c, n: {"success": True},
            max_concurrency=3,
        )
        assert output == []

    def test_exception_in_generate_fn(self, tmp_path: Path) -> None:
        """Exception in generate_fn should be caught, not crash executor."""

        def crashing_gen(cwd: Path, name: str) -> dict:
            if name == "crash":
                raise RuntimeError("boom")
            return {"success": True}

        output = generate_tier_parallel(
            cwd=tmp_path,
            tier_modules=["ok", "crash", "also_ok"],
            generate_fn=crashing_gen,
            max_concurrency=3,
        )
        assert len(output) == 3
        crash_result = output[1]
        assert crash_result["success"] is False
        assert "boom" in crash_result.get("error", "")


class TestValidateTierParallel:
    def test_validate_tier_parallel_all_modules(self, tmp_path: Path) -> None:
        """All modules in a tier should be validated."""
        validated: list[str] = []

        def mock_validate(cwd: Path, name: str) -> dict:
            validated.append(name)
            return {"success": True, "module": name}

        output = validate_tier_parallel(
            cwd=tmp_path,
            tier_modules=["mod_a", "mod_b", "mod_c"],
            validate_fn=mock_validate,
            max_concurrency=3,
        )
        assert len(output) == 3
        assert all(r["success"] for r in output)
        assert set(validated) == {"mod_a", "mod_b", "mod_c"}
        # Verify submission order preserved
        assert [r["module"] for r in output] == ["mod_a", "mod_b", "mod_c"]

    def test_validate_tier_parallel_respects_concurrency(self, tmp_path: Path) -> None:
        """Should not exceed max_concurrency simultaneous validations."""
        active: dict[str, int] = {"count": 0, "max_seen": 0}
        lock = threading.Lock()

        def slow_validate(cwd: Path, name: str) -> dict:
            with lock:
                active["count"] += 1
                active["max_seen"] = max(active["max_seen"], active["count"])
            time.sleep(0.05)
            with lock:
                active["count"] -= 1
            return {"success": True}

        validate_tier_parallel(
            cwd=tmp_path,
            tier_modules=["a", "b", "c", "d", "e"],
            validate_fn=slow_validate,
            max_concurrency=2,
        )
        assert active["max_seen"] <= 2

    def test_validate_tier_parallel_one_failure_others_succeed(self, tmp_path: Path) -> None:
        """If one module fails, others should still complete."""

        def mixed_validate(cwd: Path, name: str) -> dict:
            if name == "bad":
                return {"success": False, "error": "validation failed"}
            return {"success": True}

        output = validate_tier_parallel(
            cwd=tmp_path,
            tier_modules=["good1", "bad", "good2"],
            validate_fn=mixed_validate,
            max_concurrency=3,
        )
        successes = [r for r in output if r["success"]]
        failures = [r for r in output if not r["success"]]
        assert len(successes) == 2
        assert len(failures) == 1
        # Verify order: bad is at index 1
        assert output[1]["success"] is False

    def test_validate_tier_parallel_empty_tier(self, tmp_path: Path) -> None:
        """Empty tier list should return empty results."""
        output = validate_tier_parallel(
            cwd=tmp_path,
            tier_modules=[],
            validate_fn=lambda c, n: {"success": True},
            max_concurrency=3,
        )
        assert output == []
