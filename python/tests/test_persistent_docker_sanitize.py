"""Tests for _sanitize_docker_error() in persistent_docker.py.

TDD: Tests written FIRST (RED), then implementation added (GREEN).
"""
from __future__ import annotations

import pytest

from amil_utils.validation.persistent_docker import _sanitize_docker_error


class TestSanitizeDockerError:
    """Unit tests for Docker stderr sanitization."""

    # ------------------------------------------------------------------ #
    # Truncation                                                           #
    # ------------------------------------------------------------------ #

    def test_short_message_unchanged_length(self):
        msg = "simple error occurred"
        result = _sanitize_docker_error(msg)
        assert result == msg

    def test_long_message_truncated_to_200_chars(self):
        msg = "x" * 300
        result = _sanitize_docker_error(msg)
        assert len(result) == 203  # 200 chars + "..."

    def test_message_exactly_200_chars_not_truncated(self):
        msg = "a" * 200
        result = _sanitize_docker_error(msg)
        assert result == msg
        assert "..." not in result

    def test_message_201_chars_gets_ellipsis(self):
        msg = "a" * 201
        result = _sanitize_docker_error(msg)
        assert result.endswith("...")
        assert len(result) == 203

    def test_custom_max_length_respected(self):
        msg = "x" * 100
        result = _sanitize_docker_error(msg, max_length=50)
        assert len(result) == 53  # 50 + "..."

    # ------------------------------------------------------------------ #
    # Container ID stripping                                               #
    # ------------------------------------------------------------------ #

    def test_strips_container_id_12_hex_chars(self):
        msg = "Container abc123def456 is not running"
        result = _sanitize_docker_error(msg)
        assert "abc123def456" not in result
        assert "[REDACTED]" in result

    def test_strips_container_id_mixed_case(self):
        msg = "Container ABC123def456 failed to start"
        result = _sanitize_docker_error(msg)
        assert "ABC123def456" not in result
        assert "[REDACTED]" in result

    # ------------------------------------------------------------------ #
    # Network name stripping                                               #
    # ------------------------------------------------------------------ #

    def test_strips_network_name(self):
        msg = "network factory-de-odoo_default not found"
        result = _sanitize_docker_error(msg)
        assert "factory-de-odoo_default" not in result
        assert "[REDACTED]" in result

    def test_strips_network_with_underscores(self):
        msg = "attaching to network my_internal_net failed"
        result = _sanitize_docker_error(msg)
        assert "my_internal_net" not in result
        assert "[REDACTED]" in result

    # ------------------------------------------------------------------ #
    # IP:port stripping                                                    #
    # ------------------------------------------------------------------ #

    def test_strips_ip_port(self):
        msg = "connection refused at 192.168.1.100:5432"
        result = _sanitize_docker_error(msg)
        assert "192.168.1.100:5432" not in result
        assert "[REDACTED]" in result

    def test_strips_localhost_ip_port(self):
        msg = "bind failed: 127.0.0.1:8069 already in use"
        result = _sanitize_docker_error(msg)
        assert "127.0.0.1:8069" not in result
        assert "[REDACTED]" in result

    def test_strips_docker_internal_ip(self):
        msg = "timeout connecting to 172.17.0.2:5432"
        result = _sanitize_docker_error(msg)
        assert "172.17.0.2:5432" not in result
        assert "[REDACTED]" in result

    # ------------------------------------------------------------------ #
    # Combined / real-world patterns                                       #
    # ------------------------------------------------------------------ #

    def test_strips_multiple_patterns_in_one_message(self):
        msg = (
            "Container abc123def456 on network bridge_net "
            "failed: 10.0.0.1:5432 connection refused"
        )
        result = _sanitize_docker_error(msg)
        assert "abc123def456" not in result
        assert "bridge_net" not in result
        assert "10.0.0.1:5432" not in result

    def test_preserves_generic_error_text(self):
        msg = "Error response from daemon: No such container"
        result = _sanitize_docker_error(msg)
        assert "Error response from daemon" in result
        assert "No such container" in result

    def test_empty_string_returns_empty(self):
        assert _sanitize_docker_error("") == ""

    def test_whitespace_only_preserved(self):
        result = _sanitize_docker_error("   ")
        assert result.strip() == ""

    def test_truncation_applied_after_sanitization(self):
        """Truncation should happen after regex substitution, not before."""
        # Build a long message that has infra patterns at the end
        prefix = "A" * 190
        msg = prefix + " 192.168.1.1:5432"
        result = _sanitize_docker_error(msg)
        # The IP:port should be replaced and result should be truncated
        assert "192.168.1.1:5432" not in result

    def test_returns_string_type(self):
        result = _sanitize_docker_error("some error")
        assert isinstance(result, str)
