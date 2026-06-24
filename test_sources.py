"""
Integration tests for Aether sources (Semantic Scholar + Hugging Face Papers API).
"""
import asyncio
import sys
sys.path.insert(0, 'app')

from sources.papers_with_code import (
    enrich_arxiv_papers_with_pwc,
    enrich_paper_with_code_and_datasets,
    get_paper_repos_pwc,
    get_paper_datasets_pwc,
)
from sources.semantic_scholar import search_papers_s2, enrich_arxiv_papers_with_s2


def test_github_extraction_offline():
    print("=== Test 1: Offline GitHub Link Extraction ===")
    fake_paper = {
        "id": "2402.01234",
        "title": "Attention Is All You Need",
        "abstract": "We propose the Transformer. Code at https://github.com/tensorflow/tensor2tensor",
        "comment": "Code: https://github.com/google-research/bert and https://github.com/huggingface/transformers",
        "year": "2017",
        "authors": ["Vaswani"],
        "url": "https://arxiv.org/abs/1706.03762",
        "pdf_url": "",
    }
    result = enrich_paper_with_code_and_datasets(fake_paper)
    repos = result["code_repos"]
    print(f"  Found {len(repos)} repos:")
    for r in repos:
        print(f"    - {r['url']} (official={r['is_official']})")
    assert len(repos) >= 2, "Should find at least 2 repos"
    print("  PASSED")


def test_dataset_extraction_offline():
    print("\n=== Test 2: Offline Dataset Extraction ===")
    fake_paper = {
        "id": "2301.00001",
        "title": "A Survey",
        "abstract": "We evaluate on ImageNet, CIFAR-10, COCO, and SQuAD 2.0. Also tested on GLUE benchmark.",
        "comment": "",
        "year": "2023",
        "authors": [],
        "url": "",
        "pdf_url": "",
    }
    result = enrich_paper_with_code_and_datasets(fake_paper)
    datasets = result["datasets"]
    print(f"  Found {len(datasets)} datasets:")
    for d in datasets:
        print(f"    - {d['name']} -> {d['url']}")
    assert len(datasets) >= 3, f"Should find at least 3 datasets, got {len(datasets)}"
    print("  PASSED")


async def test_hf_papers_api_online():
    print("\n=== Test 3: Online Hugging Face Papers API Lookup ===")
    # TimesFM (2310.10688) has a verified githubRepo on Hugging Face
    arxiv_id = "2310.10688"
    fake_papers = [{
        "id": arxiv_id,
        "title": "A decoder-only foundation model for time-series forecasting",
        "abstract": "Motivated by recent advances in large language models...",
        "comment": "",
        "year": "2023",
        "authors": ["Abhimanyu Das"],
        "url": f"https://arxiv.org/abs/{arxiv_id}",
        "pdf_url": f"https://arxiv.org/pdf/{arxiv_id}.pdf",
    }]
    
    enriched = await enrich_arxiv_papers_with_pwc(fake_papers)
    assert len(enriched) == 1, "Should return 1 paper"
    p = enriched[0]
    
    print(f"  Paper: {p['title']}")
    print(f"  GitHub Repos: {p.get('code_repos')}")
    print(f"  Datasets: {len(p.get('datasets', []))}")
    print(f"  Linked Models: {len(p.get('linked_models', []))}")
    print(f"  Linked Spaces: {len(p.get('linked_spaces', []))}")
    print(f"  HF Upvotes: {p.get('hf_upvotes')}")
    
    assert len(p.get("code_repos", [])) > 0, "Should find at least one code repo"
    assert p["code_repos"][0]["is_official"] is True, "Should be official repo"
    assert "timesfm" in p["code_repos"][0]["url"], "Should point to timesfm repo"
    print("  PASSED")


async def test_s2_online():
    print("\n=== Test 4: Online Semantic Scholar Search ===")
    papers = await search_papers_s2("large language models", limit=2)
    if papers:
        p = papers[0]
        print(f"  S2 OK: {p['title'][:60]}")
        print(f"  Citations: {p['citation_count']} | TLDR: {(p['tldr'] or 'none')[:60]}")
        print("  PASSED")
    else:
        print("  SKIPPED (rate limited or offline)")


async def main():
    test_github_extraction_offline()
    test_dataset_extraction_offline()
    try:
        await test_hf_papers_api_online()
    except Exception as e:
        print(f"  FAILED Test 3 (online HF): {e}")
    await test_s2_online()
    print("\n=== All tests done ===")


if __name__ == "__main__":
    asyncio.run(main())
