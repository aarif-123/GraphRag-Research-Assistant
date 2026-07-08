"""
Semantic Scholar Academic Graph API connector.

Free API (no key required for ≤100 req/5 min).
With S2_API_KEY env var: 10 req/s.

Docs: https://api.semanticscholar.org/api-docs/graph
"""

import asyncio
import logging
import os
from typing import Any, Dict, List, Optional

import httpx

log = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────
# Config
# ──────────────────────────────────────────────────────────────────
_S2_BASE = "https://api.semanticscholar.org/graph/v1"
_S2_API_KEY = os.getenv("S2_API_KEY", "")
_S2_TIMEOUT = 10.0  # seconds
_S2_SEMAPHORE = asyncio.Semaphore(2)  # max 2 concurrent requests

import time
_S2_CACHE = {}
_S2_CACHE_TTL = 43200  # 12 hours

def _get_s2_cache(key: str):
    entry = _S2_CACHE.get(key)
    if entry and time.time() - entry[1] < _S2_CACHE_TTL:
        return entry[0]
    return None

def _set_s2_cache(key: str, val):
    _S2_CACHE[key] = (val, time.time())

# Fields we want per paper — balances richness vs. payload size
_PAPER_FIELDS = ",".join([
    "title",
    "authors",
    "year",
    "abstract",
    "citationCount",
    "influentialCitationCount",
    "openAccessPdf",
    "externalIds",
    "tldr",
    "venue",
    "publicationVenue",
    "fieldsOfStudy",
    "publicationTypes",
])

_REF_FIELDS = "title,authors,year,citationCount,externalIds,openAccessPdf"


def _headers() -> Dict[str, str]:
    h: Dict[str, str] = {"User-Agent": "Aether-Research-Assistant/5.0"}
    if _S2_API_KEY:
        h["x-api-key"] = _S2_API_KEY
    return h


def _normalize_paper(raw: Dict) -> Dict:
    """Normalize a Semantic Scholar paper record into a clean dict."""
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


async def search_papers_s2(query: str, limit: int = 8) -> List[Dict]:
    """
    Search Semantic Scholar for papers matching a query string.
    Returns list of normalized paper dicts. Returns [] on error.
    """
    if not _S2_API_KEY:
        log.debug("S2_API_KEY not configured. Skipping Semantic Scholar search.")
        return []
    if not query.strip():
        return []

    cache_key = f"search_{query}_{limit}"
    cached = _get_s2_cache(cache_key)
    if cached is not None:
        log.debug(f"S2 search cache hit for {query}")
        return cached

    params = {
        "query": query,
        "fields": _PAPER_FIELDS,
        "limit": min(limit, 10),
    }

    async with _S2_SEMAPHORE:
        for attempt in range(3):
            try:
                async with httpx.AsyncClient(
                    headers=_headers(), timeout=_S2_TIMEOUT, follow_redirects=True
                ) as client:
                    resp = await client.get(f"{_S2_BASE}/paper/search", params=params)

                if resp.status_code == 429:
                    wait_time = (2 ** attempt) + 1
                    log.warning(f"Semantic Scholar rate limited (429) — retrying in {wait_time}s... (attempt {attempt+1}/3)")
                    await asyncio.sleep(wait_time)
                    continue
                if resp.status_code != 200:
                    log.warning(f"Semantic Scholar search returned {resp.status_code}")
                    return []

                data = resp.json()
                papers = data.get("data") or []
                res = [_normalize_paper(p) for p in papers if p.get("title")]
                _set_s2_cache(cache_key, res)
                return res

            except Exception as e:
                log.warning(f"Semantic Scholar search error: {e}")
                if attempt == 2:
                    return []
                await asyncio.sleep(1)
        return []


async def get_paper_by_arxiv_id_s2(arxiv_id: str) -> Optional[Dict]:
    """
    Fetch a single paper from Semantic Scholar by its arXiv ID.
    Returns normalized paper dict or None.
    """
    if not _S2_API_KEY:
        return None
    if not arxiv_id:
        return None

    cache_key = f"paper_{arxiv_id}"
    cached = _get_s2_cache(cache_key)
    if cached is not None:
        log.debug(f"S2 paper cache hit for {arxiv_id}")
        return cached

    paper_id = f"ArXiv:{arxiv_id}"

    async with _S2_SEMAPHORE:
        for attempt in range(3):
            try:
                async with httpx.AsyncClient(
                    headers=_headers(), timeout=_S2_TIMEOUT, follow_redirects=True
                ) as client:
                    resp = await client.get(
                        f"{_S2_BASE}/paper/{paper_id}",
                        params={"fields": _PAPER_FIELDS},
                    )

                if resp.status_code == 404:
                    return None
                if resp.status_code == 429:
                    wait_time = (2 ** attempt) + 1
                    log.warning(f"Semantic Scholar rate limited (429) — retrying in {wait_time}s... (attempt {attempt+1}/3)")
                    await asyncio.sleep(wait_time)
                    continue
                if resp.status_code != 200:
                    log.warning(f"S2 paper lookup returned {resp.status_code}")
                    return None

                res = _normalize_paper(resp.json())
                _set_s2_cache(cache_key, res)
                return res

            except Exception as e:
                log.warning(f"S2 paper lookup error: {e}")
                if attempt == 2:
                    return None
                await asyncio.sleep(1)
        return None


async def get_paper_references_s2(
    s2_paper_id: str, limit: int = 10
) -> List[Dict]:
    """
    Get the top references (papers cited by) a given S2 paper.
    Returns list of lightweight paper dicts.
    """
    if not _S2_API_KEY:
        return []
    if not s2_paper_id:
        return []

    async with _S2_SEMAPHORE:
        try:
            async with httpx.AsyncClient(
                headers=_headers(), timeout=_S2_TIMEOUT, follow_redirects=True
            ) as client:
                resp = await client.get(
                    f"{_S2_BASE}/paper/{s2_paper_id}/references",
                    params={"fields": _REF_FIELDS, "limit": limit},
                )

            if resp.status_code != 200:
                return []

            data = resp.json()
            refs = []
            for item in (data.get("data") or []):
                cited = item.get("citedPaper") or {}
                if cited.get("title"):
                    ext = cited.get("externalIds") or {}
                    refs.append({
                        "title": cited.get("title", ""),
                        "year": cited.get("year"),
                        "citation_count": cited.get("citationCount", 0),
                        "arxiv_id": ext.get("ArXiv", ""),
                        "doi": ext.get("DOI", ""),
                        "authors": [
                            a.get("name", "")
                            for a in (cited.get("authors") or [])
                        ][:3],
                        "pdf_url": (
                            (cited.get("openAccessPdf") or {}).get("url", "")
                        ),
                    })
            return refs

        except Exception as e:
            log.warning(f"S2 references error: {e}")
            return []


async def enrich_arxiv_papers_with_s2(
    arxiv_papers: List[Dict],
) -> List[Dict]:
    """
    Given a list of arXiv paper dicts (from retrieve_arxiv_context),
    enrich each with Semantic Scholar data (citation counts, TLDR, refs).

    Returns a NEW list with merged data. Falls back gracefully per paper.
    Strategy: lookup each paper by arXiv ID concurrently.
    """
    if not _S2_API_KEY:
        return arxiv_papers
    if not arxiv_papers:
        return []

    async def _enrich_one(paper: Dict) -> Dict:
        arxiv_id = paper.get("id", "")
        if not arxiv_id:
            return paper
        s2 = await get_paper_by_arxiv_id_s2(arxiv_id)
        if not s2:
            return paper
        # Merge: arXiv data takes precedence for title/abstract/authors
        # S2 adds: citation_count, tldr, doi, fields_of_study, s2_url, doi_url
        return {
            **paper,
            "citation_count": s2["citation_count"],
            "influential_citations": s2["influential_citations"],
            "tldr": s2["tldr"],
            "doi": s2["doi"],
            "doi_url": s2["doi_url"],
            "fields_of_study": s2["fields_of_study"],
            "s2_id": s2["s2_id"],
            "s2_url": s2["s2_url"],
            "venue": s2.get("venue") or paper.get("venue", ""),
        }

    # Run all lookups concurrently (semaphore guards rate limit)
    tasks = [_enrich_one(p) for p in arxiv_papers]
    enriched = await asyncio.gather(*tasks, return_exceptions=True)

    result = []
    for original, r in zip(arxiv_papers, enriched):
        if isinstance(r, Exception):
            log.warning(f"S2 enrichment error: {r}")
            result.append(original)
        else:
            result.append(r)
    return result


async def fetch_s2_papers_for_query(query: str, limit: int = 5) -> List[Dict]:
    """
    Search S2 for papers about a query — used when local DB has no results.
    Returns enriched paper dicts directly from S2.
    """
    return await search_papers_s2(query, limit=limit)
