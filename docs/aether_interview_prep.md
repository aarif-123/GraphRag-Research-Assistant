# Aether project interview preparation

This is a repository-grounded answer guide for the Aether project questionnaire. It describes what the checked-in code and project documentation establish; it does not assume personal authorship, production usage, or performance results that are not evidenced here.

## How to use this guide

- **Implemented** means there is relevant code in this repository; it does not by itself prove a feature is deployed or operational.
- **Default state** describes the repository's configured defaults. A deployment can override them with environment variables.
- **Unknown** means the repository cannot establish the answer. Confirm it from your own records or experience instead of guessing.
- The project README contains useful architecture notes, but some claims conflict with the current source. Source code and current configuration take precedence below.

## High-confidence project summary

> Aether is a browser-based academic research assistant backed by a FastAPI modular monolith. It uses a Groq-hosted LLM to plan and synthesize research responses, searches external academic APIs, and can optionally use Neo4j graph retrieval and Supabase pgvector. The current default sets `FREEZE_RETRIEVAL=true`, so graph/vector database retrieval is paused; the application is configured for external-source research instead. MongoDB supports accounts, chat history, payments, and uploaded PDFs. ArXiv search uses its public XML API directly.

Be precise when describing it:

- The code uses graph database traversal; it does **not** implement a Graph Neural Network.
- “GraphRAG” is the project's name/architecture direction, but the README says Neo4j and Supabase retrieval are frozen by default.
- Do not describe the application backend as independently deployable microservices. It is principally one FastAPI application with routers and modules.
- Do not claim high throughput, a concurrency limit, measured accuracy, zero hallucinations, or 10,000-user scalability without test/production evidence.

## Batch 1 — Project, architecture, and request lifecycle

### 1. Project introduction and business problem

**What it is:** A full-stack academic research assistant. A user can ask a research question, search academic sources, inspect papers/datasets/code links, and analyze an uploaded PDF. The main research response includes the answer, source records, route, latency, model, warnings, and optional verification details.

**Problem it addresses:** Making paper discovery and synthesis easier by combining a query planner, source search, context construction, LLM synthesis, and an optional evidence-checking pass. This is the intended product problem documented in `README.md`; the repository does not contain a user study validating the business impact.

**Target audience:** Researchers, students, and people exploring academic topics. This is a product positioning statement, not an experimentally validated audience definition.

**Major implemented surfaces:**

- Research and multi-turn chat APIs.
- External paper search/enrichment.
- PDF upload, text extraction, and question answering.
- Account/authentication, history, and payment-related APIs.
- Optional graph, vector, and caching integrations.
- Static browser UI.

**Difference from general chat/search products:** The code is designed to return research-source metadata and use research-specific routes/prompts; it can enrich papers with citation, venue, dataset, and code details. Do not claim it is more accurate than ChatGPT or Perplexity without a comparative evaluation.

**Limitations visible in the repository:**

1. Graph and vector retrieval are frozen by default.
2. Research source availability, rate limits, credentials, and network availability affect results.
3. PDF extraction is text-based; there is no OCR path for scanned/image-only PDFs.
4. Verification is another LLM judgement, not a proof of correctness.
5. The repository has no documented load-test result or representative retrieval-quality benchmark.
6. There is a `frontend-v2/` directory, but the primary app mounts `frontend/`; V2 is not shown as the served production UI.
7. Some documented claims need reconciliation with the implementation; see “Claims to correct or verify.”

**Development dates, team, individual ownership, and AI assistance:** **I don't know yet.** Git history and source alone do not prove who authored each feature, which parts were AI-assisted, or when development began/ended. Prepare an honest contribution list from your own work records and commits. Do not say “I built every module” based only on repository presence.

### 2. Complete folder structure and responsibilities

Primary repository areas (not every generated/cache file):

```text
api/
  index.py                    Vercel adapter that exposes app.main:app
app/
  main.py                      Main FastAPI application, lifespan, router/static mounting
  _server.py                   Local/backward-compatible Uvicorn entry point
  config.py                    Environment loading and runtime constants/feature flags
  clients/
    groq.py                    Groq chat and BGE embedding wrappers
    pool.py                    MongoDB, Neo4j, Supabase, HTTP clients, cache and rate-limit state
  core/
    document.py                PDF retrieval, extraction, chunking and embedding/cache helpers
    exceptions.py              Domain exceptions
    generation.py              Prompts and answer verification
    graph.py                   Neo4j query/traversal logic
    planner.py                 LLM query planning and fallback parsing
    retrieval.py                Vector, ArXiv, ranking and context utilities
  embeddingService/
    embeddings.py               Embedding service implementation
  models/
    auth.py, chat.py, research.py  Pydantic request schemas and QueryPlan
  routes/
    auth.py                     Auth, account/history/payment route handlers
    chat.py                     Chat and OpenAI-compatible chat-completions routes
    graph.py                    Graph-specific API routes
    health.py                   Health/readiness/runtime configuration routes
    media.py                    PDF and audio endpoints
    research.py                 Main research, timeline, survey and bulk routes
  sources/
    core.py, kaggle.py, openalex.py, papers_with_code.py,
    semantic_scholar.py, wikipedia.py  External source adapters
  utils/
    auth.py                     JWT/password/user-context helpers
    conversation.py             Conversation context/summarization helpers
    credits.py                  Rate limit, plans, credit checks/snapshots
    misc.py                     Link and dataset/repository utilities
  Dockerfile, requirements.txt  Standalone service packaging
assets/                          Architecture illustrations and UI image assets
docs/
  backend/                       Detailed backend reference documents
  aether_interview_prep.md       This interview preparation guide
frontend/                        Main static HTML/CSS/vanilla-JS client
frontend-v2/                     Alternate/newer frontend files; not mounted by main app
ingestion/                       Local ingestion/upload scripts and checkpoints
knowledge_graph/                 Graph schema, seed/demo and traversal utilities
scripts/                          Environment, frontend, evaluation and live-test scripts
tests/
  unit/                           Unit tests
  integration/                    Mocked API/source integration tests
  ...                             Connectivity, embedding and import tests
```

### 3. Technology stack and defensible rationale

| Technology | Actual role | Defensible reason / boundary |
|---|---|---|
| Python, FastAPI, Pydantic | API application, routers, request validation | Async-friendly API framework and typed request models; the repository uses one app with modular routers. |
| Uvicorn | ASGI server for local/hosted app | Runs the FastAPI ASGI app. |
| Vanilla HTML/CSS/JavaScript | Browser UI | The primary frontend is static and served by FastAPI; it calls same-origin `/api/...` endpoints. |
| Groq API + `httpx` | LLM requests and audio transcription calls | Async HTTP client reuse through a bounded Groq `AsyncClient`; current default model names are in `app/config.py`. |
| MongoDB / PyMongo | Users, chat history, payments, uploaded PDF text | Synchronous Mongo operations are generally moved to worker threads with `asyncio.to_thread`. |
| Neo4j | Optional paper/author/venue/topic/citation graph | Graph queries are implemented, but connection/retrieval is skipped when `FREEZE_RETRIEVAL=true`. |
| Supabase / pgvector | Optional paper-chunk vector/hybrid search RPCs | Retrieval functions exist, but vector operations are skipped by the same freeze flag. |
| PyMuPDF (`fitz`) | Extract text from PDF pages | Used on a worker thread; scanned-image OCR is not implemented. |
| BGE (`BAAI/bge-base-en`) | Optional 768-dimensional text embeddings | Configured model; may load locally if `sentence-transformers` is installed or use Hugging Face Inference API when retrieval is enabled. |
| ArXiv public XML API | Search and retrieve paper metadata and abstracts | Called directly from the main application. |
| PyJWT + bcrypt | Signed JWTs and password hashing | Auth implementation uses HS256 and bcrypt password hashes. |
| Upstash Redis | Optional PDF chunk/embedding cache | In-memory fallback exists; general application cache is also in process memory. |
| Razorpay / Stripe | Payment-related routes | Razorpay order/verification APIs and a Stripe webhook handler exist. Feature use/deployment is not confirmed. |

Why each was originally selected beyond these code-level roles: **I don't know yet** unless you can confirm your design reasoning from personal experience.

### 4. Frontend-to-backend architecture

1. `app/main.py` creates the main `FastAPI` object, configures CORS, registers routers, and serves the `frontend/` directory.
2. `frontend/js/state.js` sets `API_BASE` to `window.location.origin`.
3. `frontend/js/chat.js` sends the first user query to `POST /api/research`; subsequent turns are sent to `POST /api/chat`.
4. The frontend attaches a bearer token when available and sends query parameters such as `top_k`, `min_similarity`, `temperature`, `use_heavy`, `verify`, and `mode`.
5. The backend returns JSON; frontend JavaScript renders the answer and source panels.
6. `api/index.py` imports the same `app.main:app` for Vercel. It is not a second application.

There is no API gateway or queue visible in the repository. Internal features communicate through Python function calls and shared client/database modules; the browser uses HTTP. ArXiv search uses its public XML API directly.

### 5. Number of apps, processes, and “microservices” claim

- **Main backend:** one FastAPI app (`app.main:app`) with routers for health, auth/history/payment, graph, research, chat, and media.
- **Compatibility entry points:** `app._server` and `api.index` import/expose the same main app; they do not create more backend services.
- **Process count:** depends on deployment. The main app can be one process with configurable Uvicorn workers. This is not a fixed microservice count.
- Research, auth, payment, and AI processing are **not** separate services. They are routers/modules in the main API.
- They cannot be independently deployed from this design without extracting them.
- No message queue is present in the primary execution flow.

**Accurate architecture label:** “A modular FastAPI monolith,” not “a microservices architecture.”

### 6. FastAPI entry point, routers, and lifespan

The canonical app is in `app/main.py`. Its lifespan calls `await pool.init()` at startup and `await pool.close()` at shutdown. It includes:

- health
- authentication plus history/payment routers
- graph
- research
- chat
- media

`app/_server.py` is the documented local Uvicorn entry point; `api/index.py` is the Vercel entry point. The main app also mounts static frontend files.

### 7. Research request schema

`POST /api/research` accepts `ResearchRequest` from `app/models/research.py`:

| Field | Default / validation |
|---|---|
| `query` | Optional string, maximum 2,000 characters |
| `text` | Optional compatibility alternative, maximum 2,000 characters |
| `top_k` | 8; integer 1–20 |
| `min_similarity` | 0.28; float 0–1 |
| `use_heavy` | `false` |
| `verify` | `true` |
| `filters` | Optional dictionary |
| `temperature` | 0.0; float 0–2 |
| `mode` | `"research"` |

`query` and `text` are whitespace-trimmed. `resolved_query()` uses whichever is supplied and raises HTTP 400 if neither is present. Pydantic checks field constraints before the route executes.

`POST /api/chat` instead accepts `ConversationRequest`: a non-empty `messages` array of `{role, content}` objects and similar retrieval/model controls, plus `last_paper_context`.

### 8. Full first-query lifecycle (source-grounded)

```text
Browser sendQuery()
  -> POST /api/research {query, model/retrieval controls}
  -> FastAPI validates ResearchRequest
  -> research() assigns request_id and applies REQUEST_TIMEOUT (default 60s)
  -> _research_impl(): readiness -> IP rate limit -> optional JWT user context
  -> plan lookup (plan/credits; credit deductions are disabled by default)
  -> detect Wikipedia prefix / paper URL / PDF route if applicable
  -> plan_query(query): cached strategic LLM call or fallback plan
  -> execute the selected route
  -> for standard RAG, concurrently request graph, vector, ArXiv, Semantic Scholar, CORE
  -> enrich/deduplicate/rank returned papers; gather related code/dataset metadata
  -> label datasets linked/mentioned by a paper; filter catalog additions against query terms
  -> build route-specific grounded context/prompt
  -> Groq synthesis
  -> optional second LLM verification if enabled and evidence was retrieved
  -> response JSON + credit snapshot
  -> browser renders answer and sources
```

Important default behavior and caveats:

- `FREEZE_RETRIEVAL` defaults to true. Graph and Supabase vector retrieval return no data; the planner and external-source route still exist.
- On the main full-RAG path, the code explicitly returns a “couldn't retrieve sources” response without generating an answer if all evidence lists are empty.
- Some specialized graph-only branches have separate fallback behavior when their graph lookup is empty; do not claim every route has the same no-evidence behavior.
- `set_user_context()` treats an absent/invalid bearer token as an anonymous request rather than universally requiring login. Individual endpoints may separately require auth.
- Provider/source errors are not all treated alike: some retrieval failures become warnings/empty results, others raise HTTP errors. Inspect the selected route before promising uniform failover.

### 9. Request and response shapes

Typical research response fields in `app/routes/research.py`:

```json
{
  "request_id": "...",
  "answer": "...",
  "route": "rag",
  "plan": {"standalone_query": "...", "reasoning_path": "..."},
  "papers": [],
  "chunks": [],
  "arxiv_papers": [],
  "s2_papers": [],
  "datasets": [],
  "code_repos": [],
  "verification": null,
  "latency_ms": 0,
  "model_used": "...",
  "warning": null,
  "credits": {}
}
```

Fields can differ for special routes, such as PDF summary, uploaded-PDF QA, Wikipedia, direct lookups, or no-evidence responses.

### 10. Personal contribution

**Repository evidence cannot answer this.** For interview preparation, write a verified contribution statement for each module you personally changed, distinguish team/third-party/AI-assisted work, and be able to describe one concrete decision and debugging episode. **I don't know yet** which of these changes are yours.

## Batch 2 — Planner, retrieval, embeddings, and graph

### 11. Query planner and business logic

`plan_query(query, context="")` is in `app/core/planner.py`. It:

1. Builds a prompt from the planner template.
2. Uses `PLAN_MODEL` (default `openai/gpt-oss-120b`) at temperature 0.
3. Requests structured plan output and parses/sanitizes it.
4. Produces a `QueryPlan` dataclass.
5. Caches plans under a versioned cache key; if planning/parsing fails, uses a fallback plan.

`QueryPlan` includes `standalone_query`, `route`, `graph_anchors`, `vector_keywords`, `required_metrics`, `reasoning_path`, `ambiguous`, `cache_key_str`, raw planner data, named entities, `search_tiers`, `depth`, and answer requirements.

The accepted route strings in the code are:

`entity_lookup`, `structured`, `title_lookup`, `compare`, `timeline`, `survey`, `conceptual`, `rag`, `chitchat`, and `context_only`.

That is **10**, despite README references to “9 routes.” The planner's prompt, parser, route dispatch, and default route should be checked together before claiming all routes are fully implemented. The normal research path invokes a single planner call, with caching/fallback to reduce repeated calls.

Follow-up context is more complete in the chat endpoint: it builds a compact context from recent user/assistant messages and uses conversation helpers. The frontend switches to `/api/chat` after the first exchange. Do not claim planner-based pronoun resolution always works; the exact handling depends on the planner/context prompt and is not guaranteed.

If a plan is invalid, planner code sanitizes the route to `"rag"` and falls back to the original query when the standalone query is absent/invalid. If the planner call fails, it builds a fallback plan. This is graceful fallback, not proof the chosen route is correct.

### 12. Embedding and vector retrieval

**Configured model:** `BAAI/bge-base-en` (`EMBED_MODEL` default), 768 dimensions according to project configuration/README. `create_embedding()` prefixes input with “Represent this sentence for searching relevant passages:” and L2-normalizes the vector.

**Embedding providers:** local `SentenceTransformer` if import and initialization succeed; otherwise Hugging Face Inference API requiring `HF_TOKEN`. In frozen mode, embedding generation returns a zero vector instead of making an embedding request.

**Storage/search:** Supabase RPC methods `match_paper_chunks` and `hybrid_search` are in `app/core/retrieval.py`; current vector search is skipped when `FREEZE_RETRIEVAL=true`. The repository does not establish that the database is active or contains a current corpus.

**Chunking:** PDF helper `chunk_document_text()` uses a pure-Python recursive separator strategy with defaults of 1,000 characters and 200-character overlap. It is documented as mirroring LangChain's recursive splitter; this code path does not require the LangChain library. Do not confuse uploaded PDF chunking with the paper ingestion pipeline.

**Filtering/ranking helpers:** relevance floor defaults to `0.22`; `min_similarity` can be provided by the request. The vector module contains cosine-based MMR logic (default lambda `0.6`), chunk merging, and context packing (default 5,000 token estimate using 4.2 characters per token). `paper_relevance_score()` performs token-overlap filtering on external papers.

**Important implementation mismatch to fix before enabling vector retrieval:** the current call in `run_vector_pipeline()` does not match the declared `mmr_rerank(chunks, query_emb, top_k, lam=...)` signature: it passes the query string as the second positional argument and an unsupported `lambda_param` keyword. With vector results present this path appears to fail. Retrieval is frozen by default, so this may not be exercised in the normal configuration. Do not describe the vector pipeline as “ready to activate” until this is corrected and tested.

**RRF:** a `reciprocal_rank_fusion()` helper exists, but repository search found no caller in the application pipeline. The actual current `run_vector_pipeline()` code does not invoke it. README claims about active RRF fusion should be treated as unverified/overstated.

Know the concepts, but keep the implementation claims precise:

- Cosine similarity compares vector direction; with L2-normalized vectors, dot product equals cosine similarity.
- Precision asks how many returned results are relevant; recall asks how many relevant results were found.
- RRF combines ranked lists using reciprocal rank contributions, commonly `1 / (k + rank)`. The helper exists, but is not wired into the main pipeline.
- MMR trades query relevance against similarity to already selected results. Its helper exists; its currently wired invocation appears mismatched as noted above.
- If embedding model/dimension changes, corpus vectors and database schema/index/RPC expectations must be regenerated or migrated consistently. The README says the pgvector schema expects 768-dimensional vectors.
- No Recall@K, MRR, precision, or embedding migration study was found in the cited evaluation artifacts. **I don't know yet** what offline ground-truth dataset you used.

### 13. Graph database and graph retrieval

`app/core/graph.py` uses Neo4j Cypher. It starts with seed publications found by title/author keyword matching, expands to cited/citing papers, co-author papers, venue peers, and `SIMILAR_TO` neighbors, deduplicates and ranks results, and can derive citation relationships/path information.

The graph model in `knowledge_graph/schema_and_seed.py` includes:

- Nodes: `Publication`, `Author`, `Venue`, `Topic`.
- Relationships: `CITES`, `WRITTEN_BY`, `PUBLISHED_IN`, `HAS_TOPIC`, `SIMILAR_TO`.
- `CITED_BY` is described in some docs as the reverse direction of `CITES`; verify actual stored edge conventions before describing it as a separate relationship type.

Cypher example from `app/core/graph.py`:

```cypher
MATCH (p:Publication)
WHERE toLower(p.title) CONTAINS toLower(kw)
   OR EXISTS {
       MATCH (p)-[:WRITTEN_BY]->(a:Author)
       WHERE toLower(a.name) CONTAINS toLower(kw)
   }
RETURN p.research_id AS research_id, p.title AS title, p.year AS year
```

Graph traversal can support multi-hop relationships by following paths such as citation edges; graph retrieval is **not** a GNN and does not perform neural message passing. Results can be supplied as context alongside other source data when retrieval is active.

**Current operational status:** `FREEZE_RETRIEVAL=true` by default skips Neo4j initialization and graph retrieval. README states the database is paused. It reports 196,875 nodes and 398,961 relationships, but this guide has not verified those counts against a live database or dated export. State them only as README-reported historical figures, not current live metrics.

## Batch 3 — Sources, LLM, prompts, and verification

### 14. Academic-source integrations

ArXiv search calls the public XML API directly through
`retrieve_arxiv_context()` in `app/core/retrieval.py`. Other source adapters
include Semantic Scholar, CORE, OpenAlex enrichment, Papers With
Code/Hugging Face metadata, Wikipedia, and Kaggle. These are adapters, not
guaranteed always-on providers.

Normalization/deduplication happens in source adapters and research route logic; for example, paper title strings are cleaned for matching and candidates are filtered for query relevance. Some providers are used for enrichment rather than primary search in the main route. External APIs have their own timeouts/rate behavior; do not claim a uniform retry policy for all providers.

### 15. LLM choice, prompts, and retries

Runtime model defaults from `app/config.py`:

- `REASON_MODEL`: `openai/gpt-oss-120b`
- `HEAVY_MODEL`: `openai/gpt-oss-120b`
- `PLAN_MODEL`: `openai/gpt-oss-120b`

They are environment-overridable. The README's model claim (`llama-3.3-70b-versatile`) conflicts with these code defaults; explain the configured deployment value only after checking deployment environment variables.

`groq_chat()` sends OpenAI-compatible chat completion requests to Groq with the selected model, messages, temperature, and max token limit. At temperature 0 it can use an in-memory LLM cache. It rotates among configured Groq API keys, responds to HTTP 429 using key rotation and a short `Retry-After` wait, compresses messages on 413, and raises `LLMError` after exhausted attempts. These are retry/fallback behaviors, not an SLA.

For regular research, the route chooses a prompt template based on the planned route (`grounded`, `compare`, `survey`, `conceptual`, or `timeline`); high-depth paths may fetch selected primary-source passages and append a requirements coverage warning. Generation is asynchronous at the HTTP call level. Do not say all external/DB operations are non-blocking: PyMongo and Neo4j are synchronous clients and need thread offloading, while several source adapters use async HTTP.

### 16. Citation grounding and verification

`app/core/generation.py` builds source sections from chunks and paper records, then asks a second LLM call to check answer claims only against supplied sources. The prompt asks for confidence, verified/total claims, flagged unsupported claims, and a `PASS`, `PARTIAL`, or `FAIL` verdict. `apply_verification()` appends a visible warning for partial/failing checks and warns if the check is skipped because the LLM failed.

Distinguish:

- **Citation/source retrieval:** finding paper records and source links/metadata.
- **Citation generation:** the model mentioning sources in an answer.
- **Verification:** a model comparing claims with retrieved source text/abstracts.

This does not prove that every citation is valid, that every source supports the exact claim, or that hallucinations are eliminated. Confidence is parsed from model output; it is not a calibrated probability unless separately evaluated. A model checking another model can miss errors or share the same misconception. Contradictions need explicit source comparison; no guarantee of comprehensive contradiction resolution is established.

Zero hallucinations: **No.** Say “the system adds evidence-grounded prompting and an LLM verification/warning pass to reduce risk,” not “it guarantees no hallucinations.”

## Batch 4 — FastAPI, async, databases, auth, and PDFs

### 17. FastAPI and asynchronous programming

- FastAPI routers group related HTTP handlers; Pydantic validates request bodies.
- `async` defines coroutine functions; `await` yields while awaiting asynchronous work, allowing the event loop to serve other tasks.
- Concurrency is overlapping progress on multiple operations; parallelism is simultaneous execution on multiple CPU cores.
- Threads can offload blocking I/O; processes are generally used for CPU parallelism/isolation.
- `asyncio.gather()` runs independent awaitables concurrently; code uses it for multiple retrieval calls and enrichment tasks.
- `asyncio.wait_for()` applies an overall request timeout; main research/chat routes use `REQUEST_TIMEOUT` (default 60 seconds).
- Synchronous PyMongo and Neo4j calls are commonly run through `asyncio.to_thread`. PyMuPDF extraction is also moved to a worker thread. This does not make all code non-blocking.
- The pool lifespan initializes/closes Mongo and HTTP/database clients. Groq `httpx.AsyncClient` is configured with a 30-connection / 15-keepalive limit and a configured timeout.
- Uvicorn defaults to one worker in the app entry point unless `WORKERS` is set. In-memory cache/rate-limit state is per process and is not shared across workers or serverless instances.

### 18. Databases, auth, and security-related implementation facts

**MongoDB collections used in code:** `users`, `payments`, `chat_sessions`, and `uploaded_pdfs`. User records include email, bcrypt hash, profile metadata, plan, credit state, and timestamps. History/payment/PDF records use user identifiers or document IDs as applicable. MongoDB is the account/history store; Supabase is not used for login.

**Other storage/cache:** Neo4j for optional graph retrieval; Supabase Postgres/pgvector for optional chunks; optional Upstash Redis for PDF cache; in-process dictionaries for application cache and rate limiter.

**JWT:** HS256 signed token includes user ID (`sub`), email, expiry (default seven days). The token is sent in an `Authorization: Bearer ...` header. bcrypt hashes passwords. Authenticated endpoints resolve the user against MongoDB. Some research routes allow anonymous access, so avoid saying all private routes are universally protected without checking each route.

**Rate limiting:** in-process sliding-window timestamps keyed by client IP, default 30 requests/minute. It is not a distributed limiter when multiple workers/serverless instances are used.

**CORS and secrets:** settings are environment-driven. Do not disclose keys. `app/config.py` has a development fallback JWT secret and a permissive default origin list including `*`; production must override these settings. This is a deployment configuration risk to remediate, not a feature to advertise as production-safe.

**Redis:** the code initializes an Upstash client only when URL/token are configured and keeps local cache fallback dictionaries. Do not describe Redis as required or as the global cache for all data.

### 19. PDF processing

1. Browser uploads `multipart/form-data` to `POST /api/upload/pdf`.
2. Handler checks `.pdf` extension, declared content length and actual file size (default maximum 20 MiB).
3. PyMuPDF extracts text from pages on a worker thread.
4. Empty/unreadable PDFs return 400; oversized PDFs return 413; processing errors return 500.
5. Extracted text (up to the configured MongoDB-friendly character cap) is stored in MongoDB `uploaded_pdfs`.
6. Handler returns an internal `/api/pdf/{id}.pdf` URL.
7. For PDF QA, the research route retrieves relevant chunks and prompts the model to answer only from the provided PDF context. The PDF QA route bypasses external sources.

Remote ArXiv/PDF links can also be downloaded and parsed with size guards. PDF text chunking uses 1,000-character chunks with 200-character overlap. PDF chunks/embeddings can be cached in Upstash Redis or process memory. **Scanned PDFs/OCR are not supported by this extraction path.** No automatic retention/deletion policy for uploaded PDFs is established in the inspected code; do not promise permanent or temporary storage behavior without checking deployment/data lifecycle.

Bibliographic citations to the original uploaded paper are not automatically guaranteed merely because the PDF was uploaded; the PDF QA route returns retrieved PDF chunks as source-panel context and asks the LLM to cite relevant sections/findings.

## Batch 5 — Credits, payments, deployment, tests, and resume claims

### 20. Credits, subscriptions, payments, and history

The code supports `free` and `pro` plans, per-action credit costs, a daily free allowance (default 20), and top-k limits. **Credit enforcement is disabled by default** (`CREDIT_SYSTEM_ENABLED=false`); in that mode checks are skipped and response snapshots report unlimited credits. Plan checks/Pro feature gates exist separately, so check exact route behavior before describing pricing.

Razorpay routes create an order and verify payment using HMAC-SHA256 with constant-time signature comparison. A Stripe webhook handler also exists for subscription events. Configuring credentials and deploying the correct payment integration is required; the presence of routes does not prove payments are live.

MongoDB has a unique index on `payments.razorpay_payment_id`; the repository does not establish complete end-to-end idempotent billing behavior or webhook/reconciliation guarantees. Do not promise refund, duplicate-event, or failed-request credit compensation unless separately tested.

Chat history endpoints store and query MongoDB documents associated with the authenticated `user_id`. The browser also sends recent message history to the chat endpoint. Verify exact session behavior and access controls before claiming all conversation data paths have been tested.

### 21. Deployment and operational shape

- Main FastAPI app: local Uvicorn entry point `app._server:app`; Vercel serverless entry `api/index.py`.
- `vercel.json` sends incoming routes to `api/index.py` and includes `frontend/**`.
- GitHub Actions CI checks Ruff, mypy on source modules, environment validation, unit tests, and mocked integration tests.
- CD workflow builds/deploys to Vercel when credentials are configured; it can skip deployment when the Vercel token is absent.
- `frontend-v2/` is not included in the Vercel include pattern or mounted by `app/main.py`; treat it as an alternate/experimental frontend unless deployment proves otherwise.

### 22. Testing, quality, and performance claims

Tests present include unit tests for cache, rate limiting, credits, text processing, sources, planner fallback, generation grounding, upload limits, and integration tests with mocked provider/API responses. There is also connectivity/evaluation tooling.

Evidence observed while preparing this guide:

- Full local `python -m pytest -q` run failed on two audio upload size-guard tests (`500` returned where `413` was expected).
- Running `TestTranscribeAudio` alone passed all seven tests. This points to an order/shared-state/environment interaction worth investigating; it does **not** mean the full suite passes.
- The checked-in `connectivity_report.json` is dated 2026-10-29 and records DNS resolution failures for Supabase and Neo4j. It is not a current health measurement.
- Evaluation JSON artifacts are individual examples, not a repeatable, ground-truth benchmark. One stored example has zero retrieved papers/datasets. These files do not establish answer accuracy or citation precision.
- No load-test result, concurrent-user count, throughput figure, p95/p99 latency, Recall@K, MRR, or calibrated verification accuracy was located in the examined repo.

Answer to “high-throughput?”: **I don't know yet; there is no measured throughput result in the available evidence.** Give a throughput claim only with request mix, concurrency, duration, hardware/runtime, success rate, and latency percentiles.

Answer to “10,000 users?”: not measured. A credible scale plan would require stateless workers, shared distributed rate limiting/cache, database connection/pool planning, provider quotas, queueing for long jobs, observability, load tests, and cost controls. This is a future architecture plan, not a current capability.

### 23. Active, paused, incomplete, and experimental status

| Feature | Repository status | What can safely be said |
|---|---|---|
| Main FastAPI API and primary frontend | Implemented | The `frontend/` static UI is mounted by the main app. Deployment health is not proven by code alone. |
| Groq planning/synthesis | Implemented and required for readiness | Runtime model is environment-configurable; defaults in `app/config.py` are GPT-OSS 120B. |
| External research sources | Implemented with provider-dependent behavior | ArXiv, S2, CORE, and enrichment adapters are present; provider availability/configuration varies. |
| Mongo auth/history/PDF storage | Implemented | MongoDB is initialized at startup; runtime data and production configuration are not verified here. |
| Neo4j graph search | Implemented but paused by default | `FREEZE_RETRIEVAL=true` skips it. |
| Supabase pgvector/hybrid retrieval | Implemented but paused by default | Freeze flag skips search; current MMR wiring mismatch must be resolved before claiming activation-ready. |
| In-process RRF | Helper only | Defined but not found wired into current research pipeline. |
| Answer verification | Implemented as an optional second LLM check | Warning/flags are generated; not a correctness guarantee. |
| Credit enforcement | Implemented but off by default | Requires `CREDIT_SYSTEM_ENABLED=true`. |
| Payments | API handlers exist | Live provider credentials, production payment reconciliation, and end-to-end tests are unverified. |
| OCR for scanned PDFs | Not implemented in inspected path | Text extraction only. |
| `frontend-v2/` | Alternate/unintegrated | Main app serves `frontend/`; V2 endpoint wiring should be checked before activation. |
| GNN-based GraphRAG | Not found | The graph traversal is database/Cypher-based, not a Graph Neural Network. |

### 24. Resume-claim audit and honest interview answers

| Claim/question | Defensible answer |
|---|---|
| “Microservices” | Replace with “modular FastAPI backend.” |
| “High throughput” | Not established without load-test numbers. |
| “Agentic” | The query planner selects among route types and retrieval strategies. There is no evidence here of a multi-agent framework or autonomous long-running agent loop. Prefer “LLM-assisted query planning and routing.” |
| “Automated verification guardrails” | A second LLM checks generated claims against retrieved text and returns a verdict/confidence/flagged claims. It is an imperfect heuristic, not a guarantee. |
| “Citation-backed” | The response includes source metadata and prompts for grounding/citations. Citation correctness must be evaluated; do not claim every statement is verified. |
| “Graph neural network” | Not implemented in this repo. Neo4j Cypher graph retrieval exists but is paused by default. |
| “GraphRAG active in production” | Not established. Default configuration freezes graph/vector retrieval. |
| “Redis caching” | Optional Upstash cache is used in selected PDF paths; general in-memory cache is process-local. |
| “No hallucinations” | Not supportable. The system can reduce risk and surface warnings, not guarantee zero hallucinations. |
| “Production active” | Deployment status must be verified with current environment and service health; source code alone is not evidence. |
| “I personally built…” | **I don't know yet.** Confirm against your commits and actual work. |

### 25. “What would you improve?” and design trade-offs

Grounded improvement answers:

1. Fix and test the MMR call contract before unfreezing vector retrieval; add an integration test that exercises non-empty vector results.
2. Make RRF wiring intentional: either integrate it and test multi-list ranking, or remove/qualify the claim.
3. Resolve docs/config mismatches: nine vs ten route names, seven vs eight listed integrations, README model vs config defaults, and graph-count provenance.
4. Run the entire test suite in CI and eliminate the two observed full-suite audio failures.
5. Add a reproducible retrieval-quality benchmark with labeled relevance, Recall@K/MRR, citation support measures, and regression thresholds.
6. Add load tests with concurrency and p50/p95/p99 latency, error rate, timeout behavior, and provider/database limits.
7. Add distributed rate limiting/cache if scaling to multiple workers/serverless instances.
8. Configure secure production secrets/origins and verify upload ownership/access and data retention behavior.
9. Keep “paused,” “active,” and “experimental” indicators synchronized between README, code defaults, and deployment settings.

Trade-off questions:

- **Remove planner:** the application loses structured intent classification, query rewriting, route selection, and extracted anchors/search tiers; fallback routing may be less tailored.
- ArXiv uses the public XML API directly; no separate protocol service is required.
- **Remove Neo4j:** no graph result context or relationship traversal; external-source search can still serve the default frozen configuration.
- **Remove Redis:** PDF cache can fall back to process-local memory, which is not shared and may disappear on restart.
- **Remove verification:** fewer latency/cost calls, but no second-pass unsupported-claim warning.
- **Architecture choice:** modular monolith reduces operational complexity for a single product; the repo does not document a measured comparison that proves it was selected for a specific throughput target.

## Claims to correct or verify before interviews

1. **Model mismatch:** `app/config.py` defaults to `openai/gpt-oss-120b`; README advertises `llama-3.3-70b-versatile`. Ask what the deployed environment actually sets.
2. **Route count:** planner accepts ten route names while README calls them nine.
4. **RRF status:** helper exists but was not found called from the application pipeline.
5. **Vector activation:** the MMR call/signature mismatch appears to break the non-empty path.
6. **Graph scale:** README's 196,875 node / 398,961 relationship figures need a dated live query/export to be defensible.
7. **Reliability:** two full-suite failures were observed despite the focused test passing.
8. **Current backend status:** connectivity report is stale and shows DNS errors; run fresh checks from the target deployment environment.
9. **Resume ownership/dates:** establish from personal history, commits, and project records; repository contents alone are insufficient.

## Repository references

- Application composition and startup: `app/main.py`, `app/_server.py`, `api/index.py`
- Runtime settings: `app/config.py`
- Main request schemas: `app/models/research.py`, `app/models/chat.py`, `app/models/auth.py`
- Main research execution: `app/routes/research.py`
- Chat execution/conversation: `app/routes/chat.py`, `app/utils/conversation.py`
- Planner: `app/core/planner.py`
- Retrieval and ranking: `app/core/retrieval.py`
- Graph retrieval: `app/core/graph.py`, `knowledge_graph/schema_and_seed.py`
- Prompting/verification: `app/core/generation.py`
- LLM and embeddings: `app/clients/groq.py`
- Database clients/cache: `app/clients/pool.py`
- Auth and credits: `app/utils/auth.py`, `app/utils/credits.py`, `app/routes/auth.py`
- PDF/audio: `app/core/document.py`, `app/routes/media.py`
- Frontend request flow: `frontend/js/chat.js`, `frontend/js/state.js`
- Deployment and CI/CD: `vercel.json`, `.github/workflows/ci.yml`, `.github/workflows/cd.yml`
- Test suites: `tests/unit/`, `tests/integration/`

## Personal facts to fill in (do not guess)

- Development start/end dates: **I don't know yet.**
- Team size and collaborators: **I don't know yet.**
- Modules personally authored/modified: **I don't know yet.**
- AI-generated or externally contributed work: **I don't know yet.**
- Hardest bug and how you diagnosed it: **I don't know yet.**
- A production deployment you personally verified: **I don't know yet.**
- Measured throughput/concurrent users/latency percentiles: **I don't know yet.**
- Retrieval/citation benchmark and score: **I don't know yet.**
