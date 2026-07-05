import httpx
import json
import time

BASE = "http://localhost:8000"

queries = [
    "What are the key contributions of BERT and how does masked language modeling work?",
    "show me the reference to the attention is all you need paper",
    "what is the query key value mechanism in self attention",
]

for i, q in enumerate(queries, 1):
    print("\n" + "=" * 70)
    print(f"TEST {i}: {q}")
    print("=" * 70)
    t0 = time.time()
    try:
        r = httpx.post(
            f"{BASE}/api/research",
            json={
                "messages": [{"role": "user", "content": q}],
                "top_k": 5,
                "min_similarity": 0.3,
            },
            timeout=60.0,
        )
        elapsed = round(time.time() - t0, 2)
        if r.status_code == 200:
            d = r.json()
            print(f"[ROUTE]   {d.get('route', '?')}")
            print(f"[MODEL]   {d.get('model_used', '?')}")
            print(f"[LATENCY] {d.get('latency_ms', '?')}ms  (wall: {elapsed}s)")
            print(f"[PAPERS]  {len(d.get('papers', []))} graph | {len(d.get('arxiv_papers', []))} arxiv | {len(d.get('s2_papers', []))} s2")
            print(f"[CHUNKS]  {len(d.get('chunks', []))}")
            print(f"[WARN]    {d.get('warning', 'None')}")
            arxiv = d.get("arxiv_papers", [])
            if arxiv:
                print("[ARXIV PAPERS FOUND]")
                for p in arxiv[:3]:
                    print(f"  - {p.get('title','?')} ({str(p.get('published','?'))[:4]})")
            print("[ANSWER]")
            print(d.get("answer", "NO ANSWER")[:1500])
        else:
            print(f"HTTP ERROR {r.status_code}: {r.text[:500]}")
    except Exception as e:
        print(f"EXCEPTION: {e}")
