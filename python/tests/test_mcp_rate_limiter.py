"""Tests for the sliding-window RateLimiter (CWE-400 mitigation).

Covers: under-limit allowed, over-limit blocked, per-tool isolation,
window expiry, global limit, and remaining count.
"""
from __future__ import annotations

import time

import pytest

from amil_utils.mcp.rate_limiter import RateLimiter


# ---------------------------------------------------------------------------
# Constructor validation
# ---------------------------------------------------------------------------


class TestRateLimiterInit:
    def test_valid_construction(self):
        rl = RateLimiter(max_requests=10, window_seconds=60)
        assert rl._max_requests == 10
        assert rl._window_seconds == 60
        assert rl._global_max is None

    def test_valid_construction_with_global_max(self):
        rl = RateLimiter(max_requests=10, window_seconds=60, global_max=50)
        assert rl._global_max == 50

    def test_invalid_max_requests_raises(self):
        with pytest.raises(ValueError, match="max_requests"):
            RateLimiter(max_requests=0, window_seconds=60)

    def test_invalid_window_seconds_raises(self):
        with pytest.raises(ValueError, match="window_seconds"):
            RateLimiter(max_requests=10, window_seconds=0)

    def test_negative_window_seconds_raises(self):
        with pytest.raises(ValueError, match="window_seconds"):
            RateLimiter(max_requests=10, window_seconds=-1)

    def test_invalid_global_max_raises(self):
        with pytest.raises(ValueError, match="global_max"):
            RateLimiter(max_requests=10, window_seconds=60, global_max=0)


# ---------------------------------------------------------------------------
# Under-limit: requests should be allowed
# ---------------------------------------------------------------------------


class TestUnderLimit:
    def test_single_request_allowed(self):
        rl = RateLimiter(max_requests=5, window_seconds=60)
        assert rl.allow("check_connection") is True

    def test_all_requests_under_limit_allowed(self):
        rl = RateLimiter(max_requests=3, window_seconds=60)
        tool = "list_models"
        results = [rl.allow(tool) for _ in range(3)]
        assert all(results), "All 3 requests should be allowed within the limit"

    def test_exact_limit_boundary_allowed(self):
        rl = RateLimiter(max_requests=1, window_seconds=60)
        assert rl.allow("get_model_fields") is True


# ---------------------------------------------------------------------------
# Over-limit: requests should be blocked
# ---------------------------------------------------------------------------


class TestOverLimit:
    def test_request_over_limit_blocked(self):
        rl = RateLimiter(max_requests=2, window_seconds=60)
        tool = "list_installed_modules"
        rl.allow(tool)
        rl.allow(tool)
        # Third call should be blocked
        assert rl.allow(tool) is False

    def test_many_excess_requests_all_blocked(self):
        rl = RateLimiter(max_requests=1, window_seconds=60)
        tool = "get_view_arch"
        rl.allow(tool)  # uses up the quota
        blocked = [rl.allow(tool) for _ in range(5)]
        assert all(b is False for b in blocked), "All excess calls must be blocked"


# ---------------------------------------------------------------------------
# Per-tool isolation: limits are independent per tool
# ---------------------------------------------------------------------------


class TestPerToolIsolation:
    def test_tools_do_not_share_quota(self):
        rl = RateLimiter(max_requests=1, window_seconds=60)
        # Exhaust quota for tool_a
        assert rl.allow("tool_a") is True
        assert rl.allow("tool_a") is False
        # tool_b still has its own fresh quota
        assert rl.allow("tool_b") is True

    def test_multiple_tools_independent(self):
        rl = RateLimiter(max_requests=2, window_seconds=60)
        tools = ["check_connection", "list_models", "get_model_fields"]
        for tool in tools:
            assert rl.allow(tool) is True
            assert rl.allow(tool) is True
            assert rl.allow(tool) is False  # 3rd call blocked per tool

    def test_new_tool_always_starts_fresh(self):
        rl = RateLimiter(max_requests=3, window_seconds=60)
        # Saturate a known tool
        for _ in range(3):
            rl.allow("existing_tool")
        # A brand-new tool should have full quota
        assert rl.allow("brand_new_tool") is True


# ---------------------------------------------------------------------------
# Window expiry: blocked calls become allowed after window passes
# ---------------------------------------------------------------------------


class TestWindowExpiry:
    def test_expired_window_resets_quota(self):
        """Requests blocked at t=0 should be allowed after 0.1 s window expires."""
        rl = RateLimiter(max_requests=1, window_seconds=0.1)
        tool = "find_field_conflicts"
        assert rl.allow(tool) is True
        assert rl.allow(tool) is False  # blocked within window

        time.sleep(0.15)  # wait for window to expire

        assert rl.allow(tool) is True, "Should be allowed after window expiry"

    def test_partial_window_still_blocked(self):
        """Requests should stay blocked before the window expires."""
        rl = RateLimiter(max_requests=1, window_seconds=0.5)
        tool = "get_model_relations"
        rl.allow(tool)  # consume quota
        time.sleep(0.05)  # not enough time for window to expire
        assert rl.allow(tool) is False

    def test_sliding_window_replenishes_gradually(self):
        """Old requests expire individually allowing new ones after each interval."""
        rl = RateLimiter(max_requests=2, window_seconds=0.1)
        tool = "check_module_dependency"

        # Fill the quota
        rl.allow(tool)
        time.sleep(0.06)
        rl.allow(tool)

        # Both still in window — should be blocked
        assert rl.allow(tool) is False

        # Wait for first timestamp to expire
        time.sleep(0.06)  # total ~0.12s — first call should be outside window
        assert rl.allow(tool) is True, "First slot should have expired"


# ---------------------------------------------------------------------------
# Global limit: cross-tool cap enforced
# ---------------------------------------------------------------------------


class TestGlobalLimit:
    def test_global_limit_blocks_all_tools(self):
        """Once global_max is reached, all tools should be blocked."""
        rl = RateLimiter(max_requests=10, window_seconds=60, global_max=3)
        # Use 3 different tools, 1 call each → global limit reached
        assert rl.allow("tool_a") is True
        assert rl.allow("tool_b") is True
        assert rl.allow("tool_c") is True
        # 4th call on any tool should be blocked by global cap
        assert rl.allow("tool_a") is False
        assert rl.allow("tool_d") is False  # new tool, but global cap hit

    def test_global_limit_not_exceeded_below_cap(self):
        """Calls under the global cap should all succeed."""
        rl = RateLimiter(max_requests=10, window_seconds=60, global_max=5)
        results = [rl.allow(f"tool_{i}") for i in range(5)]
        assert all(results)

    def test_global_limit_independent_of_per_tool_limit(self):
        """Global and per-tool limits enforce whichever is more restrictive."""
        rl = RateLimiter(max_requests=2, window_seconds=60, global_max=3)
        # Per-tool limit is 2
        assert rl.allow("tool_a") is True
        assert rl.allow("tool_a") is True
        assert rl.allow("tool_a") is False  # per-tool limit hit
        # Global still has 1 slot left (2 used so far)
        assert rl.allow("tool_b") is True
        # Now global is at 3 — all further calls blocked
        assert rl.allow("tool_b") is False
        assert rl.allow("tool_c") is False

    def test_no_global_limit_allows_cross_tool_calls(self):
        """Without global_max, many different tools can each use their full quota."""
        rl = RateLimiter(max_requests=1, window_seconds=60)  # no global_max
        tools = [f"tool_{i}" for i in range(50)]
        results = [rl.allow(t) for t in tools]
        assert all(results), "Without global limit, each tool gets its own quota"

    def test_global_limit_expires_with_window(self):
        """Global limit should reset after the window expires."""
        rl = RateLimiter(max_requests=5, window_seconds=0.1, global_max=2)
        assert rl.allow("tool_a") is True
        assert rl.allow("tool_b") is True
        assert rl.allow("tool_c") is False  # global cap hit

        time.sleep(0.15)  # wait for window to expire

        assert rl.allow("tool_a") is True, "Should be allowed after global window expires"


# ---------------------------------------------------------------------------
# Remaining count
# ---------------------------------------------------------------------------


class TestRemainingCount:
    def test_remaining_at_full_quota(self):
        rl = RateLimiter(max_requests=5, window_seconds=60)
        assert rl.remaining("my_tool") == 5

    def test_remaining_decreases_with_each_call(self):
        rl = RateLimiter(max_requests=5, window_seconds=60)
        tool = "check_connection"
        for expected in range(5, 0, -1):
            assert rl.remaining(tool) == expected
            rl.allow(tool)
        assert rl.remaining(tool) == 0

    def test_remaining_is_zero_when_blocked(self):
        rl = RateLimiter(max_requests=2, window_seconds=60)
        tool = "list_models"
        rl.allow(tool)
        rl.allow(tool)
        assert rl.remaining(tool) == 0

    def test_remaining_never_goes_negative(self):
        """remaining() must return 0 even after many excess blocked calls."""
        rl = RateLimiter(max_requests=1, window_seconds=60)
        tool = "get_view_arch"
        rl.allow(tool)
        # Try to over-call (all blocked)
        for _ in range(10):
            rl.allow(tool)
        assert rl.remaining(tool) == 0

    def test_remaining_is_per_tool(self):
        rl = RateLimiter(max_requests=3, window_seconds=60)
        rl.allow("tool_a")
        rl.allow("tool_a")
        # tool_a has 1 remaining, tool_b still has 3
        assert rl.remaining("tool_a") == 1
        assert rl.remaining("tool_b") == 3

    def test_remaining_resets_after_window_expiry(self):
        rl = RateLimiter(max_requests=1, window_seconds=0.1)
        tool = "get_model_fields"
        rl.allow(tool)
        assert rl.remaining(tool) == 0
        time.sleep(0.15)
        assert rl.remaining(tool) == 1, "Remaining should reset after window expires"


# ---------------------------------------------------------------------------
# Thread safety (basic smoke test)
# ---------------------------------------------------------------------------


class TestThreadSafety:
    def test_concurrent_calls_respect_limit(self):
        """Total allowed calls from concurrent threads must not exceed max_requests."""
        import threading

        max_req = 10
        rl = RateLimiter(max_requests=max_req, window_seconds=60)
        tool = "concurrent_tool"
        allowed_count = 0
        lock = threading.Lock()

        def call():
            nonlocal allowed_count
            if rl.allow(tool):
                with lock:
                    allowed_count += 1

        threads = [threading.Thread(target=call) for _ in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert allowed_count == max_req, (
            f"Expected exactly {max_req} allowed calls, got {allowed_count}"
        )
