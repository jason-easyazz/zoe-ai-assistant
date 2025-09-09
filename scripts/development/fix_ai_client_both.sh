#!/bin/bash
# FIX_AI_CLIENT_BOTH.sh
# Fix AI client for both Zoe and Zack

echo "🔧 FIXING AI CLIENT FOR BOTH PERSONALITIES"
echo "=========================================="
echo ""

cd /home/pi/zoe

# Create working ai_client with both personalities
cat > services/zoe-core/ai_client.py << 'WORKING_AI'
"""AI Client with Zoe and Zack personalities"""
import httpx
import logging
import json
from typing import Optional, Dict

logger = logging.getLogger(__name__)

async def get_ai_response(message: str, context: Optional[Dict] = None) -> str:
    """Get AI response with appropriate personality"""
    
    context = context or {}
    mode = context.get("mode", "user")
    
    # Configure based on mode
    if mode == "developer":
        # Zack - Technical assistant
        system_msg = """You are Zack, a highly technical AI assistant and system administrator.
You have expertise in Docker, Linux, Python, debugging, and DevOps.
Be direct, technical, and precise. Provide code examples and specific solutions.
You can see system information and help with technical issues."""
        temperature = 0.3
        model = "llama3.2:3b"  # Use the more capable model for technical
    else:
        # Zoe - Friendly assistant
        system_msg = """You are Zoe, a warm and friendly AI assistant.
Be conversational, caring, and helpful. Use occasional emojis to express warmth.
Help users with their daily tasks and questions in a friendly manner."""
        temperature = 0.7
        model = "llama3.2:3b"
    
    # Build the prompt
    full_prompt = f"{system_msg}\n\nUser: {message}\n\nAssistant:"
    
    try:
        # Call Ollama
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                "http://zoe-ollama:11434/api/generate",
                json={
                    "model": model,
                    "prompt": full_prompt,
                    "temperature": temperature,
                    "stream": False,
                    "options": {
                        "num_predict": 500,
                        "top_k": 40,
                        "top_p": 0.9
                    }
                }
            )
            
            if response.status_code == 200:
                data = response.json()
                result = data.get("response", "")
                if result:
                    return result
                else:
                    logger.error("Empty response from Ollama")
            else:
                logger.error(f"Ollama error: {response.status_code}")
                
    except httpx.TimeoutError:
        logger.error("Ollama timeout")
    except Exception as e:
        logger.error(f"AI generation error: {str(e)}")
    
    # Fallback responses
    if mode == "developer":
        return "I'm Zack. I'm experiencing technical difficulties accessing the AI model. Check: 1) Ollama container status, 2) Model availability (llama3.2:3b), 3) Container logs for errors."
    else:
        return "I'm having a moment! Let me try again. If this persists, please check the system status."

# Backward compatibility
async def generate_response(message: str, context: Optional[Dict] = None) -> str:
    return await get_ai_response(message, context)

# Direct function for testing
def test_ai():
    import asyncio
    result = asyncio.run(get_ai_response("Hello", {"mode": "developer"}))
    print(f"Test result: {result[:100]}")
WORKING_AI

echo "✅ Created simplified ai_client.py"

# Restart
docker compose restart zoe-core
sleep 10

# Test both
echo -e "\n🧪 Testing both personalities:"
echo "================================"

echo -e "\n1. Zoe (Friendly):"
curl -s -X POST http://localhost:8000/api/chat/ \
  -H "Content-Type: application/json" \
  -d '{"message": "Tell me a joke about computers"}' | jq -r '.response'

echo -e "\n2. Zack (Technical):"
curl -s -X POST http://localhost:8000/api/developer/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Write a Python function to monitor Docker containers"}' | jq -r '.response'

echo -e "\n3. Testing difference in responses:"
question="Explain what Docker is"

echo -e "\nZoe's explanation:"
curl -s -X POST http://localhost:8000/api/chat/ \
  -H "Content-Type: application/json" \
  -d "{\"message\": \"$question\"}" | jq -r '.response' | head -100

echo -e "\nZack's explanation:"
curl -s -X POST http://localhost:8000/api/developer/chat \
  -H "Content-Type: application/json" \
  -d "{\"message\": \"$question\"}" | jq -r '.response' | head -100
