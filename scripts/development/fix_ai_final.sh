#!/bin/bash
# FIX_AI_FINAL.sh
# Final fix for AI client with proper error handling

echo "🔧 FINAL AI CLIENT FIX"
echo "======================"
echo ""

cd /home/pi/zoe

# Create properly working AI client
cat > services/zoe-core/ai_client.py << 'FINAL_AI'
"""AI Client - Simple and Working"""
import httpx
import logging
import json

logger = logging.getLogger(__name__)

async def get_ai_response(message: str, context: dict = None) -> str:
    """Get AI response with appropriate personality"""
    
    context = context or {}
    mode = context.get("mode", "user")
    
    # Set personality
    if mode == "developer":
        # Zack
        prompt = f"""You are Zack, a technical AI assistant.
Be direct and technical in your responses.

User: {message}
Zack:"""
        temp = 0.3
    else:
        # Zoe
        prompt = f"""You are Zoe, a friendly AI assistant.
Be warm and helpful in your responses.

User: {message}
Zoe:"""
        temp = 0.7
    
    try:
        # Call Ollama
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
            response = await client.post(
                "http://zoe-ollama:11434/api/generate",
                json={
                    "model": "llama3.2:3b",
                    "prompt": prompt,
                    "temperature": temp,
                    "stream": False
                }
            )
            
            if response.status_code == 200:
                data = response.json()
                return data.get("response", "Error: No response from model")
                
    except httpx.ReadTimeout:
        logger.error("Ollama timeout")
        return "Request timed out. Please try again."
    except Exception as e:
        logger.error(f"AI error: {str(e)}")
        return f"Error: {str(e)}"
    
    return "Unable to generate response"

# Backward compatibility
async def generate_response(message: str, context: dict = None) -> str:
    return await get_ai_response(message, context)
FINAL_AI

echo "✅ Created final ai_client.py"

# Fix the developer router to handle responses properly
cat > services/zoe-core/routers/developer.py << 'DEV_ROUTER'
"""Developer Router - Zack"""
from fastapi import APIRouter
from pydantic import BaseModel
import sys
sys.path.append('/app')
from ai_client import get_ai_response
import subprocess

router = APIRouter(prefix="/api/developer")

class ChatMessage(BaseModel):
    message: str

def get_system_status():
    """Get basic system info"""
    try:
        result = subprocess.run(
            "docker ps --format 'table {{.Names}}\t{{.Status}}' | grep zoe-",
            shell=True, capture_output=True, text=True, timeout=5
        )
        return result.stdout
    except:
        return "Unable to get system status"

@router.post("/chat")
async def developer_chat(msg: ChatMessage):
    """Zack developer chat"""
    
    # Add system context
    system_info = get_system_status()
    enhanced_message = f"""System Status:
{system_info}

User Query: {msg.message}"""
    
    response = await get_ai_response(enhanced_message, {"mode": "developer"})
    return {"response": response}

@router.get("/status")
async def developer_status():
    return {"api": "online", "auto_execute": "enabled"}
DEV_ROUTER

echo "✅ Fixed developer router"

# Restart
docker compose restart zoe-core
sleep 10

# Final test
echo -e "\n🧪 FINAL TEST:"
echo "=============="

echo -e "\n1. Zoe:"
curl -s -X POST http://localhost:8000/api/chat/ \
  -H "Content-Type: application/json" \
  -d '{"message": "Hello, who are you?"}' | jq -r '.response' | head -50

echo -e "\n2. Zack:"
curl -s -X POST http://localhost:8000/api/developer/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "What is Docker?"}' | jq -r '.response' | head -50
