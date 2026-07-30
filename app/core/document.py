import asyncio
import re
import base64
import hashlib
import json
import zlib
from typing import List, Tuple, Dict, Any, Optional

import httpx
import numpy as np

# Optional import fitz (PyMuPDF)
try:
    import fitz
except ImportError:
    fitz = None

from app.config import MAX_PDF_BYTES, _MB, _REDIS_MAX_PAYLOAD_BYTES, log
from app.clients.pool import pool, cache_key, get_cache, set_cache, upstash_redis, local_chunks_cache, local_embeddings_cache
from app.clients.groq import create_embedding, create_embeddings_batch


def extract_paper_urls(text: str) -> List[str]:
    # Regex to match URLs
    urls = re.findall(r"https?://[^\s]+", text)
    paper_urls = []
    for url in urls:
        # Strip trailing punctuation
        url = url.rstrip(".,;()[]{}")
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
    cleaned = re.sub(r"[^a-zA-Z0-9\s]", "", cleaned).strip().lower()
    if len(cleaned) < 10:
        return True

    words = cleaned.split()
    summary_words = {
        "summarize",
        "summarise",
        "summary",
        "parse",
        "read",
        "pdf",
        "paper",
        "analyze",
        "analyse",
        "this",
        "explain",
        "about",
        "what",
        "is",
        "intro",
        "introduction",
    }
    if all(w in summary_words for w in words):
        return True

    return False


async def parse_pdf_from_url(url: str) -> Tuple[str, List[str]]:
    # Local uploaded PDF bypass
    if "/api/pdf/" in url:
        try:
            import app.clients.pool as pool_mod
            pdf_id = url.split("/api/pdf/")[-1].replace(".pdf", "")
            doc = await asyncio.to_thread(pool_mod.db.uploaded_pdfs.find_one, {"_id": pdf_id})
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
        # Reject oversized remote PDFs before buffering into memory.
        content_length = int(r.headers.get("content-length", 0))
        if content_length and content_length > MAX_PDF_BYTES:
            raise Exception(
                f"Remote PDF is too large ({content_length // _MB} MB). "
                f"Maximum allowed is {MAX_PDF_BYTES // _MB} MB."
            )
        pdf_bytes = r.content
        # Guard against servers that omit Content-Length (chunked transfer).
        if len(pdf_bytes) > MAX_PDF_BYTES:
            raise Exception(
                f"Remote PDF is too large ({len(pdf_bytes) // _MB} MB). "
                f"Maximum allowed is {MAX_PDF_BYTES // _MB} MB."
            )

    def _parse():
        if fitz is None:
            raise Exception(
                "PDF parsing is unavailable: PyMuPDF (fitz) is not installed in this environment."
            )
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


def chunk_document_text(
    text: str,
    chunk_size: int = 1000,
    chunk_overlap: int = 200,
) -> List[str]:
    """Split raw document text into overlapping chunks.

    Pure-Python equivalent of LangChain's
    ``RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)``.
    Uses the same separator hierarchy (double-newline -> newline -> space ->
    character) with no third-party dependencies, making it safe on Vercel.
    """
    if not text:
        return []

    # Separator hierarchy mirrors LangChain RecursiveCharacterTextSplitter
    separators = ["\n\n", "\n", " ", ""]

    def _split(t: str, seps: List[str]) -> List[str]:
        if not t:
            return []
        sep = seps[0]
        next_seps = seps[1:]

        if sep:
            parts = t.split(sep)
        else:
            # Character-level fallback: emit fixed-size slices
            step = max(chunk_size - chunk_overlap, 1)
            return [t[i : i + chunk_size] for i in range(0, len(t), step)]

        chunks: List[str] = []
        current = ""
        for part in parts:
            joined = (current + sep + part).lstrip(sep) if current else part
            if len(joined) <= chunk_size:
                current = joined
            else:
                if current:
                    chunks.append(current)
                if len(part) > chunk_size and next_seps:
                    chunks.extend(_split(part, next_seps))
                else:
                    current = part
        if current:
            chunks.append(current)
        return chunks

    raw_chunks = _split(text, separators)

    # Apply overlap window: prefix each chunk with the tail of the previous one.
    if chunk_overlap <= 0 or len(raw_chunks) <= 1:
        return [c for c in raw_chunks if c.strip()]

    result: List[str] = [raw_chunks[0]]
    for chunk in raw_chunks[1:]:
        prev_tail = result[-1][-chunk_overlap:]
        candidate = (prev_tail + " " + chunk) if prev_tail else chunk
        # Guard: never let the overlap push a chunk wildly over budget
        result.append(candidate if len(candidate) <= int(chunk_size * 1.5) else chunk)

    return [c for c in result if c.strip()]


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

        # Save to Upstash Redis (base64 encoded) — skip if payload exceeds safe limit.
        if upstash_redis:
            try:
                serialized = json.dumps(chunks).encode("utf-8")
                compressed = zlib.compress(serialized)
                b64_str = base64.b64encode(compressed).decode("utf-8")
                if len(b64_str) <= _REDIS_MAX_PAYLOAD_BYTES:
                    upstash_redis.set(redis_key, b64_str, ex=24 * 3600)  # 24 hour TTL
                    log.info(f"Cached {len(chunks)} chunks for PDF {url} in Upstash Redis.")
                else:
                    log.warning(
                        f"PDF chunk payload too large for Redis "
                        f"({len(b64_str)} bytes > {_REDIS_MAX_PAYLOAD_BYTES} limit). "
                        "Using in-memory cache only."
                    )
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

            # Save generated embeddings to Redis and in-memory cache — skip if too large.
            if upstash_redis:
                try:
                    serialized_emb = json.dumps(embeddings).encode("utf-8")
                    compressed_emb = zlib.compress(serialized_emb)
                    b64_emb_str = base64.b64encode(compressed_emb).decode("utf-8")
                    if len(b64_emb_str) <= _REDIS_MAX_PAYLOAD_BYTES:
                        upstash_redis.set(redis_emb_key, b64_emb_str, ex=24 * 3600)  # 24 hour TTL
                        log.info(
                            f"Cached {len(embeddings)} chunk embeddings for PDF {url} in Upstash Redis."
                        )
                    else:
                        log.warning(
                            f"PDF embedding payload too large for Redis "
                            f"({len(b64_emb_str)} bytes > {_REDIS_MAX_PAYLOAD_BYTES} limit). "
                            "Using in-memory cache only."
                        )
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
        log.info(
            f"Retrieved top {len(relevant_chunks)} relevant PDF chunks via numpy cosine similarity."
        )
        return relevant_chunks

    except Exception as e:
        log.error(f"Error in numpy similarity search for PDF: {e}")
        # Fallback to returning the first 5 chunks if similarity search fails
        return chunks[:5]


def is_whisper_hallucination(text: str) -> bool:
    if not text or not text.strip():
        return True
    # Strip punctuation and lowercase
    cleaned = "".join(c for c in text if c.isalnum()).lower().strip()
    if len(cleaned) <= 1:
        return True
    hallucinations = {
        "",
        "you",
        "thankyou",
        "thankyouforwatching",
        "pleaselikeandsubscribe",
        "subscribe",
        "watching",
        "bye",
        "thankyoubye",
        "thankyousomuch",
        "amaraorg",
        "subtitlesby",
        "captionedby",
        "translatedby",
        "mb",
        "so",
        "oh",
        "uh",
        "um",
    }
    if cleaned in hallucinations:
        return True

    # Check subtitle/caption artifact patterns
    lower = text.lower().strip()
    if any(pat in lower for pat in [
        "subtitles by", "captioned by", "amara.org", "thanks for watching",
        "please subscribe", "like and subscribe", "thank you for watching",
        "copyright", "all rights reserved"
    ]):
        return True
    return False
