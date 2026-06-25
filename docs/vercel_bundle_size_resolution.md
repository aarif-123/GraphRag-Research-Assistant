# Vercel Deployment Resolution: Bundle Size Exceeded

This document outlines the investigation, root cause, and resolution for the Vercel deployment error where the bundle size exceeded the maximum function size limit.

---

## 1. Problem Statement

During deployment to Vercel, the build process failed with the following error:
> **Total bundle size (4925.99 MB) exceeds the maximum function size (500 MB). Reduce the size of your dependencies or split your application into smaller functions.**

The bundle size of **4925.99 MB (~4.9 GB)** is exceptionally large for a standard serverless function, far exceeding Vercel's limit of **500 MB** for Python runtimes.

---

## 2. Root Cause Analysis (RCA)

1. **Dependency Analysis**:
   The root `requirements.txt` file in the project contained `sentence-transformers`.
   
2. **Heavy Transitive Dependencies**:
   `sentence-transformers` is built on top of **PyTorch (`torch`)**. When pip installs PyTorch in Vercel's standard Linux build container, it defaults to downloading the standard PyPI wheels, which include complete GPU/CUDA support binaries.
   - The Linux package installs dependencies like `nvidia-cudnn-cu12`, `nvidia-cublas-cu12`, `nvidia-cusolver-cu12`, `nvidia-cusparse-cu12`, and other massive CUDA shared libraries.
   - When extracted, these packages occupy **4.5 to 5.0 GB** of disk space.

3. **Vercel's Package Detection**:
   Vercel's Python runtime (`@vercel/python`) scans the repository root for a dependency file (`requirements.txt`, `pyproject.toml`, or `Pipfile`) to build the serverless functions. Because `requirements.txt` at the root contained `sentence-transformers`, Vercel attempted to bundle it, leading to the size violation.

---

## 3. Resolution Details

### A. Runtime Fallback Support (Already in Code)
The API codebase ([app.py](file:///c:/Users/Mohmmed%20Aarif/projects/production/GraphRag-Research-Assistant/app/app.py)) is already designed to run gracefully without `sentence-transformers` installed:
- It handles `ImportError` when importing `SentenceTransformer` and defaults to `None`.
- If `SentenceTransformer` is not available, the embedding generation function ([app.py:L1272-1290](file:///c:/Users/Mohmmed%20Aarif/projects/production/GraphRag-Research-Assistant/app/app.py#L1272-L1290)) automatically falls back to utilizing the remote **HuggingFace Inference API** via HTTP requests using `httpx`.
- Therefore, PyTorch and SentenceTransformers are **only** needed for offline local ingestion scripts (located in `ingestion/`), and are **not** needed to run the web server on Vercel.

### B. Changes Implemented

1. **Cleaned Root [requirements.txt](file:///c:/Users/Mohmmed%20Aarif/projects/production/GraphRag-Research-Assistant/requirements.txt)**:
   Removed `sentence-transformers` and `huggingface-hub` from the main `requirements.txt` file. It now contains only lightweight web server packages:
   ```text
   fastapi==0.115.0
   uvicorn[standard]==0.30.6
   httpx==0.27.2
   pydantic==2.9.2
   python-dotenv==1.0.1
   supabase==2.9.1
   neo4j==5.25.0
   numpy==1.26.4
   pymupdf==1.24.2
   ```

2. **Created [requirements-local.txt](file:///c:/Users/Mohmmed%20Aarif/projects/production/GraphRag-Research-Assistant/requirements-local.txt)**:
   Preserved the full suite of packages (including `sentence-transformers` and `huggingface-hub`) under a separate file name. Local developers or ingestion processes should use this file.

3. **Optimized [.vercelignore](file:///c:/Users/Mohmmed%20Aarif/projects/production/GraphRag-Research-Assistant/.vercelignore)**:
   Added patterns to prevent uploading unneeded local development and media assets to the Vercel builder, keeping the upload bundle small:
   ```text
   requirements-local.txt
   *.svg
   *.png
   test_*.py
   ```

---

## 4. Developer Instructions

### For Production Deployment (Vercel)
No actions are required. Simply trigger your Vercel deployment by pushing to your repository. Vercel will install the slimmed-down `requirements.txt` and successfully bundle the function under the 500 MB limit.
> [!IMPORTANT]
> Ensure the `HF_TOKEN` environment variable is configured in your Vercel Project Settings so the server can access the HuggingFace Inference API for generating search query embeddings.

### For Local Development and Data Ingestion
When developing locally or running ingestion pipelines (e.g., `ingestIntoSupabase.py`), install the full suite of dependencies:
```bash
pip install -r requirements-local.txt
```
This ensures local code can still generate embeddings locally via PyTorch without making HuggingFace HTTP requests.
