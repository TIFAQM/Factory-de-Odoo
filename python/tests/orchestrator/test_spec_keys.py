"""Tests for spec_keys — required top-level key validation for spec.json."""
from __future__ import annotations

import json
from pathlib import Path

from amil_utils.orchestrator.spec_keys import REQUIRED_SPEC_KEYS, check_spec_keys


def _write_spec(tmp_path: Path, data: dict) -> Path:
    p = tmp_path / "spec.json"
    p.write_text(json.dumps(data))
    return p


def test_full_spec_passes(tmp_path: Path) -> None:
    spec = {k: {} for k in REQUIRED_SPEC_KEYS}
    result = check_spec_keys(_write_spec(tmp_path, spec))
    assert result["valid"] is True
    assert result["missing"] == []
    assert result["key_count"] == len(REQUIRED_SPEC_KEYS)


def test_missing_keys_reported(tmp_path: Path) -> None:
    result = check_spec_keys(_write_spec(tmp_path, {"module_name": "uni_core"}))
    assert result["valid"] is False
    assert "models" in result["missing"]
    assert "security" in result["missing"]


def test_minimal_mode_only_needs_module_name(tmp_path: Path) -> None:
    result = check_spec_keys(
        _write_spec(tmp_path, {"module_name": "uni_core", "models": []}),
        minimal=True,
    )
    assert result["valid"] is True
    assert result["module_name"] == "uni_core"
    assert result["model_count"] == 0


def test_invalid_json_reported(tmp_path: Path) -> None:
    p = tmp_path / "spec.json"
    p.write_text("{not json")
    result = check_spec_keys(p)
    assert result["valid"] is False
    assert "error" in result


def test_missing_file_reported(tmp_path: Path) -> None:
    result = check_spec_keys(tmp_path / "nope.json")
    assert result["valid"] is False
    assert "error" in result


def test_non_object_root_reported(tmp_path: Path) -> None:
    p = tmp_path / "spec.json"
    p.write_text(json.dumps([1, 2, 3]))
    result = check_spec_keys(p)
    assert result["valid"] is False
    assert result["error"] == "spec root is not an object"
