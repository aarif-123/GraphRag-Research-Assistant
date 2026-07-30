from typing import Any, Dict, List, Optional
from fastapi import HTTPException
from pydantic import BaseModel, Field, field_validator
from dataclasses import dataclass, field


class ResearchRequest(BaseModel):
    query: Optional[str] = Field(None, max_length=2000)
    text: Optional[str] = Field(None, max_length=2000)
    top_k: int = Field(8, ge=1, le=20)
    min_similarity: float = Field(0.28, ge=0.0, le=1.0)
    use_heavy: bool = False
    verify: bool = True
    filters: Optional[Dict[str, Any]] = None
    temperature: float = Field(0.0, ge=0.0, le=2.0)
    mode: Optional[str] = "research"

    @field_validator("query", "text", mode="before")
    @classmethod
    def strip_ws(cls, v):
        return v.strip() if isinstance(v, str) else v

    def resolved_query(self) -> str:
        q = self.query or self.text
        if not q:
            raise HTTPException(400, "Provide 'query' or 'text'.")
        return q


class BulkRequest(BaseModel):
    queries: List[str] = Field(..., min_length=1, max_length=10)
    top_k: int = Field(8, ge=1, le=20)


class CompareRequest(BaseModel):
    paper_a: str = Field(..., description="Title or ID of first paper")
    paper_b: str = Field(..., description="Title or ID of second paper")
    aspects: Optional[List[str]] = Field(None, description="Specific aspects to compare")
    temperature: float = Field(0.0, ge=0.0, le=2.0)


class TimelineRequest(BaseModel):
    topic: str = Field(..., max_length=500)
    start_year: Optional[int] = None
    end_year: Optional[int] = None
    top_k: int = Field(10, ge=1, le=30)
    temperature: float = Field(0.0, ge=0.0, le=2.0)


class SurveyRequest(BaseModel):
    topic: str = Field(..., max_length=500)
    top_k: int = Field(15, ge=5, le=30)
    use_heavy: bool = True
    temperature: float = Field(0.0, ge=0.0, le=2.0)


class CitationPathRequest(BaseModel):
    from_paper: str
    to_paper: str


@dataclass
class QueryPlan:
    standalone_query: str
    route: str
    graph_anchors: List[str] = field(default_factory=list)
    vector_keywords: List[str] = field(default_factory=list)
    required_metrics: List[str] = field(default_factory=list)
    reasoning_path: str = ""
    ambiguous: bool = False
    cache_key_str: str = ""
    raw: Dict = field(default_factory=dict)
    # --- Enhanced fields for tiered retrieval and depth-aware synthesis ---
    # List of named research entities detected in the query, e.g.:
    #   [{"name": "LoRA", "type": "method", "primary_source_required": True}]
    named_entities: List[Dict] = field(default_factory=list)
    # Ordered, exact search strings for tiered entity-first retrieval:
    #   [tier1_exact_title, tier2_concept, tier3_discovery, ...]
    search_tiers: List[str] = field(default_factory=list)
    # "high" when query signals deep technical intent (how + mathematical/limitations/derive)
    depth: str = "standard"
    # Checklist of requirements the answer must satisfy (used for self-check)
    requirements: List[str] = field(default_factory=list)


