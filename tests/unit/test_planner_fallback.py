from unittest.mock import AsyncMock

import pytest

from app.core import planner
from app.core.exceptions import LLMError


def test_planner_parser_accepts_json_fence_and_surrounding_text():
    plan = planner._parse_planner_response(
        'Plan:\n```json\n{"route": "compare", "vector_keywords": ["GraphRAG"]}\n```'
    )

    assert plan["route"] == "compare"
    assert plan["vector_keywords"] == ["GraphRAG"]


@pytest.mark.asyncio
async def test_planner_failure_builds_keyword_search_fallback(monkeypatch):
    groq_call = AsyncMock(side_effect=LLMError("planner unavailable"))
    monkeypatch.setattr(planner, "groq_chat", groq_call)
    monkeypatch.setattr(planner, "get_cache", lambda *args: None)
    monkeypatch.setattr(planner, "set_cache", lambda *args: None)

    plan = await planner.plan_query(
        "How do Graph Neural Networks improve multi-hop retrieval?"
    )

    assert plan.route == "rag"
    assert plan.vector_keywords[:3] == ["graph", "neural", "networks"]
    assert plan.search_tiers == ["graph neural networks improve multi-hop retrieval"]
    groq_call.assert_awaited_once()
    assert "json_mode" not in groq_call.await_args.kwargs


@pytest.mark.asyncio
async def test_planner_sanitizes_malformed_json_fields(monkeypatch):
    groq_call = AsyncMock(
        return_value='{"route": [], "graph_anchors": "bad", "vector_keywords": ["RAG"]}'
    )
    monkeypatch.setattr(planner, "groq_chat", groq_call)
    monkeypatch.setattr(planner, "get_cache", lambda *args: None)
    monkeypatch.setattr(planner, "set_cache", lambda *args: None)

    plan = await planner.plan_query("Explain RAG")

    assert plan.route == "rag"
    assert plan.graph_anchors == []
    assert plan.vector_keywords == ["RAG"]
