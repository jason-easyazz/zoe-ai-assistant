#!/bin/bash
# Executive Mode - Professional brief with key details

echo "📊 EXECUTIVE MODE - BALANCED"
echo "============================"

cd /home/pi/zoe

cat > services/zoe-core/routers/developer_exec.py << 'EOF'
"""Executive Mode - Professional but concise"""
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
    # Gather key metrics
    docker = subprocess.run("docker ps --format '{{.Names}}:{{.Status}}'", shell=True, capture_output=True, text=True, cwd="/app")
    memory = subprocess.run("free -h | grep Mem", shell=True, capture_output=True, text=True, cwd="/app")
    disk = subprocess.run("df -h / | tail -1", shell=True, capture_output=True, text=True, cwd="/app")
    errors = subprocess.run("docker logs zoe-core --tail 10 2>&1 | grep -i error | wc -l", shell=True, capture_output=True, text=True, cwd="/app")
    
    prompt = f"""You are providing an executive brief. Professional but readable.

User asked: {msg.message}

System Data:
{docker.stdout}
{memory.stdout}
{disk.stdout}
Error count: {errors.stdout.strip()}

FORMATTING RULES:
- Use markdown headers (###)
- 8-12 lines total
- Include key metrics with context
- Use status icons strategically
- Professional tone
- Include one actionable insight

Example format:

### System Status
✅ **Operational** - All services running

### Services (7/7 active)
- Core systems: Healthy
- AI services: Online 6+ hours
- Web interface: Active

### Resources
- Memory: 1.6/7.9 GB (20% - optimal)
- Storage: 22/117 GB (19% - plenty available)

### Summary
No issues detected. System performing well within normal parameters.

Keep it professional but human-readable."""

    response = await ai_client.generate_response(prompt, {"mode": "assistant", "temperature": 0.2})
    return {"response": response["response"]}

@router.get("/status")
async def status():
    return {"status": "online"}
EOF

cp services/zoe-core/routers/developer_exec.py services/zoe-core/routers/developer.py
docker restart zoe-core

echo "✅ EXECUTIVE MODE READY"
echo ""
echo "Now you'll get professional briefs:"
echo "- Clear sections"
echo "- Key metrics with context"
echo "- 8-12 lines"
echo "- Actionable insights"
