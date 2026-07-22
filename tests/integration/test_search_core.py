"""
Integration tests for app/sources/core.py — search_core_papers().

All HTTP calls are mocked; no real network is required.

Covers:
- Happy path: 200 response returns normalized paper list
- 401 Unauthorized: returns []
- 5xx error: returns []
- Missing API key: returns [] without making any HTTP call
- Empty query: returns [] without making any HTTP call
- Results without title are filtered out
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.integration.conftest import make_mock_response

# ---------------------------------------------------------------------------
# The function under test — imported directly from the source module.
# We patch httpx.AsyncClient.get() so no real network call is made.
# The module reads CORE_API_KEY from os.environ at import time, so we must
# patch os.environ before importing or use monkeypatch.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def set_core_api_key(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CORE_API_KEY", "test-api-key-for-ci")
    # Force re-read of the env var in the module
    import app.sources.core as core_mod

    monkeypatch.setattr(core_mod, "_CORE_API_KEY", "test-api-key-for-ci")
    return "test-api-key-for-ci"


async def _run_search(query: str, mock_get, limit: int = 5):
    """Helper: call search_core_papers() with mock httpx."""
    from app.sources import core as core_mod

    with patch.object(
        core_mod.httpx.AsyncClient,
        "__aenter__",
        return_value=MagicMock(get=AsyncMock(return_value=mock_get)),
    ):
        # patch the async context manager
        pass

    # Use a simpler approach: patch httpx.AsyncClient directly
    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(return_value=mock_get)

    with patch("app.sources.core.httpx.AsyncClient", return_value=mock_client):
        from app.sources.core import search_core_papers

        return await search_core_papers(query, limit=limit)


class TestSearchCorePapersHappyPath:
    async def test_returns_normalized_papers(self, core_search_response):
        mock_resp = make_mock_response(200, core_search_response)
        results = await _run_search("transformer NLP", mock_resp)
        assert len(results) == 2
        assert results[0]["title"] == "Transformers in NLP"
        assert results[0]["source"] == "CORE"
        assert results[0]["year"] == 2021
        assert results[1]["title"] == "BERT Pre-training"

    async def test_results_have_required_fields(self, core_search_response):
        mock_resp = make_mock_response(200, core_search_response)
        results = await _run_search("deep learning", mock_resp)
        required_fields = {"id", "source", "title", "authors", "year", "abstract", "url"}
        for paper in results:
            assert required_fields.issubset(paper.keys())

    async def test_doi_url_constructed_when_doi_present(self, core_search_response):
        mock_resp = make_mock_response(200, core_search_response)
        results = await _run_search("nlp", mock_resp)
        first = results[0]
        assert first["doi_url"] == f"https://doi.org/{first['doi']}"

    async def test_empty_doi_no_doi_url(self, core_search_response):
        mock_resp = make_mock_response(200, core_search_response)
        results = await _run_search("bert", mock_resp)
        second = results[1]
        assert second["doi"] == ""
        assert second["doi_url"] == ""


class TestSearchCorePapersErrorHandling:
    async def test_401_returns_empty(self):
        mock_resp = make_mock_response(401)
        results = await _run_search("test query", mock_resp)
        assert results == []

    async def test_500_returns_empty(self):
        mock_resp = make_mock_response(500)
        results = await _run_search("test query", mock_resp)
        assert results == []

    async def test_results_without_title_filtered(self):
        payload = {
            "results": [
                {"id": 1, "title": "Valid Paper", "authors": []},
                {"id": 2, "title": None},  # no title — should be filtered
                {"id": 3},  # missing title key — should be filtered
            ]
        }
        mock_resp = make_mock_response(200, payload)
        results = await _run_search("valid", mock_resp)
        assert len(results) == 1
        assert results[0]["title"] == "Valid Paper"

    async def test_empty_results_list_returns_empty(self):
        mock_resp = make_mock_response(200, {"results": []})
        results = await _run_search("obscure query", mock_resp)
        assert results == []


class TestSearchCorePapersGuards:
    async def test_empty_query_returns_empty_without_http_call(self, monkeypatch):
        from app.sources.core import search_core_papers

        called = []

        async def fake_get(*args, **kwargs):
            called.append(True)
            return make_mock_response(200, {"results": []})

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = fake_get

        with patch("app.sources.core.httpx.AsyncClient", return_value=mock_client):
            result = await search_core_papers("   ", limit=5)

        assert result == []
        assert called == []  # No HTTP call made

    async def test_missing_api_key_returns_empty_without_http_call(self, monkeypatch):
        import app.sources.core as core_mod

        monkeypatch.setattr(core_mod, "_CORE_API_KEY", "")

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock()

        with patch("app.sources.core.httpx.AsyncClient", return_value=mock_client):
            from app.sources.core import search_core_papers

            result = await search_core_papers("test", limit=5)

        assert result == []
        mock_client.get.assert_not_called()
