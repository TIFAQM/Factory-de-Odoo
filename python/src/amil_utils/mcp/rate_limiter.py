"""Sliding-window rate limiter for the Odoo MCP server (CWE-400).

Provides per-tool and global request limiting to prevent denial-of-service
via excessive MCP tool calls. Thread-safe via threading.Lock.
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict
from typing import Deque
from collections import deque


class RateLimiter:
    """Sliding-window rate limiter with per-tool and optional global limits.

    Args:
        max_requests: Maximum requests allowed per tool within window_seconds.
        window_seconds: Duration of the sliding window in seconds.
        global_max: Optional global maximum across all tools within the window.
                    If None, no global limit is enforced.

    Thread safety: All public methods are protected by a single threading.Lock.
    """

    def __init__(
        self,
        max_requests: int,
        window_seconds: float,
        global_max: int | None = None,
    ) -> None:
        if max_requests < 1:
            raise ValueError("max_requests must be >= 1")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be > 0")
        if global_max is not None and global_max < 1:
            raise ValueError("global_max must be >= 1 when specified")

        self._max_requests = max_requests
        self._window_seconds = window_seconds
        self._global_max = global_max

        # Per-tool timestamp queues
        self._tool_timestamps: dict[str, Deque[float]] = defaultdict(deque)
        # Global timestamp queue (all calls across all tools)
        self._global_timestamps: Deque[float] = deque()
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Internal helpers (must be called with lock held)
    # ------------------------------------------------------------------

    def _purge_expired(self, timestamps: Deque[float], now: float) -> None:
        """Remove timestamps that fall outside the current sliding window."""
        cutoff = now - self._window_seconds
        while timestamps and timestamps[0] <= cutoff:
            timestamps.popleft()

    def _global_count(self, now: float) -> int:
        """Return count of global calls within the window (lock must be held)."""
        self._purge_expired(self._global_timestamps, now)
        return len(self._global_timestamps)

    def _tool_count(self, tool_name: str, now: float) -> int:
        """Return count of calls for tool_name within the window (lock must be held)."""
        self._purge_expired(self._tool_timestamps[tool_name], now)
        return len(self._tool_timestamps[tool_name])

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def allow(self, tool_name: str) -> bool:
        """Check whether a call to tool_name is allowed and record it if so.

        Args:
            tool_name: Identifier of the tool being called.

        Returns:
            True if the call is within limits and has been recorded.
            False if either the per-tool or global limit would be exceeded.
        """
        now = time.monotonic()
        with self._lock:
            tool_count = self._tool_count(tool_name, now)
            if tool_count >= self._max_requests:
                return False

            if self._global_max is not None:
                global_count = self._global_count(now)
                if global_count >= self._global_max:
                    return False
            else:
                # Still need to purge so the deque doesn't grow unbounded
                self._purge_expired(self._global_timestamps, now)

            # Record the call
            self._tool_timestamps[tool_name].append(now)
            self._global_timestamps.append(now)
            return True

    def remaining(self, tool_name: str) -> int:
        """Return the number of remaining requests allowed for tool_name.

        The value reflects the per-tool headroom only (not global headroom).
        A zero value means the tool is currently rate-limited.

        Args:
            tool_name: Identifier of the tool to query.

        Returns:
            Non-negative integer: max_requests - current_window_count.
        """
        now = time.monotonic()
        with self._lock:
            count = self._tool_count(tool_name, now)
            return max(0, self._max_requests - count)
