"""
Model Pre-warming Service for Zoe
Pre-loads models into GPU memory on startup for faster responses
"""
import asyncio
import httpx
import logging
import os
from typing import List
from model_config import MODEL_CONFIGS, model_selector

logger = logging.getLogger(__name__)

async def prewarm_models(models: List[str] = None):
    """Pre-warm models by loading them into GPU/CPU memory based on hardware"""
    if models is None:
        # Hardware-specific model pre-warming
        from model_config import model_selector
        
        if model_selector.hardware == 'jetson':
            # Jetson: Pre-warm GPU models
            models = ["hermes3:8b-llama3.1-q4_K_M"]  # PRIMARY GPU model
            logger.info("🚀 Jetson mode: Pre-warming GPU models")
        else:
            # Pi5: Pre-warm CPU-optimized models
            models = ["phi3:mini"]  # PRIMARY CPU model
            logger.info("🥧 Pi5 mode: Pre-warming CPU-optimized models")
    
    llm_url = os.getenv("LLM_URL", "http://zoe-llamacpp:11434")
    
    async with httpx.AsyncClient(timeout=60.0) as client:
        for model in models:
            try:
                logger.info(f"🔥 Pre-warming model: {model}")
                
                # Get model config for GPU settings
                model_config = MODEL_CONFIGS.get(model)
                num_gpu = 99  # Default: use all GPU layers
                if model_config and model_config.num_gpu is not None:
                    num_gpu = model_config.num_gpu
                
                logger.info(f"   Loading with num_gpu={num_gpu}")
                
                # Send a minimal request to load the model into GPU (OpenAI format)
                response = await client.post(
                    f"{llm_url}/v1/chat/completions",
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": "hi"}],
                        "stream": False,
                        "max_tokens": 5  # Minimal generation
                    },
                    timeout=60.0
                )
                if response.status_code == 200:
                    logger.info(f"✅ Model {model} pre-warmed successfully")
                else:
                    logger.warning(f"⚠️ Model {model} pre-warming failed: {response.status_code}")
            except Exception as e:
                logger.warning(f"⚠️ Failed to pre-warm model {model}: {e}")

async def prewarm_background():
    """Pre-warm models in background (non-blocking)"""
    asyncio.create_task(prewarm_models())

