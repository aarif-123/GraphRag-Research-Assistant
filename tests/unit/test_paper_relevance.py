from app.core.retrieval import paper_relevance_score


def test_paper_relevance_excludes_non_graph_rag_results():
    query = (
        "How do Graph Neural Networks enhance Retrieval-Augmented Generation "
        "for multi-hop reasoning compared to vector RAG?"
    )
    relevant = {
        "title": "Graph Neural Networks for Multi-Hop Retrieval-Augmented Generation",
        "abstract": "We use graph message passing for retrieval and reasoning.",
    }
    unrelated = {
        "title": "Autoregressive Retrieval Augmentation for Image Generation",
        "abstract": "Patch-level retrieval improves image generation.",
    }

    assert paper_relevance_score(query, relevant) > 0
    assert paper_relevance_score(query, unrelated) == 0
