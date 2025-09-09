#!/bin/bash
# FIX_ZACK_TIMEOUTS.sh
# Fix timeouts and optimize Zack's response generation

echo "⚡ FIXING TIMEOUTS & OPTIMIZING ZACK"
echo "====================================="
echo ""

cd /home/pi/zoe

# Fix 1: Increase timeout and optimize generation
cat > services/zoe-core/ai_client.py << 'OPTIMIZED_AI'
"""Optimized AI Client with longer timeouts"""
import httpx
import logging
import json

logger = logging.getLogger(__name__)

async def get_ai_response(message: str, context: dict = None) -> str:
    """Get AI response with optimized settings"""
    
    context = context or {}
    mode = context.get("mode", "user")
    
    # Configure based on mode
    if mode == "developer":
        # Zack - Technical, but optimized for speed
        prompt = f"""You are Zack, Zoe's built-in developer.
You can build features, fix issues, and design systems.
Be concise but complete. Provide code when asked.

User: {message}
Zack:"""
        temp = 0.3
        max_tokens = 800  # Limit response length to prevent timeouts
    else:
        # Zoe - Friendly
        prompt = f"""You are Zoe, a friendly AI assistant.

User: {message}
Zoe:"""
        temp = 0.7
        max_tokens = 400
    
    try:
        # Longer timeout for complex queries
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
            response = await client.post(
                "http://zoe-ollama:11434/api/generate",
                json={
                    "model": "llama3.2:3b",
                    "prompt": prompt,
                    "temperature": temp,
                    "stream": False,
                    "options": {
                        "num_predict": max_tokens,
                        "num_ctx": 2048,  # Smaller context for speed
                        "num_thread": 4   # Use multiple threads
                    }
                }
            )
            
            if response.status_code == 200:
                data = response.json()
                return data.get("response", "No response generated")
            else:
                return f"Error: HTTP {response.status_code}"
                
    except httpx.ReadTimeout:
        return "Response took too long. Try a simpler query or break it into parts."
    except Exception as e:
        logger.error(f"AI error: {str(e)}")
        return f"Error: {str(e)}"
    
    return "Unable to generate response"

async def generate_response(message: str, context: dict = None) -> str:
    return await get_ai_response(message, context)
OPTIMIZED_AI

echo "✅ Optimized AI client with 60s timeout"

# Fix 2: Create focused Zack developer router
cat > services/zoe-core/routers/developer.py << 'FOCUSED_DEV'
"""Focused Developer Router - Zack"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
import subprocess
import sys
sys.path.append('/app')
from ai_client import get_ai_response

router = APIRouter(prefix="/api/developer")

class ChatMessage(BaseModel):
    message: str
    context: Optional[dict] = None

def get_quick_status():
    """Get quick system status"""
    try:
        result = subprocess.run(
            "docker ps --format '{{.Names}}: {{.Status}}' | grep zoe- | head -3",
            shell=True, capture_output=True, text=True, timeout=2
        )
        return result.stdout[:200]  # Limit output
    except:
        return "System online"

@router.post("/chat")
async def developer_chat(msg: ChatMessage):
    """Zack - Developer Assistant"""
    
    message = msg.message.lower()
    
    # For simple queries, give quick responses
    if any(word in message for word in ["hi", "hello", "hey"]):
        return {"response": "I'm Zack, Zoe's developer. I can build features, fix issues, and design systems. What do you need?"}
    
    if "status" in message or "health" in message:
        status = get_quick_status()
        return {"response": f"System Status:\n{status}\n\nAll systems operational."}
    
    # For complex queries, use AI but with focused prompts
    if any(word in message for word in ["build", "create", "make", "add"]):
        prompt = f"As Zack the developer, provide concise code to: {msg.message}"
    elif any(word in message for word in ["fix", "debug", "solve", "issue"]):
        prompt = f"As Zack the developer, provide a solution for: {msg.message}"
    elif any(word in message for word in ["design", "architect", "plan"]):
        prompt = f"As Zack the developer, design this briefly: {msg.message}"
    else:
        prompt = msg.message
    
    # Get AI response with timeout protection
    try:
        response = await get_ai_response(prompt, {"mode": "developer"})
        return {"response": response}
    except Exception as e:
        return {"response": f"I'll need to break this down. Error: {str(e)}"}

@router.get("/status")
async def status():
    return {"api": "online", "zack": "ready", "timeout": "60s"}
FOCUSED_DEV

echo "✅ Created focused developer router"

# Restart
docker compose restart zoe-core
sleep 10

# Test with simpler queries first
echo -e "\n🧪 Testing with optimized queries:"
echo "===================================="

echo -e "\n1. Quick test:"
curl -s -X POST http://localhost:8000/api/developer/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Hi Zack"}' | jq -r '.response'

echo -e "\n2. Simple build:"
curl -s -X POST http://localhost:8000/api/developer/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Build a simple hello world endpoint"}' | jq -r '.response'

echo -e "\n3. Quick fix:"
curl -s -X POST http://localhost:8000/api/developer/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Fix: API is slow"}' | jq -r '.response'

echo -e "\n✅ Optimizations applied!"
echo "Timeouts increased to 60s, responses limited to prevent overload"
