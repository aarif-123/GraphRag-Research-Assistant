"""
Aether External Source Connectors
Provides async access to Semantic Scholar, Papers with Code, and Google Scholar.
"""

from .arxiv_mcp import (
    query_arxiv_mcp,
)
from .kaggle import (
    enrich_datasets_with_kaggle,
    search_kaggle_dataset,
)
from .openalex import (
    enrich_arxiv_papers_with_openalex,
    search_openalex,
)
from .papers_with_code import (
    enrich_arxiv_papers_with_pwc,
    get_paper_datasets_pwc,
    get_paper_repos_pwc,
)
from .semantic_scholar import (
    enrich_arxiv_papers_with_s2,
    get_paper_by_arxiv_id_s2,
    search_papers_s2,
)
from .wikipedia import (
    enrich_datasets_with_wikipedia,
    search_wikipedia_summary,
)

__all__ = [
    "search_papers_s2",
    "get_paper_by_arxiv_id_s2",
    "enrich_arxiv_papers_with_s2",
    "get_paper_repos_pwc",
    "get_paper_datasets_pwc",
    "enrich_arxiv_papers_with_pwc",
    "query_arxiv_mcp",
    "search_wikipedia_summary",
    "enrich_datasets_with_wikipedia",
    "search_kaggle_dataset",
    "enrich_datasets_with_kaggle",
    "search_openalex",
    "enrich_arxiv_papers_with_openalex",
]
