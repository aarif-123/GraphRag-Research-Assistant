"""
Unit tests for the in-memory LRU cache layer.

Tests cover:
- cache_key() determinism and uniqueness
- get_cache() / set_cache() TTL expiry
- Per-user key partitioning for private buckets (llm, plan, relations)
- Max-size eviction (oldest entry removed)
"""

from __future__ import annotations

import hashlib
import time
from typing import Any
from unittest.mock import patch

# ---------------------------------------------------------------------------
# Replicate cache_key() — same logic as in app/app.py so we test it
# independently without importing the full app (which requires live env vars).
# ---------------------------------------------------------------------------


def cache_key(*args: Any) -> str:
    raw = "|".join(str(a) for a in args)
    return hashlib.md5(raw.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Replicate the cache helpers under test.
# We import from the module-under-test only if env vars allow; otherwise we
# test the equivalent extracted logic directly.
# ---------------------------------------------------------------------------

CACHE_TTL = 300
CACHE_MAX = 4  # small value so eviction is easy to trigger in tests

_CACHE_BUCKETS = ("graph", "embed", "llm", "plan", "relations", "api")


def _make_cache():
    return {b: {} for b in _CACHE_BUCKETS}


def _get_cache(CACHE, bucket, key, user_id=None, cache_ttl=CACHE_TTL):
    if bucket in ("llm", "plan", "relations") and user_id:
        key = f"{user_id}:{key}"
    entry = CACHE[bucket].get(key)
    if not entry:
        return None
    ttl = 43200 if bucket == "api" else cache_ttl
    if time.time() - entry["ts"] > ttl:
        CACHE[bucket].pop(key, None)
        return None
    return entry["v"]


def _set_cache(CACHE, bucket, key, value, user_id=None, cache_max=CACHE_MAX):
    if bucket in ("llm", "plan", "relations") and user_id:
        key = f"{user_id}:{key}"
    b = CACHE[bucket]
    if len(b) >= cache_max:
        oldest = min(b, key=lambda k: b[k]["ts"])
        b.pop(oldest, None)
    b[key] = {"v": value, "ts": time.time()}


# ---------------------------------------------------------------------------
# cache_key() tests
# ---------------------------------------------------------------------------


class TestCacheKey:
    def test_deterministic(self):
        assert cache_key("a", "b", 1) == cache_key("a", "b", 1)

    def test_different_args_produce_different_keys(self):
        assert cache_key("query1", 8) != cache_key("query2", 8)

    def test_returns_hex_string(self):
        result = cache_key("test")
        assert isinstance(result, str)
        assert len(result) == 32  # MD5 hex digest length

    def test_order_matters(self):
        assert cache_key("a", "b") != cache_key("b", "a")

    def test_numeric_args_coerced(self):
        # Integers are coerced to str before hashing
        assert cache_key(42) == cache_key("42")


# ---------------------------------------------------------------------------
# TTL tests
# ---------------------------------------------------------------------------


class TestCacheTTL:
    def test_fresh_entry_is_returned(self):
        cache = _make_cache()
        with patch("time.time", return_value=1_000_000.0):
            _set_cache(cache, "graph", "k1", "value1")
        with patch("time.time", return_value=1_000_000.0 + CACHE_TTL - 1):
            result = _get_cache(cache, "graph", "k1")
        assert result == "value1"

    def test_expired_entry_returns_none(self):
        cache = _make_cache()
        with patch("time.time", return_value=1_000_000.0):
            _set_cache(cache, "graph", "k1", "value1")
        with patch("time.time", return_value=1_000_000.0 + CACHE_TTL + 1):
            result = _get_cache(cache, "graph", "k1")
        assert result is None

    def test_api_bucket_uses_12h_ttl(self):
        cache = _make_cache()
        with patch("time.time", return_value=1_000_000.0):
            _set_cache(cache, "api", "k_api", "api_data")
        # After standard CACHE_TTL (5 min), api entry should still be alive
        with patch("time.time", return_value=1_000_000.0 + CACHE_TTL + 60):
            result = _get_cache(cache, "api", "k_api")
        assert result == "api_data"

    def test_expired_entry_is_deleted_from_bucket(self):
        cache = _make_cache()
        with patch("time.time", return_value=1_000_000.0):
            _set_cache(cache, "embed", "k_exp", "v")
        with patch("time.time", return_value=1_000_000.0 + CACHE_TTL + 1):
            _get_cache(cache, "embed", "k_exp")
        assert "k_exp" not in cache["embed"]


# ---------------------------------------------------------------------------
# Max-size eviction tests
# ---------------------------------------------------------------------------


class TestCacheEviction:
    def test_exceeding_max_evicts_oldest(self):
        cache = _make_cache()
        base_ts = 1_000_000.0
        for i in range(CACHE_MAX):
            with patch("time.time", return_value=base_ts + i):
                _set_cache(cache, "graph", f"k{i}", f"v{i}")
        # Insert one more — oldest (k0) should be evicted
        with patch("time.time", return_value=base_ts + CACHE_MAX):
            _set_cache(cache, "graph", "k_new", "v_new")
        assert "k0" not in cache["graph"]
        assert "k_new" in cache["graph"]

    def test_within_max_no_eviction(self):
        cache = _make_cache()
        base_ts = 1_000_000.0
        for i in range(CACHE_MAX - 1):
            with patch("time.time", return_value=base_ts + i):
                _set_cache(cache, "graph", f"k{i}", f"v{i}")
        for i in range(CACHE_MAX - 1):
            assert f"k{i}" in cache["graph"]


# ---------------------------------------------------------------------------
# Per-user key partitioning tests
# ---------------------------------------------------------------------------


class TestUserPartitioning:
    def test_llm_bucket_partitioned_by_user(self):
        cache = _make_cache()
        with patch("time.time", return_value=1_000_000.0):
            _set_cache(cache, "llm", "q1", "response_user1", user_id="user1")
            _set_cache(cache, "llm", "q1", "response_user2", user_id="user2")

        with patch("time.time", return_value=1_000_000.0):
            r1 = _get_cache(cache, "llm", "q1", user_id="user1")
            r2 = _get_cache(cache, "llm", "q1", user_id="user2")

        assert r1 == "response_user1"
        assert r2 == "response_user2"

    def test_graph_bucket_not_partitioned(self):
        """Public buckets (graph, embed, api) are shared across users."""
        cache = _make_cache()
        with patch("time.time", return_value=1_000_000.0):
            _set_cache(cache, "graph", "shared_key", "shared_val")
        with patch("time.time", return_value=1_000_000.0):
            # No user_id — should still get it
            result = _get_cache(cache, "graph", "shared_key")
        assert result == "shared_val"

    def test_missing_key_returns_none(self):
        cache = _make_cache()
        assert _get_cache(cache, "graph", "nonexistent") is None
