"""
Integration tests for app/sources/wikipedia.py — search_wikipedia_summary().

All HTTP calls are mocked; no real network required.

Covers:
- Happy path: search hit → summary fetch → returns result dict
- Search returns no results → returns None
- Search non-200 → returns None
- Summary non-200 → returns None
- Empty / blank query → returns None immediately
- Cache deduplication: second call does not repeat HTTP
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from tests.integration.conftest import make_mock_response


def _make_seq_client(*responses) -> MagicMock:
    """
    Build a mock httpx.AsyncClient that returns each response in sequence
    for successive .get() calls.
    """
    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(side_effect=list(responses))
    return mock_client


class TestSearchWikipediaSummaryHappyPath:
    async def test_returns_result_dict(self, wikipedia_search_response, wikipedia_summary_response):
        import app.sources.wikipedia as wiki_mod

        wiki_mod._WIKI_CACHE.clear()

        search_resp = make_mock_response(200, wikipedia_search_response)
        summary_resp = make_mock_response(200, wikipedia_summary_response)
        mock_client = _make_seq_client(search_resp, summary_resp)

        with patch("app.sources.wikipedia.httpx.AsyncClient", return_value=mock_client):
            result = await wiki_mod.search_wikipedia_summary("Transformer model")

        assert result is not None
        assert result["title"] == "Transformer (machine learning model)"
        assert result["extract"] == "The Transformer is a deep learning model."
        assert "wikipedia" in result["url"] or "en.wikipedia" in result["url"]

    async def test_result_has_required_keys(
        self, wikipedia_search_response, wikipedia_summary_response
    ):
        import app.sources.wikipedia as wiki_mod

        wiki_mod._WIKI_CACHE.clear()

        search_resp = make_mock_response(200, wikipedia_search_response)
        summary_resp = make_mock_response(200, wikipedia_summary_response)
        mock_client = _make_seq_client(search_resp, summary_resp)

        with patch("app.sources.wikipedia.httpx.AsyncClient", return_value=mock_client):
            result = await wiki_mod.search_wikipedia_summary("attention mechanism")

        assert result is not None
        for key in ("title", "extract", "url", "description"):
            assert key in result


class TestSearchWikipediaSummaryErrorHandling:
    async def test_search_returns_empty_list_returns_none(self):
        import app.sources.wikipedia as wiki_mod

        wiki_mod._WIKI_CACHE.clear()

        empty_search = make_mock_response(200, {"query": {"search": []}})
        mock_client = _make_seq_client(empty_search)

        with patch("app.sources.wikipedia.httpx.AsyncClient", return_value=mock_client):
            result = await wiki_mod.search_wikipedia_summary("XYZ999Nonexistent")

        assert result is None

    async def test_search_non_200_returns_none(self):
        import app.sources.wikipedia as wiki_mod

        wiki_mod._WIKI_CACHE.clear()

        error_resp = make_mock_response(503)
        mock_client = _make_seq_client(error_resp)

        with patch("app.sources.wikipedia.httpx.AsyncClient", return_value=mock_client):
            result = await wiki_mod.search_wikipedia_summary("any query")

        assert result is None

    async def test_summary_non_200_returns_none(self, wikipedia_search_response):
        import app.sources.wikipedia as wiki_mod

        wiki_mod._WIKI_CACHE.clear()

        search_resp = make_mock_response(200, wikipedia_search_response)
        summary_err = make_mock_response(404)
        mock_client = _make_seq_client(search_resp, summary_err)

        with patch("app.sources.wikipedia.httpx.AsyncClient", return_value=mock_client):
            result = await wiki_mod.search_wikipedia_summary("Transformer")

        assert result is None


class TestSearchWikipediaSummaryGuards:
    async def test_empty_query_returns_none(self):
        import app.sources.wikipedia as wiki_mod

        wiki_mod._WIKI_CACHE.clear()

        mock_client = MagicMock()
        mock_client.get = AsyncMock()

        with patch("app.sources.wikipedia.httpx.AsyncClient", return_value=mock_client):
            result = await wiki_mod.search_wikipedia_summary("")

        assert result is None
        mock_client.get.assert_not_called()

    async def test_blank_query_returns_none(self):
        import app.sources.wikipedia as wiki_mod

        wiki_mod._WIKI_CACHE.clear()

        mock_client = MagicMock()
        mock_client.get = AsyncMock()

        with patch("app.sources.wikipedia.httpx.AsyncClient", return_value=mock_client):
            result = await wiki_mod.search_wikipedia_summary("   ")

        assert result is None


class TestSearchWikipediaSummaryCaching:
    async def test_second_call_uses_cache(
        self, wikipedia_search_response, wikipedia_summary_response
    ):
        import app.sources.wikipedia as wiki_mod

        wiki_mod._WIKI_CACHE.clear()

        search_resp = make_mock_response(200, wikipedia_search_response)
        summary_resp = make_mock_response(200, wikipedia_summary_response)
        mock_client = _make_seq_client(search_resp, summary_resp)

        with patch("app.sources.wikipedia.httpx.AsyncClient", return_value=mock_client):
            r1 = await wiki_mod.search_wikipedia_summary("attention is all you need")
            r2 = await wiki_mod.search_wikipedia_summary("attention is all you need")

        # Only 2 GET calls (search + summary) on first invocation; second uses cache
        assert mock_client.get.call_count == 2
        assert r1 == r2
