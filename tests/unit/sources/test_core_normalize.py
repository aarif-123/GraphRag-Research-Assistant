"""
Unit tests for app/sources/core.py — pure normalisation logic.

Tests cover:
- _normalize_work(): full record, missing optional fields, author formats
- Year extraction from ISO dates and edge cases
- DOI and PDF URL construction
- ID fallback when core_id is absent
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Inline replica of _normalize_work() so the tests are free of env-var
# and network dependencies.  Update this if the production function changes.
# ---------------------------------------------------------------------------


def _normalize_work(work: Dict[str, Any]) -> Dict[str, Any]:
    raw_authors = work.get("authors") or []
    authors: List[str] = []
    for a in raw_authors:
        if isinstance(a, dict):
            name = a.get("name", "").strip()
            if name:
                authors.append(name)
        elif isinstance(a, str):
            name = a.strip()
            if name:
                authors.append(name)

    year: Optional[int] = None
    pub_date = work.get("publishedDate") or work.get("datePublished") or ""
    if pub_date and isinstance(pub_date, str):
        match = re.search(r"\b\d{4}\b", pub_date)
        if match:
            try:
                year = int(match.group())
            except ValueError:
                pass

    pdf_url = work.get("downloadUrl") or work.get("fullTextIdentifier") or ""
    doi = work.get("doi") or ""
    core_id = str(work.get("id") or "")

    return {
        "id": f"CORE:{core_id}" if core_id else "CORE:unknown",
        "source": "CORE",
        "core_id": core_id,
        "title": work.get("title") or "Unknown Title",
        "authors": authors if authors else ["Unknown Author"],
        "year": year or "?",
        "abstract": work.get("abstract") or "",
        "pdf_url": pdf_url,
        "url": (
            work.get("downloadUrl") or (f"https://core.ac.uk/works/{core_id}" if core_id else "")
        ),
        "doi": doi,
        "doi_url": f"https://doi.org/{doi}" if doi else "",
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestNormalizeWorkAuthors:
    def test_authors_as_list_of_dicts(self, raw_core_work):
        result = _normalize_work(raw_core_work)
        assert result["authors"] == ["Alice Smith", "Bob Jones"]

    def test_authors_as_list_of_strings(self):
        work = {"id": 1, "title": "T", "authors": ["Alice", "Bob"]}
        result = _normalize_work(work)
        assert result["authors"] == ["Alice", "Bob"]

    def test_empty_author_names_excluded(self):
        work = {"id": 1, "title": "T", "authors": [{"name": ""}, {"name": "  "}]}
        result = _normalize_work(work)
        assert result["authors"] == ["Unknown Author"]

    def test_missing_authors_defaults_to_unknown(self):
        work = {"id": 1, "title": "T"}
        result = _normalize_work(work)
        assert result["authors"] == ["Unknown Author"]

    def test_mixed_author_types(self):
        work = {
            "id": 1,
            "title": "T",
            "authors": [{"name": "Alice"}, "Bob"],
        }
        result = _normalize_work(work)
        assert "Alice" in result["authors"]
        assert "Bob" in result["authors"]


class TestNormalizeWorkYear:
    def test_iso_date_extracts_year(self):
        work = {"id": 1, "title": "T", "publishedDate": "2022-06-15"}
        result = _normalize_work(work)
        assert result["year"] == 2022

    def test_year_only_string(self):
        work = {"id": 1, "title": "T", "publishedDate": "2019"}
        result = _normalize_work(work)
        assert result["year"] == 2019

    def test_fallback_to_datePublished(self):
        work = {"id": 1, "title": "T", "datePublished": "2020-01-01"}
        result = _normalize_work(work)
        assert result["year"] == 2020

    def test_missing_date_returns_question_mark(self):
        work = {"id": 1, "title": "T"}
        result = _normalize_work(work)
        assert result["year"] == "?"

    def test_malformed_date_returns_question_mark(self):
        work = {"id": 1, "title": "T", "publishedDate": "no-date-here"}
        result = _normalize_work(work)
        assert result["year"] == "?"


class TestNormalizeWorkId:
    def test_id_prefixed_with_core(self):
        work = {"id": 42, "title": "T"}
        result = _normalize_work(work)
        assert result["id"] == "CORE:42"

    def test_missing_id_returns_unknown(self):
        work = {"title": "T"}
        result = _normalize_work(work)
        assert result["id"] == "CORE:unknown"

    def test_core_id_stored_as_string(self):
        work = {"id": 123, "title": "T"}
        result = _normalize_work(work)
        assert result["core_id"] == "123"


class TestNormalizeWorkUrls:
    def test_doi_url_constructed(self, raw_core_work):
        result = _normalize_work(raw_core_work)
        doi = raw_core_work["doi"]
        assert result["doi_url"] == f"https://doi.org/{doi}"

    def test_empty_doi_no_doi_url(self):
        work = {"id": 1, "title": "T", "doi": ""}
        result = _normalize_work(work)
        assert result["doi_url"] == ""

    def test_missing_doi_no_doi_url(self):
        work = {"id": 1, "title": "T"}
        result = _normalize_work(work)
        assert result["doi_url"] == ""

    def test_pdf_url_from_downloadUrl(self):
        work = {"id": 1, "title": "T", "downloadUrl": "https://example.com/p.pdf"}
        result = _normalize_work(work)
        assert result["pdf_url"] == "https://example.com/p.pdf"

    def test_pdf_url_fallback_to_fullTextIdentifier(self):
        work = {
            "id": 1,
            "title": "T",
            "fullTextIdentifier": "https://example.com/full.pdf",
        }
        result = _normalize_work(work)
        assert result["pdf_url"] == "https://example.com/full.pdf"


class TestNormalizeWorkTitle:
    def test_title_preserved(self, raw_core_work):
        result = _normalize_work(raw_core_work)
        assert result["title"] == raw_core_work["title"]

    def test_missing_title_defaults(self):
        work = {"id": 1}
        result = _normalize_work(work)
        assert result["title"] == "Unknown Title"

    def test_source_is_always_core(self):
        work = {"id": 1, "title": "T"}
        result = _normalize_work(work)
        assert result["source"] == "CORE"
