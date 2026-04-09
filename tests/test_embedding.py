import os
import requests
from dotenv import load_dotenv

# Load environment variables (matching app.py logic)
load_dotenv(".env.local", override=True)
load_dotenv(".env", override=False)

HF_TOKEN = os.getenv("HF_TOKEN")
EMBED_MODEL = os.getenv("EMBED_MODEL", "Snowflake/snowflake-arctic-embed-l-v2.0")

def test_embedding():
    if not HF_TOKEN:
        print("❌ ERROR: HF_TOKEN is not set in your .env file.")
        return

    print(f"🔄 Testing HuggingFace Embedding API...")
    print(f"📦 Model: {EMBED_MODEL}")
    
    url = f"https://router.huggingface.co/hf-inference/models/{EMBED_MODEL}/pipeline/feature-extraction"
    headers = {"Authorization": f"Bearer {HF_TOKEN}"}
    payload = {"inputs": "This is a test sentence to check if the embedding works."}

    try:
        print("🚀 Sending request to HuggingFace...")
        response = requests.post(url, headers=headers, json=payload)
        
        if response.status_code == 200:
            embedding = response.json()
            
            # Handle nested list formatting (matches _to_plain_list in app.py)
            if isinstance(embedding, list) and len(embedding) > 0 and isinstance(embedding[0], list):
                embedding = embedding[0]
                
            print("✅ SUCCESS! Embedding model generated a valid vector.")
            print(f"📊 Dimensions: {len(embedding)}")
            if len(embedding) >= 5:
                print(f"🧬 Preview (first 5 values): {embedding[:5]}")
            
        elif response.status_code == 503:
            print("⏳ Model is currently loading (Cold Start API / 503).")
            wait_time = response.headers.get('Retry-After', 'unknown')
            print(f"HuggingFace needs ~{wait_time}s to boot the model. Try again in a moment!")
            
        else:
            print(f"❌ ERROR: Received HTTP {response.status_code}")
            print(f"Response: {response.text}")
            
    except Exception as e:
        print(f"💥 REQUEST FAILED: {e}")

if __name__ == "__main__":
    test_embedding()
