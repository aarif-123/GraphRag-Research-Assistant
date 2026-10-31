from unittest.mock import AsyncMock

import pytest

from app.core import generation
from app.core.generation import compare_prompt, grounded_prompt


def test_focused_grounded_prompt_does_not_require_long_sections_or_invented_metrics():
    prompt = grounded_prompt(
        "How does GraphRAG compare with vector RAG for multi-hop reasoning?",
        [],
        [],
        [],
        [],
    )

    assert (
        "report metrics and benchmarks only when the retrieved evidence explicitly provides them"
        in prompt.lower()
    )
    assert "in at most about 600 words" in prompt
    assert "Include a detailed Mermaid tree or flowchart" not in prompt


def test_grounded_prompt_includes_high_depth_requirements_and_primary_passages():
    prompt = grounded_prompt(
        "Explain the mechanism in detail.",
        [],
        [],
        depth="high",
        requirements=["Explain the quantization mechanism."],
        primary_passages=["[Example paper]\nPrimary-source evidence."],
    )

    assert "mechanism-focused explanation" in prompt
    assert "Explain the quantization mechanism." in prompt
    assert "[Example paper]\nPrimary-source evidence." in prompt


def test_focused_comparison_prompt_allows_missing_evidence_to_be_reported():
    prompt = compare_prompt("Compare GraphRAG and vector RAG.", [], [], [], [])

    assert "say so instead of inventing details" in prompt
    assert "Do not force a timeline, diagram, benchmark, or follow-up section" in prompt


def test_extract_verifiable_claims_keeps_complete_years():
    assert "2024" in generation.extract_verifiable_claims("Published in 2024.")


@pytest.mark.asyncio
async def test_skipped_verification_is_reported_in_answer_and_warning(monkeypatch):
    monkeypatch.setattr(
        generation,
        "verify_answer",
        AsyncMock(
            return_value={
                "confidence": None,
                "verdict": "SKIPPED",
                "error": "verification unavailable",
            }
        ),
    )

    answer, verification, warning = await generation.apply_verification(
        "A short answer.", [], "test-model", "verification-test"
    )

    assert verification["verdict"] == "SKIPPED"
    assert "treat this response as unverified" in answer
    assert warning == "Answer verification could not be completed."


@pytest.mark.asyncio
async def test_partial_verification_is_reported_even_with_high_confidence(monkeypatch):
    monkeypatch.setattr(
        generation,
        "verify_answer",
        AsyncMock(
            return_value={
                "confidence": 0.92,
                "verdict": "PARTIAL",
                "flagged_claims": ["A claim without source support."],
            }
        ),
    )

    answer, verification, warning = await generation.apply_verification(
        "A short answer.", [], "test-model", "partial-verification-test"
    )

    assert verification["confidence"] == 0.92
    assert "Grounding Warning" in answer
    assert "A claim without source support." in answer
    assert warning == "Some statements in the answer could not be fully grounded in retrieved sources."
