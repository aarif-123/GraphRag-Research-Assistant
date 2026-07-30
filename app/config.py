"""
Configuration and Environmental Variable Module.
Handles environment file loading, fallbacks, and static constants.
"""

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

# Load environmental variables
load_dotenv(".env.local", override=True)
load_dotenv(".env", override=False)
os.environ["HF_HUB_OFFLINE"] = "1"

# Setup Logging
_log_handlers = [logging.StreamHandler()]
if not os.getenv("VERCEL"):
    log_dir = Path(".logs")
    log_dir.mkdir(exist_ok=True)
    _log_handlers.insert(0, logging.FileHandler(log_dir / "app.log", encoding="utf-8"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    handlers=_log_handlers,
)
log = logging.getLogger("aether")


# Required default values for fallback
_REQUIRED_DEFAULTS = {
    "SUPABASE_URL": "https://dummy.supabase.co",
    "SUPABASE_SERVICE_ROLE_KEY": "dummy_service_role_key",
    "NEO4J_URI": "bolt://localhost:7687",
    "NEO4J_USER": "neo4j",
    "NEO4J_PASSWORD": "dummy_password",
}
for _v, _default in _REQUIRED_DEFAULTS.items():
    if not os.getenv(_v):
        os.environ[_v] = _default

# Supabase Configurations
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

# Razorpay Configurations
RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "")

# MongoDB Configurations
MONGODB_URI = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
MONGODB_DB_NAME = os.getenv("MONGODB_DB_NAME", "aether_research_assistant")

# JWT configurations
JWT_SECRET = os.getenv("JWT_SECRET", "super-secret-aether-key-change-in-prod")
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_HOURS = 24 * 7  # 1 week

# Neo4j Configurations
NEO4J_URI = os.getenv("NEO4J_URI")
NEO4J_USER = os.getenv("NEO4J_USER")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")

# Groq Configurations
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_API_KEYS = [k.strip() for k in GROQ_API_KEY.split(",") if k.strip()] if GROQ_API_KEY else []

# HuggingFace Configurations
HF_TOKEN = os.getenv("HF_TOKEN")

# Model Names
EMBED_MODEL = os.getenv("EMBED_MODEL", "BAAI/bge-base-en")
REASON_MODEL = os.getenv("REASON_MODEL", "openai/gpt-oss-20b")
HEAVY_MODEL = os.getenv("HEAVY_MODEL", "llama-3.3-70b-versatile")
PLAN_MODEL = os.getenv("PLAN_MODEL", "openai/gpt-oss-20b")  # strategic brain

# Feature Flags & Parameters
FREEZE_RETRIEVAL = os.getenv("FREEZE_RETRIEVAL", "false").lower() == "true"
MAX_GRAPH_NODES = int(os.getenv("MAX_GRAPH_NODES", "25"))
GROQ_TIMEOUT = int(os.getenv("GROQ_TIMEOUT", "30"))
EMBED_TIMEOUT = int(os.getenv("EMBED_TIMEOUT", "20"))
RATE_LIMIT = int(os.getenv("RATE_LIMIT_PER_MIN", "30"))
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "60"))
RELEVANCE_FLOOR = float(os.getenv("RELEVANCE_FLOOR", "0.22"))
MMR_LAMBDA = float(os.getenv("MMR_LAMBDA", "0.6"))  # diversity weight
CACHE_TTL = int(os.getenv("CACHE_TTL", "300"))  # 5 min
CACHE_MAX = int(os.getenv("CACHE_MAX", "512"))

# Size limit configurations
_MB = 1024 * 1024
MAX_PDF_BYTES = int(os.getenv("MAX_PDF_SIZE_MB", "20")) * _MB
MAX_AUDIO_BYTES = int(os.getenv("MAX_AUDIO_SIZE_MB", "24")) * _MB
_MAX_PDF_TEXT_CHARS = 10 * _MB
_REDIS_MAX_PAYLOAD_BYTES = 900 * 1024

# Pricing and Credits
FREE_CREDITS_PER_DAY = int(os.getenv("FREE_CREDITS_PER_DAY", "20"))
FREE_TOP_K_MAX = 8
PRO_TOP_K_MAX = 20

CREDIT_COSTS = {
    "query": 1,  # /api/research
    "chat": 1,  # /api/chat
    "timeline": 3,  # /api/research/timeline
    "compare": 3,  # /api/graph/compare
    "pdf": 5,  # PDF upload
}

# CORS origins allowed
ALLOWED_ORIGINS = [
    o.strip()
    for o in os.getenv(
        "CORS_ORIGINS",
        "http://localhost:3000,http://localhost:5173,http://localhost:5500,http://127.0.0.1:5500",
    ).split(",")
    if o.strip()
]

# Upstash Redis Configuration
UPSTASH_REDIS_REST_URL = os.getenv("UPSTASH_REDIS_REST_URL")
UPSTASH_REDIS_REST_TOKEN = os.getenv("UPSTASH_REDIS_REST_TOKEN")

# SMTP / Email configurations
SMTP_HOST = os.getenv("SMTP_HOST")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
SMTP_FROM = os.getenv("SMTP_FROM", "Aether Intelligence <no-reply@aether.com>")
REQUIRE_EMAIL_VERIFICATION = os.getenv("REQUIRE_EMAIL_VERIFICATION", "true").lower() == "true"
MAILBOXLAYER_API_KEY = os.getenv("MAILBOXLAYER_API_KEY")

# Stripe Configs
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
