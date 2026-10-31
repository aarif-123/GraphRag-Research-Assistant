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
        await groq.groq_chat([{"role": "user", "content": "Question"}], "test-model", retries=0)


@pytest.mark.asyncio
async def test_verifier_includes_external_paper_abstracts(monkeypatch):
    llm = AsyncMock(
        return_value=(
            "CONFIDENCE: 0.9\n"
            "VERIFIED_CLAIMS: 1\n"
            "TOTAL_CLAIMS: 1\n"
            "FLAGGED:\n- None\n"
            "VERDICT: PASS"
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
