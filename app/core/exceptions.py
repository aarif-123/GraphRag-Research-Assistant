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


class PlanError(Exception):
    """Raised when strategic query planning fails."""
    pass
