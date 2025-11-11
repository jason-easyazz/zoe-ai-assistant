#!/usr/bin/env python3
"""Pre-warm all models to reduce response times"""
import asyncio
import httpx
import sys

OLLAMA_URL = "http://localhost:11434/api/generate"
MODELS = [
    "gemma3n-e2b-gpu-fixed",
    "gemma3n:e4b",
    "gemma2:2b",
    "phi3:mini",
    "llama3.2:3b",
    "qwen2.5:7b",
]

async def warmup_model(model_name):
    """Pre-warm a single model"""
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                OLLAMA_URL,
                json={
                    "model": model_name,
                    "prompt": "warmup",
                    "stream": False,
                    "options": {
                        "num_gpu": 1 if "gpu" in model_name.lower() else 0,
                        "num_predict": 10
                    }
                }
            )
            if response.status_code == 200:
                print(f"✅ {model_name} warmed up")
                return True
            else:
                print(f"❌ {model_name} failed: {response.status_code}")
                return False
    except Exception as e:
        print(f"❌ {model_name} error: {e}")
        return False

async def main():
    print("🔥 Pre-warming models...")
    tasks = [warmup_model(model) for model in MODELS]
    results = await asyncio.gather(*tasks)
    success = sum(results)
    print(f"\n✅ {success}/{len(MODELS)} models warmed up")

if __name__ == "__main__":
    asyncio.run(main())




