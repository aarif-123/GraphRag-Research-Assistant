"""
OpenAlex Academic Graph API connector — bibliography metadata enrichment.

OpenAlex: https://api.openalex.org
- Free, no API key required (polite pool: 10 req/s with email header).
- Returns structured metadata: authors, venue, year, DOI, publication type.
- Primary tier in the bibliography enrichment waterfall.

Rate limit: 10 req/s in polite pool (set OPENALEX_EMAIL in .env to opt in).
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import unicodedata
from typing import Dict, List, Optional

import httpx

log = logging.getLogger(__name__)

_OA_BASE = "https://api.openalex.org"
_OA_TIMEOUT = 5.0  # seconds — kept short so it never blocks the main response
_OA_SEMAPHORE = asyncio.Semaphore(8)  # max concurrent OpenAlex requests

# Opt into the polite pool for higher rate limits (10 req/s vs 100k/day anon)
_OA_EMAIL = os.getenv("OPENALEX_EMAIL", "").strip()

# ── Conference keywords used to classify entry type ──────────────────────────
# Checked case-insensitively against the venue/host-org/type strings returned
# by OpenAlex.  Any match → @inproceedings; otherwise → @article.
_CONF_KEYWORDS: frozenset[str] = frozenset(
    {
        "cvpr",
        "iccv",
        "eccv",
        "neurips",
        "nips",
        "icml",
        "iclr",
        "acl",
        "emnlp",
        "naacl",
        "aaai",
        "ijcai",
        "sigkdd",
        "kdd",
        "sigmod",
        "vldb",
        "icse",
        "isca",
        "micro",
        "asplos",
        "sosp",
        "osdi",
        "nsdi",
        "usenix",
        "proceedings",
        "conference",
        "workshop",
        "symposium",
    }
)


def normalize_title(title: str) -> str:
    """
    Return a stable, normalised form of a paper title for use as a cache key
    or similarity comparison.

    Steps:
    1. Unicode NFKC normalisation (decompose ligatures, accents, fullwidth chars)
    2. Lowercase
    3. Strip all non-alphanumeric characters (punctuation, hyphens, spaces)
    4. Collapse whitespace

    Example:
        "Deep Residual Learning for Image Recognition" → "deepresiduallearningforimagerecognition"
    """
    if not title:
        return ""
    normalised = unicodedata.normalize("NFKC", title)
    normalised = normalised.lower()
    normalised = re.sub(r"[^a-z0-9]", "", normalised)
    return normalised


def _infer_entry_type(work: dict) -> str:
    """
    Infer the BibTeX entry type from an OpenAlex work record.

    OpenAlex `type` field values include: "article", "proceedings-article",
    "book-chapter", "preprint", "dataset", etc.
    A venue name containing a conference keyword also triggers @inproceedings.

    Returns one of: "inproceedings", "article", "misc"
    """
    oa_type = (work.get("type") or "").lower()
    if oa_type in ("proceedings-article", "conference-paper"):
        return "inproceedings"
    if oa_type == "book-chapter":
        return "incollection"

    # Check venue/source name
    source = work.get("primary_location") or {}
    source_name = ((source.get("source") or {}).get("display_name", "")).lower()
    if any(kw in source_name for kw in _CONF_KEYWORDS):
        return "inproceedings"

    return "article"


def _extract_venue(work: dict) -> str:
    """
    Extract the best human-readable venue string from an OpenAlex work record.

    Priority:
    1. primary_location → source → display_name  (journal or conference name)
    2. host_venue → display_name (legacy field in some records)
    3. Empty string if nothing is found.
    """
    loc = work.get("primary_location") or {}
    source = loc.get("source") or {}
    name = source.get("display_name", "").strip()
    if name:
        return name

    # Legacy fallback
    host = work.get("host_venue") or {}
    return host.get("display_name", "").strip()


def _extract_authors(work: dict) -> list[str]:
    """
    Extract author display names from the authorships list in an OpenAlex record.

    Returns a list of name strings (may be empty if no authorship data).
    """
    authorships = work.get("authorships") or []
    authors: list[str] = []
    for authorship in authorships:
        author_obj = authorship.get("author") or {}
        name = (author_obj.get("display_name") or "").strip()
        if name:
            authors.append(name)
    return authors


async def search_openalex(title: str) -> Optional[dict]:
    """
    Search OpenAlex for a paper by title and return enrichment metadata.

    Returns a dict with keys:
        authors    : list[str]   — ordered list of author display names
        venue      : str         — journal or conference name
        year       : int | None  — publication year
        doi        : str         — DOI without prefix (e.g. "10.1109/CVPR.2016.90")
        entry_type : str         — "article" | "inproceedings" | "incollection" | "misc"
        openalex_id: str         — OpenAlex work ID (for provenance)

    Returns None if no result is found or on any HTTP/network error.
    """
    if not title or not title.strip():
        return None

    params: dict = {
        "search": title.strip(),
        "select": "id,doi,title,authorships,publication_year,primary_location,host_venue,type",
        "per-page": 1,
    }
    if _OA_EMAIL:
        params["mailto"] = _OA_EMAIL

    async with _OA_SEMAPHORE:
        try:
            async with httpx.AsyncClient(
                headers={"User-Agent": "Aether-Research-Assistant/5.0"},
                timeout=_OA_TIMEOUT,
                follow_redirects=True,
            ) as client:
                resp = await client.get(f"{_OA_BASE}/works", params=params)

            if resp.status_code == 429:
                log.warning("OpenAlex rate limited (429) — skipping.")
                return None
            if resp.status_code != 200:
                log.warning(f"OpenAlex returned HTTP {resp.status_code} for title: {title!r}")
                return None

            data = resp.json()
            results = data.get("results") or []
            if not results:
                return None

            work = results[0]

            # Guard: reject if the title similarity is very low (wrong paper)
            returned_title = (work.get("title") or "").strip()
            if returned_title and normalize_title(returned_title) != normalize_title(title):
                # Allow partial match: the normalised query must be a substring of
                # the returned title or vice versa (handles subtitle truncation).
                nt_query = normalize_title(title)
                nt_result = normalize_title(returned_title)
                if nt_query not in nt_result and nt_result not in nt_query:
                    log.debug(
                        f"OpenAlex title mismatch — query: {title!r}, got: {returned_title!r}"
                    )
                    return None

            raw_doi: str = work.get("doi") or ""
            # OpenAlex returns full DOI URL, strip the prefix
            doi = re.sub(r"^https?://doi\.org/", "", raw_doi).strip()

            return {
                "authors": _extract_authors(work),
                "venue": _extract_venue(work),
                "year": work.get("publication_year"),
                "doi": doi,
                "entry_type": _infer_entry_type(work),
                "openalex_id": work.get("id", ""),
            }

        except httpx.TimeoutException:
            log.warning(f"OpenAlex timeout for title: {title!r}")
            return None
        except Exception as exc:
            log.warning(f"OpenAlex search error for {title!r}: {exc}")
            return None


async def enrich_arxiv_papers_with_openalex(
    arxiv_papers: List[Dict],
) -> List[Dict]:
    """
    Given a list of arXiv paper dicts, enrich each with OpenAlex data
    (authors, venue, year, DOI, entry type, openalex_id).

    Returns a NEW list with merged data. Falls back gracefully per paper.
    """
    if not arxiv_papers:
        return []

    async def _enrich_one(paper: Dict) -> Dict:
        title = paper.get("title", "")
        if not title:
            return paper

        oa = await search_openalex(title)
        if not oa:
            return paper

        # Merge: existing data takes precedence, but OpenAlex fills in missing fields
        # OpenAlex adds: authors (if missing), venue, year, doi, doi_url, entry_type, openalex_id
        enriched = {**paper}
        if not enriched.get("authors") and oa.get("authors"):
            enriched["authors"] = oa["authors"]
        if not enriched.get("venue") and oa.get("venue"):
            enriched["venue"] = oa["venue"]
        if not enriched.get("year") and oa.get("year"):
            enriched["year"] = oa["year"]
        if not enriched.get("doi") and oa.get("doi"):
            enriched["doi"] = oa["doi"]
            enriched["doi_url"] = f"https://doi.org/{oa['doi']}"
        if not enriched.get("entry_type") and oa.get("entry_type"):
            enriched["entry_type"] = oa["entry_type"]
        if not enriched.get("openalex_id") and oa.get("openalex_id"):
            enriched["openalex_id"] = oa["openalex_id"]

        return enriched

    results = await asyncio.gather(*[_enrich_one(p) for p in arxiv_papers])
    return list(results)
