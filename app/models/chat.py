from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    role: str
    content: str


class ConversationRequest(BaseModel):
    messages: List[ChatMessage] = Field(..., min_length=1)
    top_k: int = Field(8, ge=1, le=20)
    min_similarity: float = Field(0.28, ge=0.0, le=1.0)
    use_heavy: bool = False
    verify: bool = True
    filters: Optional[Dict[str, Any]] = None
    last_paper_context: Optional[str] = None
    temperature: float = Field(0.0, ge=0.0, le=2.0)
    mode: Optional[str] = "research"


class ChatCompletionRequest(BaseModel):
    model: Optional[str] = None
    messages: List[Dict[str, str]] = Field(..., min_length=1)
    temperature: float = Field(0.1, ge=0.0, le=2.0)
    max_tokens: int = Field(800, ge=1, le=4096)
    stream: bool = False
