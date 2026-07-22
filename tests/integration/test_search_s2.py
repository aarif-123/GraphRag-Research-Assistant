"""
Integration tests for app/sources/semantic_scholar.py — search_papers_s2().

All HTTP calls are mocked; no real network required.

Covers:
- Happy path: 200 returns normalized paper list
- 429 rate limit: returns []
- Non-200: returns []
- Empty query: returns [] immediately
- Cache deduplication: second call with same args does not hit network
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from tests.integration.conftest import make_mock_response


def _make_async_client(mock_response: MagicMock) -> MagicMock:
    """Build a mock httpx.AsyncClient context manager returning mock_response."""
    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(return_value=mock_response)
    return mock_client


class TestSearchPapersS2HappyPath:
    async def test_returns_normalized_papers(self, s2_search_response):
        import app.sources.semantic_scholar as s2_mod

        # Clear in-module cache before test
        s2_mod._S2_CACHE.clear()

        mock_resp = make_mock_response(200, s2_search_response)
        mock_client = _make_async_client(mock_resp)

        with patch("app.sources.semantic_scholar.httpx.AsyncClient", return_value=mock_client):
            results = await s2_mod.search_papers_s2("graph neural networks", limit=5)

        assert len(results) == 1
        paper = results[0]
        assert paper["s2_id"] == "s2-001"
        assert paper["title"] == "Graph Neural Networks"
        assert paper["year"] == 2020
        assert paper["citation_count"] == 300

    async def test_required_fields_present(self, s2_search_response):
        import app.sources.semantic_scholar as s2_mod

        s2_mod._S2_CACHE.clear()

        mock_resp = make_mock_response(200, s2_search_response)
        mock_client = _make_async_client(mock_resp)

        with patch("app.sources.semantic_scholar.httpx.AsyncClient", return_value=mock_client):
            results = await s2_mod.search_papers_s2("gnn", limit=5)

        required = {"s2_id", "title", "authors", "year", "abstract", "citation_count", "s2_url"}
        for paper in results:
            assert required.issubset(paper.keys())

    async def test_arxiv_url_constructed(self, s2_search_response):
        import app.sources.semantic_scholar as s2_mod

        s2_mod._S2_CACHE.clear()

        mock_resp = make_mock_response(200, s2_search_response)
        mock_client = _make_async_client(mock_resp)

        with patch("app.sources.semantic_scholar.httpx.AsyncClient", return_value=mock_client):
            results = await s2_mod.search_papers_s2("gnn", limit=5)

        assert results[0]["arxiv_url"] == "https://arxiv.org/abs/2001.00100"


class TestSearchPapersS2ErrorHandling:
    async def test_429_rate_limit_returns_empty(self):
        import app.sources.semantic_scholar as s2_mod

        s2_mod._S2_CACHE.clear()

        mock_resp = make_mock_response(429)
        mock_client = _make_async_client(mock_resp)

        with patch("app.sources.semantic_scholar.httpx.AsyncClient", return_value=mock_client):
            result = await s2_mod.search_papers_s2("nlp", limit=5)

        assert result == []

    async def test_non_200_returns_empty(self):
        import app.sources.semantic_scholar as s2_mod

        s2_mod._S2_CACHE.clear()

        mock_resp = make_mock_response(503)
        mock_client = _make_async_client(mock_resp)

        with patch("app.sources.semantic_scholar.httpx.AsyncClient", return_value=mock_client):
            result = await s2_mod.search_papers_s2("test", limit=5)

        assert result == []

    async def test_empty_data_list_returns_empty(self):
        import app.sources.semantic_scholar as s2_mod

        s2_mod._S2_CACHE.clear()

        mock_resp = make_mock_response(200, {"data": []})
        mock_client = _make_async_client(mock_resp)

        with patch("app.sources.semantic_scholar.httpx.AsyncClient", return_value=mock_client):
            result = await s2_mod.search_papers_s2("obscure", limit=5)

        assert result == []


class TestSearchPapersS2Guards:
    async def test_empty_query_returns_empty_immediately(self):
        import app.sources.semantic_scholar as s2_mod

        s2_mod._S2_CACHE.clear()

        mock_client = MagicMock()
        mock_client.get = AsyncMock()

        with patch("app.sources.semantic_scholar.httpx.AsyncClient", return_value=mock_client):
            result = await s2_mod.search_papers_s2("   ", limit=5)

        assert result == []
        mock_client.get.assert_not_called()


class TestSearchPapersS2Caching:
    async def test_second_call_uses_cache_not_network(self, s2_search_response):
        """The second call with identical args must NOT hit the network."""
        import app.sources.semantic_scholar as s2_mod

        s2_mod._S2_CACHE.clear()

        mock_resp = make_mock_response(200, s2_search_response)
        mock_client = _make_async_client(mock_resp)

        with patch("app.sources.semantic_scholar.httpx.AsyncClient", return_value=mock_client):
            r1 = await s2_mod.search_papers_s2("gnn cache test", limit=3)
            r2 = await s2_mod.search_papers_s2("gnn cache test", limit=3)

        # HTTP GET should only have been called once
        assert mock_client.get.call_count == 1
        assert r1 == r2

    async def test_different_queries_both_fetch(self, s2_search_response):
        import app.sources.semantic_scholar as s2_mod

        s2_mod._S2_CACHE.clear()

        mock_resp = make_mock_response(200, s2_search_response)
        mock_client = _make_async_client(mock_resp)

        with patch("app.sources.semantic_scholar.httpx.AsyncClient", return_value=mock_client):
            await s2_mod.search_papers_s2("query alpha", limit=3)
            await s2_mod.search_papers_s2("query beta", limit=3)

        # Two distinct queries → two HTTP calls
        assert mock_client.get.call_count == 2
