from app.utils.misc import clean_and_resolve_links


def test_paper_title_link_uses_url_from_matching_retrieved_record():
    answer = "[A correct paper title](https://arxiv.org/abs/9999.99999)"
    papers = [
        {
            "title": "A correct paper title",
            "id": "1234.56789",
            "url": "https://arxiv.org/abs/1234.56789",
        }
    ]

    result = clean_and_resolve_links(answer, arxiv_papers=papers)

    assert result == "[A correct paper title](https://arxiv.org/abs/1234.56789)"


def test_known_paper_url_with_unmatched_title_is_rendered_as_unlinked_text():
    answer = "[An unrelated title](https://arxiv.org/abs/1234.56789)"
    papers = [
        {
            "title": "The actual retrieved paper",
            "id": "1234.56789",
            "url": "https://arxiv.org/abs/1234.56789",
        }
    ]

    result = clean_and_resolve_links(answer, arxiv_papers=papers)

    assert result == "An unrelated title"


def test_semantic_scholar_citation_tag_uses_its_matching_paper_link():
    answer = "See [S2-1] for details."
    papers = [
        {
            "title": "Semantic Scholar paper",
            "s2_id": "paper-id-1",
            "s2_url": "https://www.semanticscholar.org/paper/paper-id-1",
        }
    ]

    result = clean_and_resolve_links(answer, s2_papers=papers)

    assert result == ("See [S2-1](https://www.semanticscholar.org/paper/paper-id-1) for details.")
