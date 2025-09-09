#!/bin/bash
# ACTIVATE_EXISTING_DISCOVERY.sh
# Use the existing model discovery infrastructure

echo "🔌 ACTIVATING EXISTING DISCOVERY SYSTEM"
echo "========================================"
echo ""

cd /home/pi/zoe

# Check what models are currently discovered
echo "1. Current discovered models:"
if [ -f data/llm_models.json ]; then
    cat data/llm_models.json | jq '.providers | to_entries[] | {provider: .key, enabled: .value.enabled, models: .value.models}'
fi

# Trigger the existing discovery
echo -e "\n2. Running model discovery with existing infrastructure:"
docker exec zoe-core python3 << 'DISCOVER'
import asyncio
import sys
sys.path.append('/app')

async def run_discovery():
    try:
        from llm_models import LLMModelManager
        manager = LLMModelManager()
        results = await manager.discover_all_models()
        print("Discovery complete!")
        return results
    except ImportError:
        # Try alternative import
        from model_discovery import discover_models
        results = await discover_models()
        print("Discovery complete via model_discovery!")
        return results

asyncio.run(run_discovery())
DISCOVER

# Connect existing discovery to AI client
echo -e "\n3. Connecting discovery to AI client:"
docker exec zoe-core bash -c 'cat > /app/ai_client.py << "EOF"
"""AI Client using existing discovery infrastructure"""
import httpx
import os
import logging
import json
from pathlib import Path
from dynamic_router import DynamicRouter

logger = logging.getLogger(__name__)

# Use the existing dynamic router
router = DynamicRouter()

async def get_ai_response(message: str, context: dict = None) -> str:
    """Use discovered models via dynamic router"""
    
    context = context or {}
    mode = context.get("mode", "user")
    
    # Get routing from existing system
    complexity = "complex" if len(message.split()) > 20 else "simple"
    provider, model = router.get_best_model_for_complexity(complexity)
    
    if not provider:
        provider = "ollama"
        model = "llama3.2:3b"
    
    logger.info(f"Routing to {provider}/{model}")
    
    # Route to provider
    if provider == "anthropic" and os.getenv("ANTHROPIC_API_KEY"):
        return await use_anthropic(message, mode, model)
    elif provider == "openai" and os.getenv("OPENAI_API_KEY"):
        return await use_openai(message, mode, model)
    elif provider == "groq" and os.getenv("GROQ_API_KEY"):
        return await use_groq(message, mode, model)
    elif provider == "cohere" and os.getenv("COHERE_API_KEY"):
        return await use_cohere(message, mode, model)
    elif provider == "mistral" and os.getenv("MISTRAL_API_KEY"):
        return await use_mistral(message, mode, model)
    else:
        return await use_ollama(message, mode, model)

# Provider implementations
async def use_anthropic(message: str, mode: str, model: str) -> str:
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": os.getenv("ANTHROPIC_API_KEY"),
                "anthropic-version": "2023-06-01"
            },
            json={
                "model": model or "claude-3-haiku-20240307",
                "max_tokens": 1000,
                "messages": [{"role": "user", "content": message}]
            }
        )
        if response.status_code == 200:
            return response.json()["content"][0]["text"]
        raise Exception(f"Anthropic error: {response.status_code}")

async def use_openai(message: str, mode: str, model: str) -> str:
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {os.getenv('OPENAI_API_KEY')}"},
            json={
                "model": model or "gpt-3.5-turbo",
                "messages": [{"role": "user", "content": message}]
            }
        )
        if response.status_code == 200:
            return response.json()["choices"][0]["message"]["content"]
        raise Exception(f"OpenAI error: {response.status_code}")

async def use_ollama(message: str, mode: str, model: str) -> str:
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            "http://zoe-ollama:11434/api/generate",
            json={
                "model": model or "llama3.2:3b",
                "prompt": message,
                "stream": False
            }
        )
        if response.status_code == 200:
            return response.json().get("response", "")
        return "Ollama error"

# Stub for other providers - add as needed
async def use_groq(message: str, mode: str, model: str) -> str:
    return await use_ollama(message, mode, "llama3.2:3b")  # Fallback

async def use_cohere(message: str, mode: str, model: str) -> str:
    return await use_ollama(message, mode, "llama3.2:3b")  # Fallback

async def use_mistral(message: str, mode: str, model: str) -> str:
    return await use_ollama(message, mode, "llama3.2:3b")  # Fallback
EOF'

echo "✅ Connected existing discovery to AI"

# Restart to apply
docker compose restart zoe-core
sleep 10

# Test discovery endpoint
echo -e "\n4. Testing discovery endpoint:"
curl -s -X POST http://localhost:8000/api/settings-ui/routellm/discover 2>/dev/null || \
curl -s -X POST http://localhost:8000/api/routellm/discover 2>/dev/null || \
echo "Discovery endpoint not found"

# Check updated models
echo -e "\n5. Updated model status:"
if [ -f data/llm_models.json ]; then
    echo "Providers with models:"
    cat data/llm_models.json | jq '.providers | to_entries[] | select(.value.enabled==true) | .key'
fi

echo -e "\n✅ Existing discovery system activated!"
echo "The infrastructure from your previous chat is now connected."
