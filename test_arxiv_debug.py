"""
Verify the NEW arXiv query logic works correctly.
Run with: python test_arxiv_debug.py
"""
import asyncio
import urllib.parse
import httpx
import xml.etree.ElementTree as ET

_NL_STOPWORDS = {
    "what", "how", "does", "do", "show", "explain", "describe", "tell",
    "find", "give", "list", "can", "you", "me", "my", "please",
    "is", "are", "a", "an", "the", "to", "for", "with", "about",
    "from", "by", "at", "on", "it", "its", "this", "that",
    "which", "where", "when", "who", "why",
    "some", "any", "more", "recent", "related", "information",
    "paper", "papers", "work", "works", "reference", "references",
}

def build_arxiv_url(query: str, limit: int = 5) -> tuple[str, str]:
    """Replicate the new logic from app.py."""
    clean_query = query.replace('"', '').replace("'", "").replace("?", "").strip()
    words = clean_query.split()
    if len(words) > 5:
        keywords = [w for w in words if w.lower() not in _NL_STOPWORDS and len(w) > 2]
        search_term = " ".join(keywords[:8]) if keywords else " ".join(words[:6])
    else:
        search_term = clean_query

    if len(search_term.split()) <= 5:
        encoded_query = urllib.parse.quote(f'all:"{search_term}"')
    else:
        encoded_query = urllib.parse.quote(f'ti:{search_term} OR abs:{search_term}')

    url = f"https://export.arxiv.org/api/query?search_query={encoded_query}&max_results={limit}&sortBy=relevance"
    return url, search_term


async def test_query(original: str, limit: int = 5):
    url, search_term = build_arxiv_url(original, limit)
    print(f"\n{'='*65}")
    print(f"Original : {original}")
    print(f"Extracted: {search_term}")
    print(f"URL      : {url}")

    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get(url)
    root = ET.fromstring(r.content)
    ns = {'atom': 'http://www.w3.org/2005/Atom'}
    total = root.find('opensearch:totalResults', {'opensearch': 'http://a9.com/-/spec/opensearch/1.1/'})
    entries = root.findall('atom:entry', ns)
    print(f"Results  : {total.text if total is not None else '?'} total, {len(entries)} returned")
    for i, e in enumerate(entries[:3]):
        t = e.find('atom:title', ns)
        p = e.find('atom:published', ns)
        print(f"  [{i+1}] {t.text.strip()[:75] if t is not None else '?'} ({str(p.text)[:4] if p is not None else '?'})")


async def main():
    queries = [
        # Raw NL questions (as user types them)
        "What are the key contributions of BERT and how does masked language modeling work?",
        "show me the reference to the attention is all you need paper",
        "what is the query key value mechanism in self attention",
        # Pre-extracted keywords (as plan.vector_keywords would produce)
        "BERT masked language modeling",
        "attention is all you need",
        "query key value self-attention transformer",
    ]
    for q in queries:
        await test_query(q)

if __name__ == "__main__":
    asyncio.run(main())
