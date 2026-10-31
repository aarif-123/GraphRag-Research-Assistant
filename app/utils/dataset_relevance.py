"""Shared lexical relevance checks for external dataset search results."""

import re
from math import ceil
from typing import Any, Dict, Iterable

_STOP_WORDS = {
    "about",
    "after",
    "also",
    "and",
    "are",
    "dataset",
    "datasets",
    "does",
    "find",
    "for",
    "from",
    "how",
    "into",
    "me",
    "papers",
    "please",
    "recent",
    "show",
    "the",
    "their",
    "this",
    "using",
    "what",
    "when",
    "which",
    "with",
    "work",
    "works",
}


def _tokens(value: str) -> set[str]:
    tokens = set()
    for token in re.findall(r"[a-z0-9]+", value.lower()):
        if token.endswith("ies") and len(token) > 5:
            token = token[:-3] + "y"
        elif token.endswith("s") and len(token) > 4:
            token = token[:-1]
        if len(token) > 2 and token not in _STOP_WORDS:
            tokens.add(token)
    return tokens


def _flatten_values(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for nested in value.values():
            yield from _flatten_values(nested)
    elif isinstance(value, (list, tuple, set)):
        for nested in value:
            yield from _flatten_values(nested)


def dataset_relevance_score(query: str, dataset: Dict[str, Any]) -> float:
    """Return query-token coverage, or zero when there is insufficient evidence."""
    query_tokens = _tokens(query)
    if not query_tokens:
        return 0.0

    fields = ("name", "full_name", "title", "ref", "description", "subtitle", "tags")
    result_tokens = set()
    for field in fields:
        result_tokens.update(_tokens(" ".join(_flatten_values(dataset.get(field, "")))))

    matched = query_tokens & result_tokens
    minimum_matches = min(len(query_tokens), max(2, ceil(len(query_tokens) * 0.34)))
    if len(matched) < minimum_matches:
        return 0.0

    return len(matched) / len(query_tokens)
