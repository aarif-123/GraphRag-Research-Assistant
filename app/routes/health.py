"""
routes/health.py — System health, stats, config, and root endpoints.
"""

import asyncio

from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse

from app.clients.groq import create_embedding, groq_chat
from app.clients.pool import CACHE, pool
from app.config import (
    FREEZE_RETRIEVAL,
    REASON_MODEL,
    SUPABASE_ANON_KEY,
    SUPABASE_KEY,
    SUPABASE_URL,
    log,
)

router = APIRouter()


@router.get("/api/health")
@router.get("/health")
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


@router.get("/api/ready")
def readiness():
    if not pool._ready:
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "ready": False},
        )
    return {"status": "ready", "ready": True}


@router.get("/api/health/full")
async def full_health():
    checks: dict = {}
    if FREEZE_RETRIEVAL:
        checks["supabase"] = "skipped"
    else:
        try:
            if pool.supabase is None:
                raise RuntimeError("Supabase client unavailable")
            await asyncio.to_thread(
                lambda: pool.supabase.table("papers").select("id").limit(1).execute()
            )
            checks["supabase"] = "ok"
        except Exception as e:
            log.warning(f"Supabase health check failed: {e}")
            checks["supabase"] = "unavailable"
    try:
        if pool.db is not None:
            await asyncio.to_thread(pool.db.client.admin.command, "ping")
            checks["mongodb"] = "ok"
        else:
            checks["mongodb"] = "unavailable"
    except Exception as e:
        log.warning(f"MongoDB health check failed: {e}")
        checks["mongodb"] = "unavailable"
    if FREEZE_RETRIEVAL:
        checks["neo4j"] = "skipped"
    else:
        try:
            if pool.neo4j is None:
                raise RuntimeError("Neo4j driver unavailable")
            await asyncio.to_thread(pool.neo4j.verify_connectivity)
            checks["neo4j"] = "ok"
        except Exception as e:
            log.warning(f"Neo4j health check failed: {e}")
            checks["neo4j"] = "unavailable"
    try:
        await groq_chat([{"role": "user", "content": "ping"}], REASON_MODEL, max_tokens=1)
        checks["groq"] = "ok"
    except Exception as e:
        log.warning(f"Groq health check failed: {e}")
        checks["groq"] = "unavailable"
    if FREEZE_RETRIEVAL:
        checks["embedding"] = "skipped"
    else:
        try:
            await create_embedding("health check")
            checks["embedding"] = "ok"
        except Exception as e:
            log.warning(f"Embedding health check failed: {e}")
            checks["embedding"] = "unavailable"
    all_ok = all(v in ("ok", "skipped") for v in checks.values())
    return JSONResponse(
        status_code=200 if all_ok else 207,
        content={"status": "healthy" if all_ok else "degraded", "checks": checks},
    )


@router.get("/api/stats")
async def stats():
    from app.core.graph import get_graph_stats

    pool.assert_ready()
    return await get_graph_stats()


@router.get("/api/models")
async def list_models(request=None):
    from app.config import HEAVY_MODEL, PLAN_MODEL, REASON_MODEL

    return {
        "models": [
            {"id": REASON_MODEL, "type": "reason", "description": "Default reasoning model"},
            {"id": HEAVY_MODEL, "type": "heavy", "description": "Heavy synthesis model (Pro only)"},
            {"id": PLAN_MODEL, "type": "plan", "description": "Strategic planning model"},
        ]
    }


@router.get("/api/config")
def get_config():
    public_key = SUPABASE_ANON_KEY or None
    if public_key and public_key == SUPABASE_KEY:
        log.error("SUPABASE_ANON_KEY matches the service-role key; omitting it from public config.")
        public_key = None
    return {
        "supabase_url": SUPABASE_URL,
        "supabase_anon_key": public_key,
    }


@router.get("/")
def root():
    return FileResponse("frontend/landing.html")


@router.get("/styles.css")
def read_styles():
    return FileResponse("frontend/styles.css")
