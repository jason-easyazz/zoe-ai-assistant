#!/bin/bash
# RESTORE_ZACK_AI.sh
# Give Zack back his full AI brain

echo "🧠 RESTORING ZACK'S AI INTELLIGENCE"
echo "===================================="
echo ""

cd /home/pi/zoe

# Backup the current broken version
cp services/zoe-core/routers/developer.py services/zoe-core/routers/developer_template.py

# Create Zack with full AI
cat > services/zoe-core/routers/developer.py << 'ZACK_AI'
"""Developer Router - Full AI Zack"""
from fastapi import APIRouter
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

def get_system_info():
    """Get real system information"""
    info = []
    try:
        # Docker containers
        result = subprocess.run("docker ps --format 'table {{.Names}}\t{{.Status}}'", 
                              shell=True, capture_output=True, text=True, timeout=5)
        info.append(f"Containers:\n{result.stdout}")
        
        # Memory
        result = subprocess.run("free -h | grep Mem", 
                              shell=True, capture_output=True, text=True, timeout=5)
        info.append(f"Memory: {result.stdout}")
        
        # Disk
        result = subprocess.run("df -h / | tail -1", 
                              shell=True, capture_output=True, text=True, timeout=5)
        info.append(f"Disk: {result.stdout}")
    except:
        pass
    return "\n".join(info)

@router.post("/chat")
async def developer_chat(msg: ChatMessage):
    """Zack - Technical AI with system awareness"""
    
    # Get current system state
    system_info = get_system_info()
    
    # Build Zack's technical prompt with system context
    zack_prompt = f"""You are Zack, a highly skilled technical AI assistant and system administrator.
You have deep expertise in Docker, Linux, Python, debugging, and DevOps.
You can write complete scripts, debug issues, and provide detailed technical solutions.

Current System Information:
{system_info}

User Query: {msg.message}

Respond with technical precision. If asked for scripts, provide complete executable code.
If asked to analyze, be thorough. If asked to debug, provide specific solutions."""
    
    # Get AI response with Zack's personality
    response = await get_ai_response(zack_prompt, context={"mode": "developer", "temperature": 0.3})
    
    return {"response": response}

@router.get("/status")
async def developer_status():
    """System status endpoint"""
    return {
        "api": "online",
        "auto_execute": "enabled",
        "ai": "active"
    }
ZACK_AI

echo "✅ Created Zack with full AI"

# Restart
docker compose restart zoe-core
sleep 10

echo -e "\n🧪 Testing Zack's restored intelligence:"
echo "========================================="

# Test 1
echo -e "\n1. Testing Analysis:"
curl -s -X POST http://localhost:8000/api/developer/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Analyze memory usage patterns and suggest optimizations"}' | \
  jq -r '.response' | head -150

# Test 2  
echo -e "\n2. Testing Script Generation:"
curl -s -X POST http://localhost:8000/api/developer/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Write a bash script to monitor and restart failed containers"}' | \
  jq -r '.response' | head -150

echo -e "\n✅ Zack should now have full AI intelligence!"
