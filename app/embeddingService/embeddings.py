from sentence_transformers import SentenceTransformer
import asyncio

from app.app import EmbeddingError

print("⬇️ Loading embedding model (local)...")

EMBED_MODEL = "BAAI/bge-base-en"

embed_model = SentenceTransformer(
    EMBED_MODEL,
    device="cpu"   # change to "cuda" if GPU available
)

print("✅ Embedding model loaded")





async def create_embedding(query: str) -> List[float]:
    try:
        # Run blocking model in thread (IMPORTANT for FastAPI async)
        emb = await asyncio.to_thread(
            embed_model.encode,
            query,
            normalize_embeddings=True
        )

        return emb.tolist()

    except Exception as e:
        raise EmbeddingError(f"Local embedding failed: {e}")