"""
Aether External Source Connectors
Provides async access to Semantic Scholar, Papers with Code, and Google Scholar.
"""
from .semantic_scholar import (
    search_papers_s2,
    get_paper_by_arxiv_id_s2,
    enrich_arxiv_papers_with_s2,
)
from .papers_with_code import (
    get_paper_repos_pwc,
    get_paper_datasets_pwc,
    enrich_arxiv_papers_with_pwc,
)
from .arxiv_mcp import (
    query_arxiv_mcp,
)
from .wikipedia import (
    search_wikipedia_summary,
    enrich_datasets_with_wikipedia,
)
from .kaggle import (
    search_kaggle_dataset,
    enrich_datasets_with_kaggle,
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
]
