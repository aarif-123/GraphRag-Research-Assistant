"""
Groq LLM Client and Embeddings Generator Module.
Provides wrappers for Groq chat model calls, key rotation, and BGE embedding generation.
"""

import asyncio
from typing import Dict, List

import httpx
import numpy as np

try:
    from sentence_transformers import SentenceTransformer
except ImportError:
    SentenceTransformer = None

from app.clients.pool import cache_key, get_cache, pool, set_cache
from app.config import (
    EMBED_MODEL,
    EMBED_TIMEOUT,
    FREEZE_RETRIEVAL,
    GROQ_API_KEY,
    GROQ_API_KEYS,
    HF_TOKEN,
    log,
)
from app.core.exceptions import EmbeddingError, LLMError

# ================================================================
# LOCAL EMBEDDING MODEL INITIALIZATION
# ================================================================
embed_model = None
if SentenceTransformer is None:
    log.warning("sentence-transformers not installed; falling back to HuggingFace API embeddings")
else:
    try:
        log.info("Loading embedding model...")
        embed_model = SentenceTransformer(EMBED_MODEL, device="cpu")
        log.info("Embedding model ready")
    except Exception as exc:
        log.warning(
            f"Local embedding model unavailable ({exc}); falling back to HuggingFace API embeddings"
        )

# ================================================================
# GROQ KEY ROTATION
# ================================================================
groq_key_index = 0


def get_current_groq_key() -> str:
    global groq_key_index
    if not GROQ_API_KEYS:
        return GROQ_API_KEY or ""
    return GROQ_API_KEYS[groq_key_index % len(GROQ_API_KEYS)]


def rotate_groq_key():
    global groq_key_index
    if GROQ_API_KEYS:
        groq_key_index = (groq_key_index + 1) % len(GROQ_API_KEYS)
        log.info(f"Rotated to Groq API Key index {groq_key_index}")


# ================================================================
# LLM WRAPPERS AND UTILITIES
# ================================================================
def compress_rag_prompt(content: str) -> str:
    """
    Compresses RAG prompt by keeping the main points:
    - Truncates long abstracts (under '  Abstract: ') to the first 120 characters + [...]
    - Truncates long chunk texts (in '=== RETRIEVED CHUNK EVIDENCE ===') to the first 150 characters + [...]
    """
    new_lines = []
    in_chunks = False
    for line in content.splitlines():
        if "=== RETRIEVED CHUNK EVIDENCE ===" in line:
            in_chunks = True
            new_lines.append(line)
            continue
        if in_chunks and (line.startswith("━━━") or line.startswith("═══") or "QUERY" in line):
            in_chunks = False

        if in_chunks:
            # If it's a chunk header line (like [1] Title | sim=0.85)
            if line.strip().startswith("[") and " | " in line:
                new_lines.append(line)
            elif line.strip():
                # Compress the chunk body text
                stripped = line.strip()
                if len(stripped) > 150:
                    indent = len(line) - len(line.lstrip())
                    new_lines.append(" " * indent + stripped[:150] + " [...]")
                else:
                    new_lines.append(line)
            else:
                new_lines.append(line)
        else:
            # Outside chunk section, check for Abstract
            if line.startswith("  Abstract: "):
                abstract_text = line[12:]
                if len(abstract_text) > 120:
                    new_lines.append("  Abstract: " + abstract_text[:120] + " [...]")
                else:
                    new_lines.append(line)
            else:
                new_lines.append(line)
    return "\n".join(new_lines)


def truncate_messages(messages: List[Dict], max_total_chars: int = 12000) -> List[Dict]:
    """
    Finds the longest message in the list and truncates it so that the sum of
    all message contents is <= max_total_chars.
    Attempts to compress RAG context intelligently first (keeping main points),
    then falls back to character-level slicing if still over limit.
    """
    total_chars = sum(len(m.get("content", "")) for m in messages)
    if total_chars <= max_total_chars:
        return messages

    # Find the index of the longest message
    longest_idx = -1
    longest_len = -1
    for i, m in enumerate(messages):
        content_len = len(m.get("content", ""))
        if content_len > longest_len:
            longest_len = content_len
            longest_idx = i

    if longest_idx == -1 or longest_len == 0:
        return messages

    truncated_messages = [dict(m) for m in messages]
    content = truncated_messages[longest_idx]["content"]

    # 1. Try smart RAG prompt compression
    compressed_content = compress_rag_prompt(content)

    # 2. If smart compression reduced the size, use it
    if len(compressed_content) < len(content):
        truncated_messages[longest_idx]["content"] = compressed_content
        # Recalculate total characters to see if we need further character-level truncation
        new_total = sum(len(m.get("content", "")) for m in truncated_messages)
        if new_total <= max_total_chars:
            return truncated_messages
        # If still too large, update variables and do character-level fallback
        content = compressed_content
        total_chars = new_total
        longest_len = len(content)

    # 3. Fallback character-level truncation
    suffix = "\n\n[... Context truncated due to rate/size limits ...]"
    excess = total_chars - max_total_chars + len(suffix)
    target_len = max(0, longest_len - excess)
    truncated_messages[longest_idx]["content"] = content[:target_len] + suffix
    return truncated_messages


async def groq_chat(
    messages: List[Dict],
    model: str,
    temperature: float = 0.1,
    max_tokens: int = 1024,
    retries: int = 2,
    json_mode: bool = False,
    purpose: str = "",
) -> str:
    ck = None
    if temperature == 0.0:
        ck = cache_key(str(messages), model, max_tokens)
        cached = get_cache("llm", ck)
        if cached:
            log.debug("LLM cache hit")
            return cached

    # Basic input checks
    if not messages:
        raise LLMError("Cannot call LLM with empty messages list")

    max_attempts = max(retries + 1, len(GROQ_API_KEYS))
    last_err = ""

    for attempt in range(max_attempts):
        # We start rotation offsets to avoid key collisions on rate limits
        if GROQ_API_KEYS:
            start_idx = 1 if len(GROQ_API_KEYS) > 1 else 0
            if "plan" in purpose or "strategic" in purpose:
                start_idx = 2 if len(GROQ_API_KEYS) > 2 else (1 if len(GROQ_API_KEYS) > 1 else 0)
            elif "graph" in purpose:
                start_idx = 1 if len(GROQ_API_KEYS) > 1 else 0
            elif "research" in purpose:
                start_idx = 2 if len(GROQ_API_KEYS) > 2 else (1 if len(GROQ_API_KEYS) > 1 else 0)
            else:
                start_idx = 0

        current_key = GROQ_API_KEY or ""
        if GROQ_API_KEYS:
            key_idx = (start_idx + attempt) % len(GROQ_API_KEYS)
            current_key = GROQ_API_KEYS[key_idx]

        headers = {
            "Authorization": f"Bearer {current_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        try:
            r = await pool.groq_http.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers=headers,
                json=payload,
            )
            # Handle too large errors with key rotation
            if r.status_code == 413:
                if len(GROQ_API_KEYS) > 1 and attempt < len(GROQ_API_KEYS) - 1:
                    log.warning(
                        f"Groq API returned too large error. Retrying with next key ({attempt + 1}/{len(GROQ_API_KEYS)})..."
                    )
                    continue
                # Try message list length compression as backup
                messages = truncate_messages(messages, max_total_chars=8000)
                return await groq_chat(
                    messages=messages,
                    model=model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    retries=retries,
                    json_mode=json_mode,
                    purpose=purpose,
                )

            if r.status_code == 429:
                # If rotation is possible, do it immediately
                if len(GROQ_API_KEYS) > 1 and attempt < len(GROQ_API_KEYS) - 1:
                    log.warning("Groq API returned 429. Retrying with next key...")
                    continue
                # Fallback: parse retry-after / backoff
                wait = min(float(r.headers.get("retry-after", "1")), 5.0)
                log.warning(f"Groq 429 — wait {wait}s")
                await asyncio.sleep(wait)
                continue

            if r.status_code != 200:
                if len(GROQ_API_KEYS) > 1 and attempt < len(GROQ_API_KEYS) - 1:
                    log.warning(f"Groq HTTP {r.status_code} on key. Retrying with next key...")
                    continue
                raise LLMError(f"Groq HTTP {r.status_code}: {r.text[:300]}")

            res = r.json()
            out = res["choices"][0]["message"]["content"]
            if ck:
                set_cache("llm", ck, out)
            return out

        except httpx.HTTPError as he:
            if len(GROQ_API_KEYS) > 1 and attempt < len(GROQ_API_KEYS) - 1:
                log.warning(f"Groq HTTP connection error: {he}. Retrying with next key...")
                continue
            last_err = str(he)
            await asyncio.sleep(0.5)

        except Exception as e:
            last_err = str(e)
            if len(GROQ_API_KEYS) > 1 and attempt < len(GROQ_API_KEYS) - 1:
                log.warning(f"Groq generic error: {e}. Retrying with next key...")
                continue
            await asyncio.sleep(0.5)

    raise LLMError(f"Groq failed after {max_attempts} attempts: {last_err}")


# ================================================================
# EMBEDDING GENERATOR AND HELPERS
# ================================================================
def _bge_normalize(vec: List[float]) -> List[float]:
    """L2-normalize so cosine_sim(a,b) == np.dot(a,b) for unit vectors."""
    arr = np.array(vec, dtype=np.float32)
    norm = np.linalg.norm(arr)
    return (arr / norm).tolist() if norm > 0.0 else vec


async def create_embedding(text: str, bypass_freeze: bool = False) -> List[float]:
    """
    Convert text to a BAAI/bge-base-en embedding vector.
    """
    if FREEZE_RETRIEVAL and not bypass_freeze:
        log.debug("Database retrieval is frozen. Skipping embedding generation.")
        return [0.0] * 768

    query_text = f"Represent this sentence for searching relevant passages: {text}"

    ck = cache_key(query_text)
    cached = get_cache("embed", ck)
    if cached:
        return cached

    # Primary: local SentenceTransformer
    if embed_model is not None:
        try:
            emb = await asyncio.to_thread(
                embed_model.encode,
                query_text,
                normalize_embeddings=True,
            )
            result = emb.tolist()
            set_cache("embed", ck, result)
            log.debug("Embedding via: local BAAI/bge-base-en")
            return result
        except Exception as exc:
            log.warning(f"Local BGE model failed, using HF API: {exc}")

    # Vercel path: HuggingFace Inference API
    if not HF_TOKEN:
        raise EmbeddingError(
            "HF_TOKEN not set. Required for BAAI/bge-base-en embeddings on Vercel."
        )

    try:
        url = f"https://router.huggingface.co/hf-inference/models/{EMBED_MODEL}/pipeline/feature-extraction"
        headers = {"Authorization": f"Bearer {HF_TOKEN}"}
        payload = {"inputs": query_text}

        async with httpx.AsyncClient(timeout=httpx.Timeout(EMBED_TIMEOUT, connect=5.0)) as client:
            resp = await client.post(url, headers=headers, json=payload)

            if resp.status_code == 503:
                wait = min(int(resp.headers.get("Retry-After", "5")), 10)
                await asyncio.sleep(wait)
                resp = await client.post(url, headers=headers, json=payload)

            if resp.status_code != 200:
                raise EmbeddingError(f"HF embedding HTTP {resp.status_code}: {resp.text[:300]}")

            data = resp.json()

        if isinstance(data, list) and data and isinstance(data[0], list):
            raw = [float(x) for x in data[0]]
        elif isinstance(data, list):
            raw = [float(x) for x in data]
        else:
            raise EmbeddingError("Unexpected HF API response format")

        result = _bge_normalize(raw)
        set_cache("embed", ck, result)
        log.debug("Embedding via: BAAI/bge-base-en HF Inference API (L2-normalized)")
        return result
    except EmbeddingError:
        raise
    except Exception as exc:
        raise EmbeddingError(f"BAAI/bge-base-en HF API embedding failed: {exc}")


async def create_embeddings_batch(
    texts: List[str], bypass_freeze: bool = False
) -> List[List[float]]:
    """
    Convert a list of texts to BAAI/bge-base-en embedding vectors.
    """
    if not texts:
        return []

    if FREEZE_RETRIEVAL and not bypass_freeze:
        log.debug("Database retrieval is frozen. Skipping batch embedding generation.")
        return [[0.0] * 768 for _ in texts]

    results = [None] * len(texts)
    missing_indices = []
    missing_query_texts = []

    for i, text in enumerate(texts):
        query_text = f"Represent this sentence for searching relevant passages: {text}"
        ck = cache_key(query_text)
        cached = get_cache("embed", ck)
        if cached:
            results[i] = cached
        else:
            missing_indices.append(i)
            missing_query_texts.append((query_text, ck))

    if not missing_indices:
        return results

    if embed_model is not None:
        try:
            raw_texts = [item[0] for item in missing_query_texts]
            embs = await asyncio.to_thread(
                embed_model.encode, raw_texts, normalize_embeddings=True, show_progress_bar=False
            )
            for idx, raw_idx in enumerate(missing_indices):
                emb_list = embs[idx].tolist()
                set_cache("embed", missing_query_texts[idx][1], emb_list)
                results[raw_idx] = emb_list
            return results
        except Exception as exc:
            log.warning(f"Local BGE batch encoding failed, falling back to HF API: {exc}")

    if not HF_TOKEN:
        raise EmbeddingError(
            "HF_TOKEN not set. Required for BAAI/bge-base-en embeddings on Vercel."
        )

    batch_size = 16
    url = f"https://router.huggingface.co/hf-inference/models/{EMBED_MODEL}/pipeline/feature-extraction"
    headers = {"Authorization": f"Bearer {HF_TOKEN}"}

    async with httpx.AsyncClient(timeout=httpx.Timeout(EMBED_TIMEOUT, connect=5.0)) as client:
        for offset in range(0, len(missing_query_texts), batch_size):
            batch = missing_query_texts[offset : offset + batch_size]
            batch_inputs = [item[0] for item in batch]
            payload = {"inputs": batch_inputs}

            try:
                resp = await client.post(url, headers=headers, json=payload)
                if resp.status_code == 503:
                    wait = min(int(resp.headers.get("Retry-After", "5")), 10)
                    await asyncio.sleep(wait)
                    resp = await client.post(url, headers=headers, json=payload)

                if resp.status_code != 200:
                    raise EmbeddingError(f"HF embedding HTTP {resp.status_code}: {resp.text[:300]}")

                data = resp.json()

                if isinstance(data, list) and len(data) > 0:
                    if (
                        isinstance(data[0], list)
                        and len(data[0]) > 0
                        and isinstance(data[0][0], list)
                    ):
                        data = [item[0] for item in data]

                    for idx, emb in enumerate(data):
                        norm_emb = _bge_normalize(emb)
                        raw_idx = missing_indices[offset + idx]
                        ck = batch[idx][1]
                        set_cache("embed", ck, norm_emb)
                        results[raw_idx] = norm_emb
                else:
                    raise EmbeddingError(f"Unexpected response format from HF API: {type(data)}")

            except Exception as e:
                log.error(f"Error in HuggingFace batch embedding call: {e}")
                raise EmbeddingError(f"HuggingFace batch embedding failed: {str(e)}")

    for i in range(len(results)):
        if results[i] is None:
            results[i] = [0.0] * 768

    return results
