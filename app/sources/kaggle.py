"""
Kaggle Datasets Integration Module
==================================
Queries Kaggle's public datasets search API using user-provided credentials
and enriches extracted datasets with Kaggle references.
"""

import os
import httpx
import logging
from typing import List, Dict, Any, Optional

log = logging.getLogger("graphrag.kaggle")

# In-memory TTL cache to minimize API calls
_KAGGLE_CACHE: Dict[str, Any] = {}

def _get_kaggle_cache(key: str) -> Optional[Dict[str, Any]]:
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

    cache_key = f"kaggle_{clean_query.lower()}"
    cached = _get_kaggle_cache(cache_key)
    if cached is not None:
        return cached

    url = "https://www.kaggle.com/api/v1/datasets/list"
    params = {"search": clean_query}

    headers = {
        "User-Agent": "Aether-Research-Assistant/5.0 (contact@aether-assistant.org)"
    }

    try:
        async with httpx.AsyncClient(headers=headers, timeout=8.0) as client:
            resp = await client.get(url, params=params, auth=(username, key))
            if resp.status_code != 200:
                log.warning(f"Kaggle search returned status code {resp.status_code} for query '{clean_query}'")
                _set_kaggle_cache(cache_key, None)
                return None

            data = resp.json()
            if not isinstance(data, list) or not data:
                log.info(f"No Kaggle datasets found for query '{clean_query}'")
                _set_kaggle_cache(cache_key, None)
                return None

            # Rerank/filter results to find the best match
            best_match = None
            best_score = -1.0

            q_words = set(clean_query.lower().split())

            for item in data[:10]: # Check top 10 results
                title = item.get("title", "")
                ref = item.get("ref", "")
                vote_count = item.get("voteCount", 0)
                
                # Check match score
                title_lower = title.lower()
                ref_lower = ref.lower()

                # Basic score based on word overlap and popularity
                words_in_title = sum(1 for w in q_words if w in title_lower)
                words_in_ref = sum(1 for w in q_words if w in ref_lower)
                overlap = max(words_in_title, words_in_ref)

                # Perfect title match gets bonus
                exact_bonus = 5.0 if clean_query.lower() == title_lower or clean_query.lower() == ref_lower.split('/')[-1] else 0.0
                
                # Normalize vote count influence (cap at 1000 votes for scoring)
                popularity_bonus = min(vote_count / 200.0, 5.0)

                score = (overlap * 2.0) + exact_bonus + popularity_bonus

                if score > best_score:
                    best_score = score
                    best_match = {
                        "title": title,
                        "ref": ref,
                        "url": item.get("url", f"https://www.kaggle.com/datasets/{ref}"),
                        "vote_count": vote_count,
                        "subtitle": item.get("subtitle", "")
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
            
        kaggle_res = await search_kaggle_dataset(name)
        if kaggle_res:
            ds["kaggle_url"] = kaggle_res["url"]
            ds["kaggle_title"] = kaggle_res["title"]
            ds["kaggle_votes"] = kaggle_res["vote_count"]
            ds["kaggle_subtitle"] = kaggle_res["subtitle"]
        return ds

    # Concurrently enrich all datasets
    tasks = [enrich_single(ds) for ds in datasets]
    return list(await asyncio.gather(*tasks))
