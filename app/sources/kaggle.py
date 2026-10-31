"""
Kaggle Datasets Integration Module
==================================
Queries Kaggle's public datasets search API using user-provided credentials
and enriches extracted datasets with Kaggle references.
"""

import logging
import os
from typing import Any, Dict, List, Optional

import httpx

from app.utils.dataset_relevance import dataset_relevance_score

log = logging.getLogger("aether.kaggle")

# In-memory cache to minimize API calls
_KAGGLE_CACHE: Dict[str, Any] = {}


def _get_kaggle_cache(key: str) -> Any:
    # Simple cache logic (no expiration for run lifecycle, or simple check)
    return _KAGGLE_CACHE.get(key)


def _set_kaggle_cache(key: str, val: Any) -> None:
    _KAGGLE_CACHE[key] = val


async def search_kaggle_dataset(query: str) -> Optional[Dict[str, Any]]:
    """
    Search Kaggle for a dataset and return the most relevant result.
    """
    username = os.getenv("KAGGLE_USERNAME")
    key = os.getenv("KAGGLE_KEY")

    if not username or not key:
        log.warning("Kaggle credentials not configured in environment variables.")
        return None

    clean_query = query.strip()
    if not clean_query:
        return None

    cache_key = f"kaggle_relevance_v2_{clean_query.lower()}"
    cached = _get_kaggle_cache(cache_key)
    if cached is not None:
        return cached

    url = "https://www.kaggle.com/api/v1/datasets/list"
    params = {"search": clean_query}

    headers = {"User-Agent": "Aether-Research-Assistant/5.0 (contact@aether-assistant.org)"}

    try:
        async with httpx.AsyncClient(headers=headers, timeout=3.5) as client:
            resp = await client.get(url, params=params, auth=(username, key))
            if resp.status_code != 200:
                log.warning(
                    f"Kaggle search returned status code {resp.status_code} for query '{clean_query}'"
                )
                _set_kaggle_cache(cache_key, None)
                return None

            data = resp.json()
            if not isinstance(data, list) or not data:
                log.info(f"No Kaggle datasets found for query '{clean_query}'")
                _set_kaggle_cache(cache_key, None)
                return None

            # Filter on dataset metadata before popularity is used as a tie-breaker.
            best_match: Optional[Dict[str, Any]] = None
            best_score = 0.0

            for item in data[:10]:  # Check top 10 results
                title = item.get("title", "")
                ref = item.get("ref", "")
                vote_count = item.get("voteCount", 0)
                relevance = dataset_relevance_score(
                    clean_query,
                    {
                        "title": title,
                        "ref": ref,
                        "subtitle": item.get("subtitle", ""),
                        "tags": item.get("tags", []),
                    },
                )
                if relevance == 0:
                    continue

                if relevance > best_score or (
                    relevance == best_score and vote_count > (best_match or {}).get("vote_count", 0)
                ):
                    best_score = relevance
                    best_match = {
                        "title": title,
                        "ref": ref,
                        "url": item.get("url", f"https://www.kaggle.com/datasets/{ref}"),
                        "vote_count": vote_count,
                        "subtitle": item.get("subtitle", ""),
                        "relevance_score": relevance,
                        "discovery_query": clean_query,
                    }

            _set_kaggle_cache(cache_key, best_match)
            return best_match

    except Exception as e:
        log.error(f"Error querying Kaggle API for query '{clean_query}': {e}")
        _set_kaggle_cache(cache_key, None)
        return None


async def enrich_datasets_with_kaggle(datasets: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Enrich a list of dataset dictionaries with Kaggle dataset URLs and metadata.
    """
    if not datasets:
        return datasets

    import asyncio

    async def enrich_single(ds: Dict[str, Any]) -> Dict[str, Any]:
        name = ds.get("name")
        if not name:
            return ds

        # If it's already a Kaggle dataset or already enriched, skip searching
        if (
            ds.get("source") == "kaggle_search"
            or "kaggle.com" in (ds.get("url") or "")
            or ds.get("kaggle_url")
        ):
            return ds

        try:
            # Add a strict timeout of 2.0 seconds for single dataset enrichment
            kaggle_res = await asyncio.wait_for(search_kaggle_dataset(name), timeout=2.0)
            if kaggle_res:
                ds["kaggle_url"] = kaggle_res["url"]
                ds["kaggle_title"] = kaggle_res["title"]
                ds["kaggle_votes"] = kaggle_res["vote_count"]
                ds["kaggle_subtitle"] = kaggle_res["subtitle"]
        except Exception as e:
            log.warning(f"Failed to enrich dataset '{name}' with Kaggle: {e}")
        return ds

    # Limit to top 3 datasets for enrichment to prevent rate limit issues
    datasets_to_enrich = datasets[:3]
    remaining_datasets = datasets[3:]

    tasks = [enrich_single(ds) for ds in datasets_to_enrich]
    enriched = await asyncio.gather(*tasks)
    return list(enriched) + remaining_datasets


async def search_kaggle_datasets_bulk(query: str, limit: int = 5) -> List[Dict[str, Any]]:
    """
    Search Kaggle for public datasets matching a query and return up to `limit` results.
    """
    username = os.getenv("KAGGLE_USERNAME")
    key = os.getenv("KAGGLE_KEY")

    if not username or not key:
        log.warning("Kaggle credentials not configured in environment variables.")
        return []

    clean_query = query.strip()
    if not clean_query:
        return []

    cache_key = f"kaggle_bulk_relevance_v2_{clean_query.lower()}_{limit}"
    cached = _get_kaggle_cache(cache_key)
    if cached is not None:
        log.info(f"Cache HIT for Kaggle bulk datasets: {clean_query}")
        return cached

    url = "https://www.kaggle.com/api/v1/datasets/list"
    params = {"search": clean_query}

    headers = {"User-Agent": "Aether-Research-Assistant/5.0 (contact@aether-assistant.org)"}

    try:
        async with httpx.AsyncClient(headers=headers, timeout=3.5) as client:
            resp = await client.get(url, params=params, auth=(username, key))
            if resp.status_code != 200:
                return []

            data = resp.json()
            if not isinstance(data, list) or not data:
                return []

            results = []
            for item in data[:10]:  # Look through top 10 results to find matches
                title = item.get("title", "")
                ref = item.get("ref", "")
                relevance = dataset_relevance_score(
                    clean_query,
                    {
                        "title": title,
                        "ref": ref,
                        "subtitle": item.get("subtitle", ""),
                        "tags": item.get("tags", []),
                    },
                )
                if relevance == 0:
                    continue

                results.append(
                    {
                        "name": title or ref.split("/")[-1],
                        "full_name": ref,
                        "url": item.get("url", f"https://www.kaggle.com/datasets/{ref}"),
                        "description": item.get("subtitle", ""),
                        "kaggle_url": item.get("url", f"https://www.kaggle.com/datasets/{ref}"),
                        "kaggle_votes": item.get("voteCount", 0),
                        "source": "kaggle_search",
                        "discovery_query": clean_query,
                        "relevance_score": relevance,
                        "relevance_label": f"Kaggle search match for: {clean_query}",
                    }
                )
            results.sort(
                key=lambda result: (
                    result["relevance_score"],
                    result.get("kaggle_votes", 0),
                ),
                reverse=True,
            )
            results = results[:limit]
            _set_kaggle_cache(cache_key, results)
            return results

    except Exception as e:
        log.error(f"Error querying Kaggle API for bulk query '{clean_query}': {e}")
        return []
