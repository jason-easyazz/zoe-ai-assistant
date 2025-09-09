#!/bin/bash
# CHECK_AND_FIX_ROUTING.sh
# Fix the routing to actually use API keys and RouteLLM

echo "🔍 CHECKING ROUTING & API KEYS"
echo "==============================="
echo ""

# Check what's available
echo "1. API Keys loaded in container:"
docker exec zoe-core printenv | grep -E "OPENAI|ANTHROPIC" | sed 's/=.*/=***/'

echo -e "\n2. Models configured:"
cat data/llm_models.json | jq '.providers | to_entries[] | select(.value.enabled==true) | {provider: .key, models: .value.models}'

echo -e "\n3. Current ai_client.py is hardcoded to Ollama only - FIXING..."

# Create proper AI client that uses RouteLLM
cat > services/zoe-core/ai_client.py << 'PROPER_AI'
"""AI Client with RouteLLM and API Integration"""
import httpx
import os
import logging
from route_llm import ZoeRouteLLM

logger = logging.getLogger(__name__)

# Initialize router
router = ZoeRouteLLM()

async def get_ai_response(message: str, context: dict = None) -> str:
    """Get AI response using RouteLLM routing"""
    
    context = context or {}
    mode = context.get("mode", "user")
    
    # Get routing decision
    routing = router.classify_query(message, context)
    provider = routing.get("provider", "ollama")
    model = routing.get("model", "llama3.2:3b")
    temperature = routing.get("temperature", 0.7)
    
    logger.info(f"Routing to {provider}/{model} (complexity: {routing.get('complexity')})")
    
    # Build prompt based on mode
    if mode == "developer":
        system_prompt = "You are Zack, a highly technical AI developer. Provide detailed technical solutions, complete code, and system designs."
    else:
        system_prompt = "You are Zoe, a warm and friendly AI assistant."
    
    full_prompt = f"{system_prompt}\n\nUser: {message}\nAssistant:"
    
    try:
        # Route to appropriate provider
        if provider == "anthropic" and os.getenv("ANTHROPIC_API_KEY"):
            return await use_anthropic(full_prompt, temperature)
        elif provider == "openai" and os.getenv("OPENAI_API_KEY"):
            return await use_openai(full_prompt, temperature)
        else:
            return await use_ollama(full_prompt, temperature, model)
    except Exception as e:
        logger.error(f"Primary provider failed: {e}, falling back to Ollama")
        return await use_ollama(full_prompt, temperature, model)

async def use_anthropic(prompt: str, temperature: float) -> str:
    """Use Anthropic Claude"""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01"
            },
            json={
                "model": "claude-3-haiku-20240307",
                "max_tokens": 1000,
                "temperature": temperature,
                "messages": [{"role": "user", "content": prompt}]
            }
        )
        
        if response.status_code == 200:
            data = response.json()
            return data["content"][0]["text"]
        else:
            raise Exception(f"Anthropic error: {response.status_code}")

async def use_openai(prompt: str, temperature: float) -> str:
    """Use OpenAI GPT"""
    api_key = os.getenv("OPENAI_API_KEY")
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}"
            },
            json={
                "model": "gpt-3.5-turbo",
                "temperature": temperature,
                "messages": [
                    {"role": "system", "content": prompt.split("\n")[0]},
                    {"role": "user", "content": prompt.split("User: ")[1].split("\n")[0]}
                ]
            }
        )
        
        if response.status_code == 200:
            data = response.json()
            return data["choices"][0]["message"]["content"]
        else:
            raise Exception(f"OpenAI error: {response.status_code}")

async def use_ollama(prompt: str, temperature: float, model: str) -> str:
    """Use local Ollama"""
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            "http://zoe-ollama:11434/api/generate",
            json={
                "model": model,
                "prompt": prompt,
                "temperature": temperature,
                "stream": False
            }
        )
        
        if response.status_code == 200:
            data = response.json()
            return data.get("response", "No response")
        else:
            raise Exception(f"Ollama error: {response.status_code}")
PROPER_AI

echo "✅ Created AI client with RouteLLM integration"

# Restart
docker compose restart zoe-core
sleep 10

# Test routing
echo -e "\n🧪 Testing model routing:"

echo -e "\n1. Simple query (should use local):"
curl -s -X POST http://localhost:8000/api/developer/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Hi"}' | jq -r '.response' | head -50

echo -e "\n2. Complex query (should use API if available):"
curl -s -X POST http://localhost:8000/api/developer/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Write a complete FastAPI router for user authentication with JWT tokens"}' | jq -r '.response' | head -200

# Check logs to see routing decisions
echo -e "\n3. Checking routing decisions in logs:"
docker logs zoe-core --tail 20 | grep -i "routing\|model" || echo "No routing messages"
