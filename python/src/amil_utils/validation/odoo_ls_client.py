"""Headless LSP client for the odoo-ls language server.

Manages ``odoo_ls_server`` as a subprocess, communicates via JSON-RPC
over stdin/stdout, and collects diagnostics for generated Odoo modules.

Wire format (LSP base protocol)::

    Content-Length: <byte-count>\\r\\n
    \\r\\n
    <json-payload>

The server emits a custom notification ``$Odoo/loadingStatusUpdate``
with ``{"state": "stop"}`` when indexing is complete.  Diagnostics are
pushed via standard ``textDocument/publishDiagnostics`` notifications.
"""
from __future__ import annotations

import json
import logging
import queue
import subprocess
import threading
from pathlib import Path
from typing import Any
from urllib.parse import unquote as url_unquote

from amil_utils.validation.types import OLSDiagnostic

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# JSON-RPC message framing helpers
# ---------------------------------------------------------------------------

_HEADER_ENCODING = "ascii"
_BODY_ENCODING = "utf-8"
_MAX_HEADER_SIZE = 8192  # 8 KB — guards against unbounded header accumulation
_MAX_BODY_SIZE = 10 * 1024 * 1024  # 10 MB — guards against oversized messages


def encode_lsp_message(msg: dict[str, Any]) -> bytes:
    """Encode a JSON-RPC message with Content-Length header.

    Returns the full wire-format bytes ready to write to stdin.
    """
    body = json.dumps(msg).encode(_BODY_ENCODING)
    header = f"Content-Length: {len(body)}\r\n\r\n".encode(_HEADER_ENCODING)
    return header + body


def decode_lsp_message(raw: bytes) -> dict[str, Any]:
    """Decode a Content-Length-framed LSP message.

    Parameters
    ----------
    raw:
        The full wire-format bytes including header and body.

    Raises
    ------
    ValueError
        If the Content-Length header is missing or malformed.
    """
    separator = b"\r\n\r\n"
    idx = raw.find(separator)
    if idx == -1:
        raise ValueError("Content-Length header not found in message")

    header_bytes = raw[:idx]
    body_bytes = raw[idx + len(separator) :]

    header_str = header_bytes.decode(_HEADER_ENCODING)
    if "Content-Length:" not in header_str:
        raise ValueError("Content-Length header not found in message")

    return json.loads(body_bytes.decode(_BODY_ENCODING))


def _uri_to_path(uri: str, *, workspace_root: Path | None = None) -> str:
    """Convert a ``file://`` URI to a local filesystem path.

    When *workspace_root* is provided, the resolved path is checked to
    ensure it does not escape the workspace (guards against directory
    traversal from a malicious or compromised language server).
    """
    prefix = "file://"
    raw = url_unquote(uri[len(prefix):]) if uri.startswith(prefix) else uri
    if workspace_root is not None:
        resolved = Path(raw).resolve()
        ws_resolved = workspace_root.resolve()
        if not resolved.is_relative_to(ws_resolved):
            logger.warning(
                "Path %s escapes workspace %s — returning raw URI",
                resolved, ws_resolved,
            )
            return uri  # Return the raw URI, not the decoded path
    return raw


def _path_to_uri(path: Path) -> str:
    """Convert a local filesystem path to a ``file://`` URI."""
    return f"file://{path.resolve()}"


# ---------------------------------------------------------------------------
# Language ID mapping for didOpen
# ---------------------------------------------------------------------------

_LANG_IDS: dict[str, str] = {
    ".py": "python",
    ".xml": "xml",
    ".csv": "csv",
}


# ---------------------------------------------------------------------------
# OdooLSClient
# ---------------------------------------------------------------------------


class OdooLSClient:
    """Headless client for the odoo-ls language server.

    Usage::

        client = OdooLSClient(
            binary_path=Path("tools/odoo-ls/odoo_ls_server"),
            config_path=Path("odools.toml"),
            workspace_root=Path("/path/to/workspace"),
        )
        client.start()  # blocks until server finishes indexing
        diagnostics = client.validate_module(Path("/path/to/module"))
        client.shutdown()
    """

    def __init__(
        self,
        binary_path: Path,
        config_path: Path,
        workspace_root: Path,
        *,
        log_level: str = "info",
        index_timeout: int = 120,
        diag_timeout: int = 30,
        profile_name: str = "factory",
    ) -> None:
        self._binary_path = binary_path
        self._config_path = config_path
        self._workspace_root = workspace_root
        self._log_level = log_level
        self._index_timeout = index_timeout
        self._diag_timeout = diag_timeout
        self._profile_name = profile_name

        # Subprocess handle
        self._process: subprocess.Popen[bytes] | None = None

        # Threading primitives
        self._ready = threading.Event()
        self._diag_event = threading.Event()
        self._lock = threading.Lock()
        self._reader_thread: threading.Thread | None = None
        # Queue for responses that must be written to stdin from a
        # separate thread (avoids deadlock: reader blocks on stdout read
        # while stdin write blocks because the server's stdin buffer is full).
        self._response_queue: "queue.Queue[dict[str, Any]]" = queue.Queue()
        self._writer_thread: threading.Thread | None = None

        # State
        self._diagnostics: dict[str, list[OLSDiagnostic]] = {}
        self._crashed = False
        self._next_id = 1
        self._pending_responses: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def is_alive(self) -> bool:
        """Return True when the subprocess is running."""
        if self._process is None:
            return False
        return self._process.poll() is None

    def start(self) -> None:
        """Spawn the language server and wait for indexing to complete.

        Raises
        ------
        TimeoutError
            If the server does not finish indexing within *index_timeout*.
        OSError
            If the binary cannot be started.
        """
        cmd = [
            str(self._binary_path),
            "--log-level",
            self._log_level,
            "--config-path",
            str(self._config_path),
        ]
        logger.info("Starting odoo-ls: %s", " ".join(cmd))

        self._ready.clear()
        self._crashed = False
        self._diagnostics = {}
        self._pending_responses = []

        try:
            self._process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except OSError:
            logger.error("Failed to start odoo-ls binary: %s", self._binary_path)
            raise

        # Start background reader + writer threads.
        # Writer is separate to avoid deadlock: reader can't write to stdin
        # while blocking on stdout read (server's stdin buffer may be full).
        self._reader_thread = threading.Thread(
            target=self._read_loop, daemon=True, name="odoo-ls-reader"
        )
        self._reader_thread.start()
        self._writer_thread = threading.Thread(
            target=self._write_loop, daemon=True, name="odoo-ls-writer"
        )
        self._writer_thread.start()

        # Send initialize request
        self._send_initialize()

        # Wait for ready signal
        if not self._ready.wait(timeout=self._index_timeout):
            # Check if process died
            if self._process.poll() is not None:
                stderr_output = ""
                if self._process.stderr:
                    stderr_output = self._process.stderr.read().decode(
                        "utf-8", errors="replace"
                    )
                self._kill()
                raise OSError(
                    f"odoo-ls exited with code {self._process.returncode}: "
                    f"{stderr_output[:500]}"
                )
            self._kill()
            raise TimeoutError(
                f"odoo-ls did not finish indexing within {self._index_timeout}s"
            )

        logger.info("odoo-ls indexing complete, server ready")

    def validate_module(self, module_path: Path) -> list[OLSDiagnostic]:
        """Open all source files in a module and collect diagnostics.

        Parameters
        ----------
        module_path:
            Path to the Odoo module directory.

        Returns
        -------
        list[OLSDiagnostic]
            Flattened list of all diagnostics for the module's files.

        Raises
        ------
        RuntimeError
            If the server is not running.
        """
        if not self.is_alive:
            raise RuntimeError("odoo-ls is not running")

        # Clear previous diagnostics
        with self._lock:
            self._diagnostics = {}
            self._diag_event.clear()

        # Collect source files
        source_files = _collect_source_files(module_path)
        if not source_files:
            return []

        # Send didOpen for each file
        for file_path in source_files:
            self._send_did_open(file_path)

        # Wait for diagnostics (server pushes them asynchronously)
        self._diag_event.wait(timeout=self._diag_timeout)

        # Flush any pending responses first
        self._flush_pending_responses()

        # Collect results
        with self._lock:
            result: list[OLSDiagnostic] = []
            for diag_list in self._diagnostics.values():
                result.extend(diag_list)
            return result

    def shutdown(self) -> None:
        """Gracefully shut down the server, or no-op if not started."""
        if self._process is None:
            return

        if self.is_alive:
            try:
                self._send_request("shutdown", {})
                self._send_notification("exit", None)
            except BrokenPipeError:
                logger.warning("Broken pipe during shutdown (server already exited)")

            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                logger.warning("odoo-ls did not exit gracefully, killing")
                self._kill()
        else:
            self._kill()

        # Stop the writer thread
        self._response_queue.put(None)  # Poison pill
        self._process = None
        logger.info("odoo-ls shut down")

    def restart(self) -> None:
        """Kill the server and start it again."""
        self.shutdown()
        self.start()

    # ------------------------------------------------------------------
    # Background reader
    # ------------------------------------------------------------------

    def _read_loop(self) -> None:
        """Read JSON-RPC messages from stdout until the stream closes.

        Reads one byte at a time for headers (required to avoid blocking
        past a message boundary when the server is waiting for a response)
        but uses exact-length reads for bodies.
        """
        assert self._process is not None
        stdout = self._process.stdout
        if stdout is None:
            return

        try:
            while True:
                # Read header byte-by-byte until \r\n\r\n.
                # We MUST use read(1) here because read(N) for N>1 would
                # block if the server sent fewer than N bytes and is waiting
                # for a response from us (deadlock). The header is typically
                # ~30 bytes so this is ~30 syscalls per message.
                header_buf = bytearray()
                while not header_buf.endswith(b"\r\n\r\n"):
                    byte = stdout.read(1)
                    if not byte:
                        return  # Stream closed
                    header_buf += byte
                    if len(header_buf) > _MAX_HEADER_SIZE:
                        logger.error("LSP header exceeded %d bytes, dropping", _MAX_HEADER_SIZE)
                        return

                # Parse Content-Length
                content_length = _parse_content_length(bytes(header_buf))
                if content_length is None:
                    continue
                if content_length > _MAX_BODY_SIZE:
                    logger.error("Content-Length %d exceeds max %d", content_length, _MAX_BODY_SIZE)
                    continue

                # Read body in one call (exact length = won't over-read)
                body_bytes = stdout.read(content_length)
                if len(body_bytes) < content_length:
                    return  # Stream closed mid-message

                try:
                    msg = json.loads(body_bytes.decode(_BODY_ENCODING))
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    logger.warning("Failed to decode message: %s", exc)
                    continue

                self._handle_message(msg)

        except (OSError, ValueError) as exc:
            logger.debug("Reader loop ended: %s", exc)
            return
        except Exception as exc:
            logger.error("Reader loop crashed: %s", exc, exc_info=True)
            return

    def _handle_message(self, msg: dict[str, Any]) -> None:
        """Route an incoming JSON-RPC message to the appropriate handler."""
        method = msg.get("method", "")

        # Server request (has "id" + "method")
        if "id" in msg and "method" in msg:
            self._handle_server_request(msg)
            return

        # Notification (has "method" but no "id")
        if method == "$Odoo/loadingStatusUpdate":
            params = msg.get("params", {})
            # params can be a string ("start"/"stop") or a dict ({"state": "start"})
            if isinstance(params, str):
                state = params
            else:
                state = params.get("state", "")
            logger.info("odoo-ls loading status: %s", state)
            if state == "stop":
                self._ready.set()

        elif method == "textDocument/publishDiagnostics":
            self._handle_diagnostics(msg)

        elif method == "$Odoo/displayCrashNotification":
            params = msg.get("params", {})
            logger.error("odoo-ls CRASH: %s", params.get("message", "unknown"))
            self._crashed = True

        elif method == "$Odoo/restartNeeded":
            logger.warning("odoo-ls requested restart")

        else:
            logger.debug("Unhandled message: %s", method)

    def _handle_server_request(self, msg: dict[str, Any]) -> None:
        """Handle a request from the server that expects a response.

        Responses are queued to ``_response_queue`` and written by the
        separate writer thread.  This avoids a deadlock where the reader
        thread blocks on ``stdin.write()`` while the server's stdin buffer
        is full and the server is blocked on stdout write.
        """
        method = msg.get("method", "")
        request_id = msg["id"]

        if method == "workspace/configuration":
            params = msg.get("params", {})
            items = params.get("items", []) if isinstance(params, dict) else []
            config_entry = {"selectedProfile": self._profile_name}
            result = [config_entry] * max(len(items), 1)
            response = {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": result,
            }
        else:
            # Acknowledge all other requests with null
            response = {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": None,
            }

        self._response_queue.put(response)

    def _handle_diagnostics(self, msg: dict[str, Any]) -> None:
        """Process a textDocument/publishDiagnostics notification."""
        params = msg.get("params", {})
        uri = params.get("uri", "")
        raw_diags = params.get("diagnostics", [])

        file_path = _uri_to_path(uri, workspace_root=self._workspace_root)

        diags = [
            OLSDiagnostic(
                file=file_path,
                line=d.get("range", {}).get("start", {}).get("line", 0),
                column=d.get("range", {}).get("start", {}).get("character", 0),
                code=str(d.get("code", "")),
                message=d.get("message", ""),
                severity=d.get("severity", 4),
            )
            for d in raw_diags
        ]

        with self._lock:
            self._diagnostics[file_path] = diags

        self._diag_event.set()

    # ------------------------------------------------------------------
    # LSP message helpers
    # ------------------------------------------------------------------

    def _send_request(self, method: str, params: Any) -> int:
        """Send a JSON-RPC request and return the request ID."""
        request_id = self._next_id
        self._next_id += 1
        msg = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }
        self._write(encode_lsp_message(msg))
        return request_id

    def _send_notification(self, method: str, params: Any) -> None:
        """Send a JSON-RPC notification (no response expected)."""
        msg: dict[str, Any] = {
            "jsonrpc": "2.0",
            "method": method,
        }
        if params is not None:
            msg["params"] = params
        self._write(encode_lsp_message(msg))

    def _write(self, data: bytes) -> None:
        """Write raw bytes to the subprocess stdin.

        Handles BrokenPipeError gracefully when the server has crashed.
        """
        if self._process is None or self._process.stdin is None:
            return
        try:
            self._process.stdin.write(data)
            self._process.stdin.flush()
        except BrokenPipeError:
            logger.warning("Broken pipe writing to odoo-ls (server may have crashed)")

    def _flush_pending_responses(self) -> None:
        """Send any queued responses back to the server."""
        with self._lock:
            responses = list(self._pending_responses)
            self._pending_responses.clear()

        for resp in responses:
            self._write(encode_lsp_message(resp))

    # ------------------------------------------------------------------
    # Initialize handshake
    # ------------------------------------------------------------------

    def _send_initialize(self) -> None:
        """Send the LSP initialize request + initialized notification."""
        init_params = {
            "processId": None,
            "capabilities": {},
            "rootUri": _path_to_uri(self._workspace_root),
            "workspaceFolders": [
                {
                    "uri": _path_to_uri(self._workspace_root),
                    "name": self._workspace_root.name,
                }
            ],
        }
        self._send_request("initialize", init_params)
        self._send_notification("initialized", {})

    # ------------------------------------------------------------------
    # File opening
    # ------------------------------------------------------------------

    def _send_did_open(self, file_path: Path) -> None:
        """Send a textDocument/didOpen notification for a file."""
        suffix = file_path.suffix
        lang_id = _LANG_IDS.get(suffix, "plaintext")

        try:
            text = file_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            logger.warning("Cannot read %s: %s", file_path, exc)
            return

        self._send_notification(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": _path_to_uri(file_path),
                    "languageId": lang_id,
                    "version": 1,
                    "text": text,
                }
            },
        )

    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # Background writer (avoids reader/writer deadlock on pipes)
    # ------------------------------------------------------------------

    def _write_loop(self) -> None:
        """Drain the response queue and write to stdin."""
        while True:
            try:
                msg = self._response_queue.get(timeout=1.0)
            except queue.Empty:
                if self._process is None or self._process.poll() is not None:
                    return  # Process exited
                continue
            if msg is None:
                return  # Poison pill = shutdown
            self._write(encode_lsp_message(msg))

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def _kill(self) -> None:
        """Force-kill the subprocess if still running."""
        if self._process is None:
            return
        try:
            self._process.kill()
            self._process.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired) as exc:
            logger.debug("Failed to kill odoo-ls subprocess: %s", exc)


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _parse_content_length(header: bytes) -> int | None:
    """Extract the Content-Length value from an LSP header block."""
    for line in header.decode(_HEADER_ENCODING).splitlines():
        if line.startswith("Content-Length:"):
            try:
                return int(line.split(":", 1)[1].strip())
            except ValueError:
                return None
    return None


def _collect_source_files(module_path: Path) -> list[Path]:
    """Collect all .py, .xml, and .csv files in a module directory."""
    extensions = {".py", ".xml", ".csv"}
    files: list[Path] = []
    if not module_path.is_dir():
        return files
    for ext in sorted(extensions):
        files.extend(sorted(module_path.rglob(f"*{ext}")))
    return files
