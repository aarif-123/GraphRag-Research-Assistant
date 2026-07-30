"""
Main FastAPI Application Entry Point for Aether GraphRAG Research Assistant.
"""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import ALLOWED_ORIGINS, log
from app.clients.pool import pool

from app.routes.health import router as health_router
from app.routes.auth import router as auth_router, router_payments, router_history
from app.routes.graph import router as graph_router
from app.routes.research import router as research_router
from app.routes.chat import router as chat_router
from app.routes.media import router as media_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Starting up Aether Research API server...")
    await pool.init()
    yield
    log.info("Shutting down Aether Research API server...")
    await pool.close()


app = FastAPI(
    title="Aether Research API",
    version="4.0.0",
    description="Graph-augmented RAG for academic research — Intelligence Edition",
    lifespan=lifespan,
)

# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
    allow_credentials=False,
)

# Include Routers
app.include_router(health_router)
app.include_router(auth_router)
app.include_router(router_payments)
app.include_router(router_history)
app.include_router(graph_router)
app.include_router(research_router)
app.include_router(chat_router)
app.include_router(media_router)

# Mount Frontend Static Files if directory exists
_frontend_dir = Path("frontend")
if _frontend_dir.exists():
    app.mount("/app", StaticFiles(directory=str(_frontend_dir), html=True), name="frontend")
    log.info(f"Frontend mounted at /app → {_frontend_dir}")

if __name__ == "__main__":
    import os
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
        reload=os.getenv("ENV", "dev") == "dev",
        reload_excludes=["*.log", "app.log", "__pycache__/*"],
        log_level="info",
        workers=int(os.getenv("WORKERS", "1")),
    )
