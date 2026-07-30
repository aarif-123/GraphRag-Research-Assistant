"""
routes/research.py — Full research execution endpoints:
  - POST /api/research
  - POST /api/research/timeline
  - POST /api/research/survey
  - POST /api/bulk
"""

import asyncio
import os
import re
import time
import uuid
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException, Request

from app.clients.groq import create_embedding, groq_chat
from app.clients.pool import cache_key, get_cache, pool, set_cache
from app.config import (
    FREE_CREDITS_PER_DAY,
    FREE_TOP_K_MAX,
    HEAVY_MODEL,
    REASON_MODEL,
    RELEVANCE_FLOOR,
    REQUEST_TIMEOUT,
    log,
)
from app.core.document import get_or_parse_pdf_safe, get_relevant_pdf_chunks
from app.core.exceptions import (
    EmbeddingError,
    GraphRetrievalError,
    LLMError,
    VectorSearchError,
)
from app.core.generation import (
    apply_verification,
    compare_prompt,
    conceptual_prompt,
    document_summary_system_instruction,
    document_summary_user_content,
    grounded_prompt,
    survey_prompt,
    timeline_prompt,
)
from app.core.graph import (
    get_co_citation_cluster,
    retrieve_graph_papers,
)
from app.core.planner import (
    check_requirements_covered,
    plan_query,
    read_primary_source_passages,
)
from app.core.retrieval import (
    merge_adjacent_chunks,
    pack_context_within_budget,
    retrieve_arxiv_context,
    run_vector_pipeline,
)
from app.models.research import (
    BulkRequest,
    ResearchRequest,
    SurveyRequest,
    TimelineRequest,
)
from app.utils.auth import set_user_context
from app.utils.credits import (
    append_credits_snapshot,
    check_and_deduct_credit,
    check_rate_limit,
    get_user_plan,
    require_pro,
)
from app.utils.misc import (
    clean_and_resolve_links,
    extract_paper_urls,
    is_simple_link_paste,
    retrieve_datasets_and_repos,
)

# External sources (graceful fallback)
try:
    from app.sources.core import search_core_papers
    from app.sources.kaggle import enrich_datasets_with_kaggle, search_kaggle_dataset
    from app.sources.openalex import enrich_arxiv_papers_with_openalex, search_openalex
    from app.sources.papers_with_code import enrich_arxiv_papers_with_pwc
    from app.sources.semantic_scholar import enrich_arxiv_papers_with_s2, search_papers_s2
    from app.sources.wikipedia import enrich_datasets_with_wikipedia, search_wikipedia_summary

    _SOURCES_AVAILABLE = True
except ImportError as _src_err:
    _SOURCES_AVAILABLE = False
    log.warning(f"External sources unavailable in routes/research: {_src_err}")

    async def enrich_arxiv_papers_with_s2(papers, **kw):
        return papers

    async def search_papers_s2(query, **kw):
        return []

    async def enrich_arxiv_papers_with_pwc(papers, **kw):
        return papers

    async def search_wikipedia_summary(query, **kw):
        return None

    async def enrich_datasets_with_wikipedia(datasets, **kw):
        return datasets

    async def search_kaggle_dataset(query, **kw):
        return None

    async def enrich_datasets_with_kaggle(datasets, **kw):
        return datasets

    async def search_core_papers(query, **kw):
        return []

    async def search_openalex(query, **kw):
        return None

    async def enrich_arxiv_papers_with_openalex(papers, **kw):
        return papers


router = APIRouter()


def _empty_response(rid: str, answer: str, route: str, t0: float) -> Dict:
    return {
        "request_id": rid,
        "answer": answer,
        "route": route,
        "papers": [],
        "chunks": [],
        "verification": None,
        "latency_ms": int((time.time() - t0) * 1000),
        "model_used": "direct-backend",
        "warning": None,
    }


def _direct_response(rid: str, answer: str, route: str, papers: List[Dict], t0: float) -> Dict:
    return {
        "request_id": rid,
        "answer": answer,
        "route": route,
        "papers": papers,
        "chunks": [],
        "verification": {"confidence": 1.0, "verdict": "PASS"},
        "latency_ms": int((time.time() - t0) * 1000),
        "model_used": "direct-backend",
        "warning": None,
    }


@router.post("/api/research")
async def research(req: ResearchRequest, request: Request):
    rid = str(uuid.uuid4())
    request.state.request_id = rid
    try:
        res = await asyncio.wait_for(_research_impl(req, request), timeout=REQUEST_TIMEOUT)
        return await append_credits_snapshot(res, request)
    except asyncio.TimeoutError:
        raise HTTPException(504, f"Timed out after {REQUEST_TIMEOUT}s.")


async def _research_impl(req: ResearchRequest, request: Request):
    pool.assert_ready()
    rid = getattr(request.state, "request_id", "unknown")
    await check_rate_limit(request.client.host if request.client else "unknown")
    await set_user_context(request)
    t0 = time.time()

    # ── Plan enforcement ──────────────────────────────────────────────
    plan_info = await get_user_plan(request)
    user_plan = plan_info.get("plan", "free")

    # Clamp top_k by plan
    if user_plan == "free":
        req.top_k = min(req.top_k, FREE_TOP_K_MAX)
        req.use_heavy = False  # Free users always use REASON_MODEL

    # Deduct credit (raises 402 if exhausted)
    await check_and_deduct_credit(request, "query")
    # ─────────────────────────────────────────────────────────────────

    raw_query = req.resolved_query()

    # Auto-detect wikipedia: / wiki: prefixes to enable instant Wiki mode
    is_wiki_prefix = False
    prefix_query = raw_query.strip()
    if prefix_query.lower().startswith("wikipedia:"):
        prefix_query = prefix_query[len("wikipedia:") :].strip()
        is_wiki_prefix = True
    elif prefix_query.lower().startswith("wiki:"):
        prefix_query = prefix_query[len("wiki:") :].strip()
        is_wiki_prefix = True

    if is_wiki_prefix:
        req.mode = "wikipedia"
        raw_query = prefix_query

    log.info(f"\n{'=' * 70}\n[{rid}] QUERY: {raw_query} (mode: {req.mode})\n{'=' * 70}")

    # ── PDF/arXiv URLs processing in Research ──
    latest_urls = extract_paper_urls(raw_query)
    all_urls = list(dict.fromkeys(latest_urls))

    new_docs = []
    if all_urls:
        for url in all_urls:
            try:
                doc_text, doc_links = await get_or_parse_pdf_safe(url, raise_on_error=True)
                new_docs.append((url, doc_text, doc_links))
            except Exception as e:
                raise HTTPException(400, f"Failed to download/parse PDF from {url}: {str(e)}")

    # Simple paste summarize bypass in Research
    if all_urls and is_simple_link_paste(raw_query, all_urls):
        target_url = all_urls[0]
        try:
            target_text, target_links = await get_or_parse_pdf_safe(target_url, raise_on_error=True)
        except Exception as e:
            raise HTTPException(400, f"Failed to download/parse PDF from {target_url}: {str(e)}")

        system_instruction = document_summary_system_instruction()
        user_content = document_summary_user_content(target_url, target_text, target_links)

        msgs = [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": user_content},
        ]
        try:
            answer = await groq_chat(
                msgs, HEAVY_MODEL, temperature=req.temperature, max_tokens=2500
            )
        except LLMError as e:
            raise HTTPException(502, f"LLM error while generating summary: {str(e)}")

        latency = int((time.time() - t0) * 1000)
        return {
            "request_id": rid,
            "answer": answer,
            "route": "pdf_summary",
            "plan": {
                "standalone_query": raw_query,
                "reasoning_path": f"PDF parsed directly from {target_url}. Generated structured summary.",
            },
            "papers": [],
            "chunks": [],
            "arxiv_papers": [],
            "s2_papers": [],
            "datasets": [],
            "code_repos": [],
            "verification": None,
            "latency_ms": latency,
            "model_used": HEAVY_MODEL,
            "warning": None,
        }

    # Build pdf_context using FAISS vector search
    pdf_context_parts = []
    pdf_chunks_raw = []
    for url in all_urls:
        relevant_chunks = await get_relevant_pdf_chunks(url, raw_query)
        if relevant_chunks:
            pdf_chunks_raw.extend(relevant_chunks)
            chunks_text = "\n\n".join(relevant_chunks)
            pdf_context_parts.append(
                f"━━━ RELEVANT PDF SECTION FOR {url} ━━━\n"
                f"{chunks_text}\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
            )
    pdf_context = "\n\n".join(pdf_context_parts) if pdf_context_parts else ""

    # ── Uploaded PDF Direct QA Route in Research ──
    has_uploaded_pdf = any("/api/pdf/" in url for url in all_urls)
    if has_uploaded_pdf:
        if not pdf_context:
            answer = "The uploaded PDF document(s) could not be read or parsed. Please ensure the PDF is not password-protected, corrupt, or scanned as images without OCR."
            return _empty_response(rid, answer, "pdf_qa", t0)

        log.info(f"[{rid}] Processing query via Uploaded PDF QA route (bypassing external APIs)")

        sys_p = (
            "You are Aether, a precise research assistant.\n"
            "Use the provided uploaded PDF context to answer the user's query.\n"
            "CRITICAL: Base your answers ONLY on the provided PDF context. "
            "If the context does not contain the answer, state that clearly and do not hallucinate or invent facts. "
            "Cite relevant sections/findings from the document."
            f"\n\n{pdf_context}"
        )
        msgs = [{"role": "system", "content": sys_p}, {"role": "user", "content": raw_query}]
        model = HEAVY_MODEL if req.use_heavy else REASON_MODEL
        try:
            answer = await groq_chat(msgs, model, temperature=req.temperature, max_tokens=2000)
        except LLMError as e:
            raise HTTPException(502, f"LLM error while answering from PDF: {str(e)}")

        # Format the retrieved chunks for rendering in the frontend sources panel
        formatted_chunks = [
            {
                "text": chunk,
                "title": "Uploaded PDF Source",
                "page": "PDF Content",
                "similarity": 0.95,
            }
            for chunk in pdf_chunks_raw
        ]

        latency = int((time.time() - t0) * 1000)
        return {
            "request_id": rid,
            "answer": answer,
            "route": "pdf_qa",
            "plan": {
                "standalone_query": raw_query,
                "reasoning_path": "Answered directly using retrieved chunks from the uploaded PDF(s).",
            },
            "papers": [],
            "chunks": formatted_chunks,
            "arxiv_papers": [],
            "s2_papers": [],
            "datasets": [],
            "code_repos": [],
            "verification": None,
            "latency_ms": latency,
            "model_used": model,
            "warning": None,
        }

    # ── Wikipedia Mode Direct Search ──
    if req.mode == "wikipedia":
        log.info(f"[{rid}] Processing query in Wikipedia Mode: {raw_query}")
        wiki_res = await search_wikipedia_summary(raw_query)
        if not wiki_res:
            answer = f"No Wikipedia page was found matching the query '{raw_query}'. You can try searching in normal Research mode for academic papers."
            latency = int((time.time() - t0) * 1000)
            return {
                "request_id": rid,
                "answer": answer,
                "route": "wikipedia",
                "plan": {
                    "standalone_query": raw_query,
                    "reasoning_path": "Wikipedia search returned no results.",
                },
                "papers": [],
                "chunks": [],
                "arxiv_papers": [],
                "s2_papers": [],
                "datasets": [],
                "code_repos": [],
                "verification": None,
                "latency_ms": latency,
                "model_used": REASON_MODEL,
                "warning": "Wikipedia page not found.",
            }

        sys_p = (
            "You are Aether, an academic research assistant. "
            "A user has queried Wikipedia. Use the retrieved page details below to formulate a beautifully structured, comprehensive explanation of the topic. "
            "Highlight its context, key details, applications, and any related datasets or sources.\n\n"
            f"Wikipedia Page Title: {wiki_res['title']}\n"
            f"URL: {wiki_res['url']}\n"
            f"Summary/Extract: {wiki_res['extract']}\n\n"
            "Format your response with proper Markdown headings and lists. "
            "You MUST clearly cite Wikipedia as the source and include the page link in your answer."
        )

        try:
            answer = await groq_chat(
                [{"role": "system", "content": sys_p}, {"role": "user", "content": raw_query}],
                REASON_MODEL,
                temperature=req.temperature,
                max_tokens=1500,
            )
        except Exception as e:
            log.warning(f"Groq synthesis failed for Wikipedia mode: {e}")
            answer = (
                f"### {wiki_res['title']}\n\n"
                f"{wiki_res['extract']}\n\n"
                f"Source: [Wikipedia]({wiki_res['url']})"
            )

        latency = int((time.time() - t0) * 1000)
        return {
            "request_id": rid,
            "answer": answer,
            "route": "wikipedia",
            "plan": {
                "standalone_query": raw_query,
                "reasoning_path": f"Direct Wikipedia search retrieved '{wiki_res['title']}'",
            },
            "papers": [],
            "chunks": [],
            "arxiv_papers": [],
            "s2_papers": [],
            "datasets": [
                {
                    "name": wiki_res["title"],
                    "full_name": wiki_res["title"],
                    "url": wiki_res["url"],
                    "wikipedia_url": wiki_res["url"],
                    "description": wiki_res["extract"],
                    "source": "wikipedia",
                }
            ],
            "code_repos": [],
            "verification": None,
            "latency_ms": latency,
            "model_used": REASON_MODEL,
            "warning": None,
        }

    # ── 1. Strategic planning brain ───────────────────────────────────
    plan = await plan_query(raw_query)
    query = plan.standalone_query

    # ── 2. Route: entity_lookup ───────────────────────────────────────
    if plan.route == "entity_lookup":
        anchors = plan.graph_anchors or [query]
        try:
            papers = await retrieve_graph_papers(keywords=anchors, anchors=anchors, limit=3)
        except GraphRetrievalError as e:
            raise HTTPException(502, str(e))
        if not papers:
            sys_p = (
                "You are Aether, a GraphRAG research assistant. No matching records were found in the database. "
                "Since this is an academic research query, you have the flexibility to address it using your general scientific knowledge, "
                "but ONLY if you are fully confident in the accuracy of the facts and there is a very low chance of hallucination or output degradation. "
                "If you are not 100% confident or if the topic is highly obscure, decline to answer by stating that no matching records were found in the index."
            )
            answer = await groq_chat(
                [
                    {"role": "system", "content": sys_p},
                    {"role": "user", "content": f"Entity lookup query: {query}"},
                ],
                REASON_MODEL,
                temperature=req.temperature,
            )
            return _empty_response(rid, answer, "entity_lookup", t0)
        p = papers[0]
        authors_str = ", ".join(a for a in (p.get("authors") or []) if a) or "Unknown"
        answer = (
            f"**{p.get('title', '?')}** ({p.get('year', '?')})\n\n"
            f"Authors: {authors_str}\n"
            f"Venue: {p.get('venue') or 'Unknown'}\n"
            f"Domain: {p.get('domain', 'Unknown')}\n"
            f"Citations: {p.get('in_citations', 'N/A')}"
        )
        return _direct_response(rid, answer, "entity_lookup", papers, t0)

    # ── 3. Route: structured (list) ───────────────────────────────────
    if plan.route == "structured":
        kw = plan.graph_anchors or plan.vector_keywords or [query]
        filters = dict(req.filters or {})
        ym = re.search(r"\b(20\d{2}|19\d{2})\b", query)
        if ym and "year" not in filters:
            filters["year"] = int(ym.group(1))
        try:
            papers = await retrieve_graph_papers(
                keywords=kw, filters=filters, anchors=plan.graph_anchors, limit=20
            )
        except GraphRetrievalError as e:
            raise HTTPException(502, str(e))
        if not papers:
            sys_p = (
                "You are Aether, a GraphRAG research assistant. No papers matching the criteria were found in the database. "
                "Since this is an academic research query, you have the flexibility to list potential papers/contributions or synthesize the area using your general scientific knowledge, "
                "but ONLY if you are fully confident in the accuracy of the facts and there is a very low chance of hallucination or output degradation. "
                "If you are not 100% confident, decline to explain that no matching records were found in the database."
            )
            answer = await groq_chat(
                [
                    {"role": "system", "content": sys_p},
                    {"role": "user", "content": f"List papers/contributions related to: {query}"},
                ],
                REASON_MODEL,
                temperature=req.temperature,
            )
            return _empty_response(rid, answer, "structured", t0)
        lines = [f"Found **{len(papers)}** papers:\n"]
        for p in papers:
            auths = ", ".join(a for a in (p.get("authors") or []) if a) or "Unknown"
            lines.append(f"- **{p.get('title', '?')}** ({p.get('year', '?')}) — {auths}")
        return _direct_response(rid, "\n".join(lines), "structured", papers, t0)

    # ── 4. Route: title_lookup ────────────────────────────────────────
    if plan.route == "title_lookup":
        anchors = plan.graph_anchors or [query]
        try:
            papers = await retrieve_graph_papers(keywords=anchors, anchors=anchors, limit=5)
        except GraphRetrievalError as e:
            raise HTTPException(502, str(e))
        if not papers:
            sys_p = (
                "You are Aether, a GraphRAG research assistant. The specified paper was not found in the database. "
                "Since this is an academic research query, you have the flexibility to provide details about the paper from your general knowledge, "
                "but ONLY if you are fully confident in the accuracy of the facts and there is a very low chance of hallucination or output degradation. "
                "If you are not 100% confident, decline by explaining that the paper is not in the database."
            )
            answer = await groq_chat(
                [
                    {"role": "system", "content": sys_p},
                    {"role": "user", "content": f"Provide information on the paper: {query}"},
                ],
                REASON_MODEL,
                temperature=req.temperature,
            )
            return _empty_response(rid, answer, "title_lookup", t0)
        p = papers[0]
        auths = ", ".join(a for a in (p.get("authors") or []) if a) or "Unknown"
        abstract = (p.get("abstract") or "")[:400]
        answer = (
            f"**{p.get('title', '?')}** ({p.get('year', '?')})\n\n"
            f"Authors: {auths}\n"
            f"Venue: {p.get('venue') or 'Unknown'}\n"
            f"Domain: {p.get('domain', 'Unknown')}\n"
            f"Citations: {p.get('in_citations', 'N/A')}\n\n"
            f"Abstract: {abstract}{'...' if len(p.get('abstract', '')) > 400 else ''}"
        )
        return _direct_response(rid, answer, "title_lookup", papers, t0)

    # ── 5. Route: chitchat ────────────────────────────────────────────
    if plan.route == "chitchat":
        sys_p = (
            "You are Aether, a research assistant purpose-built for scientific and academic queries.\n"
            "The user has sent a message that is outside your scope.\n"
            "Respond using EXACTLY this 3-part structure — nothing more, nothing less:\n"
            "  1. One polite acknowledgement sentence (≤10 words).\n"
            "  2. One sentence stating what Aether is designed for (academic research, paper discovery, \n"
            "     literature synthesis, method explanation).\n"
            "  3. One concrete suggestion of what the user COULD ask instead (give a specific example \n"
            "     relevant to the topic they raised if possible, otherwise suggest a research topic).\n"
            "IMPORTANT: Do NOT attempt to answer the off-topic question. Do NOT apologize excessively. \n"
            "Do NOT invent any academic facts. Keep the entire response under 60 words."
        )
        answer = await groq_chat(
            [{"role": "system", "content": sys_p}, {"role": "user", "content": query}],
            REASON_MODEL,
            temperature=0.3,
            max_tokens=120,
        )
        return _empty_response(rid, answer, "chitchat", t0)

    # ── 6. Routes requiring full RAG pipeline (rag / compare / survey / timeline) ──
    kw_for_embed = plan.vector_keywords or plan.graph_anchors or [query]
    embed_query = " ".join(kw_for_embed)

    warning = None

    async def fetch_graph():
        nonlocal warning
        try:
            return await retrieve_graph_papers(
                keywords=plan.graph_anchors or plan.vector_keywords,
                filters=req.filters,
                anchors=plan.graph_anchors,
            )
        except GraphRetrievalError as e:
            log.warning(f"[{rid}] Graph unavailable: {e}")
            warning = "Graph retrieval unavailable — vector-only mode."
            return []

    async def fetch_supabase():
        try:
            embedding = await create_embedding(embed_query)
        except EmbeddingError as e:
            raise HTTPException(502, str(e))

        try:
            # Retrieve from Supabase globally in parallel with graph retrieval
            return await run_vector_pipeline(
                query, embedding, req.top_k, req.min_similarity, [], rid
            )
        except VectorSearchError as e:
            raise HTTPException(502, str(e))

    async def fetch_arxiv():
        # ── Tiered entity-first retrieval ──────────────────────────────────
        # When the planner detected named research entities (e.g., "LoRA",
        # "QLoRA"), fire exact-title tier queries FIRST so primary papers
        # are always retrieved, then supplement with broader discovery queries.
        if plan.search_tiers:
            seen_ids: set = set()
            tier_results: List[Dict] = []
            discovery_results: List[Dict] = []

            # Classify tiers: first 2 are entity/exact-title lookups (limit=3 each),
            # remaining are concept/discovery (limit=4 each)
            entity_tiers = plan.search_tiers[:2]
            discovery_tiers = plan.search_tiers[2:]

            # Fire entity tiers and discovery tiers concurrently
            entity_queries = [retrieve_arxiv_context(t, limit=3) for t in entity_tiers]
            discovery_queries = [retrieve_arxiv_context(t, limit=4) for t in discovery_tiers]
            all_results = await asyncio.gather(
                *entity_queries, *discovery_queries, return_exceptions=True
            )

            n_entity = len(entity_tiers)
            for i, result in enumerate(all_results):
                if isinstance(result, Exception):
                    continue
                for paper in result:
                    dedup_key = paper.get("id") or paper.get("title", "")
                    if not dedup_key or dedup_key in seen_ids:
                        continue
                    seen_ids.add(dedup_key)
                    if i < n_entity:
                        tier_results.append(paper)  # entity tier — high priority
                    else:
                        discovery_results.append(paper)

            # Entity-tier papers lead; discovery papers supplement
            merged = tier_results + discovery_results
            log.info(
                f"[{rid}] Tiered ArXiv: {len(tier_results)} entity + "
                f"{len(discovery_results)} discovery = {len(merged)} papers "
                f"(tiers={plan.search_tiers})"
            )
            return merged[:14]

        # ── Survey multi-query path ─────────────────────────────────────────
        if plan.route == "survey" and plan.vector_keywords:
            seen_ids: set = set()
            merged: List[Dict] = []

            # Build sub-queries: one per vector keyword + one general query
            sub_queries: List[str] = list(
                dict.fromkeys([query] + [f"{kw} transformer" for kw in plan.vector_keywords[:4]])
            )

            sub_results = await asyncio.gather(
                *[retrieve_arxiv_context(sq, limit=4) for sq in sub_queries],
                return_exceptions=True,
            )
            for result in sub_results:
                if isinstance(result, Exception):
                    continue
                for paper in result:
                    arxiv_id = paper.get("id", "")
                    dedup_key = arxiv_id or paper.get("title", "")
                    if dedup_key and dedup_key not in seen_ids:
                        seen_ids.add(dedup_key)
                        merged.append(paper)

            log.info(
                f"[{rid}] Survey ArXiv multi-query: {len(sub_queries)} sub-queries → {len(merged)} unique papers"
            )
            return merged[:14]  # cap at 14 to keep prompt within token budget

        # ── Default: single query for non-survey, non-tiered routes ─────────
        return await retrieve_arxiv_context(query, limit=10)

    async def fetch_s2():
        ck = cache_key("s2", query)
        cached = get_cache("api", ck)
        if cached is not None:
            log.info(f"[{rid}] Cache HIT for S2 query: {query}")
            return cached
        try:
            res = await search_papers_s2(query, limit=10)
            set_cache("api", ck, res)
            return res
        except Exception as e:
            log.warning(f"Failed to fetch S2 papers: {e}")
            return []

    async def fetch_core():
        ck = cache_key("core", query)
        cached = get_cache("api", ck)
        if cached is not None:
            log.info(f"[{rid}] Cache HIT for CORE query: {query}")
            return cached
        try:
            res = await search_core_papers(query, limit=10)
            set_cache("api", ck, res)
            return res
        except Exception as e:
            log.warning(f"Failed to fetch CORE papers: {e}")
            return []

    graph_nodes, chunks, arxiv_papers, s2_papers, core_papers = await asyncio.gather(
        fetch_graph(), fetch_supabase(), fetch_arxiv(), fetch_s2(), fetch_core()
    )
    chunks = merge_adjacent_chunks(chunks)
    chunks = pack_context_within_budget(chunks, limit_tokens=5000)

    # ── Deduplicate and Enrich papers with S2 data ──
    def get_clean_title(title: str) -> str:
        import re

        return re.sub(r"[^a-z0-9]", "", title.lower())

    s2_by_title = {}
    for p in s2_papers:
        c_title = get_clean_title(p.get("title", ""))
        if c_title:
            s2_by_title[c_title] = p

    # Deduplicate ArXiv papers and reuse S2 data if available
    arxiv_to_enrich = []
    enriched_arxiv = []

    for p in arxiv_papers:
        c_title = get_clean_title(p.get("title", ""))
        matched_s2 = s2_by_title.get(c_title)
        if matched_s2:
            merged = {
                **p,
                "citation_count": matched_s2.get("citation_count") or 0,
                "influential_citations": matched_s2.get("influential_citations") or 0,
                "tldr": matched_s2.get("tldr") or "",
                "doi": matched_s2.get("doi") or "",
                "doi_url": matched_s2.get("doi_url") or "",
                "fields_of_study": matched_s2.get("fields_of_study") or [],
                "s2_id": matched_s2.get("s2_id") or "",
                "s2_url": matched_s2.get("s2_url") or "",
                "venue": matched_s2.get("venue") or p.get("venue", ""),
            }
            enriched_arxiv.append(merged)
        else:
            arxiv_to_enrich.append(p)

    # For ArXiv papers not in S2 search results, enrich only the top 5 (or top 2 if no API key) to optimize speed and avoid rate limiter timeouts
    enrich_limit = 5 if os.getenv("S2_API_KEY") else 2
    arxiv_to_enrich = arxiv_to_enrich[:enrich_limit]
    if arxiv_to_enrich:
        try:
            enriched_new = await enrich_arxiv_papers_with_s2(arxiv_to_enrich)
            enriched_arxiv.extend(enriched_new)
        except Exception as e:
            log.warning(f"Failed to enrich new arXiv papers with S2: {e}")
            enriched_arxiv.extend(arxiv_to_enrich)

    # Fallback/supplement with OpenAlex: if any paper in enriched_arxiv lacks DOI or venue, enrich via OpenAlex
    try:
        to_enrich_oa = [p for p in enriched_arxiv if not p.get("doi") or not p.get("venue")]
        if to_enrich_oa:
            log.info(f"Enriching {len(to_enrich_oa)} papers with OpenAlex...")
            enriched_oa = await enrich_arxiv_papers_with_openalex(to_enrich_oa)
            # Map by clean title
            oa_by_title = {get_clean_title(p.get("title", "")): p for p in enriched_oa}
            for idx, p in enumerate(enriched_arxiv):
                title_clean = get_clean_title(p.get("title", ""))
                if title_clean in oa_by_title:
                    enriched_arxiv[idx].update(oa_by_title[title_clean])
    except Exception as e:
        log.warning(f"Failed to enrich arXiv papers with OpenAlex: {e}")

    # Deduplicate CORE papers and reuse S2 data if available
    enriched_core = []
    for p in core_papers:
        c_title = get_clean_title(p.get("title", ""))
        matched_s2 = s2_by_title.get(c_title)
        if matched_s2:
            merged = {
                **p,
                "citation_count": matched_s2.get("citation_count") or 0,
                "influential_citations": matched_s2.get("influential_citations") or 0,
                "tldr": matched_s2.get("tldr") or "",
                "doi": matched_s2.get("doi") or "",
                "doi_url": matched_s2.get("doi_url") or "",
                "fields_of_study": matched_s2.get("fields_of_study") or [],
                "s2_id": matched_s2.get("s2_id") or "",
                "s2_url": matched_s2.get("s2_url") or "",
                "venue": matched_s2.get("venue") or p.get("venue", ""),
            }
            enriched_core.append(merged)
        else:
            enriched_core.append(p)

    if enriched_core:
        enriched_arxiv.extend(enriched_core)

    # Combine all candidate papers
    all_candidates = []
    seen_titles = set()

    # Add S2 search results
    for idx, p in enumerate(s2_papers):
        c_title = get_clean_title(p.get("title", ""))
        if c_title and c_title not in seen_titles:
            seen_titles.add(c_title)
            p["_source"] = "s2"
            p["_rank"] = idx
            all_candidates.append(p)

    # Add enriched ArXiv papers
    for idx, p in enumerate(enriched_arxiv):
        c_title = get_clean_title(p.get("title", ""))
        if c_title and c_title not in seen_titles:
            seen_titles.add(c_title)
            p["_source"] = "arxiv"
            p["_rank"] = idx
            all_candidates.append(p)

    # Rank candidates by hybrid significance score (relevance rank + citation count + recency bonus)
    import math

    for p in all_candidates:
        rank_score = max(0, 10 - p.get("_rank", 0))
        citations = p.get("citation_count") or 0
        citation_bonus = 2.5 * math.log(1 + citations)
        year = p.get("year")
        recency_bonus = 0.0
        try:
            if year and int(year) >= 2025:
                recency_bonus = 3.5
            elif year and int(year) == 2024:
                recency_bonus = 1.5
        except ValueError:
            pass
        p["_significance_score"] = rank_score + citation_bonus + recency_bonus

    all_candidates.sort(key=lambda x: x.get("_significance_score", 0.0), reverse=True)
    top_papers = all_candidates[:8]

    arxiv_papers = [p for p in top_papers if p.get("_source") == "arxiv"]
    s2_papers = [p for p in top_papers if p.get("_source") == "s2"]

    if not chunks and not arxiv_papers and not s2_papers:
        sys_p = (
            "You are Aether, a GraphRAG research assistant. No specific papers or context chunks could be retrieved for this query. "
            "Since this is an academic research query, you have the flexibility to address it using your general scientific knowledge, "
            "but ONLY if you are fully confident in the facts and there is a very low chance of hallucination or output degradation. "
            "If you cannot provide a highly accurate, confident answer, explain clearly and briefly that you cannot verify the details due to the lack of source literature."
        )
        try:
            answer = await groq_chat(
                [{"role": "system", "content": sys_p}, {"role": "user", "content": query}],
                REASON_MODEL,
                temperature=req.temperature,
            )
        except LLMError as e:
            raise HTTPException(502, str(e))

        latency = int((time.time() - t0) * 1000)
        return {
            "request_id": rid,
            "answer": answer,
            "route": plan.route,
            "plan": {
                "standalone_query": plan.standalone_query,
                "reasoning_path": plan.reasoning_path,
            },
            "papers": [],
            "chunks": [],
            "arxiv_papers": [],
            "s2_papers": [],
            "datasets": [],
            "code_repos": [],
            "verification": None,
            "latency_ms": latency,
            "model_used": REASON_MODEL,
            "warning": "No context or external papers retrieved.",
        }

    unique_datasets, all_repos = await retrieve_datasets_and_repos(
        query, arxiv_papers, s2_papers, graph_nodes
    )

    if unique_datasets:
        # Wikipedia enrichment is disabled in standard Research mode (only runs in Wikipedia Mode)
        try:
            unique_datasets = await enrich_datasets_with_kaggle(unique_datasets)
        except Exception as e:
            log.warning(f"Error enriching unique datasets with Kaggle: {e}")

    # Pick prompt by route
    model = HEAVY_MODEL if req.use_heavy else REASON_MODEL

    # ── Primary source reading (for depth=high RAG queries) ──────────────────
    # Fetch full-text passages from the primary papers identified by the planner
    # so the LLM has access to body content, not just 600-char abstracts.
    primary_passages: List[str] = []
    if plan.depth == "high" and plan.named_entities and plan.route in ("rag", "compare"):
        primary_entity_names = {
            e["name"].lower()
            for e in plan.named_entities
            if e.get("primary_source_required") is True
        }
        candidate_papers = (arxiv_papers or []) + (s2_papers or [])
        primary_papers = [
            p
            for p in candidate_papers
            if any(name in (p.get("title") or "").lower() for name in primary_entity_names)
        ][:3]  # cap at 3 to avoid excessive latency

        if primary_papers:
            # Combine search_tiers and required_metrics as keyword extraction terms
            extraction_terms = (plan.search_tiers or []) + (plan.required_metrics or [])
            passage_tasks = [
                read_primary_source_passages(p, extraction_terms) for p in primary_papers
            ]
            passage_lists = await asyncio.gather(*passage_tasks, return_exceptions=True)
            for result in passage_lists:
                if isinstance(result, list):
                    primary_passages.extend(result)
            if primary_passages:
                log.info(
                    f"[{rid}] Primary source passages: {len(primary_passages)} extracted "
                    f"from {len(primary_papers)} papers"
                )

    # ── Build synthesis prompt ────────────────────────────────────────────────
    if plan.route == "compare":
        prompt = compare_prompt(query, chunks, graph_nodes, arxiv_papers, s2_papers)
    elif plan.route == "survey":
        prompt = survey_prompt(query, chunks, graph_nodes, arxiv_papers, s2_papers)
        model = HEAVY_MODEL  # surveys always use heavy model
    elif plan.route == "conceptual":
        prompt = conceptual_prompt(query, chunks, graph_nodes, arxiv_papers, s2_papers)
    elif plan.route == "timeline":
        prompt = timeline_prompt(query, chunks, graph_nodes, arxiv_papers, s2_papers)
    else:
        prompt = grounded_prompt(
            query,
            chunks,
            graph_nodes,
            arxiv_papers,
            s2_papers,
            depth=plan.depth,
            requirements=plan.requirements or None,
            primary_passages=primary_passages or None,
        )

    if pdf_context:
        prompt += f"\n\n{pdf_context}"

    # Raise token budget for high-depth answers (equations + detailed mechanisms)
    max_tokens_for_answer = 3500 if plan.depth == "high" else 2000

    try:
        answer = await groq_chat(
            [{"role": "system", "content": prompt}],
            model,
            temperature=req.temperature,
            max_tokens=max_tokens_for_answer,
        )
    except LLMError as e:
        raise HTTPException(502, str(e))

    # ── Requirement coverage self-check (depth=high only) ────────────────────
    if plan.depth == "high" and plan.requirements:
        missing_reqs = await check_requirements_covered(answer, plan.requirements)
        if missing_reqs:
            reqs_notice = "\n\n---\n> [!WARNING]\n> **Evidence gap** — the retrieved sources did not contain sufficient information to fully address:\n"
            for r in missing_reqs:
                reqs_notice += f"> - {r}\n"
            reqs_notice += (
                ">\n> Consider uploading the primary paper PDF or asking a more targeted follow-up."
            )
            answer += reqs_notice

    verification = None
    if req.verify and chunks:
        answer, verification, warning = await apply_verification(
            answer, chunks, REASON_MODEL, rid, warning, arxiv_papers, s2_papers
        )

    if verification:
        verification.pop("raw", None)

    answer = clean_and_resolve_links(
        answer,
        chunks,
        graph_nodes,
        arxiv_papers
        if plan.route not in ("chitchat", "structured", "title_lookup", "entity_lookup")
        else [],
    )

    latency = int((time.time() - t0) * 1000)
    log.info(f"[{rid}] Done — {plan.route} | {model} | {latency}ms")

    # Build credit snapshot to return to frontend
    post_plan = await get_user_plan(request)
    _plan = post_plan.get("plan", "free")
    _used = post_plan.get("credits_used", 0)
    credits_snap = {
        "plan": _plan,
        "credits_used": _used,
        "credits_remaining": None if _plan == "pro" else max(0, FREE_CREDITS_PER_DAY - _used),
        "credits_limit": None if _plan == "pro" else FREE_CREDITS_PER_DAY,
        "is_unlimited": _plan == "pro",
    }

    show_external = plan.route not in ("chitchat", "structured", "title_lookup", "entity_lookup")
    return {
        "request_id": rid,
        "answer": answer,
        "route": plan.route,
        "plan": {
            "standalone_query": plan.standalone_query,
            "reasoning_path": plan.reasoning_path,
        },
        "papers": graph_nodes,
        "chunks": chunks,
        "arxiv_papers": arxiv_papers if show_external else [],
        "s2_papers": s2_papers if show_external else [],
        "datasets": unique_datasets if show_external else [],
        "code_repos": all_repos[:10] if show_external else [],
        "verification": verification,
        "latency_ms": latency,
        "model_used": model,
        "warning": warning,
        "credits": credits_snap,
    }


# ================================================================
# CONVERSATION ENDPOINT
# ================================================================


@router.post("/api/research/timeline")
async def research_timeline(req: TimelineRequest, request: Request):
    """Chronological evolution of a research topic. [3 credits for Free, unlimited for Pro]"""
    rid = str(uuid.uuid4())
    request.state.request_id = rid
    pool.assert_ready()
    await check_rate_limit(request.client.host if request.client else "unknown")
    await check_and_deduct_credit(request, "timeline")
    t0 = time.time()

    filters: Dict[str, Any] = {}
    if req.start_year:
        filters["start_year"] = req.start_year
    if req.end_year:
        filters["end_year"] = req.end_year

    async def fetch_graph():
        try:
            return await retrieve_graph_papers(keywords=[req.topic], limit=req.top_k)
        except GraphRetrievalError:
            return []

    async def fetch_supabase():
        try:
            embedding = await create_embedding(req.topic)
        except EmbeddingError as e:
            raise HTTPException(502, str(e))

        try:
            # Retrieve from Supabase globally in parallel with graph retrieval
            return await run_vector_pipeline(
                req.topic, embedding, req.top_k, RELEVANCE_FLOOR, [], rid
            )
        except VectorSearchError:
            return []

    async def fetch_arxiv():
        return await retrieve_arxiv_context(req.topic, limit=3)

    async def fetch_core():
        try:
            return await search_core_papers(req.topic, limit=3)
        except Exception as e:
            log.warning(f"Failed to fetch CORE papers: {e}")
            return []

    graph_nodes, chunks, arxiv_papers, core_papers = await asyncio.gather(
        fetch_graph(), fetch_supabase(), fetch_arxiv(), fetch_core()
    )
    if core_papers:
        arxiv_papers.extend(core_papers)
    chunks = merge_adjacent_chunks(chunks)
    chunks = pack_context_within_budget(chunks, limit_tokens=5000)

    prompt = timeline_prompt(req.topic, chunks, graph_nodes, arxiv_papers)
    answer = await groq_chat(
        [{"role": "system", "content": prompt}],
        HEAVY_MODEL,
        temperature=req.temperature,
        max_tokens=2000,
    )

    return {
        "request_id": rid,
        "answer": answer,
        "papers": graph_nodes,
        "chunks": chunks,
        "arxiv_papers": arxiv_papers,
        "latency_ms": int((time.time() - t0) * 1000),
    }


@router.post("/api/research/survey")
async def research_survey(req: SurveyRequest, request: Request):
    """Auto-generate a mini literature survey on a topic. [PRO ONLY]"""
    rid = str(uuid.uuid4())
    request.state.request_id = rid
    pool.assert_ready()
    await check_rate_limit(request.client.host if request.client else "unknown")
    await require_pro(request, "Literature Survey")
    t0 = time.time()

    async def fetch_graph():
        try:
            nodes = await retrieve_graph_papers(keywords=[req.topic], limit=req.top_k)
            # Enrich with co-citation cluster
            seed_ids = [
                g["research_id"] for g in nodes if g.get("research_id") and g.get("score") == 2
            ]
            if seed_ids:
                co_cited = await get_co_citation_cluster(seed_ids, limit=8)
                existing_ids = {g["research_id"] for g in nodes}
                for c in co_cited:
                    if c.get("research_id") and c["research_id"] not in existing_ids:
                        c["source"] = "co-citation"
                        c["score"] = 1
                        nodes.append(c)
            return nodes
        except GraphRetrievalError:
            return []

    async def fetch_supabase():
        try:
            embedding = await create_embedding(req.topic)
        except EmbeddingError as e:
            raise HTTPException(502, str(e))

        try:
            # Retrieve from Supabase globally in parallel with graph retrieval
            return await run_vector_pipeline(
                req.topic, embedding, req.top_k, RELEVANCE_FLOOR, [], rid
            )
        except VectorSearchError:
            return []

    async def fetch_arxiv():
        return await retrieve_arxiv_context(req.topic, limit=3)

    async def fetch_core():
        try:
            return await search_core_papers(req.topic, limit=3)
        except Exception as e:
            log.warning(f"Failed to fetch CORE papers: {e}")
            return []

    graph_nodes, chunks, arxiv_papers, core_papers = await asyncio.gather(
        fetch_graph(), fetch_supabase(), fetch_arxiv(), fetch_core()
    )
    if core_papers:
        arxiv_papers.extend(core_papers)
    chunks = merge_adjacent_chunks(chunks)
    chunks = pack_context_within_budget(chunks, limit_tokens=5000)

    model = HEAVY_MODEL if req.use_heavy else REASON_MODEL
    prompt = survey_prompt(req.topic, chunks, graph_nodes, arxiv_papers)
    answer = await groq_chat(
        [{"role": "system", "content": prompt}], model, temperature=req.temperature, max_tokens=3000
    )

    return {
        "request_id": rid,
        "answer": answer,
        "papers": graph_nodes,
        "paper_count": len(graph_nodes),
        "chunk_count": len(chunks),
        "arxiv_papers": arxiv_papers,
        "latency_ms": int((time.time() - t0) * 1000),
        "model_used": model,
    }


# ================================================================
# BULK RESEARCH
# ================================================================


@router.post("/api/research/bulk")
async def bulk_research(req: BulkRequest, request: Request):
    """Batch research queries. [PRO ONLY]"""
    pool.assert_ready()
    await check_rate_limit(request.client.host if request.client else "unknown")
    await require_pro(request, "Bulk Research")
    sem = asyncio.Semaphore(3)

    async def single(q: str):
        async with sem:
            try:
                r = ResearchRequest(query=q, top_k=req.top_k)
                return await _research_impl(r, request)
            except Exception as e:
                return {"query": q, "error": str(e)}

    results = await asyncio.gather(*[single(q) for q in req.queries])
    return {"results": results}
