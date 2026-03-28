"""Tests for persistent Docker manager (Gap 2).

Unit tests mock subprocess calls. Tests marked @pytest.mark.docker
require a running Docker daemon — skip in CI without Docker.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from amil_utils.validation.module_name import validate_module_name
from amil_utils.validation.persistent_docker import (
    PersistentDockerManager,
    STATE_FILE,
)


class TestPersistentDockerManagerUnit:
    """Unit tests — no Docker required."""

    def test_ensure_running_starts_containers(self, tmp_path):
        manager = PersistentDockerManager()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            manager._health_check = MagicMock(return_value=True)
            result = manager.ensure_running(state_dir=tmp_path)
        assert result is True
        assert manager._running is True

    def test_ensure_running_fails_on_bad_returncode(self, tmp_path):
        manager = PersistentDockerManager()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error")
            result = manager.ensure_running(state_dir=tmp_path)
        assert result is False

    def test_install_module_requires_running(self, tmp_path):
        manager = PersistentDockerManager()
        manager._running = False
        result = manager.install_module(tmp_path / "some_module")
        assert result.success is False
        assert "not running" in result.errors[0].lower()

    def test_state_persists_to_disk(self, tmp_path):
        manager = PersistentDockerManager()
        manager._state_dir = tmp_path
        manager._running = True
        manager.installed_modules = ["mod_a", "mod_b"]
        manager.install_order = [
            {"name": "mod_a", "timestamp": "2026-01-01T00:00:00Z", "success": True, "error": None},
        ]
        manager._save_state()

        state_path = tmp_path / STATE_FILE
        assert state_path.exists()
        state = json.loads(state_path.read_text())
        assert state["running"] is True
        assert state["installed_modules"] == ["mod_a", "mod_b"]

    def test_state_recovers_from_disk(self, tmp_path):
        state_path = tmp_path / STATE_FILE
        state_path.write_text(json.dumps({
            "running": True,
            "installed_modules": ["mod_x", "mod_y"],
            "install_order": [{"name": "mod_x", "timestamp": "T", "success": True, "error": None}],
        }))
        manager = PersistentDockerManager()
        manager._state_dir = tmp_path
        manager._load_state()
        assert manager._running is True
        assert manager.installed_modules == ["mod_x", "mod_y"]
        assert len(manager.install_order) == 1

    def test_stop_preserves_data(self, tmp_path):
        manager = PersistentDockerManager()
        manager._state_dir = tmp_path
        manager._running = True
        manager.installed_modules = ["mod_a"]
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            manager.stop()
        assert manager._running is False
        assert manager.installed_modules == ["mod_a"]

    def test_reset_destroys_volumes(self, tmp_path):
        manager = PersistentDockerManager()
        manager._state_dir = tmp_path
        manager._running = True
        manager.installed_modules = ["mod_a", "mod_b"]
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            manager.reset()
        assert manager._running is False
        assert manager.installed_modules == []
        assert manager.install_order == []

    def test_get_web_url(self):
        manager = PersistentDockerManager()
        assert manager.get_web_url() == "http://localhost:8069"

    def test_get_install_history_returns_copy(self):
        manager = PersistentDockerManager()
        manager.install_order = [{"name": "a"}]
        history = manager.get_install_history()
        assert history == [{"name": "a"}]
        history.append({"name": "b"})
        assert len(manager.install_order) == 1

    def test_health_check_returns_false_on_timeout(self):
        manager = PersistentDockerManager()
        import subprocess
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 10)):
            assert manager._health_check() is False

    def test_install_module_rejects_invalid_name(self, tmp_path):
        manager = PersistentDockerManager()
        manager._running = True
        # Command injection attempt
        result = manager.install_module(tmp_path / "foo; rm -rf /")
        assert result.success is False
        assert "Invalid module name" in result.errors[0]

    def test_install_module_rejects_uppercase_name(self, tmp_path):
        manager = PersistentDockerManager()
        manager._running = True
        result = manager.install_module(tmp_path / "MyModule")
        assert result.success is False
        assert "Invalid module name" in result.errors[0]

    def test_install_module_accepts_valid_name(self, tmp_path):
        manager = PersistentDockerManager()
        manager._running = True
        assert validate_module_name("uni_student") is None
        assert validate_module_name("hr_payroll") is None
        assert validate_module_name("0bad") is not None
        assert validate_module_name("") is not None

    def test_run_cross_module_rejects_invalid_names(self):
        manager = PersistentDockerManager()
        result = manager.run_cross_module_test(["valid_mod", "bad;name"])
        assert result.success is False

    def test_load_state_handles_corrupt_json(self, tmp_path):
        state_path = tmp_path / STATE_FILE
        state_path.write_text("{{not valid json", encoding="utf-8")
        manager = PersistentDockerManager()
        manager._state_dir = tmp_path
        # Should not raise — just logs warning and returns
        manager._load_state()
        assert manager._running is False
        assert manager.installed_modules == []

    def test_immutable_install_order(self, tmp_path):
        """Install operations create new lists, not mutate existing."""
        manager = PersistentDockerManager()
        manager._running = True
        manager._state_dir = tmp_path
        original_order = manager.install_order
        original_modules = manager.installed_modules

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            with patch("amil_utils.validation.log_parser.parse_install_log",
                       return_value=(True, None)):
                manager.install_module(tmp_path / "test_mod")

        # Original references should be unchanged (new lists created)
        assert original_order == []
        assert original_modules == []
