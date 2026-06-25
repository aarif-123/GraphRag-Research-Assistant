


"""
GraphRAG Research API v4.0 — Aether Intelligence Edition

What's new over v3.1:
─────────────────────────────────────────────────────────────────────────────
BRAIN LAYER
  • Single unified plan_query() replaces 2 sequential LLM calls (intent + keywords)
  • Super-Master strategic prompt: pronoun resolution, route, anchors, metrics, cache_key
  • Structured JSON plan drives every downstream decision

GRAPH INTELLIGENCE (full Neo4j utilisation)
  • Paper ranking: exact-match > substring > word-overlap > recency scoring
  • Citation network traversal: co-citation analysis, bibliographic coupling
  • Author collaboration graph: co-author networks, prolific author detection
  • Venue/conference clustering: papers from same top venues
  • Domain taxonomy traversal: sibling-domain expansion
  • Relationship-aware context: CITES, WRITTEN_BY, PUBLISHED_IN, SIMILAR_TO
  • Graph-path narrative: explains WHY papers are related

RETRIEVAL PIPELINE
  • Three-tier search: seed-exact → seed-fuzzy → expanded graph neighbours
  • Section-aware chunking priority: abstract → conclusion → body
  • Diversity re-ranking: MMR (Maximal Marginal Relevance) prevents redundancy
  • Cross-paper evidence linking: same claim found in multiple papers → higher weight

ANSWER QUALITY
  • Route-specific prompts: compare, synthesise, explain, entity, timeline, survey
  • Structured evidence blocks: each claim backed by paper + chunk + relationship
  • Citation graph in response: shows paper relationships visually as text
  • Relationship narrative: "Paper A cites Paper B which shares author C with Paper D"
  • Confidence-weighted answer: high/medium/low confidence per claim

NEW ENDPOINTS
  • GET  /api/graph/paper/{id}          — full paper node with all relationships
  • GET  /api/graph/author/{name}        — author ego-network
  • POST /api/graph/citation-path        — shortest citation path between two papers
  • POST /api/graph/compare             — deep structured comparison
  • GET  /api/graph/trending            — trending papers (high recent citation velocity)
  • POST /api/research/timeline         — chronological evolution of a topic
  • POST /api/research/survey           — auto-generate mini literature survey
  • GET  /api/stats                      — database statistics
─────────────────────────────────────────────────────────────────────────────
"""

import os
import re
import json
import time
import platform
import collections

# Bypass Windows WMI service hang by mocking platform uname
_UnameResult = collections.namedtuple("uname_result", ["system", "node", "release", "version", "machine", "processor"])
platform.uname = lambda: _UnameResult("Windows", "localhost", "10", "10.0.19045", "AMD64", "Intel")
platform.win32_ver = lambda *args, **kwargs: ("10", "10.0.19045", "", "")
import uuid
import hashlib
import asyncio
import logging
from typing import Optional, List, Dict, Any, Tuple
from contextlib import asynccontextmanager
from pathlib import Path
from dataclasses import dataclass, field

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

import httpx
import fitz
import numpy as np

from supabase import create_client
from neo4j import GraphDatabase, exceptions as neo4j_exceptions
from dotenv import load_dotenv
try:
    from sentence_transformers import SentenceTransformer
except ImportError:
    SentenceTransformer = None

import threading

# External source connectors (Semantic Scholar, Papers with Code)
try:
    from sources.semantic_scholar import (
        enrich_arxiv_papers_with_s2,
        search_papers_s2,
    )
    from sources.papers_with_code import (
        enrich_arxiv_papers_with_pwc,
    )
    _SOURCES_AVAILABLE = True
except ImportError:
    _SOURCES_AVAILABLE = False
    log = logging.getLogger("graphrag")
    log.warning("External source connectors not found — S2/PwC disabled")
    async def enrich_arxiv_papers_with_s2(papers, **kw): return papers
    async def search_papers_s2(query, **kw): return []
    async def enrich_arxiv_papers_with_pwc(papers, **kw): return papers

# ================================================================
# THREAD-LOCAL SUPABASE CLIENT
# ================================================================

_supabase_local = threading.local()


def get_supabase_client():
    if not hasattr(_supabase_local, "client"):
        _supabase_local.client = create_client(SUPABASE_URL, SUPABASE_KEY)
    return _supabase_local.client


# ================================================================
# ENV
# ================================================================

load_dotenv(".env.local", override=True)
load_dotenv(".env", override=False)

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
NEO4J_URI = os.getenv("NEO4J_URI")
NEO4J_USER = os.getenv("NEO4J_USER")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
HF_TOKEN = os.getenv("HF_TOKEN")

EMBED_MODEL = os.getenv("EMBED_MODEL", "BAAI/bge-base-en")
REASON_MODEL = os.getenv("REASON_MODEL", "llama-3.3-70b-versatile")
HEAVY_MODEL = os.getenv("HEAVY_MODEL", "llama-3.3-70b-versatile")
PLAN_MODEL = os.getenv("PLAN_MODEL", "llama-3.1-8b-instant")  # strategic brain

MAX_GRAPH_NODES = int(os.getenv("MAX_GRAPH_NODES", "25"))
GROQ_TIMEOUT = int(os.getenv("GROQ_TIMEOUT", "30"))
EMBED_TIMEOUT = int(os.getenv("EMBED_TIMEOUT", "20"))
RATE_LIMIT = int(os.getenv("RATE_LIMIT_PER_MIN", "30"))
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "60"))
RELEVANCE_FLOOR = float(os.getenv("RELEVANCE_FLOOR", "0.22"))
MMR_LAMBDA = float(os.getenv("MMR_LAMBDA", "0.6"))  # diversity weight
CACHE_TTL = int(os.getenv("CACHE_TTL", "300"))  # 5 min
CACHE_MAX = int(os.getenv("CACHE_MAX", "512"))

_REQUIRED = [
    "SUPABASE_URL",
    "SUPABASE_SERVICE_ROLE_KEY",
    "NEO4J_URI",
    "NEO4J_USER",
    "NEO4J_PASSWORD",
]
for _v in _REQUIRED:
    if not os.getenv(_v):
        raise RuntimeError(f"Missing required environment variable: {_v}")


# ================================================================
# LOGGING
# ================================================================

_log_handlers = [logging.StreamHandler()]
if not os.getenv("VERCEL"):
    _log_handlers.insert(0, logging.FileHandler("app.log", encoding="utf-8"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    handlers=_log_handlers,
)
log = logging.getLogger("graphrag")


# ================================================================
# CUSTOM EXCEPTIONS
# ================================================================


class EmbeddingError(Exception):
    pass


class GraphRetrievalError(Exception):
    pass


class VectorSearchError(Exception):
    pass


class LLMError(Exception):
    pass


class PlanError(Exception):
    pass


# ================================================================
# IN-MEMORY LRU CACHE  (graph · embed · llm · plan · relations · api)
# ================================================================

import contextvars
# ContextVar to isolate caches and user personal documents/conversations
current_user_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("current_user_id", default=None)


def cache_key(*args) -> str:
    raw = "|".join(str(a) for a in args)
    return hashlib.md5(raw.encode()).hexdigest()


CACHE: Dict[str, Dict[str, Any]] = {
    "graph": {},
    "embed": {},
    "llm": {},
    "plan": {},
    "relations": {},
    "api": {},  # Cache bucket for public API fetched data
}


def get_cache(bucket: str, key: str):
    # Partition user personal document and conversation cache (llm, plan, relations) by user ID
    user_id = current_user_id.get()
    if bucket in ("llm", "plan", "relations") and user_id:
        key = f"{user_id}:{key}"

    entry = CACHE[bucket].get(key)
    if not entry:
        return None
        
    # Custom TTL for public API queries: 12 hours (43200s), default is CACHE_TTL
    ttl = 43200 if bucket == "api" else CACHE_TTL
    if time.time() - entry["ts"] > ttl:
        CACHE[bucket].pop(key, None)
        return None
    return entry["v"]


def set_cache(bucket: str, key: str, value) -> None:
    user_id = current_user_id.get()
    if bucket in ("llm", "plan", "relations") and user_id:
        key = f"{user_id}:{key}"

    b = CACHE[bucket]
    if len(b) >= CACHE_MAX:
        oldest = min(b, key=lambda k: b[k]["ts"])
        b.pop(oldest, None)
    b[key] = {"v": value, "ts": time.time()}


# ================================================================
# RATE LIMITER
# ================================================================

_rate_store: Dict[str, List[float]] = {}
_last_cleanup = time.time()


async def check_rate_limit(client_ip: str) -> None:
    global _last_cleanup, _rate_store
    now = time.time()
    if now - _last_cleanup > 300:
        cutoff = now - 60
        _rate_store = {
            k: [t for t in v if t > cutoff]
            for k, v in _rate_store.items()
            if any(t > cutoff for t in v)
        }
        _last_cleanup = now
    hits = [t for t in _rate_store.get(client_ip, []) if now - t < 60.0]
    if len(hits) >= RATE_LIMIT:
        raise HTTPException(429, f"Rate limit: max {RATE_LIMIT}/min.")
    hits.append(now)
    _rate_store[client_ip] = hits


# ================================================================
# CONNECTION POOL
# ================================================================


class Pool:
    def __init__(self):
        self.supabase = None
        self.neo4j = None
        self.groq_http = None
        self.neo4j_ok = False
        self._ready = False

    async def init(self) -> None:
        errors = []
        try:
            self.supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
            log.info("Supabase connected")
        except Exception as e:
            errors.append(f"Supabase: {e}")

        try:
            self.neo4j = GraphDatabase.driver(
                NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD)
            )
            await asyncio.wait_for(
                asyncio.to_thread(self.neo4j.verify_connectivity), timeout=10.0
            )
            self.neo4j_ok = True
            log.info("Neo4j connected")
        except asyncio.TimeoutError:
            log.warning("Neo4j timed out (degraded mode)")
        except Exception as e:
            log.warning(f"Neo4j unavailable (degraded): {e}")

        self.groq_http = httpx.AsyncClient(
            timeout=httpx.Timeout(GROQ_TIMEOUT, connect=5.0),
            limits=httpx.Limits(max_connections=30, max_keepalive_connections=15),
        )
        if self.supabase:
            self._ready = True
        if errors:
            log.warning(f"Startup errors: {errors}")

    async def close(self) -> None:
        if self.groq_http:
            await self.groq_http.aclose()
        if self.neo4j:
            self.neo4j.close()
        log.info("Pool closed")

    def assert_ready(self) -> None:
        if not self._ready:
            raise HTTPException(503, "Service not initialised.")


pool = Pool()


# ================================================================
# EMBEDDING MODEL
# ================================================================

embed_model = None
if SentenceTransformer is None:
    log.warning(
        "sentence-transformers not installed; falling back to HuggingFace API embeddings"
    )
else:
    try:
        log.info("Loading embedding model...")
        embed_model = SentenceTransformer(EMBED_MODEL, device="cpu")
        log.info("Embedding model ready")
    except Exception as exc:
        log.warning(
            f"Local embedding model unavailable ({exc}); falling back to HuggingFace API embeddings"
        )


# ================================================================
# PYDANTIC MODELS
# ================================================================


class ResearchRequest(BaseModel):
    query: Optional[str] = Field(None, max_length=2000)
    text: Optional[str] = Field(None, max_length=2000)
    top_k: int = Field(8, ge=1, le=20)
    min_similarity: float = Field(0.28, ge=0.0, le=1.0)
    use_heavy: bool = False
    verify: bool = True
    filters: Optional[Dict[str, Any]] = None
    temperature: float = Field(0.0, ge=0.0, le=2.0)

    @field_validator("query", "text", mode="before")
    @classmethod
    def strip_ws(cls, v):
        return v.strip() if isinstance(v, str) else v

    def resolved_query(self) -> str:
        q = self.query or self.text
        if not q:
            raise HTTPException(400, "Provide 'query' or 'text'.")
        return q


class ChatMessage(BaseModel):
    role: str
    content: str


class ConversationRequest(BaseModel):
    messages: List[ChatMessage] = Field(..., min_length=1)
    top_k: int = Field(8, ge=1, le=20)
    min_similarity: float = Field(0.28, ge=0.0, le=1.0)
    use_heavy: bool = False
    verify: bool = True
    filters: Optional[Dict[str, Any]] = None
    last_paper_context: Optional[str] = None
    temperature: float = Field(0.0, ge=0.0, le=2.0)


class BulkRequest(BaseModel):
    queries: List[str] = Field(..., min_length=1, max_length=10)
    top_k: int = Field(8, ge=1, le=20)


class CompareRequest(BaseModel):
    paper_a: str = Field(..., description="Title or ID of first paper")
    paper_b: str = Field(..., description="Title or ID of second paper")
    aspects: Optional[List[str]] = Field(
        None, description="Specific aspects to compare"
    )
    temperature: float = Field(0.0, ge=0.0, le=2.0)


class TimelineRequest(BaseModel):
    topic: str = Field(..., max_length=500)
    start_year: Optional[int] = None
    end_year: Optional[int] = None
    top_k: int = Field(10, ge=1, le=30)
    temperature: float = Field(0.0, ge=0.0, le=2.0)


class SurveyRequest(BaseModel):
    topic: str = Field(..., max_length=500)
    top_k: int = Field(15, ge=5, le=30)
    use_heavy: bool = True
    temperature: float = Field(0.0, ge=0.0, le=2.0)


class CitationPathRequest(BaseModel):
    from_paper: str
    to_paper: str


class ChatCompletionRequest(BaseModel):
    model: Optional[str] = None
    messages: List[Dict[str, str]] = Field(..., min_length=1)
    temperature: float = Field(0.1, ge=0.0, le=2.0)
    max_tokens: int = Field(800, ge=1, le=4096)
    stream: bool = False


# ================================================================
# QUERY PLAN DATACLASS
# ================================================================


@dataclass
class QueryPlan:
    standalone_query: str
    route: str
    graph_anchors: List[str] = field(default_factory=list)
    vector_keywords: List[str] = field(default_factory=list)
    required_metrics: List[str] = field(default_factory=list)
    reasoning_path: str = ""
    ambiguous: bool = False
    cache_key_str: str = ""
    raw: Dict = field(default_factory=dict)


# ================================================================
# FASTAPI APP
# ================================================================


@asynccontextmanager
async def lifespan(app: FastAPI):
    await pool.init()
    yield
    await pool.close()


app = FastAPI(
    title="Aether GraphRAG Research API",
    version="4.0.0",
    description="Graph-augmented RAG for academic research — Intelligence Edition",
    lifespan=lifespan,
)

ALLOWED_ORIGINS = [
    o.strip()
    for o in os.getenv(
        "CORS_ORIGINS",
        "http://localhost:3000,http://localhost:5173,http://localhost:5500,http://127.0.0.1:5500",
    ).split(",")
    if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
    allow_credentials=False,
)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    import traceback
    rid = getattr(request.state, "request_id", "unknown")
    log.exception(f"[{rid}] Unhandled {type(exc).__name__}: {exc}")
    
    status_code = exc.status_code if isinstance(exc, HTTPException) else 500
    err_msg = str(exc)
    err_type = type(exc).__name__
    tb = traceback.format_exc()
    
    return JSONResponse(
        status_code=status_code,
        content={
            "detail": f"{err_type}: {err_msg}",
            "request_id": rid,
            "error_type": err_type,
            "traceback": tb.split("\n")
        }
    )


# ================================================================
# GROQ LLM  (retry + backoff + deterministic cache)
# ================================================================


async def groq_chat(
    messages: List[Dict],
    model: str,
    temperature: float = 0.1,
    max_tokens: int = 1024,
    retries: int = 2,
    json_mode: bool = False,
) -> str:
    ck = None
    if temperature == 0.0:
        ck = cache_key(str(messages), model, max_tokens)
        cached = get_cache("llm", ck)
        if cached:
            log.debug("LLM cache hit")
            return cached

    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }
    payload: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    last_err = None
    for attempt in range(retries + 1):
        try:
            r = await pool.groq_http.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers=headers,
                json=payload,
            )
            if r.status_code == 429:
                wait = min(int(r.headers.get("Retry-After", 5)), 15)
                log.warning(f"Groq 429 — wait {wait}s")
                await asyncio.sleep(wait)
                continue
            if r.status_code in (500, 503):
                await asyncio.sleep(2**attempt)
                continue
            if r.status_code != 200:
                raise LLMError(f"Groq HTTP {r.status_code}: {r.text[:300]}")
            result = r.json()["choices"][0]["message"]["content"]
            if ck:
                set_cache("llm", ck, result)
            return result
        except (httpx.TimeoutException, httpx.ConnectError) as e:
            last_err = e
            await asyncio.sleep(1.5**attempt)
    raise LLMError(f"Groq failed after {retries + 1} attempts: {last_err}")


# ================================================================
# SUPER-MASTER STRATEGIC PLANNING BRAIN
# ================================================================

SUPER_MASTER_PROMPT ="""
You are the Strategic Planning Brain for Aether, an evidence-only GraphRAG Research Assistant.
Decompose the user query into a precise execution plan.

━━━ INPUT ━━━
USER QUERY: {query}
CONVERSATION HISTORY (last 3 turns):
{context}

━━━ STEPS ━━━

STEP 1 — RESOLVE PRONOUNS
If the query contains "it", "they", "this paper", "the authors", or similar ambiguity:
  Identify the referent from CONVERSATION HISTORY and rewrite the query to be self-contained.
  If unresolvable, set "ambiguous": true.

STEP 2 — CLASSIFY ROUTE (pick exactly one):
  "entity_lookup"  → factual metadata query: author, year, domain, venue, affiliation.
                     Trigger: who, when, which year, published by, domain of, where published.
  "structured"     → list/filter: list papers, find papers on X, papers by author Y.
  "title_lookup"   → user names a specific paper and wants its record only (no analysis).
  "compare"        → side-by-side of 2+ papers, methods, or approaches.
  "timeline"       → chronological evolution of a topic across years.
  "survey"         → broad synthesis of a research area.
  "rag"            → explanation, analysis, synthesis of concepts.
  "chitchat"       → greeting or non-research question.

STEP 3 — EXTRACT GRAPH ANCHORS
  1–3 minimal paper title substrings or author names for Neo4j lookup.
  Use shortest identifying substring: "DeepSketch" not "DeepSketch paper on sketch recognition".
  Return [] if no specific entity is named.

STEP 4 — EXTRACT VECTOR KEYWORDS
  3–5 dense technical terms for semantic vector search.
  Exclude: "paper", "author", "year", "list", "find", "published", "research".

STEP 5 — IDENTIFY REQUIRED METRICS
  Specific data the answer MUST include: accuracy, dataset, year, author names, citation count, etc.
  Return [] if none.

STEP 6 — REASONING PATH
  One sentence: how you will assemble the answer from graph + vector evidence.

STEP 7 — CACHE KEY
  lowercase(standalone_query), strip punctuation.

━━━ OUTPUT FORMAT ━━━
Respond ONLY with a valid JSON object. No markdown. No explanation outside JSON.

{{
  "standalone_query": "<self-contained rewrite>",
  "ambiguous": false,
  "route": "<one of the 8 routes>",
  "graph_anchors": ["<minimal anchor>"],
  "vector_keywords": ["<term>"],
  "required_metrics": ["<metric>"],
  "reasoning_path": "<one sentence>",
  "cache_key": "<lowercase stripped>"
}}

━━━ HARD RULES ━━━
- entity_lookup → graph_anchors MUST have exactly 1 entry; vector_keywords SHOULD be [].
- chitchat → ALL retrieval fields MUST be []. No search triggered.
- ambiguous=true → standalone_query ends with " [UNRESOLVED]", route = "rag".
- compare → graph_anchors MUST have exactly 2 entries (one per paper).
- NEVER add extra keys. NEVER return prose.

━━━ EXAMPLES ━━━
Input: "who is the author of DeepSketch?"
{{"standalone_query":"Who are the authors of DeepSketch?","ambiguous":false,"route":"entity_lookup","graph_anchors":["DeepSketch"],"vector_keywords":[],"required_metrics":["author names"],"reasoning_path":"Retrieve DeepSketch node from graph and return its WRITTEN_BY relationships directly.","cache_key":"who are the authors of deepsketch"}}

Input: "compare its accuracy with ResNet-50" (prev turn: DeepSketch)
{{"standalone_query":"Compare the accuracy of DeepSketch with ResNet-50.","ambiguous":false,"route":"compare","graph_anchors":["DeepSketch","ResNet-50"],"vector_keywords":["accuracy","top-1","benchmark","classification"],"required_metrics":["accuracy percentage","dataset","parameter count"],"reasoning_path":"Retrieve both papers, then vector-search accuracy comparison chunks.","cache_key":"compare the accuracy of deepsketch with resnet50"}}

Input: "hey what's up"
{{"standalone_query":"hey what's up","ambiguous":false,"route":"chitchat","graph_anchors":[],"vector_keywords":[],"required_metrics":[],"reasoning_path":"No retrieval needed.","cache_key":"hey whats up"}}


"""
 



async def plan_query(query: str, context: str = "") -> QueryPlan:
    ck = cache_key("plan", query, context[:200])
    cached = get_cache("plan", ck)
    if cached:
        log.debug("Plan cache hit")
        return cached

    prompt = SUPER_MASTER_PROMPT.format(query=query, context=context or "None")
    try:
        raw_text = await groq_chat(
            [{"role": "user", "content": prompt}],
            PLAN_MODEL,
            temperature=0.0,
            max_tokens=400,
            json_mode=True,
        )
        data = json.loads(raw_text.strip())
    except (LLMError, json.JSONDecodeError, Exception) as e:
        log.warning(f"Plan failed ({e}), using fallback")
        data = {}

    plan = QueryPlan(
        standalone_query=data.get("standalone_query", query),
        route=data.get("route", "rag"),
        graph_anchors=data.get("graph_anchors", [])[:3],
        vector_keywords=data.get("vector_keywords", [])[:5],
        required_metrics=data.get("required_metrics", []),
        reasoning_path=data.get("reasoning_path", ""),
        ambiguous=data.get("ambiguous", False),
        cache_key_str=data.get("cache_key", re.sub(r"[^\w\s]", "", query.lower())),
        raw=data,
    )
    set_cache("plan", ck, plan)
    log.info(
        f"Plan: route={plan.route} anchors={plan.graph_anchors} kw={plan.vector_keywords}"
    )
    return plan


# ================================================================
# PAPER RANKING  (exact > substring > word-overlap > recency)
# ================================================================


def rank_papers(papers: List[Dict], anchors: List[str]) -> List[Dict]:
    """Score and sort papers by relevance to the search anchors."""
    if not anchors:
        return papers

    def score(p: Dict) -> float:
        title = (p.get("title") or "").lower()
        s = 0.0
        for anchor in anchors:
            a = anchor.lower()
            if title == a:
                s += 100.0
            elif title.startswith(a) or a in title:
                s += 60.0
            else:
                # word overlap
                t_words = set(title.split())
                a_words = set(a.split())
                overlap = len(t_words & a_words)
                s += overlap * 10.0
        # recency bonus (papers from last 5 years get up to +5)
        try:
            year = int(p.get("year", 2000))
            s += max(0, (year - 2018)) * 0.5
        except (TypeError, ValueError):
            pass
        # seed papers get a graph-score boost
        s += (p.get("score", 1) - 1) * 5.0
        return s

    return sorted(papers, key=score, reverse=True)


# ================================================================
# GRAPH RETRIEVAL  — full Neo4j intelligence
# ================================================================


def _build_filters(filters: Optional[Dict]) -> Tuple[str, str, Dict]:
    year_val = filters.get("year") if filters else None
    domain_val = filters.get("domain") if filters else None
    extra: Dict[str, Any] = {}
    yf = df = ""
    if year_val:
        yf = "AND p.year = $year"
        extra["year"] = year_val
    if domain_val:
        df = "AND toLower(p.domain) = toLower($domain)"
        extra["domain"] = domain_val
    return yf, df, extra


async def retrieve_graph_papers(
    keywords: Optional[List[str]] = None,
    filters: Optional[Dict] = None,
    limit: int = MAX_GRAPH_NODES,
    anchors: Optional[List[str]] = None,
) -> List[Dict]:
    """
    Full graph retrieval:
      1. Seed: papers whose title/author matches keywords
      2. Expand: CITES, CITED_BY, WRITTEN_BY co-authors, PUBLISHED_IN venue peers
      3. Rank by relevance to anchors
    """
    if not pool.neo4j:
        raise GraphRetrievalError("Neo4j not connected")

    safe_kw = (keywords or [])[:5]
    ck = cache_key(str(safe_kw), str(filters), limit)
    cached = get_cache("graph", ck)
    if cached:
        log.debug(f"Graph cache hit: {safe_kw}")
        return cached

    yf, df, extra = _build_filters(filters)
    params: Dict[str, Any] = {"limit": limit, "keywords": safe_kw, **extra}

    # ── Seed query ──────────────────────────────────────────────────
    seed_cypher = f"""
    WITH $keywords AS kws
    UNWIND kws AS kw
    MATCH (p:Publication)
    WHERE (toLower(p.title) CONTAINS toLower(kw)
       OR EXISTS {{
           MATCH (p)-[:WRITTEN_BY]->(a:Author)
           WHERE toLower(a.name) CONTAINS toLower(kw)
       }})
       {yf} {df}
    WITH DISTINCT p
    OPTIONAL MATCH (p)-[:WRITTEN_BY]->(a)
    OPTIONAL MATCH (p)-[:PUBLISHED_IN]->(v)
    OPTIONAL MATCH (p)-[:HAS_TOPIC]->(t)
    WITH p, collect(DISTINCT a.name) AS authors,
         v.name AS venue,
         collect(DISTINCT t.name) AS topics,
         COUNT {{ (p)-[:CITES]->() }} AS out_citations,
         COUNT {{ ()-[:CITES]->(p) }} AS in_citations
    RETURN p.research_id  AS research_id,
           p.title        AS title,
           p.year         AS year,
           p.domain       AS domain,
           p.abstract     AS abstract,
           authors        AS authors,
           venue          AS venue,
           topics         AS topics,
           in_citations   AS in_citations,
           out_citations  AS out_citations,
           2              AS score,
           'seed'         AS source
    ORDER BY in_citations DESC, p.year DESC
    LIMIT $limit
    """

    # ── Expand query ────────────────────────────────────────────────
    expand_cypher = f"""
    WITH $keywords AS kws
    UNWIND kws AS kw
    MATCH (p:Publication)
    WHERE (toLower(p.title) CONTAINS toLower(kw)
       OR EXISTS {{
           MATCH (p)-[:WRITTEN_BY]->(a:Author)
           WHERE toLower(a.name) CONTAINS toLower(kw)
       }})
       {yf} {df}
    WITH collect(DISTINCT p) AS seeds

    UNWIND seeds AS seed
    OPTIONAL MATCH (seed)-[:CITES]->(cited:Publication)
    OPTIONAL MATCH (citing:Publication)-[:CITES]->(seed)
    OPTIONAL MATCH (seed)-[:WRITTEN_BY]->(author:Author)<-[:WRITTEN_BY]-(sibling:Publication)
    OPTIONAL MATCH (seed)-[:PUBLISHED_IN]->(venue:Venue)<-[:PUBLISHED_IN]-(peer:Publication)
    OPTIONAL MATCH (seed)-[:SIMILAR_TO]->(similar:Publication)

    WITH seeds,
         collect(DISTINCT cited)   AS cited_list,
         collect(DISTINCT citing)  AS citing_list,
         collect(DISTINCT sibling) AS sibling_list,
         collect(DISTINCT peer)    AS peer_list,
         collect(DISTINCT similar) AS similar_list

    WITH seeds,
         [p IN cited_list + citing_list + sibling_list + peer_list + similar_list
          WHERE NOT p IN seeds AND p IS NOT NULL] AS expanded

    UNWIND expanded AS ep
    WITH DISTINCT ep
    OPTIONAL MATCH (ep)-[:WRITTEN_BY]->(a)
    OPTIONAL MATCH (ep)-[:PUBLISHED_IN]->(v)
    WITH ep, collect(DISTINCT a.name) AS authors,
         v.name AS venue,
         COUNT {{ ()-[:CITES]->(ep) }} AS in_citations
    RETURN ep.research_id  AS research_id,
           ep.title        AS title,
           ep.year         AS year,
           ep.domain       AS domain,
           ep.abstract     AS abstract,
           authors         AS authors,
           venue           AS venue,
           []              AS topics,
           in_citations    AS in_citations,
           0               AS out_citations,
           1               AS score,
           'expanded'      AS source
    ORDER BY in_citations DESC, ep.year DESC
    LIMIT $limit
    """

    try:

        def _fetch():
            with pool.neo4j.session() as session:
                s_rows = [dict(r) for r in session.run(seed_cypher, params)]
                e_rows = [dict(r) for r in session.run(expand_cypher, params)]
            return s_rows, e_rows

        seed_rows, expanded_rows = await asyncio.to_thread(_fetch)

        seen: set = set()
        merged: List[Dict] = []
        for row in seed_rows + expanded_rows:
            rid = row.get("research_id")
            if rid and rid not in seen:
                seen.add(rid)
                merged.append(row)

        log.info(
            f"Graph [{safe_kw}]: {len(seed_rows)} seed + {len(expanded_rows)} expanded = {len(merged)} unique"
        )

        # Rank by anchor relevance
        ranked = rank_papers(merged, anchors or keywords or [])
        result = ranked[:limit]
        set_cache("graph", ck, result)
        return result

    except neo4j_exceptions.ServiceUnavailable as e:
        raise GraphRetrievalError(f"Neo4j unavailable: {e}")
    except neo4j_exceptions.CypherSyntaxError as e:
        raise GraphRetrievalError(f"Cypher syntax error: {e}")
    except Exception as e:
        raise GraphRetrievalError(f"Graph query error: {e}")


async def get_paper_full(paper_id_or_title: str) -> Optional[Dict]:
    """Fetch a single paper with all its relationships from Neo4j."""
    if not pool.neo4j:
        return None

    ck = cache_key("paper_full", paper_id_or_title)
    cached = get_cache("relations", ck)
    if cached:
        return cached

    cypher = """
    MATCH (p:Publication)
    WHERE p.research_id = $id OR toLower(p.title) CONTAINS toLower($id)
    WITH p LIMIT 1
    OPTIONAL MATCH (p)-[:WRITTEN_BY]->(a:Author)
    OPTIONAL MATCH (p)-[:PUBLISHED_IN]->(v:Venue)
    OPTIONAL MATCH (p)-[:HAS_TOPIC]->(t:Topic)
    OPTIONAL MATCH (p)-[:CITES]->(cited:Publication)
    OPTIONAL MATCH (citing:Publication)-[:CITES]->(p)
    OPTIONAL MATCH (p)-[:SIMILAR_TO]->(sim:Publication)
    RETURN p.research_id  AS research_id,
           p.title        AS title,
           p.year         AS year,
           p.domain       AS domain,
           p.abstract     AS abstract,
           collect(DISTINCT a.name)           AS authors,
           collect(DISTINCT a.affiliation)    AS affiliations,
           v.name                             AS venue,
           collect(DISTINCT t.name)           AS topics,
           collect(DISTINCT cited.title)      AS cites,
           collect(DISTINCT citing.title)     AS cited_by,
           collect(DISTINCT sim.title)        AS similar_to,
           COUNT {{ ()-[:CITES]->(p) }}        AS citation_count
    """
    try:

        def _run():
            with pool.neo4j.session() as session:
                rows = list(session.run(cypher, {"id": paper_id_or_title}))
                return dict(rows[0]) if rows else None

        result = await asyncio.to_thread(_run)
        if result:
            set_cache("relations", ck, result)
        return result
    except Exception as e:
        log.warning(f"get_paper_full error: {e}")
        return None


async def get_author_network(author_name: str) -> Dict:
    """Get an author's ego-network: papers, co-authors, venues."""
    if not pool.neo4j:
        return {}

    ck = cache_key("author", author_name)
    cached = get_cache("relations", ck)
    if cached:
        return cached

    cypher = """
    MATCH (a:Author)
    WHERE toLower(a.name) CONTAINS toLower($name)
    WITH a LIMIT 1
    OPTIONAL MATCH (a)<-[:WRITTEN_BY]-(p:Publication)
    OPTIONAL MATCH (p)-[:WRITTEN_BY]->(coauthor:Author)
    WHERE coauthor <> a
    OPTIONAL MATCH (p)-[:PUBLISHED_IN]->(v:Venue)
    RETURN a.name           AS author_name,
           a.affiliation    AS affiliation,
           collect(DISTINCT {title: p.title, year: p.year, domain: p.domain}) AS papers,
           collect(DISTINCT coauthor.name)  AS coauthors,
           collect(DISTINCT v.name)         AS venues,
           count(DISTINCT p)                AS paper_count
    """
    try:

        def _run():
            with pool.neo4j.session() as session:
                rows = list(session.run(cypher, {"name": author_name}))
                return dict(rows[0]) if rows else {}

        result = await asyncio.to_thread(_run)
        set_cache("relations", ck, result)
        return result
    except Exception as e:
        log.warning(f"get_author_network error: {e}")
        return {}


async def get_citation_path(from_title: str, to_title: str, max_depth: int = 4) -> Dict:
    """Find shortest citation path between two papers."""
    if not pool.neo4j:
        return {}

    ck = cache_key("citepath", from_title, to_title)
    cached = get_cache("relations", ck)
    if cached:
        return cached

    cypher = """
    MATCH (a:Publication), (b:Publication)
    WHERE toLower(a.title) CONTAINS toLower($from_title)
      AND toLower(b.title) CONTAINS toLower($to_title)
    WITH a, b LIMIT 1
    MATCH path = shortestPath((a)-[:CITES*..{max_depth}]->(b))
    RETURN [node IN nodes(path) | node.title] AS path_titles,
           length(path) AS path_length
    LIMIT 1
    """.replace(
        "{max_depth}", str(max_depth)
    )

    try:

        def _run():
            with pool.neo4j.session() as session:
                rows = list(
                    session.run(
                        cypher, {"from_title": from_title, "to_title": to_title}
                    )
                )
                return dict(rows[0]) if rows else {"path_titles": [], "path_length": -1}

        result = await asyncio.to_thread(_run)
        set_cache("relations", ck, result)
        return result
    except Exception as e:
        log.warning(f"get_citation_path error: {e}")
        return {"path_titles": [], "path_length": -1, "error": str(e)}


async def get_trending_papers(limit: int = 10) -> List[Dict]:
    """Papers with high recent citation velocity (cited in last 2 years)."""
    if not pool.neo4j:
        return []

    ck = cache_key("trending", limit)
    cached = get_cache("graph", ck)
    if cached:
        return cached

    cypher = """
    MATCH (p:Publication)<-[:CITES]-(citing:Publication)
    WHERE citing.year >= 2022
    WITH p, count(citing) AS recent_citations
    ORDER BY recent_citations DESC
    LIMIT $limit
    OPTIONAL MATCH (p)-[:WRITTEN_BY]->(a:Author)
    RETURN p.research_id AS research_id,
           p.title       AS title,
           p.year        AS year,
           p.domain      AS domain,
           collect(a.name) AS authors,
           recent_citations
    ORDER BY recent_citations DESC
    """
    try:

        def _run():
            with pool.neo4j.session() as session:
                return [dict(r) for r in session.run(cypher, {"limit": limit})]

        result = await asyncio.to_thread(_run)
        set_cache("graph", ck, result)
        return result
    except Exception as e:
        log.warning(f"get_trending_papers error: {e}")
        return []


async def get_graph_stats() -> Dict:
    """Database statistics from Neo4j and Supabase."""
    if not pool.neo4j:
        return {}

    ck = cache_key("stats")
    cached = get_cache("graph", ck)
    if cached:
        return cached

    cypher = """
    MATCH (p:Publication) WITH count(p) AS papers
    MATCH (a:Author)      WITH papers, count(a) AS authors
    MATCH (v:Venue)       WITH papers, authors, count(v) AS venues
    OPTIONAL MATCH ()-[r:CITES]->() WITH papers, authors, venues, count(r) AS citations
    RETURN papers, authors, venues, citations
    """
    try:

        def _run():
            with pool.neo4j.session() as session:
                rows = list(session.run(cypher))
                return dict(rows[0]) if rows else {}

        stats = await asyncio.to_thread(_run)
        set_cache("graph", ck, stats)
        return stats
    except Exception as e:
        log.warning(f"get_graph_stats error: {e}")
        return {}


async def get_co_citation_cluster(paper_ids: List[str], limit: int = 10) -> List[Dict]:
    """Find papers frequently cited together with the given papers (co-citation)."""
    if not pool.neo4j or not paper_ids:
        return []

    cypher = """
    MATCH (p:Publication)-[:CITES]->(ref:Publication)
    WHERE p.research_id IN $ids
    WITH ref, count(p) AS co_citation_count
    WHERE co_citation_count > 1
    ORDER BY co_citation_count DESC
    LIMIT $limit
    OPTIONAL MATCH (ref)-[:WRITTEN_BY]->(a:Author)
    RETURN ref.research_id AS research_id,
           ref.title       AS title,
           ref.year        AS year,
           collect(a.name) AS authors,
           co_citation_count
    """
    try:

        def _run():
            with pool.neo4j.session() as session:
                return [
                    dict(r)
                    for r in session.run(cypher, {"ids": paper_ids, "limit": limit})
                ]

        return await asyncio.to_thread(_run)
    except Exception as e:
        log.warning(f"co_citation_cluster error: {e}")
        return []


# ================================================================
# VECTOR SEARCH  (Supabase)
# ================================================================


async def vector_search(
    embedding: List[float],
    min_similarity: float,
    match_count: int,
    filter_ids: Optional[List[str]] = None,
) -> List[Dict]:
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


# ================================================================
# EMBEDDING  (BAAI/bge-base-en via HF Inference API on Vercel)
# ================================================================
# BGE models require L2-normalized vectors for cosine similarity.
# On Vercel: sentence-transformers is too heavy, so we use HF API.
# _bge_normalize() ensures the HF API vectors match local model output.
# ================================================================


def _bge_normalize(vec: List[float]) -> List[float]:
    """L2-normalize so cosine_sim(a,b) == np.dot(a,b) for unit vectors."""
    arr = np.array(vec, dtype=np.float32)
    norm = np.linalg.norm(arr)
    return (arr / norm).tolist() if norm > 0.0 else vec


async def create_embedding(text: str) -> List[float]:
    """
    Convert text to a BAAI/bge-base-en embedding vector.

    On Vercel (production): uses HuggingFace Inference API.
      - Model: BAAI/bge-base-en (set via EMBED_MODEL env var)
      - Vectors are L2-normalized so cosine similarity == dot product
      - Matches the format stored in Supabase pgvector

    Locally (if sentence-transformers is installed): uses local model.
    """
    # BGE models require a specific query instruction prefix for retrieval tasks
    query_text = f"Represent this sentence for searching relevant passages: {text}"

    ck = cache_key(query_text)
    cached = get_cache("embed", ck)
    if cached:
        return cached

    # Primary: local SentenceTransformer (skipped on Vercel - too heavy)
    if embed_model is not None:
        try:
            emb = await asyncio.to_thread(
                embed_model.encode,
                query_text,
                normalize_embeddings=True,  # BGE: MUST be True
            )
            result = emb.tolist()
            set_cache("embed", ck, result)
            log.debug("Embedding via: local BAAI/bge-base-en")
            return result
        except Exception as exc:
            log.warning(f"Local BGE model failed, using HF API: {exc}")

    # Vercel path: HuggingFace Inference API (BAAI/bge-base-en)
    if not HF_TOKEN:
        raise EmbeddingError(
            "HF_TOKEN not set. Required for BAAI/bge-base-en embeddings on Vercel."
        )

    try:
        url = (
            f"https://router.huggingface.co/hf-inference/models/{EMBED_MODEL}"
            "/pipeline/feature-extraction"
        )
        headers = {"Authorization": f"Bearer {HF_TOKEN}"}
        payload = {"inputs": query_text}

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(EMBED_TIMEOUT, connect=5.0)
        ) as client:
            resp = await client.post(url, headers=headers, json=payload)

            if resp.status_code == 503:
                wait = min(int(resp.headers.get("Retry-After", "5")), 10)
                await asyncio.sleep(wait)
                resp = await client.post(url, headers=headers, json=payload)

            if resp.status_code != 200:
                raise EmbeddingError(
                    f"HF embedding HTTP {resp.status_code}: {resp.text[:300]}"
                )

            data = resp.json()

        # HF feature-extraction returns [[...]] or [...]
        if isinstance(data, list) and data and isinstance(data[0], list):
            raw = [float(x) for x in data[0]]
        elif isinstance(data, list):
            raw = [float(x) for x in data]
        else:
            raise EmbeddingError("Unexpected HF API response format")

        # L2-normalize: makes cosine_sim(a,b) == dot(a,b)
        # This matches what sentence-transformers returns with normalize_embeddings=True
        result = _bge_normalize(raw)
        set_cache("embed", ck, result)
        log.debug("Embedding via: BAAI/bge-base-en HF Inference API (L2-normalized)")
        return result
    except EmbeddingError:
        raise
    except Exception as exc:
        raise EmbeddingError(f"BAAI/bge-base-en HF API embedding failed: {exc}")


# ================================================================
# RECIPROCAL RANK FUSION
# ================================================================


def reciprocal_rank_fusion(result_lists: List[List[Dict]], k: int = 60) -> List[Dict]:
    scores: Dict[str, float] = {}
    chunks: Dict[str, Dict] = {}
    for lst in result_lists:
        for rank, chunk in enumerate(lst):
            cid = str(
                chunk.get("id")
                or f"{chunk.get('research_id','')}_{chunk.get('chunk_number','')}"
            )
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank + 1)
            chunks[cid] = chunk
    return [
        chunks[cid] for cid in sorted(scores, key=lambda x: scores[x], reverse=True)
    ]


# ================================================================
# MAXIMAL MARGINAL RELEVANCE  (diversity re-ranking)
# ================================================================


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


# ================================================================
# CHUNK RELEVANCE FILTER & CONTEXT ENGINEERING
# ================================================================

SIMILARITY_KEYS = ("similarity", "score", "relevance", "_score", "sim")


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


def filter_relevant_chunks(
    chunks: List[Dict], floor: float = RELEVANCE_FLOOR
) -> List[Dict]:
    filtered = [c for c in chunks if get_chunk_similarity(c) >= floor]
    dropped = len(chunks) - len(filtered)
    if dropped:
        log.info(
            f"Relevance filter: dropped {dropped}/{len(chunks)} chunks below {floor}"
        )
    return filtered


# ================================================================
# SECTION PRIORITY  (abstract and conclusion first)
# ================================================================

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


# ================================================================
# GRAPH RELATIONSHIP NARRATIVE BUILDER
# ================================================================


def build_relationship_context(graph_nodes: List[Dict]) -> str:
    """Convert graph node relationships into a human-readable narrative for the LLM."""
    if not graph_nodes:
        return ""

    lines = ["=== GRAPH RELATIONSHIP CONTEXT ==="]

    # Group by source
    seeds = [n for n in graph_nodes if n.get("source") == "seed"]
    expanded = [n for n in graph_nodes if n.get("source") == "expanded"]

    if seeds:
        lines.append(f"\nDIRECTLY MATCHED PAPERS ({len(seeds)}):")
        for n in seeds[:5]:
            authors_str = (
                ", ".join(a for a in (n.get("authors") or []) if a) or "Unknown"
            )
            venue = n.get("venue") or "Unknown venue"
            cites_in = n.get("in_citations", 0)
            topics_str = ", ".join(n.get("topics") or []) or "N/A"
            abstract = (n.get("abstract") or "")[:200]
            lines.append(
                f"  • {n.get('title','?')} ({n.get('year','?')})\n"
                f"    Authors: {authors_str}\n"
                f"    Venue: {venue} | Citations received: {cites_in}\n"
                f"    Topics: {topics_str}\n"
                f"    Abstract: {abstract}{'...' if len(n.get('abstract') or '')>200 else ''}"
            )

    if expanded:
        lines.append(f"\nRELATED PAPERS VIA GRAPH TRAVERSAL ({len(expanded)}):")
        for n in expanded[:8]:
            authors_str = (
                ", ".join(a for a in (n.get("authors") or []) if a) or "Unknown"
            )
            lines.append(
                f"  • {n.get('title','?')} ({n.get('year','?')}) — "
                f"by {authors_str} — {n.get('in_citations',0)} citations"
            )

    return "\n".join(lines)


# ================================================================
# REAL-TIME ARXIV RETRIEVAL & CONTEXT FORMATTER
# ================================================================

import xml.etree.ElementTree as ET
import urllib.parse

async def retrieve_arxiv_context(query: str, limit: int = 3) -> List[Dict[str, Any]]:
    """Retrieve relevant paper abstracts from arXiv API in real-time."""
    if not query.strip():
        return []
    
    # URL encode query and format search
    clean_query = query.replace('"', '').replace("'", "")
    encoded_query = urllib.parse.quote(f'all:"{clean_query}"' if " " in clean_query else f"all:{clean_query}")
    url = f"https://export.arxiv.org/api/query?search_query={encoded_query}&max_results={limit}&sortBy=relevance"
    
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.get(url)
            if response.status_code != 200:
                log.warning(f"arXiv API returned status code {response.status_code}")
                return []
            
            # Parse Atom feed XML
            root = ET.fromstring(response.content)
            ns = {'atom': 'http://www.w3.org/2005/Atom'}
            
            papers = []
            for entry in root.findall('atom:entry', ns):
                title_node = entry.find('atom:title', ns)
                summary_node = entry.find('atom:summary', ns)
                id_node = entry.find('atom:id', ns)
                published_node = entry.find('atom:published', ns)
                
                title = title_node.text.strip().replace("\n", " ") if title_node is not None and title_node.text else "Unknown Title"
                summary = summary_node.text.strip().replace("\n", " ") if summary_node is not None and summary_node.text else "No Abstract Available"
                if len(summary) > 600:
                    summary = summary[:600] + "..."
                
                # Extract arXiv ID and pdf link
                arxiv_url = id_node.text.strip() if id_node is not None and id_node.text else ""
                arxiv_id = arxiv_url.split('/abs/')[-1] if '/abs/' in arxiv_url else ""
                
                pdf_url = ""
                doi = ""
                journal_ref = ""
                for link in entry.findall('atom:link', ns):
                    if link.attrib.get('title') == 'pdf' or link.attrib.get('type') == 'application/pdf':
                        pdf_url = link.attrib.get('href', '')
                        break
                if not pdf_url and arxiv_id:
                    pdf_url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"
                
                # ── Enhanced: extra fields ──
                # DOI
                arxiv_ns = 'http://arxiv.org/schemas/atom'
                doi_node = entry.find(f'{{{arxiv_ns}}}doi')
                if doi_node is not None and doi_node.text:
                    doi = doi_node.text.strip()
                
                # Journal reference
                jref_node = entry.find(f'{{{arxiv_ns}}}journal_ref')
                if jref_node is not None and jref_node.text:
                    journal_ref = jref_node.text.strip()
                
                # Comment field (often contains GitHub links)
                comment_node = entry.find(f'{{{arxiv_ns}}}comment')
                comment = ""
                if comment_node is not None and comment_node.text:
                    comment = comment_node.text.strip()
                
                # Categories (e.g. cs.LG, cs.CL)
                categories = []
                primary_cat_node = entry.find(f'{{{arxiv_ns}}}primary_category')
                if primary_cat_node is not None:
                    categories.append(primary_cat_node.attrib.get('term', ''))
                for cat_node in entry.findall('atom:category', ns):
                    term = cat_node.attrib.get('term', '')
                    if term and term not in categories:
                        categories.append(term)
                
                # Extract year
                published_date = published_node.text.strip() if published_node is not None and published_node.text else ""
                year = published_date.split('-')[0] if published_date else "Unknown"
                
                # Extract authors
                authors = []
                for author_node in entry.findall('atom:author', ns):
                    name_node = author_node.find('atom:name', ns)
                    if name_node is not None and name_node.text:
                        authors.append(name_node.text.strip())
                
                papers.append({
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
                })
            
            return papers
    except Exception as e:
        log.warning(f"Error fetching from arXiv: {e}")
        return []


def format_arxiv_context(arxiv_papers: List[Dict]) -> str:
    if not arxiv_papers:
        return ""
    lines = ["=== LIVE ARXIV CROSS-REFERENCE EVIDENCE ==="]
    for i, p in enumerate(arxiv_papers):
        authors_str = ", ".join(p['authors'][:4]) if p['authors'] else "Unknown"
        cite_str = f" | Cited {p['citation_count']:,}×" if p.get('citation_count') else ""
        tldr_str = f"\n  TL;DR: {p['tldr']}" if p.get('tldr') else ""
        cats_str = f" | {', '.join(p['categories'][:3])}" if p.get('categories') else ""
        doi_str = f"\n  DOI: {p['doi_url']}" if p.get('doi_url') else ""
        jref_str = f" | Published in: {p['journal_ref']}" if p.get('journal_ref') else ""
        lines.append(
            f"[ArXiv-{i+1}] {p['title']} ({p['year']}){cite_str}\n"
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
        authors_str = ", ".join((p.get('authors') or [])[:4]) or "Unknown"
        cite_str = f" | Cited {p['citation_count']:,}×" if p.get('citation_count') else ""
        tldr_str = f"\n  TL;DR: {p['tldr']}" if p.get('tldr') else ""
        fields_str = f" | Fields: {', '.join(p['fields_of_study'][:3])}" if p.get('fields_of_study') else ""
        pdf_str = f"\n  PDF: {p['pdf_url']}" if p.get('pdf_url') else ""
        s2_link = p.get('s2_url', '')
        doi_str = f" | DOI: {p.get('doi_url', '')}" if p.get('doi_url') else ""
        lines.append(
            f"[S2-{i+1}] {p['title']} ({p.get('year', '?')}){cite_str}\n"
            f"  Authors: {authors_str}{fields_str}\n"
            f"  Abstract: {p.get('abstract', '')}{tldr_str}{doi_str}\n"
            f"  S2 Link: {s2_link}{pdf_str}"
        )
    return "\n\n".join(lines)


def format_pwc_context(arxiv_papers: List[Dict]) -> str:
    """Format Papers with Code & Hugging Face enrichment (repos, datasets, models, spaces) as LLM context."""
    entries = []
    for p in arxiv_papers:
        repos = p.get('code_repos') or []
        datasets = p.get('datasets') or []
        models = p.get('linked_models') or []
        spaces = p.get('linked_spaces') or []
        upvotes = p.get('hf_upvotes', 0)
        ai_summary = p.get('hf_ai_summary') or ""
        
        if not repos and not datasets and not models and not spaces and not upvotes and not ai_summary:
            continue
            
        parts = [f"[{p.get('title', '?')}]"]
        if upvotes:
            parts.append(f"  Hugging Face Paper Upvotes: {upvotes}")
        if ai_summary:
            parts.append(f"  HF AI Summary: {ai_summary}")
        if repos:
            repo_strs = []
            for r in repos[:3]:
                star_str = f" ⭐{r['stars']:,}" if r.get('stars') else ""
                official_str = " (official)" if r.get('is_official') else ""
                repo_strs.append(f"{r['url']}{star_str}{official_str}")
            parts.append(f"  Code Repos: {' | '.join(repo_strs)}")
        if datasets:
            ds_strs = []
            for d in datasets[:5]:
                d_url = d.get('url')
                if d_url:
                    ds_strs.append(f"{d.get('name', '?')} ({d_url})")
                else:
                    ds_strs.append(d.get('name', '?'))
            parts.append(f"  Datasets: {' | '.join(ds_strs)}")
        if models:
            model_strs = []
            for m in models[:3]:
                m_url = m.get('url')
                if m_url:
                    model_strs.append(f"{m.get('id', '?')} ({m_url})")
                else:
                    model_strs.append(m.get('id', '?'))
            parts.append(f"  Linked HF Models: {' | '.join(model_strs)}")
        if spaces:
            space_strs = []
            for s in spaces[:3]:
                s_url = s.get('url')
                emoji = s.get('emoji', '🚀')
                if s_url:
                    space_strs.append(f"{emoji} {s.get('id', '?')} ({s_url})")
                else:
                    space_strs.append(f"{emoji} {s.get('id', '?')}")
            parts.append(f"  Linked HF Spaces: {' | '.join(space_strs)}")
        entries.append("\n".join(parts))
        
    if not entries:
        return ""
    return "=== CODE, DATASETS, MODELS & SPACES (Papers with Code & Hugging Face) ===\n" + "\n\n".join(entries)


# ================================================================
# ROUTE-SPECIFIC PROMPTS
# ================================================================


def extract_paper_urls(text: str) -> List[str]:
    # Regex to match URLs
    urls = re.findall(r'https?://[^\s]+', text)
    paper_urls = []
    for url in urls:
        # Strip trailing punctuation
        url = url.rstrip('.,;()[]{}')
        # Check if it's a PDF or ArXiv link
        is_arxiv = "arxiv.org" in url
        is_pdf = url.lower().endswith(".pdf") or "/pdf/" in url.lower()
        if is_arxiv or is_pdf:
            paper_urls.append(url)
    return paper_urls


def is_simple_link_paste(text: str, urls: List[str]) -> bool:
    cleaned = text
    for url in urls:
        cleaned = cleaned.replace(url, "")
    # Remove non-alphanumeric characters
    cleaned = re.sub(r'[^a-zA-Z0-9\s]', '', cleaned).strip().lower()
    if len(cleaned) < 10:
        return True
    
    words = cleaned.split()
    summary_words = {"summarize", "summarise", "summary", "parse", "read", "pdf", "paper", "analyze", "analyse", "this", "explain", "about", "what", "is", "intro", "introduction"}
    if all(w in summary_words for w in words):
        return True
        
    return False


async def parse_pdf_from_url(url: str) -> Tuple[str, List[str]]:
    # Convert arXiv abstract URL to PDF URL
    pdf_url = url
    if "arxiv.org/abs/" in url:
        pdf_url = url.replace("arxiv.org/abs/", "arxiv.org/pdf/")
        if not pdf_url.endswith(".pdf"):
            pdf_url += ".pdf"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }
    
    async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=60.0) as client:
        r = await client.get(pdf_url)
        if r.status_code != 200:
            raise Exception(f"Failed to download PDF: HTTP {r.status_code}")
        pdf_bytes = r.content
        
    def _parse():
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        text_content = []
        extracted_links = []
        for page_num, page in enumerate(doc):
            text_content.append(page.get_text())
            for link in page.get_links():
                uri = link.get("uri")
                if uri and uri.startswith("http"):
                    extracted_links.append(uri)
        return "\n".join(text_content), list(set(extracted_links))
        
    return await asyncio.to_thread(_parse)


async def get_or_parse_pdf(url: str) -> Tuple[str, List[str]]:
    key = cache_key("parsed_pdf", url)
    cached = get_cache("relations", key)
    if cached:
        return cached
    doc_text, doc_links = await parse_pdf_from_url(url)
    set_cache("relations", key, (doc_text, doc_links))
    return doc_text, doc_links


async def get_or_parse_pdf_safe(url: str, raise_on_error: bool = False) -> Tuple[str, List[str]]:
    try:
        return await get_or_parse_pdf(url)
    except Exception as e:
        log.warning(f"Error parsing PDF URL {url}: {e}")
        if raise_on_error:
            raise e
        return "", []


def document_summary_system_instruction() -> str:
    return r"""You are Aether, a precise research assistant specialized in scientific literature analysis.
Analyze the user's provided document text and generate a comprehensive, highly structured, and readable summary.

═══ CRITICAL CONSTRAINTS ═══
- Do NOT include any introductory chat (e.g., "Here is the summary...") or raw copied text at the start of your response.
- Your entire response MUST start directly with the header "# 1. Executive Summary" and follow the exact 6-section structure in order.
- Do NOT output any mathematical formulas or derivations at the beginning. All mathematical analysis and equations MUST be placed exclusively under section "# 6. Mathematical Formulas".

═══ SUMMARY STRUCTURE ═══
You must output exactly the following six sections, using these headers:

# 1. Executive Summary
Provide a high-level overview (2-3 sentences) of the document's core contribution, the problem it solves, and the main results.

# 2. Detailed Section-by-Section Breakdown
Analyze key methodologies, experiments, architectures, and theoretical foundations. Explain each section of the paper in depth using clean subheaders (e.g., `## Introduction`, `## Architecture`).

# 3. Key Findings & Metrics
Provide a detailed markdown table or bulleted list of baseline vs. proposed results, percentages, and evaluation metrics.

# 4. Embedded Reference Links
List code repositories, dataset pages, project websites, or reference URLs that were extracted from the PDF, using clickable markdown links (e.g. `[GitHub Repo](https://github.com/...)`). If none, state "No external links found in document."

# 5. Critique & Limitations
Discuss drawbacks, assumptions, constraints, or future directions mentioned by the authors.

# 6. Mathematical Formulas
Identify all key mathematical equations, variables, and expressions in the text, and write them in standard LaTeX syntax:
- Wrap inline variables/formulas in single dollar signs (e.g., $x_i$ or $\alpha_{t}$).
- Wrap block/displayed equations in double dollar signs, and display them on their own lines (e.g., $$c_t = \sum_{j=1}^{T_x} \alpha_{tj} h_{tj}$$).
- Do NOT output raw unicode sequences like "T X t=1" or "ct' = ...". Always translate them to proper LaTeX math notation.

═══ CONSTRAINT ═══
Base your response ONLY on the provided text. Do not invent facts. Write a thorough, comprehensive summary. Do not summarize briefly or omit key details.
"""


def document_summary_user_content(target_url: str, doc_text: str, doc_links: List[str]) -> str:
    links_str = "\n".join(f"- {link}" for link in doc_links[:15]) if doc_links else "(No external links found in document.)"
    return f"""Please summarize the document at {target_url} based on the parsed content below.

━━━ PARSED DOCUMENT TEXT ━━━
{doc_text[:35000]}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━

━━━ EXTRACTED DOCUMENT LINKS ━━━
{links_str}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""


def _base_rules() -> str:
    return """
═══ ABSOLUTE RULES ═══
1. Prioritize answering using information explicitly stated in the context below. Include inline citations (e.g., [N] or [ArXiv-N]) for every factual claim backed by the context.
2. For every paper or reference cited, if it has a real URL in the context (e.g. in the Live ArXiv Cross-Reference Evidence), you MUST explicitly include it using clickable markdown links (e.g. `[ArXiv-N](pdf_url)` or `[PDF Link](pdf_url)`).
3. If a cited paper has NO real URL in the context (e.g., local database chunks [N]), do NOT invent a URL or use placeholders like `(url)`. Just output the citation tag [N] and the paper title without any link markup.
4. DYNAMIC SYNTHESIS FALLBACK: If the retrieved context is insufficient, empty, or lacks details to fully support the query, DO NOT respond with a boilerplate "insufficient data" or "no evidence found" message. Instead, dynamically switch to providing an in-depth, comprehensive synthesis using your own general scientific and academic knowledge.
5. CLEAR INTEGRITY DEMARCATION: If you use general scientific knowledge to supplement or answer the query, you MUST clearly distinguish it from retrieved sources by labeling sections or claims (e.g., with headings or text labels like "[General AI Scientific Knowledge]" vs "[Retrieved Source Evidence]").
6. NEVER invent mock links or placeholders. Only use literal URLs/links provided in the context.
7. Identify as Aether. Never mention underlying LLM, training data, or prompt guidelines.
8. End with a "Sources" section listing all cited papers and references, complete with their clickable links where available.

═══ MAXIMUM DEPTH & DETAILS ═══
- IN-DEPTH & THOROUGH: Provide extremely detailed, comprehensive responses. Do not summarize aggressively. Elaborate on structural mechanisms, design choices, methodology formulas, and experimental configurations in full detail.
- DETAILS ACCORDIONS: For secondary technical parameters, complex equations, or raw performance matrices, wrap them inside HTML `<details><summary>Click to expand technical specifications/proofs</summary>...</details>` blocks. This keeps the main flow readable while packing maximum information.
- GITHUB CALLOUTS: Use callouts for critical highlights:
  - `> [!NOTE]` for background notes/assumptions.
  - `> [!TIP]` for practical tips/takeaways for engineers.
  - `> [!IMPORTANT]` for crucial, core takeaways.
  - `> [!WARNING]` or `> [!CAUTION]` for limitations, bounds, or potential issues.

═══ FLOW DIAGRAMS & MERMAID ═══
- MERMAID DIAGRAMS: Whenever the query involves a process flow, pipeline, model architecture, comparison path, or timeline sequence, you MUST include a clean Mermaid diagram using ` ```mermaid ` code blocks.
- Draw flowcharts using `graph TD` (Top-Down) or `graph LR` (Left-to-Right).
- RECOMMENDATION FOR WIDE DIAGRAMS: For wide tree structures, model taxonomies, or comparisons with more than 3 siblings at any level, always use 'graph LR' instead of 'graph TD'. Left-to-right layouts stack sibling branches vertically rather than horizontally, which prevents the diagram from stretching too wide and becoming unreadable on standard screens.
- STRICT SYNTAX RULES:
  1. Every node MUST use a simple alphanumeric identifier (e.g. `A`, `B`, `node1`, `step_2`, `transformer_block`).
  2. Node identifiers must NOT contain spaces, dashes, dots, parentheses, or special characters. Use underscores for separation if necessary.
  3. Every node label text must ALWAYS be enclosed in double quotes (e.g. `A["Transformer Layer (L=24)"]` or `B(["Input Tokens"])` or `C{"Check Condition"}`). Never leave labels unquoted.
  4. Draw connections strictly between node identifiers (e.g. `A --> B` or `step_1 ==> step_2`). Never use labels or text containing spaces to draw connections directly.
  5. Connection labels must always use the pipe syntax immediately following the arrow with no spaces, e.g. `A -->|Yes| B` or `A ==>|No| B`. Do not write `A --> |Yes| B` or `A -- Yes --> B`.
  6. Never use raw HTML tags (like `<br>` or `<b>`) inside node labels.

═══ SMART GRAPH + VECTOR SYNTHESIS ═══
- INTEGRATE KNOWLEDGE: Combine granular textual evidence from "RETRIEVED CHUNK EVIDENCE" with the structural metadata (venues, authors, year, direct links) from "GRAPH RELATIONSHIP CONTEXT".
- TRACE RESEARCH LINEAGE: Highlight if key papers share authors, are co-cited, or publish in the same venue/domain to show how the research is connected.

═══ FORMATTING & SCANNABILITY ═══
- READABLE PARAGRAPHS: Group logically into clear, structured paragraphs.
- VISUAL HIERARCHY: Use ## for main topics, ### for sub-topics, and --- for separators.
- EMPHASIS: Use **Bold** for paper titles, key terms, and critical findings.
- DATA ORG: Use Markdown Tables for comparisons and Bullet Points for lists.
- BIG PICTURE: Use Blockquotes (>) for high-level research conclusions.
- MATHEMATICS & FORMULAS: Write ALL mathematical formulas, variables, equations, and expressions using standard LaTeX syntax. Wrap inline formulas in single dollar signs (e.g., $x_i$ or $\alpha$) and block/display equations in double dollar signs (e.g., $$y = f(x)$$) so they render properly using MathJax. Never use plain text formulas.
"""



def grounded_prompt(
    query: str,
    chunks: List[Dict],
    graph_nodes: List[Dict],
    arxiv_papers: List[Dict] = None,
    s2_papers: List[Dict] = None,
) -> str:
    chunk_text = (
        "\n\n".join(
            f"[{i+1}] {c.get('title','?')} | {c.get('section') or 'N/A'} | sim={c.get('similarity',0):.2f}\n{c.get('chunk','')}"
            for i, c in enumerate(chunks)
        )
        if chunks
        else "(No relevant chunks retrieved.)"
    )

    graph_ctx = build_relationship_context(graph_nodes)
    arxiv_ctx = format_arxiv_context(arxiv_papers)
    s2_ctx = format_s2_context(s2_papers)
    pwc_ctx = format_pwc_context(arxiv_papers or [])

    return f"""You are Aether, a precise research assistant grounded in retrieved evidence.
{_base_rules()}

━━━ QUERY ━━━
{query}
━━━━━━━━━━━━

{graph_ctx}

{arxiv_ctx}

{s2_ctx}

{pwc_ctx}

=== RETRIEVED CHUNK EVIDENCE ===
{chunk_text}

━━━ QUERY (reminder) ━━━
{query}

═══ SMART RESPONSE INSTRUCTIONS ═══
Analyze the query and all provided evidence. Structure your response as follows:

1. **Executive Summary** — 2–3 sentence direct answer.
2. **Visual Flowchart** — If applicable, include a detailed Mermaid flowchart (e.g., `graph TD`) depicting the system architecture, model layers, or procedural flow described in the papers to visually aid the user.
3. **Detailed Analysis** — Extensive thematic sections with methodology, findings, and relationships. Use HTML details-accordions for dense parameters/formulations and callouts to highlight constraints/notes.
4. **Research Lineage & Graph Connections** — How cited papers connect (use graph context).
5. **Key Papers & Citation Impact** — List the most important papers with citation counts from context.
6. **Datasets & Code Resources** — If code repos or datasets appear in the context, list them with links.
7. **Productivity & Practical Applications** — Concrete takeaways: tools researchers/engineers can use TODAY, open problems, recommended next papers to read.
8. **References** — Full citation list with links (arXiv/DOI/PDF).

Only include sections that have relevant evidence or relevant general scientific knowledge. Prioritize retrieved evidence, but feel free to synthesize general scientific knowledge to make the answer comprehensive and detailed.
"""


def compare_prompt(
    query: str,
    chunks: List[Dict],
    graph_nodes: List[Dict],
    arxiv_papers: List[Dict] = None,
    s2_papers: List[Dict] = None,
) -> str:
    chunk_text = (
        "\n\n".join(
            f"[{i+1}] {c.get('title','?')} | {c.get('section') or 'N/A'}\n{c.get('chunk','')}"
            for i, c in enumerate(chunks)
        )
        if chunks
        else "(No relevant chunks retrieved.)"
    )

    graph_ctx = build_relationship_context(graph_nodes)
    arxiv_ctx = format_arxiv_context(arxiv_papers)
    s2_ctx = format_s2_context(s2_papers)
    pwc_ctx = format_pwc_context(arxiv_papers or [])

    return f"""You are Aether. Compare the requested papers using the evidence below, supplemented by general scientific knowledge if the evidence is sparse or missing.
{_base_rules()}

━━━ QUERY ━━━
{query}
━━━━━━━━━━━━

{graph_ctx}

{arxiv_ctx}

=== EVIDENCE ===
{chunk_text}

═══ SMART COMPARISON INSTRUCTIONS ═══
Analyze the query, the paper comparison aspects, and the graph relationships.
- DYNAMIC ADAPTATION: Adapt the comparison format to best fit the user's query and specific research questions. Feel free to focus on specific aspects (e.g., comparison of a single metric) instead of forcing a full rigid table if the user requests a simple explanation.
- VISUAL COMPARISON DIAGRAM: Draw a detailed Mermaid side-by-side or pipeline diagram highlighting the core difference in architecture or data flow between the compared methods.
- SUGGESTED FRAMEWORK (for comprehensive comparisons):
  1. **Overview**: A 1-2 sentence high-level summary of each paper's main focus.
  2. **Key Differences Table**: A detailed markdown table comparing specific dimensions (e.g., methodology, dataset size, parameters, performance).
  3. **Visual Architecture Comparison**: The Mermaid diagram showing compared pipelines.
  4. **Citation Impact**: Mention citation counts for each paper (from context) if available.
  5. **Datasets & Code**: If code repos or datasets appear in context, include them.
  6. **Shared Foundations & Connections**: Describe how the papers relate to each other.
  7. **Which to Use When**: Concrete, evidence-backed decision guidelines for researchers. Use Callouts (`> [!TIP]`) to recommend selections.
  8. **Productivity & Practical Applications** — Tools engineers can use TODAY, datasets to experiment with. Use HTML details accordions for long parameter lists or logs.
  9. **Sources**: A list of cited sources.

{s2_ctx}

{pwc_ctx}
"""


def survey_prompt(
    query: str,
    chunks: List[Dict],
    graph_nodes: List[Dict],
    arxiv_papers: List[Dict] = None,
    s2_papers: List[Dict] = None,
) -> str:
    chunk_text = (
        "\n\n".join(
            f"[{i+1}] {c.get('title','?')} ({c.get('year','?')}) | {c.get('section') or 'N/A'}\n{c.get('chunk','')}"
            for i, c in enumerate(chunks)
        )
        if chunks
        else "(No relevant chunks retrieved.)"
    )

    graph_ctx = build_relationship_context(graph_nodes)
    arxiv_ctx = format_arxiv_context(arxiv_papers)
    s2_ctx = format_s2_context(s2_papers)
    pwc_ctx = format_pwc_context(arxiv_papers or [])

    return f"""You are Aether. Generate a structured mini-survey of the research area using the evidence below, supplemented by general scientific knowledge if the evidence is sparse or missing.
{_base_rules()}

━━━ TOPIC ━━━
{query}
━━━━━━━━━━━━

{graph_ctx}

{arxiv_ctx}

{s2_ctx}

{pwc_ctx}

=== EVIDENCE ===
{chunk_text}

═══ SMART SURVEY INSTRUCTIONS ═══
Synthesize the evidence into a smart, structured literature survey.
- SURVEY TAXONOMY FLOW: Include a Mermaid diagram (preferably 'graph LR' to stack taxonomic branches vertically and maximize text readability) depicting the taxonomic classification or methodological progression of models in this area.
- SUGGESTED FRAMEWORK:
  1. **Area Overview**: A 2-3 sentence blockquote of the current state of this research area.
  2. **Model Taxonomy Diagram**: The Mermaid tree diagram illustrating models.
  3. **Key Papers & Contributions**: For each major paper: **Title** (Year, Cited X×) — contribution [citation].
  4. **Research Evolution & Timeline**: Chronological narrative of how methods evolved.
  5. **Dominant Methods Table**: Markdown table comparing dominant methods.
  6. **Datasets & Code Resources**: List all datasets and code repos found in the evidence with links.
  7. **Productivity & Practical Applications** — Tools researchers can use TODAY, open problems, next papers to read. Wrap long benchmark specs inside HTML details accordions.
  8. **Open Challenges & Future Directions**: Unsolved problems from the evidence. Use callouts (`> [!WARNING]`) to highlight research gaps.
  9. **Sources**: A list of cited sources with links.
"""


def timeline_prompt(
    query: str,
    chunks: List[Dict],
    graph_nodes: List[Dict],
    arxiv_papers: List[Dict] = None,
    s2_papers: List[Dict] = None,
) -> str:
    # Sort graph nodes by year for timeline construction
    sorted_nodes = sorted(
        [n for n in graph_nodes if n.get("year")], key=lambda n: int(n.get("year", 0))
    )

    papers_by_year = {}
    for n in sorted_nodes:
        yr = str(n.get("year", "?"))
        papers_by_year.setdefault(yr, []).append(n.get("title", "?"))

    timeline_text = "\n".join(
        f"  {yr}: " + " | ".join(titles)
        for yr, titles in sorted(papers_by_year.items())
    )

    chunk_text = (
        "\n\n".join(
            f"[{i+1}] {c.get('title','?')} ({c.get('year','?')}) | {c.get('section') or 'N/A'}\n{c.get('chunk','')}"
            for i, c in enumerate(chunks)
        )
        if chunks
        else "(No relevant chunks retrieved.)"
    )

    arxiv_ctx = format_arxiv_context(arxiv_papers)
    s2_ctx = format_s2_context(s2_papers)
    pwc_ctx = format_pwc_context(arxiv_papers or [])

    return f"""You are Aether. Construct a chronological timeline of research evolution using the evidence below, supplemented by general scientific knowledge if the evidence is sparse or missing.
{_base_rules()}

━━━ TOPIC ━━━
{query}

PAPERS ORDERED BY YEAR:
{timeline_text if timeline_text else '(insufficient timeline data)'}
━━━━━━━━━━━━

{arxiv_ctx}

{s2_ctx}

{pwc_ctx}

=== CHUNK EVIDENCE ===
{chunk_text}

═══ SMART TIMELINE INSTRUCTIONS ═══
Construct a smart, narrative-driven research timeline.
- CHRONOLOGICAL MILESTONE FLOW: Include a Mermaid workflow diagram (preferably 'graph LR' to stack milestones vertically and prevent horizontal squeezing) showing the logical sequence of key milestones.
- SUGGESTED FRAMEWORK:
  1. **Milestone Flow Diagram**: The Mermaid timeline roadmap.
  2. **Chronological Milestones**: For each key year: `[YEAR]` — **Paper Title** (Cited X×) — Key breakthrough [citation].
  3. **Breakthrough Moments**: Highlight when the field pivoted to new techniques. Use Callouts (`> [!IMPORTANT]`) to highlight the shift.
  4. **Datasets & Code**: If datasets or repos appear, list them with links.
  5. **Productivity & Practical Applications** — Tools researchers can use TODAY, open problems.
  6. **Open Challenges & Research Gaps**: Unsolved problems at the end of the timeline.
  7. **Sources**: A list of cited sources.
"""


def clean_and_resolve_links(
    answer: str,
    chunks: Optional[List[Dict]] = None,
    graph_nodes: Optional[List[Dict]] = None,
    arxiv_papers: Optional[List[Dict]] = None,
) -> str:
    """Validates and replaces hallucinated or placeholder links in the response with real ones.

    - [ArXiv-X] -> Clickable link to the real arXiv PDF
    - [X] -> Clickable link to Google Scholar for the paper title
    - Any hallucinated/placeholder markdown links -> Resolved to real URLs
    """
    import urllib.parse

    # 1. Build a map of 1-based indices to real arXiv URLs
    arxiv_map = {}
    if arxiv_papers:
        for idx, p in enumerate(arxiv_papers):
            pdf_url = (
                p.get("pdf_url")
                or p.get("url")
                or f"https://arxiv.org/abs/{p.get('id', '')}"
            )
            url = p.get("url") or pdf_url
            arxiv_map[idx + 1] = {
                "pdf_url": pdf_url,
                "url": url,
                "title": p.get("title", ""),
                "id": p.get("id", ""),
            }

    # 2. Build a map of 1-based indices to database chunk paper titles and Scholar links
    chunk_map = {}
    if chunks:
        for idx, c in enumerate(chunks):
            title = c.get("title") or c.get("paper_title") or ""
            if title:
                encoded_title = urllib.parse.quote_plus(title)
                scholar_url = (
                    f"https://scholar.google.com/scholar?q={encoded_title}"
                )
                chunk_map[idx + 1] = {"url": scholar_url, "title": title}

    # 3. Build a title-to-Scholar URL map for direct text matches
    graph_map = {}
    if graph_nodes:
        for n in graph_nodes:
            title = n.get("title")
            if title:
                encoded_title = urllib.parse.quote_plus(title)
                scholar_url = (
                    f"https://scholar.google.com/scholar?q={encoded_title}"
                )
                graph_map[title.lower()] = scholar_url

    # 4. Resolve/replace markdown links
    def link_replacer(match):
        text = match.group(1).strip()
        url = match.group(2).strip()

        # Check if the URL is a placeholder or fake
        url_lower = url.lower()
        is_placeholder = (
            any(
                x in url_lower
                for x in [
                    "pdf_url",
                    "arxiv_url",
                    "placeholder",
                    "fake",
                    "link",
                    "url",
                ]
            )
            or url == "#"
            or not url.startswith("http")
        )

        # Try to resolve based on citation text (e.g. [ArXiv-1] or [1])
        arxiv_cite = re.search(r"arxiv-(\d+)", text.lower())
        if arxiv_cite:
            num = int(arxiv_cite.group(1))
            if num in arxiv_map:
                return f"[{text}]({arxiv_map[num]['pdf_url']})"

        num_cite = re.search(r"^\[?(\d+)\]?$", text)
        if num_cite:
            num = int(num_cite.group(1))
            if num in chunk_map:
                return f"[{text}]({chunk_map[num]['url']})"

        # Check if the URL has an arXiv ID that we have in our list
        for num, p in arxiv_map.items():
            if p["id"] and p["id"] in url:
                return f"[{text}]({p['pdf_url']})"
            if p["title"] and p["title"].lower() in text.lower():
                return f"[{text}]({p['pdf_url']})"

        # Check if it matches a graph paper title
        for t_lower, s_url in graph_map.items():
            if t_lower in text.lower() or t_lower in url_lower:
                return f"[{text}]({s_url})"

        # If it's a placeholder link, try to salvage it or remove the link markup
        if is_placeholder:
            # Try matching title substrings
            for num, p in arxiv_map.items():
                if (
                    p["title"]
                    and len(p["title"]) > 10
                    and p["title"].lower()[:25] in text.lower()
                ):
                    return f"[{text}]({p['pdf_url']})"
            for t_lower, s_url in graph_map.items():
                if len(t_lower) > 10 and t_lower[:25] in text.lower():
                    return f"[{text}]({s_url})"
            # Fallback: remove the link markup to avoid fake link, keeping the text
            return text

        return match.group(0)

    # Replace all [Link Text](URL)
    answer = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", link_replacer, answer)

    # Match pattern: [ArXiv-X] ... [ArXiv Link](placeholder_url)
    def arxiv_link_placeholder_replacer(match):
        num = int(match.group(1))
        between = match.group(2)
        placeholder = match.group(3)
        if num in arxiv_map:
            return f"[ArXiv-{num}]{between}[ArXiv Link]({arxiv_map[num]['pdf_url']})"
        return match.group(0)

    answer = re.sub(
        r"\[ArXiv-(\d+)\]([^\n]{0,150}?)(?:\[ArXiv Link\]|\[PDF Link\]|\[PDF\]|\[Link\])\(([^)]*)\)",
        arxiv_link_placeholder_replacer,
        answer,
    )

    # Match pattern: [X] ... [Google Scholar](placeholder_url)
    def standard_link_placeholder_replacer(match):
        num = int(match.group(1))
        between = match.group(2)
        placeholder = match.group(3)
        if num in chunk_map:
            return f"[{num}]{between}[Google Scholar]({chunk_map[num]['url']})"
        return match.group(0)

    answer = re.sub(
        r"\[(\d+)\]([^\n]{0,150}?)(?:\[Google Scholar\]|\[Scholar Link\]|\[Link\])\(([^)]*)\)",
        standard_link_placeholder_replacer,
        answer,
    )

    # 5. Convert raw citation tags like [ArXiv-1] or [1] to markdown links
    def arxiv_tag_replacer(match):
        num = int(match.group(1))
        if num in arxiv_map:
            return f"[ArXiv-{num}]({arxiv_map[num]['pdf_url']})"
        return match.group(0)

    answer = re.sub(r"\[ArXiv-(\d+)\](?!\()", arxiv_tag_replacer, answer)

    def chunk_tag_replacer(match):
        num = int(match.group(1))
        if num in chunk_map:
            return f"[{num}]({chunk_map[num]['url']})"
        return match.group(0)

    answer = re.sub(r"\[(\d+)\](?!\()", chunk_tag_replacer, answer)

    # 6. Clean up leftover parentheses from placeholder removals
    answer = re.sub(r"\((pdf_url|url|arxiv_url|placeholder|link)\)", "", answer)

    return answer


# ================================================================
# VERIFICATION PASS
# ================================================================



def extract_verifiable_claims(answer: str) -> List[str]:
    years = re.findall(r"\b(19|20)\d{2}\b", answer)
    numbers = re.findall(r"\b\d+(?:\.\d+)?%?\b", answer)
    names = re.findall(r"\b[A-Z][a-z]+ et al\.?", answer)
    quoted = re.findall(r'"([^"]{4,60})"', answer)
    return list(set(years + numbers + names + quoted))


def hard_verify(claims: List[str], chunks: List[Dict]) -> List[str]:
    raw = " ".join(c.get("chunk", "") for c in chunks).lower()
    return [c for c in claims if c.lower() not in raw]


def sanitise_flagged(flagged: List[str]) -> List[str]:
    SKIP = (
        "DIRECT SUPPORT",
        "VERDICT",
        "SOURCE",
        "CLAIM",
        "FULLY SUPPORTED",
        "PARTIALLY",
        "NOT SUPPORTED",
    )
    return [
        f
        for f in flagged
        if f.strip()
        and not any(f.strip().upper().startswith(p) for p in SKIP)
        and len(f) <= 120
        and f.lower() != "none"
    ]


async def verify_answer(answer: str, chunks: List[Dict], model: str) -> Dict[str, Any]:
    flagged_hard = hard_verify(extract_verifiable_claims(answer), chunks)
    chunk_text = "\n\n".join(
        f"[{i+1}] {c.get('title','?')}: {c.get('chunk','')}"
        for i, c in enumerate(chunks)
    )
    focus = (
        ("Pay special attention:\n" + "\n".join(f"  - {c}" for c in flagged_hard))
        if flagged_hard
        else ""
    )

    verify_prompt = f"""Fact-check this AI answer against source documents.

ANSWER:
{answer}

SOURCES:
{chunk_text}

{focus}

INSTRUCTIONS: Break into individual factual claims. Check each against sources. Rate confidence 0–1.

Respond ONLY in this format:
CONFIDENCE: <0.0-1.0>
VERIFIED_CLAIMS: <count>
TOTAL_CLAIMS: <count>
FLAGGED:
- <unsupported claim or "None">
VERDICT: <PASS / PARTIAL / FAIL>"""

    try:
        result = await groq_chat(
            [{"role": "user", "content": verify_prompt}],
            model,
            temperature=0.0,
            max_tokens=500,
        )
        conf, verified, total, flagged, verdict = 0.5, 0, 0, [], "UNKNOWN"
        for line in result.strip().split("\n"):
            line = line.strip()
            if line.startswith("CONFIDENCE:"):
                try:
                    conf = max(0.0, min(1.0, float(line.split(":")[1])))
                except:
                    pass
            elif line.startswith("VERIFIED_CLAIMS:"):
                try:
                    verified = int(line.split(":")[1])
                except:
                    pass
            elif line.startswith("TOTAL_CLAIMS:"):
                try:
                    total = int(line.split(":")[1])
                except:
                    pass
            elif line.startswith("VERDICT:"):
                verdict = line.split(":")[1].strip()
            elif line.startswith("- ") and "None" not in line:
                flagged.append(line[2:])
        return {
            "confidence": conf,
            "verified_claims": verified,
            "total_claims": total,
            "flagged_claims": flagged,
            "verdict": verdict,
            "raw": result,
        }
    except LLMError as e:
        return {
            "confidence": None,
            "verified_claims": None,
            "total_claims": None,
            "flagged_claims": [],
            "verdict": "SKIPPED",
            "error": str(e),
        }


# ================================================================
# SHARED PIPELINE HELPERS
# ================================================================


async def run_vector_pipeline(
    query: str,
    embedding: List[float],
    top_k: int,
    min_similarity: float,
    graph_nodes: List[Dict],
    rid: str,
) -> List[Dict]:
    seed_ids = [
        g["research_id"]
        for g in graph_nodes
        if g.get("score", 1) == 2 and g.get("research_id")
    ]
    expanded_ids = [
        g["research_id"]
        for g in graph_nodes
        if g.get("score", 1) == 1 and g.get("research_id")
    ]

    tasks = []
    if seed_ids:
        tasks.append(vector_search(embedding, min_similarity, top_k * 5, seed_ids))
        tasks.append(hybrid_search(query, embedding, top_k * 5, seed_ids))
    if expanded_ids:
        tasks.append(
            vector_search(
                embedding, max(min_similarity - 0.05, 0.0), top_k * 4, expanded_ids
            )
        )
    if not tasks:
        tasks.append(vector_search(embedding, min_similarity, top_k * 6))
        tasks.append(hybrid_search(query, embedding, top_k * 6))

    results = await asyncio.gather(*tasks, return_exceptions=True)
    valid = [r for r in results if isinstance(r, list)]

    if not valid:
        raise VectorSearchError("All search tasks failed.")

    fused = reciprocal_rank_fusion(valid) if len(valid) > 1 else valid[0]
    fused = fused[: top_k * 5]  # pre-filter pool
    fused = filter_relevant_chunks(fused, min_similarity)

    # Section-priority sort (within same similarity band)
    fused = sorted(
        fused,
        key=lambda c: (
            -get_chunk_similarity(c),  # highest similarity first
            section_priority(c),  # then abstract > conclusion > body
        ),
    )

    # MMR re-rank for diversity
    final = mmr_rerank(fused, embedding, top_k)
    log.info(f"[{rid}] Chunks: {len(final)} after fusion+filter+MMR")
    return final


async def apply_verification(
    answer: str, chunks: List[Dict], model: str, rid: str, warning: Optional[str]
) -> Tuple[str, Optional[Dict], Optional[str]]:
    verification = await verify_answer(answer, chunks, model)
    conf = verification.get("confidence", 1.0)
    verdict = verification.get("verdict", "PASS")
    flagged = sanitise_flagged(verification.get("flagged_claims", []))
    verification["flagged_claims"] = flagged

    log.info(f"[{rid}] Verify: conf={conf}, verdict={verdict}")

    if verdict == "FAIL" or verdict == "PARTIAL" or (conf is not None and conf < 0.7):
        suffix = (
            ("\n".join(f"  - {c}" for c in flagged))
            if flagged
            else "Some claims may be unsupported by retrieved context."
        )
        conf_pct = f"{conf:.0%}" if conf is not None else "N/A"
        if conf is not None and conf < 0.5:
            answer = f"{answer}\n\n---\n⚠️ **Verification Alert (High Hallucination Risk - {conf_pct} confidence)**:\nUnverified claims:\n{suffix}"
            warning = (warning or "") + f" Verification failed (confidence < 50%)."
        else:
            answer = f"{answer}\n\n---\n⚠️ **Verification Warning ({conf_pct} confidence)**:\nUnverified claims:\n{suffix}"
            warning = (warning or "") + f" Verification warning (confidence < 70%)."

    return answer, verification, warning


def build_conversation_context(messages: List[ChatMessage], n: int = 3) -> str:
    recent = [m for m in messages[-n * 2 :] if m.role in ("user", "assistant")]
    return "\n".join(f"{m.role.upper()}: {m.content[:300]}" for m in recent) or "None"


# ================================================================
# RESEARCH ENDPOINT
# ================================================================


@app.post("/api/research")
async def research(req: ResearchRequest, request: Request):
    rid = str(uuid.uuid4())
    request.state.request_id = rid
    try:
        return await asyncio.wait_for(
            _research_impl(req, request), timeout=REQUEST_TIMEOUT
        )
    except asyncio.TimeoutError:
        raise HTTPException(504, f"Timed out after {REQUEST_TIMEOUT}s.")


async def _research_impl(req: ResearchRequest, request: Request):
    pool.assert_ready()
    rid = getattr(request.state, "request_id", "unknown")
    await check_rate_limit(request.client.host if request.client else "unknown")
    await set_user_context(request)
    t0 = time.time()

    raw_query = req.resolved_query()
    log.info(f"\n{'='*70}\n[{rid}] QUERY: {raw_query}\n{'='*70}")

    # ── 1. Strategic planning brain ───────────────────────────────────
    plan = await plan_query(raw_query)
    query = plan.standalone_query

    # ── 2. Route: entity_lookup ───────────────────────────────────────
    if plan.route == "entity_lookup":
        anchors = plan.graph_anchors or [query]
        try:
            papers = await retrieve_graph_papers(
                keywords=anchors, anchors=anchors, limit=3
            )
        except GraphRetrievalError as e:
            raise HTTPException(502, str(e))
        if not papers:
            sys_p = "You are Aether, a research assistant. Respond comprehensively, warmly, and in detail using your general scientific knowledge, since no matching papers were found in the local index."
            answer = await groq_chat(
                [{"role": "system", "content": sys_p}, {"role": "user", "content": f"Entity lookup query: {query}"}],
                REASON_MODEL,
                temperature=req.temperature,
            )
            return _empty_response(rid, answer, "entity_lookup", t0)
        p = papers[0]
        authors_str = ", ".join(a for a in (p.get("authors") or []) if a) or "Unknown"
        answer = (
            f"**{p.get('title','?')}** ({p.get('year','?')})\n\n"
            f"Authors: {authors_str}\n"
            f"Venue: {p.get('venue') or 'Unknown'}\n"
            f"Domain: {p.get('domain','Unknown')}\n"
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
            sys_p = "You are Aether, a research assistant. Respond comprehensively, warmly, and in detail using your general scientific knowledge, listing potential papers/contributions you know of in this area, since no matching papers were found in the local index."
            answer = await groq_chat(
                [{"role": "system", "content": sys_p}, {"role": "user", "content": f"List papers related to: {query}"}],
                REASON_MODEL,
                temperature=req.temperature,
            )
            return _empty_response(rid, answer, "structured", t0)
        lines = [f"Found **{len(papers)}** papers:\n"]
        for p in papers:
            auths = ", ".join(a for a in (p.get("authors") or []) if a) or "Unknown"
            lines.append(f"- **{p.get('title','?')}** ({p.get('year','?')}) — {auths}")
        return _direct_response(rid, "\n".join(lines), "structured", papers, t0)

    # ── 4. Route: title_lookup ────────────────────────────────────────
    if plan.route == "title_lookup":
        anchors = plan.graph_anchors or [query]
        try:
            papers = await retrieve_graph_papers(
                keywords=anchors, anchors=anchors, limit=5
            )
        except GraphRetrievalError as e:
            raise HTTPException(502, str(e))
        if not papers:
            sys_p = "You are Aether, a research assistant. Respond comprehensively, warmly, and in detail using your general scientific knowledge, providing details about the paper if you know it, since it was not found in the local index."
            answer = await groq_chat(
                [{"role": "system", "content": sys_p}, {"role": "user", "content": f"Provide information on the paper: {query}"}],
                REASON_MODEL,
                temperature=req.temperature,
            )
            return _empty_response(rid, answer, "title_lookup", t0)
        p = papers[0]
        auths = ", ".join(a for a in (p.get("authors") or []) if a) or "Unknown"
        abstract = (p.get("abstract") or "")[:400]
        answer = (
            f"**{p.get('title','?')}** ({p.get('year','?')})\n\n"
            f"Authors: {auths}\n"
            f"Venue: {p.get('venue') or 'Unknown'}\n"
            f"Domain: {p.get('domain','Unknown')}\n"
            f"Citations: {p.get('in_citations','N/A')}\n\n"
            f"Abstract: {abstract}{'...' if len(p.get('abstract',''))>400 else ''}"
        )
        return _direct_response(rid, answer, "title_lookup", papers, t0)

    # ── 5. Route: chitchat ────────────────────────────────────────────
    if plan.route == "chitchat":
        sys_p = "You are Aether, a research assistant. Respond comprehensively, warmly, and in detail. Don't invent academic facts."
        answer = await groq_chat(
            [{"role": "system", "content": sys_p}, {"role": "user", "content": query}],
            REASON_MODEL,
            temperature=req.temperature,
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
        # Retrieve relevant abstracts from arXiv dynamically in parallel
        return await retrieve_arxiv_context(query, limit=5)

    graph_nodes, chunks, arxiv_papers = await asyncio.gather(
        fetch_graph(), fetch_supabase(), fetch_arxiv()
    )
    chunks = merge_adjacent_chunks(chunks)
    chunks = pack_context_within_budget(chunks, limit_tokens=5000)

    # ── Enrich arXiv papers with S2 (citation counts, TLDR) + PwC (code, datasets) ──
    if arxiv_papers:
        arxiv_papers, s2_papers = await asyncio.gather(
            enrich_arxiv_papers_with_pwc(arxiv_papers),
            search_papers_s2(query, limit=5),
        )
        arxiv_papers = await enrich_arxiv_papers_with_s2(arxiv_papers)
    else:
        s2_papers = await search_papers_s2(query, limit=5)

    if not chunks and not arxiv_papers and not s2_papers:
        warning = (warning or "") + " No chunks or external papers retrieved."

    # Collect all datasets/repos from enriched papers for response
    all_datasets = []
    all_repos = []
    for p in arxiv_papers:
        all_datasets.extend(p.get("datasets") or [])
        all_repos.extend(p.get("code_repos") or [])
    # Deduplicate datasets by name
    seen_ds = set()
    unique_datasets = []
    for d in all_datasets:
        key = d.get("name", "")
        if key and key not in seen_ds:
            seen_ds.add(key)
            unique_datasets.append(d)

    # Pick prompt by route
    model = HEAVY_MODEL if req.use_heavy else REASON_MODEL
    if plan.route == "compare":
        prompt = compare_prompt(query, chunks, graph_nodes, arxiv_papers, s2_papers)
    elif plan.route == "survey":
        prompt = survey_prompt(query, chunks, graph_nodes, arxiv_papers, s2_papers)
        model = HEAVY_MODEL  # surveys always use heavy model
    elif plan.route == "timeline":
        prompt = timeline_prompt(query, chunks, graph_nodes, arxiv_papers, s2_papers)
    else:
        prompt = grounded_prompt(query, chunks, graph_nodes, arxiv_papers, s2_papers)

    try:
        answer = await groq_chat(
            [{"role": "system", "content": prompt}],
            model,
            temperature=req.temperature,
            max_tokens=2000,
        )
    except LLMError as e:
        raise HTTPException(502, str(e))

    verification = None
    if req.verify and chunks:
        answer, verification, warning = await apply_verification(
            answer, chunks, REASON_MODEL, rid, warning
        )

    if verification:
        verification.pop("raw", None)

    answer = clean_and_resolve_links(
        answer,
        chunks,
        graph_nodes,
        arxiv_papers if plan.route not in ("chitchat", "structured", "title_lookup", "entity_lookup") else []
    )

    latency = int((time.time() - t0) * 1000)
    log.info(f"[{rid}] Done — {plan.route} | {model} | {latency}ms")

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
    }


# ================================================================
# CONVERSATION ENDPOINT
# ================================================================


@app.post("/api/chat")
async def chat_with_context(req: ConversationRequest, request: Request):
    rid = str(uuid.uuid4())
    request.state.request_id = rid
    try:
        return await asyncio.wait_for(_chat_impl(req, request), timeout=REQUEST_TIMEOUT)
    except asyncio.TimeoutError:
        raise HTTPException(504, f"Timed out after {REQUEST_TIMEOUT}s.")


async def _chat_impl(req: ConversationRequest, request: Request):
    pool.assert_ready()
    rid = getattr(request.state, "request_id", "unknown")
    await check_rate_limit(request.client.host if request.client else "unknown")
    await set_user_context(request)
    t0 = time.time()

    last_user_msg = next(
        (m.content for m in reversed(req.messages) if m.role == "user"), None
    )
    if not last_user_msg:
        raise HTTPException(400, "No user message found.")

    log.info(f"[{rid}] CHAT: {last_user_msg}")

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
            {"role": "user", "content": user_content}
        ]
        try:
            answer = await groq_chat(msgs, HEAVY_MODEL, temperature=req.temperature, max_tokens=2500)
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


    # Compile parsed PDF context for the LLM
    all_urls = list(dict.fromkeys(history_urls + latest_urls))
    pdf_context_parts = []
    for url in all_urls:
        doc_text, doc_links = await get_or_parse_pdf_safe(url, raise_on_error=False)
        if doc_text:
            pdf_context_parts.append(
                f"━━━ PARSED DOCUMENT CONTEXT FOR {url} ━━━\n"
                f"{doc_text[:30000]}\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
            )
    pdf_context = "\n\n".join(pdf_context_parts) if pdf_context_parts else ""

    # Build context string for pronoun resolution
    ctx = build_conversation_context(req.messages[:-1])

    # ── Strategic planning (includes pronoun resolution) ─────────────
    plan = await plan_query(last_user_msg, context=ctx)
    query = plan.standalone_query

    # ── Route: entity_lookup ──────────────────────────────────────────
    if plan.route == "entity_lookup":
        anchors = plan.graph_anchors or [query]
        try:
            papers = await retrieve_graph_papers(
                keywords=anchors, anchors=anchors, limit=3
            )
        except GraphRetrievalError as e:
            raise HTTPException(502, str(e))
        if not papers:
            if pdf_context:
                # Fallback: run chitchat route with PDF context instead of returning empty
                sys_p = "You are Aether. Respond comprehensively, warmly, and in detail. Do not invent academic facts."
                sys_p += f"\n\n{pdf_context}"
                msgs = [{"role": "system", "content": sys_p}] + [
                    {"role": m.role, "content": m.content} for m in req.messages
                ]
                answer = await groq_chat(msgs, REASON_MODEL, temperature=req.temperature)
                return _empty_response(rid, answer, "chitchat", t0)
            else:
                return _empty_response(
                    rid, "⚠️ No matching paper found.", "entity_lookup", t0
                )
        p = papers[0]
        auths = ", ".join(a for a in (p.get("authors") or []) if a) or "Unknown"
        answer = (
            f"**{p.get('title','?')}** ({p.get('year','?')})\n\n"
            f"Authors: {auths}\n"
            f"Venue: {p.get('venue') or 'Unknown'}\n"
            f"Domain: {p.get('domain','Unknown')}"
        )
        return _direct_response(rid, answer, "entity_lookup", papers, t0)

    # ── Route: structured ─────────────────────────────────────────────
    if plan.route == "structured":
        kw = plan.graph_anchors or plan.vector_keywords or [query]
        try:
            papers = await retrieve_graph_papers(
                keywords=kw, filters=req.filters, anchors=plan.graph_anchors, limit=20
            )
        except GraphRetrievalError as e:
            raise HTTPException(502, str(e))
        if not papers:
            if pdf_context:
                # Fallback: run chitchat route with PDF context instead of returning empty
                sys_p = "You are Aether. Respond comprehensively, warmly, and in detail. Do not invent academic facts."
                sys_p += f"\n\n{pdf_context}"
                msgs = [{"role": "system", "content": sys_p}] + [
                    {"role": m.role, "content": m.content} for m in req.messages
                ]
                answer = await groq_chat(msgs, REASON_MODEL, temperature=req.temperature)
                return _empty_response(rid, answer, "chitchat", t0)
            else:
                return _empty_response(rid, "⚠️ No papers found.", "structured", t0)
        lines = [f"Found **{len(papers)}** papers:\n"]
        for p in papers:
            auths = ", ".join(a for a in (p.get("authors") or []) if a) or "Unknown"
            lines.append(f"• **{p.get('title','?')}** ({p.get('year','?')}) — {auths}")
        return _direct_response(rid, "\n".join(lines), "structured", papers, t0)

    # ── Route: chitchat ───────────────────────────────────────────────
    if plan.route == "chitchat":
        sys_p = (
            "You are Aether. Respond comprehensively, warmly, and in detail. Do not invent academic facts."
        )
        if pdf_context:
            sys_p += f"\n\n{pdf_context}"
        msgs = [{"role": "system", "content": sys_p}] + [
            {"role": m.role, "content": m.content} for m in req.messages
        ]
        answer = await groq_chat(msgs, REASON_MODEL, temperature=req.temperature)
        return _empty_response(rid, answer, "chitchat", t0)

    # ── Full RAG ──────────────────────────────────────────────────────
    warning = None

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
            embedding = await create_embedding(
                " ".join(plan.vector_keywords or plan.graph_anchors or [query])
            )
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
        # Retrieve relevant abstracts from arXiv dynamically in parallel
        return await retrieve_arxiv_context(query, limit=5)

    graph_nodes, chunks, arxiv_papers = await asyncio.gather(
        fetch_graph(), fetch_supabase(), fetch_arxiv()
    )
    chunks = merge_adjacent_chunks(chunks)
    chunks = pack_context_within_budget(chunks, limit_tokens=5000)

    # ── Enrich arXiv papers with S2 + PwC ──
    if arxiv_papers:
        arxiv_papers, s2_papers = await asyncio.gather(
            enrich_arxiv_papers_with_pwc(arxiv_papers),
            search_papers_s2(query, limit=5),
        )
        arxiv_papers = await enrich_arxiv_papers_with_s2(arxiv_papers)
    else:
        s2_papers = await search_papers_s2(query, limit=5)

    # Collect datasets/repos
    all_datasets, all_repos = [], []
    for p in arxiv_papers:
        all_datasets.extend(p.get("datasets") or [])
        all_repos.extend(p.get("code_repos") or [])
    seen_ds: set = set()
    unique_datasets = []
    for d in all_datasets:
        key = d.get("name", "")
        if key and key not in seen_ds:
            seen_ds.add(key)
            unique_datasets.append(d)

    model = HEAVY_MODEL if req.use_heavy else REASON_MODEL

    if plan.route == "compare":
        prompt = compare_prompt(query, chunks, graph_nodes, arxiv_papers, s2_papers)
    elif plan.route == "survey":
        prompt = survey_prompt(query, chunks, graph_nodes, arxiv_papers, s2_papers)
        model = HEAVY_MODEL
    elif plan.route == "timeline":
        prompt = timeline_prompt(query, chunks, graph_nodes, arxiv_papers, s2_papers)
    else:
        prompt = grounded_prompt(query, chunks, graph_nodes, arxiv_papers, s2_papers)

    if pdf_context:
        prompt += f"\n\n{pdf_context}"

    msgs = [{"role": "system", "content": prompt}] + [
        {"role": m.role, "content": m.content} for m in req.messages
    ]
    try:
        answer = await groq_chat(msgs, model, temperature=req.temperature, max_tokens=2000)
    except LLMError as e:
        raise HTTPException(502, str(e))

    verification = None
    if req.verify and chunks:
        answer, verification, warning = await apply_verification(
            answer, chunks, REASON_MODEL, rid, warning
        )

    if verification:
        verification.pop("raw", None)

    show_external = plan.route not in ("chitchat", "structured", "title_lookup", "entity_lookup")
    answer = clean_and_resolve_links(
        answer,
        chunks,
        graph_nodes,
        arxiv_papers if show_external else []
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
    }


# ================================================================
# GRAPH INTELLIGENCE ENDPOINTS
# ================================================================


@app.get("/api/graph/paper/{paper_id}")
async def get_paper(paper_id: str, request: Request):
    pool.assert_ready()
    await check_rate_limit(request.client.host if request.client else "unknown")
    result = await get_paper_full(paper_id)
    if not result:
        raise HTTPException(404, f"Paper '{paper_id}' not found.")
    return result


@app.get("/api/graph/author/{author_name}")
async def get_author(author_name: str, request: Request):
    pool.assert_ready()
    await check_rate_limit(request.client.host if request.client else "unknown")
    result = await get_author_network(author_name)
    if not result:
        raise HTTPException(404, f"Author '{author_name}' not found.")
    return result


@app.post("/api/graph/citation-path")
async def citation_path(req: CitationPathRequest, request: Request):
    pool.assert_ready()
    await check_rate_limit(request.client.host if request.client else "unknown")
    result = await get_citation_path(req.from_paper, req.to_paper)
    return result


@app.get("/api/graph/trending")
async def trending(limit: int = 10, request: Request = None):
    pool.assert_ready()
    if request:
        await check_rate_limit(request.client.host if request.client else "unknown")
    papers = await get_trending_papers(limit=min(limit, 30))
    return {"papers": papers, "count": len(papers)}


@app.post("/api/graph/compare")
async def compare_papers(req: CompareRequest, request: Request):
    """Deep structured comparison of two papers using graph + vector evidence."""
    rid = str(uuid.uuid4())
    request.state.request_id = rid
    pool.assert_ready()
    await check_rate_limit(request.client.host if request.client else "unknown")
    t0 = time.time()

    aspects_str = (
        ", ".join(req.aspects)
        if req.aspects
        else "methodology, results, datasets, contributions"
    )
    query = f"Compare {req.paper_a} and {req.paper_b} in terms of: {aspects_str}"

    try:
        embedding = await create_embedding(f"{req.paper_a} {req.paper_b} {aspects_str}")
    except EmbeddingError as e:
        raise HTTPException(502, str(e))

    graph_nodes: List[Dict] = []
    try:
        graph_nodes = await retrieve_graph_papers(
            keywords=[req.paper_a, req.paper_b],
            anchors=[req.paper_a, req.paper_b],
            limit=10,
        )
    except GraphRetrievalError:
        pass

    # Also fetch full paper details for richer context
    paper_a_full, paper_b_full = await asyncio.gather(
        get_paper_full(req.paper_a),
        get_paper_full(req.paper_b),
    )

    filter_ids = [g["research_id"] for g in graph_nodes if g.get("research_id")]
    chunks: List[Dict] = []
    if filter_ids:
        try:
            chunks = await run_vector_pipeline(
                query, embedding, 12, RELEVANCE_FLOOR, graph_nodes, rid
            )
        except VectorSearchError:
            pass

    prompt = compare_prompt(query, chunks, graph_nodes)
    answer = await groq_chat(
        [{"role": "system", "content": prompt}],
        HEAVY_MODEL,
        temperature=req.temperature,
        max_tokens=2000,
    )

    return {
        "request_id": rid,
        "answer": answer,
        "paper_a": paper_a_full,
        "paper_b": paper_b_full,
        "chunks": chunks,
        "latency_ms": int((time.time() - t0) * 1000),
    }


# ================================================================
# SPECIALISED RESEARCH ENDPOINTS
# ================================================================


@app.post("/api/research/timeline")
async def research_timeline(req: TimelineRequest, request: Request):
    """Chronological evolution of a research topic."""
    rid = str(uuid.uuid4())
    request.state.request_id = rid
    pool.assert_ready()
    await check_rate_limit(request.client.host if request.client else "unknown")
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

    graph_nodes, chunks, arxiv_papers = await asyncio.gather(
        fetch_graph(), fetch_supabase(), fetch_arxiv()
    )
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


@app.post("/api/research/survey")
async def research_survey(req: SurveyRequest, request: Request):
    """Auto-generate a mini literature survey on a topic."""
    rid = str(uuid.uuid4())
    request.state.request_id = rid
    pool.assert_ready()
    await check_rate_limit(request.client.host if request.client else "unknown")
    t0 = time.time()

    async def fetch_graph():
        try:
            nodes = await retrieve_graph_papers(keywords=[req.topic], limit=req.top_k)
            # Enrich with co-citation cluster
            seed_ids = [
                g["research_id"]
                for g in nodes
                if g.get("research_id") and g.get("score") == 2
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

    graph_nodes, chunks, arxiv_papers = await asyncio.gather(
        fetch_graph(), fetch_supabase(), fetch_arxiv()
    )
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


@app.post("/api/research/bulk")
async def bulk_research(req: BulkRequest, request: Request):
    pool.assert_ready()
    await check_rate_limit(request.client.host if request.client else "unknown")
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


# ================================================================
# STATS ENDPOINT
# ================================================================


@app.get("/api/stats")
async def stats():
    graph_stats = await get_graph_stats()
    return {
        "graph": graph_stats,
        "cache_sizes": {k: len(v) for k, v in CACHE.items()},
        "cache_ttl": CACHE_TTL,
    }


# ================================================================
# OPENAI-COMPATIBLE ENDPOINTS
# ================================================================


@app.get("/v1/models")
async def list_models():
    return {
        "object": "list",
        "data": [
            {
                "id": REASON_MODEL,
                "object": "model",
                "created": 1677610602,
                "owned_by": "groq",
            },
            {
                "id": HEAVY_MODEL,
                "object": "model",
                "created": 1677610602,
                "owned_by": "groq",
            },
        ],
    }


@app.post("/v1/chat/completions")
async def chat_completions(req: ChatCompletionRequest, request: Request):
    rid = str(uuid.uuid4())
    request.state.request_id = rid
    pool.assert_ready()
    await check_rate_limit(request.client.host if request.client else "unknown")
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


# ================================================================
# ================================================================
# AUTH & HISTORY ENDPOINTS
# ================================================================

async def set_user_context(request: Request) -> Optional[str]:
    try:
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header.split(" ")[1]
            user = await get_supabase_user(token)
            if user:
                uid = user.get("id")
                current_user_id.set(uid)
                return uid
    except Exception as e:
        log.warning(f"Error setting user context: {e}")
    current_user_id.set(None)
    return None


def get_token_from_request(request: Request) -> str:
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    return auth_header.split(" ")[1]


async def get_supabase_user(token: str) -> Optional[Dict[str, Any]]:
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(
                f"{SUPABASE_URL}/auth/v1/user",
                headers={
                    "apikey": SUPABASE_KEY,
                    "Authorization": f"Bearer {token}"
                }
            )
            if resp.status_code == 200:
                return resp.json()
        except Exception as e:
            log.error(f"Error validating Supabase token: {e}")
    return None


@app.get("/api/auth/me")
async def auth_me(request: Request):
    token = get_token_from_request(request)
    user = await get_supabase_user(token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid token")
    return {
        "id": user.get("id"),
        "email": user.get("email"),
        "user_metadata": user.get("user_metadata", {})
    }


@app.get("/api/history")
async def list_history(request: Request):
    token = get_token_from_request(request)
    user = await get_supabase_user(token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid token")
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(
                f"{SUPABASE_URL}/rest/v1/chat_sessions?user_id=eq.{user.get('id')}&select=*&order=updated_at.desc",
                headers={
                    "apikey": SUPABASE_KEY,
                    "Authorization": f"Bearer {token}"
                }
            )
            if resp.status_code != 200:
                log.error(f"Supabase GET history error {resp.status_code}: {resp.text}")
                raise HTTPException(status_code=resp.status_code, detail=resp.text)
            return resp.json()
        except Exception as e:
            log.error(f"Error fetching history: {e}")
            raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/history")
async def create_history(request: Request):
    token = get_token_from_request(request)
    user = await get_supabase_user(token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid token")
    
    try:
        body = await request.json()
    except Exception:
        body = {}
        
    title = body.get("title", "New Chat")
    messages = body.get("messages", [])
    
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(
                f"{SUPABASE_URL}/rest/v1/chat_sessions",
                headers={
                    "apikey": SUPABASE_KEY,
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "Prefer": "return=representation"
                },
                json={
                    "user_id": user.get("id"),
                    "title": title,
                    "messages": messages
                }
            )
            if resp.status_code not in (200, 201):
                log.error(f"Supabase POST history error {resp.status_code}: {resp.text}")
                raise HTTPException(status_code=resp.status_code, detail=resp.text)
            res = resp.json()
            return res[0] if isinstance(res, list) and len(res) > 0 else res
        except Exception as e:
            log.error(f"Error creating history session: {e}")
            raise HTTPException(status_code=500, detail=str(e))


@app.put("/api/history/{session_id}")
async def update_history(session_id: str, request: Request):
    token = get_token_from_request(request)
    user = await get_supabase_user(token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid token")
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")
        
    update_data = {}
    if "title" in body:
        update_data["title"] = body["title"]
    if "messages" in body:
        update_data["messages"] = body["messages"]
    
    from datetime import datetime, timezone
    update_data["updated_at"] = datetime.now(timezone.utc).isoformat()
    
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.patch(
                f"{SUPABASE_URL}/rest/v1/chat_sessions?id=eq.{session_id}&user_id=eq.{user.get('id')}",
                headers={
                    "apikey": SUPABASE_KEY,
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "Prefer": "return=representation"
                },
                json=update_data
            )
            if resp.status_code not in (200, 201):
                log.error(f"Supabase PATCH history error {resp.status_code}: {resp.text}")
                raise HTTPException(status_code=resp.status_code, detail=resp.text)
            res = resp.json()
            return res[0] if isinstance(res, list) and len(res) > 0 else res
        except Exception as e:
            log.error(f"Error updating history session: {e}")
            raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/history")
async def delete_all_history(request: Request):
    token = get_token_from_request(request)
    user = await get_supabase_user(token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid token")
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.delete(
                f"{SUPABASE_URL}/rest/v1/chat_sessions?user_id=eq.{user.get('id')}",
                headers={
                    "apikey": SUPABASE_KEY,
                    "Authorization": f"Bearer {token}"
                }
            )
            if resp.status_code not in (200, 204):
                log.error(f"Supabase DELETE all history error {resp.status_code}: {resp.text}")
                raise HTTPException(status_code=resp.status_code, detail=resp.text)
            return {"status": "success"}
        except Exception as e:
            log.error(f"Error deleting all history: {e}")
            raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/history/{session_id}")
async def delete_history(session_id: str, request: Request):
    token = get_token_from_request(request)
    user = await get_supabase_user(token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid token")
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.delete(
                f"{SUPABASE_URL}/rest/v1/chat_sessions?id=eq.{session_id}&user_id=eq.{user.get('id')}",
                headers={
                    "apikey": SUPABASE_KEY,
                    "Authorization": f"Bearer {token}"
                }
            )
            if resp.status_code not in (200, 204):
                log.error(f"Supabase DELETE history error {resp.status_code}: {resp.text}")
                raise HTTPException(status_code=resp.status_code, detail=resp.text)
            return {"status": "success"}
        except Exception as e:
            log.error(f"Error deleting history session: {e}")
            raise HTTPException(status_code=500, detail=str(e))


# ================================================================
# HEALTH ENDPOINTS
# ================================================================


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "service": "Aether GraphRAG Research API",
        "version": "4.0.0",
        "ready": pool._ready,
        "neo4j": pool.neo4j_ok,
        "cache_sizes": {k: len(v) for k, v in CACHE.items()},
        "features": [
            "super-master-planning-brain",
            "paper-ranking-exact-substring-wordoverlap-recency",
            "mmr-diversity-reranking",
            "section-priority-chunks",
            "graph-relationship-narrative",
            "citation-network-traversal",
            "author-ego-network",
            "co-citation-clustering",
            "route-specific-prompts",
            "compare-timeline-survey-endpoints",
            "anti-hallucination-verification",
            "lru-caching-5-buckets",
        ],
    }


@app.get("/")
def root():
    from fastapi.responses import FileResponse
    return FileResponse("frontend/landing.html")


@app.get("/styles.css")
def read_styles():
    from fastapi.responses import FileResponse
    return FileResponse("frontend/styles.css")


@app.get("/api/config")
def get_config():
    return {
        "supabase_url": SUPABASE_URL,
        "supabase_anon_key": SUPABASE_KEY
    }


@app.get("/api/health/full")
async def full_health():
    checks: Dict[str, str] = {}
    try:
        await asyncio.to_thread(
            lambda: get_supabase_client()
            .table("papers")
            .select("id")
            .limit(1)
            .execute()
        )
        checks["supabase"] = "ok"
    except Exception as e:
        checks["supabase"] = f"error: {e}"
    try:
        await asyncio.to_thread(pool.neo4j.verify_connectivity)
        checks["neo4j"] = "ok"
    except Exception as e:
        checks["neo4j"] = f"error: {e}"
    try:
        await groq_chat(
            [{"role": "user", "content": "ping"}], REASON_MODEL, max_tokens=1
        )
        checks["groq"] = "ok"
    except Exception as e:
        checks["groq"] = f"error: {e}"
    try:
        await create_embedding("health check")
        checks["embedding"] = "ok"
    except Exception as e:
        checks["embedding"] = f"error: {e}"
    all_ok = all(v == "ok" for v in checks.values())
    return JSONResponse(
        status_code=200 if all_ok else 207,
        content={"status": "healthy" if all_ok else "degraded", "checks": checks},
    )


# ================================================================
# RESPONSE HELPERS
# ================================================================


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


def _direct_response(
    rid: str, answer: str, route: str, papers: List[Dict], t0: float
) -> Dict:
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


# ================================================================
# FRONTEND STATIC FILES
# ================================================================

_frontend_dir = Path("frontend")
if _frontend_dir.exists():
    app.mount(
        "/app", StaticFiles(directory=str(_frontend_dir), html=True), name="frontend"
    )
    log.info(f"Frontend at /app → {_frontend_dir}")
else:
    log.warning(f"No frontend dir at {_frontend_dir}")


# ================================================================
# ENTRYPOINT
# ================================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.app:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
        reload=os.getenv("ENV", "prod") == "dev",
        log_level="info",
        workers=int(os.getenv("WORKERS", "1")),
    )
