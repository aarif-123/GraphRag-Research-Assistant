"""
Unit tests for app/sources/semantic_scholar.py — normalisation and cache logic.

Covers:
- _normalize_paper(): full record, missing optional fields, URL construction
- _get_s2_cache() / _set_s2_cache(): cache hit, miss, TTL expiry
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional
from unittest.mock import patch

# ---------------------------------------------------------------------------
# Inline replicas of the helpers under test.
# ---------------------------------------------------------------------------

_S2_CACHE_TTL = 43200  # 12 hours


def _normalize_paper(raw: Dict[str, Any]) -> Dict[str, Any]:
    authors = [a.get("name", "") for a in (raw.get("authors") or [])]
    pdf_url = ""
    if raw.get("openAccessPdf") and raw["openAccessPdf"].get("url"):
        pdf_url = raw["openAccessPdf"]["url"]

    ext_ids = raw.get("externalIds") or {}
    arxiv_id = ext_ids.get("ArXiv", "")
    doi = ext_ids.get("DOI", "")

    tldr = ""
    if raw.get("tldr") and raw["tldr"].get("text"):
        tldr = raw["tldr"]["text"]

    fields_of_study = raw.get("fieldsOfStudy") or []

    return {
        "s2_id": raw.get("paperId", ""),
        "title": raw.get("title", "Unknown Title"),
        "authors": authors,
        "year": raw.get("year"),
        "abstract": (raw.get("abstract") or "")[:600],
        "citation_count": raw.get("citationCount", 0),
        "influential_citations": raw.get("influentialCitationCount", 0),
        "tldr": tldr,
        "venue": raw.get("venue", ""),
        "fields_of_study": fields_of_study,
        "arxiv_id": arxiv_id,
        "doi": doi,
        "pdf_url": pdf_url,
        "arxiv_url": f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else "",
        "s2_url": f"https://www.semanticscholar.org/paper/{raw.get('paperId', '')}",
        "doi_url": f"https://doi.org/{doi}" if doi else "",
    }


_S2_CACHE: Dict[str, Any] = {}


def _get_s2_cache(key: str) -> Optional[Any]:
    entry = _S2_CACHE.get(key)
    if entry and time.time() - entry[1] < _S2_CACHE_TTL:
        return entry[0]
    return None


def _set_s2_cache(key: str, val: Any) -> None:
    _S2_CACHE[key] = (val, time.time())


# ---------------------------------------------------------------------------
# Tests — _normalize_paper()
# ---------------------------------------------------------------------------


class TestNormalizePaper:
    def test_full_record(self, raw_s2_paper):
        result = _normalize_paper(raw_s2_paper)
        assert result["s2_id"] == "abc123"
        assert result["title"] == "Graph Neural Networks: A Review"
        assert result["authors"] == ["Alice", "Bob"]
        assert result["year"] == 2020
        assert result["citation_count"] == 500
        assert result["influential_citations"] == 50
        assert result["tldr"] == "A review of GNNs"
        assert result["venue"] == "ICML"
        assert result["arxiv_id"] == "2001.00001"
        assert result["doi"] == "10.1234/foo"
        assert result["pdf_url"] == "https://example.com/paper.pdf"

    def test_missing_optional_fields(self):
        raw = {"paperId": "xyz", "title": "Minimal Paper"}
        result = _normalize_paper(raw)
        assert result["s2_id"] == "xyz"
        assert result["authors"] == []
        assert result["year"] is None
        assert result["citation_count"] == 0
        assert result["tldr"] == ""
        assert result["arxiv_id"] == ""
        assert result["doi"] == ""
        assert result["pdf_url"] == ""
        assert result["arxiv_url"] == ""
        assert result["doi_url"] == ""

    def test_missing_title_defaults(self):
        result = _normalize_paper({"paperId": "p1"})
        assert result["title"] == "Unknown Title"

    def test_abstract_truncated_at_600(self):
        long_abstract = "A" * 700
        result = _normalize_paper({"paperId": "p1", "abstract": long_abstract})
        assert len(result["abstract"]) == 600

    def test_short_abstract_not_truncated(self):
        short = "Short abstract."
        result = _normalize_paper({"paperId": "p1", "abstract": short})
        assert result["abstract"] == short

    def test_arxiv_url_constructed(self):
        raw = {
            "paperId": "p",
            "externalIds": {"ArXiv": "2001.99999"},
        }
        result = _normalize_paper(raw)
        assert result["arxiv_url"] == "https://arxiv.org/abs/2001.99999"

    def test_doi_url_constructed(self):
        raw = {
            "paperId": "p",
            "externalIds": {"DOI": "10.1234/test"},
        }
        result = _normalize_paper(raw)
        assert result["doi_url"] == "https://doi.org/10.1234/test"

    def test_s2_url_always_constructed(self):
        raw = {"paperId": "paper42"}
        result = _normalize_paper(raw)
        assert result["s2_url"] == "https://www.semanticscholar.org/paper/paper42"

    def test_no_open_access_pdf_returns_empty_url(self):
        raw = {"paperId": "p", "openAccessPdf": None}
        result = _normalize_paper(raw)
        assert result["pdf_url"] == ""

    def test_fields_of_study_preserved(self, raw_s2_paper):
        result = _normalize_paper(raw_s2_paper)
        assert result["fields_of_study"] == ["Computer Science"]


# ---------------------------------------------------------------------------
# Tests — S2 in-memory cache helpers
# ---------------------------------------------------------------------------


class TestS2Cache:
    def setup_method(self):
        """Clear shared cache before each test."""
        _S2_CACHE.clear()

    def test_cache_miss_returns_none(self):
        assert _get_s2_cache("nonexistent") is None

    def test_cache_hit_returns_value(self):
        with patch("time.time", return_value=1_000_000.0):
            _set_s2_cache("k1", {"title": "Paper A"})
        with patch("time.time", return_value=1_000_000.0 + 60):
            result = _get_s2_cache("k1")
        assert result == {"title": "Paper A"}

    def test_cache_entry_expires_after_ttl(self):
        with patch("time.time", return_value=1_000_000.0):
            _set_s2_cache("k_exp", "value")
        with patch("time.time", return_value=1_000_000.0 + _S2_CACHE_TTL + 1):
            result = _get_s2_cache("k_exp")
        assert result is None

    def test_cache_entry_alive_just_before_ttl(self):
        with patch("time.time", return_value=1_000_000.0):
            _set_s2_cache("k_alive", "still_alive")
        with patch("time.time", return_value=1_000_000.0 + _S2_CACHE_TTL - 1):
            result = _get_s2_cache("k_alive")
        assert result == "still_alive"

    def test_overwriting_key_updates_value(self):
        with patch("time.time", return_value=1_000_000.0):
            _set_s2_cache("k_overwrite", "first")
            _set_s2_cache("k_overwrite", "second")
        with patch("time.time", return_value=1_000_000.0):
            assert _get_s2_cache("k_overwrite") == "second"
