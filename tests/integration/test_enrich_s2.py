"""
Integration tests for app/sources/semantic_scholar.py — enrich_arxiv_papers_with_s2().

Covers:
- Single paper successfully enriched with S2 metadata
- Paper missing arxiv_id is returned unchanged
- S2 lookup returns None → paper returned unchanged
- Batch timeout returns original papers without raising
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch

from tests.integration.conftest import make_mock_response


def _single_paper_s2_response(arxiv_id: str = "1706.03762") -> Dict[str, Any]:
    return {
        "paperId": f"s2-{arxiv_id}",
        "title": "Attention Is All You Need",
        "authors": [{"name": "Vaswani"}],
        "year": 2017,
        "abstract": "The Transformer architecture.",
        "citationCount": 50000,
        "influentialCitationCount": 5000,
        "openAccessPdf": {"url": "https://arxiv.org/pdf/1706.03762.pdf"},
        "externalIds": {"ArXiv": arxiv_id, "DOI": "10.48550/arXiv.1706.03762"},
        "tldr": {"text": "Introduces the Transformer model."},
        "venue": "NeurIPS",
        "publicationVenue": None,
        "fieldsOfStudy": ["Computer Science"],
        "publicationTypes": [],
    }


class TestEnrichArxivPapersWithS2:
    async def test_paper_enriched_with_s2_metadata(self, sample_paper):
        import app.sources.semantic_scholar as s2_mod

        s2_mod._S2_CACHE.clear()

        arxiv_id = sample_paper["id"]  # "2301.00001"
        mock_s2_data = _single_paper_s2_response(arxiv_id)
        mock_resp = make_mock_response(200, mock_s2_data)

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("app.sources.semantic_scholar.httpx.AsyncClient", return_value=mock_client):
            results = await s2_mod.enrich_arxiv_papers_with_s2([sample_paper])

        assert len(results) == 1
        enriched = results[0]
        # S2-provided fields should be merged in
        assert enriched["citation_count"] == 50000
        assert enriched["tldr"] == "Introduces the Transformer model."
        assert enriched["doi"] == "10.48550/arXiv.1706.03762"
        # Original fields preserved
        assert enriched["title"] == sample_paper["title"]

    async def test_paper_without_arxiv_id_unchanged(self):
        import app.sources.semantic_scholar as s2_mod

        s2_mod._S2_CACHE.clear()

        paper = {"title": "Some Paper", "authors": ["Alice"], "year": 2022}
        # No "id" key → no arxiv_id lookup
        results = await s2_mod.enrich_arxiv_papers_with_s2([paper])
        assert results == [paper]

    async def test_s2_lookup_returns_none_paper_unchanged(self, sample_paper):
        import app.sources.semantic_scholar as s2_mod

        s2_mod._S2_CACHE.clear()

        # 404 → _normalize_paper not called → get_paper_by_arxiv_id_s2 returns None
        mock_resp = make_mock_response(404)
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("app.sources.semantic_scholar.httpx.AsyncClient", return_value=mock_client):
            results = await s2_mod.enrich_arxiv_papers_with_s2([sample_paper])

        assert results[0]["title"] == sample_paper["title"]
        # No S2 fields should be present
        assert "citation_count" not in results[0]

    async def test_empty_input_returns_empty(self):
        import app.sources.semantic_scholar as s2_mod

        results = await s2_mod.enrich_arxiv_papers_with_s2([])
        assert results == []

    async def test_batch_with_multiple_papers(self, sample_paper_list):
        import app.sources.semantic_scholar as s2_mod

        s2_mod._S2_CACHE.clear()

        # Return 404 for both → papers unchanged
        mock_resp = make_mock_response(404)
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("app.sources.semantic_scholar.httpx.AsyncClient", return_value=mock_client):
            results = await s2_mod.enrich_arxiv_papers_with_s2(sample_paper_list)

        assert len(results) == len(sample_paper_list)

    async def test_timeout_returns_original_papers(self, sample_paper):
        """If the batch gather times out, original papers are returned gracefully."""
        import app.sources.semantic_scholar as s2_mod

        s2_mod._S2_CACHE.clear()

        async def slow_lookup(*args, **kwargs):
            await asyncio.sleep(100)  # will be cancelled by wait_for timeout

        with patch.object(s2_mod, "get_paper_by_arxiv_id_s2", side_effect=slow_lookup):
            with patch("asyncio.wait_for", side_effect=asyncio.TimeoutError):
                results = await s2_mod.enrich_arxiv_papers_with_s2([sample_paper])

        assert results == [sample_paper]
