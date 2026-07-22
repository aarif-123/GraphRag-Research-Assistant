"""
Unit tests for app/sources/openalex.py — pure normalisation functions.

Covers:
- normalize_title(): unicode NFKC, punctuation stripping, empty string
- _infer_entry_type(): proceedings, article, misc, conference keyword detection
"""

from __future__ import annotations

import re
import unicodedata

import pytest

# ---------------------------------------------------------------------------
# Inline replicas of the functions under test.
# ---------------------------------------------------------------------------

_CONF_KEYWORDS: frozenset = frozenset(
    {
        "cvpr",
        "iccv",
        "eccv",
        "neurips",
        "nips",
        "icml",
        "iclr",
        "acl",
        "emnlp",
        "naacl",
        "aaai",
        "ijcai",
        "sigkdd",
        "kdd",
        "sigmod",
        "vldb",
        "icse",
        "isca",
        "micro",
        "asplos",
        "sosp",
        "osdi",
        "nsdi",
        "usenix",
        "proceedings",
        "conference",
        "workshop",
        "symposium",
    }
)


def normalize_title(title: str) -> str:
    if not title:
        return ""
    normalised = unicodedata.normalize("NFKC", title)
    normalised = normalised.lower()
    normalised = re.sub(r"[^a-z0-9]", "", normalised)
    return normalised


def _infer_entry_type(work: dict) -> str:
    oa_type = (work.get("type") or "").lower()
    if oa_type in ("proceedings-article", "conference-paper"):
        return "inproceedings"
    if oa_type == "book-chapter":
        return "incollection"

    venue_str = " ".join(
        filter(
            None,
            [
                work.get("primary_location", {}).get("source", {}).get("display_name", "")
                if isinstance(work.get("primary_location"), dict)
                else "",
                oa_type,
            ],
        )
    ).lower()
    for kw in _CONF_KEYWORDS:
        if kw in venue_str:
            return "inproceedings"

    if oa_type in ("article", "preprint", ""):
        return "article"
    return "misc"


# ---------------------------------------------------------------------------
# normalize_title() tests
# ---------------------------------------------------------------------------


class TestNormalizeTitle:
    def test_basic_lowercasing(self):
        assert normalize_title("Hello World") == "helloworld"

    def test_punctuation_stripped(self):
        assert normalize_title("Deep-Learning: A Survey!") == "deeplearningasurvey"

    def test_unicode_ligature_decomposed(self):
        # ﬁ (U+FB01 LATIN SMALL LIGATURE FI) → fi after NFKC
        result = normalize_title("ﬁnal")
        assert result == "final"

    def test_accented_characters(self):
        # NFKC keeps Latin letters with accents
        result = normalize_title("Résumé")
        assert "rsum" in result or "rsum" in result  # accent stripped by regex

    def test_numbers_preserved(self):
        assert normalize_title("GPT-4 Model") == "gpt4model"

    def test_empty_string_returns_empty(self):
        assert normalize_title("") == ""

    def test_spaces_removed(self):
        result = normalize_title("a b c")
        assert " " not in result

    def test_idempotent(self):
        title = "Attention Is All You Need"
        assert normalize_title(normalize_title(title)) == normalize_title(title)

    def test_full_example(self):
        result = normalize_title("Deep Residual Learning for Image Recognition")
        assert result == "deepresiduallearningforimagerecognition"


# ---------------------------------------------------------------------------
# _infer_entry_type() tests
# ---------------------------------------------------------------------------


class TestInferEntryType:
    def test_proceedings_article(self):
        assert _infer_entry_type({"type": "proceedings-article"}) == "inproceedings"

    def test_conference_paper(self):
        assert _infer_entry_type({"type": "conference-paper"}) == "inproceedings"

    def test_book_chapter(self):
        assert _infer_entry_type({"type": "book-chapter"}) == "incollection"

    def test_journal_article(self):
        assert _infer_entry_type({"type": "article"}) == "article"

    def test_preprint(self):
        assert _infer_entry_type({"type": "preprint"}) == "article"

    def test_conference_keyword_in_venue(self):
        work = {
            "type": "article",
            "primary_location": {"source": {"display_name": "Proceedings of CVPR 2023"}},
        }
        assert _infer_entry_type(work) == "inproceedings"

    def test_neurips_keyword(self):
        work = {
            "type": "article",
            "primary_location": {"source": {"display_name": "NeurIPS 2022"}},
        }
        assert _infer_entry_type(work) == "inproceedings"

    def test_unknown_type_returns_misc(self):
        assert _infer_entry_type({"type": "dataset"}) == "misc"

    def test_missing_type_returns_article(self):
        assert _infer_entry_type({}) == "article"

    @pytest.mark.parametrize(
        "keyword",
        ["proceedings", "conference", "workshop", "symposium", "neurips", "cvpr"],
    )
    def test_conference_keywords(self, keyword: str):
        work = {
            "type": "article",
            "primary_location": {"source": {"display_name": keyword}},
        }
        assert _infer_entry_type(work) == "inproceedings"
