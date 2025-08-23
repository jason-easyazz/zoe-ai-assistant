#!/bin/bash
# EMERGENCY FIX - Get it working again

echo "🚨 EMERGENCY FIX"
echo "==============="

cd /home/pi/zoe

# FIX 1: Restore flexible backend that answers ANY question
cat > services/zoe-core/routers/developer.py << 'EOF'
"""Basic working developer router"""
from fastapi import APIRouter
from pydantic import BaseModel
import subprocess
import sys
sys.path.append('/app')
from ai_client import ai_client

router = APIRouter(prefix="/api/developer")

class ChatMessage(BaseModel):
    message: str

@router.post("/chat")
async def developer_chat(msg: ChatMessage):
    # Just pass to AI and let it handle everything
    try:
        response = await ai_client.generate_response(
            f"You are a system admin assistant. User asked: {msg.message}. Provide a helpful response.",
            {"mode": "assistant"}
        )
        return {"response": response["response"]}
    except Exception as e:
        return {"response": f"Error: {str(e)}"}

@router.get("/status")
async def status():
    return {"status": "online"}
EOF

# FIX 2: Restart backend
docker restart zoe-core

echo "✅ Backend fixed - will answer ANY question now"
echo ""
echo "The system should work again. Just refresh and try asking different questions."

