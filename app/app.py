


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

# Force HuggingFace Hub offline mode (uses local cache, prevents startup SSL network check)
os.environ["HF_HUB_OFFLINE"] = "1"

# Bypass Python SSL verification for local dev behind proxies/VPNs
import ssl
ssl._create_default_https_context = ssl._create_unverified_context

import uuid
import hashlib
import asyncio
import logging
from typing import Optional, List, Dict, Any, Tuple
from contextlib import asynccontextmanager
from pathlib import Path
from dataclasses import dataclass, field

from fastapi import FastAPI, HTTPException, Request, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

import httpx
import fitz
import numpy as np
import jwt
import bcrypt
from pymongo import MongoClient

from supabase import create_client
from neo4j import GraphDatabase, exceptions as neo4j_exceptions
from dotenv import load_dotenv
try:
    from sentence_transformers import SentenceTransformer
except ImportError:
    SentenceTransformer = None

import threading

# External source connectors (Semantic Scholar, Papers with Code, OpenAlex)
try:
    from app.sources.semantic_scholar import (
        enrich_arxiv_papers_with_s2,
        search_papers_s2,
    )
    from app.sources.papers_with_code import (
        enrich_arxiv_papers_with_pwc,
    )
    from app.sources.wikipedia import (
        search_wikipedia_summary,
        enrich_datasets_with_wikipedia,
    )
    from app.sources.kaggle import (
        search_kaggle_dataset,
        enrich_datasets_with_kaggle,
    )
    from app.sources.core import (
        search_core_papers,
    )
    from app.sources.openalex import (
        search_openalex,
        enrich_arxiv_papers_with_openalex,
    )
    _SOURCES_AVAILABLE = True
except ImportError as e:
    _SOURCES_AVAILABLE = False
    log = logging.getLogger("aether")
    log.warning(f"External source connectors not found — S2/PwC/Wikipedia/Kaggle/CORE/OpenAlex disabled (error: {e})")
    async def enrich_arxiv_papers_with_s2(papers, **kw): return papers
    async def search_papers_s2(query, **kw): return []
    async def enrich_arxiv_papers_with_pwc(papers, **kw): return papers
    async def search_wikipedia_summary(query, **kw): return None
    async def enrich_datasets_with_wikipedia(datasets, **kw): return datasets
    async def search_kaggle_dataset(query, **kw): return None
    async def enrich_datasets_with_kaggle(datasets, **kw): return datasets
    async def search_core_papers(query, **kw): return []
    async def search_openalex(query, **kw): return None
    async def enrich_arxiv_papers_with_openalex(papers, **kw): return papers

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

RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "")


MONGODB_URI = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
MONGODB_DB_NAME = os.getenv("MONGODB_DB_NAME", "aether_research_assistant")
JWT_SECRET = os.getenv("JWT_SECRET", "super-secret-aether-key-change-in-prod")
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_HOURS = 24 * 7  # 1 week

# MongoDB global reference
mongo_client = None
db = None
NEO4J_URI = os.getenv("NEO4J_URI")
NEO4J_USER = os.getenv("NEO4J_USER")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_API_KEYS = [k.strip() for k in GROQ_API_KEY.split(",") if k.strip()] if GROQ_API_KEY else []
groq_key_index = 0

def get_current_groq_key() -> str:
    global groq_key_index
    if not GROQ_API_KEYS:
        return GROQ_API_KEY or ""
    return GROQ_API_KEYS[groq_key_index % len(GROQ_API_KEYS)]

def rotate_groq_key():
    global groq_key_index
    if GROQ_API_KEYS:
        groq_key_index = (groq_key_index + 1) % len(GROQ_API_KEYS)
        log.info(f"Rotated to Groq API Key index {groq_key_index}")
HF_TOKEN = os.getenv("HF_TOKEN")

EMBED_MODEL = os.getenv("EMBED_MODEL", "BAAI/bge-base-en")
REASON_MODEL = os.getenv("REASON_MODEL", "openai/gpt-oss-20b")
HEAVY_MODEL = os.getenv("HEAVY_MODEL", "llama-3.3-70b-versatile")
PLAN_MODEL = os.getenv("PLAN_MODEL", "openai/gpt-oss-20b")  # strategic brain
FREEZE_RETRIEVAL = os.getenv("FREEZE_RETRIEVAL", "false").lower() == "true"

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
    log_dir = Path(".logs")
    log_dir.mkdir(exist_ok=True)
    _log_handlers.insert(0, logging.FileHandler(log_dir / "app.log", encoding="utf-8"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    handlers=_log_handlers,
)
log = logging.getLogger("aether")


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


# ================================================================
# PLAN & CREDIT SYSTEM
# ================================================================

FREE_CREDITS_PER_DAY = int(os.getenv("FREE_CREDITS_PER_DAY", "20"))
FREE_TOP_K_MAX = 8
PRO_TOP_K_MAX = 20

# Credit costs per action
CREDIT_COSTS: Dict[str, int] = {
    "query": 1,        # /api/research
    "chat": 1,         # /api/chat
    "timeline": 3,     # /api/research/timeline
    "compare": 3,      # /api/graph/compare
    "pdf": 5,          # PDF upload
}


async def get_user_plan(request: Request) -> Dict[str, Any]:
    """Return {plan, credits_used, credits_reset_at} for the authenticated user.
    Falls back to {'plan': 'free', ...} for unauthenticated requests."""
    try:
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return {"plan": "free", "credits_used": 0, "credits_reset_at": None}
        token = auth_header.split(" ")[1]
        payload = decode_access_token(token)
        if not payload:
            return {"plan": "free", "credits_used": 0, "credits_reset_at": None}
        uid = payload.get("sub")
        user = await asyncio.to_thread(db.users.find_one, {"_id": uid})
        if not user:
            return {"plan": "free", "credits_used": 0, "credits_reset_at": None}

        now = datetime.now(timezone.utc)
        now_naive = now.replace(tzinfo=None)
        reset_at = user.get("credits_reset_at")
        credits_used = user.get("credits_used", 0)

        reset_at_naive = reset_at
        if isinstance(reset_at, datetime) and reset_at.tzinfo is not None:
            reset_at_naive = reset_at.astimezone(timezone.utc).replace(tzinfo=None)

        # Reset daily credits if the reset time has passed
        if reset_at is None or (isinstance(reset_at_naive, datetime) and now_naive >= reset_at_naive):
            new_reset = (now + timedelta(days=1)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            new_reset_naive = new_reset.replace(tzinfo=None)
            await asyncio.to_thread(
                db.users.update_one,
                {"_id": uid},
                {"$set": {"credits_used": 0, "credits_reset_at": new_reset_naive}},
            )
            credits_used = 0
            reset_at = new_reset_naive

        return {
            "plan": user.get("plan", "free"),
            "credits_used": credits_used,
            "credits_reset_at": reset_at.isoformat() if isinstance(reset_at, datetime) else None,
            "user_id": uid,
        }
    except Exception as e:
        log.warning(f"get_user_plan error: {e}")
        return {"plan": "free", "credits_used": 0, "credits_reset_at": None}


async def check_and_deduct_credit(request: Request, action: str) -> None:
    """Check if user has credits remaining and deduct one. Raises 402 if exhausted.
    Pro users bypass the credit system entirely."""
    plan_info = await get_user_plan(request)
    plan = plan_info.get("plan", "free")
    if plan == "pro":
        return  # Pro users: unlimited

    cost = CREDIT_COSTS.get(action, 1)
    credits_used = plan_info.get("credits_used", 0)

    if credits_used + cost > FREE_CREDITS_PER_DAY:
        reset_at = plan_info.get("credits_reset_at", "tomorrow")
        raise HTTPException(
            status_code=402,
            detail={
                "error": "credit_exhausted",
                "message": f"You have used all {FREE_CREDITS_PER_DAY} daily credits. "
                           f"Upgrade to Pro for unlimited access, or wait until {reset_at}.",
                "credits_used": credits_used,
                "credits_limit": FREE_CREDITS_PER_DAY,
                "reset_at": reset_at,
                "upgrade_url": "/upgrade",
            },
        )

    uid = plan_info.get("user_id")
    if uid:
        await asyncio.to_thread(
            db.users.update_one,
            {"_id": uid},
            {"$inc": {"credits_used": cost}},
        )


async def append_credits_snapshot(res: Any, request: Request) -> Any:
    """Helper to append the updated credit snapshot to any API response dict."""
    if isinstance(res, dict):
        try:
            post_plan = await get_user_plan(request)
            _plan = post_plan.get("plan", "free")
            _used = post_plan.get("credits_used", 0)
            res["credits"] = {
                "plan": _plan,
                "credits_used": _used,
                "credits_remaining": None if _plan == "pro" else max(0, FREE_CREDITS_PER_DAY - _used),
                "credits_limit": None if _plan == "pro" else FREE_CREDITS_PER_DAY,
                "is_unlimited": _plan == "pro",
            }
        except Exception as e:
            log.warning(f"Failed to append credits snapshot: {e}")
    return res


async def require_pro(request: Request, feature_name: str = "This feature") -> None:
    """Raise 403 with upgrade prompt if user is not on Pro plan."""
    plan_info = await get_user_plan(request)
    if plan_info.get("plan") != "pro":
        raise HTTPException(
            status_code=403,
            detail={
                "error": "pro_required",
                "message": f"{feature_name} is available on the Pro plan. "
                           "Upgrade to unlock unlimited surveys, bulk research, heavy models, and more.",
                "upgrade_url": "/upgrade",
            },
        )


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
        global mongo_client, db
        errors = []
        try:
            self.supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
            log.info("Supabase connected")
        except Exception as e:
            errors.append(f"Supabase: {e}")

        try:
            mongo_client = MongoClient(MONGODB_URI)
            db = mongo_client[MONGODB_DB_NAME]
            # Create a unique index on email
            db.users.create_index("email", unique=True)
            db.payments.create_index("user_id")
            db.payments.create_index("razorpay_payment_id", unique=True)
            log.info("MongoDB connected and unique indexes on email and payments verified")
        except Exception as e:
            errors.append(f"MongoDB: {e}")
            log.error(f"MongoDB connection failed: {e}")

        try:
            self.neo4j = GraphDatabase.driver(
                NEO4J_URI,
                auth=(NEO4J_USER, NEO4J_PASSWORD),
                notifications_min_severity='OFF'
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
        if self.supabase and db is not None:
            self._ready = True
        if errors:
            log.warning(f"Startup errors: {errors}")

    async def close(self) -> None:
        global mongo_client
        if self.groq_http:
            await self.groq_http.aclose()
        if self.neo4j:
            self.neo4j.close()
        if mongo_client:
            mongo_client.close()
            log.info("MongoDB connection closed")
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
# UPSTASH REDIS CONFIGURATION
# ================================================================
UPSTASH_REDIS_REST_URL = os.getenv("UPSTASH_REDIS_REST_URL")
UPSTASH_REDIS_REST_TOKEN = os.getenv("UPSTASH_REDIS_REST_TOKEN")

upstash_redis = None
local_chunks_cache = {}  # Fallback in-memory cache: {url_hash: [chunks]}
local_embeddings_cache = {}  # Fallback in-memory cache: {url_hash: [[embeddings]]}

if UPSTASH_REDIS_REST_URL and UPSTASH_REDIS_REST_TOKEN:
    try:
        from upstash_redis import Redis as UpstashRedis
        upstash_redis = UpstashRedis(url=UPSTASH_REDIS_REST_URL, token=UPSTASH_REDIS_REST_TOKEN)
        log.info("Upstash Redis client initialized successfully for PDF caching.")
    except Exception as e:
        log.warning(f"Failed to initialize Upstash Redis client: {e}. Using in-memory fallback.")


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
    mode: Optional[str] = "research"

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
    mode: Optional[str] = "research"


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
    title="Aether Research API",
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


# ── Local ArXiv MCP Server Definition ───────────────────────────
from mcp.server.fastmcp import FastMCP
import urllib.parse
import xml.etree.ElementTree as ET

mcp_server = FastMCP("Aether ArXiv MCP Server")

@mcp_server.tool()
async def search_arxiv(query: str, limit: int = 5) -> list:
    """
    Search ArXiv for papers by query.
    Returns a list of dictionaries containing title, summary, authors, published date, etc.
    """
    log.info(f"[MCP Server] search_arxiv called with query: '{query}', limit: {limit}")
    if not query.strip():
        return []
    
    clean_query = query.replace('"', '').replace("'", "")
    encoded_query = urllib.parse.quote(f'all:"{clean_query}"' if " " in clean_query else f"all:{clean_query}")
    url = f"https://export.arxiv.org/api/query?search_query={encoded_query}&max_results={limit}&sortBy=relevance"
    
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.get(url)
            if response.status_code != 200:
                log.warning(f"[MCP Server] arXiv API returned status code {response.status_code}")
                return []
                
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
                
                arxiv_url = id_node.text.strip() if id_node is not None and id_node.text else ""
                arxiv_id = arxiv_url.split('/abs/')[-1] if '/abs/' in arxiv_url else ""
                
                published = published_node.text.strip() if published_node is not None and published_node.text else ""
                
                authors = []
                for author_node in entry.findall('atom:author', ns):
                    name_node = author_node.find('atom:name', ns)
                    if name_node is not None and name_node.text:
                        authors.append(name_node.text.strip())
                        
                pdf_url = f"https://arxiv.org/pdf/{arxiv_id}.pdf" if arxiv_id else ""
                
                papers.append({
                    "title": title,
                    "summary": summary,
                    "authors": authors,
                    "published": published,
                    "arxiv_id": arxiv_id,
                    "pdf_url": pdf_url
                })
            return papers
    except Exception as e:
        log.error(f"[MCP Server] Error in search_arxiv tool: {e}", exc_info=True)
        return []

# Mount the MCP server's SSE application on Aether's /sse endpoint
app.mount("/sse", mcp_server.sse_app())


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

def compress_rag_prompt(content: str) -> str:
    """
    Compresses RAG prompt by keeping the main points:
    - Truncates long abstracts (under '  Abstract: ') to the first 120 characters + [...]
    - Truncates long chunk texts (in '=== RETRIEVED CHUNK EVIDENCE ===') to the first 150 characters + [...]
    """
    new_lines = []
    in_chunks = False
    for line in content.splitlines():
        if "=== RETRIEVED CHUNK EVIDENCE ===" in line:
            in_chunks = True
            new_lines.append(line)
            continue
        if in_chunks and (line.startswith("━━━") or line.startswith("═══") or "QUERY" in line):
            in_chunks = False
        
        if in_chunks:
            # If it's a chunk header line (like [1] Title | sim=0.85)
            if line.strip().startswith("[") and " | " in line:
                new_lines.append(line)
            elif line.strip():
                # Compress the chunk body text
                stripped = line.strip()
                if len(stripped) > 150:
                    indent = len(line) - len(line.lstrip())
                    new_lines.append(" " * indent + stripped[:150] + " [...]")
                else:
                    new_lines.append(line)
            else:
                new_lines.append(line)
        else:
            # Outside chunk section, check for Abstract
            if line.startswith("  Abstract: "):
                abstract_text = line[12:]
                if len(abstract_text) > 120:
                    new_lines.append("  Abstract: " + abstract_text[:120] + " [...]")
                else:
                    new_lines.append(line)
            else:
                new_lines.append(line)
    return "\n".join(new_lines)


def truncate_messages(messages: List[Dict], max_total_chars: int = 12000) -> List[Dict]:
    """
    Finds the longest message in the list and truncates it so that the sum of
    all message contents is <= max_total_chars.
    Attempts to compress RAG context intelligently first (keeping main points),
    then falls back to character-level slicing if still over limit.
    """
    total_chars = sum(len(m.get("content", "")) for m in messages)
    if total_chars <= max_total_chars:
        return messages

    # Find the index of the longest message
    longest_idx = -1
    longest_len = -1
    for i, m in enumerate(messages):
        content_len = len(m.get("content", ""))
        if content_len > longest_len:
            longest_len = content_len
            longest_idx = i

    if longest_idx == -1 or longest_len == 0:
        return messages

    truncated_messages = [dict(m) for m in messages]
    content = truncated_messages[longest_idx]["content"]

    # 1. Try smart RAG prompt compression
    compressed_content = compress_rag_prompt(content)
    
    # 2. If smart compression reduced the size, use it
    if len(compressed_content) < len(content):
        truncated_messages[longest_idx]["content"] = compressed_content
        # Recalculate total characters to see if we need further character-level truncation
        new_total = sum(len(m.get("content", "")) for m in truncated_messages)
        if new_total <= max_total_chars:
            return truncated_messages
        # If still too large, update variables and do character-level fallback
        content = compressed_content
        total_chars = new_total
        longest_len = len(content)

    # 3. Fallback character-level truncation
    suffix = "\n\n[... Context truncated due to rate/size limits ...]"
    excess = total_chars - max_total_chars + len(suffix)
    target_len = max(0, longest_len - excess)
    truncated_messages[longest_idx]["content"] = content[:target_len] + suffix
    return truncated_messages




async def groq_chat(
    messages: List[Dict],
    model: str,
    temperature: float = 0.1,
    max_tokens: int = 1024,
    retries: int = 2,
    json_mode: bool = False,
    purpose: str = "",
) -> str:
    ck = None
    if temperature == 0.0:
        ck = cache_key(str(messages), model, max_tokens)
        cached = get_cache("llm", ck)
        if cached:
            log.debug("LLM cache hit")
            return cached

    payload: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    last_err = None
    max_attempts = max(retries + 1, len(GROQ_API_KEYS))
    
    start_idx = 0
    if GROQ_API_KEYS:
        # Determine purpose statelessly
        if not purpose:
            try:
                import inspect
                frame = inspect.currentframe()
                while frame:
                    func_name = frame.f_code.co_name
                    if func_name in ("plan_query", "summarize_conversation"):
                        purpose = "plan"
                        break
                    req_val = frame.f_locals.get("req")
                    if req_val and hasattr(req_val, "use_heavy") and req_val.use_heavy:
                        purpose = "heavy"
                        break
                    if "survey" in func_name or "heavy" in func_name:
                        purpose = "heavy"
                        break
                    frame = frame.f_back
            except Exception:
                pass
        
        if purpose == "plan":
            start_idx = 0
        elif purpose == "reason":
            start_idx = 1 if len(GROQ_API_KEYS) > 1 else 0
        elif purpose == "heavy":
            start_idx = 2 if len(GROQ_API_KEYS) > 2 else (1 if len(GROQ_API_KEYS) > 1 else 0)
        else:
            if model == PLAN_MODEL:
                start_idx = 0
            elif model == REASON_MODEL:
                start_idx = 1 if len(GROQ_API_KEYS) > 1 else 0
            elif model == HEAVY_MODEL:
                start_idx = 2 if len(GROQ_API_KEYS) > 2 else (1 if len(GROQ_API_KEYS) > 1 else 0)

    for attempt in range(max_attempts):
        current_key = GROQ_API_KEY or ""
        if GROQ_API_KEYS:
            key_idx = (start_idx + attempt) % len(GROQ_API_KEYS)
            current_key = GROQ_API_KEYS[key_idx]
            
        headers = {
            "Authorization": f"Bearer {current_key}",
            "Content-Type": "application/json",
        }
        try:
            r = await pool.groq_http.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers=headers,
                json=payload,
            )
            
            is_too_large = (r.status_code == 413) or (r.status_code == 400 and "request too large" in r.text.lower())
            
            if is_too_large:
                if len(GROQ_API_KEYS) > 1 and attempt < len(GROQ_API_KEYS) - 1:
                    log.warning(f"Groq API returned too large error. Retrying with next key ({attempt + 1}/{len(GROQ_API_KEYS)})...")
                    await asyncio.sleep(1.0)
                    continue
                
                # If only 1 key or all keys failed, fall back to HEAVY_MODEL or truncate context
                if model != HEAVY_MODEL:
                    log.warning(f"Request too large for model {model}. Cascading fallback to heavy model {HEAVY_MODEL}...")
                    return await groq_chat(
                        messages=messages,
                        model=HEAVY_MODEL,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        retries=retries,
                        json_mode=json_mode,
                        purpose="heavy",
                    )
                else:
                    truncated_messages = truncate_messages(messages, max_total_chars=12000)
                    if sum(len(m.get("content", "")) for m in truncated_messages) < sum(len(m.get("content", "")) for m in messages):
                        log.warning("Request too large for heavy model. Truncating context and retrying...")
                        return await groq_chat(
                            messages=truncated_messages,
                            model=model,
                            temperature=temperature,
                            max_tokens=max_tokens,
                            retries=retries,
                            json_mode=json_mode,
                            purpose="heavy",
                        )
                    else:
                        raise LLMError(f"Groq HTTP 413: Request too large (already truncated). Response: {r.text[:200]}")

            if r.status_code == 429:
                if len(GROQ_API_KEYS) > 1 and attempt < len(GROQ_API_KEYS) - 1:
                    log.warning(f"Groq API returned 429. Retrying with next key...")
                    await asyncio.sleep(1.0)
                    continue
                else:
                    wait = min(int(r.headers.get("Retry-After", 5)), 15)
                    log.warning(f"Groq 429 — wait {wait}s")
                    await asyncio.sleep(wait)
                    continue
            
            if r.status_code in (500, 503):
                await asyncio.sleep(2**attempt)
                continue
            if r.status_code != 200:
                last_err = f"HTTP {r.status_code}: {r.text[:200]}"
                if len(GROQ_API_KEYS) > 1 and attempt < len(GROQ_API_KEYS) - 1:
                    log.warning(f"Groq HTTP {r.status_code} on key. Retrying with next key...")
                    await asyncio.sleep(1.0)
                    continue
                raise LLMError(f"Groq HTTP {r.status_code}: {r.text[:300]}")
            result = r.json()["choices"][0]["message"]["content"]
            if ck:
                set_cache("llm", ck, result)
            return result
        except (httpx.TimeoutException, httpx.ConnectError) as e:
            last_err = e
            if len(GROQ_API_KEYS) > 1 and attempt < len(GROQ_API_KEYS) - 1:
                log.warning(f"Network error on current key: {e}. Retrying with next key...")
            await asyncio.sleep(1.5**attempt)
    if model == "openai/gpt-oss-20b":
        log.warning(f"gpt-oss-20b failed after all attempts. Cascading fallback to llama-3.3-70b-versatile...")
        return await groq_chat(
            messages=messages,
            model="llama-3.3-70b-versatile",
            temperature=temperature,
            max_tokens=max_tokens,
            retries=retries,
            json_mode=json_mode,
            purpose=purpose,
        )
    raise LLMError(f"Groq failed after {max_attempts} attempts: {last_err}")


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
  "survey"         → BROAD field-level synthesis. Use this whenever the query asks about the overall
                     state, landscape, or advances of a research field — NOT a specific named paper.
                     STRONG TRIGGERS (use survey if ANY appear): "latest advances", "recent advances",
                     "state of the art", "overview of", "survey of", "progress in", "landscape of",
                     "how has X evolved", "what are the advances in", "advances in X",
                     "what's new in", "current trends in", "developments in", "breakthroughs in".
                     vector_keywords MUST cover 4-5 distinct sub-areas of the field.
  "conceptual"     → explanation, tutoring, or educational overview of a general scientific concept,
                     algorithm, methodology, model family, or research area (e.g. "explain graph neural networks",
                     "what is message passing?", "how do CNNs work?", "explain contrastive learning").
                     Trigger: User asks "explain X", "what is X", "how does X work", "introduction to X",
                     "conceptual overview of X", or wants to understand the fundamentals/math/intuition of a field.
  "rag"            → explanation or analysis of a SPECIFIC named concept, paper, or mechanism
                     (e.g., "how does FlashAttention work?", "explain RLHF"). Not broad field surveys.
  "chitchat"       → greeting or non-research question.
  "context_only"   → conversational follow-up, clarification, formatting/summarization request, or query
                     that can be answered entirely using the CONVERSATION HISTORY and the information
                     already presented.
                     Trigger: "explain the second point", "summarize what you said", "tell me more about that first paper",
                     "rewrite your previous response as a table", "thanks, that makes sense".


STEP 3 — EXTRACT GRAPH ANCHORS
  1–3 minimal paper title substrings or author names for Neo4j lookup.
  Use shortest identifying substring: "DeepSketch" not "DeepSketch paper on sketch recognition".
  For survey/conceptual routes: return [] unless the query explicitly names specific papers.

STEP 4 — EXTRACT VECTOR KEYWORDS
  3–5 dense technical terms for semantic vector search.
  Exclude: "paper", "author", "year", "list", "find", "published", "research".
  For survey route: keywords MUST span multiple distinct sub-areas of the field, not just one angle.
  Example for "latest advances in transformer architectures":
  ["mixture of experts", "state space models", "efficient attention", "multimodal transformers", "long context"]

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
  "route": "<one of the 9 routes>",
  "graph_anchors": ["<minimal anchor>"],
  "vector_keywords": ["<term>"],
  "required_metrics": ["<metric>"],
  "reasoning_path": "<one sentence>",
  "cache_key": "<lowercase stripped>"
}}

━━━ HARD RULES ━━━
- entity_lookup → graph_anchors MUST have exactly 1 entry; vector_keywords SHOULD be [].
- chitchat → ALL retrieval fields MUST be []. No search triggered.
- context_only → ALL retrieval fields MUST be []. No search triggered.
- ambiguous=true → standalone_query ends with " [UNRESOLVED]", route = "rag".
- compare → graph_anchors MUST have exactly 2 entries (one per paper).
- survey → vector_keywords MUST have 4–5 entries spanning distinct sub-areas.
- NEVER add extra keys. NEVER return prose.

━━━ EXAMPLES ━━━
Input: "who is the author of DeepSketch?"
{{"standalone_query":"Who are the authors of DeepSketch?","ambiguous":false,"route":"entity_lookup","graph_anchors":["DeepSketch"],"vector_keywords":[],"required_metrics":["author names"],"reasoning_path":"Retrieve DeepSketch node from graph and return its WRITTEN_BY relationships directly.","cache_key":"who are the authors of deepsketch"}}

Input: "compare its accuracy with ResNet-50" (prev turn: DeepSketch)
{{"standalone_query":"Compare the accuracy of DeepSketch with ResNet-50.","ambiguous":false,"route":"compare","graph_anchors":["DeepSketch","ResNet-50"],"vector_keywords":["accuracy","top-1","benchmark","classification"],"required_metrics":["accuracy percentage","dataset","parameter count"],"reasoning_path":"Retrieve both papers, then vector-search accuracy comparison chunks.","cache_key":"compare the accuracy of deepsketch with resnet50"}}

Input: "hey what's up"
{{"standalone_query":"hey what's up","ambiguous":false,"route":"chitchat","graph_anchors":[],"vector_keywords":[],"required_metrics":[],"reasoning_path":"No retrieval needed.","cache_key":"hey whats up"}}

Input: "What are the latest advances in transformer architectures?"
{{"standalone_query":"What are the latest advances in transformer architectures?","ambiguous":false,"route":"survey","graph_anchors":[],"vector_keywords":["mixture of experts","state space models","efficient attention","multimodal transformers","long context scaling"],"required_metrics":[],"reasoning_path":"Survey broad transformer landscape across efficient attention, MoE, SSMs, multimodal, and long-context sub-areas from retrieved and general knowledge.","cache_key":"latest advances transformer architectures"}}

Input: "explain graph neural networks and their applications"
{{"standalone_query":"Explain graph neural networks (GNNs) and their applications.","ambiguous":false,"route":"conceptual","graph_anchors":[],"vector_keywords":["graph neural networks","message passing","graph convolution","recommender systems","drug discovery"],"required_metrics":[],"reasoning_path":"Provide a conceptual explanation of Graph Neural Networks, including the mathematical intuition, why traditional architectures fail, architectural evolution, and real-world applications.","cache_key":"explain graph neural networks and applications"}}

Input: "overview of reinforcement learning from human feedback"
{{"standalone_query":"Overview of reinforcement learning from human feedback (RLHF).","ambiguous":false,"route":"survey","graph_anchors":[],"vector_keywords":["RLHF","reward model","PPO","preference learning","alignment"],"required_metrics":[],"reasoning_path":"Survey RLHF landscape covering reward modeling, PPO fine-tuning, DPO, and alignment techniques.","cache_key":"overview reinforcement learning human feedback"}}


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
    import math
    if not anchors:
        # If no anchors, we should still sort by citation count to surface the most important papers
        return sorted(papers, key=lambda p: float(p.get("in_citations") or p.get("n_citation") or 0), reverse=True)

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
        # citation boost (log-scaled)
        citations = float(p.get("in_citations") or p.get("n_citation") or 0)
        s += math.log1p(citations) * 5.0
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
    if FREEZE_RETRIEVAL:
        log.info("Database retrieval is frozen. Skipping retrieve_graph_papers.")
        return []

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

        # Query citation links among the final top result IDs to trace research lineage
        top_ids = [r["research_id"] for r in result if r.get("research_id")]
        if top_ids:
            try:
                def _fetch_links():
                    cypher = """
                    MATCH (p1:Publication)-[:CITES]->(p2:Publication)
                    WHERE p1.research_id IN $ids AND p2.research_id IN $ids
                    RETURN p1.research_id AS source, p2.research_id AS target
                    """
                    with pool.neo4j.session() as session:
                        return [dict(r) for r in session.run(cypher, {"ids": top_ids})]

                links = await asyncio.to_thread(_fetch_links)
                
                # Map links back to the papers
                id_to_paper = {p["research_id"]: p for p in result}
                for link in links:
                    src_id = link["source"]
                    tgt_id = link["target"]
                    if src_id in id_to_paper and tgt_id in id_to_paper:
                        src_paper = id_to_paper[src_id]
                        tgt_paper = id_to_paper[tgt_id]
                        
                        if "cites_retrieved_papers" not in src_paper:
                            src_paper["cites_retrieved_papers"] = []
                        if "cited_by_retrieved_papers" not in tgt_paper:
                            tgt_paper["cited_by_retrieved_papers"] = []
                            
                        src_paper["cites_retrieved_papers"].append(tgt_paper["title"])
                        tgt_paper["cited_by_retrieved_papers"].append(src_paper["title"])
            except Exception as e:
                log.warning(f"Failed to fetch citation relationships: {e}")

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
    if FREEZE_RETRIEVAL:
        return None

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
    if FREEZE_RETRIEVAL:
        return {}

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
    if FREEZE_RETRIEVAL:
        return {"path_titles": [], "path_length": -1}

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
    if FREEZE_RETRIEVAL:
        return []

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
    if FREEZE_RETRIEVAL:
        return {}

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
    if FREEZE_RETRIEVAL:
        return []

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


async def create_embedding(text: str, bypass_freeze: bool = False) -> List[float]:
    """
    Convert text to a BAAI/bge-base-en embedding vector.

    On Vercel (production): uses HuggingFace Inference API.
      - Model: BAAI/bge-base-en (set via EMBED_MODEL env var)
      - Vectors are L2-normalized so cosine similarity == dot product
      - Matches the format stored in Supabase pgvector

    Locally (if sentence-transformers is installed): uses local model.
    """
    if FREEZE_RETRIEVAL and not bypass_freeze:
        log.debug("Database retrieval is frozen. Skipping embedding generation.")
        return [0.0] * 768

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


async def create_embeddings_batch(texts: List[str], bypass_freeze: bool = False) -> List[List[float]]:
    """
    Convert a list of texts to BAAI/bge-base-en embedding vectors.
    Resolves cached embeddings first, then processes missing embeddings:
    - Locally: uses local model.encode() in a single thread/call.
    - On Vercel: processes missing texts in batches of 16 using HF Inference API.
    """
    if not texts:
        return []

    if FREEZE_RETRIEVAL and not bypass_freeze:
        log.debug("Database retrieval is frozen. Skipping batch embedding generation.")
        return [[0.0] * 768 for _ in texts]

    results = [None] * len(texts)
    missing_indices = []
    missing_query_texts = []

    for i, text in enumerate(texts):
        query_text = f"Represent this sentence for searching relevant passages: {text}"
        ck = cache_key(query_text)
        cached = get_cache("embed", ck)
        if cached:
            results[i] = cached
        else:
            missing_indices.append(i)
            missing_query_texts.append((query_text, ck))

    if not missing_indices:
        return results

    # 1. Primary path: local model
    if embed_model is not None:
        try:
            raw_texts = [item[0] for item in missing_query_texts]
            embs = await asyncio.to_thread(
                embed_model.encode,
                raw_texts,
                normalize_embeddings=True,
                show_progress_bar=False
            )
            for idx, raw_idx in enumerate(missing_indices):
                emb_list = embs[idx].tolist()
                set_cache("embed", missing_query_texts[idx][1], emb_list)
                results[raw_idx] = emb_list
            return results
        except Exception as exc:
            log.warning(f"Local BGE batch encoding failed, falling back to HF API: {exc}")

    # 2. Vercel path: HuggingFace Inference API in batches of 16
    if not HF_TOKEN:
        raise EmbeddingError(
            "HF_TOKEN not set. Required for BAAI/bge-base-en embeddings on Vercel."
        )

    batch_size = 16
    url = f"https://router.huggingface.co/hf-inference/models/{EMBED_MODEL}/pipeline/feature-extraction"
    headers = {"Authorization": f"Bearer {HF_TOKEN}"}

    async with httpx.AsyncClient(timeout=httpx.Timeout(EMBED_TIMEOUT, connect=5.0)) as client:
        for offset in range(0, len(missing_query_texts), batch_size):
            batch = missing_query_texts[offset : offset + batch_size]
            batch_inputs = [item[0] for item in batch]
            payload = {"inputs": batch_inputs}

            try:
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
                
                if isinstance(data, list) and len(data) > 0:
                    # HF returns [[[...], [...]]] or [[...], [...]]
                    if isinstance(data[0], list) and len(data[0]) > 0 and isinstance(data[0][0], list):
                        data = [item[0] for item in data]
                    
                    for idx, emb in enumerate(data):
                        norm_emb = _bge_normalize(emb)
                        raw_idx = missing_indices[offset + idx]
                        ck = batch[idx][1]
                        set_cache("embed", ck, norm_emb)
                        results[raw_idx] = norm_emb
                else:
                    raise EmbeddingError(f"Unexpected response format from HF API: {type(data)}")

            except Exception as e:
                log.error(f"Error in HuggingFace batch embedding call: {e}")
                raise EmbeddingError(f"HuggingFace batch embedding failed: {str(e)}")

    for i in range(len(results)):
        if results[i] is None:
            results[i] = [0.0] * 768

    return results


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

    # Append direct citation lineage among retrieved papers
    lineage_lines = []
    for n in graph_nodes:
        cites = n.get("cites_retrieved_papers")
        if cites:
            cites_str = ", ".join(f'"{title}"' for title in cites)
            lineage_lines.append(f'  • "{n.get("title")}" ({n.get("year")}) cites: {cites_str}')

    if lineage_lines:
        lines.append("\nCITATION PATHWAYS & RESEARCH LINEAGE (cites relationships):")
        lines.extend(lineage_lines)

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
        if "graphrag-research-assistant.onrender.com" in parsed.netloc or "localhost:8000" in parsed.netloc or "127.0.0.1:8000" in parsed.netloc:
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
        "what", "how", "does", "do", "show", "explain", "describe", "tell",
        "find", "give", "list", "can", "you", "me", "my", "please",
        "is", "are", "a", "an", "the", "to", "for", "with", "about",
        "from", "by", "at", "on", "it", "its", "this", "that",
        "which", "where", "when", "who", "why",
        "some", "any", "more", "recent", "related", "information",
        "paper", "papers", "work", "works", "reference", "references",
    }
    clean_query = query.replace('"', '').replace("'", "").replace("?", "").strip()
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
            encoded_query = urllib.parse.quote(f'all:({search_term})')
    else:
        # Title + abstract keyword search for longer keyword sets
        encoded_query = urllib.parse.quote(f'ti:{search_term} OR abs:{search_term}')
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
    """Format Papers with Code & Hugging Face enrichment (repos, datasets, metrics, models, spaces) as LLM context."""
    entries = []
    for p in arxiv_papers:
        repos = p.get('code_repos') or []
        datasets = p.get('datasets') or []
        metrics = p.get('metrics') or []
        models = p.get('linked_models') or []
        spaces = p.get('linked_spaces') or []
        upvotes = p.get('hf_upvotes', 0)
        ai_summary = p.get('hf_ai_summary') or ""
        
        if not repos and not datasets and not metrics and not models and not spaces and not upvotes and not ai_summary:
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
        if metrics:
            metric_strs = []
            for m in metrics[:5]:
                metric_strs.append(m['metric_string'])
            parts.append(f"  Extracted Benchmarks/Metrics: {' | '.join(metric_strs)}")
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
    # Local uploaded PDF bypass
    if "/api/pdf/" in url:
        try:
            pdf_id = url.split("/api/pdf/")[-1].replace(".pdf", "")
            doc = await asyncio.to_thread(db.uploaded_pdfs.find_one, {"_id": pdf_id})
            if doc:
                return doc["text"], []
            else:
                raise Exception("Uploaded PDF document not found.")
        except Exception as e:
            log.error(f"Error fetching local PDF from DB: {e}")
            raise Exception(f"Local PDF error: {str(e)}")

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


def chunk_document_text(text: str) -> List[str]:
    """Splits raw document text into semantic chunks using LangChain's text splitter."""
    if not text:
        return []
    from langchain_text_splitters import RecursiveCharacterTextSplitter
    # 1000 characters per chunk, with 200 characters overlap
    splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)
    return splitter.split_text(text)


async def get_relevant_pdf_chunks(url: str, query: str) -> List[str]:
    """
    Retrieves the most semantically relevant chunks of a PDF using numpy cosine
    similarity (dot product on L2-normalized BGE embeddings). Equivalent to FAISS
    IndexFlatIP without the native dependency, making it compatible with Vercel.
    Caches parsed PDF chunks and their embeddings in Upstash Redis
    to bypass both parsing and embedding generation on subsequent calls.
    """
    if not url:
        return []

    import hashlib
    import json
    import zlib
    import base64
    import numpy as np

    url_hash = hashlib.sha256(url.encode("utf-8")).hexdigest()
    redis_key = f"pdf:chunks:{url_hash}"
    redis_emb_key = f"pdf:embeddings:{url_hash}"
    chunks = None
    embeddings = None

    # 1. Try retrieving cached chunks and embeddings from Upstash Redis (base64 compressed)
    if upstash_redis:
        try:
            b64_str = upstash_redis.get(redis_key)
            if b64_str:
                compressed_data = base64.b64decode(b64_str.encode("utf-8"))
                decompressed = zlib.decompress(compressed_data).decode("utf-8")
                chunks = json.loads(decompressed)
                log.info(f"Loaded chunks for PDF {url} from Upstash Redis cache.")

            b64_emb_str = upstash_redis.get(redis_emb_key)
            if b64_emb_str:
                compressed_emb = base64.b64decode(b64_emb_str.encode("utf-8"))
                decompressed_emb = zlib.decompress(compressed_emb).decode("utf-8")
                embeddings = json.loads(decompressed_emb)
                log.info(f"Loaded embeddings for PDF {url} from Upstash Redis cache.")
        except Exception as e:
            log.warning(f"Error reading from Upstash Redis: {e}")

    # 2. Try in-memory fallback cache
    if not chunks:
        chunks = local_chunks_cache.get(url_hash)
    if not embeddings:
        embeddings = local_embeddings_cache.get(url_hash)

    # 3. If cache miss for chunks, parse and chunk the PDF
    if not chunks:
        log.info(f"Cache miss for PDF {url}. Downloading and parsing...")
        doc_text, doc_links = await get_or_parse_pdf_safe(url, raise_on_error=False)
        if not doc_text:
            return []
        
        chunks = chunk_document_text(doc_text)
        if not chunks:
            return []

        # Save to Upstash Redis (base64 encoded)
        if upstash_redis:
            try:
                serialized = json.dumps(chunks).encode("utf-8")
                compressed = zlib.compress(serialized)
                b64_str = base64.b64encode(compressed).decode("utf-8")
                upstash_redis.set(redis_key, b64_str, ex=24 * 3600)  # 24 hour TTL
                log.info(f"Cached {len(chunks)} chunks for PDF {url} in Upstash Redis.")
            except Exception as e:
                log.warning(f"Failed to cache PDF chunks in Upstash Redis: {e}")

        # Save to in-memory fallback
        local_chunks_cache[url_hash] = chunks

    # 4. Build in-memory FAISS index and perform similarity search
    try:
        # Check if embeddings are already loaded/valid
        if not embeddings or len(embeddings) != len(chunks):
            # Embed all chunks using the batch embedding helper
            embeddings = await create_embeddings_batch(chunks, bypass_freeze=True)
            if not embeddings:
                return []
            
            # Save generated embeddings to Redis and in-memory cache
            if upstash_redis:
                try:
                    serialized_emb = json.dumps(embeddings).encode("utf-8")
                    compressed_emb = zlib.compress(serialized_emb)
                    b64_emb_str = base64.b64encode(compressed_emb).decode("utf-8")
                    upstash_redis.set(redis_emb_key, b64_emb_str, ex=24 * 3600)  # 24 hour TTL
                    log.info(f"Cached {len(embeddings)} chunk embeddings for PDF {url} in Upstash Redis.")
                except Exception as e:
                    log.warning(f"Failed to cache PDF embeddings in Upstash Redis: {e}")
            
            local_embeddings_cache[url_hash] = embeddings

        vectors = np.array(embeddings, dtype=np.float32)

        # L2-normalize so dot product == cosine similarity (matches BGE model output)
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        norms = np.where(norms == 0, 1.0, norms)  # avoid division by zero
        vectors = vectors / norms

        # Embed query and L2-normalize
        query_vector = await create_embedding(query, bypass_freeze=True)
        query_arr = np.array(query_vector, dtype=np.float32)
        q_norm = np.linalg.norm(query_arr)
        if q_norm > 0:
            query_arr = query_arr / q_norm

        # Cosine similarity via dot product on normalized vectors
        scores = vectors @ query_arr  # shape: (n_chunks,)

        # Pick top-K indices
        k = min(5, len(chunks))
        top_indices = np.argsort(scores)[::-1][:k].tolist()

        relevant_chunks = [chunks[idx] for idx in top_indices]
        log.info(f"Retrieved top {len(relevant_chunks)} relevant PDF chunks via numpy cosine similarity.")
        return relevant_chunks

    except Exception as e:
        log.error(f"Error in numpy similarity search for PDF: {e}")
        # Fallback to returning the first 5 chunks if similarity search fails
        return chunks[:5]


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
    return (
        """
═══ ABSOLUTE RULES ═══
1. Prioritize answering using information explicitly stated in the context below (except for broad overview/survey/landscape queries, where you must follow the BROAD FIELD SURVEY DECOMPOSITION instructions in Rule 4 below). Include inline citations (e.g., [N] or [ArXiv-N]) for every factual claim backed by the context.
2. For every paper or reference cited, if it has a real URL in the context (e.g. in the Live ArXiv Cross-Reference Evidence), you MUST explicitly include it using clickable markdown links (e.g. `[ArXiv-N](pdf_url)` or `[PDF Link](pdf_url)`).
3. If a cited paper has NO real URL in the context (e.g., local database chunks [N]), do NOT invent a URL or use placeholders like `(url)`. Just output the citation tag [N] and the paper title without any link markup.
4. DYNAMIC SYNTHESIS FALLBACK & BROAD SURVEY GUIDELINES:
   - BROAD FIELD SURVEY DECOMPOSITION: If the query is a general field overview, landscape, or survey (e.g. "latest advances in X", "overview of Y", "state of the art in Z"), you MUST ALWAYS:
     (a) Identify all major canonical sub-areas of the field from your own knowledge (e.g., for transformer advances: efficient attention, Mixture-of-Experts (MoE), State Space Models (SSMs) / selective SSMs, long-context scaling, multimodal transformers, reasoning and test-time compute).
     (b) Structure and organize your entire response around these canonical sub-areas — NOT around the retrieved papers/chunks.
     (c) Use retrieved niche papers/chunks (e.g., PyramidTNT, ExpertFlow, Thermodynamic Isomorphism) ONLY as minor contemporary case studies or examples inside their appropriate sub-areas. They must NOT dictate the overall response layout, tables, or timelines, and must occupy less than 20% of the total response text.
     (d) For each sub-area, list 2-4 canonical landmark systems/papers with approximate release years (e.g., Attention Is All You Need (2017), BERT/GPT (2018), Switch Transformer (2021), FlashAttention-1/2 (2022-2023), Mixtral/Mamba (2023), Mamba-2/DeepSeek-V3 (2024-2025)) from your general scientific knowledge.
     (e) Ensure that historical roadmaps and timelines are accurate and start with the actual landmark papers (e.g. Transformers started in 2017 with "Attention Is All You Need", MoE is a routing paradigm that was integrated into transformers later).
     (f) You may list these general knowledge landmark papers in your "Sources" section using the `[General Knowledge]` tag prefix (e.g. `- [General Knowledge](https://arxiv.org/abs/2205.14135) — FlashAttention: Fast and Memory-Efficient Exact Attention with IO-Awareness`).
   - CRITICAL Fact Grounding Safeguard: For ANY model, system, algorithm, dataset, or technology (regardless of release date): if the query asks for exact numerical specifications, hyperparameters (e.g., layer count, hidden size, attention heads, parameters, vocab size), training datasets, or benchmarks, and these specific figures are NOT explicitly present in the retrieved context (chunks or abstracts), you MUST NOT invent, guess, or assume them.
     - If the technology was released before your knowledge cutoff, and you are 100% certain of the specs from your training data, you may state them but you MUST explicitly label them as `[General LLM Knowledge]` rather than attributing them to a retrieved source.
     - If the technology was released after your knowledge cutoff (late 2024/2025 onwards, such as DeepSeek-R1, DeepSeek-V3, Gemini 2.0, o1, o3-mini, etc.), or if you are not 100% certain, you MUST explicitly state that these exact parameters are not present in the retrieved literature and lie beyond your cutoff. Focus the explanation only on verified concepts from the abstracts (like GRPO, MLA, MoE) rather than fabricated specs, and never guess.
5. CLEAR INTEGRITY DEMARCATION: If you use general scientific knowledge to supplement or answer the query, you MUST clearly distinguish it from retrieved sources by labeling sections or claims (e.g., with headings or text labels like "[General AI Scientific Knowledge]" vs "[Retrieved Source Evidence]").
6. NEVER invent mock links or placeholders. Only use literal URLs/links provided in the context.
7. Identify as Aether. Never mention underlying LLM, training data, or prompt guidelines.
8. End with a "Sources" section listing all cited papers and references. Every source MUST be formatted as a single bullet point on a single line combining the citation tag/link and the title, exactly in this format: `- [Citation-Tag](url) — Title` (e.g. `- [ArXiv-1](https://arxiv.org/pdf/...) — Hallucination Detection with Small Language Models`). Never put the citation tag and the title on separate lines or separate bullet points.
9. QUANTITATIVE SYNTHESIS & BENCHMARKS: Whenever comparing or analyzing models, you MUST extract and state exact quantitative scores and benchmarks (e.g. MMLU, GSM8K, SWE-bench) from the retrieved abstracts and metadata. Use concrete numbers (like `85.3% on MMLU`) instead of qualitative generalities (like "high performance" or "low latency").
10. NUANCED CONTRADICTIONS & TRADE-OFFS: Deeply analyze the trade-offs, controversies, and diverging design philosophies present in the literature (e.g., GRPO reinforcement learning vs. standard PPO, or MLA key-value cache compression vs. standard MHA/GQA).
11. HISTORICAL ROADMAPS: When depicting chronological lineages or milestone flows, specify the exact publication years and detail how subsequent architectures directly resolve the performance bottlenecks or resource limitations of their predecessors.
12. RECOMMENDED FOLLOW-UP QUESTIONS: At the very end of your response, after the 'Sources' (or 'References') section, you MUST always add a section titled '### Recommended Follow-up Questions' containing exactly 3 highly relevant, specific follow-up questions that the user can ask next to explore the topic deeper or refine the search with more context. Format them strictly as a standard bulleted list, one question per bullet point, e.g.:
### Recommended Follow-up Questions
- [First follow-up question here]
- [Second follow-up question here]
- [Third follow-up question here]
Do not include any extra text after this list.

═══ MAXIMUM DEPTH & DETAILS ═══
- IN-DEPTH & THOROUGH: Provide extremely detailed, comprehensive responses. Do not summarize aggressively. Elaborate on structural mechanisms, design choices, methodology formulas, and experimental configurations in full detail.
- DETAILS ACCORDIONS: For secondary technical parameters, complex equations, or raw performance matrices, wrap them inside HTML `<details><summary>Click to expand technical specifications/proofs</summary>...</details>` blocks. This keeps the main flow readable while packing maximum information.
- GITHUB CALLOUTS: Use callouts for critical highlights:
  - `> [!NOTE]` for background notes/assumptions.
  - `> [!TIP]` for practical tips/takeaways for engineers.
  - `> [!IMPORTANT]` for crucial, core takeaways.
  - `> [!WARNING]` or `> [!CAUTION]` for limitations, bounds, or potential issues.

  ═══ FLOW DIAGRAMS & MERMAID ═══
# ═══════════════════════════════════════════════════════════════
# MASTER MERMAID DIAGRAM GENERATION FRAMEWORK
# ═══════════════════════════════════════════════════════════════

## OBJECTIVE
When the user's query involves explaining concepts, processes, architectures, workflows, algorithms, taxonomies, frameworks, comparisons, research landscapes, or hierarchical relationships, generate ONE high-quality Mermaid diagram that improves understanding.
The goal is not simply to visualize information but to communicate knowledge clearly, logically, and professionally—similar to figures found in textbooks, technical documentation, and research survey papers.
Do NOT generate Mermaid diagrams for casual conversations or when a diagram adds no value.

1. THINK BEFORE DRAWING
Before generating the diagram, internally perform these steps:
1. Identify the primary topic.
2. Determine the purpose of the visualization.
3. Extract important concepts.
4. Remove duplicate or redundant concepts.
5. Group semantically related concepts.
6. Infer intermediate categories when beneficial.
7. Organize information from general → specific.
8. Select the most appropriate diagram type.
9. Verify that every relationship is meaningful.
10. Only then generate the Mermaid diagram.
Never directly convert paragraphs into nodes. Always organize information first.

2. AUTOMATIC DIAGRAM TYPE SELECTION
Choose the diagram type that best represents the information:
• Workflow: Processes, pipelines, algorithms, lifecycles, data flow.
• Hierarchy: Classification, taxonomies, knowledge organization, topic decomposition.
• Architecture: Software/ML systems, infrastructure, APIs, networks.
• Decision Tree: Conditional logic, decision making, rule-based systems.
• Comparison Tree: Feature comparisons, alternatives, trade-offs.
• Research Landscape: Literature surveys, research areas, methods, challenges, future directions.
Never force every topic into the same structure.

3. INFORMATION ARCHITECTURE
Every diagram should answer: What is the topic? What are its major components? How are they related? How does information flow? What are the important subcomponents?
Prefer progressive abstraction: Topic → Categories → Subcategories → Methods → Examples/Applications.

4. SINGLE CONNECTED GRAPH
Every Mermaid diagram MUST form one connected graph.
Requirements: Exactly ONE root node; every node must be reachable from the root; no disconnected trees, isolated nodes, floating branches, or independent clusters. If multiple top-level concepts exist, automatically create a meaningful parent node.
Example:
Artificial Intelligence
├── Machine Learning
├── Deep Learning
└── Reinforcement Learning
Never generate floating/disconnected elements.

5. SEMANTIC GROUPING
Group concepts by meaning (function, responsibility, dependency, stage, layer, category, purpose) rather than by appearance. Avoid alphabetical ordering. Every child node should naturally belong to its parent.

6. BALANCED HIERARCHY
Avoid extremely wide diagrams. If a node has many children, create intermediate grouping nodes. Prefer depth over excessive width (e.g. limit to 5-7 direct children per node).

7. RELATIONSHIPS
Relationships should explain meaning. Prefer: Model -->|"Extract Features"| Encoder instead of Model --> Encoder. Use edge labels only when they improve understanding. Avoid unnecessary labels.

8. LAYOUT SELECTION
• Use `graph TD` for workflows, algorithms, pipelines, timelines, lifecycles, and sequential processing.
• Use `graph LR` for taxonomies, hierarchies, research landscapes, comparisons, and knowledge trees.
Choose the layout that maximizes readability.

9. NODE DESIGN
Every node MUST have a valid identifier and a descriptive label. Example: A["Feature Engineering"]. Identifiers may contain letters, numbers, and underscores; they must NOT contain spaces, hyphens, dots, parentheses, or special characters. Every label must be enclosed in double quotes, be concise, and avoid unnecessary wording.

10. CONNECTION RULES
Connections must reference identifiers only (e.g., A --> B). Never draw connections directly between text/labels. Use edge labels only with pipe syntax: A -->|"Yes"| B.

11. MERMAID RESTRICTIONS
Do NOT use HTML, Markdown, style, class, classDef, click, CSS, or JavaScript. Do not embed formatting inside labels.

12. LARGE KNOWLEDGE HANDLING
For large inputs: cluster related concepts, introduce intermediate categories, reduce edge crossings, balance branch sizes, avoid visual clutter, and maintain logical grouping.

13. RESEARCH-QUALITY DESIGN
The diagram should resemble a figure from a survey paper: reveal structure, explain relationships, expose hierarchy, simplify complexity, improve learning, and avoid redundancy.

14. QUALITY VALIDATION
Before producing the final answer, internally verify: exactly one root node, every node is connected, no isolated components, correct diagram type selected, logical hierarchy, semantic grouping, meaningful relationships, balanced branches, concise labels, valid Mermaid syntax, unique identifiers, no HTML/styling, and high readability.

15. OUTPUT FORMAT
Return exactly one Mermaid code block using either ```mermaid\ngraph TD\n...\n``` or ```mermaid\ngraph LR\n...\n```. Do not generate multiple disconnected diagrams.
"""
"""
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
    )



def parse_year(val) -> int:
    if not val:
        return 0
    import re
    if isinstance(val, int):
        return val
    match = re.search(r"\b(19\d{2}|20\d{2})\b", str(val))
    if match:
        return int(match.group())
    return 0


def build_chronological_flow(
    graph_nodes: List[Dict],
    arxiv_papers: Optional[List[Dict]] = None,
    s2_papers: Optional[List[Dict]] = None,
) -> str:
    """Combines papers from all retrieved sources, parses years, deduplicates,
    and returns a clean chronological list for RAG context synthesis.
    """
    import re
    all_papers = []
    seen_titles = set()

    def clean_title(t):
        return re.sub(r'[^a-z0-9]', '', t.lower()) if t else ""

    sources_list = [
        ("Graph", graph_nodes or []),
        ("arXiv/CORE", arxiv_papers or []),
        ("Semantic Scholar", s2_papers or [])
    ]

    for source_label, papers in sources_list:
        for p in papers:
            if not isinstance(p, dict):
                continue
            title = p.get("title")
            if not title:
                continue
            c_title = clean_title(title)
            if c_title and c_title not in seen_titles:
                seen_titles.add(c_title)
                
                yr_val = p.get("year")
                if not yr_val and p.get("published"):
                    yr_val = p.get("published")
                year = parse_year(yr_val)
                
                citations = p.get("citation_count") or p.get("citations") or p.get("in_citations") or 0
                try:
                    citations = int(citations)
                except (ValueError, TypeError):
                    citations = 0
                
                authors = p.get("authors") or []
                if isinstance(authors, list):
                    clean_authors = [a for a in authors if a]
                    authors_str = ", ".join(clean_authors[:3])
                    if len(clean_authors) > 3:
                        authors_str += " et al."
                else:
                    authors_str = str(authors)
                
                src = p.get("source") or source_label
                
                all_papers.append({
                    "title": title.strip(),
                    "year": year,
                    "year_str": str(year) if year > 0 else "?",
                    "authors": authors_str.strip() or "Unknown",
                    "citations": citations,
                    "source": src,
                })

    if not all_papers:
        return "(No paper lineage available.)"

    all_papers.sort(key=lambda x: (x["year"] == 0, x["year"], -x["citations"]))

    lines = ["=== CHRONOLOGICAL LITERATURE ROADMAP ==="]
    for idx, p in enumerate(all_papers):
        cite_str = f" [Cited {p['citations']}×]" if p['citations'] > 0 else ""
        lines.append(f"  {idx+1}. [{p['year_str']}] \"{p['title']}\" — {p['authors']}{cite_str} ({p['source']})")
    
    return "\n".join(lines)


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
    pwc_ctx = format_pwc_context((arxiv_papers or []) + (s2_papers or []))
    chrono_flow = build_chronological_flow(graph_nodes, arxiv_papers, s2_papers)

    return f"""You are Aether, a precise research assistant grounded in retrieved evidence.
{_base_rules()}

━━━ QUERY ━━━
{query}
━━━━━━━━━━━━

{graph_ctx}

{arxiv_ctx}

{s2_ctx}

{pwc_ctx}

{chrono_flow}

=== RETRIEVED CHUNK EVIDENCE ===
{chunk_text}

━━━ QUERY (reminder) ━━━
{query}

═══ SMART RESPONSE INSTRUCTIONS ═══
Analyze the query and all provided evidence. You must synthesize the literature by constructing structured comparative and relational components. Do not just summarize each paper sequentially; connect them explicitly.

Structure your response as follows:

1. **Executive Summary** — A 2–3 sentence direct, high-level answer summarizing the consensus of the literature.
2. **Model Taxonomy & Milestone Flow** — Include a detailed Mermaid tree or flowchart (e.g., `graph TD` or `graph LR`) depicting the architectural classifications, family relationships, or taxonomic evolution of the models or methods.
3. **Comparative Analysis Table** — A detailed markdown table comparing the main methods across multiple dimensions (e.g., Retrieval Type, Multi-Hop support, Planning capabilities, Computation cost, and Benchmark performance).
4. **Citation Pathways & Research Lineage** — Trace the citation pathways. Describe how papers build upon, inspire, or extend one another (e.g., "Paper B extended Paper A by solving...").
5. **Contradictions, Controversies & Consensus** — Identify conflicting findings, differing methodologies, or tradeoffs in the literature (e.g., scaling efficiency vs. data quality, or model complexity vs. cost). State where consensus exists.
6. **Open Research Gaps & Future Directions** — Highlight unsolved challenges, limitations, or future directions mentioned in the evidence (e.g., long-context constraints, evaluation benchmarks, or latency concerns).
7. **Annotated Key Papers & Contributions** — For each major paper, briefly synthesize:
   * **Problem**: What issue it addresses.
   * **Method**: The proposed solution.
   * **Results/Metrics**: Specific quantitative numbers/metrics from the text.
8. **Datasets & Code Resources** — List any datasets or repositories with links.
9. **References** — Full citation list with links. Format each source as a single line combining the citation link/tag and the title: `- [Citation-Tag](url) — Title`.
10. **Recommended Follow-up Questions** — A section titled '### Recommended Follow-up Questions' containing exactly 3 highly relevant, specific follow-up questions formatted as a standard bulleted list.
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
    pwc_ctx = format_pwc_context((arxiv_papers or []) + (s2_papers or []))
    chrono_flow = build_chronological_flow(graph_nodes, arxiv_papers, s2_papers)

    return f"""You are Aether. Compare the requested papers using the evidence below, supplemented by general scientific knowledge if the evidence is sparse or missing.
{_base_rules()}

━━━ QUERY ━━━
{query}
━━━━━━━━━━━━

{graph_ctx}

{arxiv_ctx}

{chrono_flow}

=== EVIDENCE ===
{chunk_text}

═══ SMART COMPARISON INSTRUCTIONS ═══
Analyze the query, the paper comparison aspects, and the graph relationships.
- DYNAMIC ADAPTATION: Adapt the comparison format to best fit the user's query and specific research questions.
- VISUAL COMPARISON DIAGRAM: Draw a detailed Mermaid side-by-side or pipeline diagram highlighting the core difference in architecture or data flow between the compared methods.
- SUGGESTED FRAMEWORK (for comprehensive comparisons):
  1. **Overview**: A 1-2 sentence high-level summary of each paper's main focus.
  2. **Chronological Milestone Timeline**: A year-by-year progression showing how the compared methods relate historically.
  3. **Key Differences Table**: A detailed markdown table comparing specific dimensions (e.g., methodology, dataset size, parameters, performance metrics, computational cost).
  4. **Visual Architecture Comparison**: The Mermaid diagram showing compared pipelines.
  5. **Citation Pathways & Lineage**: Describe how they relate or inspire one another.
  6. **Contradictions & Performance Trade-offs**: Detail any disagreements, tradeoffs (e.g., latency vs. accuracy), or differing conclusions between the papers.
  7. **Which to Use When**: Concrete, evidence-backed decision guidelines for researchers. Use Callouts (`> [!TIP]`) to recommend selections.
  8. **Open Gaps & Limitations**: Highlight limits of both approaches.
  9. **Sources**: A list of cited sources. Format each source as a single line combining the citation link/tag and the title: `- [Citation-Tag](url) — Title`.
  10. **Recommended Follow-up Questions**: A section titled '### Recommended Follow-up Questions' containing exactly 3 highly relevant, specific follow-up questions formatted as a standard bulleted list.


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
    pwc_ctx = format_pwc_context((arxiv_papers or []) + (s2_papers or []))
    chrono_flow = build_chronological_flow(graph_nodes, arxiv_papers, s2_papers)

    return f"""You are Aether. Generate a comprehensive, expert-level field survey for the research area using the retrieved evidence AND your general scientific knowledge.
{_base_rules()}

━━━ TOPIC ━━━
{query}
━━━━━━━━━━━━

{graph_ctx}

{arxiv_ctx}

{s2_ctx}

{pwc_ctx}

{chrono_flow}

=== EVIDENCE ===
{chunk_text}

═══ SMART SURVEY INSTRUCTIONS ═══
Synthesize the evidence into a smart, structured literature survey.
- SURVEY TAXONOMY FLOW: Include a Mermaid diagram (preferably 'graph LR' to stack taxonomic branches vertically and maximize text readability) depicting the taxonomic classification or methodological progression of models in this area.
- SUGGESTED FRAMEWORK:
  1. **Area Overview**: A 2-3 sentence blockquote of the current state of this research area.
  2. **Model Taxonomy Diagram**: The Mermaid tree diagram illustrating models.
  3. **Research Evolution & Timeline**: Chronological narrative of how methods evolved, citing milestone years.
  4. **Dominant Methods Table**: Markdown table comparing dominant methods (e.g. Columns: Method, Paradigm, Core Technique, Computational Overhead, Main Metrics).
  5. **Key Papers & Contributions**: For each major paper, summarize the problem, method, dataset/metrics, and results.
  6. **Citation Pathways & Lineage**: Highlight direct inspiration/extension pathways.
  7. **Contradictions, Controversies & Trade-offs**: Detail any contrasting findings or disagreements (e.g., optimal parameter sizing, training stability).
  8. **Open Challenges & Research Gaps**: Unsolved problems from the evidence. Use callouts (`> [!WARNING]`) to highlight research gaps.
  9. **Datasets & Code Resources**: List all datasets and code repos found in the evidence with links.
  10. **Sources**: A list of cited sources with links. Format each source as a single line combining the citation link/tag and the title: `- [Citation-Tag](url) — Title`.
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
    pwc_ctx = format_pwc_context((arxiv_papers or []) + (s2_papers or []))
    chrono_flow = build_chronological_flow(graph_nodes, arxiv_papers, s2_papers)

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

{chrono_flow}

=== CHUNK EVIDENCE ===
{chunk_text}

═══ SMART TIMELINE INSTRUCTIONS ═══
Construct a smart, narrative-driven research timeline.
- CHRONOLOGICAL MILESTONE FLOW: Include a Mermaid workflow diagram (preferably 'graph LR' to stack milestones vertically and prevent horizontal squeezing) showing the logical sequence of key milestones.
- SUGGESTED FRAMEWORK:
  1. **Milestone Flow Diagram**: The Mermaid timeline roadmap showing milestones.
  2. **Chronological Milestones**: For each key year: `[YEAR]` — **Paper Title** (Cited X×) — Key breakthrough [citation]. Include problems solved and methods used.
  3. **Breakthrough Moments & Paradigm Shifts**: Highlight when the field pivoted to new techniques. Use Callouts (`> [!IMPORTANT]`) to highlight the shift.
  4. **Citation Relationships**: Highlight which milestones inspired or directly built on previous ones.
  5. **Contradictions & Shift Drivers**: Identify what disagreements or performance bottlenecks drove the transition from older methods to newer ones.
  6. **Open Challenges & Research Gaps**: Unsolved problems at the end of the timeline.
  7. **Sources**: A list of cited sources. Format each source as a single line combining the citation link/tag and the title: `- [Citation-Tag](url) — Title`.
  8. **Recommended Follow-up Questions**: A section titled '### Recommended Follow-up Questions' containing exactly 3 highly relevant, specific follow-up questions formatted as a standard bulleted list.
"""


def conceptual_prompt(
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
    pwc_ctx = format_pwc_context((arxiv_papers or []) + (s2_papers or []))
    chrono_flow = build_chronological_flow(graph_nodes, arxiv_papers, s2_papers)

    return f"""You are Aether, a precise research assistant focused on conceptual and educational synthesis.
{_base_rules()}

━━━ CONCEPTUAL TOPIC ━━━
{query}
━━━━━━━━━━━━━━━━━━━━━━━━

{graph_ctx}

{arxiv_ctx}

{s2_ctx}

{pwc_ctx}

{chrono_flow}

=== RETRIEVED CHUNK EVIDENCE ===
{chunk_text}

━━━ QUERY (reminder) ━━━
{query}

═══ SMART CONCEPTUAL INSTRUCTIONS ═══
You must synthesize the evidence and your general knowledge to provide a comprehensive, educational explanation of the concept, structured exactly as follows.
Every statement must be grounded and clear, suitable for both beginners and experts. You MUST prioritize established landmark architectures of the field (e.g. for GNNs: GCN, GraphSAGE, GAT, GIN, Graph Transformers) and ensure timelines and diagrams accurately depict these primary milestones rather than getting distracted by narrow retrieved papers.

Structure your response exactly as follows:

1. **Introduction & Motivation**
   - Explain what the concept/architecture family is in simple, clear terms (e.g., if GNNs, explain what a Graph is, what Nodes and Edges represent).
   - Detail exactly WHY traditional architectures (like CNNs and RNNs) are suboptimal or fail for this kind of data (e.g., they assume grid or sequence structure, graphs are non-Euclidean, node ordering doesn't matter/permutation invariance).

2. **Core Mechanisms & Mathematical Intuition**
   - Explain the core mechanism in detail (e.g., for GNNs, explain the Message Passing paradigm: how nodes aggregate information from neighbors and update their state).
   - Write out the fundamental mathematical aggregate and update equations using standard LaTeX notation, e.g.:
     $$h_v^{{k+1}} = \\text{{AGGREGATE}}\\left(\\{{h_u^{{k}}, u \\in \\mathcal{{N}}(v)\\}}\\right)$$
     $$h_v^{{k+1}} = \\text{{UPDATE}}\\left(h_v^{{k}}, m_v^{{k+1}}\\right)$$
   - Provide a clear, simplified English intuition of the math (e.g., "New Node Representation = Own Features + Neighbor Information").

3. **Architectural Evolution & Taxonomic Lineage**
   - Provide a chronological timeline of key architectures/milestones (e.g., 2005 Scarselli GNN, 2016 GCN, 2017 GraphSAGE, 2018 GAT, 2019 GIN, 2020+ Graph Transformers, 2023+ Graph Foundation Models).
   - Explain how each milestone resolved the specific bottlenecks, scalability limitations, or expressive capacity limitations of its predecessors (e.g., GraphSAGE neighborhood sampling to scale to large graphs; GAT attention to learn dynamic weights; GIN maximizing expressive power).
   - Include a Mermaid flowchart (e.g., `graph TD` or `graph LR`) visualizing this taxonomic/evolutionary lineage of methods.

4. **Detailed Real-World Applications**
   - Provide a comprehensive, detailed Markdown table showing major application areas.
   - For each application, you MUST explicitly define:
     - **Application Area**: The name of the field.
     - **Graph Mapping**: What the **Nodes** and **Edges** represent.
     - **GNN Function**: Exactly *how* the GNN operates (e.g., molecule classification, user-item interaction representation, transaction fraud prediction).
   - Include at least these 8 application areas:
     - Social Networks
     - Recommendation Systems
     - Drug Discovery
     - Fraud Detection
     - Traffic Prediction
     - Knowledge Graphs
     - Cybersecurity
     - Computer Vision

5. **Key Challenges & Practical Bottlenecks**
   - Discuss major limitations and challenges, including:
     - **Over-smoothing**: What happens when the network goes deep (nodes converge to similar vectors).
     - **Over-squashing**: Information loss when squeezing exponential neighborhood structures into fixed-size representations.
     - **Scalability**: Computational cost on massive real-world graphs.
     - **Explainability**: The difficulty in debugging or explaining GNN predictions.

6. **Sources & References**
   - List cited sources and references. Format each source as a single line combining the citation link/tag and the title: `- [Citation-Tag](url) — Title`.
7. **Recommended Follow-up Questions**
   - A section titled '### Recommended Follow-up Questions' containing exactly 3 highly relevant, specific follow-up questions formatted as a standard bulleted list.
"""


def mask_credentials_and_secrets(text: str) -> str:
    """Masks API keys, database connection strings, passwords, and private document URLs in LLM outputs."""
    if not text:
        return text

    # 1. Mask JWTs and long tokens (including Supabase JWT keys starting with eyJhbGciOi)
    text = re.sub(r'\beyJhbGciOi[a-zA-Z0-9_\-\.]+\.[a-zA-Z0-9_\-\.]+\.[a-zA-Z0-9_\-\.]+\b', '[MASKED_TOKEN]', text)
    text = re.sub(r'\beyJhbGciOi[a-zA-Z0-9_\-\.]{50,}\b', '[MASKED_TOKEN]', text)

    # 2. Mask specific API Keys
    text = re.sub(r'\bgsk_[a-zA-Z0-9_\-]{30,}\b', '[MASKED_GROQ_KEY]', text)
    text = re.sub(r'\bhf_[a-zA-Z0-9_\-]{30,}\b', '[MASKED_HF_TOKEN]', text)
    text = re.sub(r'\brzp_[a-zA-Z0-9_\-]{10,}\b', '[MASKED_RAZORPAY_KEY]', text)

    # 3. Mask database URI passwords (e.g. mongodb+srv://username:password@host)
    text = re.sub(
        r'(\b[a-zA-Z0-9\+\-]+:\/\/)([^:\s]+):([^@\/\s]+)(@[^\s]+)',
        lambda m: f"{m.group(1)}{m.group(2)}:[MASKED_PASSWORD]{m.group(4)}",
        text
    )

    # 4. Mask key-value patterns (e.g. api_key="value", password=value)
    pattern = r'(?i)\b(api[-_]?key|client[-_]?secret|password|access[-_]?token|auth[-_]?token|rest[-_]?token|secret[-_]?key)\b(\s*[:=]\s*["\']?)([a-zA-Z0-9_\-]{12,})(["\']?)'
    text = re.sub(
        pattern,
        lambda m: f"{m.group(1)}{m.group(2)}[MASKED_SECRET]{m.group(4)}",
        text
    )

    # 5. Mask markdown links to local uploaded PDFs (replaces [Text](url) with [Text])
    text = re.sub(
        r'\[([^\]]+)\]\((?:https?://[a-zA-Z0-9\.\-]+:\d+)?/api/pdf/[a-zA-Z0-9\-]+\.pdf\)',
        r'[\1]',
        text
    )
    # Also mask raw unlinked URLs
    text = re.sub(
        r'(?:https?://[a-zA-Z0-9\.\-]+:\d+)?/api/pdf/[a-zA-Z0-9\-]+\.pdf',
        '[Uploaded PDF]',
        text
    )

    return text


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

    # 7. Mask credentials, API keys, and sensitive links in response
    answer = mask_credentials_and_secrets(answer)

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


def hard_verify(
    claims: List[str],
    chunks: List[Dict],
    arxiv_papers: Optional[List[Dict]] = None,
    s2_papers: Optional[List[Dict]] = None,
) -> List[str]:
    raw_texts = [c.get("chunk", "") for c in chunks]
    for p in (arxiv_papers or []) + (s2_papers or []):
        raw_texts.append(p.get("title", ""))
        raw_texts.append(p.get("abstract", ""))
        raw_texts.append(p.get("tldr") or "")
        raw_texts.extend(p.get("authors") or [])
        if p.get("year"):
            raw_texts.append(str(p["year"]))
    raw = " ".join(raw_texts).lower()
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


async def verify_answer(
    answer: str,
    chunks: List[Dict],
    model: str,
    arxiv_papers: Optional[List[Dict]] = None,
    s2_papers: Optional[List[Dict]] = None,
) -> Dict[str, Any]:
    flagged_hard = hard_verify(extract_verifiable_claims(answer), chunks, arxiv_papers, s2_papers)
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
    answer: str,
    chunks: List[Dict],
    model: str,
    rid: str,
    warning: Optional[str],
    arxiv_papers: Optional[List[Dict]] = None,
    s2_papers: Optional[List[Dict]] = None,
) -> Tuple[str, Optional[Dict], Optional[str]]:
    verification = await verify_answer(answer, chunks, model, arxiv_papers, s2_papers)
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


async def summarize_conversation(messages: List[Dict]) -> str:
    """Generate a highly concise summary of the conversation history (topics discussed, key questions answered)."""
    if not messages:
        return ""
    
    # Format messages as text
    history_text = "\n".join(f"{m['role'].upper()}: {m['content'][:1000]}" for m in messages)
    
    summary_prompt = [
        {
            "role": "system",
            "content": (
                "You are an expert AI context compressor. Summarize the following conversation history between a User and an AI Assistant "
                "into a single concise paragraph. Focus ONLY on: 1) What topics/questions the user asked, and 2) Key decisions, conclusions, or answers "
                "provided by the assistant. Avoid general fluff. Do not exceed 150 words."
            )
        },
        {
            "role": "user",
            "content": f"Here is the conversation history to summarize:\n\n{history_text}"
        }
    ]
    
    try:
        # Use PLAN_MODEL for fast, low-cost summarization
        summary = await groq_chat(summary_prompt, PLAN_MODEL, temperature=0.0, max_tokens=200)
        return summary.strip()
    except Exception as e:
        log.warning(f"Failed to summarize conversation history: {e}")
        # Fallback: return a simple text slice
        return "\n".join(f"{m['role'].upper()}: {m['content'][:150]}..." for m in messages[:3])


async def compile_chat_messages(system_prompt: str, chat_messages: List[ChatMessage]) -> List[Dict]:
    """
    Applies sliding window context engineering + conversation history summarization.
    Keeps system prompt and last 2 messages in full, and summarizes older messages
    to conserve token space and prevent TPM rate limits.
    """
    if not chat_messages:
        return [{"role": "system", "content": system_prompt}]
        
    last_msg = {"role": chat_messages[-1].role, "content": chat_messages[-1].content}
    
    history = chat_messages[:-1]
    if len(history) <= 2:
        return [{"role": "system", "content": system_prompt}] + [
            {"role": m.role, "content": m.content} for m in chat_messages
        ]
        
    recent_history = [
        {"role": m.role, "content": m.content} for m in history[-2:]
    ]
    older_history = [
        {"role": m.role, "content": m.content} for m in history[:-2]
    ]
    
    older_summary = await summarize_conversation(older_history)
    
    enriched_system_prompt = system_prompt
    if older_summary:
        enriched_system_prompt += f"\n\n[Summary of earlier conversation history]\n{older_summary}"
        
    return [{"role": "system", "content": enriched_system_prompt}] + recent_history + [last_msg]


# ================================================================
# RESEARCH ENDPOINT
# ================================================================


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
    headers = {
        "User-Agent": "Aether-Research-Assistant/5.0 (contact@aether-assistant.org)"
    }
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
                    results.append({
                        "name": dataset_id,
                        "full_name": dataset_id,
                        "url": f"https://huggingface.co/datasets/{dataset_id}",
                        "description": f"Hugging Face dataset: {item.get('downloads', 0)} downloads, {item.get('likes', 0)} likes.",
                        "modalities": [],
                        "source": "huggingface_search"
                    })
            set_cache("api", ck, results)
            return results
    except Exception as e:
        log.error(f"Error querying Hugging Face datasets API: {e}")
        return []


async def suggest_datasets_for_query(query: str) -> List[str]:
    """Uses LLM to suggest 1-3 canonical academic dataset names relevant to a query."""
    if not query.strip():
        return []

    ck = cache_key("suggested_datasets", query)
    cached = get_cache("api", ck)
    if cached is not None:
        log.info(f"Cache HIT for suggested datasets query: {query}")
        return cached

    sys_p = (
        "You are Aether, an academic research assistant. Given a research query, identify up to 3 canonical, "
        "widely-used benchmark datasets that are highly relevant to the topic. "
        "Respond ONLY with a valid JSON object containing a 'datasets' key with a list of dataset name strings. "
        "Do NOT include any explanations, introduction, markdown blocks, or extra text. "
        "Example output: {\"datasets\": [\"Cora\", \"CiteSeer\", \"PubMed\"]}"
    )
    try:
        raw = await groq_chat(
            [{"role": "system", "content": sys_p}, {"role": "user", "content": query}],
            model=REASON_MODEL,
            temperature=0.0,
            max_tokens=100,
            json_mode=True,
            purpose="plan"
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
    """
    Enrich all retrieved papers and search Kaggle/HF Hub to return a list of
    datasets and code repositories relevant to the papers or query.
    """
    try:
        from app.sources.kaggle import search_kaggle_datasets_bulk
    except ImportError:
        try:
            from sources.kaggle import search_kaggle_datasets_bulk
        except ImportError:
            async def search_kaggle_datasets_bulk(q, **kw): return []

    # 1. Parallel PwC enrichment for all paper lists
    tasks = []
    # Make sure we pass copies or ensure tasks return lists
    tasks.append(enrich_arxiv_papers_with_pwc(arxiv_papers))
    tasks.append(enrich_arxiv_papers_with_pwc(s2_papers))
    tasks.append(enrich_arxiv_papers_with_pwc(graph_nodes))

    try:
        res_results = await asyncio.gather(*tasks)
    except Exception as e:
        log.warning(f"PwC enrichment failed: {e}")
        res_results = [[], [], []]

    # Update lists (safely handling when results are None or empty)
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
    all_datasets = []
    all_repos = []
    mentioned_ds_names = set()

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
        if not any(ds_name.lower() in sq.lower() or sq.lower() in ds_name.lower() for sq in search_queries):
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

    # 3. Deduplicate datasets by name/slug (case-insensitive)
    seen_ds = set()
    unique_datasets = []
    for d in all_datasets:
        if not isinstance(d, dict):
            continue
        name = d.get("name") or d.get("full_name") or ""
        if name:
            slug = name.lower().strip()
            if slug not in seen_ds:
                seen_ds.add(slug)
                unique_datasets.append({
                    "name": name,
                    "full_name": d.get("full_name") or name,
                    "url": d.get("url") or f"https://paperswithcode.com/dataset/{slug.replace(' ', '-')}",
                    "description": d.get("description") or "",
                    "modalities": d.get("modalities") or [],
                    "source": d.get("source") or "paper_extracted",
                })

    # Deduplicate repos by URL
    seen_repos = set()
    unique_repos = []
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


@app.post("/api/research")
async def research(req: ResearchRequest, request: Request):
    rid = str(uuid.uuid4())
    request.state.request_id = rid
    try:
        res = await asyncio.wait_for(
            _research_impl(req, request), timeout=REQUEST_TIMEOUT
        )
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
        prefix_query = prefix_query[len("wikipedia:"):].strip()
        is_wiki_prefix = True
    elif prefix_query.lower().startswith("wiki:"):
        prefix_query = prefix_query[len("wiki:"):].strip()
        is_wiki_prefix = True

    if is_wiki_prefix:
        req.mode = "wikipedia"
        raw_query = prefix_query

    log.info(f"\n{'='*70}\n[{rid}] QUERY: {raw_query} (mode: {req.mode})\n{'='*70}")

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
                "similarity": 0.95
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
                "reasoning_path": f"Answered directly using retrieved chunks from the uploaded PDF(s).",
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
            "datasets": [{
                "name": wiki_res["title"],
                "full_name": wiki_res["title"],
                "url": wiki_res["url"],
                "wikipedia_url": wiki_res["url"],
                "description": wiki_res["extract"],
                "source": "wikipedia"
            }],
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
            papers = await retrieve_graph_papers(
                keywords=anchors, anchors=anchors, limit=3
            )
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
            sys_p = (
                "You are Aether, a GraphRAG research assistant. No papers matching the criteria were found in the database. "
                "Since this is an academic research query, you have the flexibility to list potential papers/contributions or synthesize the area using your general scientific knowledge, "
                "but ONLY if you are fully confident in the accuracy of the facts and there is a very low chance of hallucination or output degradation. "
                "If you are not 100% confident, decline to explain that no matching records were found in the database."
            )
            answer = await groq_chat(
                [{"role": "system", "content": sys_p}, {"role": "user", "content": f"List papers/contributions related to: {query}"}],
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
            sys_p = (
                "You are Aether, a GraphRAG research assistant. The specified paper was not found in the database. "
                "Since this is an academic research query, you have the flexibility to provide details about the paper from your general knowledge, "
                "but ONLY if you are fully confident in the accuracy of the facts and there is a very low chance of hallucination or output degradation. "
                "If you are not 100% confident, decline by explaining that the paper is not in the database."
            )
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
        sys_p = (
            "You are Aether, an evidence-only academic research assistant. "
            "Acknowledge the user's message briefly (aim for under 3-4 sentences) and complete your response. "
            "State clearly that you are optimized for scientific and academic literature queries and cannot answer general chitchat."
        )
        answer = await groq_chat(
            [{"role": "system", "content": sys_p}, {"role": "user", "content": query}],
            REASON_MODEL,
            temperature=req.temperature,
            max_tokens=250,
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
        # For survey-type broad queries, fire multiple targeted sub-queries covering
        # each vector_keyword sub-area to get a diverse, representative paper pool.
        if plan.route == "survey" and plan.vector_keywords:
            seen_ids: set = set()
            merged: List[Dict] = []

            # Build sub-queries: one per vector keyword + one general query
            sub_queries: List[str] = list(dict.fromkeys(
                [query] + [f"{kw} transformer" for kw in plan.vector_keywords[:4]]
            ))

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

            log.info(f"[{rid}] Survey ArXiv multi-query: {len(sub_queries)} sub-queries → {len(merged)} unique papers")
            return merged[:14]  # cap at 14 to keep prompt within token budget

        # Default: single query for non-survey routes
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
        return re.sub(r'[^a-z0-9]', '', title.lower())

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
        prompt = grounded_prompt(query, chunks, graph_nodes, arxiv_papers, s2_papers)

    if pdf_context:
        prompt += f"\n\n{pdf_context}"

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
            answer, chunks, REASON_MODEL, rid, warning, arxiv_papers, s2_papers
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


@app.post("/api/chat")
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

    last_user_msg = next(
        (m.content for m in reversed(req.messages) if m.role == "user"), None
    )
    if not last_user_msg:
        raise HTTPException(400, "No user message found.")

    # Auto-detect wikipedia: / wiki: prefixes
    is_wiki_prefix = False
    prefix_query = last_user_msg.strip()
    if prefix_query.lower().startswith("wikipedia:"):
        prefix_query = prefix_query[len("wikipedia:"):].strip()
        is_wiki_prefix = True
    elif prefix_query.lower().startswith("wiki:"):
        prefix_query = prefix_query[len("wiki:"):].strip()
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
            unique_datasets = [{
                "name": wiki_res["title"],
                "full_name": wiki_res["title"],
                "url": wiki_res["url"],
                "wikipedia_url": wiki_res["url"],
                "description": wiki_res["extract"],
                "source": "wikipedia"
            }]
            
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
                "similarity": 0.95
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
                "reasoning_path": f"Answered directly using retrieved chunks from the uploaded PDF(s).",
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
            papers = await retrieve_graph_papers(
                keywords=anchors, anchors=anchors, limit=3
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
                        answer = await groq_chat(msgs, REASON_MODEL, temperature=req.temperature, max_tokens=1500)
                    except LLMError as e:
                        raise HTTPException(502, str(e))
                    return {
                        "request_id": rid,
                        "answer": answer,
                        "route": "entity_lookup",
                        "plan": {"standalone_query": plan.standalone_query, "reasoning_path": plan.reasoning_path},
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
            f"**{p.get('title','?')}** ({p.get('year','?')})\n\n"
            f"Authors: {auths}\n"
            f"Venue: {p.get('venue') or 'Unknown'}\n"
            f"Domain: {p.get('domain','Unknown')}"
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
                    lines = [f"Found **{len(all_fb)}** papers (sourced from arXiv/Semantic Scholar):\n"]
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
                        "plan": {"standalone_query": plan.standalone_query, "reasoning_path": plan.reasoning_path},
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
            lines.append(f"• **{p.get('title','?')}** ({p.get('year','?')}) — {auths}")
        return _direct_response(rid, "\n".join(lines), "structured", papers, t0)

    # ── Route: chitchat ───────────────────────────────────────────────
    if plan.route == "chitchat":
        sys_p = (
            "You are Aether, an evidence-only academic research assistant. "
            "Acknowledge the user's message briefly (aim for under 3-4 sentences) and complete your response. "
            "State clearly that you are optimized for scientific and academic literature queries and cannot answer general chitchat."
            "Do not invent academic facts."
        )
        if pdf_context:
            sys_p = (
                "You are Aether, an academic research assistant. "
                "Respond to the chitchat using the provided document context if relevant, otherwise reply briefly. Keep the output complete."
            )
            sys_p += f"\n\n{pdf_context}"
            
        msgs = await compile_chat_messages(sys_p, req.messages)
        answer = await groq_chat(msgs, REASON_MODEL, temperature=req.temperature, max_tokens=350 if pdf_context else 250)
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
        # Retrieve relevant abstracts from arXiv using combined keywords (not raw NL query)
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
        return re.sub(r'[^a-z0-9]', '', title.lower())

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
        "credits": {
            "plan": user_plan,
            "credits_used": plan_info.get("credits_used", 0) + CREDIT_COSTS.get("chat", 1),
            "credits_remaining": None if user_plan == "pro" else max(0, FREE_CREDITS_PER_DAY - plan_info.get("credits_used", 0) - CREDIT_COSTS.get("chat", 1)),
            "credits_limit": None if user_plan == "pro" else FREE_CREDITS_PER_DAY,
            "is_unlimited": user_plan == "pro",
        },
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
    """Deep structured comparison of two papers. [3 credits for Free, unlimited for Pro]"""
    rid = str(uuid.uuid4())
    request.state.request_id = rid
    pool.assert_ready()
    await check_rate_limit(request.client.host if request.client else "unknown")
    await check_and_deduct_credit(request, "compare")
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


@app.post("/api/research/survey")
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


@app.post("/api/research/bulk")
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
async def list_models(request: Request):
    """List available models. [PRO ONLY]"""
    await require_pro(request, "API Access (/v1/models)")
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


# ================================================================
# ================================================================
# AUTH & HISTORY ENDPOINTS
# ================================================================

def hash_password(password: str) -> str:
    pw_bytes = password.encode("utf-8")
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(pw_bytes, salt)
    return hashed.decode("utf-8")


def verify_password(password: str, hashed_password: str) -> bool:
    pw_bytes = password.encode("utf-8")
    hashed_bytes = hashed_password.encode("utf-8")
    return bcrypt.checkpw(pw_bytes, hashed_bytes)


from datetime import datetime, timedelta, timezone

def create_access_token(user_id: str, email: str) -> str:
    payload = {
        "sub": user_id,
        "email": email,
        "exp": datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRY_HOURS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError:
        return None

async def set_user_context(request: Request) -> Optional[str]:
    try:
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header.split(" ")[1]
            user = await get_authenticated_user(token)
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


async def get_authenticated_user(token: str) -> Optional[Dict[str, Any]]:
    try:
        payload = decode_access_token(token)
        if not payload:
            return None
        uid = payload.get("sub")
        if not uid:
            return None
        user = await asyncio.to_thread(db.users.find_one, {"_id": uid})
        if not user:
            return None
        return {
            "id": user["_id"],
            "email": user["email"],
            "user_metadata": user.get("user_metadata", {})
        }
    except Exception as e:
        log.error(f"Error validating MongoDB user token: {e}")
    return None


SMTP_HOST = os.getenv("SMTP_HOST")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
SMTP_FROM = os.getenv("SMTP_FROM", "Aether Intelligence <no-reply@aether.com>")
REQUIRE_EMAIL_VERIFICATION = os.getenv("REQUIRE_EMAIL_VERIFICATION", "true").lower() == "true"


async def send_auth_email(to_email: str, subject: str, text_content: str, html_content: str) -> bool:
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart

    if not SMTP_HOST or not SMTP_USER or not SMTP_PASSWORD:
        # Development / local mock mode
        log.info(
            f"\n=================== [MOCK EMAIL] ==================="
            f"\nTO: {to_email}"
            f"\nFROM: {SMTP_FROM}"
            f"\nSUBJECT: {subject}"
            f"\nCONTENT: {text_content}"
            f"\n====================================================\n"
        )
        return True

    try:
        def send_sync():
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = SMTP_FROM
            msg["To"] = to_email

            part1 = MIMEText(text_content, "plain")
            part2 = MIMEText(html_content, "html")
            msg.attach(part1)
            msg.attach(part2)

            with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
                server.starttls()
                server.login(SMTP_USER, SMTP_PASSWORD)
                server.sendmail(SMTP_FROM, to_email, msg.as_string())

        await asyncio.to_thread(send_sync)
        log.info(f"Email sent successfully to {to_email}")
        return True
    except Exception as e:
        log.error(f"Failed to send email to {to_email} via SMTP: {e}")
        return False


async def validate_email_mailboxlayer(email: str) -> Tuple[bool, Optional[str]]:
    api_key = os.getenv("MAILBOXLAYER_API_KEY")
    if not api_key:
        return True, None
        
    try:
        url = "http://apilayer.net/api/check"
        params = {"access_key": api_key, "email": email}
        async with httpx.AsyncClient(timeout=5.0) as client:
            res = await client.get(url, params=params)
            if res.status_code != 200:
                log.warning(f"Mailboxlayer API returned status code {res.status_code}")
                return True, None
                
            data = res.json()
            if "error" in data:
                log.warning(f"Mailboxlayer API error: {data['error']}")
                return True, None
                
            if not data.get("format_valid", True):
                return False, "Invalid email address format."
            if not data.get("mx_found", True):
                return False, "This email domain does not exist or cannot receive emails."
            if data.get("disposable", False):
                return False, "Disposable or temporary email addresses are not allowed."
                
            return True, None
    except Exception as e:
        log.error(f"Error calling Mailboxlayer API: {e}")
        return True, None


async def send_verification_email(email: str, user_id: str, request: Request = None) -> None:
    import random
    code = f"{random.randint(100000, 999999)}"
    expires_at = datetime.now(timezone.utc) + timedelta(hours=24)
    expires_at_naive = expires_at.replace(tzinfo=None)
    
    # Save verification code to MongoDB
    await asyncio.to_thread(
        db.users.update_one,
        {"_id": user_id},
        {"$set": {
            "verification_code": code,
            "verification_expires_at": expires_at_naive
        }}
    )
    
    base_url = "http://localhost:8000/"
    if request:
        base_url = str(request.base_url)
        
    verify_link = f"{base_url}api/auth/verify-link?email={email}&code={code}"
    
    subject = "Verify your Aether account"
    text_content = f"Welcome to Aether! Please verify your email by clicking the following link:\n{verify_link}\nThis link is valid for 24 hours."
    
    html_content = f"""
    <html>
    <body style="font-family: Arial, sans-serif; background-color: #0b0e14; color: #f8fafc; padding: 40px; text-align: center;">
        <div style="max-width: 500px; margin: 0 auto; background-color: #111118; border: 1px solid rgba(255, 255, 255, 0.08); padding: 30px; border-radius: 12px; box-shadow: 0 10px 30px rgba(0,0,0,0.5);">
            <h2 style="color: #6366f1; margin-bottom: 20px;">Welcome to Aether</h2>
            <p style="color: #94a3b8; font-size: 16px; line-height: 1.5;">Thank you for registering. Please click the button below to verify your email address and activate your account:</p>
            <div style="margin: 30px 0;">
                <a href="{verify_link}" style="background-color: #6366f1; color: white; padding: 12px 28px; border-radius: 8px; font-weight: bold; text-decoration: none; display: inline-block; font-size: 16px; box-shadow: 0 4px 15px rgba(99, 102, 241, 0.4);">
                    Verify Email Address
                </a>
            </div>
            <p style="color: #64748b; font-size: 12px; margin-top: 20px;">Or copy and paste this link in your browser:<br><a href="{verify_link}" style="color: #818cf8; word-break: break-all;">{verify_link}</a></p>
            <p style="color: #64748b; font-size: 12px;">This link is valid for 24 hours. If you did not sign up for Aether, please ignore this email.</p>
        </div>
    </body>
    </html>
    """
    await send_auth_email(email, subject, text_content, html_content)


async def send_reset_email(email: str, user_id: str) -> None:
    import random
    token = f"{random.randint(100000, 999999)}"
    expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
    expires_at_naive = expires_at.replace(tzinfo=None)
    
    # Save reset token to MongoDB
    await asyncio.to_thread(
        db.users.update_one,
        {"_id": user_id},
        {"$set": {
            "password_reset_token": token,
            "password_reset_expires_at": expires_at_naive
        }}
    )
    
    subject = "Reset your Aether password"
    text_content = f"We received a request to reset your Aether password.\nYour reset code is: {token}\nThis code is valid for 1 hour."
    
    html_content = f"""
    <html>
    <body style="font-family: Arial, sans-serif; background-color: #0b0e14; color: #f8fafc; padding: 40px; text-align: center;">
        <div style="max-width: 500px; margin: 0 auto; background-color: #111118; border: 1px solid rgba(255, 255, 255, 0.08); padding: 30px; border-radius: 12px; box-shadow: 0 10px 30px rgba(0,0,0,0.5);">
            <h2 style="color: #ef4444; margin-bottom: 20px;">Reset Password Request</h2>
            <p style="color: #94a3b8; font-size: 16px; line-height: 1.5;">We received a request to reset your Aether account password. Please enter the following 6-digit code on the reset password screen to proceed:</p>
            <div style="background-color: rgba(239, 68, 68, 0.1); border: 1px solid rgba(239, 68, 68, 0.3); padding: 15px; border-radius: 8px; font-size: 32px; font-weight: bold; letter-spacing: 6px; color: #f87171; margin: 30px 0;">
                {token}
            </div>
            <p style="color: #64748b; font-size: 12px;">This code is valid for 1 hour. If you did not request a password reset, please ignore this email or contact support if you have concerns.</p>
        </div>
    </body>
    </html>
    """
    await send_auth_email(email, subject, text_content, html_content)


class SignUpRequest(BaseModel):
    email: str
    password: str


class LoginRequest(BaseModel):
    email: str
    password: str


class ProfileUpdateRequest(BaseModel):
    full_name: Optional[str] = None
    institution: Optional[str] = None
    role: Optional[str] = None


class PasswordUpdateRequest(BaseModel):
    password: str


class VerifyEmailRequest(BaseModel):
    email: str
    code: str


class ResendVerificationRequest(BaseModel):
    email: str


class ForgotPasswordRequest(BaseModel):
    email: str


class ResetPasswordRequest(BaseModel):
    email: str
    token: str
    new_password: str


@app.post("/api/auth/signup")
async def auth_signup(req: SignUpRequest, request: Request):
    email = req.email.strip().lower()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Invalid email address")
        
    # Validate email via Mailboxlayer API
    is_valid, err_msg = await validate_email_mailboxlayer(email)
    if not is_valid:
        raise HTTPException(status_code=400, detail=err_msg)
    if len(req.password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters long")
    
    # Check if user exists
    existing = await asyncio.to_thread(db.users.find_one, {"email": email})
    if existing:
        raise HTTPException(status_code=400, detail="An account with this email already exists")
    
    # Create user
    uid = str(uuid.uuid4())
    password_hash = hash_password(req.password)
    now = datetime.now(timezone.utc)
    credits_reset = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    
    is_verified_init = not REQUIRE_EMAIL_VERIFICATION
    user_doc = {
        "_id": uid,
        "email": email,
        "password_hash": password_hash,
        "user_metadata": {
            "full_name": email.split("@")[0].capitalize(),
            "institution": "",
            "role": ""
        },
        # --- Plan & credit fields ---
        "plan": "free",              # "free" | "pro"
        "credits_used": 0,           # resets daily
        "credits_reset_at": credits_reset,
        "stripe_customer_id": None,  # set on first Stripe checkout
        "stripe_subscription_id": None,
        # ----------------------------
        "is_verified": is_verified_init,
        "created_at": now,
        "updated_at": now,
    }
    await asyncio.to_thread(db.users.insert_one, user_doc)
    
    if REQUIRE_EMAIL_VERIFICATION:
        await send_verification_email(email, uid, request)
        return {
            "status": "verification_pending",
            "email": email,
            "msg": "Please verify your email address via the link sent to you."
        }
    
    token = create_access_token(uid, email)
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "id": uid,
            "email": email,
            "user_metadata": user_doc["user_metadata"]
        }
    }


@app.post("/api/auth/login")
async def auth_login(req: LoginRequest, request: Request):
    email = req.email.strip().lower()
    user = await asyncio.to_thread(db.users.find_one, {"email": email})
    if not user or not verify_password(req.password, user["password_hash"]):
        raise HTTPException(status_code=400, detail="Invalid email or password")
    
    if REQUIRE_EMAIL_VERIFICATION and not user.get("is_verified", False):
        await send_verification_email(email, user["_id"], request)
        return {
            "status": "verification_pending",
            "email": email,
            "msg": "Please verify your email address to log in."
        }
        
    token = create_access_token(user["_id"], user["email"])
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "id": user["_id"],
            "email": user["email"],
            "user_metadata": user.get("user_metadata", {})
        }
    }


@app.post("/api/auth/verify-email")
async def verify_email(req: VerifyEmailRequest):
    email = req.email.strip().lower()
    user = await asyncio.to_thread(db.users.find_one, {"email": email})
    if not user:
        raise HTTPException(status_code=400, detail="User not found")
        
    stored_code = user.get("verification_code")
    expires_at = user.get("verification_expires_at")
    
    if not stored_code or stored_code != req.code.strip():
        raise HTTPException(status_code=400, detail="Invalid verification code")
        
    if expires_at and datetime.utcnow() > expires_at:
        raise HTTPException(status_code=400, detail="Verification code has expired")
        
    # Mark verified
    await asyncio.to_thread(
        db.users.update_one,
        {"_id": user["_id"]},
        {"$set": {"is_verified": True}, "$unset": {"verification_code": "", "verification_expires_at": ""}}
    )
    
    # Return access token
    token = create_access_token(user["_id"], user["email"])
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "id": user["_id"],
            "email": user["email"],
            "user_metadata": user.get("user_metadata", {})
        }
    }


@app.get("/api/auth/verify-link")
async def verify_email_link(email: str, code: str):
    email = email.strip().lower()
    user = await asyncio.to_thread(db.users.find_one, {"email": email})
    if not user:
        return HTMLResponse(
            status_code=400,
            content="""
            <html>
            <body style="font-family: Arial, sans-serif; background-color: #0b0e14; color: #f8fafc; padding: 40px; text-align: center;">
                <h2 style="color: #ef4444;">User Not Found</h2>
                <p style="color: #94a3b8;">The requested user account was not found.</p>
                <p><a href="/" style="color: #6366f1; text-decoration: none; font-weight: bold;">Return to Landing Page</a></p>
            </body>
            </html>
            """
        )
        
    stored_code = user.get("verification_code")
    expires_at = user.get("verification_expires_at")
    
    if not stored_code or stored_code != code.strip():
        return HTMLResponse(
            status_code=400,
            content="""
            <html>
            <body style="font-family: Arial, sans-serif; background-color: #0b0e14; color: #f8fafc; padding: 40px; text-align: center;">
                <h2 style="color: #ef4444;">Invalid Verification Link</h2>
                <p style="color: #94a3b8;">This verification link is invalid or has already been used.</p>
                <p><a href="/" style="color: #6366f1; text-decoration: none; font-weight: bold;">Return to Landing Page</a></p>
            </body>
            </html>
            """
        )
        
    if expires_at and datetime.utcnow() > expires_at:
        return HTMLResponse(
            status_code=400,
            content="""
            <html>
            <body style="font-family: Arial, sans-serif; background-color: #0b0e14; color: #f8fafc; padding: 40px; text-align: center;">
                <h2 style="color: #ef4444;">Verification Link Expired</h2>
                <p style="color: #94a3b8;">This verification link has expired. Please log in to request a new link.</p>
                <p><a href="/" style="color: #6366f1; text-decoration: none; font-weight: bold;">Return to Landing Page</a></p>
            </body>
            </html>
            """
        )
        
    # Mark verified
    await asyncio.to_thread(
        db.users.update_one,
        {"_id": user["_id"]},
        {"$set": {"is_verified": True}, "$unset": {"verification_code": "", "verification_expires_at": ""}}
    )
    
    # Redirect to landing page with verification success query param
    return RedirectResponse(url="/?verified=true")


@app.post("/api/auth/resend-verification")
async def resend_verification(req: ResendVerificationRequest, request: Request):
    email = req.email.strip().lower()
    user = await asyncio.to_thread(db.users.find_one, {"email": email})
    if not user:
        raise HTTPException(status_code=400, detail="User not found")
        
    await send_verification_email(email, user["_id"], request)
    return {"msg": "Verification link resent successfully"}


@app.post("/api/auth/forgot-password")
async def forgot_password(req: ForgotPasswordRequest):
    email = req.email.strip().lower()
    user = await asyncio.to_thread(db.users.find_one, {"email": email})
    if not user:
        return {"msg": "If this email exists, a reset code has been sent."}
        
    await send_reset_email(email, user["_id"])
    return {"msg": "Password reset code sent successfully"}


@app.post("/api/auth/reset-password")
async def reset_password(req: ResetPasswordRequest):
    email = req.email.strip().lower()
    user = await asyncio.to_thread(db.users.find_one, {"email": email})
    if not user:
        raise HTTPException(status_code=400, detail="User not found")
        
    stored_token = user.get("password_reset_token")
    expires_at = user.get("password_reset_expires_at")
    
    if not stored_token or stored_token != req.token.strip():
        raise HTTPException(status_code=400, detail="Invalid or missing reset token")
        
    if expires_at and datetime.utcnow() > expires_at:
        raise HTTPException(status_code=400, detail="Reset token has expired")
        
    if len(req.new_password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters long")
        
    password_hash = hash_password(req.new_password)
    
    await asyncio.to_thread(
        db.users.update_one,
        {"_id": user["_id"]},
        {"$set": {"password_hash": password_hash}, "$unset": {"password_reset_token": "", "password_reset_expires_at": ""}}
    )
    
    return {"msg": "Password reset successful"}


@app.put("/api/auth/profile")
async def auth_update_profile(req: ProfileUpdateRequest, request: Request):
    token = get_token_from_request(request)
    user = await get_authenticated_user(token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid token")
    uid = user["id"]
    
    # Fetch from DB to ensure it exists
    user_db = await asyncio.to_thread(db.users.find_one, {"_id": uid})
    if not user_db:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Update fields
    meta = user_db.get("user_metadata", {})
    if req.full_name is not None:
        meta["full_name"] = req.full_name
    if req.institution is not None:
        meta["institution"] = req.institution
    if req.role is not None:
        meta["role"] = req.role
        
    await asyncio.to_thread(
        db.users.update_one,
        {"_id": uid},
        {"$set": {"user_metadata": meta, "updated_at": datetime.now(timezone.utc)}}
    )
    
    return {
        "id": uid,
        "email": user_db["email"],
        "user_metadata": meta
    }


@app.put("/api/auth/password")
async def auth_update_password(req: PasswordUpdateRequest, request: Request):
    token = get_token_from_request(request)
    user = await get_authenticated_user(token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid token")
    uid = user["id"]
    
    if len(req.password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters long")
        
    user_db = await asyncio.to_thread(db.users.find_one, {"_id": uid})
    if not user_db:
        raise HTTPException(status_code=404, detail="User not found")
        
    password_hash = hash_password(req.password)
    await asyncio.to_thread(
        db.users.update_one,
        {"_id": uid},
        {"$set": {"password_hash": password_hash, "updated_at": datetime.now(timezone.utc)}}
    )
    return {"status": "success"}


@app.get("/api/auth/me")
async def auth_me(request: Request):
    token = get_token_from_request(request)
    user = await get_authenticated_user(token)
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
    user = await get_authenticated_user(token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid token")
    try:
        sessions = await asyncio.to_thread(
            lambda: list(db.chat_sessions.find({"user_id": user["id"]}).sort("updated_at", -1))
        )
        for s in sessions:
            s["id"] = s.pop("_id")
            if isinstance(s.get("updated_at"), datetime):
                s["updated_at"] = s["updated_at"].isoformat()
            if isinstance(s.get("created_at"), datetime):
                s["created_at"] = s["created_at"].isoformat()
        return sessions
    except Exception as e:
        log.error(f"Error fetching history: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/history")
async def create_history(request: Request):
    token = get_token_from_request(request)
    user = await get_authenticated_user(token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid token")
    
    try:
        body = await request.json()
    except Exception:
        body = {}
        
    title = body.get("title", "New Chat")
    messages = body.get("messages", [])
    
    try:
        session_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        session_doc = {
            "_id": session_id,
            "user_id": user["id"],
            "title": title,
            "messages": messages,
            "created_at": now,
            "updated_at": now
        }
        await asyncio.to_thread(db.chat_sessions.insert_one, session_doc)
        
        session_doc["id"] = session_doc.pop("_id")
        session_doc["created_at"] = session_doc["created_at"].isoformat()
        session_doc["updated_at"] = session_doc["updated_at"].isoformat()
        return session_doc
    except Exception as e:
        log.error(f"Error creating history session: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/api/history/{session_id}")
async def update_history(session_id: str, request: Request):
    token = get_token_from_request(request)
    user = await get_authenticated_user(token)
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
    
    update_data["updated_at"] = datetime.now(timezone.utc)
    
    try:
        from pymongo import ReturnDocument
        res = await asyncio.to_thread(
            db.chat_sessions.find_one_and_update,
            {"_id": session_id, "user_id": user["id"]},
            {"$set": update_data},
            return_document=ReturnDocument.AFTER
        )
        if not res:
            raise HTTPException(status_code=404, detail="Chat session not found")
            
        res["id"] = res.pop("_id")
        if isinstance(res.get("created_at"), datetime):
            res["created_at"] = res["created_at"].isoformat()
        if isinstance(res.get("updated_at"), datetime):
            res["updated_at"] = res["updated_at"].isoformat()
        return res
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Error updating history session: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/history")
async def delete_all_history(request: Request):
    token = get_token_from_request(request)
    user = await get_authenticated_user(token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid token")
    try:
        await asyncio.to_thread(db.chat_sessions.delete_many, {"user_id": user["id"]})
        return {"status": "success"}
    except Exception as e:
        log.error(f"Error deleting all history: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/history/{session_id}")
async def delete_history(session_id: str, request: Request):
    token = get_token_from_request(request)
    user = await get_authenticated_user(token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid token")
    try:
        res = await asyncio.to_thread(
            db.chat_sessions.delete_one,
            {"_id": session_id, "user_id": user["id"]}
        )
        if res.deleted_count == 0:
            raise HTTPException(status_code=404, detail="Chat session not found")
        return {"status": "success"}
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Error deleting history session: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ================================================================
# PLAN MANAGEMENT ENDPOINTS
# ================================================================


@app.get("/api/credits")
async def get_credits(request: Request):
    """Return remaining daily credits and plan info for the authenticated user."""
    plan_info = await get_user_plan(request)
    plan = plan_info.get("plan", "free")
    credits_used = plan_info.get("credits_used", 0)
    credits_remaining = None if plan == "pro" else max(0, FREE_CREDITS_PER_DAY - credits_used)
    return {
        "plan": plan,
        "credits_used": credits_used,
        "credits_limit": None if plan == "pro" else FREE_CREDITS_PER_DAY,
        "credits_remaining": credits_remaining,
        "credits_reset_at": plan_info.get("credits_reset_at"),
        "is_unlimited": plan == "pro",
        "credit_costs": CREDIT_COSTS,
    }


@app.get("/api/auth/plan")
async def get_plan(request: Request):
    """Return the current user's subscription plan and credit status."""
    token = get_token_from_request(request)
    user = await get_authenticated_user(token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid token")
    plan_info = await get_user_plan(request)
    plan = plan_info.get("plan", "free")
    credits_used = plan_info.get("credits_used", 0)
    return {
        "plan": plan,
        "is_pro": plan == "pro",
        "credits_used": credits_used,
        "credits_limit": None if plan == "pro" else FREE_CREDITS_PER_DAY,
        "credits_remaining": None if plan == "pro" else max(0, FREE_CREDITS_PER_DAY - credits_used),
        "credits_reset_at": plan_info.get("credits_reset_at"),
        "features": {
            "survey": plan == "pro",
            "bulk_research": plan == "pro",
            "heavy_model": plan == "pro",
            "api_access": plan == "pro",
            "top_k_max": PRO_TOP_K_MAX if plan == "pro" else FREE_TOP_K_MAX,
            "citation_network_full": plan == "pro",
        },
    }


@app.post("/api/auth/upgrade")
async def stripe_webhook(request: Request):
    """Stripe webhook receiver — flips user plan to pro on payment, back to free on cancellation.
    Set this URL as your Stripe webhook endpoint.
    Events handled: checkout.session.completed, customer.subscription.deleted
    """
    import hmac
    import hashlib

    STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")

    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    # Verify Stripe signature if secret is configured
    if STRIPE_WEBHOOK_SECRET:
        try:
            # Simple timestamp+signature check (full stripe SDK not required)
            parts = {p.split("=")[0]: p.split("=")[1] for p in sig_header.split(",") if "=" in p}
            ts = parts.get("t", "")
            sig = parts.get("v1", "")
            signed_payload = f"{ts}.{payload.decode()}"
            expected = hmac.new(
                STRIPE_WEBHOOK_SECRET.encode(),
                signed_payload.encode(),
                hashlib.sha256,
            ).hexdigest()
            if not hmac.compare_digest(expected, sig):
                raise HTTPException(status_code=400, detail="Invalid Stripe signature")
        except HTTPException:
            raise
        except Exception as e:
            log.warning(f"Stripe signature verification error: {e}")
            raise HTTPException(status_code=400, detail="Signature verification failed")

    try:
        event = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    event_type = event.get("type", "")
    data = event.get("data", {}).get("object", {})

    if event_type == "checkout.session.completed":
        # Payment successful → upgrade user to Pro
        customer_email = data.get("customer_details", {}).get("email") or data.get("customer_email")
        customer_id = data.get("customer")
        subscription_id = data.get("subscription")
        if customer_email:
            await asyncio.to_thread(
                db.users.update_one,
                {"email": customer_email.lower()},
                {"$set": {
                    "plan": "pro",
                    "stripe_customer_id": customer_id,
                    "stripe_subscription_id": subscription_id,
                    "updated_at": datetime.now(timezone.utc),
                }},
            )
            log.info(f"[Stripe] Upgraded {customer_email} to Pro (sub: {subscription_id})")

    elif event_type in ("customer.subscription.deleted", "customer.subscription.paused"):
        # Subscription cancelled/paused → downgrade to Free
        customer_id = data.get("customer")
        if customer_id:
            await asyncio.to_thread(
                db.users.update_one,
                {"stripe_customer_id": customer_id},
                {"$set": {
                    "plan": "free",
                    "stripe_subscription_id": None,
                    "updated_at": datetime.now(timezone.utc),
                }},
            )
            log.info(f"[Stripe] Downgraded customer {customer_id} to Free")

    return {"received": True, "event": event_type}


class RazorpayVerifyRequest(BaseModel):
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str


@app.post("/api/auth/razorpay/create-order")
async def razorpay_create_order(request: Request):
    token = get_token_from_request(request)
    user = await get_authenticated_user(token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid token")

    if not RAZORPAY_KEY_ID or not RAZORPAY_KEY_SECRET:
        raise HTTPException(status_code=500, detail="Razorpay is not configured on the server")

    amount = 49900  # Rs 499 in paise
    receipt_id = f"rcpt_{user['id'][:8]}_{int(time.time())}"

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://api.razorpay.com/v1/orders",
                auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET),
                json={
                    "amount": amount,
                    "currency": "INR",
                    "receipt": receipt_id
                },
                timeout=10.0
            )
            
            if response.status_code != 200:
                log.error(f"Razorpay order creation failed: {response.text}")
                raise HTTPException(status_code=response.status_code, detail="Failed to create order with Razorpay")
                
            order_data = response.json()
            return {
                "order_id": order_data["id"],
                "amount": order_data["amount"],
                "currency": order_data["currency"],
                "key_id": RAZORPAY_KEY_ID
            }
    except httpx.RequestError as e:
        log.error(f"HTTP request to Razorpay failed: {e}")
        raise HTTPException(status_code=500, detail="Could not connect to Razorpay")
    except Exception as e:
        log.error(f"Error creating Razorpay order: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/auth/razorpay/verify-payment")
async def razorpay_verify_payment(req: RazorpayVerifyRequest, request: Request):
    token = get_token_from_request(request)
    user = await get_authenticated_user(token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid token")

    if not RAZORPAY_KEY_SECRET:
        raise HTTPException(status_code=500, detail="Razorpay secret is not configured on the server")

    import hmac
    import hashlib

    msg = f"{req.razorpay_order_id}|{req.razorpay_payment_id}"
    expected = hmac.new(
        RAZORPAY_KEY_SECRET.encode(),
        msg.encode(),
        hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(expected, req.razorpay_signature):
        log.warning(f"Razorpay signature mismatch for user {user['email']}")
        raise HTTPException(status_code=400, detail="Payment signature verification failed")

    # Upgrade the user's plan to pro in MongoDB and record the payment
    try:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        
        await asyncio.to_thread(
            db.users.update_one,
            {"_id": user["id"]},
            {"$set": {
                "plan": "pro",
                "razorpay_order_id": req.razorpay_order_id,
                "razorpay_payment_id": req.razorpay_payment_id,
                "updated_at": now,
            }}
        )
        
        # Save detailed payment record in payments collection
        payment_record = {
            "user_id": user["id"],
            "email": user["email"],
            "razorpay_order_id": req.razorpay_order_id,
            "razorpay_payment_id": req.razorpay_payment_id,
            "razorpay_signature": req.razorpay_signature,
            "amount": 49900,  # Rs 499 in paise
            "currency": "INR",
            "plan": "pro",
            "status": "completed",
            "created_at": now
        }
        await asyncio.to_thread(
            db.payments.insert_one,
            payment_record
        )
        
        log.info(f"[Razorpay] Successfully upgraded user {user['email']} to Pro and saved payment record.")
        return {"status": "success", "message": "Successfully upgraded to Pro"}
    except Exception as e:
        log.error(f"Error upgrading user plan/saving payment in MongoDB: {e}")
        raise HTTPException(status_code=500, detail="Failed to update plan or save payment record")


@app.get("/api/auth/payments/history")
async def get_payment_history(request: Request):
    token = get_token_from_request(request)
    user = await get_authenticated_user(token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid token")

    try:
        # Find all payments for this user, sorted by created_at descending
        cursor = db.payments.find({"user_id": user["id"]}).sort("created_at", -1)
        payments = await asyncio.to_thread(list, cursor)
        
        formatted_payments = []
        for p in payments:
            formatted_payments.append({
                "id": str(p.get("_id")),
                "razorpay_order_id": p.get("razorpay_order_id"),
                "razorpay_payment_id": p.get("razorpay_payment_id"),
                "amount": p.get("amount", 0) / 100.0,  # Convert paise to Rs
                "currency": p.get("currency", "INR"),
                "plan": p.get("plan", "pro"),
                "status": p.get("status", "completed"),
                "created_at": p.get("created_at").isoformat() if isinstance(p.get("created_at"), datetime) else None
            })
            
        return formatted_payments
    except Exception as e:
        log.error(f"Error fetching payment history: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch payment history")


# ================================================================
# HEALTH ENDPOINTS
# ================================================================


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "service": "Aether Research API",
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


@app.post("/api/upload/pdf")
async def upload_pdf(request: Request, file: UploadFile = File(...)):
    await set_user_context(request)
    
    filename = file.filename or "document.pdf"
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")
        
    try:
        content = await file.read()
        if not content:
            raise HTTPException(status_code=400, detail="Empty PDF file.")
            
        def _extract():
            doc = fitz.open(stream=content, filetype="pdf")
            text_content = []
            for page in doc:
                text_content.append(page.get_text())
            return "\n".join(text_content).strip()
            
        extracted_text = await asyncio.to_thread(_extract)
        if not extracted_text:
            raise HTTPException(status_code=400, detail="The PDF contains no readable text.")
            
        pdf_id = f"pdf-{uuid.uuid4()}"
        
        # Save to MongoDB
        await asyncio.to_thread(
            db.uploaded_pdfs.insert_one,
            {
                "_id": pdf_id,
                "name": filename,
                "text": extracted_text,
                "created_at": datetime.now(timezone.utc)
            }
        )
        
        pdf_url = f"/api/pdf/{pdf_id}.pdf"
        
        return {
            "file_id": pdf_id,
            "url": pdf_url,
            "name": filename,
            "text_length": len(extracted_text)
        }
    except HTTPException as he:
        raise he
    except Exception as e:
        log.error(f"Error parsing PDF file upload: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to process PDF: {str(e)}")


@app.get("/api/pdf/{pdf_id}")
async def get_pdf_text(pdf_id: str, request: Request):
    await set_user_context(request)
    pdf_id = pdf_id.replace(".pdf", "")
    doc = await asyncio.to_thread(db.uploaded_pdfs.find_one, {"_id": pdf_id})
    if not doc:
        raise HTTPException(status_code=404, detail="PDF not found.")
    return {"text": doc["text"], "name": doc.get("name", "Document")}


def is_whisper_hallucination(text: str) -> bool:
    if not text:
        return True
    # Strip punctuation and lowercase
    cleaned = "".join(c for c in text if c.isalnum()).lower().strip()
    hallucinations = {
        "", "you", "thankyou", "thankyouforwatching", "pleaselikeandsubscribe", 
        "subscribe", "watching", "bye", "thankyoubye", "thankyousomuch"
    }
    return cleaned in hallucinations


@app.post("/api/audio/transcribe")
async def transcribe_audio(request: Request, file: UploadFile = File(...)):
    await set_user_context(request)
    
    if not GROQ_API_KEY and not GROQ_API_KEYS:
        raise HTTPException(
            status_code=503,
            detail="Groq API key not configured on the server."
        )
        
    content = await file.read()
    if not content:
        raise HTTPException(
            status_code=400,
            detail="Empty audio file."
        )
        
    max_attempts = len(GROQ_API_KEYS) if GROQ_API_KEYS else 1
    last_err = None
    
    for attempt in range(max_attempts):
        current_key = GROQ_API_KEY or ""
        if GROQ_API_KEYS:
            key_idx = (groq_key_index + attempt) % len(GROQ_API_KEYS)
            current_key = GROQ_API_KEYS[key_idx]
            
        headers = {
            "Authorization": f"Bearer {current_key}"
        }
        
        # Normalize content type by stripping parameters (e.g. codecs=opus)
        content_type = file.content_type or "audio/webm"
        if ";" in content_type:
            content_type = content_type.split(";")[0].strip()
            
        files = {
            "file": (file.filename or "speech.webm", content, content_type)
        }
        data = {
            "model": "whisper-large-v3"
        }
        
        try:
            r = await pool.groq_http.post(
                "https://api.groq.com/openai/v1/audio/transcriptions",
                headers=headers,
                files=files,
                data=data,
                timeout=60.0
            )
            
            if r.status_code == 200:
                resp_json = r.json()
                text = resp_json.get("text", "")
                if is_whisper_hallucination(text):
                    text = ""
                return {"text": text}
            elif r.status_code == 429:
                rotate_groq_key()
                last_err = f"Groq HTTP 429: {r.text[:200]}"
                continue
            else:
                last_err = f"Groq HTTP {r.status_code}: {r.text[:200]}"
                rotate_groq_key()
                continue
        except Exception as e:
            last_err = str(e)
            rotate_groq_key()
            continue
            
    raise HTTPException(
        status_code=500,
        detail=f"Failed to transcribe audio. Error: {last_err}"
    )


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
        if mongo_client:
            await asyncio.to_thread(mongo_client.admin.command, "ping")
            checks["mongodb"] = "ok"
        else:
            checks["mongodb"] = "error: MongoClient not initialized"
    except Exception as e:
        checks["mongodb"] = f"error: {e}"
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
        reload=os.getenv("ENV", "dev") == "dev",
        reload_excludes=["*.log", "app.log", "__pycache__/*"],
        log_level="info",
        workers=int(os.getenv("WORKERS", "1")),
    )
