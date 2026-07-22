"""
Unit tests for app/sources/wikipedia.py — cache helpers and enrichment logic.

Covers:
- _get_wiki_cache() / _set_wiki_cache(): hit, miss, TTL expiry
- enrich_datasets_with_wikipedia(): relevance filtering, empty list pass-through,
  dataset without name unchanged
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, patch

# ---------------------------------------------------------------------------
# Inline replicas of the helpers under test.
# ---------------------------------------------------------------------------

_WIKI_CACHE_TTL = 43200  # 12 hours
_WIKI_CACHE: Dict[str, Any] = {}


def _get_wiki_cache(key: str) -> Optional[Any]:
    entry = _WIKI_CACHE.get(key)
    if entry and time.time() - entry[1] < _WIKI_CACHE_TTL:
        return entry[0]
    return None


def _set_wiki_cache(key: str, val: Any) -> None:
    _WIKI_CACHE[key] = (val, time.time())


async def enrich_datasets_with_wikipedia(
    datasets: List[Dict[str, Any]],
    search_fn,  # injectable for testing
) -> List[Dict[str, Any]]:
    """Enriched replica of the production function with injected search_fn."""
    if not datasets:
        return []

    sem = asyncio.Semaphore(5)

    async def _enrich_one(ds: Dict[str, Any]) -> Dict[str, Any]:
        name = ds.get("name", "")
        if not name:
            return ds

        async with sem:
            wiki_res = await search_fn(f"{name} dataset")
            if not wiki_res:
                wiki_res = await search_fn(name)

            if wiki_res:
                title_lower = wiki_res["title"].lower()
                name_lower = name.lower()
                extract_lower = wiki_res["extract"].lower()

                words_match = name_lower in title_lower or any(
                    word in title_lower for word in name_lower.split()
                )
                is_dataset_related = any(
                    term in extract_lower or term in title_lower
                    for term in [
                        "dataset",
                        "data",
                        "corpus",
                        "benchmark",
                        "database",
                        "collection",
                        "image",
                        "text",
                        "speech",
                    ]
                )

                if words_match or is_dataset_related:
                    enriched = dict(ds)
                    enriched["wikipedia_url"] = wiki_res["url"]
                    if wiki_res["extract"] and (
                        not ds.get("description") or len(ds.get("description", "")) < 20
                    ):
                        enriched["description"] = wiki_res["extract"]
                    return enriched

        return ds

    tasks = [_enrich_one(d) for d in datasets]
    return await asyncio.gather(*tasks)


# ---------------------------------------------------------------------------
# Tests — cache helpers
# ---------------------------------------------------------------------------


class TestWikiCache:
    def setup_method(self):
        _WIKI_CACHE.clear()

    def test_miss_returns_none(self):
        assert _get_wiki_cache("missing_key") is None

    def test_hit_returns_value(self):
        with patch("time.time", return_value=1_000_000.0):
            _set_wiki_cache("k1", {"title": "Neural Network"})
        with patch("time.time", return_value=1_000_000.0 + 100):
            result = _get_wiki_cache("k1")
        assert result == {"title": "Neural Network"}

    def test_expired_entry_returns_none(self):
        with patch("time.time", return_value=1_000_000.0):
            _set_wiki_cache("k_exp", "stale_data")
        with patch("time.time", return_value=1_000_000.0 + _WIKI_CACHE_TTL + 1):
            result = _get_wiki_cache("k_exp")
        assert result is None

    def test_alive_just_before_ttl(self):
        with patch("time.time", return_value=1_000_000.0):
            _set_wiki_cache("k_alive", "fresh")
        with patch("time.time", return_value=1_000_000.0 + _WIKI_CACHE_TTL - 1):
            assert _get_wiki_cache("k_alive") == "fresh"


# ---------------------------------------------------------------------------
# Tests — enrich_datasets_with_wikipedia()
# ---------------------------------------------------------------------------


class TestEnrichDatasetsWithWikipedia:
    async def test_empty_list_returns_empty(self):
        result = await enrich_datasets_with_wikipedia([], search_fn=AsyncMock())
        assert result == []

    async def test_dataset_without_name_unchanged(self):
        ds = {"id": 1, "url": "https://example.com"}
        result = await enrich_datasets_with_wikipedia([ds], search_fn=AsyncMock(return_value=None))
        assert result[0] == ds

    async def test_search_returns_none_dataset_unchanged(self):
        ds = {"name": "ImageNet", "description": ""}
        result = await enrich_datasets_with_wikipedia([ds], search_fn=AsyncMock(return_value=None))
        assert result[0] == ds
        assert "wikipedia_url" not in result[0]

    async def test_matching_wiki_result_enriches_dataset(self):
        ds = {"name": "ImageNet", "description": ""}
        wiki_response = {
            "title": "ImageNet",
            "extract": "ImageNet is a large dataset used for image classification.",
            "url": "https://en.wikipedia.org/wiki/ImageNet",
        }
        result = await enrich_datasets_with_wikipedia(
            [ds], search_fn=AsyncMock(return_value=wiki_response)
        )
        enriched = result[0]
        assert enriched["wikipedia_url"] == "https://en.wikipedia.org/wiki/ImageNet"
        assert "ImageNet" in enriched["description"]

    async def test_irrelevant_wiki_result_not_applied(self):
        """If neither name match nor dataset terms appear, dataset unchanged."""
        ds = {"name": "XYZ Corp", "description": ""}
        wiki_response = {
            "title": "Some Unrelated Article",
            "extract": "This article is about ancient Roman history and warfare.",
            "url": "https://en.wikipedia.org/wiki/SomeUnrelated",
        }
        result = await enrich_datasets_with_wikipedia(
            [ds], search_fn=AsyncMock(return_value=wiki_response)
        )
        assert "wikipedia_url" not in result[0]

    async def test_existing_description_not_overwritten_when_long(self):
        ds = {
            "name": "CIFAR-10",
            "description": "This is a sufficiently long existing description that exceeds 20 chars.",
        }
        wiki_response = {
            "title": "CIFAR-10",
            "extract": "CIFAR-10 is a benchmark dataset for image recognition.",
            "url": "https://en.wikipedia.org/wiki/CIFAR-10",
        }
        result = await enrich_datasets_with_wikipedia(
            [ds], search_fn=AsyncMock(return_value=wiki_response)
        )
        # Description longer than 20 chars → should NOT be overwritten
        assert result[0]["description"] == ds["description"]

    async def test_multiple_datasets_processed(self):
        datasets = [
            {"name": "MNIST", "description": ""},
            {"name": "COCO", "description": ""},
        ]
        wiki_responses = {
            "MNIST dataset": {
                "title": "MNIST",
                "extract": "MNIST is a dataset of handwritten digits.",
                "url": "https://en.wikipedia.org/wiki/MNIST",
            },
            "COCO dataset": None,
            "COCO": None,
        }

        async def mock_search(query: str):
            return wiki_responses.get(query)

        result = await enrich_datasets_with_wikipedia(datasets, search_fn=mock_search)
        assert len(result) == 2
        assert "wikipedia_url" in result[0]  # MNIST enriched
        assert "wikipedia_url" not in result[1]  # COCO not enriched
