"""Tests for orchestrator phase module."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from unittest.mock import patch

from amil_utils.orchestrator.phase import (
    _REMOVAL_MANIFEST_NAME,
    phase_add,
    phase_complete,
    phase_find,
    phase_insert,
    phase_next_decimal,
    phase_plan_index,
    phase_remove,
    phase_repair,
    phases_list,
)


def _make_project(tmp_path: Path, *, num_phases: int = 3) -> Path:
    """Create a .planning directory with phases, ROADMAP.md, and STATE.md."""
    planning = tmp_path / ".planning"
    planning.mkdir()
    phases = planning / "phases"
    phases.mkdir()

    phase_data = [
        ("01-setup", "Setup"),
        ("02-core", "Core module"),
        ("03-advanced", "Advanced features"),
    ]

    roadmap_lines = ["# Roadmap\n"]
    for i, (dir_name, desc) in enumerate(phase_data[:num_phases], 1):
        phase_dir = phases / dir_name
        phase_dir.mkdir()
        (phase_dir / ".gitkeep").write_text("")

        plan_content = (
            f"---\nphase: {i}\nplan: 01\ntype: implementation\n"
            f"wave: 1\ndepends_on: []\nfiles_modified: [src/main.py]\n"
            f"autonomous: true\nmust_haves:\n---\n\n"
            f"# Plan {i}-01\n\n<objective>\nBuild {desc.lower()}\n</objective>\n\n"
            f"<task>\n## Task 1\nDo something\n</task>\n\n"
            f"<task>\n## Task 2\nDo something else\n</task>\n"
        )
        (phase_dir / f"{str(i).zfill(2)}-01-PLAN.md").write_text(plan_content)

        roadmap_lines.append(f"### Phase {i}: {desc}\n")
        roadmap_lines.append(f"**Goal:** Build {desc.lower()}\n")
        roadmap_lines.append(f"**Requirements**: REQ-{str(i).zfill(2)}\n")
        if i > 1:
            roadmap_lines.append(f"**Depends on:** Phase {i - 1}\n")
        roadmap_lines.append(f"**Plans:** 1 plans\n\n")

    (planning / "ROADMAP.md").write_text("\n".join(roadmap_lines))

    state_content = (
        "# Session State\n\n## Position\n\n"
        "**Milestone:** v1.0\n"
        "**Current Phase:** 1\n"
        "**Current Phase Name:** Setup\n"
        "**Status:** Executing\n"
        "**Current Plan:** 1\n"
        "**Total Plans in Phase:** 1\n"
        "**Total Phases:** 3\n"
        "**Progress:** 0%\n"
        "**Last Activity:** 2026-03-13\n"
        "**Last Activity Description:** Working on phase 1\n"
    )
    (planning / "STATE.md").write_text(state_content)

    return planning


class TestPhasesList:
    def test_empty_no_phases_dir(self, tmp_path: Path) -> None:
        planning = tmp_path / ".planning"
        planning.mkdir()
        result = phases_list(tmp_path)
        assert result["count"] == 0
        assert result["directories"] == []

    def test_lists_directories(self, tmp_path: Path) -> None:
        _make_project(tmp_path)
        result = phases_list(tmp_path)
        assert result["count"] == 3
        assert "directories" in result
        assert result["directories"][0].startswith("01")

    def test_filter_by_type_plans(self, tmp_path: Path) -> None:
        _make_project(tmp_path)
        result = phases_list(tmp_path, file_type="plans")
        assert result["count"] >= 1
        assert "files" in result
        assert all(f.endswith("-PLAN.md") for f in result["files"])

    def test_filter_by_phase(self, tmp_path: Path) -> None:
        _make_project(tmp_path)
        result = phases_list(tmp_path, phase="1")
        assert result["count"] == 1
        assert "directories" in result

    def test_phase_not_found(self, tmp_path: Path) -> None:
        _make_project(tmp_path)
        result = phases_list(tmp_path, phase="99")
        assert result["count"] == 0
        assert "error" in result


class TestPhaseNextDecimal:
    def test_no_phases_dir(self, tmp_path: Path) -> None:
        planning = tmp_path / ".planning"
        planning.mkdir()
        result = phase_next_decimal(tmp_path, "06")
        assert result["next"] == "06.1"
        assert result["found"] is False

    def test_no_existing_decimals(self, tmp_path: Path) -> None:
        _make_project(tmp_path)
        result = phase_next_decimal(tmp_path, "01")
        assert result["next"] == "01.1"
        assert result["found"] is True

    def test_existing_decimals(self, tmp_path: Path) -> None:
        planning = _make_project(tmp_path)
        phases = planning / "phases"
        (phases / "01.1-hotfix").mkdir()
        result = phase_next_decimal(tmp_path, "01")
        assert result["next"] == "01.2"
        assert result["existing"] == ["01.1"]


class TestPhaseFind:
    def test_found(self, tmp_path: Path) -> None:
        _make_project(tmp_path)
        result = phase_find(tmp_path, "1")
        assert result["found"] is True
        assert "01" in result["phase_number"]

    def test_not_found(self, tmp_path: Path) -> None:
        _make_project(tmp_path)
        result = phase_find(tmp_path, "99")
        assert result["found"] is False

    def test_no_phase_arg(self) -> None:
        result = phase_find("/tmp", "")
        assert result["found"] is False


class TestPhasePlanIndex:
    def test_builds_plan_index(self, tmp_path: Path) -> None:
        _make_project(tmp_path)
        result = phase_plan_index(tmp_path, "1")
        assert len(result["plans"]) >= 1
        assert result["plans"][0]["wave"] == 1
        assert result["plans"][0]["task_count"] == 2

    def test_detects_completed_plans(self, tmp_path: Path) -> None:
        planning = _make_project(tmp_path)
        (planning / "phases" / "01-setup" / "01-01-SUMMARY.md").write_text(
            "---\nphase: 1\nplan: 01\n---\n# Summary\nDone."
        )
        result = phase_plan_index(tmp_path, "1")
        assert len(result["incomplete"]) == 0

    def test_detects_checkpoints(self, tmp_path: Path) -> None:
        planning = _make_project(tmp_path)
        plan = (
            "---\nphase: 1\nplan: 02\nwave: 2\nautonomous: false\n"
            "depends_on: []\nfiles_modified: []\nmust_haves:\n---\n\n"
            "<task>\n## Task 1\nManual review\n</task>\n"
        )
        (planning / "phases" / "01-setup" / "01-02-PLAN.md").write_text(plan)
        result = phase_plan_index(tmp_path, "1")
        assert result["has_checkpoints"] is True

    def test_phase_not_found(self, tmp_path: Path) -> None:
        _make_project(tmp_path)
        result = phase_plan_index(tmp_path, "99")
        assert "error" in result

    def test_extracts_objective(self, tmp_path: Path) -> None:
        _make_project(tmp_path)
        result = phase_plan_index(tmp_path, "1")
        assert result["plans"][0]["objective"] == "Build setup"


class TestPhaseAdd:
    def test_adds_phase(self, tmp_path: Path) -> None:
        _make_project(tmp_path)
        result = phase_add(tmp_path, "Testing phase")
        assert result["phase_number"] == 4
        assert result["slug"] == "testing-phase"
        phase_dir = tmp_path / ".planning" / "phases" / "04-testing-phase"
        assert phase_dir.exists()

    def test_updates_roadmap(self, tmp_path: Path) -> None:
        _make_project(tmp_path)
        phase_add(tmp_path, "Testing phase")
        roadmap = (tmp_path / ".planning" / "ROADMAP.md").read_text()
        assert "Phase 4:" in roadmap

    def test_raises_without_roadmap(self, tmp_path: Path) -> None:
        planning = tmp_path / ".planning"
        planning.mkdir()
        with pytest.raises(ValueError, match="ROADMAP"):
            phase_add(tmp_path, "Test")


class TestPhaseInsert:
    def test_inserts_decimal(self, tmp_path: Path) -> None:
        _make_project(tmp_path)
        result = phase_insert(tmp_path, "2", "Urgent fix")
        assert result["phase_number"] == "02.1"
        assert result["after_phase"] == "2"
        phase_dir = tmp_path / ".planning" / "phases" / "02.1-urgent-fix"
        assert phase_dir.exists()

    def test_updates_roadmap(self, tmp_path: Path) -> None:
        _make_project(tmp_path)
        phase_insert(tmp_path, "2", "Urgent fix")
        roadmap = (tmp_path / ".planning" / "ROADMAP.md").read_text()
        assert "Phase 02.1:" in roadmap

    def test_increments_existing_decimals(self, tmp_path: Path) -> None:
        planning = _make_project(tmp_path)
        (planning / "phases" / "02.1-first").mkdir()
        result = phase_insert(tmp_path, "2", "Second fix")
        assert result["phase_number"] == "02.2"


class TestPhaseRemove:
    def test_removes_phase_no_summaries(self, tmp_path: Path) -> None:
        _make_project(tmp_path)
        result = phase_remove(tmp_path, "2")
        assert result["removed"] == "2"
        assert result["roadmap_updated"] is True
        assert not (tmp_path / ".planning" / "phases" / "02-core").exists()

    def test_blocks_with_summaries(self, tmp_path: Path) -> None:
        planning = _make_project(tmp_path)
        (planning / "phases" / "02-core" / "02-01-SUMMARY.md").write_text("Done")
        with pytest.raises(ValueError, match="executed"):
            phase_remove(tmp_path, "2")

    def test_force_removes_with_summaries(self, tmp_path: Path) -> None:
        planning = _make_project(tmp_path)
        (planning / "phases" / "02-core" / "02-01-SUMMARY.md").write_text("Done")
        result = phase_remove(tmp_path, "2", force=True)
        assert result["removed"] == "2"

    def test_renumbers_subsequent_integer(self, tmp_path: Path) -> None:
        _make_project(tmp_path)
        phase_remove(tmp_path, "2")
        phases_dir = tmp_path / ".planning" / "phases"
        dirs = sorted(d.name for d in phases_dir.iterdir() if d.is_dir())
        assert any(d.startswith("02-advanced") for d in dirs)

    def test_removes_decimal_phase(self, tmp_path: Path) -> None:
        planning = _make_project(tmp_path)
        phases = planning / "phases"
        (phases / "02.1-hotfix").mkdir()
        (phases / "02.2-patch").mkdir()
        phase_remove(tmp_path, "02.1")
        dirs = sorted(d.name for d in phases.iterdir() if d.is_dir())
        assert any(d.startswith("02.1-patch") for d in dirs)
        assert not any(d.startswith("02.2") for d in dirs)

    def test_updates_state_total(self, tmp_path: Path) -> None:
        _make_project(tmp_path)
        phase_remove(tmp_path, "2")
        state = (tmp_path / ".planning" / "STATE.md").read_text()
        assert "**Total Phases:** 2" in state


class TestPhaseComplete:
    def test_completes_phase(self, tmp_path: Path) -> None:
        planning = _make_project(tmp_path)
        (planning / "phases" / "01-setup" / "01-01-SUMMARY.md").write_text("Done")
        result = phase_complete(tmp_path, "1")
        assert result["completed_phase"] == "1"
        assert result["roadmap_updated"] is True

    def test_finds_next_phase(self, tmp_path: Path) -> None:
        planning = _make_project(tmp_path)
        (planning / "phases" / "01-setup" / "01-01-SUMMARY.md").write_text("Done")
        result = phase_complete(tmp_path, "1")
        assert result["next_phase"] is not None
        assert result["is_last_phase"] is False

    def test_last_phase(self, tmp_path: Path) -> None:
        planning = _make_project(tmp_path)
        (planning / "phases" / "03-advanced" / "03-01-SUMMARY.md").write_text("Done")
        result = phase_complete(tmp_path, "3")
        assert result["is_last_phase"] is True

    def test_updates_state(self, tmp_path: Path) -> None:
        planning = _make_project(tmp_path)
        (planning / "phases" / "01-setup" / "01-01-SUMMARY.md").write_text("Done")
        phase_complete(tmp_path, "1")
        state = (planning / "STATE.md").read_text()
        assert "Ready to plan" in state

    def test_raises_for_missing_phase(self, tmp_path: Path) -> None:
        _make_project(tmp_path)
        with pytest.raises(ValueError, match="not found"):
            phase_complete(tmp_path, "99")


class TestPhaseRemoveAtomicity:
    """Tests for transaction manifest and atomic phase removal."""

    def test_successful_removal_cleans_up_manifest(self, tmp_path: Path) -> None:
        """After a successful removal, no manifest file should remain."""
        _make_project(tmp_path)
        phase_remove(tmp_path, "2")
        manifest_path = tmp_path / ".planning" / "phases" / _REMOVAL_MANIFEST_NAME
        assert not manifest_path.exists()

    def test_normal_removal_still_works(self, tmp_path: Path) -> None:
        """Existing behavior is preserved with the manifest wrapper."""
        _make_project(tmp_path)
        result = phase_remove(tmp_path, "2")
        assert result["removed"] == "2"
        assert result["directory_deleted"] == "02-core"
        assert result["roadmap_updated"] is True

        phases_dir = tmp_path / ".planning" / "phases"
        dirs = sorted(d.name for d in phases_dir.iterdir() if d.is_dir())
        assert any(d.startswith("02-advanced") for d in dirs)
        assert not any(d.startswith("03-") for d in dirs)

    def test_failure_at_rename_rolls_back(self, tmp_path: Path) -> None:
        """If rename fails on the second dir rename, the first is reversed."""
        planning = _make_project(tmp_path, num_phases=3)
        phases_dir = planning / "phases"

        # Add a 4th phase so we have two subsequent dirs to rename (03, 04)
        (phases_dir / "04-extra").mkdir()
        (phases_dir / "04-extra" / ".gitkeep").write_text("")
        roadmap_path = planning / "ROADMAP.md"
        roadmap = roadmap_path.read_text()
        roadmap += "\n### Phase 4: Extra\n**Goal:** Extra\n**Plans:** 0 plans\n"
        roadmap_path.write_text(roadmap)

        rename_call_count = 0
        original_rename = Path.rename

        def failing_rename(self_path: Path, target: Path) -> Path:
            nonlocal rename_call_count
            rename_call_count += 1
            # Fail on second dir rename call
            if rename_call_count == 2:
                raise OSError("Simulated rename failure")
            return original_rename(self_path, target)

        with patch.object(Path, "rename", failing_rename):
            with pytest.raises(OSError, match="Simulated rename failure"):
                phase_remove(tmp_path, "2")

        # Manifest should be cleaned up after rollback
        manifest_path = phases_dir / _REMOVAL_MANIFEST_NAME
        assert not manifest_path.exists()

        # The first rename should have been reversed
        dirs = sorted(d.name for d in phases_dir.iterdir() if d.is_dir())
        # Phase 2 directory was deleted (that happened before renames)
        # but the remaining dirs should be in their original positions
        # because the first rename was rolled back
        dir_names = {d for d in dirs}
        # 03-advanced should be back (reversed from 02-advanced)
        assert "03-advanced" in dir_names, (
            f"Rollback should restore original name. Found: {dir_names}"
        )

    def test_phase_repair_with_orphaned_manifest(self, tmp_path: Path) -> None:
        """phase_repair reverses completed renames from an orphaned manifest."""
        planning = _make_project(tmp_path)
        phases_dir = planning / "phases"

        # Simulate a partial removal: manually rename 03-advanced -> 02-advanced
        # and leave an orphaned manifest
        (phases_dir / "03-advanced").rename(phases_dir / "02-advanced-renamed")
        manifest = {
            "target_phase": "2",
            "normalized": "02",
            "is_decimal": False,
            "operations": [
                {"type": "delete_dir", "target": "02-core", "status": "done"},
                {
                    "type": "rename_dir",
                    "from": "03-advanced",
                    "to": "02-advanced-renamed",
                    "status": "done",
                },
                {"type": "roadmap_update", "status": "pending"},
            ],
        }
        manifest_path = phases_dir / _REMOVAL_MANIFEST_NAME
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        result = phase_repair(tmp_path)

        assert result["repaired"] is True
        assert not manifest_path.exists()

        # The rename should have been reversed
        dirs = sorted(d.name for d in phases_dir.iterdir() if d.is_dir())
        assert "03-advanced" in dirs
        assert "02-advanced-renamed" not in dirs

    def test_phase_repair_no_manifest_is_noop(self, tmp_path: Path) -> None:
        """phase_repair with no manifest does nothing."""
        _make_project(tmp_path)
        result = phase_repair(tmp_path)
        assert result["repaired"] is False
        assert "No orphaned manifest" in result["details"]
