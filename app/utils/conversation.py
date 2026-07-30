"""
utils/conversation.py — Conversation history management, context compression,
and message compilation for the chat endpoint.
"""

from typing import Dict, List

from app.config import PLAN_MODEL, log
from app.models.chat import ChatMessage


def compress_rag_prompt(content: str) -> str:
    """Remove excessive whitespace and boilerplate from a prompt to reduce token usage."""
    import re

    # Collapse multiple blank lines
    content = re.sub(r"\n{3,}", "\n\n", content)
    # Strip trailing spaces
    content = "\n".join(line.rstrip() for line in content.splitlines())
    return content.strip()


def truncate_messages(messages: List[Dict], max_total_chars: int = 120_000) -> List[Dict]:
    """Truncate the message list so that the total character count stays within budget.
    Always keeps the last message in full. Older messages are truncated from the front.
    """
    total = sum(len(m.get("content", "")) for m in messages)
    if total <= max_total_chars:
        return messages

    result = []
    budget = max_total_chars
    for msg in reversed(messages):
        content = msg.get("content", "")
        if budget <= 0:
            break
        if len(content) <= budget:
            result.insert(0, msg)
            budget -= len(content)
        else:
            truncated = {**msg, "content": content[:budget] + "…[truncated]"}
            result.insert(0, truncated)
            budget = 0
    return result


def build_conversation_context(messages: List[ChatMessage], n: int = 3) -> str:
    """Build a compact text summary of the last n turns for the planner prompt."""
    recent = [m for m in messages[-n * 2 :] if m.role in ("user", "assistant")]
    return "\n".join(f"{m.role.upper()}: {m.content[:300]}" for m in recent) or "None"


async def summarize_conversation(messages: List[Dict]) -> str:
    """Generate a concise summary of older conversation history using a lightweight model.
    Used by compile_chat_messages to compress old turns into a single paragraph.
    """
    from app.clients.groq import groq_chat  # lazy import

    if not messages:
        return ""

    history_text = "\n".join(f"{m['role'].upper()}: {m['content'][:1000]}" for m in messages)

    summary_prompt = [
        {
            "role": "system",
            "content": (
                "You are an expert AI context compressor. Summarize the following conversation history between a User and an AI Assistant "
                "into a single concise paragraph. Focus ONLY on: 1) What topics/questions the user asked, and 2) Key decisions, conclusions, or answers "
                "provided by the assistant. Avoid general fluff. Do not exceed 150 words."
            ),
        },
        {
            "role": "user",
            "content": f"Here is the conversation history to summarize:\n\n{history_text}",
        },
    ]

    try:
        summary = await groq_chat(summary_prompt, PLAN_MODEL, temperature=0.0, max_tokens=200)
        return summary.strip()
    except Exception as e:
        log.warning(f"Failed to summarize conversation history: {e}")
        return "\n".join(f"{m['role'].upper()}: {m['content'][:150]}..." for m in messages[:3])


async def compile_chat_messages(system_prompt: str, chat_messages: List[ChatMessage]) -> List[Dict]:
    """Apply sliding-window context engineering + conversation summarisation.

    Keeps the system prompt and last 2 messages in full, and summarises
    older messages to conserve token space and prevent TPM rate limits.
    """
    if not chat_messages:
        return [{"role": "system", "content": system_prompt}]

    last_msg = {"role": chat_messages[-1].role, "content": chat_messages[-1].content}

    history = chat_messages[:-1]
    if len(history) <= 2:
        return [{"role": "system", "content": system_prompt}] + [
            {"role": m.role, "content": m.content} for m in chat_messages
        ]

    recent_history = [{"role": m.role, "content": m.content} for m in history[-2:]]
    older_history = [{"role": m.role, "content": m.content} for m in history[:-2]]

    older_summary = await summarize_conversation(older_history)

    enriched_system_prompt = system_prompt
    if older_summary:
        enriched_system_prompt += f"\n\n[Summary of earlier conversation history]\n{older_summary}"

    return [{"role": "system", "content": enriched_system_prompt}] + recent_history + [last_msg]
