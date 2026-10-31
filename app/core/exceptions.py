"""
Custom Exception Classes for Aether Research Assistant.
"""


class EmbeddingError(Exception):
    """Raised when embedding generation fails."""

    pass


class GraphRetrievalError(Exception):
    """Raised when querying/retrieving from Neo4j fails."""

    pass


class VectorSearchError(Exception):
    """Raised when vector search operations fail."""

    pass


class LLMError(Exception):
    """Raised when calls to the Groq/LLM APIs fail."""

    pass


def user_facing_llm_error(error: LLMError) -> str:
    """Return actionable, non-technical guidance for an LLM failure."""
    message = str(error).lower()
    if "output limit" in message or "incomplete" in message or "truncated" in message:
        return (
            "I couldn't fit a complete answer in the response. Try asking for fewer papers "
            "or request only titles, years, and links."
        )
    return (
        "I couldn't complete this research request right now. Please try again; if it "
        "happens again, narrow the question to a smaller set of papers or details."
    )


class PlanError(Exception):
    """Raised when strategic query planning fails."""

    pass
