#!/usr/bin/env python3
"""Pre-warm models on startup to reduce response times"""
import asyncio
import httpx
import logging
import os
import sys

# Add parent directory to path for model_config import
sys.path.insert(0, os.path.dirname(__file__))
from model_config import MODEL_CONFIGS

logger = logging.getLogger(__name__)

LLM_URL = os.getenv("LLM_URL", "http://zoe-llamacpp:11434/v1/chat/completions")
# Allow running from host or container
if os.path.exists("/.dockerenv"):
    LLM_URL = "http://zoe-llamacpp:11434/v1/chat/completions"
else:
    LLM_URL = "http://localhost:11434/v1/chat/completions"
MODELS = [
    "gemma3n-e2b-gpu-fixed",
    "gemma3n:e4b",
    "gemma2:2b",
    "phi3:mini",
    "llama3.2:3b",
    "qwen2.5:7b",
]

async def warmup_model(model_name: str):
    """Pre-warm a single model"""
    try:
        # ✅ FIX: Add delay between warmup attempts to avoid overwhelming Ollama
        await asyncio.sleep(0.5)
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            # ✅ FIX: Use correct URL format and add error handling
            try:
                # Get model config for GPU settings
                model_config = MODEL_CONFIGS.get(model_name)
                num_gpu = 99  # Default: use all GPU layers
                if model_config and model_config.num_gpu is not None:
                    num_gpu = model_config.num_gpu
                
                logger.info(f"   Warming {model_name} with num_gpu={num_gpu}")
                
                response = await client.post(
                    LLM_URL,
                    json={
                        "model": model_name,
                        "messages": [{"role": "user", "content": "hi"}],
                        "stream": False,
                        "max_tokens": 5  # Minimal tokens for warmup
                    }
                )
                if response.status_code == 200:
                    logger.info(f"✅ {model_name} warmed up")
                    return True
                else:
                    error_text = response.text[:200] if hasattr(response, 'text') else str(response.status_code)
                    logger.warning(f"⚠️ {model_name} warmup failed: HTTP {response.status_code} - {error_text}")
                    return False
            except httpx.ConnectError as e:
                logger.warning(f"⚠️ {model_name} connection error: {e}")
                return False
            except httpx.TimeoutException:
                logger.warning(f"⚠️ {model_name} warmup timeout")
                return False
    except Exception as e:
        logger.warning(f"⚠️ {model_name} warmup error: {str(e)}")
        return False

async def warmup_all_models():
    """Pre-warm all models"""
    logger.info("🔥 Pre-warming models...")
    tasks = [warmup_model(model) for model in MODELS]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    success = sum(1 for r in results if r is True)
    logger.info(f"✅ {success}/{len(MODELS)} models warmed up")
    return success

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(warmup_all_models())

