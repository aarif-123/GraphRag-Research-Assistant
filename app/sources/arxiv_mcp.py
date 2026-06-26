import os
import logging
from typing import List, Dict, Any
from mcp import ClientSession
from mcp.client.sse import sse_client

log = logging.getLogger(__name__)

async def query_arxiv_mcp(query: str, limit: int = 5) -> List[Dict[str, Any]]:
    """
    Query the remote ArXiv MCP Server over SSE, call its 'search_arxiv' tool,
    and map the results to the schema expected by the Aether research pipeline.
    """
    mcp_url = os.getenv("ARXIV_MCP_URL")
    if not mcp_url:
        log.warning("ARXIV_MCP_URL environment variable is not set. Skipping MCP query.")
        return []

    # Ensure we use the /sse path if it's not already appended
    if not mcp_url.endswith("/sse") and not mcp_url.endswith("/sse/"):
        mcp_url = mcp_url.rstrip("/") + "/sse"

    log.info(f"Connecting to remote ArXiv MCP server at: {mcp_url}")
    
    try:
        # Use MCP SSE Client to establish the transport session
        async with sse_client(mcp_url) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                
                log.info(f"Calling 'search_arxiv' tool via MCP for query: '{query}'")
                response = await session.call_tool(
                    "search_arxiv",
                    arguments={"query": query, "limit": limit}
                )
                
                # Check response structure
                if not response or not hasattr(response, "content"):
                    log.warning("MCP response is empty or invalid.")
                    return []
                
                # The MCP call returns TextContent or similar objects inside content list.
                # In FastMCP, returning a list/dict gets serialized as JSON string inside TextContent.text
                papers_raw = []
                for content in response.content:
                    if hasattr(content, "text") and content.text:
                        import json
                        try:
                            # FastMCP serializes returned complex objects to JSON strings or representations
                            data = json.loads(content.text)
                            if isinstance(data, list):
                                papers_raw.extend(data)
                            elif isinstance(data, dict):
                                papers_raw.append(data)
                        except json.JSONDecodeError:
                            # Fallback if it returned raw string representation or other text
                            log.warning(f"Could not parse MCP text content as JSON: {content.text[:100]}")
                
                # Map raw MCP results to Aether pipeline schema
                mapped_papers = []
                for paper in papers_raw:
                    arxiv_id = paper.get("arxiv_id", "")
                    published = paper.get("published", "")
                    year = published.split("-")[0] if published else "Unknown"
                    
                    mapped_papers.append({
                        "title": paper.get("title", "Unknown Title"),
                        "abstract": paper.get("summary", "No Abstract Available"),
                        "authors": paper.get("authors", []),
                        "year": year,
                        "url": f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else paper.get("pdf_url", ""),
                        "pdf_url": paper.get("pdf_url", ""),
                        "id": arxiv_id,
                        # Enhanced fields defaulted
                        "doi": "",
                        "doi_url": "",
                        "journal_ref": "",
                        "comment": "",
                        "categories": [paper.get("primary_category", "")] if paper.get("primary_category") else [],
                        "citation_count": None,
                        "tldr": "",
                        "code_repos": [],
                        "datasets": [],
                        "has_code": False,
                    })
                
                log.info(f"Successfully retrieved and mapped {len(mapped_papers)} papers from remote ArXiv MCP server.")
                return mapped_papers

    except Exception as e:
        log.error(f"Error querying ArXiv MCP server at {mcp_url}: {e}", exc_info=True)
        return []
