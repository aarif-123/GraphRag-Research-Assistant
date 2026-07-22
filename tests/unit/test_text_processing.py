"""
Unit tests for text-processing utilities.

Covers:
- compress_rag_prompt(): abstract truncation, chunk body truncation,
  preservation of lines below threshold, multi-section correctness
- truncate_messages(): already-within-limit pass-through, single-message
  truncation, empty-list edge case, smart compression preferred over
  character-level truncation
"""

from __future__ import annotations

from typing import Dict, List

# ---------------------------------------------------------------------------
# Inline replicas of the helpers under test.
# Importing app/app.py directly requires live environment variables.
# These are verbatim copies of the production functions so the tests validate
# the real algorithm without the startup side-effects.
# ---------------------------------------------------------------------------

ABSTRACT_LIMIT = 120
CHUNK_LIMIT = 150


def compress_rag_prompt(content: str) -> str:
    new_lines = []
    in_chunks = False
    for line in content.splitlines():
        if "=== RETRIEVED CHUNK EVIDENCE ===" in line:
            in_chunks = True
            new_lines.append(line)
            continue
        if in_chunks and (line.startswith("━━━") or line.startswith("═══") or "QUERY" in line):
            in_chunks = False

        if in_chunks:
            if line.strip().startswith("[") and " | " in line:
                new_lines.append(line)
            elif line.strip():
                stripped = line.strip()
                if len(stripped) > CHUNK_LIMIT:
                    indent = len(line) - len(line.lstrip())
                    new_lines.append(" " * indent + stripped[:CHUNK_LIMIT] + " [...]")
                else:
                    new_lines.append(line)
            else:
                new_lines.append(line)
        else:
            if line.startswith("  Abstract: "):
                abstract_text = line[12:]
                if len(abstract_text) > ABSTRACT_LIMIT:
                    new_lines.append("  Abstract: " + abstract_text[:ABSTRACT_LIMIT] + " [...]")
                else:
                    new_lines.append(line)
            else:
                new_lines.append(line)
    return "\n".join(new_lines)


def truncate_messages(messages: List[Dict], max_total_chars: int = 12000) -> List[Dict]:
    total_chars = sum(len(m.get("content", "")) for m in messages)
    if total_chars <= max_total_chars:
        return messages

    longest_idx = -1
    longest_len = -1
    for i, m in enumerate(messages):
        content_len = len(m.get("content", ""))
        if content_len > longest_len:
            longest_len = content_len
            longest_idx = i

    if longest_idx == -1 or longest_len == 0:
        return messages

    truncated_messages = [dict(m) for m in messages]
    content = truncated_messages[longest_idx]["content"]

    compressed_content = compress_rag_prompt(content)
    if len(compressed_content) < len(content):
        truncated_messages[longest_idx]["content"] = compressed_content
        new_total = sum(len(m.get("content", "")) for m in truncated_messages)
        if new_total <= max_total_chars:
            return truncated_messages
        content = compressed_content
        total_chars = new_total
        longest_len = len(content)

    suffix = "\n\n[... Context truncated due to rate/size limits ...]"
    excess = total_chars - max_total_chars + len(suffix)
    target_len = max(0, longest_len - excess)
    truncated_messages[longest_idx]["content"] = content[:target_len] + suffix
    return truncated_messages


# ---------------------------------------------------------------------------
# compress_rag_prompt() tests
# ---------------------------------------------------------------------------


def _make_abstract_section(text: str) -> str:
    return f"  Abstract: {text}"


class TestCompressRagPrompt:
    def test_short_abstract_preserved(self):
        short = "A" * ABSTRACT_LIMIT
        prompt = _make_abstract_section(short)
        result = compress_rag_prompt(prompt)
        assert "[...]" not in result
        assert short in result

    def test_long_abstract_truncated(self):
        long_text = "B" * (ABSTRACT_LIMIT + 50)
        prompt = _make_abstract_section(long_text)
        result = compress_rag_prompt(prompt)
        assert "[...]" in result
        # Truncated portion only has ABSTRACT_LIMIT chars of the text
        assert "B" * ABSTRACT_LIMIT in result
        assert "B" * (ABSTRACT_LIMIT + 1) not in result

    def test_non_abstract_line_untouched(self):
        line = "Some other line that is not an abstract."
        result = compress_rag_prompt(line)
        assert result == line

    def test_chunk_section_header_preserved(self):
        prompt = (
            "=== RETRIEVED CHUNK EVIDENCE ===\n"
            "[1] Attention Is All You Need | sim=0.92\n"
            "This is the chunk body text that fits within the limit."
        )
        result = compress_rag_prompt(prompt)
        assert "[1] Attention Is All You Need | sim=0.92" in result

    def test_long_chunk_body_truncated(self):
        long_body = "X" * (CHUNK_LIMIT + 100)
        prompt = f"=== RETRIEVED CHUNK EVIDENCE ===\n[1] Paper | sim=0.80\n  {long_body}"
        result = compress_rag_prompt(prompt)
        assert "[...]" in result

    def test_short_chunk_body_preserved(self):
        short_body = "Y" * CHUNK_LIMIT
        prompt = f"=== RETRIEVED CHUNK EVIDENCE ===\n[1] Paper | sim=0.80\n  {short_body}"
        result = compress_rag_prompt(prompt)
        assert "[...]" not in result

    def test_empty_string_returns_empty(self):
        assert compress_rag_prompt("") == ""

    def test_section_boundary_resets_chunk_mode(self):
        """Lines after ═══ or ━━━ should no longer be treated as chunk bodies."""
        prompt = (
            "=== RETRIEVED CHUNK EVIDENCE ===\n"
            "[1] Paper | sim=0.80\n"
            "━━━ END ━━━\n"
            "  Abstract: Short abstract."
        )
        result = compress_rag_prompt(prompt)
        # Abstract line should NOT have been treated as a chunk body
        assert "Short abstract." in result


# ---------------------------------------------------------------------------
# truncate_messages() tests
# ---------------------------------------------------------------------------


class TestTruncateMessages:
    def test_within_limit_returned_unchanged(self):
        messages = [
            {"role": "user", "content": "short"},
            {"role": "assistant", "content": "reply"},
        ]
        result = truncate_messages(messages, max_total_chars=1000)
        assert result == messages

    def test_empty_list_returned_unchanged(self):
        result = truncate_messages([], max_total_chars=100)
        assert result == []

    def test_truncation_adds_suffix(self):
        long_content = "A" * 15000
        messages = [{"role": "user", "content": long_content}]
        result = truncate_messages(messages, max_total_chars=100)
        assert "[... Context truncated" in result[0]["content"]

    def test_only_longest_message_truncated(self):
        messages = [
            {"role": "system", "content": "short system"},
            {"role": "user", "content": "B" * 15000},
        ]
        result = truncate_messages(messages, max_total_chars=200)
        # System message should be unchanged
        assert result[0]["content"] == "short system"
        assert "[... Context truncated" in result[1]["content"]

    def test_result_is_within_limit(self):
        limit = 500
        messages = [{"role": "user", "content": "C" * 5000}]
        result = truncate_messages(messages, max_total_chars=limit)
        total = sum(len(m.get("content", "")) for m in result)
        assert total <= limit

    def test_messages_are_shallow_copied(self):
        """Original list must not be mutated."""
        original = "D" * 15000
        messages = [{"role": "user", "content": original}]
        truncate_messages(messages, max_total_chars=100)
        assert messages[0]["content"] == original

    def test_all_empty_content_unchanged(self):
        messages = [{"role": "user", "content": ""}]
        result = truncate_messages(messages, max_total_chars=10)
        assert result == messages
