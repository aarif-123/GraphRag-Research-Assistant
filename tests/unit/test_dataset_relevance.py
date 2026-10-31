from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.sources import kaggle
from app.utils import misc
from app.utils.dataset_relevance import dataset_relevance_score


class FakeResponse:
    status_code = 200

    def __init__(self, data: list[dict[str, Any]]) -> None:
        self._data = data

    def json(self) -> list[dict[str, Any]]:
        return self._data


class FakeAsyncClient:
    def __init__(self, data: list[dict[str, Any]], **kwargs: Any) -> None:
        self.response = FakeResponse(data)

    async def __aenter__(self) -> FakeAsyncClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    async def get(self, *args: Any, **kwargs: Any) -> FakeResponse:
        return self.response


def test_dataset_relevance_requires_multiple_topic_terms() -> None:
    query = "find datasets for spiking neural networks"

    assert (
        dataset_relevance_score(
            query,
            {"name": "Spiking Neural Network Benchmark"},
        )
        == 1.0
    )
    assert dataset_relevance_score(query, {"name": "Piano MIDI collection"}) == 0.0
    assert (
        dataset_relevance_score(
            query,
            {"name": "Neuroscience collection", "tags": ["spiking neural networks"]},
        )
        == 1.0
    )


@pytest.mark.asyncio
async def test_huggingface_search_filters_unrelated_datasets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = [
        {"id": "someone/piano-midi", "tags": ["music", "audio"]},
        {
            "id": "research/spiking-neural-network-data",
            "tags": ["neuroscience"],
            "downloads": 24,
            "likes": 2,
        },
    ]
    monkeypatch.setattr(misc.httpx, "AsyncClient", lambda **kwargs: FakeAsyncClient(data, **kwargs))
    monkeypatch.setattr(misc, "get_cache", lambda *args: None)
    monkeypatch.setattr(misc, "set_cache", lambda *args: None)

    results = await misc.search_huggingface_datasets("spiking neural networks")

    assert [result["name"] for result in results] == ["research/spiking-neural-network-data"]
    assert results[0]["relevance_label"] == (
        "Hugging Face search match for: spiking neural networks"
    )


@pytest.mark.asyncio
async def test_kaggle_bulk_search_filters_unrelated_datasets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = [
        {"title": "Piano MIDI collection", "ref": "someone/piano-midi", "voteCount": 1000},
        {
            "title": "Spiking neural network benchmark",
            "ref": "research/spiking-neural-network-data",
            "subtitle": "Neuroscience recordings",
            "voteCount": 10,
        },
    ]
    monkeypatch.setenv("KAGGLE_USERNAME", "test-user")
    monkeypatch.setenv("KAGGLE_KEY", "test-key")
    monkeypatch.setattr(
        kaggle.httpx, "AsyncClient", lambda **kwargs: FakeAsyncClient(data, **kwargs)
    )
    kaggle._KAGGLE_CACHE.clear()

    results = await kaggle.search_kaggle_datasets_bulk("spiking neural networks")

    assert [result["name"] for result in results] == ["Spiking neural network benchmark"]
    assert results[0]["relevance_score"] == 1.0
    assert results[0]["relevance_label"] == ("Kaggle search match for: spiking neural networks")


@pytest.mark.asyncio
async def test_retrieval_keeps_paper_provenance_and_search_labels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paper = {
        "title": "A study of spiking neural network benchmarks",
        "url": "https://arxiv.org/abs/2501.12345",
        "datasets": [
            {
                "name": "research/spikebench",
                "url": "https://huggingface.co/datasets/research/spikebench",
                "source": "huggingface_linked",
            }
        ],
        "code_repos": [],
    }
    second_paper = {
        "title": "Another spiking neural network study",
        "url": "https://arxiv.org/abs/2502.12345",
        "datasets": [
            {
                "name": "research/spikebench",
                "url": "https://huggingface.co/datasets/research/spikebench",
                "source": "huggingface_linked",
            }
        ],
        "code_repos": [],
    }

    async def identity_enrichment(papers: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return papers

    async def kaggle_search(query: str, **kwargs: Any) -> list[dict[str, Any]]:
        return []

    async def huggingface_search(query: str, **kwargs: Any) -> list[dict[str, Any]]:
        if query == "spiking neural networks":
            return [
                {
                    "name": "research/spiking-neural-data",
                    "url": "https://huggingface.co/datasets/research/spiking-neural-data",
                    "source": "huggingface_search",
                    "relevance_label": f"Hugging Face search match for: {query}",
                    "discovery_query": query,
                    "relevance_score": 1.0,
                }
            ]
        return []

    monkeypatch.setattr(
        "app.sources.papers_with_code.enrich_arxiv_papers_with_pwc",
        identity_enrichment,
    )
    kaggle_search_mock = AsyncMock(side_effect=kaggle_search)
    huggingface_search_mock = AsyncMock(side_effect=huggingface_search)
    monkeypatch.setattr(kaggle, "search_kaggle_datasets_bulk", kaggle_search_mock)
    monkeypatch.setattr(misc, "search_huggingface_datasets", huggingface_search_mock)

    datasets, _ = await misc.retrieve_datasets_and_repos(
        "spiking neural networks",
        [paper, second_paper],
        [],
        [],
    )

    linked_dataset = next(item for item in datasets if item["name"] == "research/spikebench")
    assert linked_dataset["paper_references"] == [
        {
            "title": paper["title"],
            "url": paper["url"],
            "relationship": "Linked by paper",
        },
        {
            "title": second_paper["title"],
            "url": second_paper["url"],
            "relationship": "Linked by paper",
        },
    ]
    discovered_dataset = next(
        item for item in datasets if item["name"] == "research/spiking-neural-data"
    )
    assert discovered_dataset["relevance_label"] == (
        "Hugging Face search match for: spiking neural networks"
    )
    assert any(
        call.args[0] == "spiking neural networks" for call in kaggle_search_mock.await_args_list
    )
