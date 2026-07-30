"""
Strategic Planning Brain — plan_query v2 with entity detection,
tiered search, depth classification, and primary-source reader.
"""

import json
import re
from typing import Dict, List

from app.config import PLAN_MODEL, log
from app.core.exceptions import LLMError
from app.models.research import QueryPlan
from app.clients.pool import cache_key, get_cache, set_cache
from app.clients.groq import groq_chat

# ──────────────────────────────────────────────────────────────────────────────
# SUPER-MASTER STRATEGIC PLANNING BRAIN PROMPT v2
# ──────────────────────────────────────────────────────────────────────────────

SUPER_MASTER_PROMPT = """
You are the Strategic Planning Brain for Aether, an evidence-only GraphRAG Research Assistant.
Decompose the user query into a precise execution plan.

━━━ INPUT ━━━
USER QUERY: {query}
CONVERSATION HISTORY (last 3 turns):
{context}

━━━ STEPS ━━━

STEP 1 — RESOLVE PRONOUNS
If the query contains "it", "they", "this paper", "the authors", or similar ambiguity:
  Identify the referent from CONVERSATION HISTORY and rewrite the query to be self-contained.
  If unresolvable, set "ambiguous": true.

STEP 2 — CLASSIFY ROUTE (pick exactly one):
  "entity_lookup"  → factual metadata query: author, year, domain, venue, affiliation.
                     Trigger: who, when, which year, published by, domain of, where published.
  "structured"     → list/filter: list papers, find papers on X, papers by author Y.
  "title_lookup"   → user names a specific paper and wants its record only (no analysis).
  "compare"        → side-by-side of 2+ papers, methods, or approaches.
  "timeline"       → chronological evolution of a topic across years.
  "survey"         → BROAD field-level synthesis. Use this whenever the query asks about the overall
                     state, landscape, or advances of a research field — NOT a specific named paper.
                     STRONG TRIGGERS (use survey if ANY appear): "latest advances", "recent advances",
                     "state of the art", "overview of", "survey of", "progress in", "landscape of",
                     "how has X evolved", "what are the advances in", "advances in X",
                     "what's new in", "current trends in", "developments in", "breakthroughs in".
                     vector_keywords MUST cover 4-5 distinct sub-areas of the field.
  "conceptual"     → explanation, tutoring, or educational overview of a general scientific concept,
                     algorithm, methodology, model family, or research area (e.g. "explain graph neural networks",
                     "what is message passing?", "how do CNNs work?", "explain contrastive learning").
                     Trigger: User asks "explain X", "what is X", "how does X work", "introduction to X",
                     "conceptual overview of X", or wants to understand the fundamentals/math/intuition of a field.
  "rag"            → explanation or analysis of a SPECIFIC named concept, paper, or mechanism
                     (e.g., "how does FlashAttention work?", "explain RLHF"). Not broad field surveys.
  "chitchat"       → greeting or non-research question.
  "context_only"   → conversational follow-up, clarification, formatting/summarization request, or query
                     that can be answered entirely using the CONVERSATION HISTORY and the information
                     already presented.
                     Trigger: "explain the second point", "summarize what you said", "tell me more about that first paper",
                     "rewrite your previous response as a table", "thanks, that makes sense".

STEP 2.5 — DETECT NAMED RESEARCH ENTITIES & GENERATE TIERED SEARCH QUERIES
  Scan the query for explicitly named papers, methods, algorithms, datasets, or architectures.
  These are proper nouns like: LoRA, QLoRA, BERT, GPT-4, FlashAttention, ResNet, RLHF, NF4,
  Stable Diffusion, Mamba, Mixtral, DeepSeek, etc.

  For each named entity found, add an entry to "named_entities":
    {{"name": "<entity>", "type": "method|paper|dataset|concept", "primary_source_required": true/false}}
  Set "primary_source_required": true for any method/paper that the user is asking to explain,
  compare, or analyze in depth. Set false for tangentially mentioned entities.

  Then generate "search_tiers" — an ordered list of exact search strings for the retrieval layer.
  CRITICAL: For named entities, search_tiers MUST include the exact paper title, not just keywords.
  Order:
    Tier 1 (exact paper/entity lookups first) — e.g. "LoRA Low-Rank Adaptation of Large Language Models"
    Tier 2 (required mechanism/concept terms) — e.g. "QLoRA NF4 NormalFloat double quantization"
    Tier 3 (supporting/discovery searches)    — e.g. "4-bit quantization parameter efficient fine tuning"
  If no named entities found, return search_tiers: [].
  For chitchat/context_only routes: return search_tiers: [].
  Maximum 7 entries in search_tiers.

STEP 3 — EXTRACT GRAPH ANCHORS
  1–3 minimal paper title substrings or author names for Neo4j lookup.
  Use shortest identifying substring: "DeepSketch" not "DeepSketch paper on sketch recognition".
  For survey/conceptual routes: return [] unless the query explicitly names specific papers.

STEP 4 — EXTRACT VECTOR KEYWORDS
  3–5 dense technical terms for semantic vector search.
  Exclude: "paper", "author", "year", "list", "find", "published", "research".
  For survey route: keywords MUST span multiple distinct sub-areas of the field, not just one angle.
  Example for "latest advances in transformer architectures":
  ["mixture of experts", "state space models", "efficient attention", "multimodal transformers", "long context"]

STEP 5 — IDENTIFY REQUIRED METRICS
  Specific data the answer MUST include: accuracy, dataset, year, author names, citation count, etc.
  Return [] if none.

STEP 5.5 — CLASSIFY DEPTH
  Set "depth": "high" if the query contains at minimum TWO of these signals:
    - "how" (mechanism question)
    - "mathematical" OR "math" OR "equation" OR "formula" OR "derive" OR "proof"
    - "limitations" OR "limitation" OR "bottleneck" OR "problem with"
    - "mechanism" OR "why does" OR "explain in detail"
  Otherwise set "depth": "standard".

  When depth is "high", also generate "requirements": a checklist of 3–6 specific sub-questions
  the answer MUST address. Example for "explain LoRA limitations and how QLoRA fixes them mathematically":
    ["Explain the concrete memory limitation of LoRA",
     "Explain how QLoRA resolves this limitation",
     "Explain what NF4 (NormalFloat) is",
     "Explain the mathematical mechanism of NF4",
     "Connect NF4 to memory reduction"]
  For depth "standard": return "requirements": [].

STEP 6 — REASONING PATH
  One sentence: how you will assemble the answer from graph + vector evidence.

STEP 7 — CACHE KEY
  lowercase(standalone_query), strip punctuation.

━━━ OUTPUT FORMAT ━━━
Respond ONLY with a valid JSON object. No markdown. No explanation outside JSON.

{{
  "standalone_query": "<self-contained rewrite>",
  "ambiguous": false,
  "route": "<one of the 9 routes>",
  "graph_anchors": ["<minimal anchor>"],
  "vector_keywords": ["<term>"],
  "required_metrics": ["<metric>"],
  "named_entities": [{{"name": "<entity>", "type": "<type>", "primary_source_required": true}}],
  "search_tiers": ["<exact tier1 search>", "<tier2 search>", "<tier3 search>"],
  "depth": "standard",
  "requirements": [],
  "reasoning_path": "<one sentence>",
  "cache_key": "<lowercase stripped>"
}}

━━━ HARD RULES ━━━
- entity_lookup → graph_anchors MUST have exactly 1 entry; vector_keywords SHOULD be [].
- chitchat → ALL retrieval fields MUST be []; named_entities=[]; search_tiers=[]; requirements=[].
- context_only → ALL retrieval fields MUST be []; named_entities=[]; search_tiers=[]; requirements=[].
- ambiguous=true → standalone_query ends with " [UNRESOLVED]", route = "rag".
- compare → graph_anchors MUST have exactly 2 entries (one per paper).
- survey → vector_keywords MUST have 4–5 entries spanning distinct sub-areas.
- search_tiers entries MUST be short, precise search strings — NOT full sentences or questions.
- NEVER add extra keys. NEVER return prose.

━━━ EXAMPLES ━━━
Input: "who is the author of DeepSketch?"
{{"standalone_query":"Who are the authors of DeepSketch?","ambiguous":false,"route":"entity_lookup","graph_anchors":["DeepSketch"],"vector_keywords":[],"required_metrics":["author names"],"named_entities":[{{"name":"DeepSketch","type":"paper","primary_source_required":false}}],"search_tiers":[],"depth":"standard","requirements":[],"reasoning_path":"Retrieve DeepSketch node from graph and return its WRITTEN_BY relationships directly.","cache_key":"who are the authors of deepsketch"}}

Input: "compare its accuracy with ResNet-50" (prev turn: DeepSketch)
{{"standalone_query":"Compare the accuracy of DeepSketch with ResNet-50.","ambiguous":false,"route":"compare","graph_anchors":["DeepSketch","ResNet-50"],"vector_keywords":["accuracy","top-1","benchmark","classification"],"required_metrics":["accuracy percentage","dataset","parameter count"],"named_entities":[{{"name":"DeepSketch","type":"paper","primary_source_required":true}},{{"name":"ResNet-50","type":"method","primary_source_required":true}}],"search_tiers":["DeepSketch sketch recognition","ResNet-50 deep residual learning image recognition","DeepSketch vs ResNet accuracy benchmark"],"depth":"standard","requirements":[],"reasoning_path":"Retrieve both papers, then vector-search accuracy comparison chunks.","cache_key":"compare the accuracy of deepsketch with resnet50"}}

Input: "hey what's up"
{{"standalone_query":"hey what's up","ambiguous":false,"route":"chitchat","graph_anchors":[],"vector_keywords":[],"required_metrics":[],"named_entities":[],"search_tiers":[],"depth":"standard","requirements":[],"reasoning_path":"No retrieval needed.","cache_key":"hey whats up"}}

Input: "What are the latest advances in transformer architectures?"
{{"standalone_query":"What are the latest advances in transformer architectures?","ambiguous":false,"route":"survey","graph_anchors":[],"vector_keywords":["mixture of experts","state space models","efficient attention","multimodal transformers","long context scaling"],"required_metrics":[],"named_entities":[],"search_tiers":[],"depth":"standard","requirements":[],"reasoning_path":"Survey broad transformer landscape across efficient attention, MoE, SSMs, multimodal, and long-context sub-areas from retrieved and general knowledge.","cache_key":"latest advances transformer architectures"}}

Input: "explain graph neural networks and their applications"
{{"standalone_query":"Explain graph neural networks (GNNs) and their applications.","ambiguous":false,"route":"conceptual","graph_anchors":[],"vector_keywords":["graph neural networks","message passing","graph convolution","recommender systems","drug discovery"],"required_metrics":[],"named_entities":[],"search_tiers":[],"depth":"standard","requirements":[],"reasoning_path":"Provide a conceptual explanation of Graph Neural Networks, including the mathematical intuition, why traditional architectures fail, architectural evolution, and real-world applications.","cache_key":"explain graph neural networks and applications"}}

Input: "Explain the limitations of LoRA and how QLoRA resolves them, including the mathematical mechanism of NF4"
{{"standalone_query":"Explain the limitations of LoRA and how QLoRA resolves them, including the mathematical mechanism of NF4 quantization.","ambiguous":false,"route":"rag","graph_anchors":["LoRA","QLoRA"],"vector_keywords":["low-rank adaptation","quantization aware training","NormalFloat NF4","memory efficient finetuning","double quantization"],"required_metrics":["memory reduction","quantization bits","trainable parameters"],"named_entities":[{{"name":"LoRA","type":"method","primary_source_required":true}},{{"name":"QLoRA","type":"method","primary_source_required":true}},{{"name":"NF4","type":"concept","primary_source_required":true}}],"search_tiers":["LoRA Low-Rank Adaptation Large Language Models","QLoRA Efficient Finetuning Quantized LLMs","QLoRA NF4 NormalFloat double quantization paged optimizers","LoRA frozen model gradient memory","4-bit quantization parameter efficient fine tuning"],"depth":"high","requirements":["Explain the concrete memory limitation of LoRA during finetuning","Explain how QLoRA resolves this memory limitation","Define NF4 NormalFloat and its information-theoretic basis","Explain the mathematical quantization mechanism of NF4","Connect NF4 double quantization to memory reduction in practice"],"reasoning_path":"Retrieve LoRA and QLoRA primary papers via exact title lookup, extract mechanism passages, then synthesize with mathematical depth.","cache_key":"explain limitations lora how qlora resolves them mathematical mechanism nf4"}}

"""


# ──────────────────────────────────────────────────────────────────────────────
# plan_query  (v2 — uses plan_v2 cache prefix to avoid stale v1 hits)
# ──────────────────────────────────────────────────────────────────────────────

async def plan_query(query: str, context: str = "") -> QueryPlan:
    # 'v2' prefix ensures old cached plans (missing new fields) are not reused
    ck = cache_key("plan_v2", query, context[:200])
    cached = get_cache("plan", ck)
    if cached:
        log.debug("Plan cache hit")
        return cached

    prompt = SUPER_MASTER_PROMPT.format(query=query, context=context or "None")
    try:
        raw_text = await groq_chat(
            [{"role": "user", "content": prompt}],
            PLAN_MODEL,
            temperature=0.0,
            max_tokens=800,
            json_mode=True,
        )
        data = json.loads(raw_text.strip())
    except (LLMError, json.JSONDecodeError, Exception) as e:
        log.warning(f"Plan failed ({e}), using fallback")
        data = {}

    # Validate and sanitise named_entities and search_tiers
    raw_entities = data.get("named_entities", [])
    named_entities: List[Dict] = [
        e for e in raw_entities
        if isinstance(e, dict) and e.get("name")
    ]

    raw_tiers = data.get("search_tiers", [])
    search_tiers: List[str] = [
        str(t).strip() for t in raw_tiers
        if isinstance(t, str) and t.strip()
    ][:7]

    depth = data.get("depth", "standard")
    if depth not in ("standard", "high"):
        depth = "standard"

    raw_reqs = data.get("requirements", [])
    requirements: List[str] = [str(r) for r in raw_reqs if isinstance(r, str) and r.strip()]

    plan = QueryPlan(
        standalone_query=data.get("standalone_query", query),
        route=data.get("route", "rag"),
        graph_anchors=data.get("graph_anchors", [])[:3],
        vector_keywords=data.get("vector_keywords", [])[:5],
        required_metrics=data.get("required_metrics", []),
        reasoning_path=data.get("reasoning_path", ""),
        ambiguous=data.get("ambiguous", False),
        cache_key_str=data.get("cache_key", re.sub(r"[^\w\s]", "", query.lower())),
        raw=data,
        named_entities=named_entities,
        search_tiers=search_tiers,
        depth=depth,
        requirements=requirements,
    )
    set_cache("plan", ck, plan)
    log.info(
        f"Plan: route={plan.route} anchors={plan.graph_anchors} "
        f"kw={plan.vector_keywords} tiers={plan.search_tiers} depth={plan.depth}"
    )
    return plan


# ──────────────────────────────────────────────────────────────────────────────
# PRIMARY SOURCE READER  (zero vector DB — keyword extraction over PDF)
# ──────────────────────────────────────────────────────────────────────────────

async def read_primary_source_passages(
    paper: Dict,
    search_terms: List[str],
    max_passages: int = 6,
    passage_chars: int = 800,
) -> List[str]:
    """Fetch a paper's full text and extract the most relevant passages
    using BM25-style keyword overlap against search_terms.

    Only called for papers where the planner set primary_source_required=True.
    Uses the existing get_or_parse_pdf_safe infrastructure (retries, SSL bypass).
    Returns up to max_passages text excerpts, each ≤ passage_chars characters.
    """
    # Import here to avoid circular imports
    from app.core.document import get_or_parse_pdf_safe

    pdf_url = paper.get("pdf_url") or paper.get("url") or ""
    if not pdf_url:
        return []

    # Convert arXiv abstract URL to PDF URL if needed
    if "/abs/" in pdf_url and not pdf_url.endswith(".pdf"):
        arxiv_id = pdf_url.split("/abs/")[-1].rstrip("/")
        pdf_url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"

    try:
        doc_text, _ = await get_or_parse_pdf_safe(pdf_url, raise_on_error=True)
    except Exception as e:
        log.debug(f"Primary source read failed for {pdf_url}: {e}")
        return []

    if not doc_text or len(doc_text) < 200:
        return []

    # Split into overlapping windows of ~passage_chars characters with 50% overlap
    step = passage_chars // 2
    windows = [doc_text[i: i + passage_chars] for i in range(0, len(doc_text), step)]

    # Score each window by keyword overlap (BM25-inspired: term frequency, unique hits)
    lowered_terms = [t.lower() for t in search_terms if t]

    def score_window(window: str) -> float:
        lw = window.lower()
        hits = sum(1 for term in lowered_terms if term in lw)
        # Weight longer term matches more (they're more specific)
        weighted = sum(len(term) for term in lowered_terms if term in lw)
        return hits + 0.01 * weighted

    scored = [(score_window(w), w) for w in windows if len(w.strip()) > 100]
    scored.sort(key=lambda x: x[0], reverse=True)

    # Return top passages that have at least 1 keyword hit, deduplicated
    seen_starts: set = set()
    results: List[str] = []
    for score, passage in scored:
        if score < 1.0:
            break
        start_sig = passage[:60]
        if start_sig not in seen_starts:
            seen_starts.add(start_sig)
            results.append(passage.strip())
        if len(results) >= max_passages:
            break

    return results


# ──────────────────────────────────────────────────────────────────────────────
# REQUIREMENT COVERAGE CHECK  (lightweight second LLM call for depth=high)
# ──────────────────────────────────────────────────────────────────────────────

async def check_requirements_covered(
    draft_answer: str,
    requirements: List[str],
) -> List[str]:
    """Given a draft answer and a list of required sub-topics, ask a small LLM
    to identify which requirements are NOT adequately covered.

    Returns a list of missing requirement strings. Empty list = all covered.
    Only called when plan.depth == "high" and plan.requirements is non-empty.
    """
    if not requirements or not draft_answer.strip():
        return []

    reqs_formatted = "\n".join(f"  {i + 1}. {r}" for i, r in enumerate(requirements))
    coverage_prompt = f"""You are a strict academic evaluator.

REQUIREMENTS (each must be explicitly addressed in the answer):
{reqs_formatted}

DRAFT ANSWER (first 3000 chars):
{draft_answer[:3000]}

For each requirement, answer ONLY with JSON in this exact format:
{{"coverage": [{{"req": "<requirement text>", "covered": true/false, "reason": "<1 sentence>"}}]}}

A requirement is "covered": true ONLY if the answer explicitly addresses it with concrete details
(not just a passing mention). Mathematical requirements need equations or formulas to be covered.
"""
    try:
        raw = await groq_chat(
            [{"role": "user", "content": coverage_prompt}],
            PLAN_MODEL,
            temperature=0.0,
            max_tokens=600,
            json_mode=True,
        )
        parsed = json.loads(raw.strip())
        coverage_list = parsed.get("coverage", [])
        missing = [
            item["req"]
            for item in coverage_list
            if isinstance(item, dict) and not item.get("covered", True)
        ]
        if missing:
            log.info(f"Requirement coverage: {len(missing)} missing — {missing}")
        return missing
    except Exception as e:
        log.debug(f"Requirement coverage check failed ({e}), skipping.")
        return []
