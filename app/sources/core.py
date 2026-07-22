"""
CORE Open-Access Academic Search API v3 Connector.
Docs: https://api.core.ac.uk/docs/v3/
"""

import logging
import os
from typing import Any, Dict, List

import httpx

log = logging.getLogger(__name__)

# Base URL for CORE API v3
_CORE_BASE = "https://api.core.ac.uk/v3"
_CORE_API_KEY = os.getenv("CORE_API_KEY", "").strip()
_CORE_TIMEOUT = 10.0  # seconds


def _headers() -> Dict[str, str]:
    headers = {"User-Agent": "Aether-Research-Assistant/5.0", "Accept": "application/json"}
    if _CORE_API_KEY:
        headers["Authorization"] = f"Bearer {_CORE_API_KEY}"
    return headers


def _normalize_work(work: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize a CORE work record into Aether's unified paper schema."""

    # Process authors: can be list of dicts (name) or strings
    raw_authors = work.get("authors") or []
    authors = []
    for a in raw_authors:
        if isinstance(a, dict):
            name = a.get("name", "").strip()
            if name:
                authors.append(name)
        elif isinstance(a, str):
            name = a.strip()
            if name:
                authors.append(name)

    # Extract publication year from published date (e.g. "2023-01-01")
    year = None
    pub_date = work.get("publishedDate") or work.get("datePublished") or ""
    if pub_date and isinstance(pub_date, str):
        # Match first 4-digit sequence
        import re

        match = re.search(r"\b\d{4}\b", pub_date)
        if match:
            try:
                year = int(match.group())
            except ValueError:
                pass

    # Extract download / PDF URL
    pdf_url = work.get("downloadUrl") or work.get("fullTextIdentifier") or ""

    # Extract DOI and external URLs
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
        "url": work.get("downloadUrl") or f"https://core.ac.uk/works/{core_id}" if core_id else "",
        "doi": doi,
        "doi_url": f"https://doi.org/{doi}" if doi else "",
    }


async def search_core_papers(query: str, limit: int = 5) -> List[Dict[str, Any]]:
    """
    Search the CORE API v3 database for papers matching query.
    Returns a list of normalized paper dicts.
    """
    if not _CORE_API_KEY:
        log.warning("CORE_API_KEY environment variable is not configured. Skipping CORE search.")
        return []

    if not query.strip():
        return []

    params: Dict[str, Any] = {"q": query, "limit": limit, "pageSize": limit}

    try:
        async with httpx.AsyncClient(timeout=_CORE_TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(f"{_CORE_BASE}/search/works", params=params, headers=_headers())

            if resp.status_code == 401:
                log.error("CORE API returned 401 Unauthorized. Check your CORE_API_KEY in .env.")
                return []
            elif resp.status_code != 200:
                log.warning(
                    f"CORE API returned status {resp.status_code} for search query: {query}"
                )
                return []

            data = resp.json()
            results = data.get("results") or []

            normalized = []
            for work in results:
                if work.get("title"):
                    normalized.append(_normalize_work(work))

            return normalized

    except Exception as e:
        log.error(f"Error querying CORE API: {e}", exc_info=True)
        return []
