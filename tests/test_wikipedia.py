"""
Test Wikipedia Integration Module
=================================
Validates searching Wikipedia and enriching datasets.

Usage:
    python -m tests.test_wikipedia
"""

import asyncio
import os
import sys

# Adjust path to import from app
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.sources.wikipedia import enrich_datasets_with_wikipedia, search_wikipedia_summary


async def run_tests():
    print("Testing Wikipedia Integration...")
    print("================================\n")

    # 1. Test search_wikipedia_summary
    query = "ImageNet"
    print(f"1. Searching Wikipedia for '{query}'...")
    res = await search_wikipedia_summary(query)

    if res:
        print("   [SUCCESS] Found page!")
        print(f"   Title: {res['title']}")
        print(f"   URL: {res['url']}")
        print(f"   Summary: {res['extract'][:120]}...\n")
    else:
        print(f"   [FAILED] No page found for '{query}'.\n")

    # 2. Test dataset enrichment
    mock_datasets = [
        {"name": "ImageNet", "description": ""},
        {"name": "CIFAR-10", "description": "some existing short description"},
        {"name": "NotARealDatasetNameXYZ123", "description": ""},
    ]
    print("2. Testing dataset enrichment...")
    enriched = await enrich_datasets_with_wikipedia(mock_datasets)

    for i, ds in enumerate(enriched):
        print(f"   Dataset {i + 1}: '{ds['name']}'")
        print(f"   - Wikipedia URL: {ds.get('wikipedia_url', 'None')}")
        print(f"   - Description: {ds.get('description', 'None')[:100]}...")
        print("-" * 30)


if __name__ == "__main__":
    asyncio.run(run_tests())
