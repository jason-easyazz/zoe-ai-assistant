#!/bin/bash
# FIX_ZOE_CONNECTION.sh
# Connect Zoe's chat endpoint to the working AI

echo "🔧 FIXING ZOE CHAT CONNECTION"
echo "============================="
echo ""

cd /home/pi/zoe

# Check current chat.py
echo "📋 Current chat router:"
docker exec zoe-core cat routers/chat.py

# Fix the chat router to properly call AI
cat > services/zoe-core/routers/chat_fixed.py << 'FIXED_CHAT'
"""Fixed Chat Router for Zoe"""
from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional
import sys
sys.path.append('/app')
from ai_client import get_ai_response

router = APIRouter(tags=["chat"])

class ChatMessage(BaseModel):
    message: str
    context: Optional[dict] = None

@router.post("/api/chat/")
@router.post("/api/chat")
async def chat(msg: ChatMessage):
    """Handle user chat messages (Zoe)"""
    try:
        # This works as proven by direct test
        response = await get_ai_response(msg.message, context={"mode": "user"})
        return {"response": response}
    except Exception as e:
        # Log the actual error
        import logging
        logging.error(f"Chat error: {str(e)}")
        # Return the error for debugging
        return {"response": f"Error: {str(e)}"}
FIXED_CHAT

# Backup current and replace
docker exec zoe-core cp routers/chat.py routers/chat_backup.py
docker exec zoe-core cp routers/chat_fixed.py routers/chat.py

# Restart
docker compose restart zoe-core
sleep 10

# Test both
echo -e "\n🧪 Testing fixed Zoe:"
curl -s -X POST http://localhost:8000/api/chat/ \
  -H "Content-Type: application/json" \
  -d '{"message": "Hello! Tell me a joke"}' | \
  jq -r '.response'
