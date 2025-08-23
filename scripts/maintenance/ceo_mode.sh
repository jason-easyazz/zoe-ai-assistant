#!/bin/bash
# CEO Mode - Ultra concise responses

echo "👔 CEO MODE - SHORT & SWEET"
echo "=========================="

cd /home/pi/zoe

cat > services/zoe-core/routers/developer_ceo.py << 'EOF'
"""CEO Mode - Executive summaries only"""
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
    # Get data
    docker_status = subprocess.run("docker ps --format '{{.Names}}'", shell=True, capture_output=True, text=True, cwd="/app")
    memory = subprocess.run("free -h | grep Mem | awk '{print $3\"/\"$2}'", shell=True, capture_output=True, text=True, cwd="/app")
    disk = subprocess.run("df -h / | tail -1 | awk '{print $5}'", shell=True, capture_output=True, text=True, cwd="/app")
    
    container_count = len(docker_status.stdout.strip().split('\n'))
    
    prompt = f"""You are reporting to a CEO. BE EXTREMELY BRIEF.

User asked: {msg.message}

Data:
- Containers running: {container_count}/7
- Memory: {memory.stdout.strip()}
- Disk: {disk.stdout.strip()}

RULES:
- MAX 3-4 lines response
- Use icons: ✅ good, ⚠️ warning, ❌ problem
- Only highlight problems
- Skip explanations unless asked
- One line summary

Example good response:
✅ All systems operational
- 7/7 services running
- Resources: Memory 20%, Disk 19%

Example problem response:
⚠️ 2 services down
- zoe-core, zoe-ui offline
- Action: Restarting services...

BE BRIEF. CEO has 10 seconds to read this."""

    response = await ai_client.generate_response(prompt, {"mode": "assistant", "temperature": 0.1})
    return {"response": response["response"]}

@router.get("/status")
async def status():
    return {"status": "online"}
EOF

cp services/zoe-core/routers/developer_ceo.py services/zoe-core/routers/developer.py
docker restart zoe-core

echo "✅ CEO MODE ACTIVATED"
echo ""
echo "Responses will now be like:"
echo ""
echo "✅ All systems operational"
echo "• 7/7 services running"
echo "• Resources normal (20% usage)"
echo ""
echo "That's it! Short and sweet."
