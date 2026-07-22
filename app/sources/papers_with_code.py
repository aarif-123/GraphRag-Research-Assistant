"""
Papers with Code / Hugging Face - GitHub & Dataset Extractor

Fetches paper metadata (official code repos, stars, linked models, datasets, and Spaces)
via the Hugging Face papers API, and falls back/merges with local regex extraction.
"""

import asyncio
import logging
import os
import re
from typing import Any, Dict, List, Optional

import httpx

log = logging.getLogger(__name__)

# GitHub link regex
_GITHUB_RE = re.compile(
    r"https?://github\.com/([\w\-\.]+/[\w\-\.]+)",
    re.IGNORECASE,
)

# Common dataset name patterns found in paper abstracts/titles
_DATASET_PATTERNS = [
    # NLP & Reasoning Benchmarks
    r"\b(MMLU(?:-Pro)?)\b",
    r"\b(GSM8K)\b",
    r"\b(MATH)\b",
    r"\b(HumanEval)\b",
    r"\b(MBPP)\b",
    r"\b(HellaSwag)\b",
    r"\b(ARC-Easy|ARC-Challenge|ARC)\b",
    r"\b(TruthfulQA)\b",
    r"\b(TriviaQA)\b",
    r"\b(SQuAD(?:\s*(?:2\.0|v\d))?)\b",
    r"\b(HotpotQA)\b",
    r"\b(Natural\s+Questions)\b",
    r"\b(GLUE|SuperGLUE)\b",
    r"\b(BIG-bench(?:-hard)?)\b",
    r"\b(DROP)\b",
    r"\b(WinoGrande)\b",
    r"\b(RULER)\b",
    r"\b(L-Eval)\b",
    # Agent & Code Benchmarks
    r"\b(SWE-bench(?:-Lite|-multimodal)?)\b",
    r"\b(Chatbot\s+Arena|LMSYS)\b",
    r"\b(WebArena)\b",
    r"\b(GAIA)\b",
    r"\b(AlpacaEval)\b",
    r"\b(MT-Bench)\b",
    # Vision & Multimodal Benchmarks
    r"\b(ImageNet(?:-1k|-21k|-\w+)?)\b",
    r"\b(COCO(?:\s\d{4})?)\b",
    r"\b(MS-?COCO)\b",
    r"\b(CIFAR-\d+)\b",
    r"\b(MNIST|Fashion-MNIST)\b",
    r"\b(LAION-[\w\d]+)\b",
    r"\b(nuScenes)\b",
    r"\b(KITTI)\b",
    r"\b(Waymo\s+Open\s+Dataset)\b",
    r"\b(MMBench)\b",
    r"\b(MMMU)\b",
    r"\b(MathVista)\b",
    # Text Corpora & Datasets
    r"\b(WikiText-\d+)\b",
    r"\b(Penn\s+Treebank|PTB)\b",
    r"\b(WMT\s*\d{2,4})\b",
    r"\b(OpenWebText)\b",
    r"\b(C4(?:\s+dataset)?)\b",
    r"\b(The\s+Pile)\b",
    r"\b(Common\s+Crawl)\b",
    r"\b(BooksCorpus)\b",
    r"\b(LibriSpeech)\b",
    r"\b(VoxCeleb\d?)\b",
]

_DATASET_RE = [re.compile(p, re.IGNORECASE) for p in _DATASET_PATTERNS]

_EXCLUDE_WORDS = {
    "the",
    "gpu",
    "cpu",
    "llm",
    "rag",
    "usa",
    "nlp",
    "api",
    "url",
    "pdf",
    "ram",
    "vram",
    "sgd",
    "tpu",
    "tmd",
    "web",
    "mlm",
    "clm",
    "clt",
    "cpe",
    "cot",
    "llms",
    "bert",
    "gpt",
    "lstm",
    "rnn",
    "cnn",
    "ann",
    "mlp",
    "sota",
    "loss",
    "optimizer",
    "adam",
    "relu",
    "gelu",
    "attention",
    "transformer",
    "transformers",
    "encoder",
    "decoder",
    "tokens",
    "token",
    "model",
    "models",
    "method",
    "methods",
    "approach",
    "framework",
    "architecture",
    "architectures",
    "paper",
    "papers",
    "author",
    "authors",
    "dataset",
    "datasets",
    "benchmark",
    "benchmarks",
    "corpus",
    "eval",
    "evaluation",
    "task",
    "tasks",
    "zero-shot",
    "few-shot",
    "fine-tuning",
    "pre-training",
    "training",
    "inference",
    "accuracy",
    "performance",
    "results",
    "result",
    "metrics",
    "metric",
    "baseline",
    "baselines",
    "state-of-the-art",
    "system",
    "systems",
    "our",
    "new",
    "method",
    "we",
    "this",
    "for",
    "and",
    "cites",
    "with",
    "from",
    "that",
    "than",
    "over",
    "under",
}


def _extract_github_links(text: str) -> List[Dict]:
    """Extract GitHub repository URLs from arbitrary text."""
    if not text:
        return []
    matches = _GITHUB_RE.findall(text)
    seen: set = set()
    repos = []
    for match in matches:
        stripped = match.rstrip('.,;)>"')
        url = f"https://github.com/{stripped}"
        if url not in seen:
            seen.add(url)
            repos.append(
                {
                    "url": url,
                    "name": match.rstrip(".,;)>\"'"),
                    "stars": None,
                    "framework": "",
                    "is_official": True,
                    "source": "arxiv_comment",
                }
            )
    return repos


def _extract_datasets(text: str) -> List[Dict]:
    """Extract known and heuristically identified dataset/benchmark names from paper text."""
    if not text:
        return []
    found: Dict[str, Dict] = {}

    # 1. Match specific common benchmark patterns
    for pattern in _DATASET_RE:
        for m in pattern.finditer(text):
            name = m.group(0).strip()
            if name not in found:
                slug = name.lower().replace(" ", "-").replace("/", "-")
                found[name] = {
                    "name": name,
                    "full_name": name,
                    "url": f"https://paperswithcode.com/dataset/{slug}",
                    "description": "",
                    "modalities": [],
                    "source": "abstract_extraction",
                }

    # 2. General dataset phrase matching (e.g., "MMLU benchmark", "LAION-5B dataset")
    general_matches = re.finditer(
        r"\b([A-Z][a-zA-Z0-9\-]{2,20}(?:\s+[A-Z][a-zA-Z0-9\-]{1,20})*)\s+(dataset|benchmark|corpus|eval|evaluation)\b",
        text,
    )
    for m in general_matches:
        name = m.group(1).strip()
        category = m.group(2).lower()
        if name.lower() not in _EXCLUDE_WORDS and name not in found:
            slug = name.lower().replace(" ", "-").replace("/", "-")
            found[name] = {
                "name": name,
                "full_name": name,
                "url": f"https://paperswithcode.com/dataset/{slug}",
                "description": f"Extracted {category}",
                "modalities": [],
                "source": f"general_{category}_extraction",
            }

    # 3. Capitalized acronym benchmark matching (e.g. GSM8K, MMLU)
    abbr_matches = re.finditer(r"\b([A-Z][A-Z0-9\-]{2,9})\b", text)
    for m in abbr_matches:
        name = m.group(1).strip().rstrip("-")
        if name.lower() not in _EXCLUDE_WORDS and name not in found:
            if len(name) >= 2 and (any(char.isdigit() for char in name) or len(name) >= 3):
                slug = name.lower().replace(" ", "-").replace("/", "-")
                found[name] = {
                    "name": name,
                    "full_name": name,
                    "url": f"https://paperswithcode.com/dataset/{slug}",
                    "description": "Extracted benchmark acronym",
                    "modalities": [],
                    "source": "abbr_benchmark_extraction",
                }

    return list(found.values())


def _extract_benchmarks_and_metrics(text: str) -> List[Dict]:
    """Extract quantitative benchmark performance results (e.g. '85.3% on MMLU') from paper abstract/title text."""
    if not text:
        return []
    import re

    found = []
    seen_benchmarks = set()

    # Split into sentence-like clauses
    sentences = re.split(r"\.(?!\d)|[!?;\n]\s*", text)
    for sent in sentences:
        if not sent.strip():
            continue

        # 1. Find all benchmark candidates (specific patterns + acronyms)
        benchmarks = []
        for pattern in _DATASET_RE:
            for m in pattern.finditer(sent):
                benchmarks.append((m.group(0).strip(), m.start()))

        abbr_matches = re.finditer(r"\b([A-Z][A-Z0-9\-]{2,9})\b", sent)
        for m in abbr_matches:
            name = m.group(1).strip().rstrip("-")
            if len(name) >= 2 and name.lower() not in _EXCLUDE_WORDS:
                if not any(name == b[0] for b in benchmarks):
                    benchmarks.append((name, m.start()))

        if not benchmarks:
            continue

        # 2. Find all numeric/percentage values
        numbers = []
        num_matches = re.finditer(r"\b\d+(?:\.\d+)?%?", sent)
        for m in num_matches:
            numbers.append((m.group(0).strip(), m.start()))

        if not numbers:
            continue

        # 3. For each benchmark, find the closest number in the sentence
        for bench_name, bench_pos in benchmarks:
            slug = bench_name.lower()
            if slug in seen_benchmarks:
                continue

            closest_num, min_dist = None, float("inf")
            for num_val, num_pos in numbers:
                dist = abs(bench_pos - num_pos)
                if dist < min_dist:
                    min_dist = dist
                    closest_num = num_val

            # Only link if they are relatively close (e.g. within 60 characters)
            if closest_num and min_dist < 60:
                seen_benchmarks.add(slug)
                found.append(
                    {"benchmark": bench_name, "metric_string": f"{closest_num} on {bench_name}"}
                )

    return found


import time

_HF_CACHE: Dict[str, Any] = {}
_HF_CACHE_TTL = 43200  # 12 hours


def _get_hf_cache(key: str):
    entry = _HF_CACHE.get(key)
    if entry and time.time() - entry[1] < _HF_CACHE_TTL:
        return entry[0]
    return None


def _set_hf_cache(key: str, val):
    _HF_CACHE[key] = (val, time.time())


async def fetch_hf_paper_metadata(arxiv_id: str) -> Optional[Dict[str, Any]]:
    """Fetch paper metadata from Hugging Face Papers API."""
    if not arxiv_id:
        return None
    # Remove version suffix if any (e.g. 1706.03762v5 -> 1706.03762)
    clean_id = re.sub(r"v\d+$", "", arxiv_id)

    cached = _get_hf_cache(clean_id)
    if cached is not None:
        log.debug(f"HF paper cache hit for {clean_id}")
        return cached

    url = f"https://huggingface.co/api/papers/{clean_id}"

    headers = {
        "User-Agent": "Aether-Research-Assistant/5.0",
    }
    hf_token = os.getenv("HF_TOKEN", "")
    if hf_token:
        headers["Authorization"] = f"Bearer {hf_token}"

    try:
        async with httpx.AsyncClient(timeout=1.5) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 200:
                res = resp.json()
                _set_hf_cache(clean_id, res)
                return res
            else:
                log.debug(f"HF paper lookup returned {resp.status_code} for {clean_id}")
    except Exception as e:
        log.warning(f"Error fetching HF paper metadata for {clean_id}: {e}")
    return None


def enrich_paper_with_code_and_datasets(paper: Dict) -> Dict:
    """
    Enrich a single arXiv paper dict with code repos and dataset names offline.
    This is kept for backward compatibility and synchronous contexts.
    """
    comment_str = paper.get("comment") or ""
    abstract_str = paper.get("abstract") or ""
    title_str = paper.get("title") or ""

    comment_repos = _extract_github_links(comment_str)
    abstract_repos = _extract_github_links(abstract_str)

    seen_urls: set = set()
    code_repos = []
    for r in comment_repos + abstract_repos:
        if r["url"] not in seen_urls:
            seen_urls.add(r["url"])
            code_repos.append(r)

    search_text = abstract_str + " " + title_str
    datasets = _extract_datasets(search_text)
    metrics = _extract_benchmarks_and_metrics(search_text)

    return {
        **paper,
        "code_repos": code_repos,
        "datasets": datasets,
        "metrics": metrics,
        "has_code": len(code_repos) > 0,
    }


async def enrich_arxiv_papers_with_pwc(arxiv_papers: List[Dict]) -> List[Dict]:
    """
    Enrich each arXiv paper with GitHub links, datasets, models, spaces, and HF upvotes.
    Retrieves from Hugging Face papers API where available, and falls back/merges
    with offline regex extraction.
    """
    if not arxiv_papers:
        return []

    async def _enrich_one(paper: Dict) -> Dict:
        arxiv_id = paper.get("id") or paper.get("arxiv_id") or ""
        hf_data = None
        if arxiv_id:
            hf_data = await fetch_hf_paper_metadata(arxiv_id)

        # Start with a copy of the paper dict
        enriched = dict(paper)

        # Initialize lists
        code_repos = []
        datasets = []
        linked_models = []
        linked_spaces = []
        hf_upvotes = 0
        hf_summary = ""
        hf_keywords: List[str] = []

        seen_repos = set()
        seen_datasets = set()

        # 1. Process Hugging Face API data if available
        if hf_data:
            # Code Repo
            repo_url = hf_data.get("githubRepo")
            if repo_url and isinstance(repo_url, str):
                repo_url = repo_url.rstrip("/")
                name_match = re.search(
                    r"github\.com/([\w\-\.]+/[\w\-\.]+)", repo_url, re.IGNORECASE
                )
                repo_name = name_match.group(1) if name_match else repo_url.split("/")[-1]

                code_repos.append(
                    {
                        "url": repo_url,
                        "name": repo_name,
                        "stars": hf_data.get("githubStars"),
                        "framework": "",
                        "is_official": True,
                        "source": "huggingface_api",
                    }
                )
                seen_repos.add(repo_url.lower())

            # Linked Datasets
            for item in hf_data.get("linkedDatasets") or []:
                dataset_id = item.get("id")
                if dataset_id:
                    datasets.append(
                        {
                            "name": dataset_id,
                            "full_name": dataset_id,
                            "url": f"https://huggingface.co/datasets/{dataset_id}",
                            "description": f"Hugging Face dataset: {item.get('downloads', 0)} downloads, {item.get('likes', 0)} likes.",
                            "modalities": item.get("datasetsServerInfo", {}).get("modalities", []),
                            "source": "huggingface_linked",
                        }
                    )
                    seen_datasets.add(dataset_id.lower())

            # Linked Models
            for item in hf_data.get("linkedModels") or []:
                model_id = item.get("id")
                if model_id:
                    linked_models.append(
                        {
                            "id": model_id,
                            "author": item.get("author"),
                            "likes": item.get("likes", 0),
                            "downloads": item.get("downloads", 0),
                            "url": f"https://huggingface.co/{model_id}",
                        }
                    )

            # Linked Spaces
            for item in hf_data.get("linkedSpaces") or []:
                space_id = item.get("id")
                if space_id:
                    linked_spaces.append(
                        {
                            "id": space_id,
                            "emoji": item.get("emoji", "🚀"),
                            "url": f"https://huggingface.co/spaces/{space_id}",
                        }
                    )

            hf_upvotes = hf_data.get("upvotes") or 0
            hf_summary = hf_data.get("ai_summary") or ""
            hf_keywords = hf_data.get("ai_keywords") or []

        # 2. Extract offline links (arXiv comments & abstract) to complement
        comment_str = paper.get("comment") or ""
        abstract_str = paper.get("abstract") or ""
        title_str = paper.get("title") or ""

        local_repos = _extract_github_links(comment_str) + _extract_github_links(abstract_str)
        for repo in local_repos:
            url_lower = repo["url"].lower()
            if url_lower not in seen_repos:
                seen_repos.add(url_lower)
                code_repos.append(repo)

        search_text = abstract_str + " " + title_str
        local_datasets = _extract_datasets(search_text)
        for ds in local_datasets:
            name_lower = ds["name"].lower()
            if name_lower not in seen_datasets:
                seen_datasets.add(name_lower)
                datasets.append(ds)

        # 3. Merge into the enriched paper dict
        metrics = _extract_benchmarks_and_metrics(search_text)
        enriched["code_repos"] = code_repos
        enriched["datasets"] = datasets
        enriched["metrics"] = metrics
        enriched["linked_models"] = linked_models
        enriched["linked_spaces"] = linked_spaces
        enriched["hf_upvotes"] = hf_upvotes
        enriched["hf_ai_summary"] = hf_summary
        enriched["hf_ai_keywords"] = hf_keywords
        enriched["has_code"] = len(code_repos) > 0

        return enriched

    tasks = [_enrich_one(p) for p in arxiv_papers]
    return await asyncio.gather(*tasks)


async def get_paper_repos_pwc(arxiv_id: str) -> List[Dict]:
    """Retrieve GitHub repositories for an arXiv ID."""
    hf_data = await fetch_hf_paper_metadata(arxiv_id)
    repos = []
    if hf_data and hf_data.get("githubRepo"):
        repo_url = hf_data["githubRepo"]
        repo_url = repo_url.rstrip("/")
        name_match = re.search(r"github\.com/([\w\-\.]+/[\w\-\.]+)", repo_url, re.IGNORECASE)
        repo_name = name_match.group(1) if name_match else repo_url.split("/")[-1]

        repos.append(
            {
                "url": repo_url,
                "name": repo_name,
                "stars": hf_data.get("githubStars"),
                "framework": "",
                "is_official": True,
                "source": "huggingface_api",
            }
        )
    return repos


async def get_paper_datasets_pwc(arxiv_id: str) -> List[Dict]:
    """Retrieve datasets for an arXiv ID."""
    hf_data = await fetch_hf_paper_metadata(arxiv_id)
    datasets = []
    if hf_data:
        for item in hf_data.get("linkedDatasets") or []:
            dataset_id = item.get("id")
            if dataset_id:
                datasets.append(
                    {
                        "name": dataset_id,
                        "full_name": dataset_id,
                        "url": f"https://huggingface.co/datasets/{dataset_id}",
                        "description": f"Hugging Face dataset: {item.get('downloads', 0)} downloads, {item.get('likes', 0)} likes.",
                        "modalities": item.get("datasetsServerInfo", {}).get("modalities", []),
                        "source": "huggingface_linked",
                    }
                )
    return datasets
