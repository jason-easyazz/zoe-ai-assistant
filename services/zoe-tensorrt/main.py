"""
TensorRT-LLM Inference Service for Jetson Orin
Provides GPU-accelerated inference using TensorRT-LLM
"""
import os
import logging
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional, List
import asyncio

# TensorRT-LLM imports
try:
    import tensorrt_llm
    from tensorrt_llm.runtime import ModelRunner
    TENSORRT_AVAILABLE = True
except ImportError:
    TENSORRT_AVAILABLE = False
    logging.warning("TensorRT-LLM not available, running in fallback mode")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Zoe TensorRT-LLM Service")

# Global model runner
model_runner: Optional[ModelRunner] = None
ENGINE_DIR = os.getenv("TENSORRT_ENGINE_DIR", "/workspace/models/hermes3-8b-trt-engine")

class GenerateRequest(BaseModel):
    prompt: str
    max_tokens: int = 256
    temperature: float = 0.7
    top_p: float = 0.9
    stream: bool = False

class GenerateResponse(BaseModel):
    text: str
    tokens_generated: int
    inference_time_ms: float

@app.on_event("startup")
async def load_model():
    """Load TensorRT-LLM engine on startup"""
    global model_runner
    
    if not TENSORRT_AVAILABLE:
        logger.warning("TensorRT-LLM not available - service running in mock mode")
        return
    
    try:
        logger.info(f"Loading TensorRT engine from {ENGINE_DIR}")
        
        # Initialize ModelRunner with TensorRT engine
        model_runner = ModelRunner.from_dir(ENGINE_DIR)
        
        logger.info("✅ TensorRT-LLM model loaded successfully")
        
        # Warm-up inference
        logger.info("Warming up model...")
        _ = await generate_text("Hello", max_tokens=5)
        logger.info("✅ Model warmed up and ready")
        
    except Exception as e:
        logger.error(f"Failed to load TensorRT model: {e}")
        model_runner = None

async def generate_text(prompt: str, max_tokens: int = 256, 
                       temperature: float = 0.7, top_p: float = 0.9) -> dict:
    """Generate text using TensorRT-LLM"""
    
    if model_runner is None:
        # Fallback mode for testing
        return {
            "text": f"[TensorRT-LLM not loaded] Echo: {prompt[:50]}",
            "tokens_generated": 10,
            "inference_time_ms": 0.0
        }
    
    try:
        import time
        start_time = time.time()
        
        # Run inference
        output = model_runner.generate(
            [prompt],
            max_new_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
            end_id=128009,  # Llama 3.1 EOS token
        )
        
        inference_time = (time.time() - start_time) * 1000
        
        generated_text = output[0][0] if output else ""
        tokens_generated = len(generated_text.split())
        
        return {
            "text": generated_text,
            "tokens_generated": tokens_generated,
            "inference_time_ms": inference_time
        }
        
    except Exception as e:
        logger.error(f"Generation error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/generate", response_model=GenerateResponse)
async def generate(request: GenerateRequest):
    """Generate text from prompt"""
    result = await generate_text(
        request.prompt,
        max_tokens=request.max_tokens,
        temperature=request.temperature,
        top_p=request.top_p
    )
    return GenerateResponse(**result)

@app.get("/health")
async def health():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "tensorrt_loaded": model_runner is not None,
        "engine_dir": ENGINE_DIR
    }

@app.get("/info")
async def info():
    """Get model information"""
    return {
        "model": "Hermes-3-Llama-3.1-8B",
        "backend": "TensorRT-LLM",
        "engine_dir": ENGINE_DIR,
        "loaded": model_runner is not None,
        "tensorrt_available": TENSORRT_AVAILABLE
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8011)

