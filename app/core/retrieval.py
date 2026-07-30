import asyncio
import os
import urllib.parse
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional

import httpx
import numpy as np

from app.clients.pool import cache_key, get_cache, get_supabase_client, pool, set_cache
from app.config import FREEZE_RETRIEVAL, MMR_LAMBDA, RELEVANCE_FLOOR, log
from app.core.exceptions import VectorSearchError

SIMILARITY_KEYS = ("similarity", "score", "relevance", "_score", "sim")


async def vector_search(
    embedding: List[float],
    min_similarity: float,
    match_count: int,
    filter_ids: Optional[List[str]] = None,
) -> List[Dict]:
    if FREEZE_RETRIEVAL:
        log.info("Database retrieval is frozen. Skipping vector_search.")
        return []

    if not pool.supabase:
        raise VectorSearchError("Supabase not connected")
    try:

        def _rpc():
            return (
                get_supabase_client()
                .rpc(
                    "match_paper_chunks",
                    {
                        "query_embedding": embedding,
                        "match_threshold": min_similarity,
                        "match_count": match_count,
                        "filter_ids": filter_ids or [],
                    },
                )
                .execute()
            )

        rpc = await asyncio.to_thread(_rpc)
        return rpc.data or []
    except Exception as e:
        raise VectorSearchError(f"Vector search failed: {e}")


async def hybrid_search(
    query_text: str,
    query_embedding: List[float],
    match_count: int,
    filter_ids: Optional[List[str]] = None,
) -> List[Dict]:
    if FREEZE_RETRIEVAL:
        log.info("Database retrieval is frozen. Skipping hybrid_search.")
        return []

    if not pool.supabase:
        raise VectorSearchError("Supabase not connected")
    try:

        def _rpc():
            return (
                get_supabase_client()
                .rpc(
                    "hybrid_search",
                    {
                        "query_text": query_text,
                        "query_embedding": query_embedding,
                        "match_count": match_count,
                        "filter_ids": filter_ids or [],
                    },
                )
                .execute()
            )

        rpc = await asyncio.to_thread(_rpc)
        return rpc.data or []
    except Exception as e:
        raise VectorSearchError(f"Hybrid search failed: {e}")


def reciprocal_rank_fusion(result_lists: List[List[Dict]], k: int = 60) -> List[Dict]:
    scores: Dict[str, float] = {}
    chunks: Dict[str, Dict] = {}
    for lst in result_lists:
        for rank, chunk in enumerate(lst):
            cid = str(
                chunk.get("id") or f"{chunk.get('research_id', '')}_{chunk.get('chunk_number', '')}"
            )
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank + 1)
            chunks[cid] = chunk
    return [chunks[cid] for cid in sorted(scores, key=lambda x: scores[x], reverse=True)]


def mmr_rerank(
    chunks: List[Dict], query_emb: List[float], top_k: int, lam: float = MMR_LAMBDA
) -> List[Dict]:
    """
    Select chunks using MMR to balance relevance and diversity.
    lam=1.0 → pure relevance, lam=0.0 → pure diversity.
    """
    if not chunks or len(chunks) <= top_k:
        return chunks

    def get_emb(c: Dict) -> Optional[np.ndarray]:
        e = c.get("embedding")
        if e and isinstance(e, list):
            return np.array(e, dtype=float)
        return None

    q = np.array(query_emb, dtype=float)

    def cosine(a: np.ndarray, b: np.ndarray) -> float:
        na, nb = np.linalg.norm(a), np.linalg.norm(b)
        if na == 0 or nb == 0:
            return 0.0
        return float(np.dot(a, b) / (na * nb))

    selected: List[Dict] = []
    remaining = list(chunks)

    while len(selected) < top_k and remaining:
        best_idx, best_score = 0, -float("inf")
        for i, c in enumerate(remaining):
            emb = get_emb(c)
            rel = cosine(emb, q) if emb is not None else get_chunk_similarity(c)
            if not selected:
                score = rel
            else:
                max_sim = max(
                    (
                        cosine(emb, get_emb(s))
                        if (emb is not None and get_emb(s) is not None)
                        else 0.0
                    )
                    for s in selected
                )
                score = lam * rel - (1 - lam) * max_sim
            if score > best_score:
                best_score, best_idx = score, i
        selected.append(remaining.pop(best_idx))

    return selected


def get_chunk_similarity(chunk: dict) -> float:
    for key in SIMILARITY_KEYS:
        if key in chunk:
            try:
                return float(chunk[key])
            except (TypeError, ValueError):
                pass
    return 1.0


def merge_adjacent_chunks(chunks: List[Dict]) -> List[Dict]:
    """
    If multiple chunks belong to the same paper (research_id) and are adjacent in chunk_index,
    merge them into a single chunk. This prevents sentences/formulas from being chopped.
    """
    if not chunks:
        return []

    paper_order = []
    by_paper = {}
    for c in chunks:
        rid = c.get("research_id") or c.get("paper_id")
        if not rid:
            ref_id = f"raw_{id(c)}"
            paper_order.append(ref_id)
            by_paper[ref_id] = [c]
            continue
        if rid not in by_paper:
            paper_order.append(rid)
            by_paper[rid] = []
        by_paper[rid].append(c)

    merged_chunks = []
    for rid in paper_order:
        paper_chunks = by_paper[rid]
        if len(paper_chunks) <= 1:
            merged_chunks.extend(paper_chunks)
            continue

        paper_chunks = sorted(paper_chunks, key=lambda c: c.get("chunk_index", 0))

        current = paper_chunks[0].copy()
        for next_chunk in paper_chunks[1:]:
            curr_idx = current.get("chunk_index")
            next_idx = next_chunk.get("chunk_index")

            if curr_idx is not None and next_idx is not None and next_idx - curr_idx <= 1:
                next_text = next_chunk.get("chunk", "")
                if next_text:
                    current["chunk"] = (current.get("chunk", "") + " " + next_text).strip()
                current["chunk_index"] = next_idx
                curr_sim = get_chunk_similarity(current)
                next_sim = get_chunk_similarity(next_chunk)
                current["similarity"] = max(curr_sim, next_sim)
                if "score" in current:
                    current["score"] = current["similarity"]
            else:
                merged_chunks.append(current)
                current = next_chunk.copy()
        merged_chunks.append(current)

    return merged_chunks


def pack_context_within_budget(chunks: List[Dict], limit_tokens: int = 5000) -> List[Dict]:
    """
    Selects and packs chunks dynamically until a token budget limit is reached.
    Assumes 1 token ~= 4.2 characters on average for text estimation.
    """
    packed = []
    current_chars = 0
    max_chars = int(limit_tokens * 4.2)

    for c in chunks:
        chunk_text = c.get("chunk", "")
        char_len = len(chunk_text) + len(c.get("title", "")) + 50
        if current_chars + char_len > max_chars:
            log.info(f"Context budget reached: packing stopped. Total chars: {current_chars}")
            break
        packed.append(c)
        current_chars += char_len
    return packed


def filter_relevant_chunks(chunks: List[Dict], floor: float = RELEVANCE_FLOOR) -> List[Dict]:
    filtered = [c for c in chunks if get_chunk_similarity(c) >= floor]
    dropped = len(chunks) - len(filtered)
    if dropped:
        log.info(f"Relevance filter: dropped {dropped}/{len(chunks)} chunks below {floor}")
    return filtered


_SECTION_PRIORITY = {
    "abstract": 0,
    "conclusion": 1,
    "introduction": 2,
    "related work": 3,
}


def section_priority(chunk: Dict) -> int:
    section = (chunk.get("section") or "").lower()
    for key, pri in _SECTION_PRIORITY.items():
        if key in section:
            return pri
    return 10


async def retrieve_arxiv_context(query: str, limit: int = 3) -> List[Dict[str, Any]]:
    """Retrieve relevant paper abstracts from arXiv API in real-time."""
    if not query.strip():
        return []

    ck = cache_key("arxiv", query, limit)
    cached = get_cache("api", ck)
    if cached is not None:
        log.info(f"Cache HIT for arXiv context query: {query} (limit={limit})")
        return cached

    # Try ArXiv MCP Server if configured in env
    mcp_url = os.getenv("ARXIV_MCP_URL")
    is_self = False
    if mcp_url:
        parsed = urllib.parse.urlparse(mcp_url)
        # Avoid calling ourselves via HTTP to prevent single-worker deadlocks
        if (
            "graphrag-research-assistant.onrender.com" in parsed.netloc
            or "localhost:8000" in parsed.netloc
            or "127.0.0.1:8000" in parsed.netloc
        ):
            is_self = True

    if mcp_url and not is_self:
        try:
            from app.sources.arxiv_mcp import query_arxiv_mcp

            mcp_papers = await query_arxiv_mcp(query, limit=limit)
            if mcp_papers:
                return mcp_papers
        except Exception as e:
            log.warning(f"ArXiv MCP query failed: {e}. Falling back to standard XML feed.")

    # ── Smart query extraction: strip NL question filler words, keep domain terms ──
    _NL_STOPWORDS = {
        "what",
        "how",
        "does",
        "do",
        "show",
        "explain",
        "describe",
        "tell",
        "find",
        "give",
        "list",
        "can",
        "you",
        "me",
        "my",
        "please",
        "is",
        "are",
        "a",
        "an",
        "the",
        "to",
        "for",
        "with",
        "about",
        "from",
        "by",
        "at",
        "on",
        "it",
        "its",
        "this",
        "that",
        "which",
        "where",
        "when",
        "who",
        "why",
        "some",
        "any",
        "more",
        "recent",
        "related",
        "information",
        "paper",
        "papers",
        "work",
        "works",
        "reference",
        "references",
    }
    clean_query = query.replace('"', "").replace("'", "").replace("?", "").strip()
    words = clean_query.split()
    # If it looks like a NL question (>5 words), extract meaningful keywords
    if len(words) > 5:
        keywords = [w for w in words if w.lower() not in _NL_STOPWORDS and len(w) > 2]
        # Use up to 8 most meaningful keywords
        search_term = " ".join(keywords[:8]) if keywords else " ".join(words[:6])
    else:
        search_term = clean_query

    # Build arXiv query using title+abstract field search for <=5 words phrase (if quotes are present)
    # or general keyword search (parenthesized) to prevent strict phrase match issues
    if len(search_term.split()) <= 5:
        if '"' in search_term or "'" in search_term:
            encoded_query = urllib.parse.quote(f'all:"{search_term}"')
        else:
            encoded_query = urllib.parse.quote(f"all:({search_term})")
    else:
        # Title + abstract keyword search for longer keyword sets
        encoded_query = urllib.parse.quote(f"ti:{search_term} OR abs:{search_term}")
    url = f"https://export.arxiv.org/api/query?search_query={encoded_query}&max_results={limit}&sortBy=relevance"

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.get(url)
            if response.status_code != 200:
                log.warning(f"arXiv API returned status code {response.status_code}")
                return []

            # Parse Atom feed XML
            root = ET.fromstring(response.content)
            ns = {"atom": "http://www.w3.org/2005/Atom"}

            papers = []
            for entry in root.findall("atom:entry", ns):
                title_node = entry.find("atom:title", ns)
                summary_node = entry.find("atom:summary", ns)
                id_node = entry.find("atom:id", ns)
                published_node = entry.find("atom:published", ns)

                title = (
                    title_node.text.strip().replace("\n", " ")
                    if title_node is not None and title_node.text
                    else "Unknown Title"
                )
                summary = (
                    summary_node.text.strip().replace("\n", " ")
                    if summary_node is not None and summary_node.text
                    else "No Abstract Available"
                )
                if len(summary) > 600:
                    summary = summary[:600] + "..."

                # Extract arXiv ID and pdf link
                arxiv_url = id_node.text.strip() if id_node is not None and id_node.text else ""
                arxiv_id = arxiv_url.split("/abs/")[-1] if "/abs/" in arxiv_url else ""

                pdf_url = ""
                doi = ""
                journal_ref = ""
                for link in entry.findall("atom:link", ns):
                    if (
                        link.attrib.get("title") == "pdf"
                        or link.attrib.get("type") == "application/pdf"
                    ):
                        pdf_url = link.attrib.get("href", "")
                        break
                if not pdf_url and arxiv_id:
                    pdf_url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"

                # ── Enhanced: extra fields ──
                # DOI
                arxiv_ns = "http://arxiv.org/schemas/atom"
                doi_node = entry.find(f"{{{arxiv_ns}}}doi")
                if doi_node is not None and doi_node.text:
                    doi = doi_node.text.strip()

                # Journal reference
                jref_node = entry.find(f"{{{arxiv_ns}}}journal_ref")
                if jref_node is not None and jref_node.text:
                    journal_ref = jref_node.text.strip()

                # Comment field (often contains GitHub links)
                comment_node = entry.find(f"{{{arxiv_ns}}}comment")
                comment = ""
                if comment_node is not None and comment_node.text:
                    comment = comment_node.text.strip()

                # Categories (e.g. cs.LG, cs.CL)
                categories = []
                primary_cat_node = entry.find(f"{{{arxiv_ns}}}primary_category")
                if primary_cat_node is not None:
                    categories.append(primary_cat_node.attrib.get("term", ""))
                for cat_node in entry.findall("atom:category", ns):
                    term = cat_node.attrib.get("term", "")
                    if term and term not in categories:
                        categories.append(term)

                # Extract year
                published_date = (
                    published_node.text.strip()
                    if published_node is not None and published_node.text
                    else ""
                )
                year = published_date.split("-")[0] if published_date else "Unknown"

                # Extract authors
                authors = []
                for author_node in entry.findall("atom:author", ns):
                    name_node = author_node.find("atom:name", ns)
                    if name_node is not None and name_node.text:
                        authors.append(name_node.text.strip())

                papers.append(
                    {
                        "title": title,
                        "abstract": summary,
                        "authors": authors,
                        "year": year,
                        "url": arxiv_url,
                        "pdf_url": pdf_url,
                        "id": arxiv_id,
                        # Enhanced fields
                        "doi": doi,
                        "doi_url": f"https://doi.org/{doi}" if doi else "",
                        "journal_ref": journal_ref,
                        "comment": comment,
                        "categories": categories,
                        # These will be filled by S2/PwC enrichment
                        "citation_count": None,
                        "tldr": "",
                        "code_repos": [],
                        "datasets": [],
                        "has_code": False,
                    }
                )

            set_cache("api", ck, papers)
            return papers
    except Exception as e:
        log.warning(f"Error fetching from arXiv: {e}")
        return []


def format_arxiv_context(arxiv_papers: List[Dict]) -> str:
    if not arxiv_papers:
        return ""
    lines = ["=== LIVE ARXIV CROSS-REFERENCE EVIDENCE ==="]
    for i, p in enumerate(arxiv_papers):
        authors_str = ", ".join(p["authors"][:4]) if p["authors"] else "Unknown"
        cite_str = f" | Cited {p['citation_count']:,}×" if p.get("citation_count") else ""
        tldr_str = f"\n  TL;DR: {p['tldr']}" if p.get("tldr") else ""
        cats_str = f" | {', '.join(p['categories'][:3])}" if p.get("categories") else ""
        doi_str = f"\n  DOI: {p['doi_url']}" if p.get("doi_url") else ""
        jref_str = f" | Published in: {p['journal_ref']}" if p.get("journal_ref") else ""
        lines.append(
            f"[ArXiv-{i + 1}] {p['title']} ({p['year']}){cite_str}\n"
            f"  Authors: {authors_str} | ID: {p['id']}{cats_str}{jref_str}\n"
            f"  Abstract: {p['abstract']}{tldr_str}{doi_str}\n"
            f"  PDF: {p['pdf_url']}"
        )
    return "\n\n".join(lines)


def format_s2_context(s2_papers: List[Dict]) -> str:
    """Format Semantic Scholar search results as LLM context."""
    if not s2_papers:
        return ""
    lines = ["=== SEMANTIC SCHOLAR EVIDENCE ==="]
    for i, p in enumerate(s2_papers):
        authors_str = ", ".join((p.get("authors") or [])[:4]) or "Unknown"
        cite_str = f" | Cited {p['citation_count']:,}×" if p.get("citation_count") else ""
        tldr_str = f"\n  TL;DR: {p['tldr']}" if p.get("tldr") else ""
        fields_str = (
            f" | Fields: {', '.join(p['fields_of_study'][:3])}" if p.get("fields_of_study") else ""
        )
        pdf_str = f"\n  PDF: {p['pdf_url']}" if p.get("pdf_url") else ""
        s2_link = p.get("s2_url", "")
        doi_str = f" | DOI: {p.get('doi_url', '')}" if p.get("doi_url") else ""
        lines.append(
            f"[S2-{i + 1}] {p['title']} ({p.get('year', '?')}){cite_str}\n"
            f"  Authors: {authors_str}{fields_str}\n"
            f"  Abstract: {p.get('abstract', '')}{tldr_str}{doi_str}\n"
            f"  S2 Link: {s2_link}{pdf_str}"
        )
    return "\n\n".join(lines)


def format_pwc_context(arxiv_papers: List[Dict]) -> str:
    """Format Papers with Code & Hugging Face enrichment (repos, datasets, metrics, models, spaces) as LLM context."""
    entries = []
    for p in arxiv_papers:
        repos = p.get("code_repos") or []
        datasets = p.get("datasets") or []
        metrics = p.get("metrics") or []
        models = p.get("linked_models") or []
        spaces = p.get("linked_spaces") or []
        upvotes = p.get("hf_upvotes", 0)
        ai_summary = p.get("hf_ai_summary") or ""

        if (
            not repos
            and not datasets
            and not metrics
            and not models
            and not spaces
            and not upvotes
            and not ai_summary
        ):
            continue

        parts = [f"[{p.get('title', '?')}]"]
        if upvotes:
            parts.append(f"  Hugging Face Paper Upvotes: {upvotes}")
        if ai_summary:
            parts.append(f"  HF AI Summary: {ai_summary}")
        if repos:
            repo_strs = []
            for r in repos[:3]:
                star_str = f" ⭐{r['stars']:,}" if r.get("stars") else ""
                official_str = " (official)" if r.get("is_official") else ""
                repo_strs.append(f"{r['url']}{star_str}{official_str}")
            parts.append(f"  Code Repos: {' | '.join(repo_strs)}")
        if datasets:
            ds_strs = []
            for d in datasets[:5]:
                d_url = d.get("url")
                if d_url:
                    ds_strs.append(f"{d.get('name', '?')} ({d_url})")
                else:
                    ds_strs.append(d.get("name", "?"))
            parts.append(f"  Datasets: {' | '.join(ds_strs)}")
        if metrics:
            metric_strs = []
            for m in metrics[:5]:
                metric_strs.append(m["metric_string"])
            parts.append(f"  Extracted Benchmarks/Metrics: {' | '.join(metric_strs)}")
        if models:
            model_strs = []
            for m in models[:3]:
                m_url = m.get("url")
                if m_url:
                    model_strs.append(f"{m.get('id', '?')} ({m_url})")
                else:
                    model_strs.append(m.get("id", "?"))
            parts.append(f"  Linked HF Models: {' | '.join(model_strs)}")
        if spaces:
            space_strs = []
            for s in spaces[:3]:
                s_url = s.get("url")
                emoji = s.get("emoji", "🚀")
                if s_url:
                    space_strs.append(f"{emoji} {s.get('id', '?')} ({s_url})")
                else:
                    space_strs.append(f"{emoji} {s.get('id', '?')}")
            parts.append(f"  Linked HF Spaces: {' | '.join(space_strs)}")
        entries.append("\n".join(parts))

    if not entries:
        return ""
    return (
        "=== CODE, DATASETS, MODELS & SPACES (Papers with Code & Hugging Face) ===\n"
        + "\n\n".join(entries)
    )


async def run_vector_pipeline(
    query: str,
    embedding: List[float],
    top_k: int = 8,
    min_similarity: float = 0.28,
    filter_ids: Optional[List[str]] = None,
    tag: str = "",
) -> List[Dict]:
    """Runs full vector search pipeline: vector search -> filter relevance -> merge adjacent -> MMR rerank."""
    try:
        raw_chunks = await vector_search(embedding, min_similarity, top_k * 2, filter_ids)
    except VectorSearchError:
        return []

    if not raw_chunks:
        return []

    rel_chunks = filter_relevant_chunks(raw_chunks, floor=min_similarity)
    merged = merge_adjacent_chunks(rel_chunks)
    reranked = mmr_rerank(query, merged, lambda_param=MMR_LAMBDA)
    return reranked[:top_k]


def build_chronological_flow(*paper_lists: Optional[List[Dict]]) -> str:
    """Combines papers from multiple lists, sorts chronologically by published year/date, and formats a timeline summary string."""
    all_papers = []
    seen_titles = set()

    for plist in paper_lists:
        if not plist:
            continue
        for p in plist:
            if not isinstance(p, dict):
                continue
            title = (p.get("title") or p.get("name") or "").strip()
            if not title or title.lower() in seen_titles:
                continue
            seen_titles.add(title.lower())

            year = p.get("year") or p.get("published_year")
            if not year and p.get("published"):
                try:
                    year = int(str(p["published"])[:4])
                except (ValueError, TypeError):
                    year = None

            all_papers.append(
                {
                    "title": title,
                    "year": year or 9999,
                    "authors": p.get("authors") or [],
                    "summary": p.get("summary") or p.get("abstract") or "",
                    "url": p.get("pdf_url") or p.get("url") or "",
                }
            )

    all_papers.sort(key=lambda x: x["year"])

    lines = []
    for p in all_papers:
        yr_str = str(p["year"]) if p["year"] != 9999 else "Unknown Year"
        auth_str = (
            ", ".join(p["authors"][:2]) + (" et al." if len(p["authors"]) > 2 else "")
            if p["authors"]
            else "Unknown Authors"
        )
        lines.append(f"• [{yr_str}] {p['title']} — {auth_str}")
        if p["summary"]:
            lines.append(f"  Summary: {p['summary'][:150]}...")

    return "\n".join(lines)
