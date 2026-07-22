"""
Shared pytest fixtures for unit tests.

All fixtures here are pure in-process — no real network or database I/O.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Sample data fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def sample_paper() -> Dict[str, Any]:
    """A minimal valid paper dict matching the internal schema."""
    return {
        "id": "2301.00001",
        "title": "Attention Is All You Need",
        "authors": ["Ashish Vaswani", "Noam Shazeer"],
        "year": 2017,
        "abstract": "We propose a new simple network architecture, the Transformer.",
        "url": "https://arxiv.org/abs/1706.03762",
        "pdf_url": "https://arxiv.org/pdf/1706.03762.pdf",
        "doi": "10.48550/arXiv.1706.03762",
    }


@pytest.fixture()
def sample_paper_list(sample_paper: Dict[str, Any]) -> List[Dict[str, Any]]:
    second = {
        "id": "2301.00002",
        "title": "BERT: Pre-training of Deep Bidirectional Transformers",
        "authors": ["Jacob Devlin", "Ming-Wei Chang"],
        "year": 2018,
        "abstract": "We introduce BERT.",
        "url": "https://arxiv.org/abs/1810.04805",
        "pdf_url": "",
        "doi": "",
    }
    return [sample_paper, second]


@pytest.fixture()
def raw_s2_paper() -> Dict[str, Any]:
    """Raw Semantic Scholar API response for a single paper."""
    return {
        "paperId": "abc123",
        "title": "Graph Neural Networks: A Review",
        "authors": [{"name": "Alice"}, {"name": "Bob"}],
        "year": 2020,
        "abstract": "A comprehensive review of GNNs.",
        "citationCount": 500,
        "influentialCitationCount": 50,
        "openAccessPdf": {"url": "https://example.com/paper.pdf"},
        "externalIds": {"ArXiv": "2001.00001", "DOI": "10.1234/foo"},
        "tldr": {"text": "A review of GNNs"},
        "venue": "ICML",
        "publicationVenue": None,
        "fieldsOfStudy": ["Computer Science"],
        "publicationTypes": ["JournalArticle"],
    }


@pytest.fixture()
def raw_core_work() -> Dict[str, Any]:
    """Raw CORE API v3 work record."""
    return {
        "id": 999,
        "title": "Deep Learning Fundamentals",
        "authors": [{"name": "Alice Smith"}, {"name": "Bob Jones"}],
        "abstract": "An introduction to deep learning.",
        "publishedDate": "2022-06-15",
        "downloadUrl": "https://core.ac.uk/download/pdf/999.pdf",
        "doi": "10.9999/dl-fundamentals",
    }


# ---------------------------------------------------------------------------
# Mock client fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_supabase_client() -> MagicMock:
    """A MagicMock that mimics the supabase Python client's fluent interface."""
    client = MagicMock()
    # .rpc(...).execute() → returns data=[]
    rpc_result = MagicMock()
    rpc_result.data = []
    client.rpc.return_value.execute.return_value = rpc_result
    return client


@pytest.fixture()
def mock_neo4j_driver() -> MagicMock:
    """A MagicMock that mimics the neo4j Driver with a session context manager."""
    driver = MagicMock()
    session = MagicMock()
    driver.session.return_value.__enter__ = MagicMock(return_value=session)
    driver.session.return_value.__exit__ = MagicMock(return_value=False)
    return driver


@pytest.fixture()
def mock_groq_response() -> Dict[str, Any]:
    return {"choices": [{"message": {"content": "This is a mocked LLM answer."}}]}


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------


@pytest.fixture()
def frozen_time(monkeypatch: pytest.MonkeyPatch):
    """Freeze time.time() at a fixed value for deterministic TTL tests."""
    fixed = 1_700_000_000.0

    monkeypatch.setattr(time, "time", lambda: fixed)
    return fixed
