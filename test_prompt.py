import os
import asyncio
import httpx
from dotenv import load_dotenv
from typing import List

# Load env variables
load_dotenv(".env.local", override=True)
load_dotenv(".env", override=False)

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
if GROQ_API_KEY and "," in GROQ_API_KEY:
    GROQ_API_KEY = GROQ_API_KEY.split(",")[0].strip()
# Use openai/gpt-oss-20b by default (cascades to llama-3.3-70b-versatile on failure)
HEAVY_MODEL = "openai/gpt-oss-20b"

from app._server import get_or_parse_pdf_safe

def document_summary_system_instruction() -> str:
    return r"""You are Aether, a precise research assistant specialized in scientific literature analysis.
Analyze the provided document text and generate a comprehensive, highly structured, and readable summary.

═══ CRITICAL CONSTRAINTS ═══
- Your response MUST start directly with the header "# 1. Executive Summary". Do NOT output any other characters, introductory remarks, greetings, or raw text before this header.
- You MUST follow the exact 6-section structure in order.
- Do NOT output any mathematical formulas or derivations at the beginning of the response. All mathematical analysis and equations MUST be placed exclusively under section "# 6. Mathematical Formulas".

═══ SUMMARY STRUCTURE ═══
You must output exactly the following six sections, using these headers:

# 1. Executive Summary
Provide a high-level overview (2-3 sentences) of the document's core contribution, the problem it solves, and the main results.

# 2. Detailed Section-by-Section Breakdown
Analyze key methodologies, experiments, architectures, and theoretical foundations. Explain each section of the paper in depth using clean subheaders (e.g., `## Introduction`, `## Architecture`).

# 3. Key Findings & Metrics
Provide a detailed markdown table or bulleted list of baseline vs. proposed results, percentages, and evaluation metrics.

# 4. Embedded Reference Links
List code repositories, dataset pages, project websites, or reference URLs that were extracted from the PDF, using clickable markdown links (e.g. `[GitHub Repo](https://github.com/...)`). If none, state "No external links found in document."

# 5. Critique & Limitations
Discuss drawbacks, assumptions, constraints, or future directions mentioned by the authors.

# 6. Mathematical Formulas
Identify all key mathematical equations, variables, and expressions in the text, and write them in standard LaTeX syntax:
- Wrap inline variables/formulas in single dollar signs (e.g., $x_i$ or $\alpha_{t}$).
- Wrap block/displayed equations in double dollar signs, and display them on their own lines (e.g., $$c_t = \sum_{j=1}^{T_x} \alpha_{tj} h_{tj}$$).
- Do NOT output raw unicode sequences like "T X t=1" or "ct' = ...". Always translate them to proper LaTeX math notation.

═══ CONSTRAINT ═══
Base your response ONLY on the provided text. Do not invent facts. Write a thorough, comprehensive summary. Do not summarize briefly or omit key details.
"""

async def main():
    url = "https://arxiv.org/pdf/1409.0473.pdf"
    print("Parsing PDF...")
    doc_text, doc_links = await get_or_parse_pdf_safe(url, raise_on_error=True)
    
    sys_inst = document_summary_system_instruction()
    links_str = "\n".join(f"- {link}" for link in doc_links[:15]) if doc_links else "(No external links found in document.)"
    
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }
    
    # Simulating a multi-turn chat to lock document text in history and isolate the instruction
    payload = {
        "model": HEAVY_MODEL,
        "messages": [
            {"role": "system", "content": sys_inst},
            {"role": "user", "content": f"Here is the parsed document text for {url}:\n\n━━━ PARSED DOCUMENT TEXT ━━━\n{doc_text[:20000]}\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n━━━ EXTRACTED DOCUMENT LINKS ━━━\n{links_str}\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"},
            {"role": "assistant", "content": "I have received the parsed document text and extracted links. I will analyze them and generate the structured summary according to your guidelines. Please issue the command to begin."},
            {"role": "user", "content": "Please generate the comprehensive summary now. You MUST start your response directly with '# 1. Executive Summary' and follow the 6-section structure exactly."}
        ],
        "temperature": 0.0,
        "max_tokens": 2500,
    }
    
    async with httpx.AsyncClient(timeout=60.0) as client:
        print(f"Calling Groq with {payload['model']}...")
        r = await client.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers=headers,
            json=payload,
        )
        print(f"Status Code: {r.status_code}")
        
        # Cascading fallback: if gpt-oss-20b fails, retry with llama-3.3-70b-versatile
        if r.status_code != 200 and payload["model"] == "openai/gpt-oss-20b":
            print("gpt-oss-20b failed. Cascading fallback to llama-3.3-70b-versatile...")
            payload["model"] = "llama-3.3-70b-versatile"
            r = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers=headers,
                json=payload,
            )
            print(f"Status Code (Fallback): {r.status_code}")

        if r.status_code == 200:
            result = r.json()["choices"][0]["message"]["content"]
            print("Writing response to test_response_multiturn.txt...")
            with open("test_response_multiturn.txt", "w", encoding="utf-8") as f:
                f.write(result)
            print("Done writing file.")
        else:
            print(r.text)

if __name__ == "__main__":
    asyncio.run(main())
