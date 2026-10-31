import asyncio
import json
import os
import sys
import time

from dotenv import load_dotenv

sys.stdout.reconfigure(encoding="utf-8")

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

load_dotenv(os.path.join(PROJECT_ROOT, ".env.local"), override=True)
load_dotenv(os.path.join(PROJECT_ROOT, ".env"), override=False)

from starlette.requests import Request

from app.clients.pool import pool
from app.models.chat import ChatMessage, ConversationRequest
from app.routes.chat import _chat_impl

# High value research question without punctuation that breaks arXiv syntax
RESEARCH_QUESTION = "Graph Neural Networks for Retrieval-Augmented Generation and multi-hop reasoning"

async def run_eval():
    print("=== Research Question Quality & Grounding Evaluation ===")
    print(f"Query: \"{RESEARCH_QUESTION}\"\n")

    await pool.init()

    scope = {
        "type": "http",
        "method": "POST",
        "headers": [],
        "client": ("127.0.0.1", 54321),
    }
    request = Request(scope)
    request.state.request_id = f"eval-{int(time.time())}"

    req = ConversationRequest(
        messages=[ChatMessage(role="user", content=RESEARCH_QUESTION)],
        top_k=5,
        temperature=0.2,
        mode="research"
    )

    t0 = time.time()
    res = await _chat_impl(req, request)
    total_time = time.time() - t0

    papers = res.get("arxiv_papers", [])
    print(f"Total Latency: {total_time:.2f}s")
    print(f"Route: {res.get('route')}")
    print(f"Model: {res.get('model_used')}")
    print(f"ArXiv Papers Retrieved: {len(papers)}")
    for i, p in enumerate(papers, 1):
        print(f"  {i}. [{p.get('published', '')[:4]}] {p.get('title')} (arXiv:{p.get('arxiv_id')})")
        print(f"     Authors: {', '.join(p.get('authors', [])[:3])}")

    datasets = res.get("datasets", [])
    print(f"\nDiscovered Datasets: {len(datasets)}")
    for d in datasets[:3]:
        print(f"  - {d.get('name')} ({d.get('source')}): {d.get('url')}")

    answer = res.get("answer", "")
    print(f"\nGenerated Answer Length: {len(answer)} chars")

    with open("eval_grounded_result.json", "w", encoding="utf-8") as f:
        json.dump({
            "query": RESEARCH_QUESTION,
            "latency": total_time,
            "papers": papers,
            "datasets": datasets,
            "answer": answer
        }, f, indent=2, ensure_ascii=False)

    await pool.close()

if __name__ == "__main__":
    asyncio.run(run_eval())
