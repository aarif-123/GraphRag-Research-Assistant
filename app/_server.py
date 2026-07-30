"""
GraphRAG Research API — Server Entry Point.
Imports and exposes the FastAPI application from app.main for backward compatibility.
"""

import os

import uvicorn

from app.main import app

__all__ = ["app"]

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
        reload=os.getenv("ENV", "dev") == "dev",
        reload_excludes=["*.log", "app.log", "__pycache__/*"],
        log_level="info",
        workers=int(os.getenv("WORKERS", "1")),
    )

