"""
Root pytest configuration and shared fixtures for unit and integration test suites.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest


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
