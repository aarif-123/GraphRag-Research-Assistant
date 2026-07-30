"""
utils/misc.py — Miscellaneous utilities: link extraction, credential masking,
link resolution, HuggingFace dataset search, and dataset/repo retrieval.
"""

import asyncio
import re
import urllib.parse
from typing import Any, Dict, List, Optional, Tuple

import httpx

from app.clients.pool import cache_key, get_cache, set_cache
from app.config import log

# ──────────────────────────────────────────────────────────────────────────────
# URL DETECTION
# ──────────────────────────────────────────────────────────────────────────────


def extract_paper_urls(text: str) -> List[str]:
    """Extract arXiv and PDF URLs from arbitrary text."""
    urls = re.findall(r"https?://[^\s]+", text)
    paper_urls = []
    for url in urls:
        url = url.rstrip(".,;()[]{}")  # strip trailing punctuation
        is_arxiv = "arxiv.org" in url
        is_pdf = url.lower().endswith(".pdf") or "/pdf/" in url.lower()
        if is_arxiv or is_pdf:
            paper_urls.append(url)
    return paper_urls


def is_simple_link_paste(text: str, urls: List[str]) -> bool:
    """Return True when the user message is nothing more than a link paste,
    with at most a few generic words like 'summarize' or 'read'.
    """
    cleaned = text
    for url in urls:
        cleaned = cleaned.replace(url, "")
    cleaned = re.sub(r"[^a-zA-Z0-9\s]", "", cleaned).strip().lower()
    if len(cleaned) < 10:
        return True

    words = cleaned.split()
    summary_words = {
        "summarize",
        "summarise",
        "summary",
        "parse",
        "read",
        "pdf",
        "paper",
        "analyze",
        "analyse",
        "this",
        "explain",
        "about",
        "what",
        "is",
        "intro",
        "introduction",
    }
    if all(w in summary_words for w in words):
        return True

    return False


# ──────────────────────────────────────────────────────────────────────────────
# CREDENTIAL MASKING
# ──────────────────────────────────────────────────────────────────────────────


def mask_credentials_and_secrets(text: str) -> str:
    """Masks API keys, database URIs, passwords, and private document URLs
    in LLM outputs before they are returned to the client.
    """
    if not text:
        return text

    # 1. Mask JWTs / Supabase tokens
    text = re.sub(
        r"\beyJhbGciOi[a-zA-Z0-9_\-\.]+\.[a-zA-Z0-9_\-\.]+\.[a-zA-Z0-9_\-\.]+\b",
        "[MASKED_TOKEN]",
        text,
    )
    text = re.sub(r"\beyJhbGciOi[a-zA-Z0-9_\-\.]{50,}\b", "[MASKED_TOKEN]", text)

    # 2. Known API key prefixes
    text = re.sub(r"\bgsk_[a-zA-Z0-9_\-]{30,}\b", "[MASKED_GROQ_KEY]", text)
    text = re.sub(r"\bhf_[a-zA-Z0-9_\-]{30,}\b", "[MASKED_HF_TOKEN]", text)
    text = re.sub(r"\brzp_[a-zA-Z0-9_\-]{10,}\b", "[MASKED_RAZORPAY_KEY]", text)

    # 3. Database connection string passwords
    text = re.sub(
        r"(\b[a-zA-Z0-9\+\-]+:\/\/)([^:\s]+):([^@\/\s]+)(@[^\s]+)",
        lambda m: f"{m.group(1)}{m.group(2)}:[MASKED_PASSWORD]{m.group(4)}",
        text,
    )

    # 4. Generic key=value credential patterns
    pattern = r'(?i)\b(api[-_]?key|client[-_]?secret|password|access[-_]?token|auth[-_]?token|rest[-_]?token|secret[-_]?key)\b(\s*[:=]\s*["\']?)([a-zA-Z0-9_\-]{12,})(["\']?)'
    text = re.sub(pattern, lambda m: f"{m.group(1)}{m.group(2)}[MASKED_SECRET]{m.group(4)}", text)

    # 5. Local uploaded PDF links
    text = re.sub(
        r"\[([^\]]+)\]\((?:https?://[a-zA-Z0-9\.\-]+:\d+)?/api/pdf/[a-zA-Z0-9\-]+\.pdf\)",
        r"[\1]",
        text,
    )
    text = re.sub(
        r"(?:https?://[a-zA-Z0-9\.\-]+:\d+)?/api/pdf/[a-zA-Z0-9\-]+\.pdf", "[Uploaded PDF]", text
    )

    return text


# ──────────────────────────────────────────────────────────────────────────────
# LINK RESOLUTION
# ──────────────────────────────────────────────────────────────────────────────


def clean_and_resolve_links(
    answer: str,
    chunks: Optional[List[Dict]] = None,
    graph_nodes: Optional[List[Dict]] = None,
    arxiv_papers: Optional[List[Dict]] = None,
) -> str:
    """Validate and replace hallucinated or placeholder links in the response
    with real arXiv or Google Scholar URLs derived from retrieved evidence.
    """

    # 1. Build a map of 1-based indices → real arXiv URLs
    arxiv_map: Dict[int, Dict] = {}
    if arxiv_papers:
        for idx, p in enumerate(arxiv_papers):
            pdf_url = p.get("pdf_url") or p.get("url") or f"https://arxiv.org/abs/{p.get('id', '')}"
            url = p.get("url") or pdf_url
            arxiv_map[idx + 1] = {
                "pdf_url": pdf_url,
                "url": url,
                "title": p.get("title", ""),
                "id": p.get("id", ""),
            }

    # 2. Build a map of 1-based indices → database chunk Scholar links
    chunk_map: Dict[int, Dict] = {}
    if chunks:
        for idx, c in enumerate(chunks):
            title = c.get("title") or c.get("paper_title") or ""
            if title:
                encoded_title = urllib.parse.quote_plus(title)
                scholar_url = f"https://scholar.google.com/scholar?q={encoded_title}"
                chunk_map[idx + 1] = {"url": scholar_url, "title": title}

    # 3. Build a title-to-Scholar URL map for graph papers
    graph_map: Dict[str, str] = {}
    if graph_nodes:
        for n in graph_nodes:
            title = n.get("title")
            if title:
                encoded_title = urllib.parse.quote_plus(title)
                scholar_url = f"https://scholar.google.com/scholar?q={encoded_title}"
                graph_map[title.lower()] = scholar_url

    def link_replacer(match: re.Match) -> str:
        text = match.group(1).strip()
        url = match.group(2).strip()

        url_lower = url.lower()
        is_placeholder = (
            any(
                x in url_lower
                for x in ["pdf_url", "arxiv_url", "placeholder", "fake", "link", "url"]
            )
            or url == "#"
            or not url.startswith("http")
        )

        # ArXiv-N citations
        arxiv_cite = re.search(r"arxiv-(\d+)", text.lower())
        if arxiv_cite:
            num = int(arxiv_cite.group(1))
            if num in arxiv_map:
                return f"[{text}]({arxiv_map[num]['pdf_url']})"

        # Numeric [N] citations
        num_cite = re.search(r"^\[?(\d+)\]?$", text)
        if num_cite:
            num = int(num_cite.group(1))
            if num in chunk_map:
                return f"[{text}]({chunk_map[num]['url']})"

        # ArXiv ID or title in URL
        for num, p in arxiv_map.items():
            if p["id"] and p["id"] in url:
                return f"[{text}]({p['pdf_url']})"
            if p["title"] and p["title"].lower() in text.lower():
                return f"[{text}]({p['pdf_url']})"

        # Graph paper title match
        for t_lower, s_url in graph_map.items():
            if t_lower in text.lower() or t_lower in url_lower:
                return f"[{text}]({s_url})"

        if is_placeholder:
            for num, p in arxiv_map.items():
                if p["title"] and len(p["title"]) > 10 and p["title"].lower()[:25] in text.lower():
                    return f"[{text}]({p['pdf_url']})"
            for t_lower, s_url in graph_map.items():
                if len(t_lower) > 10 and t_lower[:25] in text.lower():
                    return f"[{text}]({s_url})"
            return text

        return match.group(0)

    answer = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", link_replacer, answer)

    def arxiv_link_placeholder_replacer(match: re.Match) -> str:
        num = int(match.group(1))
        between = match.group(2)
        if num in arxiv_map:
            return f"[ArXiv-{num}]{between}[ArXiv Link]({arxiv_map[num]['pdf_url']})"
        return match.group(0)

    answer = re.sub(
        r"\[ArXiv-(\d+)\]([^\n]{0,150}?)(?:\[ArXiv Link\]|\[PDF Link\]|\[PDF\]|\[Link\])\(([^)]*)\)",
        arxiv_link_placeholder_replacer,
        answer,
    )

    def standard_link_placeholder_replacer(match: re.Match) -> str:
        num = int(match.group(1))
        between = match.group(2)
        if num in chunk_map:
            return f"[{num}]{between}[Google Scholar]({chunk_map[num]['url']})"
        return match.group(0)

    answer = re.sub(
        r"\[(\d+)\]([^\n]{0,150}?)(?:\[Google Scholar\]|\[Scholar Link\]|\[Link\])\(([^)]*)\)",
        standard_link_placeholder_replacer,
        answer,
    )

    def arxiv_tag_replacer(match: re.Match) -> str:
        num = int(match.group(1))
        if num in arxiv_map:
            return f"[ArXiv-{num}]({arxiv_map[num]['pdf_url']})"
        return match.group(0)

    answer = re.sub(r"\[ArXiv-(\d+)\](?!\()", arxiv_tag_replacer, answer)

    def chunk_tag_replacer(match: re.Match) -> str:
        num = int(match.group(1))
        if num in chunk_map:
            return f"[{num}]({chunk_map[num]['url']})"
        return match.group(0)

    answer = re.sub(r"\[(\d+)\](?!\()", chunk_tag_replacer, answer)

    answer = re.sub(r"\((pdf_url|url|arxiv_url|placeholder|link)\)", "", answer)
    answer = mask_credentials_and_secrets(answer)
    return answer


# ──────────────────────────────────────────────────────────────────────────────
# DATASET & REPO RETRIEVAL
# ──────────────────────────────────────────────────────────────────────────────


async def search_huggingface_datasets(query: str, limit: int = 5) -> List[Dict[str, Any]]:
    """Search Hugging Face Hub for datasets matching a query."""
    if not query.strip():
        return []

    ck = cache_key("hf_datasets", query, limit)
    cached = get_cache("api", ck)
    if cached is not None:
        log.info(f"Cache HIT for HF datasets query: {query}")
        return cached

    url = "https://huggingface.co/api/datasets"
    params = {"search": query, "limit": limit}
    headers = {"User-Agent": "Aether-Research-Assistant/5.0 (contact@aether-assistant.org)"}
    try:
        async with httpx.AsyncClient(headers=headers, timeout=3.5) as client:
            resp = await client.get(url, params=params)
            if resp.status_code != 200:
                return []
            data = resp.json()
            results = []
            for item in data[:limit]:
                dataset_id = item.get("id")
                if dataset_id:
                    results.append(
                        {
                            "name": dataset_id,
                            "full_name": dataset_id,
                            "url": f"https://huggingface.co/datasets/{dataset_id}",
                            "description": f"Hugging Face dataset: {item.get('downloads', 0)} downloads, {item.get('likes', 0)} likes.",
                            "modalities": [],
                            "source": "huggingface_search",
                        }
                    )
            set_cache("api", ck, results)
            return results
    except Exception as e:
        log.error(f"Error querying Hugging Face datasets API: {e}")
        return []


async def suggest_datasets_for_query(query: str) -> List[str]:
    """Use an LLM to suggest 1-3 canonical academic dataset names for a query."""
    from app.clients.groq import groq_chat  # lazy import
    from app.config import REASON_MODEL

    if not query.strip():
        return []

    ck = cache_key("suggested_datasets", query)
    cached = get_cache("api", ck)
    if cached is not None:
        return cached

    sys_p = (
        "You are Aether, an academic research assistant. Given a research query, identify up to 3 canonical, "
        "widely-used benchmark datasets that are highly relevant to the topic. "
        "Respond ONLY with a valid JSON object containing a 'datasets' key with a list of dataset name strings. "
        "Do NOT include any explanations, introduction, markdown blocks, or extra text. "
        'Example output: {"datasets": ["Cora", "CiteSeer", "PubMed"]}'
    )
    try:
        raw = await groq_chat(
            [{"role": "system", "content": sys_p}, {"role": "user", "content": query}],
            model=REASON_MODEL,
            temperature=0.0,
            max_tokens=100,
            json_mode=True,
            purpose="plan",
        )
        import json

        data = json.loads(raw.strip())
        suggested = data.get("datasets", [])
        if isinstance(suggested, list):
            res = [str(s).strip() for s in suggested if s][:3]
            set_cache("api", ck, res)
            return res
    except Exception as e:
        log.warning(f"Failed to suggest datasets via LLM: {e}")
    return []


async def retrieve_datasets_and_repos(
    query: str,
    arxiv_papers: List[Dict],
    s2_papers: List[Dict],
    graph_nodes: List[Dict],
) -> Tuple[List[Dict], List[Dict]]:
    """Enrich all retrieved papers and search Kaggle/HF Hub to return a list of
    datasets and code repositories relevant to the papers or query.
    """
    try:
        from app.sources.kaggle import search_kaggle_datasets_bulk
    except ImportError:
        try:
            from sources.kaggle import search_kaggle_datasets_bulk
        except ImportError:

            async def search_kaggle_datasets_bulk(q: str, **kw: Any) -> List[Dict]:
                return []

    try:
        from app.sources.papers_with_code import enrich_arxiv_papers_with_pwc
    except ImportError:

        async def enrich_arxiv_papers_with_pwc(papers: List[Dict], **kw: Any) -> List[Dict]:
            return papers

    # 1. Parallel PwC enrichment for all paper lists
    tasks = [
        enrich_arxiv_papers_with_pwc(arxiv_papers),
        enrich_arxiv_papers_with_pwc(s2_papers),
        enrich_arxiv_papers_with_pwc(graph_nodes),
    ]

    try:
        res_results = await asyncio.gather(*tasks)
    except Exception as e:
        log.warning(f"PwC enrichment failed: {e}")
        res_results = [[], [], []]

    res_arxiv = res_results[0] or []
    res_s2 = res_results[1] or []
    res_graph = res_results[2] or []

    if arxiv_papers and len(res_arxiv) == len(arxiv_papers):
        for idx, p in enumerate(res_arxiv):
            arxiv_papers[idx].update(p)
    if s2_papers and len(res_s2) == len(s2_papers):
        for idx, p in enumerate(res_s2):
            s2_papers[idx].update(p)
    if graph_nodes and len(res_graph) == len(graph_nodes):
        for idx, p in enumerate(res_graph):
            graph_nodes[idx].update(p)

    # Collect mentioned datasets and repos
    all_datasets: List[Dict] = []
    all_repos: List[Dict] = []
    mentioned_ds_names: set = set()

    for papers_list in (arxiv_papers, s2_papers, graph_nodes):
        if not papers_list:
            continue
        for p in papers_list:
            if isinstance(p, dict):
                for ds in p.get("datasets") or []:
                    all_datasets.append(ds)
                    if isinstance(ds, dict) and ds.get("name"):
                        mentioned_ds_names.add(ds["name"])
                for repo in p.get("code_repos") or []:
                    all_repos.append(repo)

    # 2. Search Kaggle & HF in parallel for LLM-suggested + top mentioned datasets
    llm_suggested = await suggest_datasets_for_query(query)
    log.info(f"LLM suggested benchmark datasets for query '{query}': {llm_suggested}")

    search_queries = list(llm_suggested)
    for ds_name in list(mentioned_ds_names)[:2]:
        if not any(
            ds_name.lower() in sq.lower() or sq.lower() in ds_name.lower() for sq in search_queries
        ):
            search_queries.append(ds_name)

    search_tasks = []
    for sq in search_queries:
        search_tasks.append(search_kaggle_datasets_bulk(sq, limit=3))
        search_tasks.append(search_huggingface_datasets(sq, limit=3))

    try:
        search_results = await asyncio.gather(*search_tasks)
        for results in search_results:
            if results:
                all_datasets.extend(results)
    except Exception as e:
        log.warning(f"Error searching datasets in bulk: {e}")

    # 3. Deduplicate datasets by name/slug
    seen_ds: set = set()
    unique_datasets: List[Dict] = []
    for d in all_datasets:
        if not isinstance(d, dict):
            continue
        name = d.get("name") or d.get("full_name") or ""
        if name:
            slug = name.lower().strip()
            if slug not in seen_ds:
                seen_ds.add(slug)
                unique_datasets.append(
                    {
                        "name": name,
                        "full_name": d.get("full_name") or name,
                        "url": d.get("url")
                        or f"https://paperswithcode.com/dataset/{slug.replace(' ', '-')}",
                        "description": d.get("description") or "",
                        "modalities": d.get("modalities") or [],
                        "source": d.get("source") or "paper_extracted",
                    }
                )

    # Deduplicate repos by URL
    seen_repos: set = set()
    unique_repos: List[Dict] = []
    for r in all_repos:
        if not isinstance(r, dict):
            continue
        url = r.get("url") or ""
        if url:
            url_clean = url.lower().rstrip("/").strip()
            if url_clean not in seen_repos:
                seen_repos.add(url_clean)
                unique_repos.append(r)

    return unique_datasets, unique_repos
