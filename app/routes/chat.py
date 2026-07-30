"""
routes/chat.py — Full chat execution endpoints:
  - POST /api/chat
  - POST /api/v1/chat/completions
"""

import asyncio
import os
import time
import uuid
from typing import Dict, List

from fastapi import APIRouter, HTTPException, Request

from app.clients.groq import create_embedding, groq_chat
from app.clients.pool import pool
from app.config import (
    CREDIT_COSTS,
    FREE_CREDITS_PER_DAY,
    FREE_TOP_K_MAX,
    HEAVY_MODEL,
    REASON_MODEL,
    REQUEST_TIMEOUT,
    log,
)
from app.core.document import get_or_parse_pdf_safe, get_relevant_pdf_chunks
from app.core.exceptions import EmbeddingError, GraphRetrievalError, LLMError, VectorSearchError
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
    retrieve_graph_papers,
)
from app.core.planner import (
    plan_query,
)
from app.core.retrieval import (
    merge_adjacent_chunks,
    pack_context_within_budget,
    retrieve_arxiv_context,
    run_vector_pipeline,
)
from app.models.chat import ChatCompletionRequest, ConversationRequest
from app.utils.auth import set_user_context
from app.utils.conversation import (
    build_conversation_context,
    compile_chat_messages,
)
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
    log.warning(f"External sources unavailable in routes/chat: {_src_err}")

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


@router.post("/api/chat")
async def chat_with_context(req: ConversationRequest, request: Request):
    rid = str(uuid.uuid4())
    request.state.request_id = rid
    try:
        res = await asyncio.wait_for(_chat_impl(req, request), timeout=REQUEST_TIMEOUT)
        return await append_credits_snapshot(res, request)
    except asyncio.TimeoutError:
        raise HTTPException(504, f"Timed out after {REQUEST_TIMEOUT}s.")


async def _chat_impl(req: ConversationRequest, request: Request):
    pool.assert_ready()
    rid = getattr(request.state, "request_id", "unknown")
    await check_rate_limit(request.client.host if request.client else "unknown")
    await set_user_context(request)
    t0 = time.time()

    # ── Plan enforcement ──────────────────────────────────────────────
    plan_info = await get_user_plan(request)
    user_plan = plan_info.get("plan", "free")
    if user_plan == "free":
        req.top_k = min(req.top_k, FREE_TOP_K_MAX)
        req.use_heavy = False
    await check_and_deduct_credit(request, "chat")
    # ─────────────────────────────────────────────────────────────────

    last_user_msg = next((m.content for m in reversed(req.messages) if m.role == "user"), None)
    if not last_user_msg:
        raise HTTPException(400, "No user message found.")

    # Auto-detect wikipedia: / wiki: prefixes
    is_wiki_prefix = False
    prefix_query = last_user_msg.strip()
    if prefix_query.lower().startswith("wikipedia:"):
        prefix_query = prefix_query[len("wikipedia:") :].strip()
        is_wiki_prefix = True
    elif prefix_query.lower().startswith("wiki:"):
        prefix_query = prefix_query[len("wiki:") :].strip()
        is_wiki_prefix = True

    if is_wiki_prefix:
        req.mode = "wikipedia"
        last_user_msg = prefix_query
        for m in reversed(req.messages):
            if m.role == "user":
                m.content = prefix_query
                break

    log.info(f"[{rid}] CHAT: {last_user_msg} (mode: {req.mode})")

    # ── Wikipedia Mode Direct Search in Chat ──
    if req.mode == "wikipedia":
        log.info(f"[{rid}] Multi-turn query in Wikipedia Mode: {last_user_msg}")
        wiki_res = await search_wikipedia_summary(last_user_msg)

        wiki_context = ""
        unique_datasets = []
        if wiki_res:
            wiki_context = (
                f"\n\n━━━ WIKIPEDIA CONTEXT FOR {wiki_res['title']} ━━━\n"
                f"URL: {wiki_res['url']}\n"
                f"Summary: {wiki_res['extract']}\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
            )
            unique_datasets = [
                {
                    "name": wiki_res["title"],
                    "full_name": wiki_res["title"],
                    "url": wiki_res["url"],
                    "wikipedia_url": wiki_res["url"],
                    "description": wiki_res["extract"],
                    "source": "wikipedia",
                }
            ]

        sys_p = (
            "You are Aether, an academic research assistant. "
            "Use the provided Wikipedia context (if any) to address the user's query. "
            "Cite Wikipedia and provide links where appropriate."
        )
        sys_p += wiki_context

        # Compile messages
        chat_msgs = []
        chat_msgs.append({"role": "system", "content": sys_p})
        for msg in req.messages:
            if msg.role != "system":
                chat_msgs.append({"role": msg.role, "content": msg.content})

        try:
            answer = await groq_chat(
                chat_msgs,
                REASON_MODEL,
                temperature=req.temperature,
                max_tokens=1500,
            )
        except Exception as e:
            log.warning(f"Groq synthesis failed for Wikipedia mode in chat: {e}")
            if wiki_res:
                answer = (
                    f"### {wiki_res['title']}\n\n"
                    f"{wiki_res['extract']}\n\n"
                    f"Source: [Wikipedia]({wiki_res['url']})"
                )
            else:
                answer = f"No Wikipedia page was found matching the query '{last_user_msg}'."

        latency = int((time.time() - t0) * 1000)
        return {
            "request_id": rid,
            "answer": answer,
            "route": "wikipedia",
            "plan": {
                "standalone_query": last_user_msg,
                "reasoning_path": f"Multi-turn Wikipedia search for '{wiki_res['title'] if wiki_res else last_user_msg}'",
            },
            "papers": [],
            "chunks": [],
            "arxiv_papers": [],
            "s2_papers": [],
            "datasets": unique_datasets,
            "code_repos": [],
            "verification": None,
            "latency_ms": latency,
            "model_used": REASON_MODEL,
            "warning": None if wiki_res else "No matching Wikipedia page found.",
        }

    # 1. Parse and cache PDF/arXiv URLs
    latest_urls = extract_paper_urls(last_user_msg)
    history_urls = []
    for m in req.messages[:-1]:
        if m.role == "user":
            history_urls.extend(extract_paper_urls(m.content))
    # Deduplicate history URLs while preserving order
    history_urls = list(dict.fromkeys(history_urls))

    new_urls = [u for u in latest_urls if u not in history_urls]

    new_docs = []
    if new_urls:
        for url in new_urls:
            try:
                doc_text, doc_links = await get_or_parse_pdf_safe(url, raise_on_error=True)
                new_docs.append((url, doc_text, doc_links))
            except Exception as e:
                raise HTTPException(400, f"Failed to download/parse PDF from {url}: {str(e)}")

    # If the user pasted a URL as a simple paste, return the structured summary immediately
    if latest_urls and is_simple_link_paste(last_user_msg, latest_urls):
        target_url = latest_urls[0]
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
                "standalone_query": last_user_msg,
                "reasoning_path": f"PDF parsed directly from {target_url}. Generated structured summary.",
            },
            "papers": [],
            "chunks": [],
            "arxiv_papers": [],
            "verification": None,
            "latency_ms": latency,
            "model_used": HEAVY_MODEL,
            "warning": None,
        }

    # Build context string for pronoun resolution
    ctx = build_conversation_context(req.messages[:-1])

    # ── Strategic planning (includes pronoun resolution) ─────────────
    plan = await plan_query(last_user_msg, context=ctx)
    query = plan.standalone_query

    # Compile parsed PDF context for the LLM using FAISS vector search
    all_urls = list(dict.fromkeys(history_urls + latest_urls))
    pdf_context_parts = []
    pdf_chunks_raw = []
    for url in all_urls:
        relevant_chunks = await get_relevant_pdf_chunks(url, query)
        if relevant_chunks:
            pdf_chunks_raw.extend(relevant_chunks)
            chunks_text = "\n\n".join(relevant_chunks)
            pdf_context_parts.append(
                f"━━━ RELEVANT PDF SECTION FOR {url} ━━━\n"
                f"{chunks_text}\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
            )
    pdf_context = "\n\n".join(pdf_context_parts) if pdf_context_parts else ""

    # ── Uploaded PDF Direct QA Route ──
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
        msgs = await compile_chat_messages(sys_p, req.messages)
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
                "standalone_query": query,
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

    # ── Route: entity_lookup ──────────────────────────────────────────
    if plan.route == "entity_lookup":
        anchors = plan.graph_anchors or [query]
        try:
            papers = await retrieve_graph_papers(keywords=anchors, anchors=anchors, limit=3)
        except GraphRetrievalError as e:
            raise HTTPException(502, str(e))
        if not papers:
            if pdf_context:
                sys_p = "You are Aether. Respond comprehensively, warmly, and in detail. Do not invent academic facts."
                sys_p += f"\n\n{pdf_context}"
                msgs = await compile_chat_messages(sys_p, req.messages)
                answer = await groq_chat(msgs, REASON_MODEL, temperature=req.temperature)
                return _empty_response(rid, answer, "chitchat", t0)
            else:
                # ── DB miss: fall through to arXiv/S2 for real grounded answers ──
                log.info(f"[{rid}] entity_lookup DB miss — falling back to arXiv/S2")
                # Use extracted anchors as search terms (much better than raw NL query)
                arxiv_search = " ".join(anchors) if anchors else query
                arxiv_fb = await retrieve_arxiv_context(arxiv_search, limit=5)
                s2_fb = await search_papers_s2(arxiv_search, limit=5)
                if arxiv_fb or s2_fb:
                    fb_prompt = grounded_prompt(query, [], [], arxiv_fb, s2_fb)
                    msgs = await compile_chat_messages(fb_prompt, req.messages)
                    try:
                        answer = await groq_chat(
                            msgs, REASON_MODEL, temperature=req.temperature, max_tokens=1500
                        )
                    except LLMError as e:
                        raise HTTPException(502, str(e))
                    return {
                        "request_id": rid,
                        "answer": answer,
                        "route": "entity_lookup",
                        "plan": {
                            "standalone_query": plan.standalone_query,
                            "reasoning_path": plan.reasoning_path,
                        },
                        "papers": [],
                        "chunks": [],
                        "arxiv_papers": arxiv_fb,
                        "s2_papers": s2_fb,
                        "datasets": [],
                        "code_repos": [],
                        "verification": None,
                        "latency_ms": int((time.time() - t0) * 1000),
                        "model_used": REASON_MODEL,
                        "warning": "Not found in local database — results sourced from arXiv/Semantic Scholar.",
                    }
                else:
                    sys_p = (
                        "You are Aether, a GraphRAG research assistant. No matching records were found in the database for this query.\n"
                        "CRITICAL: Do NOT invent, guess, or hallucinate metadata (authors, venue, year, domain).\n"
                        "Explain clearly that no matching records were found in the database or online (arXiv/Semantic Scholar), and invite the user to provide the exact paper title, DOI, or upload the PDF."
                    )
                    msgs = await compile_chat_messages(sys_p, req.messages)
                    answer = await groq_chat(msgs, REASON_MODEL, temperature=req.temperature)
                    return _empty_response(rid, answer, "entity_lookup", t0)
        p = papers[0]
        auths = ", ".join(a for a in (p.get("authors") or []) if a) or "Unknown"
        answer = (
            f"**{p.get('title', '?')}** ({p.get('year', '?')})\n\n"
            f"Authors: {auths}\n"
            f"Venue: {p.get('venue') or 'Unknown'}\n"
            f"Domain: {p.get('domain', 'Unknown')}"
        )
        return _direct_response(rid, answer, "entity_lookup", papers, t0)

    # ── Route: structured ─────────────────────────────────────────────
    if plan.route == "structured":
        kw = (plan.graph_anchors or []) + (plan.vector_keywords or [])
        if not kw:
            kw = [query]
        try:
            papers = await retrieve_graph_papers(
                keywords=kw, filters=req.filters, anchors=plan.graph_anchors, limit=20
            )
        except GraphRetrievalError as e:
            raise HTTPException(502, str(e))
        if not papers:
            if pdf_context:
                sys_p = "You are Aether. Respond comprehensively, warmly, and in detail. Do not invent academic facts."
                sys_p += f"\n\n{pdf_context}"
                msgs = await compile_chat_messages(sys_p, req.messages)
                answer = await groq_chat(msgs, REASON_MODEL, temperature=req.temperature)
                return _empty_response(rid, answer, "chitchat", t0)
            else:
                # ── DB miss: fall through to arXiv/S2 for real grounded list ──
                log.info(f"[{rid}] structured DB miss — falling back to arXiv/S2")
                # Use extracted keywords as search terms
                arxiv_search = " ".join(kw) if kw else query
                arxiv_fb = await retrieve_arxiv_context(arxiv_search, limit=10)
                s2_fb = await search_papers_s2(arxiv_search, limit=10)
                if arxiv_fb or s2_fb:
                    all_fb = arxiv_fb + s2_fb
                    lines = [
                        f"Found **{len(all_fb)}** papers (sourced from arXiv/Semantic Scholar):\n"
                    ]
                    seen_titles = set()
                    for p in all_fb:
                        title = p.get("title") or p.get("name") or "?"
                        if title in seen_titles:
                            continue
                        seen_titles.add(title)
                        year = p.get("year") or p.get("published", "")[:4] or "?"
                        auths = ", ".join((p.get("authors") or [])[:3]) or "Unknown"
                        lines.append(f"• **{title}** ({year}) — {auths}")
                    return {
                        "request_id": rid,
                        "answer": "\n".join(lines),
                        "route": "structured",
                        "plan": {
                            "standalone_query": plan.standalone_query,
                            "reasoning_path": plan.reasoning_path,
                        },
                        "papers": [],
                        "chunks": [],
                        "arxiv_papers": arxiv_fb,
                        "s2_papers": s2_fb,
                        "datasets": [],
                        "code_repos": [],
                        "verification": None,
                        "latency_ms": int((time.time() - t0) * 1000),
                        "model_used": REASON_MODEL,
                        "warning": "Not found in local database — results sourced from arXiv/Semantic Scholar.",
                    }
                else:
                    sys_p = (
                        "You are Aether, a GraphRAG research assistant. No matching papers were found in the database or online.\n"
                        "CRITICAL: Do NOT invent or guess paper lists, citations, or authors.\n"
                        "State clearly that no records matching these criteria were found anywhere, and invite the user to upload relevant PDFs or specify exact titles/arXiv IDs."
                    )
                    msgs = await compile_chat_messages(sys_p, req.messages)
                    answer = await groq_chat(msgs, REASON_MODEL, temperature=req.temperature)
                    return _empty_response(rid, answer, "structured", t0)
        lines = [f"Found **{len(papers)}** papers:\n"]
        for p in papers:
            auths = ", ".join(a for a in (p.get("authors") or []) if a) or "Unknown"
            lines.append(f"• **{p.get('title', '?')}** ({p.get('year', '?')}) — {auths}")
        return _direct_response(rid, "\n".join(lines), "structured", papers, t0)

    # ── Route: chitchat ───────────────────────────────────────────────
    if plan.route == "chitchat":
        if pdf_context:
            # If the user has an attached PDF, be more flexible — maybe they're asking about it
            sys_p = (
                "You are Aether, an academic research assistant. "
                "Respond briefly using the provided document context if relevant, otherwise gently redirect to research."
                " Keep the response complete and under 80 words."
            )
            sys_p += f"\n\n{pdf_context}"
            msgs = await compile_chat_messages(sys_p, req.messages)
            answer = await groq_chat(
                msgs, REASON_MODEL, temperature=req.temperature, max_tokens=200
            )
        else:
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
            msgs = await compile_chat_messages(sys_p, req.messages)
            answer = await groq_chat(msgs, REASON_MODEL, temperature=0.3, max_tokens=120)
        return _empty_response(rid, answer, "chitchat", t0)

    # ── Route: context_only ───────────────────────────────────────────
    if plan.route == "context_only":
        sys_p = (
            "You are Aether, an academic research assistant. "
            "Address the user's query using the conversation history. "
            "Rely on the facts and papers already discussed in the chat. Do not invent new academic facts."
        )
        if pdf_context:
            sys_p += f"\n\n{pdf_context}"

        msgs = await compile_chat_messages(sys_p, req.messages)
        answer = await groq_chat(msgs, REASON_MODEL, temperature=req.temperature, max_tokens=1500)
        return _empty_response(rid, answer, "context_only", t0)

    # ── Full RAG ──────────────────────────────────────────────────────
    warning = None

    # Combine graph anchors and vector keywords to preserve both specific entities and domain terms
    search_keywords = (plan.graph_anchors or []) + (plan.vector_keywords or [])
    search_query = " ".join(search_keywords) if search_keywords else query

    async def fetch_graph():
        nonlocal warning
        try:
            return await retrieve_graph_papers(
                keywords=plan.graph_anchors or plan.vector_keywords,
                filters=req.filters,
                anchors=plan.graph_anchors,
            )
        except GraphRetrievalError:
            warning = "Graph retrieval unavailable."
            return []

    async def fetch_supabase():
        try:
            embedding = await create_embedding(search_query)
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
        if plan.search_tiers:
            seen_ids: set = set()
            tier_results: List[Dict] = []
            discovery_results: List[Dict] = []
            entity_tiers = plan.search_tiers[:2]
            discovery_tiers = plan.search_tiers[2:]
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
                        tier_results.append(paper)
                    else:
                        discovery_results.append(paper)
            merged = tier_results + discovery_results
            log.info(
                f"[chat] Tiered ArXiv: {len(tier_results)} entity + "
                f"{len(discovery_results)} discovery = {len(merged)} papers"
            )
            return merged[:14]
        # ── Default single-query path ────────────────────────────────────────
        return await retrieve_arxiv_context(search_query, limit=10)

    async def fetch_s2():
        try:
            return await search_papers_s2(search_query, limit=10)
        except Exception as e:
            log.warning(f"Failed to fetch S2 papers: {e}")
            return []

    async def fetch_core():
        try:
            return await search_core_papers(search_query, limit=10)
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
        # Check if we already have it in S2 search results
        matched_s2 = s2_by_title.get(c_title)
        if matched_s2:
            # Merge S2 data (citation_count, tldr, etc.)
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

    if core_papers:
        enriched_arxiv.extend(core_papers)

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
        # Base rank score (lower rank in search is better: 10 - rank)
        rank_score = max(0, 10 - p.get("_rank", 0))

        # Citation bonus: 2.5 * ln(1 + citation_count)
        citations = p.get("citation_count") or 0
        citation_bonus = 2.5 * math.log(1 + citations)

        # Recency bonus: ensure recent papers (e.g. 2025/2026) are highly competitive
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

    # Sort candidates by significance score in descending order
    all_candidates.sort(key=lambda x: x.get("_significance_score", 0.0), reverse=True)

    # Select the top 8 overall papers
    top_papers = all_candidates[:8]

    # Split back into arxiv_papers and s2_papers preserving the ranked order for prompts
    arxiv_papers = [p for p in top_papers if p.get("_source") == "arxiv"]
    s2_papers = [p for p in top_papers if p.get("_source") == "s2"]

    # [BEGIN OPTION A FALLBACK FIX] - Commented out original unconditional fallback
    # if not chunks and not arxiv_papers and not s2_papers and not pdf_context:
    #     sys_p = (
    #         "You are Aether, a GraphRAG research assistant. No specific papers or context chunks could be retrieved for this query.\n"
    #         "CRITICAL: Do NOT hallucinate, guess, or invent citations, authors, papers, or specific scientific results.\n"
    #         "Explain clearly that you do not have the source literature in your database, and ask the user to upload the PDF or provide a specific identifier (like DOI or arXiv ID)."
    #     )
    #     msgs = await compile_chat_messages(sys_p, req.messages)
    #     try:
    #         answer = await groq_chat(msgs, REASON_MODEL, temperature=req.temperature)
    #     except LLMError as e:
    #         raise HTTPException(502, str(e))
    #
    #     latency = int((time.time() - t0) * 1000)
    #     return {
    #         "request_id": rid,
    #         "answer": answer,
    #         "route": plan.route,
    #         "plan": {
    #             "standalone_query": plan.standalone_query,
    #             "reasoning_path": plan.reasoning_path,
    #         },
    #         "papers": [],
    #         "chunks": [],
    #         "arxiv_papers": [],
    #         "s2_papers": [],
    #         "datasets": [],
    #         "code_repos": [],
    #         "verification": None,
    #         "latency_ms": latency,
    #         "model_used": REASON_MODEL,
    #         "warning": "No context or external papers retrieved.",
    #     }

    # New conditional empty-evidence check:
    # Only force defensive refusal if the user was looking for specific entities/anchors (plan.graph_anchors is not empty).
    # If graph_anchors is empty, let it fall through to the main LLM generation (which uses Rule 4/5 for general synthesis).
    if not chunks and not arxiv_papers and not s2_papers and not pdf_context and plan.graph_anchors:
        sys_p = (
            "You are Aether, a GraphRAG research assistant. No specific papers or context chunks could be retrieved for this query.\n"
            "CRITICAL: Do NOT hallucinate, guess, or invent citations, authors, papers, or specific scientific results.\n"
            "Explain clearly that you do not have the source literature in your database, and ask the user to upload the PDF or provide a specific identifier (like DOI or arXiv ID)."
        )
        msgs = await compile_chat_messages(sys_p, req.messages)
        try:
            answer = await groq_chat(msgs, REASON_MODEL, temperature=req.temperature)
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
    # [END OPTION A FALLBACK FIX]

    unique_datasets, all_repos = await retrieve_datasets_and_repos(
        query, arxiv_papers, s2_papers, graph_nodes
    )

    if unique_datasets:
        # Wikipedia enrichment is disabled in standard Research mode (only runs in Wikipedia Mode)
        try:
            unique_datasets = await enrich_datasets_with_kaggle(unique_datasets)
        except Exception as e:
            log.warning(f"Error enriching unique datasets with Kaggle: {e}")

    model = HEAVY_MODEL if req.use_heavy else REASON_MODEL

    if plan.route == "compare":
        prompt = compare_prompt(query, chunks, graph_nodes, arxiv_papers, s2_papers)
    elif plan.route == "survey":
        prompt = survey_prompt(query, chunks, graph_nodes, arxiv_papers, s2_papers)
        model = HEAVY_MODEL
    elif plan.route == "conceptual":
        prompt = conceptual_prompt(query, chunks, graph_nodes, arxiv_papers, s2_papers)
    elif plan.route == "timeline":
        prompt = timeline_prompt(query, chunks, graph_nodes, arxiv_papers, s2_papers)
    else:
        prompt = grounded_prompt(query, chunks, graph_nodes, arxiv_papers, s2_papers)

    if pdf_context:
        prompt += f"\n\n{pdf_context}"

    msgs = await compile_chat_messages(prompt, req.messages)
    try:
        answer = await groq_chat(msgs, model, temperature=req.temperature, max_tokens=2000)
    except LLMError as e:
        raise HTTPException(502, str(e))

    verification = None
    if req.verify and chunks:
        answer, verification, warning = await apply_verification(
            answer, chunks, REASON_MODEL, rid, warning, arxiv_papers, s2_papers
        )

    if verification:
        verification.pop("raw", None)

    show_external = plan.route not in ("chitchat", "structured", "title_lookup", "entity_lookup")
    answer = clean_and_resolve_links(
        answer, chunks, graph_nodes, arxiv_papers if show_external else []
    )

    latency = int((time.time() - t0) * 1000)
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
        "credits": {
            "plan": user_plan,
            "credits_used": plan_info.get("credits_used", 0) + CREDIT_COSTS.get("chat", 1),
            "credits_remaining": None
            if user_plan == "pro"
            else max(
                0,
                FREE_CREDITS_PER_DAY
                - plan_info.get("credits_used", 0)
                - CREDIT_COSTS.get("chat", 1),
            ),
            "credits_limit": None if user_plan == "pro" else FREE_CREDITS_PER_DAY,
            "is_unlimited": user_plan == "pro",
        },
    }


@router.post("/v1/chat/completions")
async def chat_completions(req: ChatCompletionRequest, request: Request):
    """OpenAI-compatible completions. [PRO ONLY]"""
    rid = str(uuid.uuid4())
    request.state.request_id = rid
    pool.assert_ready()
    await check_rate_limit(request.client.host if request.client else "unknown")
    await require_pro(request, "API Access (/v1/chat/completions)")
    model = HEAVY_MODEL if req.model in (HEAVY_MODEL, "heavy") else REASON_MODEL
    try:
        answer = await groq_chat(req.messages, model, req.temperature, req.max_tokens)
    except LLMError as e:
        raise HTTPException(502, str(e))
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:8]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": answer},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": None,
            "completion_tokens": None,
            "total_tokens": None,
        },
    }
