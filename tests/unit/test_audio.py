from app.core.document import is_whisper_hallucination


def test_whisper_hallucination_empty_or_short():
    assert is_whisper_hallucination("") is True
    assert is_whisper_hallucination(" ") is True
    assert is_whisper_hallucination(".") is True
    assert is_whisper_hallucination("a") is True


def test_whisper_hallucination_known_patterns():
    assert is_whisper_hallucination("Thank you for watching!") is True
    assert is_whisper_hallucination("Please like and subscribe.") is True
    assert is_whisper_hallucination("Subtitles by Amara.org") is True
    assert is_whisper_hallucination("Thank you so much") is True
    assert is_whisper_hallucination("Uh") is True


def test_whisper_valid_speech():
    assert is_whisper_hallucination("What is GraphRAG architecture?") is False
    assert is_whisper_hallucination("Explain quantum computing concepts.") is False
    assert is_whisper_hallucination("Find papers about transformer attention mechanisms.") is False
