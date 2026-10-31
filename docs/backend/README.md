# Aether Backend Documentation

This directory is a source-grounded guide to the backend as it exists in this
repository. It distinguishes runtime behavior from code that is present but
paused, optional, legacy, or not independently verified. Start with the master
overview, then use the endpoint and workflow guides to trace a request.

## Reading order

1. [00. BACKEND MASTER OVERVIEW](./00.%20BACKEND%20MASTER%20OVERVIEW.md)
2. [01. BACKEND ARCHITECTURE](./01.%20BACKEND%20ARCHITECTURE.md)
3. [02. API ENDPOINT DOCUMENTATION](./02.%20API%20ENDPOINT%20DOCUMENTATION.md)
4. [03. BUSINESS LOGIC AND EXECUTION FLOWS](./03.%20BUSINESS%20LOGIC%20AND%20EXECUTION%20FLOWS.md)
5. [04. FUNCTION AND CLASS LEVEL DOCUMENTATION](./04.%20FUNCTION%20AND%20CLASS%20LEVEL%20DOCUMENTATION.md)
6. [05. AI AND RAG PIPELINE](./05.%20AI%20AND%20RAG%20PIPELINE.md)
7. [06. QUERY PLANNER DEEP DIVE](./06.%20QUERY%20PLANNER%20DEEP%20DIVE.md)
8. [07. DATABASE AND DATA MODEL DOCUMENTATION](./07.%20DATABASE%20AND%20DATA%20MODEL%20DOCUMENTATION.md)
9. [08. EXTERNAL INTEGRATIONS](./08.%20EXTERNAL%20INTEGRATIONS.md)
10. [09. AUTHENTICATION AND SECURITY](./09.%20AUTHENTICATION%20AND%20SECURITY.md)
11. [10. ASYNC PROGRAMMING AND PERFORMANCE](./10.%20ASYNC%20PROGRAMMING%20AND%20PERFORMANCE.md)
12. [11. ERROR HANDLING AND RELIABILITY](./11.%20ERROR%20HANDLING%20AND%20RELIABILITY.md)
13. [12. TESTING AND DEPLOYMENT](./12.%20TESTING%20AND%20DEPLOYMENT.md)
14. [13. INTERVIEW PREPARATION](./13.%20INTERVIEW%20PREPARATION.md)

## Scope and evidence

The application backend inventory contains Python files under `app/`,
alongside the Vercel entry point, offline Neo4j tooling, Supabase ingestion
scripts, CI/deployment configuration, and tests. The docs describe the main
FastAPI application and adjacent tools, and mark components that are not
mounted by `app.main:app`.

The checked-in configuration defaults to `FREEZE_RETRIEVAL=true` and
`CREDIT_SYSTEM_ENABLED=false`; runtime environment values can override them.
The local server had been observed returning ready during the preceding work,
but that is not evidence that every external provider or production deployment
is healthy.

## Documentation coverage notes

- The API catalog covers the route decorators found in `app/routes/`, as well
  as the static and health aliases. A separate table covers the standalone
  `knowledge_graph.api:app`; it is not included by the main application.
- Database fields are listed only where visible in inserts, updates, or query
  code. Supabase remote table definitions and production Neo4j contents cannot
  be inferred from this repository.
- Test file names and CI commands are inventoried. No overall coverage
  percentage is claimed because no coverage report was generated for this
  documentation task.
- Project duration, individual authorship, and a live production deployment
  cannot be established from source code.

## Suggested source files to study

For an interview walkthrough, prioritize
[`app/main.py`](../../app/main.py),
[`app/routes/research.py`](../../app/routes/research.py),
[`app/routes/chat.py`](../../app/routes/chat.py),
[`app/core/planner.py`](../../app/core/planner.py),
[`app/core/retrieval.py`](../../app/core/retrieval.py),
[`app/core/generation.py`](../../app/core/generation.py),
[`app/clients/pool.py`](../../app/clients/pool.py),
[`app/clients/groq.py`](../../app/clients/groq.py),
[`app/routes/auth.py`](../../app/routes/auth.py), and
[`app/routes/media.py`](../../app/routes/media.py).
