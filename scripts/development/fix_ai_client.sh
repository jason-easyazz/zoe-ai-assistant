#!/bin/bash
# FIX_AI_CLIENT.sh
# Fix the AI client implementation

echo "🔧 FIXING AI CLIENT"
echo "==================="
echo ""

cd /home/pi/zoe

# Check current ai_client.py
echo "Current ai_client.py implementation:"
docker exec zoe-core head -30 ai_client.py

# Create a working ai_client
cat > services/zoe-core/ai_client.py << 'AI_CLIENT'
"""Working AI Client"""
import httpx
import os
import json
import logging

logger = logging.getLogger(__name__)

async def get_ai_response(message: str, context: dict = None) -> str:
    """Get AI response using available models"""
    
    context = context or {}
    mode = context.get("mode", "user")
    
    # Set personality based on mode
    if mode == "developer":
        system_prompt = "You are Zack, a technical AI assistant. Be precise, technical, and provide detailed solutions."
        temperature = 0.3
        model = "llama3.2:3b"
    else:
        system_prompt = "You are Zoe, a warm and friendly AI assistant. Be helpful, conversational, and caring."
        temperature = 0.7
        model = "llama3.2:3b"
    
    # Build full prompt
    full_prompt = f"{system_prompt}\n\nUser: {message}\nAssistant:"
    
    try:
        # Try Ollama first (always available)
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                "http://zoe-ollama:11434/api/generate",
                json={
                    "model": model,
                    "prompt": full_prompt,
                    "temperature": temperature,
                    "stream": False
                }
            )
            
            if response.status_code == 200:
                data = response.json()
                return data.get("response", "I'm having trouble responding.")
            else:
                logger.error(f"Ollama returned status {response.status_code}")
                
    except Exception as e:
        logger.error(f"Ollama error: {e}")
    
    # Fallback response
    if mode == "developer":
        return "Technical systems are temporarily unavailable. Please check container logs."
    else:
        return "I'm having trouble processing that request. Please try again."

# For backward compatibility
async def generate_response(message: str, context: dict = None) -> str:
    return await get_ai_response(message, context)
AI_CLIENT

echo "Created new ai_client.py"

# Restart container
docker compose restart zoe-core
sleep 10

# Test both personalities
echo -e "\n🧪 Testing AI responses:"
echo "Testing Zoe:"
curl -s -X POST http://localhost:8000/api/chat/ \
  -H "Content-Type: application/json" \
  -d '{"message": "Hello! How are you?"}' | jq -r '.response' | head -50

echo -e "\nTesting Zack:"
curl -s -X POST http://localhost:8000/api/developer/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Explain Docker networking"}' | jq -r '.response' | head -50
