"""Tests for orchestrator module_status module."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from amil_utils.orchestrator.module_status import (
    VALID_TRANSITIONS,
    get_generation_queue,
    module_status_get,
    module_status_init,
    module_status_read,
    module_status_transition,
    read_status_file,
    tier_status,
)


class TestValidTransitions:
    def test_planned_to_spec_approved(self) -> None:
        assert "spec_approved" in VALID_TRANSITIONS["planned"]

    def test_shipped_is_terminal(self) -> None:
        assert VALID_TRANSITIONS["shipped"] == []


class TestReadStatusFile:
    def test_returns_empty_when_missing(self, tmp_path: Path) -> None:
        data = read_status_file(tmp_path)
        assert data["modules"] == {}

    def test_reads_existing(self, tmp_path: Path) -> None:
        planning = tmp_path / ".planning"
        planning.mkdir()
        (planning / "module_status.json").write_text(json.dumps({
            "_meta": {"version": 1},
            "modules": {"hr_payroll": {"status": "planned"}},
            "tiers": {},
        }))
        data = read_status_file(tmp_path)
        assert "hr_payroll" in data["modules"]


class TestModuleStatusRead:
    def test_returns_full_data(self, tmp_path: Path) -> None:
        data = module_status_read(tmp_path)
        assert "modules" in data
        assert "_meta" in data


class TestModuleStatusGet:
    def test_returns_planned_default(self, tmp_path: Path) -> None:
        result = module_status_get(tmp_path, "new_module")
        assert result["status"] == "planned"
        assert result["name"] == "new_module"

    def test_returns_existing_status(self, tmp_path: Path) -> None:
        planning = tmp_path / ".planning"
        planning.mkdir()
        (planning / "module_status.json").write_text(json.dumps({
            "_meta": {"version": 1},
            "modules": {"hr_payroll": {"status": "generated", "tier": "core"}},
            "tiers": {},
        }))
        result = module_status_get(tmp_path, "hr_payroll")
        assert result["status"] == "generated"


class TestModuleStatusInit:
    def test_creates_module_entry(self, tmp_path: Path) -> None:
        (tmp_path / ".planning").mkdir()
        result = module_status_init(tmp_path, "hr_payroll", "core", ["base"])
        assert result["modules"]["hr_payroll"]["status"] == "planned"
        assert result["modules"]["hr_payroll"]["tier"] == "core"

    def test_creates_artifact_dir(self, tmp_path: Path) -> None:
        (tmp_path / ".planning").mkdir()
        module_status_init(tmp_path, "hr_payroll", "core")
        assert (tmp_path / ".planning" / "modules" / "hr_payroll" / "CONTEXT.md").exists()

    def test_duplicate_raises(self, tmp_path: Path) -> None:
        (tmp_path / ".planning").mkdir()
        module_status_init(tmp_path, "hr_payroll", "core")
        with pytest.raises(ValueError, match="already exists"):
            module_status_init(tmp_path, "hr_payroll", "core")


class TestModuleStatusTransition:
    def test_valid_transition(self, tmp_path: Path) -> None:
        (tmp_path / ".planning").mkdir()
        module_status_init(tmp_path, "hr_payroll", "core")
        result = module_status_transition(tmp_path, "hr_payroll", "spec_approved")
        assert result["modules"]["hr_payroll"]["status"] == "spec_approved"

    def test_invalid_transition_raises(self, tmp_path: Path) -> None:
        (tmp_path / ".planning").mkdir()
        module_status_init(tmp_path, "hr_payroll", "core")
        with pytest.raises(ValueError, match="Invalid transition"):
            module_status_transition(tmp_path, "hr_payroll", "shipped")

    def test_transition_chain(self, tmp_path: Path) -> None:
        (tmp_path / ".planning").mkdir()
        module_status_init(tmp_path, "mod_a", "core")
        module_status_transition(tmp_path, "mod_a", "spec_approved")
        module_status_transition(tmp_path, "mod_a", "generated")
        module_status_transition(tmp_path, "mod_a", "checked")
        result = module_status_transition(tmp_path, "mod_a", "shipped")
        assert result["modules"]["mod_a"]["status"] == "shipped"


class TestTierStatus:
    def test_groups_by_tier(self, tmp_path: Path) -> None:
        (tmp_path / ".planning").mkdir()
        module_status_init(tmp_path, "mod_a", "core")
        module_status_init(tmp_path, "mod_b", "hr")
        result = tier_status(tmp_path)
        assert "core" in result["tiers"]
        assert "hr" in result["tiers"]

    def test_complete_tier(self, tmp_path: Path) -> None:
        (tmp_path / ".planning").mkdir()
        module_status_init(tmp_path, "mod_a", "core")
        module_status_transition(tmp_path, "mod_a", "spec_approved")
        module_status_transition(tmp_path, "mod_a", "generated")
        module_status_transition(tmp_path, "mod_a", "checked")
        module_status_transition(tmp_path, "mod_a", "shipped")
        result = tier_status(tmp_path)
        assert result["tiers"]["core"]["status"] == "complete"


class TestGetGenerationQueue:
    """Tests for get_generation_queue() batch functionality."""

    def _setup_status(self, tmp_path: Path, modules: dict[str, dict]) -> None:
        """Create module_status.json with given module data."""
        status_dir = tmp_path / ".planning"
        status_dir.mkdir(parents=True, exist_ok=True)
        (status_dir / "module_status.json").write_text(json.dumps({
            "_meta": {"version": 1, "last_updated": None},
            "modules": modules,
            "tiers": {},
        }))

    def test_returns_spec_approved_modules_only(self, tmp_path: Path) -> None:
        self._setup_status(tmp_path, {
            "mod_a": {"status": "shipped", "tier": "core", "depends": []},
            "mod_b": {"status": "spec_approved", "tier": "core", "depends": []},
            "mod_c": {"status": "planned", "tier": "hr", "depends": []},
            "mod_d": {"status": "spec_approved", "tier": "hr", "depends": []},
        })
        queue = get_generation_queue(tmp_path)
        assert "mod_b" in queue
        assert "mod_d" in queue
        assert "mod_a" not in queue  # Already shipped
        assert "mod_c" not in queue  # Not yet approved

    def test_returns_empty_when_all_generated(self, tmp_path: Path) -> None:
        self._setup_status(tmp_path, {
            "mod_a": {"status": "generated", "tier": "core", "depends": []},
            "mod_b": {"status": "shipped", "tier": "hr", "depends": []},
        })
        queue = get_generation_queue(tmp_path)
        assert queue == []

    def test_returns_empty_when_no_modules(self, tmp_path: Path) -> None:
        self._setup_status(tmp_path, {})
        queue = get_generation_queue(tmp_path)
        assert queue == []

    def test_returns_empty_when_status_file_missing(self, tmp_path: Path) -> None:
        queue = get_generation_queue(tmp_path)
        assert queue == []

    def test_excludes_checked_modules(self, tmp_path: Path) -> None:
        self._setup_status(tmp_path, {
            "mod_a": {"status": "checked", "tier": "core", "depends": []},
            "mod_b": {"status": "spec_approved", "tier": "core", "depends": []},
        })
        queue = get_generation_queue(tmp_path)
        assert queue == ["mod_b"]

    def test_preserves_insertion_order(self, tmp_path: Path) -> None:
        self._setup_status(tmp_path, {
            "mod_z": {"status": "spec_approved", "tier": "core", "depends": []},
            "mod_a": {"status": "spec_approved", "tier": "core", "depends": []},
            "mod_m": {"status": "spec_approved", "tier": "core", "depends": []},
        })
        queue = get_generation_queue(tmp_path)
        assert queue == ["mod_z", "mod_a", "mod_m"]

    def test_works_with_real_init_and_transition(self, tmp_path: Path) -> None:
        """Integration: use actual init + transition to set up spec_approved."""
        (tmp_path / ".planning").mkdir()
        module_status_init(tmp_path, "hr_payroll", "core")
        module_status_transition(tmp_path, "hr_payroll", "spec_approved")
        module_status_init(tmp_path, "hr_leave", "hr")
        # hr_leave stays at "planned"
        queue = get_generation_queue(tmp_path)
        assert queue == ["hr_payroll"]
