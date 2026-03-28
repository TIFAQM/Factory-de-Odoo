"""Tests for orchestrator registry module."""
from __future__ import annotations

import json
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from amil_utils.orchestrator.registry import (
    EMPTY_REGISTRY,
    MODEL_NAME_PATTERN,
    RELATIONAL_TYPES,
    _atomic_write_json,
    read_model_from_registry,
    read_registry_file,
    remove_module_from_registry,
    rollback_registry,
    spec_to_manifest,
    stats_registry,
    tiered_registry_injection,
    update_from_spec,
    update_registry,
    validate_registry,
)


def _make_registry(tmp_path: Path, data: dict | None = None) -> Path:
    planning = tmp_path / ".planning"
    planning.mkdir(exist_ok=True)
    reg_path = planning / "model_registry.json"
    reg_data = data or {
        "_meta": {
            "version": 1,
            "last_updated": "2026-03-10T00:00:00Z",
            "modules_contributing": ["mod_a"],
            "odoo_version": "19.0",
        },
        "models": {
            "mod_a.model_one": {
                "name": "mod_a.model_one",
                "module": "mod_a",
                "description": "Test model",
                "fields": {
                    "name": {"name": "name", "type": "Char"},
                    "partner_id": {
                        "name": "partner_id",
                        "type": "Many2one",
                        "comodel_name": "res.partner",
                    },
                },
                "_inherit": [],
            },
        },
    }
    reg_path.write_text(json.dumps(reg_data, indent=2))
    return reg_path


class TestConstants:
    def test_empty_registry_structure(self) -> None:
        assert "_meta" in EMPTY_REGISTRY
        assert "models" in EMPTY_REGISTRY
        assert EMPTY_REGISTRY["_meta"]["version"] == 0

    def test_model_name_pattern(self) -> None:
        assert MODEL_NAME_PATTERN.match("res.partner")
        assert MODEL_NAME_PATTERN.match("account_move.line")
        assert not MODEL_NAME_PATTERN.match("Bad.Name")
        assert not MODEL_NAME_PATTERN.match("123.invalid")

    def test_relational_types(self) -> None:
        assert "Many2one" in RELATIONAL_TYPES
        assert "One2many" in RELATIONAL_TYPES
        assert "Many2many" in RELATIONAL_TYPES
        assert "Char" not in RELATIONAL_TYPES


class TestReadRegistryFile:
    def test_returns_empty_when_missing(self, tmp_path: Path) -> None:
        result = read_registry_file(tmp_path)
        assert result["_meta"]["version"] == 0
        assert result["models"] == {}

    def test_reads_existing(self, tmp_path: Path) -> None:
        _make_registry(tmp_path)
        result = read_registry_file(tmp_path)
        assert result["_meta"]["version"] == 1
        assert "mod_a.model_one" in result["models"]

    def test_recovers_from_bak(self, tmp_path: Path) -> None:
        planning = tmp_path / ".planning"
        planning.mkdir()
        # Write corrupted main file
        (planning / "model_registry.json").write_text("{invalid json")
        # Write valid backup
        bak_data = {
            "_meta": {"version": 5, "last_updated": None, "modules_contributing": [], "odoo_version": "19.0"},
            "models": {"backed.up": {"name": "backed.up", "module": "backup"}},
        }
        (planning / "model_registry.json.bak").write_text(json.dumps(bak_data))
        result = read_registry_file(tmp_path)
        assert result["_meta"]["version"] == 5
        assert "backed.up" in result["models"]


class TestReadModelFromRegistry:
    def test_returns_model(self, tmp_path: Path) -> None:
        _make_registry(tmp_path)
        result = read_model_from_registry(tmp_path, "mod_a.model_one")
        assert result is not None
        assert result["module"] == "mod_a"

    def test_returns_none_for_missing(self, tmp_path: Path) -> None:
        _make_registry(tmp_path)
        result = read_model_from_registry(tmp_path, "nonexistent.model")
        assert result is None


class TestUpdateRegistry:
    def test_merges_models(self, tmp_path: Path) -> None:
        _make_registry(tmp_path)
        manifest = {
            "module": "mod_b",
            "models": {
                "mod_b.new_model": {
                    "name": "mod_b.new_model",
                    "module": "mod_b",
                    "fields": {},
                },
            },
        }
        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text(json.dumps(manifest))
        result = update_registry(tmp_path, str(manifest_path))
        assert result["_meta"]["version"] == 2
        assert "mod_b.new_model" in result["models"]
        assert "mod_a.model_one" in result["models"]
        assert "mod_b" in result["_meta"]["modules_contributing"]

    def test_creates_backup(self, tmp_path: Path) -> None:
        _make_registry(tmp_path)
        manifest = {"module": "x", "models": {}}
        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text(json.dumps(manifest))
        update_registry(tmp_path, str(manifest_path))
        assert (tmp_path / ".planning" / "model_registry.json.bak").exists()


class TestRollbackRegistry:
    def test_restores_backup(self, tmp_path: Path) -> None:
        planning = tmp_path / ".planning"
        planning.mkdir()
        bak_data = {
            "_meta": {"version": 2, "last_updated": None, "modules_contributing": [], "odoo_version": "19.0"},
            "models": {},
        }
        (planning / "model_registry.json.bak").write_text(json.dumps(bak_data))
        (planning / "model_registry.json").write_text(json.dumps({"_meta": {"version": 3}, "models": {}}))
        result = rollback_registry(tmp_path)
        assert result is not None
        assert result["_meta"]["version"] == 2

    def test_returns_none_without_backup(self, tmp_path: Path) -> None:
        (tmp_path / ".planning").mkdir()
        result = rollback_registry(tmp_path)
        assert result is None


class TestValidateRegistry:
    def test_valid_registry(self, tmp_path: Path) -> None:
        data = {
            "_meta": {"version": 1, "last_updated": None, "modules_contributing": ["x"], "odoo_version": "19.0"},
            "models": {
                "x.model": {
                    "name": "x.model",
                    "module": "x",
                    "fields": {
                        "ref_id": {"name": "ref_id", "type": "Many2one", "comodel_name": "x.model"},
                        "line_ids": {"name": "line_ids", "type": "One2many", "comodel_name": "x.model", "inverse_name": "ref_id"},
                    },
                },
            },
        }
        _make_registry(tmp_path, data)
        result = validate_registry(tmp_path)
        assert result["valid"] is True
        assert result["model_count"] == 1

    def test_detects_missing_target(self, tmp_path: Path) -> None:
        data = {
            "_meta": {"version": 1, "last_updated": None, "modules_contributing": [], "odoo_version": "19.0"},
            "models": {
                "x.model": {
                    "name": "x.model",
                    "module": "x",
                    "fields": {
                        "ref_id": {"name": "ref_id", "type": "Many2one", "comodel_name": "nonexistent.model"},
                    },
                },
            },
        }
        _make_registry(tmp_path, data)
        result = validate_registry(tmp_path)
        assert result["valid"] is False
        assert any("nonexistent.model" in e for e in result["errors"])

    def test_detects_missing_inverse_name(self, tmp_path: Path) -> None:
        data = {
            "_meta": {"version": 1, "last_updated": None, "modules_contributing": [], "odoo_version": "19.0"},
            "models": {
                "x.model": {
                    "name": "x.model",
                    "module": "x",
                    "fields": {
                        "line_ids": {"name": "line_ids", "type": "One2many", "comodel_name": "x.model"},
                    },
                },
            },
        }
        _make_registry(tmp_path, data)
        result = validate_registry(tmp_path)
        assert result["valid"] is False
        assert any("inverse_name" in e for e in result["errors"])


class TestStatsRegistry:
    def test_computes_stats(self, tmp_path: Path) -> None:
        data = {
            "_meta": {"version": 3, "last_updated": None, "modules_contributing": ["a", "b"], "odoo_version": "19.0"},
            "models": {
                "a.model": {
                    "name": "a.model", "module": "a",
                    "fields": {
                        "name": {"name": "name", "type": "Char"},
                        "b_id": {"name": "b_id", "type": "Many2one", "comodel_name": "b.model"},
                    },
                },
                "b.model": {
                    "name": "b.model", "module": "b",
                    "fields": {"name": {"name": "name", "type": "Char"}},
                },
            },
        }
        _make_registry(tmp_path, data)
        result = stats_registry(tmp_path)
        assert result["model_count"] == 2
        assert result["field_count"] == 3
        assert result["cross_reference_count"] == 1
        assert result["version"] == 3


class TestSpecToManifest:
    def test_converts_spec(self) -> None:
        spec = {
            "module_name": "test_mod",
            "models": [
                {
                    "name": "test_mod.my_model",
                    "description": "A model",
                    "fields": [
                        {"name": "title", "type": "Char"},
                        {"name": "value", "type": "Float"},
                    ],
                },
            ],
        }
        result = spec_to_manifest(spec)
        assert result["module"] == "test_mod"
        assert "test_mod.my_model" in result["models"]
        assert "title" in result["models"]["test_mod.my_model"]["fields"]

    def test_handles_empty_spec(self) -> None:
        result = spec_to_manifest({})
        assert result["module"] == "unknown"
        assert result["models"] == {}


class TestUpdateFromSpec:
    def test_updates_from_spec(self, tmp_path: Path) -> None:
        _make_registry(tmp_path)
        spec = {
            "module_name": "new_mod",
            "models": [
                {"name": "new_mod.thing", "fields": [{"name": "x", "type": "Integer"}]},
            ],
        }
        result = update_from_spec(tmp_path, spec)
        assert "new_mod.thing" in result["models"]
        assert result["_meta"]["version"] == 2


class TestTieredRegistryInjection:
    def test_returns_tiered_view(self, tmp_path: Path) -> None:
        planning = tmp_path / ".planning"
        planning.mkdir()
        # Set up module_status.json
        (planning / "module_status.json").write_text(json.dumps({
            "_meta": {"version": 1},
            "modules": {
                "base_mod": {"status": "generated", "depends": []},
                "mid_mod": {"status": "generated", "depends": ["base_mod"]},
                "top_mod": {"status": "planned", "depends": ["mid_mod"]},
            },
            "tiers": {},
        }))
        # Set up model_registry.json
        (planning / "model_registry.json").write_text(json.dumps({
            "_meta": {"version": 1, "last_updated": None, "modules_contributing": ["base_mod", "mid_mod"], "odoo_version": "19.0"},
            "models": {
                "base_mod.core": {"name": "base_mod.core", "module": "base_mod", "fields": {"x": {"name": "x", "type": "Char", "help": "test"}}},
                "mid_mod.service": {"name": "mid_mod.service", "module": "mid_mod", "fields": {"y": {"name": "y", "type": "Integer", "help": "mid"}}},
            },
        }))
        result = tiered_registry_injection(tmp_path, "top_mod")
        # mid_mod is direct dep -> full fields
        assert "y" in result["models"]["mid_mod.service"]["fields"]
        assert result["models"]["mid_mod.service"]["fields"]["y"].get("type") == "Integer"
        # base_mod is transitive dep -> field-list only (no metadata)
        assert "x" in result["models"]["base_mod.core"]["fields"]
        assert result["models"]["base_mod.core"]["fields"]["x"].get("type") is None

    def test_unknown_module_returns_empty(self, tmp_path: Path) -> None:
        planning = tmp_path / ".planning"
        planning.mkdir()
        (planning / "module_status.json").write_text(json.dumps({
            "_meta": {"version": 1}, "modules": {}, "tiers": {},
        }))
        (planning / "model_registry.json").write_text(json.dumps({
            "_meta": {"version": 1, "last_updated": None, "modules_contributing": [], "odoo_version": "19.0"},
            "models": {},
        }))
        result = tiered_registry_injection(tmp_path, "nonexistent")
        assert result["models"] == {}


class TestAtomicWriteJsonRegistry:
    """Tests for _atomic_write_json race condition fix and error handling."""

    def test_concurrent_writes_no_corruption(self, tmp_path: Path) -> None:
        """Two threads writing to the same file simultaneously must not corrupt data."""
        planning = tmp_path / ".planning"
        planning.mkdir()
        target = planning / "model_registry.json"
        target.write_text(json.dumps({"seed": True}), encoding="utf-8")

        barrier = threading.Barrier(2, timeout=5)
        errors: list[Exception] = []

        def writer(value: int) -> None:
            try:
                barrier.wait()
                data = {"_meta": {"version": value}, "models": {}}
                _atomic_write_json(target, data)
            except Exception as exc:
                errors.append(exc)

        t1 = threading.Thread(target=writer, args=(1,))
        t2 = threading.Thread(target=writer, args=(2,))
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        assert not errors, f"Unexpected errors: {errors}"
        # File must be valid JSON — one of the two writers won
        raw = target.read_text(encoding="utf-8")
        result = json.loads(raw)
        assert result["_meta"]["version"] in (1, 2)

    def test_no_leftover_tmp_files_after_concurrent_writes(self, tmp_path: Path) -> None:
        """After concurrent writes, no .tmp files should remain."""
        planning = tmp_path / ".planning"
        planning.mkdir()
        target = planning / "model_registry.json"
        target.write_text(json.dumps({"seed": True}), encoding="utf-8")

        barrier = threading.Barrier(4, timeout=5)
        errors: list[Exception] = []

        def writer(value: int) -> None:
            try:
                barrier.wait()
                _atomic_write_json(target, {"_meta": {"version": value}, "models": {}})
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        tmp_files = list(planning.glob("*.tmp"))
        assert tmp_files == [], f"Leftover tmp files: {tmp_files}"

    def test_tmp_cleaned_up_on_rename_failure(self, tmp_path: Path) -> None:
        """When rename raises OSError, the temp file must be cleaned up."""
        planning = tmp_path / ".planning"
        planning.mkdir()
        target = planning / "model_registry.json"

        with patch("pathlib.Path.rename", side_effect=OSError("mock rename failure")):
            with pytest.raises(OSError, match="mock rename failure"):
                _atomic_write_json(target, {"_meta": {"version": 1}, "models": {}})

        tmp_files = list(planning.glob("*.tmp"))
        assert tmp_files == [], f"Temp file not cleaned up: {tmp_files}"

    def test_backup_created_on_write(self, tmp_path: Path) -> None:
        """Backup .bak file should still be created when target exists."""
        planning = tmp_path / ".planning"
        planning.mkdir()
        target = planning / "model_registry.json"
        original = {"_meta": {"version": 1}, "models": {}}
        target.write_text(json.dumps(original, indent=2), encoding="utf-8")

        updated = {"_meta": {"version": 2}, "models": {"new.model": {}}}
        _atomic_write_json(target, updated)

        bak_path = planning / "model_registry.json.bak"
        assert bak_path.exists()
        bak_data = json.loads(bak_path.read_text(encoding="utf-8"))
        assert bak_data["_meta"]["version"] == 1

    def test_no_backup_when_target_missing(self, tmp_path: Path) -> None:
        """No .bak should be created when the target file does not exist yet."""
        planning = tmp_path / ".planning"
        planning.mkdir()
        target = planning / "model_registry.json"

        _atomic_write_json(target, {"_meta": {"version": 1}, "models": {}})

        bak_path = planning / "model_registry.json.bak"
        assert not bak_path.exists()
        assert target.exists()
