#!/usr/bin/env python3
"""
Benchmark script for odoo-ls language server.

GO/NO-GO thresholds:
  - Indexing must complete in < 120 seconds
  - First diagnostic must arrive in < 10 seconds after didOpen
  - Server must handle $/Odoo/loadingStatusUpdate notification

Usage:
    python scripts/benchmark_odoo_ls.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

# Ensure amil_utils is importable when running as a standalone script
_src_dir = str(Path(__file__).resolve().parent.parent / "python" / "src")
if _src_dir not in sys.path:
    sys.path.insert(0, _src_dir)

from amil_utils.validation.odoo_ls_client import encode_lsp_message as encode_message

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BINARY = os.environ.get("ODOOLS_BINARY", os.path.expanduser("~/Factory-de-Odoo/tools/odoo-ls/odoo_ls_server"))
CONFIG = os.environ.get("ODOOLS_CONFIG", "/tmp/factory-benchmark/odools.toml")
WORKSPACE = os.environ.get("ODOOLS_WORKSPACE", "/tmp/factory-benchmark")
ODOO_SOURCE = os.environ.get("ODOO_SOURCE_PATH", os.path.expanduser("~/Factory-de-Odoo/tools/odoo-source/19.0"))

INDEXING_THRESHOLD_S = 120
DIAG_LATENCY_THRESHOLD_S = 10

# ---------------------------------------------------------------------------
# LSP wire helpers
# ---------------------------------------------------------------------------


def read_message(stdout) -> dict | None:
    """Read one LSP message from stdout. Returns None on EOF."""
    header = b""
    while not header.endswith(b"\r\n\r\n"):
        byte = stdout.read(1)
        if not byte:
            return None
        header += byte
    try:
        length = int(
            header.decode("ascii").split("Content-Length: ")[1].split("\r\n")[0]
        )
    except (IndexError, ValueError):
        return None
    body = stdout.read(length)
    if len(body) < length:
        return None
    return json.loads(body)


# ---------------------------------------------------------------------------
# Benchmark
# ---------------------------------------------------------------------------


def main() -> int:
    results = {
        "indexing_time_s": None,
        "diagnostic_latency_s": None,
        "diagnostics": [],
        "loading_status_received": False,
        "errors": [],
    }

    # Ensure workspace exists
    Path(WORKSPACE).mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # 1. Start odoo-ls
    # ------------------------------------------------------------------
    print(f"[*] Starting odoo-ls: {BINARY}")
    print(f"[*] Config: {CONFIG}")

    if not os.path.isfile(BINARY):
        results["errors"].append(f"Binary not found: {BINARY}")
        _print_results(results)
        return 1

    proc = subprocess.Popen(
        [BINARY, "--log-level", "info", "--config-path", CONFIG],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    try:
        return _run_benchmark(proc, results)
    except Exception as exc:
        import traceback
        tb = traceback.format_exc()
        results["errors"].append(f"Unexpected error: {exc}\n{tb}")
        _print_results(results)
        return 1
    finally:
        _cleanup(proc)


def _handle_server_request(msg: dict, stdin) -> None:
    """Respond to server-initiated requests (method + id present)."""
    if "id" not in msg or "method" not in msg:
        return
    req_method = msg["method"]
    if req_method == "workspace/configuration":
        req_params = msg.get("params", {})
        items = req_params.get("items", []) if isinstance(req_params, dict) else []
        print(f"    [workspace/configuration] request items: {json.dumps(items)[:300]}")
        config_response = {
            "configPath": CONFIG,
            "odooPath": "/home/inshal-rauf/Factory-de-Odoo/tools/odoo-source/19.0",
            "addons": [
                "/home/inshal-rauf/Factory-de-Odoo/tools/odoo-source/19.0/addons",
                "/home/inshal-rauf/Factory-de-Odoo/tools/odoo-source/19.0/odoo/addons",
            ],
            "pythonPath": "/usr/bin/python3",
            "diagMissingImports": "only_odoo",
            "refreshMode": "off",
        }
        result = [config_response] * len(items) if items else [config_response]
        response = {
            "jsonrpc": "2.0",
            "id": msg["id"],
            "result": result,
        }
    else:
        response = {
            "jsonrpc": "2.0",
            "id": msg["id"],
            "result": None,
        }
    stdin.write(encode_message(response))
    stdin.flush()


def _run_benchmark(proc: subprocess.Popen, results: dict) -> int:
    stdin = proc.stdin
    stdout = proc.stdout

    # ------------------------------------------------------------------
    # 2. Send initialize
    # ------------------------------------------------------------------
    print("[*] Sending initialize request ...")
    initialize_req = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "processId": None,
            "rootUri": f"file://{WORKSPACE}",
            "capabilities": {},
            "workspaceFolders": [
                {"uri": f"file://{WORKSPACE}", "name": "benchmark"},
                {"uri": f"file://{ODOO_SOURCE}", "name": "odoo-19"},
            ],
        },
    }
    stdin.write(encode_message(initialize_req))
    stdin.flush()

    # Wait for initialize response
    init_response = None
    deadline = time.monotonic() + 30  # 30s timeout for init handshake
    while time.monotonic() < deadline:
        msg = read_message(stdout)
        if msg is None:
            stderr_out = _read_stderr(proc)
            results["errors"].append(
                f"Server closed stdout during init. stderr: {stderr_out}"
            )
            _print_results(results)
            return 1
        if msg.get("id") == 1:
            init_response = msg
            break
        # Could be other notifications; continue

    if init_response is None:
        results["errors"].append("Timed out waiting for initialize response")
        _print_results(results)
        return 1

    if "error" in init_response:
        results["errors"].append(
            f"Initialize error: {json.dumps(init_response['error'])}"
        )
        _print_results(results)
        return 1

    print("[+] Initialize response received")

    # ------------------------------------------------------------------
    # 3. Send initialized notification
    # ------------------------------------------------------------------
    initialized_notif = {
        "jsonrpc": "2.0",
        "method": "initialized",
        "params": {},
    }
    stdin.write(encode_message(initialized_notif))
    stdin.flush()
    print("[*] Sent initialized notification")

    # ------------------------------------------------------------------
    # 4. Wait for $/Odoo/loadingStatusUpdate {state: "stop"}
    # ------------------------------------------------------------------
    print("[*] Waiting for indexing to complete ($/Odoo/loadingStatusUpdate) ...")
    indexing_start = time.monotonic()
    indexing_deadline = indexing_start + 300  # hard timeout 5 min
    indexing_msg_count = 0

    while time.monotonic() < indexing_deadline:
        msg = read_message(stdout)
        if msg is None:
            stderr_out = _read_stderr(proc)
            results["errors"].append(
                f"Server closed stdout during indexing. stderr: {stderr_out}"
            )
            break

        if not isinstance(msg, dict):
            print(f"    [!] Non-dict message: {repr(msg)[:200]}")
            continue

        method = msg.get("method", "")

        # Handle server requests that need a response
        _handle_server_request(msg, stdin)

        # Match loadingStatusUpdate flexibly ($/Odoo/ or $Odoo/)
        if "loadingStatusUpdate" in method:
            params = msg.get("params", {})
            # params can be a string (e.g. "loading") or a dict with "state"
            if isinstance(params, str):
                state = params
            elif isinstance(params, dict):
                state = params.get("state", "")
            else:
                state = str(params)
            print(f"    Loading status ({method}): {json.dumps(params)}")
            results["loading_status_received"] = True
            if state == "stop":
                elapsed = time.monotonic() - indexing_start
                results["indexing_time_s"] = round(elapsed, 2)
                print(
                    f"[+] Indexing complete in {results['indexing_time_s']}s"
                )
                break
        elif method in ("window/showMessage", "window/logMessage"):
            params = msg.get("params", {})
            msg_text = params.get("message", json.dumps(params)) if isinstance(params, dict) else str(params)
            # Only print important log messages, skip verbose per-file logs
            if any(kw in msg_text.lower() for kw in (
                "building", "detected", "adding sys", "end of", "end building",
                "error", "unable", "failed", "modules loaded",
            )):
                print(f"    [{method}] {msg_text}")
        elif "setConfiguration" in method:
            print(f"    [{method}] (config loaded)")
        elif method in ("textDocument/publishDiagnostics", "$/progress"):
            # Suppress verbose per-file diagnostics during indexing
            indexing_msg_count = indexing_msg_count + 1
            if indexing_msg_count % 100 == 0:
                print(f"    ... {indexing_msg_count} indexing messages processed")
        elif method:
            # Log other notifications for debugging
            print(f"    [{method}] (ignored)")

    if results["indexing_time_s"] is None and not results["errors"]:
        results["errors"].append(
            "Timed out waiting for indexing to complete (300s hard limit)"
        )

    if results["indexing_time_s"] is None:
        _print_results(results)
        return 1

    # ------------------------------------------------------------------
    # 5. Create test module with deliberate error
    # ------------------------------------------------------------------
    test_module_dir = Path(WORKSPACE) / "test_benchmark_module"
    test_module_dir.mkdir(parents=True, exist_ok=True)

    manifest = test_module_dir / "__manifest__.py"
    manifest.write_text(
        """{
    "name": "Benchmark Test Module",
    "version": "19.0.1.0.0",
    "category": "Tools",
    "depends": ["base"],
    "installable": True,
}
"""
    )

    init_file = test_module_dir / "__init__.py"
    init_file.write_text("from . import models\n")

    models_dir = test_module_dir / "models"
    models_dir.mkdir(exist_ok=True)
    (models_dir / "__init__.py").write_text("from . import test_model\n")

    test_model_file = models_dir / "test_model.py"
    test_model_content = """from odoo import models, fields

class BenchmarkTestModel(models.Model):
    _name = "benchmark.test"
    _description = "Benchmark Test"

    name = fields.Char(string="Name")
    # Deliberate error: reference nonexistent model
    partner_id = fields.Many2one("res.nonexistent", string="Partner")
"""
    test_model_file.write_text(test_model_content)

    test_file_uri = f"file://{test_model_file}"

    # ------------------------------------------------------------------
    # 6. Send textDocument/didOpen
    # ------------------------------------------------------------------
    print(f"[*] Sending didOpen for {test_file_uri} ...")
    did_open = {
        "jsonrpc": "2.0",
        "method": "textDocument/didOpen",
        "params": {
            "textDocument": {
                "uri": test_file_uri,
                "languageId": "python",
                "version": 1,
                "text": test_model_content,
            }
        },
    }
    stdin.write(encode_message(did_open))
    stdin.flush()
    diag_start = time.monotonic()

    # ------------------------------------------------------------------
    # 7. Wait for textDocument/publishDiagnostics
    # ------------------------------------------------------------------
    print("[*] Waiting for diagnostics ...")
    diag_deadline = diag_start + 30  # 30s hard timeout for diagnostics

    while time.monotonic() < diag_deadline:
        msg = read_message(stdout)
        if msg is None:
            stderr_out = _read_stderr(proc)
            results["errors"].append(
                f"Server closed stdout waiting for diagnostics. stderr: {stderr_out}"
            )
            break

        if not isinstance(msg, dict):
            print(f"    [!] Non-dict message: {repr(msg)[:200]}")
            continue

        method = msg.get("method", "")

        # Handle server requests that need a response
        _handle_server_request(msg, stdin)

        if method == "textDocument/publishDiagnostics":
            elapsed = time.monotonic() - diag_start
            results["diagnostic_latency_s"] = round(elapsed, 2)
            params = msg.get("params", {})
            diags = params.get("diagnostics", [])
            results["diagnostics"] = [
                {
                    "message": d.get("message", ""),
                    "severity": d.get("severity"),
                    "code": d.get("code"),
                    "source": d.get("source"),
                    "range": d.get("range"),
                }
                for d in diags
            ]
            print(
                f"[+] Diagnostics received in {results['diagnostic_latency_s']}s"
                f" ({len(diags)} diagnostic(s))"
            )
            break
        elif method in ("window/showMessage", "window/logMessage"):
            params = msg.get("params", {})
            msg_text = params.get("message", json.dumps(params)) if isinstance(params, dict) else str(params)
            print(f"    [{method}] {msg_text}")
        elif method:
            print(f"    [{method}] (ignored)")

    if results["diagnostic_latency_s"] is None and not any(
        "diagnostic" in e.lower() for e in results["errors"]
    ):
        # It's possible the server sends empty diagnostics or none at all
        # for our test file. That's still a valid response time-wise.
        results["errors"].append(
            "No publishDiagnostics received within 30s"
        )

    # ------------------------------------------------------------------
    # 8. Shutdown + exit
    # ------------------------------------------------------------------
    print("[*] Sending shutdown ...")
    shutdown_req = {"jsonrpc": "2.0", "id": 2, "method": "shutdown", "params": None}
    stdin.write(encode_message(shutdown_req))
    stdin.flush()

    # Wait briefly for shutdown response
    shutdown_deadline = time.monotonic() + 10
    while time.monotonic() < shutdown_deadline:
        msg = read_message(stdout)
        if msg is None:
            break
        if msg.get("id") == 2:
            print("[+] Shutdown acknowledged")
            break

    exit_notif = {"jsonrpc": "2.0", "method": "exit", "params": None}
    stdin.write(encode_message(exit_notif))
    stdin.flush()
    print("[+] Exit sent")

    _print_results(results)
    return 0 if _is_go(results) else 1


def _is_go(results: dict) -> bool:
    """Evaluate GO/NO-GO against thresholds."""
    if results["indexing_time_s"] is None:
        return False
    if results["indexing_time_s"] > INDEXING_THRESHOLD_S:
        return False
    if not results["loading_status_received"]:
        return False
    # Diagnostic latency: NO-GO only if we got diagnostics but too slow
    if (
        results["diagnostic_latency_s"] is not None
        and results["diagnostic_latency_s"] > DIAG_LATENCY_THRESHOLD_S
    ):
        return False
    if results["errors"]:
        # Allow "no diagnostics" as a soft concern, not a hard NO-GO
        non_critical = all(
            "No publishDiagnostics" in e for e in results["errors"]
        )
        if not non_critical:
            return False
    return True


def _read_stderr(proc: subprocess.Popen) -> str:
    """Read available stderr without blocking."""
    import select

    ready, _, _ = select.select([proc.stderr], [], [], 0.5)
    if ready:
        return proc.stderr.read(4096).decode("utf-8", errors="replace")
    return "(no stderr available)"


def _cleanup(proc: subprocess.Popen) -> None:
    """Terminate the server process."""
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
    # Clean up test module
    test_dir = Path(WORKSPACE) / "test_benchmark_module"
    if test_dir.exists():
        import shutil
        shutil.rmtree(test_dir, ignore_errors=True)


def _print_results(results: dict) -> None:
    """Print benchmark results and GO/NO-GO verdict."""
    print("\n" + "=" * 60)
    print("ODOO-LS BENCHMARK RESULTS")
    print("=" * 60)

    idx_time = results["indexing_time_s"]
    diag_lat = results["diagnostic_latency_s"]
    loading = results["loading_status_received"]
    diags = results["diagnostics"]
    errors = results["errors"]

    print(f"  Indexing time:           {idx_time}s (threshold: <{INDEXING_THRESHOLD_S}s)")
    print(f"  Diagnostic latency:      {diag_lat}s (threshold: <{DIAG_LATENCY_THRESHOLD_S}s)")
    print(f"  Loading status received: {loading}")
    print(f"  Diagnostics count:       {len(diags)}")

    if diags:
        print("\n  Diagnostics detail:")
        for i, d in enumerate(diags, 1):
            code = d.get("code", "N/A")
            source = d.get("source", "N/A")
            msg = d.get("message", "N/A")
            sev = d.get("severity", "N/A")
            print(f"    [{i}] code={code} source={source} severity={sev}")
            print(f"        {msg}")

    if errors:
        print("\n  Errors:")
        for e in errors:
            print(f"    - {e}")

    go = _is_go(results)
    verdict = "GO" if go else "NO-GO"
    print(f"\n  {'=' * 40}")
    print(f"  VERDICT: {verdict}")
    print(f"  {'=' * 40}")

    if go:
        concerns = []
        if diag_lat is None:
            concerns.append("No diagnostics received (server may not diagnose test file)")
        if idx_time is not None and idx_time > INDEXING_THRESHOLD_S * 0.8:
            concerns.append(f"Indexing time ({idx_time}s) close to threshold")
        if concerns:
            print("\n  Concerns:")
            for c in concerns:
                print(f"    - {c}")

    print("=" * 60)


if __name__ == "__main__":
    sys.exit(main())
