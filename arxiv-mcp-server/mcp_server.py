import os
import uvicorn
import arxiv
from fastapi import FastAPI
from fastmcp import FastMCP

# Initialize FastMCP Server
mcp = FastMCP("ArXiv MCP Server")

@mcp.tool()
async def search_arxiv(query: str, limit: int = 5) -> list:
    """
    Search ArXiv for scientific papers matching a query.
    Returns paper details (title, authors, summary, arxiv_id, pdf_url, published, primary_category).
    """
    client = arxiv.Client()
    search = arxiv.Search(
        query=query,
        max_results=limit,
        sort_by=arxiv.SortCriterion.Relevance
    )
    
    results = []
    try:
        for paper in client.results(search):
            results.append({
                "title": paper.title,
                "authors": [a.name for a in paper.authors],
                "summary": paper.summary,
                "arxiv_id": paper.entry_id.split("/abs/")[-1],
                "pdf_url": paper.pdf_url,
                "published": paper.published.isoformat(),
                "primary_category": paper.primary_category
            })
    except Exception as e:
        return [{"error": str(e)}]
        
    return results

@mcp.tool()
async def get_paper_details(arxiv_id: str) -> dict:
    """
    Retrieve full details for a specific paper by its ArXiv ID.
    """
    client = arxiv.Client()
    search = arxiv.Search(id_list=[arxiv_id])
    try:
        results = list(client.results(search))
        if not results:
            return {"error": "Paper not found"}
        paper = results[0]
        return {
            "title": paper.title,
            "authors": [a.name for a in paper.authors],
            "summary": paper.summary,
            "arxiv_id": paper.entry_id.split("/abs/")[-1],
            "pdf_url": paper.pdf_url,
            "published": paper.published.isoformat(),
            "primary_category": paper.primary_category,
            "comment": paper.comment,
            "journal_ref": paper.journal_ref,
            "categories": paper.categories
        }
    except Exception as e:
        return {"error": str(e)}

# Generate standard HTTP/SSE application using FastMCP
mcp_app = mcp.http_app(path="/")

# Initialize and mount to FastAPI to handle lifespans and custom ports cleanly
app = FastAPI(
    title="ArXiv MCP Server",
    lifespan=mcp_app.lifespan
)
app.mount("/", mcp_app)

if __name__ == "__main__":
    # Render binds automatically to the PORT env variable
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
