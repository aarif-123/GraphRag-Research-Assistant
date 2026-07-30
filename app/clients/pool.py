"""
Connection Pooling and Database Clients Module.
Provides Mongo, Neo4j, Supabase, and Upstash Redis clients,
along with global caching, rate limiting, and credit validation helpers.
"""

import asyncio
import contextvars
import hashlib
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import httpx
import jwt
from fastapi import HTTPException, Request
from neo4j import GraphDatabase
from pymongo import MongoClient
from supabase import create_client

from app.config import (
    CACHE_MAX,
    CACHE_TTL,
    CREDIT_COSTS,
    FREE_CREDITS_PER_DAY,
    FREEZE_RETRIEVAL,
    GROQ_TIMEOUT,
    JWT_ALGORITHM,
    JWT_EXPIRY_HOURS,
    JWT_SECRET,
    MONGODB_DB_NAME,
    MONGODB_URI,
    NEO4J_PASSWORD,
    NEO4J_URI,
    NEO4J_USER,
    RATE_LIMIT,
    SUPABASE_KEY,
    SUPABASE_URL,
    UPSTASH_REDIS_REST_TOKEN,
    UPSTASH_REDIS_REST_URL,
    log,
)

# ================================================================
# THREAD-LOCAL SUPABASE CLIENT
# ================================================================
_supabase_local = threading.local()


def get_supabase_client():
    if not hasattr(_supabase_local, "client"):
        _supabase_local.client = create_client(SUPABASE_URL, SUPABASE_KEY)
    return _supabase_local.client


# ================================================================
# GLOBAL DB CLIENT REFERENCES
# ================================================================
mongo_client = None
db = None

# Upstash Redis
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
# IN-MEMORY LRU CACHE
# ================================================================
current_user_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "current_user_id", default=None
)


def cache_key(*args) -> str:
    raw = "|".join(str(a) for a in args)
    return hashlib.md5(raw.encode()).hexdigest()


CACHE: Dict[str, Dict[str, Any]] = {
    "graph": {},
    "embed": {},
    "llm": {},
    "plan": {},
    "relations": {},
    "api": {},
}


def get_cache(bucket: str, key: str):
    user_id = current_user_id.get()
    if bucket in ("llm", "plan", "relations") and user_id:
        key = f"{user_id}:{key}"

    entry = CACHE[bucket].get(key)
    if not entry:
        return None

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
# RATE LIMITER STATE
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
# ACCESS TOKEN ENCODING/DECODING & USER RETRIEVAL
# ================================================================
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
            "user_metadata": user.get("user_metadata", {}),
        }
    except Exception as e:
        log.error(f"Error validating MongoDB user token: {e}")
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


# ================================================================
# PLAN & CREDIT SYSTEM
# ================================================================
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
        if reset_at is None or (
            isinstance(reset_at_naive, datetime) and now_naive >= reset_at_naive
        ):
            new_reset = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
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
                "credits_remaining": None
                if _plan == "pro"
                else max(0, FREE_CREDITS_PER_DAY - _used),
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


# ================================================================
# CONNECTION POOL CLASS
# ================================================================
class Pool:
    def __init__(self):
        self.supabase = None
        self.neo4j = None
        self.groq_http = None
        self.db = None
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
            self.db = db
            # Create a unique index on email
            db.users.create_index("email", unique=True)
            db.payments.create_index("user_id")
            db.payments.create_index("razorpay_payment_id", unique=True)
            log.info("MongoDB connected and unique indexes on email and payments verified")
        except Exception as e:
            errors.append(f"MongoDB: {e}")
            log.error(f"MongoDB connection failed: {e}")

        if FREEZE_RETRIEVAL:
            log.info("Database retrieval is frozen. Skipping Neo4j connection initialization.")
        else:
            try:
                self.neo4j = GraphDatabase.driver(
                    NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD), notifications_min_severity="OFF"
                )
                await asyncio.wait_for(asyncio.to_thread(self.neo4j.verify_connectivity), timeout=10.0)
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
