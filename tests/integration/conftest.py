"""
Shared fixtures for integration tests.

All HTTP calls are mocked using pytest-mock / unittest.mock so no real
network requests are made during CI runs.
"""

from __future__ import annotations

import json
from typing import Any, Dict
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# HTTP response factory
# ---------------------------------------------------------------------------


def make_mock_response(
    status_code: int = 200,
    json_data: Any = None,
    text: str = "",
) -> MagicMock:
    """Build a mock httpx.Response-like object."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text or (json.dumps(json_data) if json_data else "")
    resp.json = MagicMock(return_value=json_data or {})
    resp.content = (resp.text or "").encode()
    return resp


# ---------------------------------------------------------------------------
# Sample API response payloads
# ---------------------------------------------------------------------------


@pytest.fixture()
def core_search_response() -> Dict[str, Any]:
    return {
        "results": [
            {
                "id": 111,
                "title": "Transformers in NLP",
                "authors": [{"name": "Alice"}],
                "abstract": "A survey of transformer models.",
                "publishedDate": "2021-05-10",
                "downloadUrl": "https://core.ac.uk/download/111.pdf",
                "doi": "10.111/transformers",
            },
            {
                "id": 222,
                "title": "BERT Pre-training",
                "authors": [{"name": "Bob"}],
                "abstract": "BERT model paper.",
                "publishedDate": "2019-10-01",
                "downloadUrl": "https://core.ac.uk/download/222.pdf",
                "doi": "",
            },
        ],
        "totalHits": 2,
    }


@pytest.fixture()
def s2_search_response() -> Dict[str, Any]:
    return {
        "data": [
            {
                "paperId": "s2-001",
                "title": "Graph Neural Networks",
                "authors": [{"name": "Alice"}],
                "year": 2020,
                "abstract": "GNNs for node classification.",
                "citationCount": 300,
                "influentialCitationCount": 30,
                "openAccessPdf": {"url": "https://example.com/gnn.pdf"},
                "externalIds": {"ArXiv": "2001.00100", "DOI": ""},
                "tldr": {"text": "GNN survey"},
                "venue": "ICLR",
                "publicationVenue": None,
                "fieldsOfStudy": ["Computer Science"],
                "publicationTypes": [],
            }
        ],
        "total": 1,
        "offset": 0,
        "next": None,
    }


@pytest.fixture()
def wikipedia_search_response() -> Dict[str, Any]:
    return {"query": {"search": [{"title": "Transformer (machine learning model)", "pageid": 42}]}}


@pytest.fixture()
def wikipedia_summary_response() -> Dict[str, Any]:
    return {
        "title": "Transformer (machine learning model)",
        "displaytitle": "Transformer",
        "extract": "The Transformer is a deep learning model.",
        "description": "Machine learning architecture",
        "content_urls": {
            "desktop": {
                "page": "https://en.wikipedia.org/wiki/Transformer_(machine_learning_model)"
            }
        },
        "thumbnail": {"source": ""},
    }
