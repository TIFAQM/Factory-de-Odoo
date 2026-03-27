#!/usr/bin/env python3
"""Context Monitor — PostToolUse/AfterTool hook.

Reads context metrics from the statusline bridge file and injects
warnings when context usage is high. This makes the AGENT aware of
context limits (the statusline only shows the user).

Port of amil-context-monitor.js.

Thresholds:
  WARNING  (remaining <= 35%): Agent should wrap up current task
  CRITICAL (remaining <= 25%): Agent should stop immediately and save state

Debounce: 5 tool uses between warnings to avoid spam
Severity escalation bypasses debounce (WARNING -> CRITICAL fires immediately)
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

WARNING_THRESHOLD = 35   # remaining_percentage <= 35%
CRITICAL_THRESHOLD = 25  # remaining_percentage <= 25%
STALE_SECONDS = 60       # ignore metrics older than 60s
DEBOUNCE_CALLS = 5       # min tool uses between warnings


def main() -> None:
    raw = sys.stdin.read()
    if not raw.strip():
        return

    data = json.loads(raw)
    session_id = data.get("session_id")

    if not session_id:
        return

    tmpdir = tempfile.gettempdir()
    metrics_path = Path(tmpdir) / f"claude-ctx-{session_id}.json"

    # If no metrics file, this is a subagent or fresh session
    if not metrics_path.exists():
        return

    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    now = int(time.time())

    # Ignore stale metrics
    if metrics.get("timestamp") and (now - metrics["timestamp"]) > STALE_SECONDS:
        return

    remaining = metrics.get("remaining_percentage", 100)
    used_pct = metrics.get("used_pct", 0)

    # No warning needed
    if remaining > WARNING_THRESHOLD:
        return

    # Debounce: check if we warned recently
    warn_path = Path(tmpdir) / f"claude-ctx-{session_id}-warned.json"
    warn_data = {"callsSinceWarn": 0, "lastLevel": None}
    first_warn = True

    if warn_path.exists():
        try:
            warn_data = json.loads(warn_path.read_text(encoding="utf-8"))
            first_warn = False
        except (OSError, json.JSONDecodeError):
            pass  # Corrupted file, reset

    warn_data["callsSinceWarn"] = (warn_data.get("callsSinceWarn") or 0) + 1

    is_critical = remaining <= CRITICAL_THRESHOLD
    current_level = "critical" if is_critical else "warning"

    # Emit immediately on first warning, then debounce subsequent ones
    # Severity escalation (WARNING -> CRITICAL) bypasses debounce
    severity_escalated = current_level == "critical" and warn_data.get("lastLevel") == "warning"
    if not first_warn and warn_data["callsSinceWarn"] < DEBOUNCE_CALLS and not severity_escalated:
        # Update counter and exit without warning
        warn_path.write_text(json.dumps(warn_data), encoding="utf-8")
        return

    # Reset debounce counter
    warn_data["callsSinceWarn"] = 0
    warn_data["lastLevel"] = current_level
    warn_path.write_text(json.dumps(warn_data), encoding="utf-8")

    # Detect if Amil is active (has .planning/STATE.md in working directory)
    cwd = data.get("cwd") or os.getcwd()
    is_amil_active = (Path(cwd) / ".planning" / "STATE.md").exists()

    # Build advisory warning message
    if is_critical:
        if is_amil_active:
            message = (
                f"CONTEXT CRITICAL: Usage at {used_pct}%. Remaining: {remaining}%. "
                "Context is nearly exhausted. Do NOT start new complex work or write handoff files — "
                "Amil state is already tracked in STATE.md. Inform the user so they can run "
                "/amil:pause-work at the next natural stopping point."
            )
        else:
            message = (
                f"CONTEXT CRITICAL: Usage at {used_pct}%. Remaining: {remaining}%. "
                "Context is nearly exhausted. Inform the user that context is low and ask how they "
                "want to proceed. Do NOT autonomously save state or write handoff files unless the user asks."
            )
    else:
        if is_amil_active:
            message = (
                f"CONTEXT WARNING: Usage at {used_pct}%. Remaining: {remaining}%. "
                "Context is getting limited. Avoid starting new complex work. If not between "
                "defined plan steps, inform the user so they can prepare to pause."
            )
        else:
            message = (
                f"CONTEXT WARNING: Usage at {used_pct}%. Remaining: {remaining}%. "
                "Be aware that context is getting limited. Avoid unnecessary exploration or "
                "starting new complex work."
            )

    # Detect hook event name based on environment
    hook_event = os.environ.get("AMIL_HOOK_EVENT", "PostToolUse")

    output = {
        "hookSpecificOutput": {
            "hookEventName": hook_event,
            "additionalContext": message,
        }
    }

    sys.stdout.write(json.dumps(output))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Silent fail — never block tool execution
        sys.exit(0)
