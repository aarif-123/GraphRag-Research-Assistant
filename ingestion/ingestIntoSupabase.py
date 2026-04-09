import csv
import ast
import uuid
import hashlib
import re
import os
import time
import logging
from queue import Queue
from threading import Thread, Lock
from supabase import create_client
from sentence_transformers import SentenceTransformer
from dotenv import load_dotenv

# =========================================================
# 🔐 ENV
# =========================================================
load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError("Missing Supabase credentials")

# =========================================================
# ⚙️ CONFIG
# =========================================================
CSV_FILE = "./ingestion/dblp-v10.csv"
CHECKPOINT_FILE = "./ingestion/checkpoint.txt"

BATCH_SIZE = 250
CHUNK_SIZE = 120
NUM_WORKERS = 4

# =========================================================
# 🧾 LOGGING
# =========================================================
LOG_FILE = "ingestion.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler()
    ]
)

log = logging.getLogger("ingestion")
logging.getLogger("httpx").setLevel(logging.WARNING)

# =========================================================
# 🔌 INIT
# =========================================================
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# ✅ FIXED MODEL LOAD (NO DOUBLE LOAD)
model_path = "./models/bge-base-en"
if os.path.exists(model_path):
    log.info("📦 Loading local model...")
    model = SentenceTransformer(model_path, device="cpu")
else:
    log.info("🌐 Loading HuggingFace model...")
    model = SentenceTransformer("BAAI/bge-base-en", device="cpu")

checkpoint_lock = Lock()

# =========================================================
# 🧠 HELPERS
# =========================================================
def clean_text(text):
    if not text:
        return ""
    return re.sub(r"\s+", " ", str(text)).strip()


def parse_list(value):
    try:
        return ast.literal_eval(value) if value else []
    except:
        return []


def chunk_text(text):
    words = text.split()
    return [" ".join(words[i:i+CHUNK_SIZE]) for i in range(0, len(words), CHUNK_SIZE)]


# =========================================================
# ✅ PAPER ID (SAFE UUID)
# =========================================================
def get_paper_id(row):
    raw_id = row.get("id")

    try:
        return str(uuid.UUID(raw_id))
    except:
        title = clean_text(row.get("title"))
        return str(uuid.uuid5(uuid.NAMESPACE_DNS, title))


# =========================================================
# ✅ FIXED CHUNK ID (NO BIGINT OVERFLOW)
# =========================================================
def get_chunk_id(paper_id, idx):
    raw = f"{paper_id}_{idx}".encode()
    h = hashlib.sha256(raw).hexdigest()

    # ✅ SAFE BIGINT RANGE
    return int(h[:16], 16) % (2**63 - 1)


# =========================================================
# 🧠 CHECKPOINT
# =========================================================
def load_checkpoint():
    if not os.path.exists(CHECKPOINT_FILE):
        log.info("📁 No checkpoint found → starting from 0")
        return 0
    try:
        with open(CHECKPOINT_FILE, "r") as f:
            idx = int(f.read().strip())
            log.info(f"🔁 Resuming from row {idx}")
            return idx
    except:
        log.warning("⚠️ Corrupted checkpoint → reset to 0")
        return 0


def save_checkpoint(idx):
    with checkpoint_lock:
        with open(CHECKPOINT_FILE, "w") as f:
            f.write(str(idx))


# =========================================================
# 🔁 STRONG RETRY (EXPONENTIAL BACKOFF)
# =========================================================
def retry(func, retries=5, base_delay=1):
    for attempt in range(retries):
        try:
            return func()
        except Exception as e:
            wait = base_delay * (2 ** attempt)
            log.warning(f"⚠️ Retry {attempt+1}/{retries} after {wait}s: {e}")
            time.sleep(wait)

    raise Exception("❌ Max retries exceeded")


# =========================================================
# 🚀 DB WORKER
# =========================================================
def db_worker(queue):
    while True:
        item = queue.get()
        if item is None:
            break

        papers_batch, chunks_batch, idx = item

        try:
            # ✅ INSERT PAPERS
            retry(lambda: supabase.table("papers").upsert(
                papers_batch,
                on_conflict="id"
            ).execute())

            # ✅ INSERT CHUNKS (SMALLER BATCHS)
            for i in range(0, len(chunks_batch), 100):
                batch = chunks_batch[i:i+100]

                retry(lambda: supabase.table("paper_chunks").upsert(
                    batch,
                    on_conflict="id"
                ).execute())

            log.info(f"✅ Row {idx} | papers={len(papers_batch)} chunks={len(chunks_batch)}")

            save_checkpoint(idx)

        except Exception:
            log.exception(f"❌ FAILED at row {idx}")

        queue.task_done()


# =========================================================
# 🚀 MAIN PIPELINE
# =========================================================
def process():
    start_idx = load_checkpoint()

    queue = Queue(maxsize=20)

    workers = []
    for _ in range(NUM_WORKERS):
        t = Thread(target=db_worker, args=(queue,))
        t.start()
        workers.append(t)

    papers_batch = []
    chunks_batch = []

    with open(CSV_FILE, encoding="utf-8") as f:
        reader = csv.DictReader(f)

        for idx, row in enumerate(reader):
            if idx < start_idx:
                continue

            try:
                title = clean_text(row.get("title"))
                abstract = clean_text(row.get("abstract"))

                if not title:
                    continue

                paper_id = get_paper_id(row)
                full_text = f"{title}. {abstract}"

                papers_batch.append({
                    "id": paper_id,
                    "title": title,
                    "abstract": abstract,
                    "venue": clean_text(row.get("venue")),
                    "year": int(row["year"]) if row.get("year") else None,
                    "n_citation": int(row["n_citation"]) if row.get("n_citation") else 0,
                    "authors": parse_list(row.get("authors")),
                    "citations": parse_list(row.get("references")),
                })

                if full_text.strip():
                    chunks = chunk_text(full_text)

                    embeddings = model.encode(
                        chunks,
                        batch_size=32,
                        convert_to_numpy=True,
                        normalize_embeddings=True,
                        show_progress_bar=False
                    )

                    for i, chunk in enumerate(chunks):
                        chunks_batch.append({
                            "id": get_chunk_id(paper_id, i),
                            "research_id": paper_id,
                            "chunk": chunk,
                            "chunk_index": i,
                            "embedding": embeddings[i].tolist(),
                        })

                if len(papers_batch) >= BATCH_SIZE:
                    queue.put((papers_batch.copy(), chunks_batch.copy(), idx))
                    papers_batch.clear()
                    chunks_batch.clear()

            except Exception:
                log.exception(f"⚠️ Row {idx} failed")

            if idx % 200 == 0:
                log.info(f"📊 Progress: {idx}")

    if papers_batch:
        queue.put((papers_batch.copy(), chunks_batch.copy(), idx))

    queue.join()

    for _ in workers:
        queue.put(None)

    for t in workers:
        t.join()

    log.info("🎉 INGESTION COMPLETED SUCCESSFULLY")


# =========================================================
# ▶ RUN
# =========================================================
if __name__ == "__main__":
    process()