"""
Unit tests for the sliding-window rate limiter.

Tests cover:
- Under-limit requests pass without raising
- At-limit raises 429
- Over-limit raises 429
- Requests older than 60 s are excluded from the window count
- Cleanup logic removes stale IP entries
"""

from __future__ import annotations

import time
from typing import Dict, List

import pytest
from fastapi import HTTPException

# ---------------------------------------------------------------------------
# Inline replica of check_rate_limit() logic for isolated testing.
# We test the algorithm without importing app.py (which requires env vars).
# ---------------------------------------------------------------------------

_RATE_LIMIT = 5  # requests per minute (small for test speed)
_CLEANUP_INTERVAL = 300  # seconds


def check_rate_limit(
    client_ip: str,
    rate_store: Dict[str, List[float]],
    last_cleanup_ref: list,  # mutable single-element list so we can update it
    now: float,
    rate_limit: int = _RATE_LIMIT,
) -> None:
    """Raise HTTPException(429) when client exceeds rate_limit per 60-second window."""
    if now - last_cleanup_ref[0] > _CLEANUP_INTERVAL:
        cutoff = now - 60
        cleaned = {
            k: [t for t in v if t > cutoff]
            for k, v in rate_store.items()
            if any(t > cutoff for t in v)
        }
        rate_store.clear()
        rate_store.update(cleaned)
        last_cleanup_ref[0] = now

    hits = [t for t in rate_store.get(client_ip, []) if now - t < 60.0]
    if len(hits) >= rate_limit:
        raise HTTPException(429, f"Rate limit: max {rate_limit}/min.")
    hits.append(now)
    rate_store[client_ip] = hits


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _make_state():
    rate_store: Dict[str, List[float]] = {}
    last_cleanup = [time.time()]
    return rate_store, last_cleanup


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRateLimiter:
    def test_single_request_passes(self):
        store, lc = _make_state()
        # Should not raise
        check_rate_limit("1.2.3.4", store, lc, now=1_000.0)

    def test_under_limit_passes(self):
        store, lc = _make_state()
        for i in range(_RATE_LIMIT - 1):
            check_rate_limit("1.2.3.4", store, lc, now=1_000.0 + i)
        # One more under the cap — still fine
        check_rate_limit("1.2.3.4", store, lc, now=1_000.0 + _RATE_LIMIT - 1)

    def test_at_limit_raises_429(self):
        store, lc = _make_state()
        for i in range(_RATE_LIMIT):
            check_rate_limit("1.2.3.4", store, lc, now=1_000.0 + i * 0.1)
        with pytest.raises(HTTPException) as exc_info:
            check_rate_limit("1.2.3.4", store, lc, now=1_000.0 + _RATE_LIMIT * 0.1)
        assert exc_info.value.status_code == 429

    def test_over_limit_raises_429(self):
        store, lc = _make_state()
        # Saturate the window
        for i in range(_RATE_LIMIT):
            try:
                check_rate_limit("9.9.9.9", store, lc, now=2_000.0 + i)
            except HTTPException:
                pass
        with pytest.raises(HTTPException) as exc_info:
            check_rate_limit("9.9.9.9", store, lc, now=2_000.0 + _RATE_LIMIT + 1)
        assert exc_info.value.status_code == 429

    def test_different_ips_tracked_independently(self):
        store, lc = _make_state()
        # Saturate ip_a
        for i in range(_RATE_LIMIT):
            check_rate_limit("10.0.0.1", store, lc, now=3_000.0 + i * 0.1)
        # ip_b should still pass
        check_rate_limit("10.0.0.2", store, lc, now=3_000.0)

    def test_requests_older_than_60s_excluded(self):
        store, lc = _make_state()
        # Fill window at t=0
        for i in range(_RATE_LIMIT):
            try:
                check_rate_limit("5.5.5.5", store, lc, now=float(i))
            except HTTPException:
                pass
        # 61 seconds later — old hits should be outside the 60-s window
        check_rate_limit("5.5.5.5", store, lc, now=61.0)

    def test_cleanup_removes_stale_ips(self):
        store, lc = _make_state()
        # Make a request from ip_old at t=0 and set last_cleanup to well in the past
        store["192.168.0.1"] = [0.0]
        lc[0] = 0.0
        # Trigger cleanup at t = 600 (> CLEANUP_INTERVAL=300, entries older than 60s cleared)
        check_rate_limit("192.168.0.2", store, lc, now=600.0)
        # ip_old's single entry at t=0 is now > 60s old → removed
        assert "192.168.0.1" not in store
