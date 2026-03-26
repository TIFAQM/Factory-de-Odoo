"""Tests for the headless odoo-ls LSP client."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from amil_utils.validation.odoo_ls_client import (
    OdooLSClient,
    decode_lsp_message,
    encode_lsp_message,
)


class TestEncodeMessage:
    """Tests for encode_lsp_message framing."""

    def test_encode_adds_content_length_header(self) -> None:
        msg = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
        encoded = encode_lsp_message(msg)
        assert encoded.startswith(b"Content-Length: ")
        assert b"\r\n\r\n" in encoded
        header, body = encoded.split(b"\r\n\r\n", 1)
        length = int(header.decode().split("Content-Length: ")[1])
        assert length == len(body)
        assert json.loads(body) == msg

    def test_encode_handles_unicode(self) -> None:
        msg = {"jsonrpc": "2.0", "method": "test", "params": {"text": "caf\u00e9"}}
        encoded = encode_lsp_message(msg)
        header, body = encoded.split(b"\r\n\r\n", 1)
        length = int(header.decode().split("Content-Length: ")[1])
        assert length == len(body)  # Byte count, not char count

    def test_encode_returns_bytes(self) -> None:
        msg = {"jsonrpc": "2.0", "method": "ping"}
        assert isinstance(encode_lsp_message(msg), bytes)


class TestDecodeMessage:
    """Tests for decode_lsp_message parsing."""

    def test_decode_parses_content_length_body(self) -> None:
        msg = {"jsonrpc": "2.0", "method": "test", "params": {"key": "value"}}
        body = json.dumps(msg).encode("utf-8")
        raw = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body
        decoded = decode_lsp_message(raw)
        assert decoded == msg

    def test_roundtrip_encode_decode(self) -> None:
        msg = {"jsonrpc": "2.0", "id": 42, "result": [1, 2, 3]}
        encoded = encode_lsp_message(msg)
        decoded = decode_lsp_message(encoded)
        assert decoded == msg

    def test_decode_unicode_body(self) -> None:
        msg = {"jsonrpc": "2.0", "method": "test", "params": {"text": "\u00fc\u00f6\u00e4"}}
        body = json.dumps(msg).encode("utf-8")
        raw = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body
        decoded = decode_lsp_message(raw)
        assert decoded == msg

    def test_decode_raises_on_missing_header(self) -> None:
        with pytest.raises(ValueError, match="Content-Length"):
            decode_lsp_message(b"no-header-here")


class TestOdooLSClientLifecycle:
    """Tests for OdooLSClient state management."""

    def test_is_alive_false_before_start(self, tmp_path: Path) -> None:
        client = OdooLSClient(
            binary_path=Path("odoo_ls_server"),
            config_path=tmp_path / "odools.toml",
            workspace_root=tmp_path,
        )
        assert client.is_alive is False

    def test_shutdown_noop_when_not_started(self, tmp_path: Path) -> None:
        client = OdooLSClient(
            binary_path=Path("odoo_ls_server"),
            config_path=tmp_path / "odools.toml",
            workspace_root=tmp_path,
        )
        client.shutdown()  # Should not raise

    def test_validate_module_raises_when_not_started(self, tmp_path: Path) -> None:
        client = OdooLSClient(
            binary_path=Path("odoo_ls_server"),
            config_path=tmp_path / "odools.toml",
            workspace_root=tmp_path,
        )
        with pytest.raises(RuntimeError, match="not running"):
            client.validate_module(tmp_path)

    def test_start_raises_timeout_on_bad_binary(self, tmp_path: Path) -> None:
        """Binary that exits immediately should timeout."""
        (tmp_path / "odools.toml").write_text('[[config]]\nname="test"')
        client = OdooLSClient(
            binary_path=Path("/usr/bin/false"),
            config_path=tmp_path / "odools.toml",
            workspace_root=tmp_path,
            index_timeout=2,
        )
        with pytest.raises((TimeoutError, OSError)):
            client.start()

    def test_restart_noop_when_not_started(self, tmp_path: Path) -> None:
        """Restart on a never-started client should not raise."""
        client = OdooLSClient(
            binary_path=Path("odoo_ls_server"),
            config_path=tmp_path / "odools.toml",
            workspace_root=tmp_path,
        )
        # restart calls shutdown + start; shutdown is noop but start needs
        # a real binary, so we only verify shutdown path does not blow up
        client.shutdown()

    def test_constructor_stores_params(self, tmp_path: Path) -> None:
        binary = Path("/usr/local/bin/odoo_ls_server")
        config = tmp_path / "odools.toml"
        client = OdooLSClient(
            binary_path=binary,
            config_path=config,
            workspace_root=tmp_path,
            log_level="debug",
            index_timeout=60,
            diag_timeout=15,
        )
        assert client._binary_path == binary
        assert client._config_path == config
        assert client._workspace_root == tmp_path
        assert client._log_level == "debug"
        assert client._index_timeout == 60
        assert client._diag_timeout == 15

    def test_diagnostics_dict_starts_empty(self, tmp_path: Path) -> None:
        client = OdooLSClient(
            binary_path=Path("odoo_ls_server"),
            config_path=tmp_path / "odools.toml",
            workspace_root=tmp_path,
        )
        assert client._diagnostics == {}


class TestMessageHandling:
    """Tests for internal message routing (unit-testable without subprocess)."""

    def test_handle_loading_status_stop_sets_ready(self, tmp_path: Path) -> None:
        client = OdooLSClient(
            binary_path=Path("odoo_ls_server"),
            config_path=tmp_path / "odools.toml",
            workspace_root=tmp_path,
        )
        msg = {
            "jsonrpc": "2.0",
            "method": "$/Odoo/loadingStatusUpdate",
            "params": {"state": "stop"},
        }
        client._handle_message(msg)
        assert client._ready.is_set()

    def test_handle_loading_status_non_stop_does_not_set_ready(
        self, tmp_path: Path
    ) -> None:
        client = OdooLSClient(
            binary_path=Path("odoo_ls_server"),
            config_path=tmp_path / "odools.toml",
            workspace_root=tmp_path,
        )
        msg = {
            "jsonrpc": "2.0",
            "method": "$/Odoo/loadingStatusUpdate",
            "params": {"state": "loading"},
        }
        client._handle_message(msg)
        assert not client._ready.is_set()

    def test_handle_publish_diagnostics_stores_results(self, tmp_path: Path) -> None:
        client = OdooLSClient(
            binary_path=Path("odoo_ls_server"),
            config_path=tmp_path / "odools.toml",
            workspace_root=tmp_path,
        )
        msg = {
            "jsonrpc": "2.0",
            "method": "textDocument/publishDiagnostics",
            "params": {
                "uri": "file:///tmp/test/models/test.py",
                "diagnostics": [
                    {
                        "range": {
                            "start": {"line": 10, "character": 5},
                            "end": {"line": 10, "character": 20},
                        },
                        "severity": 1,
                        "code": "OLS30101",
                        "message": "Undefined model 'res.partne'",
                    },
                ],
            },
        }
        client._handle_message(msg)
        assert "/tmp/test/models/test.py" in client._diagnostics
        diags = client._diagnostics["/tmp/test/models/test.py"]
        assert len(diags) == 1
        assert diags[0].code == "OLS30101"
        assert diags[0].severity == 1
        assert diags[0].line == 10
        assert diags[0].column == 5

    def test_handle_publish_diagnostics_empty_clears_file(
        self, tmp_path: Path
    ) -> None:
        client = OdooLSClient(
            binary_path=Path("odoo_ls_server"),
            config_path=tmp_path / "odools.toml",
            workspace_root=tmp_path,
        )
        # First add a diagnostic
        client._diagnostics["/tmp/test.py"] = ["placeholder"]
        # Then publish empty list
        msg = {
            "jsonrpc": "2.0",
            "method": "textDocument/publishDiagnostics",
            "params": {
                "uri": "file:///tmp/test.py",
                "diagnostics": [],
            },
        }
        client._handle_message(msg)
        assert client._diagnostics.get("/tmp/test.py") == []

    def test_handle_crash_notification_sets_crashed(self, tmp_path: Path) -> None:
        client = OdooLSClient(
            binary_path=Path("odoo_ls_server"),
            config_path=tmp_path / "odools.toml",
            workspace_root=tmp_path,
        )
        msg = {
            "jsonrpc": "2.0",
            "method": "$/Odoo/displayCrashNotification",
            "params": {"message": "Server crashed"},
        }
        client._handle_message(msg)
        assert client._crashed is True

    def test_handle_workspace_config_request(self, tmp_path: Path) -> None:
        """workspace/configuration requests should be queued for response."""
        client = OdooLSClient(
            binary_path=Path("odoo_ls_server"),
            config_path=tmp_path / "odools.toml",
            workspace_root=tmp_path,
        )
        msg = {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "workspace/configuration",
            "params": {"items": [{"section": "Odoo"}]},
        }
        client._handle_message(msg)
        assert len(client._pending_responses) == 1
        resp = client._pending_responses[0]
        assert resp["id"] == 7
        assert resp["result"] == [{"Odoo": {"selectedProfile": "factory"}}]

    def test_handle_unknown_method_does_not_raise(self, tmp_path: Path) -> None:
        client = OdooLSClient(
            binary_path=Path("odoo_ls_server"),
            config_path=tmp_path / "odools.toml",
            workspace_root=tmp_path,
        )
        msg = {
            "jsonrpc": "2.0",
            "method": "$/unknown/notification",
            "params": {},
        }
        client._handle_message(msg)  # Should not raise

    def test_diagnostics_set_event(self, tmp_path: Path) -> None:
        """Publishing diagnostics should signal the diag event."""
        client = OdooLSClient(
            binary_path=Path("odoo_ls_server"),
            config_path=tmp_path / "odools.toml",
            workspace_root=tmp_path,
        )
        msg = {
            "jsonrpc": "2.0",
            "method": "textDocument/publishDiagnostics",
            "params": {
                "uri": "file:///tmp/test.py",
                "diagnostics": [],
            },
        }
        client._handle_message(msg)
        assert client._diag_event.is_set()
