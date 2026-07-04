# 🔬 Aether GraphRAG Research Assistant

> **Graph-Augmented Retrieval with Academic Source Enrichment, Anti-Hallucination Verification, and Self-Hosted MCP Capabilities**  
> Ask research questions. Get grounded, cited answers enriched with Semantic Scholar stats and official code repositories — powered by Neo4j knowledge graphs, Supabase vector search, MongoDB user/session databases, and Groq LLMs.

---

## 📋 Table of Contents

- [Overview](#-overview)
- [Architecture](#-architecture)
- [Features](#-features)
- [Tech Stack](#-tech-stack)
- [Project Structure](#-project-structure)
- [Prerequisites](#-prerequisites)
- [Setup & Installation](#-setup--installation)
- [Environment Variables](#-environment-variables)
- [Running the Project](#-running-the-project)
- [API Reference](#-api-reference)
- [Frontend Guide](#-frontend-guide)
- [Testing Connectivity](#-testing-connectivity)
- [Anti-Hallucination Pipeline](#-anti-hallucination-pipeline)
- [Troubleshooting](#-troubleshooting)

---

## 🧠 Overview

**Aether GraphRAG Research Assistant v4.5** is a full-stack AI research tool that combines:

- **Graph-based retrieval** via Neo4j Aura (196,875 nodes, 398,961 relationships across 111,896 publication nodes).
- **Vector similarity search** via Supabase pgvector (3-tier: seed-exact → seed-fuzzy → expanded neighbours).
- **Custom MongoDB Authentication & History**: Users, profiles, and chat sessions are stored in MongoDB Atlas, with secure `bcrypt` hashing and `PyJWT` token validation.
- **Academic Source Enrichment**: Automatically fetches citations, abstracts, venues, datasets, and code repositories from Semantic Scholar and Papers With Code.
- **Self-Hosted ArXiv MCP Server**: Exposes a Server-Sent Events (SSE) `/sse` route hosting a Local ArXiv Model Context Protocol (MCP) server, alongside connecting to remote MCP servers.
- **Token Engineering**: Custom token budgeting limiting chitchat queries, and smart fallback responses for empty-evidence states to prevent token waste and hallucinations.
- **Anti-hallucination verification**: Grounded zero-temperature generation followed by a dual-pass verification pipeline.

It serves both a **REST API** (FastAPI) and a **browser-based frontend UI** from a single server.

---

## 🏗️ Architecture

```mermaid
graph TD
    %% Users & Frontend
    User([User Client]) ==>|HTTPS| Frontend[HTML/JS Frontend]
    Frontend ==>|Auth / History APIs| MongoDB[(MongoDB Atlas)]
    Frontend ==>|Research / Chat APIs| API[FastAPI Server]

    %% Authentication & Session Store
    API -->|JWT verification & CRUD| MongoDB
    subgraph MongoDB Storage
        users_col[(users collection)]
        chat_sessions_col[(chat_sessions collection)]
    end
    MongoDB --- users_col
    MongoDB --- chat_sessions_col

    %% Routing
    API ==>|plan_query| Planner[Query Planner Brain]
    Planner ==>|Intent Route & Keywords| Router{Intent Router}

    %% Retrieval Pipeline
    subgraph Data Sources & Indexes
        Neo4j[(Neo4j Graph Database)]
        Supabase[(Supabase pgvector)]
        ArXiv_MCP[Local ArXiv MCP /sse]
        ArXiv_Ext[Direct ArXiv XML API]
        S2_API[Semantic Scholar API]
        PwC_API[Papers With Code API]
    end

    Router -->|entity/structured| Neo4j
    Router -->|rag/compare/survey/timeline| Neo4j
    Router -->|rag/compare/survey/timeline| Supabase
    Router -->|RAG External Context| ArXiv_MCP
    ArXiv_MCP -->|Self-Loop Protection Fallback| ArXiv_Ext
    Router -->|Paper Stats Enrichment| S2_API
    Router -->|Code & Dataset Stats| PwC_API

    %% Response Generation & Safety
    Neo4j --> RRF[RRF Fusion & MMR Re-Ranking]
    Supabase --> RRF
    RRF --> Context[Context Assembly]
    ArXiv_MCP --> Context
    ArXiv_Ext --> Context
    S2_API --> Context
    PwC_API --> Context

    Context ==>|Prompt + Context| LLM[Groq LLM Generation]
    LLM ==>|Raw Answer| Verifier{Verification Pipeline}
    Verifier ==>|Confidence & Hallucination Check| FinalResponse[Verified Grounded Answer]
    FinalResponse ==>|JSON Response| Frontend
```

---

## ✨ Features

### 🔍 Advanced Hybrid Retrieval Pipeline
- **Unified Query Planning Brain** — `plan_query()` (single LLM call via `llama-3.1-8b-instant`) produces a `QueryPlan` with: route, graph anchors, vector keywords, required metrics, and a deterministic cache key.
- **Intent Routing** — auto-routes to: `research`, `compare`, `timeline`, `survey`, or `chitchat`.
- **Graph Traversal** — Neo4j seed paper ranking (exact → substring → word-overlap → recency) + expand via `CITES`, `WRITTEN_BY`, `PUBLISHED_IN`, `SIMILAR_TO` relationships.
- **3-Tier Vector Search** — seed-exact → seed-fuzzy → expanded graph neighbours via Supabase pgvector.
- **RRF Fusion + MMR Re-ranking** — Reciprocal Rank Fusion merges lists; Maximal Marginal Relevance (λ=0.6) prevents redundancy.

### 🌐 Academic Source & Code Enrichment
- **Semantic Scholar Integration** — automatically retrieves up-to-date citation counts, paper abstracts, and venue statistics.
- **Papers With Code Integration** — extracts official code repositories (GitHub), linked models, datasets, and upvote metrics.
- **Self-Hosted ArXiv MCP Server** — mounts a local MCP SSE endpoint directly on `/sse` implementing the `search_arxiv` tool, with built-in self-loopback deadlock prevention.

### 🔌 Custom Auth & History Persistence
- **MongoDB Atlas Backend**: User registration, login, profile configurations, and conversation logs are persisted in MongoDB.
- **Security Utilities**: Custom JWT authentication tokens are signed with a server secret; passwords are encrypted using `bcrypt` salting.

### 🛡️ Token Engineering & Anti-Hallucination
- **Chitchat Constraints**: Limits chitchat prompts to a strict `max_tokens=250` limit (or `350` with PDF context), prompting the LLM for concise, complete sentences to avoid text cutoff.
- **Flexible Research Fallback**: If no documents or chunks are found in the local index or external sources, Aether is granted the flexibility to respond using its general scientific knowledge, but **only** if 100% confident, preventing output degradation.
- **Verification Pass**: Dual-pass LLM fact-checking for a final `PASS/FAIL` verdict with confidence scoring.

### 🖥️ Modern Frontend UI
- Premium dark-mode glassmorphic research assistant interface.
- **MongoDB JWT Auth** with login/signup flow (`landing.html`).
- **Interactive Mermaid Diagrams**: Auto-renders layout flowcharts, automatically stripping inline style overrides for high contrast and readability on dark themes, with zoom/fullscreen modals.
- Adjustable parameters (Top K, Min Similarity, Model, Hallucination Check).

---

## 🛠️ Tech Stack

| Layer | Technology |
|---|---|
| **Backend Framework** | FastAPI 0.115 + Uvicorn |
| **Graph Database** | Neo4j Aura (cloud) |
| **Vector Database** | Supabase (pgvector) |
| **User & History DB** | MongoDB Atlas |
| **LLM Provider** | Groq API — `llama-3.1-8b-instant` (plan/fast) · `llama-3.3-70b-versatile` (heavy) |
| **Embedding Model** | `BAAI/bge-base-en` (local via `sentence-transformers`; HuggingFace Inference API fallback) |
| **Authentication** | PyJWT + bcrypt Hashing |
| **Frontend** | HTML5 + CSS3 (Glassmorphic) + Vanilla JavaScript |
| **Diagram Engine** | Mermaid.js v10 (with custom sanitization & zoom/fullscreen viewport) |
| **External Sources** | Semantic Scholar API · Papers With Code · Local/Remote ArXiv MCP Server |

---

## 📁 Project Structure

```
GraphRag-Research-Assistant/
├── api/
│   └── index.py                      # Vercel serverless entry point
├── app/
│   ├── app.py                        # Main FastAPI application, auth endpoints & all API routes (v4.5)
│   ├── embeddingService/
│   │   └── embeddings.py             # Local BAAI/bge-base-en + HF API fallback
│   └── sources/
│       ├── semantic_scholar.py       # Semantic Scholar citation & abstract enrichment
│       ├── papers_with_code.py       # GitHub repos, datasets & upvote enrichment
│       └── arxiv_mcp.py              # ArXiv MCP connector (paper search + PDF fetch)
├── frontend/
│   ├── index.html                    # Main chat interface
│   ├── landing.html                  # Secure Login/Signup landing page
│   ├── app.js                        # Frontend logic (JWT session authentication, Mermaid sanitizing, SSE controls)
│   └── styles.css                    # Dark-theme glassmorphic stylesheet
├── ingestion/
│   ├── ingestIntoSupabase.py         # Primary ingestion script
│   └── scripttouploadpaperchunkstable.py  # Paper chunks uploader
├── docs/
│   └── vercel_bundle_size_resolution.md  # Deployment notes
├── tests/                            # Backend tests
├── requirements.txt                  # Python dependencies
├── requirements-local.txt            # Local-only dependencies (sentence-transformers)
├── .env                              # Environment variables (git-ignored)
└── vercel.json                       # Vercel deployment configuration
```

---

## 📦 Prerequisites

- Python **3.10+**
- A **MongoDB Atlas** database.
- A **Supabase** project with the `match_paper_chunks` RPC function deployed.
- A **Neo4j Aura** (or self-hosted) instance with publication graph data.
- A **Groq API** key — get one at [console.groq.com](https://console.groq.com).
- A **HuggingFace** token (optional, used as fallback for embeddings on cloud platforms like Vercel).

---

## 🚀 Setup & Installation

### 1. Clone the project

```bash
cd c:\Users\YourName\projects\GraphRag-Research-Assistant
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
pip install -r requirements.txt
```

### 4. Configure environment variables

Copy `.env.example` to `.env` and fill in the required variables (including your MongoDB connection settings).

---

## 🔑 Environment Variables

Configure the following variables in your `.env` or `.env.local` file:

```env
# ── MongoDB Configuration (New) ──────────────────────────────────────
MONGODB_URI=mongodb+srv://dbUser:password@cluster0.mongodb.net/
MONGODB_DB_NAME=aether_research_assistant
JWT_SECRET=your-jwt-signing-secret-key

# ── Supabase Configuration ───────────────────────────────────────────
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_ROLE_KEY=your-service-role-key
SUPABASE_KEY=your-anon-public-key

# ── Neo4j Configuration ──────────────────────────────────────────────
NEO4J_URI=neo4j+s://your-instance.databases.neo4j.io
NEO4J_USER=neo4j
NEO4J_PASSWORD=your-neo4j-password

# ── LLM & Embeddings ──────────────────────────────────────────────────
GROQ_API_KEY=your-groq-api-key
HF_TOKEN=your-huggingface-token

# ── Optional / Server Configuration ──────────────────────────────────
ARXIV_MCP_URL=https://graphrag-research-assistant.onrender.com/sse
EMBED_MODEL=BAAI/bge-base-en
REASON_MODEL=llama-3.1-8b-instant        # Fast model for routing & verification
HEAVY_MODEL=llama-3.3-70b-versatile      # Powerful model for deep research
MAX_GRAPH_NODES=20
ENV=prod
PORT=8000
```

---

## ▶️ Running the Project

### Start the server locally

```bash
python -m app.app
```

You will see:
```
INFO | graphrag | Frontend served at /app from ./frontend
INFO | graphrag | Supabase connected
INFO | graphrag | MongoDB connected and unique index on email verified
INFO | graphrag | Neo4j connected
INFO | Application startup complete.
INFO | Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)
```

### Run on Vercel locally

```bash
vercel dev
```

### Access the application

| URL | Purpose |
|---|---|
| `http://localhost:8000/app` | 🖥️ **Frontend UI** (redirects to `/app/landing.html` if unauthenticated) |
| `http://localhost:8000/docs` | 📖 **Swagger API docs** (interactive) |
| `http://localhost:8000/sse` | 🔌 **Local ArXiv MCP Server SSE Endpoint** |
| `http://localhost:8000/api/health/full` | 🔍 **Full Diagnostics Health Check** |

---

## 📡 API Reference

### `POST /api/auth/signup` & `POST /api/auth/login`
Endpoints for user sign-up and sign-in. Returns a signed JWT token on success.

---

### `POST /api/research`
Main research query endpoint. Returns a grounded answer and retrieved context.

---

### `POST /api/chat`
Multi-turn conversation with RAG context and session tracking.

---

### `GET /api/history` · `POST /api/history` · `DELETE /api/history/{session_id}`
Endpoints for listing, saving, and deleting user chat sessions synced to MongoDB.

---

## 🛡️ Anti-Hallucination Pipeline

A rigorous 7-step pipeline prevents LLM fabrication:
1. **Intent Classification**: Routing of query.
2. **Keyword Extraction**: Embedding + entity extraction.
3. **Graph Retrieval**: Neo4j seed expansion.
4. **Vector Search**: Tiered pgvector search.
5. **Relevance Filter**: Cosine similarity floor filtering.
6. **Grounded Answer**: Zero-temperature prompting with mandatory inline citations.
7. **Verification Pass**: Dual-pass LLM fact-checking for a final `PASS/FAIL` verdict with confidence scoring.

---

## 📄 License

This project is licensed under the **Apache License 2.0**.
See the [LICENSE](LICENSE) file for full terms.

---

*Built with ❤️ using FastAPI, Neo4j, MongoDB, Supabase, Groq, and HuggingFace*
