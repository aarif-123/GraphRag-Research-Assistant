from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.clients import groq
from app.core import generation
from app.core.exceptions import LLMError


@pytest.mark.asyncio
async def test_groq_rejects_output_truncated_by_token_limit(monkeypatch):
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {
        "choices": [
            {
                "finish_reason": "length",
                "message": {"content": "An incomplete answer"},
            }
        ]
    }
    http_client = MagicMock()
    http_client.post = AsyncMock(return_value=response)

    monkeypatch.setattr(groq, "GROQ_API_KEYS", ["test-key"])
    monkeypatch.setattr(groq, "GROQ_API_KEY", "test-key")
    monkeypatch.setattr(groq.pool, "groq_http", http_client)

    with pytest.raises(LLMError, match="response was incomplete"):
        await groq.groq_chat(
            [{"role": "user", "content": "Question"}],
            "test-model",
            max_tokens=groq._MAX_OUTPUT_TOKENS,
            retries=0,
        )


@pytest.mark.asyncio
async def test_groq_retries_truncated_output_once_with_larger_budget(monkeypatch):
    truncated = MagicMock()
    truncated.status_code = 200
    truncated.json.return_value = {
        "choices": [{"finish_reason": "length", "message": {"content": "Partial"}}]
    }
    complete = MagicMock()
    complete.status_code = 200
    complete.json.return_value = {
        "choices": [{"finish_reason": "stop", "message": {"content": "Complete answer"}}]
    }
    http_client = MagicMock()
    http_client.post = AsyncMock(side_effect=[truncated, complete])

    monkeypatch.setattr(groq, "GROQ_API_KEYS", ["test-key"])
    monkeypatch.setattr(groq, "GROQ_API_KEY", "test-key")
    monkeypatch.setattr(groq.pool, "groq_http", http_client)

    answer = await groq.groq_chat(
        [{"role": "user", "content": "Question"}], "test-model", max_tokens=1000, retries=0
    )

    assert answer == "Complete answer"
    assert [call.kwargs["json"]["max_tokens"] for call in http_client.post.await_args_list] == [
        1000,
        2000,
    ]


@pytest.mark.asyncio
async def test_verifier_includes_external_paper_abstracts(monkeypatch):
    llm = AsyncMock(
        return_value=(
            "CONFIDENCE: 0.9\nVERIFIED_CLAIMS: 1\nTOTAL_CLAIMS: 1\nFLAGGED:\n- None\nVERDICT: PASS"
        )
    )
    monkeypatch.setattr(generation, "groq_chat", llm)

    await generation.verify_answer(
        "The paper studies graph retrieval.",
        [],
        "test-model",
        arxiv_papers=[
            {
                "title": "Graph Retrieval Study",
                "abstract": "The paper studies graph retrieval.",
            }
        ],
    )

    prompt = llm.await_args.args[0][0]["content"]
    assert "[ArXiv] Graph Retrieval Study" in prompt
    assert "The paper studies graph retrieval." in prompt
