from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from starlette.requests import Request

from app.models.chat import ChatMessage, ConversationRequest
from app.models.research import QueryPlan, ResearchRequest
from app.routes import chat, health, research


@pytest.mark.asyncio
async def test_chat_refuses_to_generate_when_all_retrieval_sources_are_empty(monkeypatch):
    async def empty_results(*args, **kwargs):
        return []

    monkeypatch.setattr(chat.pool, "assert_ready", lambda: None)
    monkeypatch.setattr(chat, "check_rate_limit", AsyncMock())
    monkeypatch.setattr(chat, "set_user_context", AsyncMock())
    monkeypatch.setattr(chat, "get_user_plan", AsyncMock(return_value={"plan": "free"}))
    monkeypatch.setattr(chat, "check_and_deduct_credit", AsyncMock())
    monkeypatch.setattr(
        chat,
        "plan_query",
        AsyncMock(
            return_value=QueryPlan(
                standalone_query="GraphRAG multi-hop reasoning",
                route="rag",
            )
        ),
    )
    monkeypatch.setattr(chat, "get_relevant_pdf_chunks", AsyncMock(return_value=[]))
    monkeypatch.setattr(chat, "retrieve_graph_papers", empty_results)
    monkeypatch.setattr(chat, "create_embedding", AsyncMock(return_value=[0.0] * 768))
    monkeypatch.setattr(chat, "run_vector_pipeline", empty_results)
    monkeypatch.setattr(chat, "retrieve_arxiv_context", empty_results)
    monkeypatch.setattr(chat, "search_papers_s2", empty_results)
    monkeypatch.setattr(chat, "search_core_papers", empty_results)
    llm = AsyncMock()
    monkeypatch.setattr(chat, "groq_chat", llm)

    request = Request(
        {"type": "http", "method": "POST", "headers": [], "client": ("127.0.0.1", 54321)}
    )
    request.state.request_id = "no-evidence-test"
    response = await chat._chat_impl(
        ConversationRequest(
            messages=[ChatMessage(role="user", content="How does GraphRAG support multi-hop QA?")],
            mode="research",
        ),
        request,
    )

    assert "couldn't retrieve research sources" in response["answer"]
    assert response["warning"]
    assert response["verification"] is None
    assert response["arxiv_papers"] == []
    assert response["s2_papers"] == []
    assert response["chunks"] == []
    llm.assert_not_awaited()


@pytest.mark.asyncio
async def test_research_refuses_to_generate_when_all_retrieval_sources_are_empty(monkeypatch):
    async def empty_results(*args, **kwargs):
        return []

    monkeypatch.setattr(research.pool, "assert_ready", lambda: None)
    monkeypatch.setattr(research, "check_rate_limit", AsyncMock())
    monkeypatch.setattr(research, "set_user_context", AsyncMock())
    monkeypatch.setattr(research, "get_user_plan", AsyncMock(return_value={"plan": "free"}))
    monkeypatch.setattr(research, "check_and_deduct_credit", AsyncMock())
    monkeypatch.setattr(
        research,
        "plan_query",
        AsyncMock(
            return_value=QueryPlan(
                standalone_query="GraphRAG multi-hop reasoning",
                route="rag",
            )
        ),
    )
    monkeypatch.setattr(research, "retrieve_graph_papers", empty_results)
    monkeypatch.setattr(research, "create_embedding", AsyncMock(return_value=[0.0] * 768))
    monkeypatch.setattr(research, "run_vector_pipeline", empty_results)
    monkeypatch.setattr(research, "retrieve_arxiv_context", empty_results)
    monkeypatch.setattr(research, "search_papers_s2", empty_results)
    monkeypatch.setattr(research, "search_core_papers", empty_results)
    llm = AsyncMock()
    monkeypatch.setattr(research, "groq_chat", llm)

    request = Request(
        {"type": "http", "method": "POST", "headers": [], "client": ("127.0.0.1", 54321)}
    )
    request.state.request_id = "research-no-evidence-test"
    response = await research._research_impl(
        ResearchRequest(query="How does GraphRAG support multi-hop QA?"),
        request,
    )

    assert "couldn't retrieve research sources" in response["answer"]
    assert response["warning"]
    assert response["verification"] is None
    assert response["papers"] == []
    assert response["arxiv_papers"] == []
    assert response["s2_papers"] == []
    llm.assert_not_awaited()


def test_public_config_never_returns_the_service_role_key(monkeypatch):
    monkeypatch.setattr(health, "SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setattr(health, "SUPABASE_ANON_KEY", "public-anon-key")
    monkeypatch.setattr(health, "SUPABASE_KEY", "private-service-role-key")

    response = health.get_config()

    assert response == {
        "supabase_url": "https://example.supabase.co",
        "supabase_anon_key": "public-anon-key",
    }
    assert "service_role_key" not in str(response).lower()

    monkeypatch.setattr(health, "SUPABASE_ANON_KEY", "private-service-role-key")
    response = health.get_config()
    assert response["supabase_anon_key"] is None


def test_readiness_endpoint_reflects_application_state(monkeypatch):
    monkeypatch.setattr(health.pool, "_ready", False)
    response = health.readiness()
    assert response.status_code == 503

    monkeypatch.setattr(health.pool, "_ready", True)
    response = health.readiness()
    assert response == {"status": "ready", "ready": True}
