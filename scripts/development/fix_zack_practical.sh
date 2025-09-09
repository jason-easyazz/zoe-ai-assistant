#!/bin/bash
# FIX_ZACK_PRACTICAL.sh
# Practical fix for Zack within local model limits

echo "🔧 PRACTICAL ZACK FIX"
echo "====================="
echo ""

cd /home/pi/zoe

# Remove the pattern matching that's intercepting everything
cat > services/zoe-core/routers/developer.py << 'PRACTICAL_DEV'
"""Practical Developer Router"""
from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional
import sys
sys.path.append('/app')
from ai_client import get_ai_response

router = APIRouter(prefix="/api/developer")

class ChatMessage(BaseModel):
    message: str

@router.post("/chat")
async def developer_chat(msg: ChatMessage):
    """Zack - Developer Assistant"""
    
    # Direct pass-through to AI with developer context
    response = await get_ai_response(msg.message, {"mode": "developer"})
    return {"response": response}

@router.get("/status")
async def status():
    return {"api": "online"}
PRACTICAL_DEV

echo "✅ Removed problematic pattern matching"

# Test
docker compose restart zoe-core
sleep 10

echo -e "\n🧪 Testing direct AI responses:"
echo "Testing build request:"
curl -s -X POST http://localhost:8000/api/developer/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Write a Python function to add two numbers"}' | jq -r '.response'
