import httpx
import logging
import urllib.parse
import asyncio
import time
from typing import List, Dict, Any, Optional

log = logging.getLogger(__name__)

# Cache search and summary results
_WIKI_CACHE = {}
_WIKI_CACHE_TTL = 43200  # 12 hours

def _get_wiki_cache(key: str):
    entry = _WIKI_CACHE.get(key)
    if entry and time.time() - entry[1] < _WIKI_CACHE_TTL:
        return entry[0]
    return None

def _set_wiki_cache(key: str, val):
    _WIKI_CACHE[key] = (val, time.time())

async def search_wikipedia_summary(query: str) -> Optional[Dict[str, Any]]:
    """
    Search Wikipedia for the given query, retrieve the top page, 
    and fetch its summary details.
    """
    if not query or not query.strip():
        return None

    clean_query = query.strip()
    cache_key = f"summary_{clean_query}"
    cached = _get_wiki_cache(cache_key)
    if cached is not None:
        log.debug(f"Wikipedia cache hit for {clean_query}")
        return cached

    # 1. Search for pages matching query using Action API
    search_url = "https://en.wikipedia.org/w/api.php"
    search_params = {
        "action": "query",
        "list": "search",
        "srsearch": clean_query,
        "format": "json",
        "utf8": 1,
        "formatversion": 2
    }

    headers = {
        "User-Agent": "Aether-Research-Assistant/5.0 (contact@aether-assistant.org)"
    }

    try:
        async with httpx.AsyncClient(headers=headers, timeout=8.0) as client:
            resp = await client.get(search_url, params=search_params)
            if resp.status_code != 200:
                log.warning(f"Wikipedia search returned status code {resp.status_code}")
                return None

            data = resp.json()
            search_results = data.get("query", {}).get("search", [])
            if not search_results:
                log.info(f"No Wikipedia pages found for query '{clean_query}'")
                return None

            # Get the top search result title
            top_title = search_results[0].get("title")
            if not top_title:
                return None

            # 2. Fetch page summary using REST API
            encoded_title = urllib.parse.quote(top_title)
            summary_url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{encoded_title}"
            
            resp = await client.get(summary_url)
            if resp.status_code != 200:
                log.warning(f"Wikipedia REST summary returned status code {resp.status_code} for {top_title}")
                return None

            summary_data = resp.json()
            wiki_url = summary_data.get("content_urls", {}).get("desktop", {}).get("page", "")
            if not wiki_url:
                wiki_url = f"https://en.wikipedia.org/wiki/{encoded_title}"

            result = {
                "title": summary_data.get("title", top_title),
                "displaytitle": summary_data.get("displaytitle", top_title),
                "extract": summary_data.get("extract", ""),
                "description": summary_data.get("description", ""),
                "url": wiki_url,
                "thumbnail": summary_data.get("thumbnail", {}).get("source", "")
            }

            _set_wiki_cache(cache_key, result)
            return result

    except Exception as e:
        log.warning(f"Error searching Wikipedia for '{clean_query}': {e}")
        return None

async def enrich_datasets_with_wikipedia(datasets: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Enrich a list of dataset dictionaries with Wikipedia URLs and descriptions.
    Runs concurrently with a semaphore to prevent spamming.
    """
    if not datasets:
        return []

    sem = asyncio.Semaphore(5)

    async def _enrich_one(ds: Dict[str, Any]) -> Dict[str, Any]:
        name = ds.get("name", "")
        if not name:
            return ds

        async with sem:
            # Try searching with "{name} dataset" first to get specific matches
            wiki_res = await search_wikipedia_summary(f"{name} dataset")
            if not wiki_res:
                wiki_res = await search_wikipedia_summary(name)

            if wiki_res:
                # Basic relevance filtering
                title_lower = wiki_res["title"].lower()
                name_lower = name.lower()
                extract_lower = wiki_res["extract"].lower()

                # Either the name should match or the summary/title should refer to data concepts
                words_match = name_lower in title_lower or any(word in title_lower for word in name_lower.split())
                is_dataset_related = any(
                    term in extract_lower or term in title_lower 
                    for term in ["dataset", "data", "corpus", "benchmark", "database", "collection", "image", "text", "speech"]
                )

                if words_match or is_dataset_related:
                    enriched_ds = dict(ds)
                    enriched_ds["wikipedia_url"] = wiki_res["url"]
                    # Update description if it's empty or very short
                    if wiki_res["extract"] and (not ds.get("description") or len(ds.get("description", "")) < 20):
                        enriched_ds["description"] = wiki_res["extract"]
                    log.info(f"Enriched dataset '{name}' with Wikipedia info from page '{wiki_res['title']}'")
                    return enriched_ds

            return ds

    tasks = [_enrich_one(d) for d in datasets]
    return await asyncio.gather(*tasks)
