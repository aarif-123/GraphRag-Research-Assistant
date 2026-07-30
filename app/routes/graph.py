"""
routes/graph.py — Graph intelligence endpoints: paper lookup, author network,
citation path, paper comparison, and trending papers.
"""

import asyncio

from fastapi import APIRouter, HTTPException, Request

from app.clients.pool import pool
from app.utils.credits import check_rate_limit, require_pro
from app.core.graph import (
    get_paper_full,
    get_author_network,
    get_citation_path,
    get_trending_papers,
    retrieve_graph_papers,
    get_co_citation_cluster,
)
from app.core.retrieval import (
    vector_search,
    run_vector_pipeline,
    retrieve_arxiv_context,
    format_arxiv_context,
    format_s2_context,
    format_pwc_context,
    build_chronological_flow,
)
from app.core.generation import compare_prompt
from app.core.exceptions import GraphRetrievalError, EmbeddingError, LLMError
from app.clients.groq import groq_chat, create_embedding
from app.models.research import CompareRequest, CitationPathRequest
from app.config import REASON_MODEL, HEAVY_MODEL, log

router = APIRouter(prefix="/api/graph")


@router.get("/paper/{paper_id}")
async def get_paper(paper_id: str, request: Request):
    pool.assert_ready()
    await check_rate_limit(request.client.host if request.client else "unknown")
    result = await get_paper_full(paper_id)
    if not result:
        raise HTTPException(404, f"Paper '{paper_id}' not found.")
    return result


@router.get("/author/{author_name}")
async def get_author(author_name: str, request: Request):
    pool.assert_ready()
    await check_rate_limit(request.client.host if request.client else "unknown")
    return await get_author_network(author_name)


@router.post("/citation-path")
async def citation_path(req: CitationPathRequest, request: Request):
    pool.assert_ready()
    await check_rate_limit(request.client.host if request.client else "unknown")
    result = await get_citation_path(req.from_paper, req.to_paper)
    return result


@router.get("/trending")
async def trending(limit: int = 10, request: Request = None):
    pool.assert_ready()
    return await get_trending_papers(limit)


@router.post("/compare")
async def compare_papers(req: CompareRequest, request: Request):
    from app.utils.credits import check_and_deduct_credit, append_credits_snapshot

    pool.assert_ready()
    await check_rate_limit(request.client.host if request.client else "unknown")
    await check_and_deduct_credit(request, "compare")

    query = f"Compare {req.paper_a} and {req.paper_b}"
    if req.aspects:
        query += f" focusing on: {', '.join(req.aspects)}"

    try:
        graph_nodes = await retrieve_graph_papers(
            keywords=[req.paper_a, req.paper_b],
            anchors=[req.paper_a, req.paper_b],
            limit=10,
        )
    except GraphRetrievalError:
        graph_nodes = []

    try:
        embedding = await create_embedding(query)
    except EmbeddingError as e:
        raise HTTPException(502, str(e))

    chunks = await run_vector_pipeline(query, embedding, 10, 0.25, [], "compare")
    arxiv_papers = await retrieve_arxiv_context(query, limit=5)

    prompt = compare_prompt(query, chunks, graph_nodes, arxiv_papers)
    try:
        model = HEAVY_MODEL if req.temperature > 0 else REASON_MODEL
        answer = await groq_chat(
            [{"role": "user", "content": prompt}],
            model,
            temperature=req.temperature,
            max_tokens=2000,
        )
    except LLMError as e:
        raise HTTPException(502, str(e))

    res = {
        "query": query,
        "answer": answer,
        "papers": graph_nodes,
        "arxiv_papers": arxiv_papers,
    }
    return await append_credits_snapshot(res, request)
