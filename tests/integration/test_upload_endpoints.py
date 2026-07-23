"""
Integration tests for the PDF upload and audio transcription endpoints.

All external I/O is mocked:
  - fitz (PyMuPDF)         → replaced with a MagicMock so no binary is required
  - MongoDB (db)           → replaced with MagicMock
  - Groq HTTP client       → replaced with AsyncMock
  - asyncio.to_thread      → patched to run the callable synchronously

No real network calls, no database connections, no API keys needed.

Covers:
  POST /api/upload/pdf
    - Happy path: valid PDF returns file_id, url, text_length, truncated=False
    - Content-Length header pre-check: returns 413 before body is read
    - Body size guard: returns 413 when Content-Length absent but body is oversized
    - Non-PDF extension: returns 400
    - Empty file: returns 400
    - fitz unavailable (None): returns 500
    - No extracted text: returns 400
    - Oversized text is truncated: response has truncated=True
    - Internal error: returns 500

  POST /api/audio/transcribe
    - Happy path: valid Groq 200 → returns transcribed text
    - Content-Length pre-check: returns 413 before body is read
    - Body size guard: returns 413 when Content-Length absent but body is oversized
    - Empty audio: returns 400
    - Groq 429 key rotation and eventual success
    - All keys exhausted: returns 500
    - Groq returns Whisper hallucination: text is empty string
"""

from __future__ import annotations

import io
import os
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from httpx import Headers

# ---------------------------------------------------------------------------
# Ensure dummy env vars are set before importing the app so DB connections
# are stubbed at module-load time.
# ---------------------------------------------------------------------------
for _k, _v in {
    "SUPABASE_URL": "https://dummy.supabase.co",
    "SUPABASE_SERVICE_ROLE_KEY": "dummy_service_role_key",
    "NEO4J_URI": "bolt://localhost:7687",
    "NEO4J_USER": "neo4j",
    "NEO4J_PASSWORD": "dummy",
    "GROQ_API_KEY": "gsk_dummy",
    "MONGODB_URI": "mongodb://localhost:27017",
    "MONGODB_DB_NAME": "test_db",
    "MAX_PDF_SIZE_MB": "10",   # 10 MB limit in tests
    "MAX_AUDIO_SIZE_MB": "5",  # 5 MB limit in tests
}.items():
    os.environ.setdefault(_k, _v)

import app._server as srv  # noqa: E402

_MB = 1024 * 1024


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def client() -> TestClient:
    """A synchronous TestClient wrapping the FastAPI app."""
    return TestClient(srv.app, raise_server_exceptions=False)


def _make_fitz_mock(text: str = "Sample extracted text from PDF.") -> MagicMock:
    """Return a fitz.open(...) mock that extracts a fixed text string."""
    page = MagicMock()
    page.get_text.return_value = text
    doc = MagicMock()
    doc.__iter__ = MagicMock(return_value=iter([page]))
    fitz_mock = MagicMock()
    fitz_mock.open.return_value = doc
    return fitz_mock


def _make_db_mock() -> MagicMock:
    """Return a mock MongoDB db with an uploaded_pdfs collection."""
    db = MagicMock()
    db.uploaded_pdfs.insert_one = MagicMock(return_value=MagicMock(inserted_id="pdf-test"))
    db.uploaded_pdfs.find_one = MagicMock(return_value=None)
    return db


# ---------------------------------------------------------------------------
# POST /api/upload/pdf
# ---------------------------------------------------------------------------


class TestUploadPdf:
    # The endpoint calls asyncio.to_thread to run fitz in a thread pool.
    # We patch it to invoke the callable directly (synchronous test context).
    _THREAD_PATCH = "app._server.asyncio.to_thread"
    _FITZ_PATCH = "app._server.fitz"
    _DB_PATCH = "app._server.db"

    @staticmethod
    def _run_to_thread(fn, *args, **kwargs):
        """Synchronous stand-in for asyncio.to_thread used in patches."""
        return fn(*args, **kwargs)

    def _post_pdf(
        self,
        client: TestClient,
        content: bytes = b"%PDF-1.4 dummy content",
        filename: str = "test.pdf",
        content_length_override: int | None = None,
        extra_headers: dict[str, str] | None = None,
    ):
        headers: dict[str, str] = extra_headers or {}
        if content_length_override is not None:
            headers["content-length"] = str(content_length_override)
        return client.post(
            "/api/upload/pdf",
            files={"file": (filename, io.BytesIO(content), "application/pdf")},
            headers=headers,
        )

    def test_happy_path(self, client: TestClient):
        fitz_mock = _make_fitz_mock("Extracted text content.")
        db_mock = _make_db_mock()

        with (
            patch(self._FITZ_PATCH, fitz_mock),
            patch(self._DB_PATCH, db_mock),
            patch(self._THREAD_PATCH, side_effect=self._run_to_thread),
            patch("app._server.set_user_context", new_callable=AsyncMock),
        ):
            resp = self._post_pdf(client)

        assert resp.status_code == 200
        body = resp.json()
        assert "file_id" in body
        assert body["url"].startswith("/api/pdf/")
        assert body["text_length"] > 0
        assert body["truncated"] is False
        db_mock.uploaded_pdfs.insert_one.assert_called_once()

    def test_content_length_header_pre_check_returns_413(self, client: TestClient):
        """When Content-Length header exceeds MAX_PDF_BYTES, reject before reading."""
        oversized = srv.MAX_PDF_BYTES + 1
        with patch("app._server.set_user_context", new_callable=AsyncMock):
            resp = self._post_pdf(
                client,
                content=b"small",
                content_length_override=oversized,
            )
        assert resp.status_code == 413
        assert "too large" in resp.json()["detail"].lower()

    def test_body_size_guard_returns_413(self, client: TestClient):
        """When body itself exceeds MAX_PDF_BYTES (no Content-Length header), reject after read."""
        oversized_body = b"x" * (srv.MAX_PDF_BYTES + 1)
        with (
            patch("app._server.set_user_context", new_callable=AsyncMock),
            # Prevent the fitz call from running — we expect 413 before it
            patch(self._FITZ_PATCH, _make_fitz_mock()),
        ):
            resp = self._post_pdf(client, content=oversized_body)
        assert resp.status_code == 413

    def test_non_pdf_extension_returns_400(self, client: TestClient):
        with patch("app._server.set_user_context", new_callable=AsyncMock):
            resp = self._post_pdf(client, filename="report.docx")
        assert resp.status_code == 400
        assert "pdf" in resp.json()["detail"].lower()

    def test_empty_file_returns_400(self, client: TestClient):
        with (
            patch("app._server.set_user_context", new_callable=AsyncMock),
            patch(self._FITZ_PATCH, _make_fitz_mock()),
        ):
            resp = self._post_pdf(client, content=b"")
        assert resp.status_code == 400
        assert "empty" in resp.json()["detail"].lower()

    def test_fitz_unavailable_returns_500(self, client: TestClient):
        with (
            patch(self._FITZ_PATCH, None),
            patch(self._DB_PATCH, _make_db_mock()),
            patch(self._THREAD_PATCH, side_effect=self._run_to_thread),
            patch("app._server.set_user_context", new_callable=AsyncMock),
        ):
            resp = self._post_pdf(client)
        assert resp.status_code == 500
        assert "pymupdf" in resp.json()["detail"].lower()

    def test_no_extracted_text_returns_400(self, client: TestClient):
        fitz_mock = _make_fitz_mock(text="")  # blank text
        with (
            patch(self._FITZ_PATCH, fitz_mock),
            patch(self._DB_PATCH, _make_db_mock()),
            patch(self._THREAD_PATCH, side_effect=self._run_to_thread),
            patch("app._server.set_user_context", new_callable=AsyncMock),
        ):
            resp = self._post_pdf(client)
        assert resp.status_code == 400
        assert "no readable text" in resp.json()["detail"].lower()

    def test_oversized_text_is_truncated(self, client: TestClient):
        """PDFs producing text > _MAX_PDF_TEXT_CHARS must be stored truncated."""
        huge_text = "A" * (srv._MAX_PDF_TEXT_CHARS + 500)
        fitz_mock = _make_fitz_mock(text=huge_text)
        db_mock = _make_db_mock()

        with (
            patch(self._FITZ_PATCH, fitz_mock),
            patch(self._DB_PATCH, db_mock),
            patch(self._THREAD_PATCH, side_effect=self._run_to_thread),
            patch("app._server.set_user_context", new_callable=AsyncMock),
        ):
            resp = self._post_pdf(client)

        assert resp.status_code == 200
        body = resp.json()
        assert body["truncated"] is True
        assert body["text_length"] == srv._MAX_PDF_TEXT_CHARS

        # Confirm the truncated flag is persisted in MongoDB
        call_args = db_mock.uploaded_pdfs.insert_one.call_args[0][0]
        assert call_args["truncated"] is True
        assert len(call_args["text"]) == srv._MAX_PDF_TEXT_CHARS

    def test_internal_error_returns_500(self, client: TestClient):
        fitz_mock = MagicMock()
        fitz_mock.open.side_effect = RuntimeError("corrupt PDF")

        with (
            patch(self._FITZ_PATCH, fitz_mock),
            patch(self._DB_PATCH, _make_db_mock()),
            patch(self._THREAD_PATCH, side_effect=self._run_to_thread),
            patch("app._server.set_user_context", new_callable=AsyncMock),
        ):
            resp = self._post_pdf(client)

        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# POST /api/audio/transcribe
# ---------------------------------------------------------------------------


def _make_groq_response(status_code: int = 200, text: str = "What is GraphRAG?") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = {"text": text}
    resp.text = ""
    return resp


class TestTranscribeAudio:
    _GROQ_HTTP_PATCH = "app._server.pool"

    def _post_audio(
        self,
        client: TestClient,
        content: bytes = b"fake-audio-data",
        filename: str = "speech.webm",
        content_type: str = "audio/webm",
        content_length_override: int | None = None,
    ):
        headers: dict[str, str] = {}
        if content_length_override is not None:
            headers["content-length"] = str(content_length_override)
        return client.post(
            "/api/audio/transcribe",
            files={"file": (filename, io.BytesIO(content), content_type)},
            data={"language": "en"},
            headers=headers,
        )

    def test_happy_path(self, client: TestClient):
        groq_resp = _make_groq_response(200, "What is the transformer architecture?")
        pool_mock = MagicMock()
        pool_mock.groq_http.post = AsyncMock(return_value=groq_resp)

        with (
            patch("app._server.set_user_context", new_callable=AsyncMock),
            patch("app._server.pool", pool_mock),
            patch("app._server.GROQ_API_KEY", "gsk_dummy"),
            patch("app._server.GROQ_API_KEYS", ["gsk_dummy"]),
        ):
            resp = self._post_audio(client)

        assert resp.status_code == 200
        assert resp.json()["text"] == "What is the transformer architecture?"

    def test_content_length_header_pre_check_returns_413(self, client: TestClient):
        oversized = srv.MAX_AUDIO_BYTES + 1
        with patch("app._server.set_user_context", new_callable=AsyncMock):
            resp = self._post_audio(client, content_length_override=oversized)
        assert resp.status_code == 413
        assert "too large" in resp.json()["detail"].lower()

    def test_body_size_guard_returns_413(self, client: TestClient):
        oversized_body = b"x" * (srv.MAX_AUDIO_BYTES + 1)
        with patch("app._server.set_user_context", new_callable=AsyncMock):
            resp = self._post_audio(client, content=oversized_body)
        assert resp.status_code == 413

    def test_empty_audio_returns_400(self, client: TestClient):
        with (
            patch("app._server.set_user_context", new_callable=AsyncMock),
            patch("app._server.GROQ_API_KEY", "gsk_dummy"),
            patch("app._server.GROQ_API_KEYS", ["gsk_dummy"]),
        ):
            resp = self._post_audio(client, content=b"")
        assert resp.status_code == 400
        assert "empty" in resp.json()["detail"].lower()

    def test_groq_429_rotates_key_and_retries(self, client: TestClient):
        """A 429 on the first key should rotate and succeed on the second key."""
        fail_resp = _make_groq_response(429)
        ok_resp = _make_groq_response(200, "Retried successfully")

        pool_mock = MagicMock()
        pool_mock.groq_http.post = AsyncMock(side_effect=[fail_resp, ok_resp])

        with (
            patch("app._server.set_user_context", new_callable=AsyncMock),
            patch("app._server.pool", pool_mock),
            patch("app._server.GROQ_API_KEY", ""),
            patch("app._server.GROQ_API_KEYS", ["gsk_key1", "gsk_key2"]),
            patch("app._server.rotate_groq_key"),
        ):
            resp = self._post_audio(client)

        assert resp.status_code == 200
        assert resp.json()["text"] == "Retried successfully"

    def test_all_groq_keys_exhausted_returns_500(self, client: TestClient):
        fail_resp = _make_groq_response(500)
        pool_mock = MagicMock()
        pool_mock.groq_http.post = AsyncMock(return_value=fail_resp)

        with (
            patch("app._server.set_user_context", new_callable=AsyncMock),
            patch("app._server.pool", pool_mock),
            patch("app._server.GROQ_API_KEY", ""),
            patch("app._server.GROQ_API_KEYS", ["gsk_only_key"]),
            patch("app._server.rotate_groq_key"),
        ):
            resp = self._post_audio(client)

        assert resp.status_code == 500

    def test_whisper_hallucination_returns_empty_string(self, client: TestClient):
        """Whisper hallucination outputs (e.g. 'Thank you for watching') must be blanked."""
        groq_resp = _make_groq_response(200, "Thank you for watching!")
        pool_mock = MagicMock()
        pool_mock.groq_http.post = AsyncMock(return_value=groq_resp)

        with (
            patch("app._server.set_user_context", new_callable=AsyncMock),
            patch("app._server.pool", pool_mock),
            patch("app._server.GROQ_API_KEY", "gsk_dummy"),
            patch("app._server.GROQ_API_KEYS", ["gsk_dummy"]),
        ):
            resp = self._post_audio(client)

        assert resp.status_code == 200
        assert resp.json()["text"] == ""
