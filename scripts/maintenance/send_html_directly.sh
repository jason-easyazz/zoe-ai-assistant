#!/bin/bash
# Send HTML directly from backend instead of markdown

echo "📝 SENDING HTML DIRECTLY"
echo "======================="

cd /home/pi/zoe

cat > services/zoe-core/routers/developer_html.py << 'EOF'
"""Send HTML directly instead of markdown"""
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
    # Get system data
    docker = subprocess.run("docker ps --format '{{.Names}}:{{.Status}}'", shell=True, capture_output=True, text=True, cwd="/app")
    memory = subprocess.run("free -h | grep Mem", shell=True, capture_output=True, text=True, cwd="/app")
    disk = subprocess.run("df -h / | tail -1", shell=True, capture_output=True, text=True, cwd="/app")
    errors = subprocess.run("docker logs zoe-core --tail 10 2>&1 | grep -i error | wc -l", shell=True, capture_output=True, text=True, cwd="/app")
    
    prompt = f"""Create an executive system report. 

User asked: {msg.message}

Data:
{docker.stdout}
{memory.stdout}
{disk.stdout}
Errors: {errors.stdout.strip()}

FORMAT AS HTML (not markdown):
Use <h4> for headers
Use <div> for sections
Use <strong> for emphasis
Use <span style="color:green">✅</span> for good status
Use <br> for line breaks

Example:
<h4 style="color:#1e40af;margin:10px 0">System Status</h4>
<div><span style="color:green">✅</span> <strong>Healthy</strong> - All services running</div>
<br>
<h4 style="color:#1e40af;margin:10px 0">Services</h4>
<div style="margin-left:20px">
- Core: Running (4 min)<br>
- AI: Online (7 hours)
</div>

Keep it concise and professional."""

    response = await ai_client.generate_response(prompt, {"mode": "assistant", "temperature": 0.2})
    
    # Clean up any markdown that might slip through
    html = response["response"]
    html = html.replace("###", "").replace("**", "").replace("- ", "• ")
    
    return {"response": html}

@router.get("/status")
async def status():
    return {"status": "online"}
EOF

cp services/zoe-core/routers/developer_html.py services/zoe-core/routers/developer.py
docker restart zoe-core

echo "✅ Now sending HTML directly!"
echo ""
echo "Refresh the dashboard and it will display properly formatted!"
