import pandas as pd
import ast
import time
import uuid
import logging
import os
from supabase import create_client

# =========================================================
# 🔐 CONFIG
# =========================================================

SUPABASE_URL="https://kexzlhgcurpvlssvergg.supabase.co"
SUPABASE_KEY="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImtleHpsaGdjdXJwdmxzc3ZlcmdnIiwicm9sZSI6ImFub24iLCJpYXQiOjE3Njk2ODE4NjUsImV4cCI6MjA4NTI1Nzg2NX0.hOdElG3U9tZEeGHPF5A_t8g_KeH9k_-1P_GDTchNqak"


CSV_FILE = "ingestion/paper_chunks_rows.csv"   # ✅ fixed path
BATCH_SIZE = 500
CHECKPOINT_FILE = "ingestion/upload_checkpoint.txt"
START_FROM = 1   # ✅ your requirement

# =========================================================
# 🧾 LOGGING
# =========================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

# =========================================================
# 🔌 INIT
# =========================================================
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# =========================================================
# 🧠 HELPERS
# =========================================================
def parse_embedding(val):
    try:
        if isinstance(val, list):
            return val
        return ast.literal_eval(val)
    except:
        return None


def is_valid_uuid(val):
    try:
        uuid.UUID(str(val))
        return True
    except:
        return False


def load_checkpoint():
    try:
        with open(CHECKPOINT_FILE, "r") as f:
            return int(f.read().strip())
    except:
        return 0


def save_checkpoint(idx):
    with open(CHECKPOINT_FILE, "w") as f:
        f.write(str(idx))


# =========================================================
# 🚀 LOAD DATA
# =========================================================
df = pd.read_csv(CSV_FILE)
total_rows = len(df)

logging.info(f"📄 Loaded {total_rows} rows")

start_idx = max(load_checkpoint(), START_FROM)
logging.info(f"🚀 Starting from row {start_idx}")

# =========================================================
# 🚀 PROCESS
# =========================================================
buffer = []
seen = set()

for idx, row in enumerate(df.to_dict("records")):

    if idx < start_idx:
        continue

    try:
        research_id = str(row.get("research_id"))

        if not is_valid_uuid(research_id):
            continue

        chunk = str(row.get("chunk", "")).strip()
        if not chunk:
            continue

        chunk_index = int(row.get("chunk_index", 0))

        # 🔥 In-memory duplicate check
        unique_key = (research_id, chunk_index)
        if unique_key in seen:
            continue
        seen.add(unique_key)

        embedding = parse_embedding(row.get("embedding"))
        if embedding is None or len(embedding) != 768:
            continue

        record = {
            "research_id": research_id,
            "chunk": chunk,
            "chunk_index": chunk_index,
            "embedding": embedding,
            "section": row.get("section", "general"),
            "token_count": int(row.get("token_count", len(chunk.split())))
        }

        buffer.append(record)

        # =================================================
        # 🚀 BATCH INSERT
        # =================================================
        if len(buffer) >= BATCH_SIZE:
            try:
                supabase.table("paper_chunks").insert(buffer).execute()

                logging.info(f"✅ Uploaded {idx} / {total_rows}")
                save_checkpoint(idx)

                buffer.clear()
                time.sleep(0.2)

            except Exception as e:
                error_msg = str(e)

                if "duplicate key value violates unique constraint" in error_msg:
                    logging.warning(f"⚠️ Duplicate batch at {idx}, fallback row-by-row")

                    for rec in buffer:
                        try:
                            supabase.table("paper_chunks").insert(rec).execute()
                        except Exception as inner_e:
                            if "duplicate key value" in str(inner_e):
                                continue
                            else:
                                logging.error(f"❌ Row failed: {inner_e}")

                    buffer.clear()

                else:
                    logging.error(f"❌ Batch failed at {idx}: {e}")
                    buffer.clear()

    except Exception as e:
        logging.warning(f"⚠️ Row {idx} skipped: {e}")

# =========================================================
# 🚀 FINAL FLUSH
# =========================================================
if buffer:
    try:
        supabase.table("paper_chunks").insert(buffer).execute()
        logging.info("✅ Final batch uploaded")
    except Exception as e:
        logging.error(f"❌ Final batch failed: {e}")

logging.info("🎉 Upload completed successfully!")