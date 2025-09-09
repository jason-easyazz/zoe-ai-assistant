#!/bin/bash
# FIX_API_INTEGRATION.sh
# Fix the actual API calls to OpenAI and Anthropic

echo "🔧 FIXING API INTEGRATION"
echo "========================="
echo ""

# Test if API keys are actually valid
echo "1. Testing API keys directly:"

# Test OpenAI
echo -n "OpenAI: "
docker exec zoe-core python3 -c "
import os
import httpx
api_key = os.getenv('OPENAI_API_KEY')
if api_key:
    response = httpx.post(
        'https://api.openai.com/v1/models',
        headers={'Authorization': f'Bearer {api_key}'},
        timeout=10
    )
    if response.status_code == 200:
        print('✅ Valid')
    else:
        print(f'❌ Invalid - Status {response.status_code}')
else:
    print('❌ No key')
" 2>/dev/null || echo "❌ Error"

# Test Anthropic
echo -n "Anthropic: "
docker exec zoe-core python3 -c "
import os
import httpx
api_key = os.getenv('ANTHROPIC_API_KEY')
if api_key:
    # Anthropic doesn't have a simple test endpoint, so we'll just verify key format
    if api_key.startswith('sk-'):
        print('✅ Formatted correctly')
    else:
        print('❌ Invalid format')
else:
    print('❌ No key')
" 2>/dev/null || echo "❌ Error"

# Fix the AI client to properly handle API calls
echo -e "\n2. Fixing AI client with proper error handling:"
docker exec zoe-core bash -c 'cat > /app/ai_client_fixed.py << "EOF"
"""Fixed AI Client with proper API integration"""
import httpx
import os
import logging
import json

logger = logging.getLogger(__name__)

async def get_ai_response(message: str, context: dict = None) -> str:
    """Get AI response with proper API handling"""
    
    context = context or {}
    mode = context.get("mode", "user")
    
    # Check complexity
    is_complex = len(message.split()) > 15
    
    # For complex queries, try APIs first
    if is_complex:
        # Try OpenAI
        if os.getenv("OPENAI_API_KEY"):
            try:
                logger.info("Trying OpenAI API...")
                response = await call_openai(message, mode)
                if response:
                    return response
            except Exception as e:
                logger.error(f"OpenAI failed: {e}")
        
        # Try Anthropic
        if os.getenv("ANTHROPIC_API_KEY"):
            try:
                logger.info("Trying Anthropic API...")
                response = await call_anthropic(message, mode)
                if response:
                    return response
            except Exception as e:
                logger.error(f"Anthropic failed: {e}")
    
    # Fallback to Ollama
    logger.info("Using local Ollama")
    return await call_ollama(message, mode)

async def call_openai(message: str, mode: str) -> str:
    """Call OpenAI API"""
    api_key = os.getenv("OPENAI_API_KEY")
    
    if mode == "developer":
        system = "You are Zack, a technical AI developer. Provide detailed technical solutions and code."
        model = "gpt-4-turbo-preview"  # Use GPT-4 for complex dev tasks
    else:
        system = "You are Zoe, a friendly AI assistant."
        model = "gpt-3.5-turbo"
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            },
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": message}
                ],
                "max_tokens": 1000,
                "temperature": 0.3 if mode == "developer" else 0.7
            }
        )
        
        if response.status_code == 200:
            data = response.json()
            return data["choices"][0]["message"]["content"]
        else:
            raise Exception(f"OpenAI API error: {response.status_code} - {response.text}")

async def call_anthropic(message: str, mode: str) -> str:
    """Call Anthropic API"""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    
    if mode == "developer":
        system = "You are Zack, a technical AI developer. Provide detailed technical solutions and code."
    else:
        system = "You are Zoe, a friendly AI assistant."
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            },
            json={
                "model": "claude-3-haiku-20240307",
                "max_tokens": 1000,
                "system": system,
                "messages": [
                    {"role": "user", "content": message}
                ]
            }
        )
        
        if response.status_code == 200:
            data = response.json()
            return data["content"][0]["text"]
        else:
            raise Exception(f"Anthropic API error: {response.status_code}")

async def call_ollama(message: str, mode: str) -> str:
    """Call local Ollama"""
    if mode == "developer":
        prompt = f"You are Zack, a technical developer. User: {message}\nZack:"
    else:
        prompt = f"You are Zoe, a friendly assistant. User: {message}\nZoe:"
    
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            "http://zoe-ollama:11434/api/generate",
            json={
                "model": "llama3.2:3b",
                "prompt": prompt,
                "stream": False
            }
        )
        
        if response.status_code == 200:
            data = response.json()
            return data.get("response", "No response from Ollama")
        else:
            return "Local model unavailable"
EOF
mv /app/ai_client_fixed.py /app/ai_client.py'

echo "✅ Fixed AI client"

# Restart
docker compose restart zoe-core
sleep 10

# Test with a medium complexity query
echo -e "\n3. Testing with medium complexity:"
curl -s -X POST http://localhost:8000/api/developer/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Write a Python function that sorts a list of dictionaries by a specific key"}' | \
  jq -r '.response' || echo "Failed to get response"

# Check logs
echo -e "\n4. Checking logs for API usage:"
docker logs zoe-core --tail 30 | grep -E "OpenAI|Anthropic|Ollama|API" || echo "No API logs"
