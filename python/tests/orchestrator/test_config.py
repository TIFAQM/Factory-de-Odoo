"""Tests for orchestrator config module."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from amil_utils.orchestrator.config import (
    VALID_LOCALIZATIONS,
    VALID_ODOO_VERSIONS,
    _parse_value,
    config_ensure_section,
    config_get,
    config_set,
    validate_odoo_config_key,
)


class TestConfigEnsureSection:
    def test_creates_config_when_missing(self, tmp_path: Path) -> None:
        planning = tmp_path / ".planning"
        planning.mkdir()
        result = config_ensure_section(tmp_path)
        assert result["created"] is True
        assert (planning / "config.json").exists()

    def test_returns_exists_when_present(self, tmp_path: Path) -> None:
        planning = tmp_path / ".planning"
        planning.mkdir()
        (planning / "config.json").write_text('{"profile": "quality"}')
        result = config_ensure_section(tmp_path)
        assert result["created"] is False
        assert result["reason"] == "already_exists"

    def test_creates_planning_dir_if_missing(self, tmp_path: Path) -> None:
        result = config_ensure_section(tmp_path)
        assert result["created"] is True
        assert (tmp_path / ".planning" / "config.json").exists()

    def test_default_config_values(self, tmp_path: Path) -> None:
        config_ensure_section(tmp_path)
        config = json.loads((tmp_path / ".planning" / "config.json").read_text())
        assert config["model_profile"] == "balanced"
        assert config["commit_docs"] is True
        assert config["branching_strategy"] == "none"


class TestValidateOdooConfigKey:
    def test_valid_version(self) -> None:
        assert validate_odoo_config_key("odoo.version", "17.0", "17.0") is None

    def test_invalid_version(self) -> None:
        err = validate_odoo_config_key("odoo.version", "16.0", "16.0")
        assert err is not None
        assert "Invalid" in err

    def test_valid_localization(self) -> None:
        assert validate_odoo_config_key("odoo.localization", "pk", "pk") is None

    def test_invalid_localization(self) -> None:
        err = validate_odoo_config_key("odoo.localization", "xx", "xx")
        assert err is not None

    def test_multi_company_must_be_bool(self) -> None:
        err = validate_odoo_config_key("odoo.multi_company", "yes", "yes")
        assert err is not None

    def test_scope_levels_must_be_array(self) -> None:
        err = validate_odoo_config_key("odoo.scope_levels", "single", "single")
        assert err is not None

    def test_non_odoo_key_always_valid(self) -> None:
        assert validate_odoo_config_key("model_profile", "quality", "quality") is None


class TestConfigSet:
    def test_sets_simple_value(self, tmp_path: Path) -> None:
        planning = tmp_path / ".planning"
        planning.mkdir()
        (planning / "config.json").write_text("{}")
        result = config_set(tmp_path, "model_profile", "quality")
        assert result["updated"] is True
        config = json.loads((planning / "config.json").read_text())
        assert config["model_profile"] == "quality"

    def test_sets_nested_value(self, tmp_path: Path) -> None:
        planning = tmp_path / ".planning"
        planning.mkdir()
        (planning / "config.json").write_text('{"workflow": {}}')
        config_set(tmp_path, "workflow.research", "false")
        config = json.loads((planning / "config.json").read_text())
        assert config["workflow"]["research"] is False

    def test_parses_boolean_true(self, tmp_path: Path) -> None:
        planning = tmp_path / ".planning"
        planning.mkdir()
        (planning / "config.json").write_text("{}")
        config_set(tmp_path, "flag", "true")
        config = json.loads((planning / "config.json").read_text())
        assert config["flag"] is True

    def test_parses_number(self, tmp_path: Path) -> None:
        planning = tmp_path / ".planning"
        planning.mkdir()
        (planning / "config.json").write_text("{}")
        config_set(tmp_path, "count", "42")
        config = json.loads((planning / "config.json").read_text())
        assert config["count"] == 42

    def test_preserves_odoo_version_as_string(self, tmp_path: Path) -> None:
        planning = tmp_path / ".planning"
        planning.mkdir()
        (planning / "config.json").write_text("{}")
        config_set(tmp_path, "odoo.version", "17.0")
        config = json.loads((planning / "config.json").read_text())
        assert config["odoo"]["version"] == "17.0"

    def test_validates_odoo_key(self, tmp_path: Path) -> None:
        planning = tmp_path / ".planning"
        planning.mkdir()
        (planning / "config.json").write_text("{}")
        with pytest.raises(ValueError, match="Invalid"):
            config_set(tmp_path, "odoo.version", "16.0")

    def test_missing_key_path_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="key"):
            config_set(tmp_path, "", "value")


class TestConfigGet:
    def test_gets_simple_value(self, tmp_path: Path) -> None:
        planning = tmp_path / ".planning"
        planning.mkdir()
        (planning / "config.json").write_text('{"model_profile": "quality"}')
        result = config_get(tmp_path, "model_profile")
        assert result == "quality"

    def test_gets_nested_value(self, tmp_path: Path) -> None:
        planning = tmp_path / ".planning"
        planning.mkdir()
        (planning / "config.json").write_text('{"workflow": {"research": true}}')
        result = config_get(tmp_path, "workflow.research")
        assert result is True

    def test_missing_key_raises(self, tmp_path: Path) -> None:
        planning = tmp_path / ".planning"
        planning.mkdir()
        (planning / "config.json").write_text("{}")
        with pytest.raises(KeyError, match="not found"):
            config_get(tmp_path, "nonexistent")

    def test_missing_config_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            config_get(tmp_path, "anything")


class TestParseValueStrictNumeric:
    def test_integer(self) -> None:
        assert _parse_value("123") == 123
        assert isinstance(_parse_value("123"), int)

    def test_float(self) -> None:
        result = _parse_value("12.5")
        assert result == 12.5
        assert isinstance(result, float)

    def test_scientific_notation_stays_string(self) -> None:
        assert _parse_value("123.4e5") == "123.4e5"
        assert isinstance(_parse_value("123.4e5"), str)

    def test_negative_integer(self) -> None:
        assert _parse_value("-42") == -42
        assert isinstance(_parse_value("-42"), int)

    def test_boolean_true(self) -> None:
        assert _parse_value("true") is True

    def test_plain_string(self) -> None:
        assert _parse_value("hello") == "hello"
        assert isinstance(_parse_value("hello"), str)
