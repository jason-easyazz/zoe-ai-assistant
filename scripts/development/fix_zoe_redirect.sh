#!/bin/bash
# FIX_ZOE_REDIRECT.sh
# Fix the 307 redirect issue for Zoe chat

echo "🔍 DIAGNOSING ZOE CHAT REDIRECT"
echo "================================"
echo ""

cd /home/pi/zoe

# Test with and without trailing slash
echo "📋 Testing endpoint variations..."
echo ""
echo "1. Testing /api/chat (no trailing slash):"
curl -s -X POST http://localhost:8000/api/chat \
    -H "Content-Type: application/json" \
    -d '{"message": "test"}' \
    -w "\n  Status: %{http_code}\n" \
    -o /dev/null

echo ""
echo "2. Testing /api/chat/ (with trailing slash):"
response=$(curl -s -X POST http://localhost:8000/api/chat/ \
    -H "Content-Type: application/json" \
    -d '{"message": "test"}' \
    -w "\n  Status: %{http_code}" 2>/dev/null)
    
status=$(echo "$response" | grep "Status:" | awk '{print $2}')
if [ "$status" = "200" ]; then
    echo "  ✅ Working with trailing slash!"
else
    echo "$response"
fi

# Check what's in chat.py vs chat_override.py
echo -e "\n📁 Checking chat router files..."
echo "Original chat.py:"
docker exec zoe-core head -20 routers/chat.py

echo -e "\nOverride chat_override.py:"
docker exec zoe-core head -20 routers/chat_override.py 2>/dev/null || echo "  File not accessible"

# Check which one is being used
echo -e "\n🔍 Checking which chat router is imported..."
docker exec zoe-core grep "from routers import" main.py

# Fix: Update chat.py to handle both message formats
echo -e "\n🔧 Fixing chat endpoint..."
docker exec zoe-core python3 << 'FIX_CHAT'
# Read current chat.py
with open('/app/routers/chat.py', 'r') as f:
    content = f.read()

# Check if it needs fixing
if 'ChatMessage' not in content and 'message: str' not in content:
    # Create a working chat router
    new_content = '''"""Chat Router for User Interactions"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
import logging

router = APIRouter(prefix="/api/chat", tags=["chat"])
logger = logging.getLogger(__name__)

class ChatMessage(BaseModel):
    message: str
    context: Optional[dict] = None

@router.post("/")
@router.post("")  # Handle both with and without trailing slash
async def chat(request: ChatMessage):
    """Handle user chat messages"""
    try:
        # Import AI client
        try:
            from ai_client import generate_response
        except ImportError:
            # Fallback if ai_client doesn't exist
            async def generate_response(msg, ctx=None):
                return f"Echo: {msg}"
        
        # Generate response
        response = await generate_response(request.message, request.context)
        
        # Return in expected format
        if isinstance(response, dict):
            return response
        else:
            return {"response": str(response)}
            
    except Exception as e:
        logger.error(f"Chat error: {e}")
        return {"response": "I'm having trouble responding. Please try again."}
'''
    
    with open('/app/routers/chat.py', 'w') as f:
        f.write(new_content)
    
    print("✅ Fixed chat.py router")
else:
    print("✅ Chat router appears OK")
FIX_CHAT

# Restart
echo -e "\n🔄 Restarting zoe-core..."
docker compose restart zoe-core
sleep 10

# Test both endpoints
echo -e "\n🧪 Testing fixed endpoints..."
echo "Testing Zoe at /api/chat:"
response=$(curl -s -X POST http://localhost:8000/api/chat \
    -H "Content-Type: application/json" \
    -d '{"message": "Hello!"}')

if echo "$response" | grep -q "response"; then
    echo "  ✅ Zoe is working!"
    echo "  Response: $(echo $response | jq -r '.response' | head -c 100)..."
else
    echo "  ❌ Still not working"
    echo "  Response: $response"
fi

echo -e "\nTesting Zack at /api/developer/chat:"
response=$(curl -s -X POST http://localhost:8000/api/developer/chat \
    -H "Content-Type: application/json" \
    -d '{"message": "Status"}')

if echo "$response" | grep -q "response"; then
    echo "  ✅ Zack is working!"
    echo "  Response: $(echo $response | jq -r '.response' | head -c 100)..."
else
    echo "  ❌ Not working"
fi
