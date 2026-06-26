# 🔬 ArXiv MCP Server (SSE Version)

This is a standalone Model Context Protocol (MCP) server that exposes ArXiv search tools over **Server-Sent Events (SSE)**. It is specifically pre-configured to build as a Docker container and deploy directly on **Render** (or Google Cloud Run, Railway, etc.).

---

## 🛠️ Deploying to Render

1. **Commit and Push**: Ensure the `arxiv-mcp-server/` directory is committed and pushed to your GitHub repository.
2. **Create Render Web Service**:
   - Log in to [dashboard.render.com](https://dashboard.render.com).
   - Click **New** -> **Web Service**.
   - Connect your GitHub repository.
   - Choose **Docker** as the Runtime (Render will automatically detect the `arxiv-mcp-server/Dockerfile` if you set the **Root Directory** to `arxiv-mcp-server` in the settings).
3. **Configure Environment Variables**:
   - Set `PORT` to `8000` (Render will automatically route traffic to this port).
4. **Deploy**: Trigger the deployment. Once live, Render will give you a public URL (e.g., `https://arxiv-mcp-xyz.onrender.com`).

---

## 🔌 Integrating with Aether

Once your Render service is running, you can connect it directly to the Aether backend.

### 1. Configure the Environment Variable
Add the Render service URL to your `.env` file in the main project directory:
```env
ARXIV_MCP_URL=https://arxiv-mcp-xyz.onrender.com
```

### 2. Client Code Example
A client module in Aether can fetch data from this endpoint:
```python
import httpx

async def get_arxiv_mcp_papers(query: str, limit: int = 5):
    url = f"{os.getenv('ARXIV_MCP_URL')}/tools/search_arxiv/call"
    payload = {
        "arguments": {
            "query": query,
            "limit": limit
        }
    }
    async with httpx.AsyncClient() as client:
        response = await client.post(url, json=payload, timeout=15.0)
        if response.status_code == 200:
            return response.json().get("content", [])
        return []
```
