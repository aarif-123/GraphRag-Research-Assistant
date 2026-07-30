"""
routes/health.py — System health, stats, config, and root endpoints.
"""

import asyncio

from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse

from app.clients.groq import create_embedding, groq_chat
from app.clients.pool import CACHE, get_supabase_client, mongo_client, pool
from app.config import REASON_MODEL, SUPABASE_KEY, SUPABASE_URL

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


@router.get("/api/health/full")
async def full_health():
    checks: dict = {}
    try:
        await asyncio.to_thread(
            lambda: get_supabase_client().table("papers").select("id").limit(1).execute()
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
        await groq_chat([{"role": "user", "content": "ping"}], REASON_MODEL, max_tokens=1)
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
    return {"supabase_url": SUPABASE_URL, "supabase_anon_key": SUPABASE_KEY}


@router.get("/")
def root():
    return FileResponse("frontend/landing.html")


@router.get("/styles.css")
def read_styles():
    return FileResponse("frontend/styles.css")
