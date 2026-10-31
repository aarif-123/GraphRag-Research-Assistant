"""
scripts/test_quality.py
Runs a high-value research query through the end-to-end GraphRAG pipeline,
monitors all API communications, and records the complete response for quality analysis.
"""

import asyncio
import json
import os
import sys
import time

from dotenv import load_dotenv

# Ensure UTF-8 output encoding for Windows terminal
sys.stdout.reconfigure(encoding="utf-8")

# Ensure project root is in sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

load_dotenv(os.path.join(PROJECT_ROOT, ".env.local"), override=True)
load_dotenv(os.path.join(PROJECT_ROOT, ".env"), override=False)

from starlette.requests import Request

from app.clients.pool import pool
from app.models.chat import ChatMessage, ConversationRequest
from app.routes.chat import _chat_impl

RESEARCH_QUESTION = (
    "How do Graph Neural Networks enhance Retrieval-Augmented Generation (GraphRAG) "
    "for multi-hop reasoning over complex knowledge bases compared to standard vector RAG?"
)


async def main():
    print("=== Starting End-to-End Response Quality Test ===")
    print(f"Research Query:\n\"{RESEARCH_QUESTION}\"\n")

    t_start = time.time()
    await pool.init()
    init_time = time.time() - t_start
    print(f"[Init] Pool initialized in {init_time:.2f}s. Ready: {pool._ready}")

    scope = {
        "type": "http",
        "method": "POST",
        "headers": [],
        "client": ("127.0.0.1", 54321),
    }
    request = Request(scope)
    request.state.request_id = f"qual-test-{int(time.time())}"

    req = ConversationRequest(
        messages=[ChatMessage(role="user", content=RESEARCH_QUESTION)],
        top_k=6,
        temperature=0.2,
        mode="research",
    )

    t0 = time.time()
    try:
        response = await _chat_impl(req, request)
        duration = time.time() - t0

        result_payload = {
            "question": RESEARCH_QUESTION,
            "total_latency_seconds": round(duration, 2),
            "route": response.get("route"),
            "model_used": response.get("model_used"),
            "plan": response.get("plan"),
            "arxiv_papers_count": len(response.get("arxiv_papers", [])),
            "arxiv_papers": [
                {
                    "id": p.get("id") or p.get("arxiv_id"),
                    "title": p.get("title"),
                    "year": p.get("year"),
                    "authors": p.get("authors", [])[:4],
                    "url": p.get("url") or p.get("pdf_url"),
                    "abstract_snippet": (p.get("abstract") or "")[:250],
                }
                for p in response.get("arxiv_papers", [])
            ],
            "s2_papers_count": len(response.get("s2_papers", [])),
            "datasets_count": len(response.get("datasets", [])),
            "datasets": [
                {
                    "name": d.get("name"),
                    "source": d.get("source"),
                    "url": d.get("url"),
                    "description": (d.get("description") or "")[:150],
                }
                for d in response.get("datasets", [])
            ],
            "code_repos_count": len(response.get("code_repos", [])),
            "code_repos": response.get("code_repos", []),
            "answer": response.get("answer", ""),
        }

        output_file = "quality_test_result.json"
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(result_payload, f, indent=2, ensure_ascii=False)

        print("\n=== Pipeline Execution Summary ===")
        print(f"Total Execution Time : {duration:.2f}s")
        print(f"Assigned Route       : {response.get('route')}")
        print(f"Synthesis Model      : {response.get('model_used')}")
        print(f"ArXiv Papers Found   : {len(response.get('arxiv_papers', []))}")
        print(f"Datasets Discovered  : {len(response.get('datasets', []))}")
        print(f"Code Repositories    : {len(response.get('code_repos', []))}")
        print(f"Answer Length        : {len(response.get('answer', ''))} characters")
        print(f"Detailed output saved to: {output_file}")

    except Exception as e:
        import traceback
        traceback.print_exc()
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
