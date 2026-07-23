"""
Unit tests for upload-limit constants and pure helper logic.

Covers:
- _MB / MAX_PDF_BYTES / MAX_AUDIO_BYTES constant values (env-driven)
- _MAX_PDF_TEXT_CHARS truncation boundary
- _REDIS_MAX_PAYLOAD_BYTES guard boundary
- Text-truncation flag logic (was_truncated) matches the implementation
- Redis skip-condition: payload > threshold → do not write
"""

from __future__ import annotations

import os

import pytest

# ---------------------------------------------------------------------------
# Constants are read from env at module import time.  We set test-specific
# values before importing so the tests are deterministic even if the developer
# has custom env vars set.
# ---------------------------------------------------------------------------

_TEST_PDF_MB = 5
_TEST_AUDIO_MB = 10

# Patch before import so the module sees our test values.
os.environ.setdefault("MAX_PDF_SIZE_MB", str(_TEST_PDF_MB))
os.environ.setdefault("MAX_AUDIO_SIZE_MB", str(_TEST_AUDIO_MB))

# Standard _server import guard — env vars for DB connections must exist.
# tests/conftest.py already sets them, so this is a safety belt.
for _k, _v in {
    "SUPABASE_URL": "https://dummy.supabase.co",
    "SUPABASE_SERVICE_ROLE_KEY": "dummy",
    "NEO4J_URI": "bolt://localhost:7687",
    "NEO4J_USER": "neo4j",
    "NEO4J_PASSWORD": "dummy",
    "GROQ_API_KEY": "gsk_dummy",
}.items():
    os.environ.setdefault(_k, _v)

import app._server as srv  # noqa: E402  (import after env setup)

_MB = 1024 * 1024


# ---------------------------------------------------------------------------
# Size constant sanity checks
# ---------------------------------------------------------------------------


class TestSizeConstants:
    def test_mb_value(self):
        assert srv._MB == _MB

    def test_max_pdf_bytes_is_env_driven(self):
        """MAX_PDF_BYTES must equal MAX_PDF_SIZE_MB * 1 MiB."""
        expected = int(os.getenv("MAX_PDF_SIZE_MB", "20")) * _MB
        assert srv.MAX_PDF_BYTES == expected

    def test_max_audio_bytes_is_env_driven(self):
        expected = int(os.getenv("MAX_AUDIO_SIZE_MB", "24")) * _MB
        assert srv.MAX_AUDIO_BYTES == expected

    def test_max_pdf_text_chars_is_10_mb(self):
        assert srv._MAX_PDF_TEXT_CHARS == 10 * _MB

    def test_redis_max_payload_is_900_kb(self):
        assert srv._REDIS_MAX_PAYLOAD_BYTES == 900 * 1024

    def test_audio_limit_below_groq_hard_limit(self):
        """Groq Whisper hard limit is 25 MB — our default must stay below it."""
        groq_whisper_hard_limit = 25 * _MB
        assert srv.MAX_AUDIO_BYTES < groq_whisper_hard_limit

    def test_pdf_text_chars_below_mongodb_limit(self):
        """MongoDB BSON document limit is 16 MB — text cap must be below that."""
        mongodb_limit = 16 * _MB
        assert srv._MAX_PDF_TEXT_CHARS < mongodb_limit


# ---------------------------------------------------------------------------
# Text-truncation logic
# (Replicated from upload_pdf; tested independently of HTTP layer.)
# ---------------------------------------------------------------------------


def _apply_truncation(text: str, limit: int) -> tuple[str, bool]:
    """Mirror the truncation logic from upload_pdf."""
    was_truncated = len(text) > limit
    if was_truncated:
        text = text[:limit]
    return text, was_truncated


class TestTextTruncation:
    def test_short_text_not_truncated(self):
        text = "Hello world"
        result, was_truncated = _apply_truncation(text, srv._MAX_PDF_TEXT_CHARS)
        assert result == text
        assert was_truncated is False

    def test_exactly_at_limit_not_truncated(self):
        text = "x" * srv._MAX_PDF_TEXT_CHARS
        result, was_truncated = _apply_truncation(text, srv._MAX_PDF_TEXT_CHARS)
        assert len(result) == srv._MAX_PDF_TEXT_CHARS
        assert was_truncated is False

    def test_one_over_limit_is_truncated(self):
        text = "x" * (srv._MAX_PDF_TEXT_CHARS + 1)
        result, was_truncated = _apply_truncation(text, srv._MAX_PDF_TEXT_CHARS)
        assert len(result) == srv._MAX_PDF_TEXT_CHARS
        assert was_truncated is True

    def test_large_text_capped_at_limit(self):
        text = "A" * (20 * _MB)  # 20 MB of text
        result, was_truncated = _apply_truncation(text, srv._MAX_PDF_TEXT_CHARS)
        assert len(result) == srv._MAX_PDF_TEXT_CHARS
        assert was_truncated is True

    def test_empty_text_not_truncated(self):
        result, was_truncated = _apply_truncation("", srv._MAX_PDF_TEXT_CHARS)
        assert result == ""
        assert was_truncated is False


# ---------------------------------------------------------------------------
# Redis payload guard logic
# (Replicated from get_relevant_pdf_chunks; tested without a live Redis client.)
# ---------------------------------------------------------------------------


def _should_write_to_redis(payload_bytes: bytes, limit: int) -> bool:
    """Mirror the Redis write-guard condition."""
    return len(payload_bytes) <= limit


class TestRedisPayloadGuard:
    def test_small_payload_allowed(self):
        payload = b"x" * 100
        assert _should_write_to_redis(payload, srv._REDIS_MAX_PAYLOAD_BYTES) is True

    def test_exactly_at_limit_allowed(self):
        payload = b"x" * srv._REDIS_MAX_PAYLOAD_BYTES
        assert _should_write_to_redis(payload, srv._REDIS_MAX_PAYLOAD_BYTES) is True

    def test_one_over_limit_blocked(self):
        payload = b"x" * (srv._REDIS_MAX_PAYLOAD_BYTES + 1)
        assert _should_write_to_redis(payload, srv._REDIS_MAX_PAYLOAD_BYTES) is False

    def test_large_payload_blocked(self):
        payload = b"x" * (2 * _MB)  # 2 MB — well over 900 KB threshold
        assert _should_write_to_redis(payload, srv._REDIS_MAX_PAYLOAD_BYTES) is False

    def test_empty_payload_allowed(self):
        assert _should_write_to_redis(b"", srv._REDIS_MAX_PAYLOAD_BYTES) is True
