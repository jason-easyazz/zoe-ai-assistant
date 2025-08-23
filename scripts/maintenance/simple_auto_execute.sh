#!/bin/bash
# Simple fix - make existing endpoint auto-execute

echo "🔧 SIMPLE FIX: Auto-Execute Commands"
echo "===================================="

cd /home/pi/zoe

# Update the existing developer.py to auto-execute
cat > services/zoe-core/routers/developer_fix.py << 'EOF'
"""Fixed developer router that auto-executes"""
from fastapi import APIRouter
from pydantic import BaseModel
import subprocess
import json

router = APIRouter(prefix="/api/developer")

class ChatMessage(BaseModel):
    message: str

@router.post("/chat")
async def developer_chat(msg: ChatMessage):
    """Auto-executing chat"""
    message = msg.message.lower()
    
    # Auto-execute for common commands
    if 'docker' in message or 'container' in message:
        result = subprocess.run("docker ps", shell=True, capture_output=True, text=True, cwd="/app")
        return {"response": f"Docker containers:\n```\n{result.stdout}\n```"}
    
    elif 'health' in message or 'status' in message:
        result = subprocess.run("docker ps --format 'table {{.Names}}\t{{.Status}}'", shell=True, capture_output=True, text=True, cwd="/app")
        return {"response": f"System status:\n```\n{result.stdout}\n```"}
    
    elif 'memory' in message or 'cpu' in message:
        result = subprocess.run("free -h", shell=True, capture_output=True, text=True, cwd="/app")
        return {"response": f"Memory usage:\n```\n{result.stdout}\n```"}
    
    elif 'disk' in message:
        result = subprocess.run("df -h /", shell=True, capture_output=True, text=True, cwd="/app")
        return {"response": f"Disk usage:\n```\n{result.stdout}\n```"}
    
    else:
        return {"response": "Ask me to check: docker containers, system health, memory, cpu, or disk"}

@router.get("/status")
async def status():
    return {"status": "online", "full_access": True}
EOF

# Replace the old developer.py
cp services/zoe-core/routers/developer_fix.py services/zoe-core/routers/developer.py

# Restart
docker restart zoe-core
sleep 8

# Test
echo "Testing..."
curl -X POST http://localhost:8000/api/developer/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "show docker containers"}' | jq -r '.response'

echo ""
echo "✅ Fixed! Refresh dashboard and try:"
echo '  "Show docker containers"'
echo '  "Check system health"'
