"""
Test Kaggle Integration Module
==============================
Validates searching Kaggle datasets and enriching dataset metadata.

Usage:
    python -m tests.test_kaggle
"""

import asyncio
import sys
import os
from dotenv import load_dotenv

# Adjust path to import from app
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Load dotenv config
load_dotenv(".env.local", override=True)
load_dotenv(".env", override=False)

from app.sources.kaggle import search_kaggle_dataset, enrich_datasets_with_kaggle

async def run_tests():
    print("Testing Kaggle Integration...")
    print("=============================\n")

    # 1. Test search_kaggle_dataset
    query = "stanford dogs"
    print(f"1. Searching Kaggle for '{query}'...")
    res = await search_kaggle_dataset(query)
    
    if res:
        print(f"   [SUCCESS] Found dataset on Kaggle!")
        print(f"   Title: {res['title']}")
        print(f"   Ref: {res['ref']}")
        print(f"   URL: {res['url']}")
        print(f"   Votes: {res['vote_count']}")
        print(f"   Subtitle: {res['subtitle']}\n")
    else:
        print(f"   [FAILED] No dataset found for '{query}'. Make sure Kaggle keys are configured in environment.\n")

    # 2. Test dataset enrichment
    mock_datasets = [
        {"name": "stanford dogs", "description": ""},
        {"name": "NotARealDatasetNameXYZ123", "description": ""}
    ]
    print("2. Testing dataset enrichment...")
    enriched = await enrich_datasets_with_kaggle(mock_datasets)
    
    for i, ds in enumerate(enriched):
        print(f"   Dataset {i+1}: '{ds['name']}'")
        print(f"   - Kaggle URL: {ds.get('kaggle_url', 'None')}")
        print(f"   - Kaggle Title: {ds.get('kaggle_title', 'None')}")
        print(f"   - Kaggle Votes: {ds.get('kaggle_votes', 'None')}")
        print("-" * 30)

if __name__ == "__main__":
    asyncio.run(run_tests())
