# 🔬 Aether GraphRAG Research Assistant

> **Graph-Augmented Retrieval with Multi-Source Academic Enrichment, Anti-Hallucination Verification, and a Self-Hosted ArXiv MCP Server**
>
> Ask research questions — get grounded, cited answers enriched with live citation stats, GitHub code repos, and dataset links — powered by a Neo4j knowledge graph, Supabase pgvector, MongoDB Atlas, and Groq LLMs.

<div align="center">

[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688?style=flat-square&logo=fastapi)](https://fastapi.tiangolo.com)
[![Neo4j](https://img.shields.io/badge/Neo4j-Aura-4581C3?style=flat-square&logo=neo4j)](https://neo4j.com/cloud/aura/)
[![Supabase](https://img.shields.io/badge/Supabase-pgvector-3ECF8E?style=flat-square&logo=supabase)](https://supabase.com)
[![MongoDB](https://img.shields.io/badge/MongoDB-Atlas-47A248?style=flat-square&logo=mongodb)](https://www.mongodb.com/atlas)
[![Groq](https://img.shields.io/badge/Groq-LLM-F55036?style=flat-square)](https://groq.com)
[![License](https://img.shields.io/badge/License-Apache_2.0-blue?style=flat-square)](LICENSE)
[![CI](https://github.com/<your-username>/GraphRag-Research-Assistant/actions/workflows/ci.yml/badge.svg)](https://github.com/<your-username>/GraphRag-Research-Assistant/actions/workflows/ci.yml)

</div>

> [!NOTE]
> **Neo4j and Supabase are integrated but currently paused** (`FREEZE_RETRIEVAL=true`).
> The system runs in external-API-only mode. All graph and vector retrieval code is preserved and can be re-enabled by removing that flag when the databases are back online.

---

## 📋 Table of Contents

- [Quick Start](#-quick-start)
- [Running Tests](#-running-tests)
- [CI/CD](#-cicd)
- [Overview](#-overview)
- [System Architecture](#-system-architecture)
  - [High-Level Architecture](#high-level-architecture)
  - [Request Lifecycle Flow](#request-lifecycle-flow)
  - [Retrieval Pipeline](#retrieval-pipeline)
  - [Data Ingestion Pipeline](#data-ingestion-pipeline)
- [Features](#-features)
- [Tech Stack](#-tech-stack)
- [Project Structure](#-project-structure)
- [Prerequisites](#-prerequisites)
- [Setup & Installation](#-setup--installation)
- [Environment Variables](#-environment-variables)
- [Running the Project](#-running-the-project)
- [API Reference](#-api-reference)
- [Anti-Hallucination Pipeline](#-anti-hallucination-pipeline)
- [Credit & Subscription System](#-credit--subscription-system)
- [Deployment](#-deployment)
- [Troubleshooting](#-troubleshooting)

---

## ⚡ Quick Start

```bash
# 1. Clone
git clone https://github.com/your-username/GraphRag-Research-Assistant.git
cd GraphRag-Research-Assistant

# 2. Create and activate a virtual environment
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS / Linux:
source .venv/bin/activate

# 3. Install production dependencies
pip install -r requirements.txt

# 4. Configure environment
cp .env.example .env.local
# Edit .env.local and fill in your real credentials (Supabase, Neo4j, Groq, etc.)

# 5. Validate your configuration before starting
python scripts/validate_env.py --env-file .env.local

# 6. Start the development server
uvicorn app._server:app --reload --host 0.0.0.0 --port 8000
```

> [!IMPORTANT]
> `scripts/validate_env.py` will exit with a clear error report if any required variable is missing or misconfigured. Always run it after editing `.env.local`.

---

## 🧪 Running Tests

Install dev dependencies first:

```bash
pip install -r requirements-dev.txt
```

**Unit tests** (no network, no API keys needed):

```bash
pytest tests/unit/ -v
```

**Integration tests** (HTTP is mocked — no real API calls):

```bash
pytest tests/integration/ -v
```

**All tests at once:**

```bash
pytest tests/ -v
```

**What's covered:**

| Suite | Module | What's tested |
|---|---|---|
| Unit | `test_cache.py` | TTL expiry, eviction, per-user partitioning |
| Unit | `test_rate_limiter.py` | Sliding window, 429, cleanup |
| Unit | `test_text_processing.py` | Prompt compression, message truncation |
| Unit | `test_credit_system.py` | Free/Pro limits, daily reset, cost table |
| Unit | `test_audio.py` | Audio transcription endpoint unit coverage |
| Unit | `sources/test_core_normalize.py` | Author formats, year extraction, URL construction |
| Unit | `sources/test_openalex_normalize.py` | Unicode normalisation, BibTeX type inference |
| Unit | `sources/test_s2_normalize.py` | Field mapping, cache hit/miss/expiry |
| Unit | `sources/test_wikipedia_cache.py` | Cache TTL, enrichment relevance filter |
| Integration | `test_search_core.py` | 200/401/5xx responses, title filtering, missing key |
| Integration | `test_search_s2.py` | 200/429/503, cache dedup, empty query guard |
| Integration | `test_search_wikipedia.py` | Two-stage search+summary, cache, guard |
| Integration | `test_enrich_s2.py` | Enrichment merge, 404 passthrough, timeout |

---

## 🔄 CI/CD

Every push and pull request to `main` triggers the following pipeline:

| Job | Tool | What it checks |
|---|---|---|
| **Lint & Format** | `ruff` | Code style and formatting (100-char line length) |
| **Type Check** | `mypy` | Type annotations on `app/sources/` |
| **Env Validation** | `scripts/validate_env.py` | Config format/range with stub values |
| **Unit Tests** | `pytest tests/unit/` | Pure logic — no network needed |
| **Integration Tests** | `pytest tests/integration/` | Mocked HTTP — no API keys needed |

------

## 🧠 Overview

**Aether GraphRAG Research Assistant v4.5** is a production-grade, full-stack AI research tool that answers academic queries with grounded, fully-cited responses. It combines:

- **Neo4j graph database** *(integrated, currently paused)* — 196,875 nodes & 398,961 relationships across `CITES`, `WRITTEN_BY`, `PUBLISHED_IN`, `SIMILAR_TO`, `HAS_TOPIC`. Full traversal code is in place for future activation.
- **Supabase pgvector** *(integrated, currently paused)* — three-tier vector search (seed-exact → seed-fuzzy → expanded graph neighbours) with RRF fusion and MMR re-ranking, ready to re-enable.
- **Seven external academic sources**: ArXiv XML API, ArXiv MCP Server (SSE), Semantic Scholar, Papers With Code, OpenAlex, CORE Open Access, Wikipedia, and Kaggle.
- **Strategic Planning Brain**: a single `plan_query()` LLM call that classifies intent into 9 routes, extracts graph anchors, vector keywords, and required metrics — eliminating sequential LLM roundtrips.
- **Anti-hallucination pipeline**: zero-temperature grounded generation + dual-pass LLM verification (`PASS/FAIL` with confidence scoring).
- **MongoDB-backed auth** with JWT tokens, bcrypt password hashing, email verification, and a Razorpay-integrated freemium credit system.
- **Self-hosted ArXiv MCP Server** mounted at `/sse` using the Model Context Protocol (FastMCP), with deadlock-safe self-loopback detection.

---

## 🏗️ System Architecture

### High-Level Architecture

![High-Level Architecture](assets/aether_full_system_architecture.svg)

```mermaid
graph TB
    subgraph Client["Client Layer"]
        Browser["Browser Client"]
    end

    subgraph Frontend["Frontend Static HTML/JS/CSS"]
        Landing["landing.html Login/Signup"]
        Chat["index.html Research Chat UI"]
        AppJS["app.js JWT Auth, SSE Controls, Mermaid Renderer"]
    end

    subgraph FastAPI["FastAPI Backend app/app.py"]
        direction TB
        Middleware["CORS Middleware Rate Limiter 30 req/min JWT Auth Guard"]
        MCP["Local ArXiv MCP Server /sse FastMCP SSE endpoint search_arxiv tool"]
        Router["Intent Router 9 Route Types"]
        Brain["Strategic Planning Brain plan_query LLM call SUPER_MASTER_PROMPT"]
        Retrieval["Hybrid Retrieval Engine"]
        LLMLayer["LLM Generation Layer groq_chat"]
        Verify["Verification Pass Dual-pass fact-check"]
        ContextAssembly["Context Assembly and Prompt Engineering"]
    end

    subgraph Databases["Persistent Storage"]
        Neo4j[("Neo4j Aura 196875 nodes 398961 relationships 111896 publications")]
        Supabase[("Supabase pgvector paper_chunks table match_paper_chunks RPC hybrid_search RPC")]
        MongoDB[("MongoDB Atlas users collection chat_sessions payments uploaded_pdfs")]
        Redis[("Upstash Redis PDF chunk cache optional")]
    end

    subgraph ExternalAPIs["External Academic APIs"]
        ArXivXML["ArXiv XML API export.arxiv.org"]
        S2["Semantic Scholar API Citations, Abstracts, TLDR"]
        PwC["Papers With Code GitHub Repos, Datasets, HF Models/Spaces"]
        OpenAlex["OpenAlex Open scholarly graph"]
        CORE["CORE Open Access core.ac.uk v3"]
        Wiki["Wikipedia API Dataset context"]
        Kaggle["Kaggle Datasets API"]
    end

    subgraph EmbeddingLayer["Embedding Layer"]
        LocalBGE["Local BAAI/bge-base-en SentenceTransformer dev"]
        HFAPI["HuggingFace Inference API BAAI/bge-base-en prod/Vercel"]
    end

    subgraph LLMProviders["LLM Providers Groq API"]
        PlanModel["openai/gpt-oss-20b Plan, Verify, Fast routes"]
        HeavyModel["llama-3.3-70b-versatile Survey, Compare, Deep research"]
    end

    subgraph InMemoryCache["In-Process LRU Cache"]
        CacheGraph["graph 5 min TTL"]
        CacheEmbed["embed 5 min TTL"]
        CacheLLM["llm 5 min TTL"]
        CachePlan["plan 5 min TTL"]
        CacheAPI["api 12 hr TTL"]
    end

    subgraph Ingestion["Offline Data Ingestion"]
        IngestScript["ingestIntoSupabase.py CSV to chunk to embed to upsert"]
        ChunkScript["scripttouploadpaperchunkstable.py"]
    end

    Browser --> Landing & Chat
    Landing & Chat --> AppJS

    AppJS -->|"HTTPS REST Bearer JWT"| Middleware
    Middleware --> MCP
    Middleware --> Brain

    Brain --> Router
    Router --> Retrieval

    Retrieval -->|"Seed + Expand Cypher queries"| Neo4j
    Retrieval -->|"match_paper_chunks RPC hybrid_search RPC"| Supabase
    Retrieval -->|"Cache lookup"| InMemoryCache

    Retrieval --> ArXivXML & S2 & PwC & OpenAlex & CORE & Wiki & Kaggle
    Retrieval --> MCP

    Retrieval --> LocalBGE
    Retrieval --> HFAPI

    Retrieval --> ContextAssembly
    ContextAssembly --> LLMLayer
    LLMLayer --> PlanModel & HeavyModel
    LLMLayer --> Verify

    Middleware -->|"JWT decode users, sessions"| MongoDB
    AppJS -->|"Auth, History, Chat sessions"| MongoDB

    FastAPI -->|"PDF parse and embed Fitz + LangChain splitter"| Redis
    Redis -.->|"Cache miss fallback"| InMemoryCache

    IngestScript -->|"BAAI/bge-base-en vectors"| Supabase
    ChunkScript --> Supabase
```

---

### Request Lifecycle Flow

```mermaid
sequenceDiagram
    autonumber
    participant U as Browser Client
    participant FE as Frontend app.js
    participant API as FastAPI /api/research
    participant Auth as JWT Auth Guard
    participant Credits as Credit System
    participant Brain as plan_query Brain
    participant Graph as Neo4j Graph Retrieval
    participant Vec as Supabase Vector Search
    participant Ext as External APIs ArXiv/S2/PwC/OpenAlex
    participant LLM as Groq LLM
    participant Verif as Verification Pass
    participant DB as MongoDB

    U->>FE: User submits research query
    FE->>API: POST /api/research {query, top_k, verify}
    API->>Auth: Decode Bearer JWT
    Auth-->>API: user_id or anonymous
    API->>Credits: check_and_deduct_credit query
    Credits-->>API: OK or 402 credit_exhausted

    API->>Brain: plan_query(query, conversation_context)
    Note over Brain: SUPER_MASTER_PROMPT to JSON plan with route, anchors, keywords, metrics, cache_key
    Brain-->>API: QueryPlan with route, graph_anchors, vector_keywords

    par Graph retrieval when not chitchat
        API->>Graph: retrieve_graph_papers(keywords, anchors)
        Graph->>Graph: Seed Cypher title/author match
        Graph->>Graph: Expand via CITES, CITED_BY, WRITTEN_BY, PUBLISHED_IN, SIMILAR_TO
        Graph-->>API: ranked papers
    and Vector search
        API->>Vec: vector_search(embedding, min_similarity)
        Note over Vec: 3-tier seed-exact to seed-fuzzy to expanded-IDs
        Vec-->>API: paper_chunks with similarity scores
    and External enrichment parallel
        API->>Ext: retrieve_arxiv_context(query)
        API->>Ext: enrich_arxiv_papers_with_s2(papers)
        API->>Ext: enrich_arxiv_papers_with_pwc(papers)
        API->>Ext: enrich_arxiv_papers_with_openalex(papers)
        Ext-->>API: citations, TLDR summaries, repos, datasets, models
    end

    API->>API: RRF fusion and MMR re-rank lambda=0.6
    API->>API: Relevance floor filter min 0.22 similarity
    API->>API: Section priority sort abstract to conclusion to intro
    API->>API: Context budget packing 5000 token limit
    API->>API: build_relationship_context graph narrative

    API->>LLM: groq_chat(route_prompt + context, model, T=0.0)
    Note over LLM: Multi-key rotation, 429/413 retry, cascade fallback
    LLM-->>API: raw answer text

    alt verify=true
        API->>Verif: groq_chat(verification_prompt, PLAN_MODEL)
        Verif-->>API: verdict PASS/FAIL, confidence 0 to 1, issues list
    end

    API->>DB: upsert chat session MongoDB
    API-->>FE: answer, sources, graph_nodes, verification, credits
    FE-->>U: Rendered response with Mermaid diagrams and citations
```

---

### Retrieval Pipeline

![Retrieval Pipeline Flowchart](assets/aether_intent_routing_flowchart.svg)

```mermaid
flowchart LR
    Q["User Query"] --> PLAN["Strategic Brain\nplan_query\ngpt-oss-20b"]

    PLAN -->|route| ROUTER{"Intent\nRouter"}

    ROUTER -->|"entity_lookup\nstructured\ntitle_lookup"| GRAPH_ONLY["Graph-Only Path"]
    ROUTER -->|"rag, compare\nsurvey, timeline\nconceptual"| HYBRID["Hybrid Path\nGraph + Vector + External"]
    ROUTER -->|"chitchat\ncontext_only"| LLM_DIRECT["Direct LLM\nno retrieval"]

    subgraph GraphRetrieval["Neo4j Graph Retrieval"]
        SEED["Seed Papers\ntitle/author match\nrecency + citation score"]
        EXPAND["Graph Expansion\nCITES, CITED_BY\nWRITTEN_BY, PUBLISHED_IN\nSIMILAR_TO"]
        RANK["Paper Ranking\nexact > substring >\nword-overlap > recency\nlog-citation boost"]
        LINEAGE["Citation Lineage\nPath narratives between\nretrieved papers"]
        SEED --> EXPAND --> RANK --> LINEAGE
    end

    subgraph VectorSearch["Supabase pgvector"]
        T1["Tier 1 Seed IDs\nexact vector match"]
        T2["Tier 2 Seed IDs\nfuzzy threshold"]
        T3["Tier 3 Expanded\nneighbour IDs"]
        T1 -->|miss| T2 -->|miss| T3
    end

    subgraph ExternalEnrichment["External Source Enrichment parallel"]
        AX["ArXiv XML API\nplus MCP Server /sse"]
        S2E["Semantic Scholar\ncitations, TLDR, abstract"]
        PWCE["Papers With Code\nGitHub repos, datasets\nHF models, spaces, metrics"]
        OA["OpenAlex\nopen scholarly graph"]
        CE["CORE Open Access"]
    end

    HYBRID --> GraphRetrieval & VectorSearch & ExternalEnrichment

    GraphRetrieval --> FUSE["RRF Fusion\nReciprocal Rank Fusion\nk=60"]
    VectorSearch --> FUSE

    FUSE --> MMR["MMR Re-Ranking\nMaximal Marginal Relevance\nlambda=0.6 relevance/diversity"]
    MMR --> FILTER["Relevance Floor\nmin 0.22 cosine similarity"]
    FILTER --> MERGE["Adjacent Chunk Merge\nSection Priority\nabstract, conclusion, intro"]
    MERGE --> BUDGET["Context Budget Packing\n5000 token limit\nSmart RAG compression"]

    ExternalEnrichment --> CTX_ASSEMBLE["Context Assembly"]
    BUDGET --> CTX_ASSEMBLE
    LINEAGE --> CTX_ASSEMBLE

    CTX_ASSEMBLE --> PROMPT["Route-Specific Prompt\nresearch, compare, survey\ntimeline, entity, conceptual"]
    PROMPT --> GROQ["Groq LLM\nT=0.0 grounded generation"]
    GROQ --> VERIFY["Dual-Pass Verification\nPASS/FAIL + confidence"]
    VERIFY --> RESP["Verified Response\nSources, Graph nodes\nCredits snapshot"]
```

---

### Data Ingestion Pipeline

```mermaid
flowchart TD
    CSV["DBLP CSV Paper Data\ndblp-v10.csv"] --> CLEAN["clean_text\nparse_list helpers"]
    CLEAN --> CHUNK["Chunking\nChunk size 120 chars\nBatch size 250 rows"]
    CHUNK --> EMBED["BAAI/bge-base-en\nSentenceTransformer\nL2-normalized vectors"]
    EMBED --> UPSERT["Supabase Upsert\npaper_chunks table\npgvector index"]
    UPSERT --> CHECKPOINT["checkpoint.txt\nResume-safe ingestion\n4 parallel workers"]
```

---

## ✨ Features

### 🔍 Advanced Hybrid Retrieval Pipeline

| Component | Detail |
|---|---|
| **Strategic Planning Brain** | Single `plan_query()` LLM call (Super-Master Prompt) → `QueryPlan` with route, anchors, keywords, metrics, cache key — eliminates 2 sequential LLM calls from v3 |
| **9 Intent Routes** | `research`, `compare`, `timeline`, `survey`, `conceptual`, `entity_lookup`, `structured`, `title_lookup`, `chitchat`, `context_only` |
| **Graph Traversal** | Neo4j seed expansion via `CITES`, `CITED_BY`, `WRITTEN_BY`, `PUBLISHED_IN`, `SIMILAR_TO` + co-citation clustering |
| **Paper Ranking** | Exact-match > substring > word-overlap > recency scoring + log-citation boost |
| **3-Tier Vector Search** | seed-exact → seed-fuzzy → expanded-neighbour IDs via Supabase pgvector |
| **RRF Fusion** | Reciprocal Rank Fusion (k=60) merges graph and vector result lists |
| **MMR Re-ranking** | Maximal Marginal Relevance (λ=0.6) balances relevance vs. redundancy |
| **Context Budget** | Section priority sort (abstract→conclusion→intro) + 5000-token budget packing + smart RAG compression |

### 🌐 Academic Source Enrichment

| Source | Data Retrieved |
|---|---|
| **Semantic Scholar API** | Citation counts, abstracts, TL;DR summaries, fields of study, S2 links |
| **Papers With Code** | Official GitHub repos (stars), datasets, benchmark metrics, HuggingFace models & Spaces |
| **OpenAlex** | Open scholarly graph — works, venues, authors |
| **CORE Open Access** | Full-text open-access paper search via core.ac.uk v3 API |
| **ArXiv XML API** | Live paper search with categories, DOI, journal refs, comment fields |
| **ArXiv MCP Server** | Self-hosted SSE endpoint at `/sse` (FastMCP) — `search_arxiv` + `get_paper_details` tools |
| **Wikipedia** | Dataset/concept contextual summaries |
| **Kaggle** | Dataset enrichment for ML research queries |

### 🔌 Auth & Session Management

- **MongoDB Atlas** for users, chat sessions, payment records, and uploaded PDFs
- **JWT** (`HS256`, 1-week expiry) with `PyJWT`; passwords hashed with `bcrypt`
- **Email verification** flow (verify-link + token), **password reset**, **profile update**
- **Multi-turn conversation** with full session persistence (`/api/history`)

### 💳 Credit & Subscription System

| Tier | Credits/Day | Top K | Models |
|---|---|---|---|
| **Free** | 20 | 8 | Reason model |
| **Pro** | Unlimited | 20 | All models |

- Credit costs: `query=1`, `chat=1`, `timeline=3`, `compare=3`, `pdf=5`
- **Razorpay** payment gateway integration (`create-order` + `verify-payment` + HMAC-SHA256 signature validation)
- Credits reset daily at midnight UTC

### 🛡️ Token Engineering & Anti-Hallucination

- **Chitchat constraints**: `max_tokens=250` (350 with PDF context) with explicit complete-sentence enforcement
- **Multi-key Groq rotation**: Distributes plan/reason/heavy calls across multiple API keys by purpose
- **Cascade fallback**: `gpt-oss-20b` → `llama-3.3-70b-versatile` on 413/timeout
- **Smart RAG compression**: Truncates abstracts (120 chars) and chunk bodies (150 chars) before character-level slicing
- **Dual-pass verification**: Second LLM call checks factual grounding with `PASS/FAIL` + `confidence` + issue list

### 🖥️ Frontend UI

![Aether Frontend Research Chat UI](assets/systemView.png)

- Glassmorphic dark-mode research chat interface
- JWT login/signup flow (`landing.html` → `index.html`)
- **Mermaid.js v10** auto-rendering with inline-style sanitization and zoom/fullscreen modal
- Adjustable parameters: Top K, Min Similarity, Model selection, Hallucination check toggle
- Audio transcription (`/api/audio/transcribe`) and PDF upload (`/api/upload/pdf`)

---

## 🛠️ Tech Stack

| Layer | Technology | Version |
|---|---|---|
| **Backend Framework** | FastAPI + Uvicorn | 0.115 / 0.30.6 |
| **Graph Database** | Neo4j Aura (cloud) | 5.x |
| **Vector Database** | Supabase (pgvector) | 2.9.1 |
| **User & Auth DB** | MongoDB Atlas | ≥ 4.0 |
| **PDF Cache** | Upstash Redis REST | optional |
| **LLM Provider** | Groq API | — |
| **LLM Models** | `openai/gpt-oss-20b` (plan/fast) · `llama-3.3-70b-versatile` (heavy) | — |
| **Embedding Model** | `BAAI/bge-base-en` (local SentenceTransformer or HF Inference API) | L2-normalized, 768-dim |
| **PDF Parsing** | PyMuPDF (fitz) | 1.24.2 |
| **Text Splitting** | LangChain `RecursiveCharacterTextSplitter` | — |
| **Authentication** | PyJWT + bcrypt | ≥ 2.8 / ≥ 4.0 |
| **Payment Gateway** | Razorpay (HMAC-SHA256 signature verification) | — |
| **MCP Protocol** | FastMCP (SSE transport) | ≥ 1.2.0 |
| **Frontend** | HTML5 + CSS3 (Glassmorphic) + Vanilla JS | — |
| **Diagram Engine** | Mermaid.js | v10 |
| **Deployment** | Vercel (serverless) + Render (MCP server) | — |

---

## 📁 Project Structure

```
GraphRag-Research-Assistant/
├── api/
│   └── index.py                          # Vercel serverless entry point
│
├── app/
│   ├── _server.py                        # Main FastAPI application
│   │                                     #  - Pool: Supabase + Neo4j + MongoDB + Groq
│   │                                     #  - plan_query() Strategic Brain
│   │                                     #  - retrieve_graph_papers() + Cypher traversal
│   │                                     #  - vector_search() + hybrid_search()
│   │                                     #  - RRF fusion + MMR re-ranking
│   │                                     #  - groq_chat() with multi-key rotation
│   │                                     #  - Local ArXiv MCP Server (/sse)
│   │                                     #  - All 30+ REST API endpoints
│   │                                     #  - JWT Auth + Razorpay payments
│   │
│   ├── embeddingService/
│   │   └── embeddings.py                 # BAAI/bge-base-en local/HF API wrapper
│   │
│   └── sources/                          # External academic source connectors
│       ├── __init__.py                   # Public re-exports for all connectors
│       ├── semantic_scholar.py           # S2 citation, abstract, TL;DR enrichment
│       ├── papers_with_code.py           # GitHub repos, datasets, HF models/spaces
│       ├── arxiv_mcp.py                  # ArXiv MCP connector (SSE client)
│       ├── openalex.py                   # OpenAlex open scholarly graph
│       ├── core.py                       # CORE Open Access API v3
│       ├── wikipedia.py                  # Wikipedia summary enrichment
│       └── kaggle.py                     # Kaggle dataset search
│
├── frontend/
│   ├── index.html                        # Main research chat interface
│   ├── landing.html                      # Secure Login / Signup page
│   ├── app.js                            # Frontend logic: JWT, SSE, Mermaid sanitizer
│   └── styles.css                        # Dark-theme glassmorphic stylesheet
│
├── arxiv-mcp-server/                     # Standalone ArXiv MCP Server (Render deploy)
│   ├── mcp_server.py                     # FastMCP server: search_arxiv + get_paper_details
│   ├── requirements.txt
│   └── Dockerfile
│
├── ingestion/
│   ├── ingestIntoSupabase.py             # Primary ingestion: CSV → chunks → BGE → Supabase
│   └── scripttouploadpaperchunkstable.py # Paper chunks uploader utility
│
├── tests/
│   ├── conftest.py                       # Top-level test fixtures
│   ├── test_connectivity.py              # Full database + API connectivity suite
│   ├── test_embedding.py                 # Embedding pipeline tests
│   ├── test_kaggle.py
│   ├── test_wikipedia.py
│   ├── unit/                             # Pure-logic unit tests (no network)
│   │   ├── test_audio.py
│   │   ├── test_cache.py
│   │   ├── test_credit_system.py
│   │   ├── test_rate_limiter.py
│   │   ├── test_text_processing.py
│   │   └── sources/                      # Per-connector normalisation tests
│   └── integration/                      # Mocked-HTTP integration tests
│       ├── test_enrich_s2.py
│       ├── test_search_core.py
│       ├── test_search_s2.py
│       └── test_search_wikipedia.py
│
├── docs/
│   └── vercel_bundle_size_resolution.md  # Deployment notes
│
├── pyproject.toml                        # Project metadata, ruff & pytest config
├── requirements.txt                      # Production Python dependencies
├── requirements-local.txt                # Local-only (sentence-transformers)
├── requirements-dev.txt                  # Dev/test dependencies
├── vercel.json                           # Vercel routing configuration
└── .env.example / .env.local            # Environment variables template / local config
```

---

## 📦 Prerequisites

- Python **3.11+**
- **Neo4j Aura** instance loaded with publication graph data
- **Supabase** project with `match_paper_chunks` and `hybrid_search` RPC functions
- **MongoDB Atlas** cluster (free tier works)
- **Groq API** key → [console.groq.com](https://console.groq.com)
- **HuggingFace** token (required on Vercel; optional locally with `sentence-transformers`)
- *(Optional)* **Razorpay** account for subscription payments
- *(Optional)* **Upstash Redis** for persistent PDF chunk caching

---

## 🚀 Setup & Installation

### 1. Clone the repository

```bash
git clone https://github.com/your-username/GraphRag-Research-Assistant.git
cd GraphRag-Research-Assistant

```

### 2. Create a virtual environment

```bash
python -m venv venv

# Windows
venv\Scripts\activate

# macOS / Linux
source venv/bin/activate
```

### 3. Install dependencies

```bash
# Production (no local embedding model)
pip install -r requirements.txt

# Local development (includes sentence-transformers)
pip install -r requirements-local.txt
```

### 4. Configure environment variables

```bash
cp .env.example .env
# Edit .env with your credentials
```

---

## 🔑 Environment Variables

```env
# -- MongoDB -------------------------------------------------------
MONGODB_URI=mongodb+srv://user:password@cluster0.mongodb.net/
MONGODB_DB_NAME=aether_research_assistant
JWT_SECRET=your-jwt-signing-secret-256-bit

# -- Supabase ------------------------------------------------------
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_ROLE_KEY=your-service-role-key

# -- Neo4j ---------------------------------------------------------
NEO4J_URI=neo4j+s://your-instance.databases.neo4j.io
NEO4J_USER=neo4j
NEO4J_PASSWORD=your-neo4j-password

# -- LLM & Embeddings ----------------------------------------------
GROQ_API_KEY=gsk_key1,gsk_key2,gsk_key3   # comma-separated for rotation
HF_TOKEN=hf_your_huggingface_token
EMBED_MODEL=BAAI/bge-base-en
REASON_MODEL=openai/gpt-oss-20b
HEAVY_MODEL=llama-3.3-70b-versatile
PLAN_MODEL=openai/gpt-oss-20b

# -- External Sources (optional) -----------------------------------
ARXIV_MCP_URL=https://your-render-app.onrender.com/sse
CORE_API_KEY=your-core-api-key
S2_API_KEY=your-semantic-scholar-api-key
OPENALEX_EMAIL=your-email@example.com      # raises rate limit to 10 req/s
KAGGLE_USERNAME=your-kaggle-username
KAGGLE_KEY=your-kaggle-api-key

# -- Payments (Optional) -------------------------------------------
RAZORPAY_KEY_ID=rzp_live_xxx
RAZORPAY_KEY_SECRET=your-razorpay-secret

# -- Redis Cache (Optional) ----------------------------------------
UPSTASH_REDIS_REST_URL=https://your-redis.upstash.io
UPSTASH_REDIS_REST_TOKEN=your-upstash-token

# -- CORS ----------------------------------------------------------
CORS_ORIGINS=http://localhost:3000,http://localhost:5173

# -- Tuning --------------------------------------------------------
MAX_GRAPH_NODES=25
RELEVANCE_FLOOR=0.22
MMR_LAMBDA=0.6
CACHE_TTL=300
CACHE_MAX=512
FREE_CREDITS_PER_DAY=20
GROQ_TIMEOUT=30
EMBED_TIMEOUT=20
RATE_LIMIT_PER_MIN=30
REQUEST_TIMEOUT=60

# -- Feature flags -------------------------------------------------
FREEZE_RETRIEVAL=false   # set true to disable live DB/vector retrieval (offline mode)
ENV=prod
PORT=8000
```

---

## ▶️ Running the Project

### Start locally

```bash
python -m app._server
```

Expected startup log:

```
INFO | aether | Loading embedding model...
INFO | aether | Embedding model ready
INFO | aether | Supabase connected
INFO | aether | MongoDB connected and unique indexes on email and payments verified
INFO | aether | Neo4j connected
INFO | Application startup complete.
INFO | Uvicorn running on http://0.0.0.0:8000
```

### Vercel local dev

```bash
vercel dev
```

### Access the application

| URL | Purpose |
|---|---|
| `http://localhost:8000/app` | Frontend UI (redirects to `landing.html` if unauthenticated) |
| `http://localhost:8000/docs` | Swagger / OpenAPI interactive docs |
| `http://localhost:8000/sse` | Local ArXiv MCP Server (SSE endpoint) |
| `http://localhost:8000/api/health` | Quick health check |
| `http://localhost:8000/api/health/full` | Full diagnostics (DB connectivity + embedding) |

---

## 📡 API Reference

### Authentication

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/auth/signup` | Register; returns signed JWT |
| `POST` | `/api/auth/login` | Login; returns signed JWT |
| `GET` | `/api/auth/me` | Get current user profile |
| `PUT` | `/api/auth/profile` | Update display name / settings |
| `PUT` | `/api/auth/password` | Change password |
| `POST` | `/api/auth/verify-email` | Verify email with token |
| `GET` | `/api/auth/verify-link` | Email verification link handler |
| `POST` | `/api/auth/resend-verification` | Re-send verification email |
| `POST` | `/api/auth/forgot-password` | Send password-reset token |
| `POST` | `/api/auth/reset-password` | Reset password with token |
| `GET` | `/api/auth/plan` | Get subscription plan + credits |
| `POST` | `/api/auth/upgrade` | Manual plan upgrade |

### Research & Chat

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/research` | **Main research query** — grounded answer + sources |
| `POST` | `/api/chat` | Multi-turn conversation with RAG + session tracking |
| `POST` | `/api/research/timeline` | Chronological evolution of a topic (cost: 3 credits) |
| `POST` | `/api/research/survey` | Auto-generate mini literature survey (Pro) |
| `POST` | `/api/research/bulk` | Batch up to 10 queries simultaneously |

### Graph Intelligence

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/graph/paper/{paper_id}` | Full paper node with all Neo4j relationships |
| `GET` | `/api/graph/author/{author_name}` | Author ego-network: papers, co-authors, venues |
| `POST` | `/api/graph/citation-path` | Shortest citation path between two papers |
| `POST` | `/api/graph/compare` | Deep structured paper comparison (cost: 3 credits) |
| `GET` | `/api/graph/trending` | Trending papers by recent citation velocity (≥ 2022) |
| `GET` | `/api/stats` | Neo4j + Supabase database statistics |

### History & Sessions

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/history` | List all user chat sessions |
| `POST` | `/api/history` | Create / upsert a chat session |
| `PUT` | `/api/history/{session_id}` | Update session messages |
| `DELETE` | `/api/history/{session_id}` | Delete a specific session |
| `DELETE` | `/api/history` | Delete all sessions for user |

### Files & Media

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/upload/pdf` | Upload PDF — parse with PyMuPDF — stored in MongoDB |
| `GET` | `/api/pdf/{pdf_id}` | Retrieve uploaded PDF text |
| `POST` | `/api/audio/transcribe` | Audio transcription endpoint |

### Payments & Credits

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/credits` | Current credit balance snapshot |
| `POST` | `/api/auth/razorpay/create-order` | Create Razorpay payment order |
| `POST` | `/api/auth/razorpay/verify-payment` | Verify Razorpay signature + activate Pro |
| `GET` | `/api/auth/payments/history` | Payment transaction history |

### OpenAI-Compatible Endpoint

| Method | Path | Description |
|---|---|---|
| `POST` | `/v1/chat/completions` | OpenAI-compatible chat completions interface |

### Config & Health

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/health` | Quick connectivity health check |
| `GET` | `/api/health/full` | Full diagnostics: DB + embedding + external APIs |
| `GET` | `/api/config` | Runtime configuration snapshot |

---

## 🛡️ Anti-Hallucination Pipeline

Aether's 7-step pipeline systematically prevents LLM fabrication:

```mermaid
flowchart LR
    Q["User Query"] --> S1["1. Intent Classification\nplan_query 9 route types"]
    S1 --> S2["2. Keyword and Anchor\nExtraction\ngraph_anchors, vector_keywords"]
    S2 --> S3["3. Graph Retrieval\nNeo4j seed + expansion\ncitation lineage"]
    S3 --> S4["4. Vector Search\n3-tier Supabase pgvector\nplus external enrichment"]
    S4 --> S5["5. Relevance Filter\nCosine min 0.22 floor\nMMR diversity rerank"]
    S5 --> S6["6. Grounded Generation\nT=0.0, mandatory citations\nzero hallucination prompts"]
    S6 --> S7["7. Dual-Pass Verification\nPASS/FAIL verdict\nconfidence 0 to 1, issue list"]
    S7 --> R["Verified Response\nSources, Graph nodes\nCredits snapshot"]
```

**Key safety mechanisms:**

- `T=0.0` (zero temperature) for all research generation — deterministic, not creative
- Mandatory inline citation format `[Paper Title, Year]` enforced in system prompt
- If no evidence found: LLM may respond **only if 100% confident** from general knowledge — never hallucinated citations
- Verification is an independent second LLM call cross-checking claims against assembled context
- Chitchat queries are capped at 250 tokens to prevent topic drift

---

## 💳 Credit & Subscription System

```mermaid
stateDiagram-v2
    [*] --> Free : Register
    Free --> CheckCredits : API Request
    CheckCredits --> DeductCredit : credits_used + cost is 20 or less
    CheckCredits --> Blocked : credits_used + cost exceeds 20
    Blocked --> Upgrade : POST /api/auth/razorpay/create-order
    Upgrade --> VerifyPayment : POST /api/auth/razorpay/verify-payment
    VerifyPayment --> Pro : HMAC-SHA256 signature valid
    Pro --> Unlimited : No credit deduction
    DeductCredit --> DailyReset : credits_reset_at reached
    DailyReset --> Free : credits_used reset to 0
```

---

## 🌐 Deployment

### Vercel (Frontend + API)

```bash
vercel deploy --prod
```

Vercel routes defined in `vercel.json`:
- `/api/*` → `api/index.py` (FastAPI serverless)
- `/v1/*` → `api/index.py`
- `/app/*` → `frontend/index.html`
- `/*` → `frontend/landing.html`
- Static assets served directly from `frontend/`

> **Note**: `sentence-transformers` is too large for Vercel's 250 MB limit. Set `HF_TOKEN` to use the HuggingFace Inference API for embeddings on Vercel.

### Render (ArXiv MCP Server)

Deploy `arxiv-mcp-server/` as a separate Render web service using the included `Dockerfile`. Set `ARXIV_MCP_URL` in your main `.env` to point to the Render URL. The main app's self-loopback detection prevents deadlocks if both services share the same host.

---

## 🔬 Testing Connectivity

```bash
python tests/test_connectivity.py
```

Checks: Supabase RPC, Neo4j Cypher, MongoDB `ping`, embedding generation, Groq LLM call, and all external API connectors.

---

## 🔧 Troubleshooting

| Issue | Likely Cause | Fix |
|---|---|---|
| `Missing required env: SUPABASE_URL` | `.env` not loaded | Run from project root; check `load_dotenv` paths |
| `EmbeddingError: HF_TOKEN not set` | No local model + no HF token | Set `HF_TOKEN` or install `requirements-local.txt` |
| `GraphRetrievalError: Neo4j not connected` | Wrong URI or credentials | Check `NEO4J_URI` format: `neo4j+s://...` |
| `LLMError: Groq failed after N attempts` | Rate limit or invalid key | Add comma-separated keys to `GROQ_API_KEY` |
| `402 credit_exhausted` | Daily free limit reached | Upgrade to Pro or wait for midnight UTC reset |
| Mermaid diagrams not rendering | Browser CSP or missing script | Check `mermaid.initialize()` in `app.js` |
| `/sse` endpoint 404 on Vercel | MCP SSE not supported serverlessly | Deploy `arxiv-mcp-server/` to Render separately |

---

## 📄 License

This project is licensed under the **Apache License 2.0**.  
See the [LICENSE](LICENSE) file for full terms.

---

*Built with ❤️ using FastAPI · Neo4j · MongoDB · Supabase · Groq · HuggingFace · Razorpay · FastMCP*
