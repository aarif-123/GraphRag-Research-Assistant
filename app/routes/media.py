"""
routes/media.py — PDF upload/retrieval and audio transcription endpoints.
"""

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile

from app.clients.groq import rotate_groq_key
from app.clients.pool import pool
from app.config import (
    _MAX_PDF_TEXT_CHARS,
    _MB,
    GROQ_API_KEY,
    GROQ_API_KEYS,
    MAX_AUDIO_BYTES,
    MAX_PDF_BYTES,
    log,
)
from app.utils.auth import set_user_context

try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None  # type: ignore

router = APIRouter(prefix="/api")


def _is_whisper_hallucination(text: str) -> bool:
    if not text or not text.strip():
        return True
    cleaned = "".join(c for c in text if c.isalnum()).lower().strip()
    if len(cleaned) <= 1:
        return True
    hallucinations = {
        "",
        "you",
        "thankyou",
        "thankyouforwatching",
        "pleaselikeandsubscribe",
        "subscribe",
        "watching",
        "bye",
        "thankyoubye",
        "thankyousomuch",
        "amaraorg",
        "subtitlesby",
        "captionedby",
        "translatedby",
        "mb",
        "so",
        "oh",
        "uh",
        "um",
    }
    if cleaned in hallucinations:
        return True
    lower = text.lower().strip()
    if any(
        pat in lower
        for pat in [
            "subtitles by",
            "captioned by",
            "amara.org",
            "thanks for watching",
            "please subscribe",
            "like and subscribe",
            "thank you for watching",
            "copyright",
            "all rights reserved",
        ]
    ):
        return True
    return False


@router.post("/upload/pdf")
async def upload_pdf(request: Request, file: UploadFile = File(...)):
    await set_user_context(request)

    filename = file.filename or "document.pdf"
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")

    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > MAX_PDF_BYTES:
        raise HTTPException(
            status_code=413,
            detail=(
                f"File too large. Maximum allowed size is {MAX_PDF_BYTES // _MB} MB. "
                f"Received approximately {int(content_length) // _MB} MB."
            ),
        )

    try:
        content = await file.read()
        if not content:
            raise HTTPException(status_code=400, detail="Empty PDF file.")

        if len(content) > MAX_PDF_BYTES:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"File too large. Maximum allowed size is {MAX_PDF_BYTES // _MB} MB. "
                    f"Received {len(content) // _MB} MB."
                ),
            )

        def _extract():
            if fitz is None:
                raise HTTPException(
                    status_code=500,
                    detail="PDF parsing is unavailable: PyMuPDF is not installed in this environment.",
                )
            doc = fitz.open(stream=content, filetype="pdf")
            text_content = [page.get_text() for page in doc]
            return "\n".join(text_content).strip()

        extracted_text = await asyncio.to_thread(_extract)
        if not extracted_text:
            raise HTTPException(status_code=400, detail="The PDF contains no readable text.")

        was_truncated = len(extracted_text) > _MAX_PDF_TEXT_CHARS
        if was_truncated:
            extracted_text = extracted_text[:_MAX_PDF_TEXT_CHARS]
            log.warning(
                f"PDF '{filename}' text truncated to {_MAX_PDF_TEXT_CHARS // _MB} MB "
                "to stay within MongoDB document limit."
            )

        pdf_id = f"pdf-{uuid.uuid4()}"
        await asyncio.to_thread(
            pool.db.uploaded_pdfs.insert_one,
            {
                "_id": pdf_id,
                "name": filename,
                "text": extracted_text,
                "truncated": was_truncated,
                "created_at": datetime.now(timezone.utc),
            },
        )
        pdf_url = f"/api/pdf/{pdf_id}.pdf"
        return {
            "file_id": pdf_id,
            "url": pdf_url,
            "name": filename,
            "text_length": len(extracted_text),
            "truncated": was_truncated,
        }
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Error parsing PDF file upload: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to process PDF: {str(e)}")


@router.get("/pdf/{pdf_id}")
async def get_pdf_text(pdf_id: str, request: Request):
    await set_user_context(request)
    pdf_id = pdf_id.replace(".pdf", "")
    doc = await asyncio.to_thread(pool.db.uploaded_pdfs.find_one, {"_id": pdf_id})
    if not doc:
        raise HTTPException(status_code=404, detail="PDF not found.")
    return {"text": doc["text"], "name": doc.get("name", "Document")}


@router.post("/audio/transcribe")
@router.post("/voice/transcribe")
async def transcribe_audio(
    request: Request,
    file: UploadFile = File(...),
    language: Optional[str] = Form("en"),
    prompt: Optional[str] = Form(None),
):
    await set_user_context(request)

    if not GROQ_API_KEY and not GROQ_API_KEYS:
        raise HTTPException(status_code=503, detail="Groq API key not configured on the server.")

    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > MAX_AUDIO_BYTES:
        raise HTTPException(
            status_code=413,
            detail=(
                f"Audio file too large. Maximum allowed size is {MAX_AUDIO_BYTES // _MB} MB "
                f"(Groq Whisper limit). Received approximately {int(content_length) // _MB} MB."
            ),
        )

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty audio file.")

    if len(content) > MAX_AUDIO_BYTES:
        raise HTTPException(
            status_code=413,
            detail=(
                f"Audio file too large. Maximum allowed size is {MAX_AUDIO_BYTES // _MB} MB "
                f"(Groq Whisper limit). Received {len(content) // _MB} MB."
            ),
        )

    max_attempts = len(GROQ_API_KEYS) if GROQ_API_KEYS else 1
    last_err = None

    default_prompt = (
        "User question, research query, or conversation about artificial intelligence, "
        "graph RAG, machine learning, Python, computer science, literature, or general topics."
    )
    effective_prompt = prompt.strip() if (prompt and prompt.strip()) else default_prompt

    for attempt in range(max_attempts):
        current_key = GROQ_API_KEY or ""
        if GROQ_API_KEYS:
            from app.clients.groq import groq_key_index

            key_idx = (groq_key_index + attempt) % len(GROQ_API_KEYS)
            current_key = GROQ_API_KEYS[key_idx]

        headers = {"Authorization": f"Bearer {current_key}"}

        content_type = file.content_type or "audio/webm"
        if ";" in content_type:
            content_type = content_type.split(";")[0].strip()

        files = {"file": (file.filename or "speech.webm", content, content_type)}
        data: dict = {
            "model": "whisper-large-v3",
            "temperature": "0",
            "prompt": effective_prompt,
        }
        if language and language.strip() and language.strip().lower() != "auto":
            data["language"] = language.strip().lower()

        try:
            r = await pool.groq_http.post(
                "https://api.groq.com/openai/v1/audio/transcriptions",
                headers=headers,
                files=files,
                data=data,
                timeout=60.0,
            )
            if r.status_code == 200:
                resp_json = r.json()
                text = resp_json.get("text", "")
                if _is_whisper_hallucination(text):
                    text = ""
                return {"text": text}
            elif r.status_code == 429:
                rotate_groq_key()
                last_err = f"Groq HTTP 429: {r.text[:200]}"
                continue
            else:
                last_err = f"Groq HTTP {r.status_code}: {r.text[:200]}"
                rotate_groq_key()
                continue
        except Exception as e:
            last_err = str(e)
            rotate_groq_key()
            continue

    raise HTTPException(status_code=500, detail=f"Failed to transcribe audio. Error: {last_err}")
