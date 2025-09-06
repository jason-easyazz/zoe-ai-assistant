"""Use real data and proper HTML"""
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
    # Always get basic system info
    real_data = {}
    
    # Get REAL data
    docker = subprocess.run("docker ps --format '{{.Names}}'", shell=True, capture_output=True, text=True, cwd="/app")
    real_data['containers'] = len(docker.stdout.strip().split('\n')) if docker.stdout else 0
    
    memory = subprocess.run("free -h | grep Mem | awk '{print $3,$2}'", shell=True, capture_output=True, text=True, cwd="/app")
    if memory.stdout:
        parts = memory.stdout.strip().split()
        real_data['mem_used'] = parts[0] if len(parts) > 0 else "?"
        real_data['mem_total'] = parts[1] if len(parts) > 1 else "?"
    
    disk = subprocess.run("df -h / | tail -1 | awk '{print $5}'", shell=True, capture_output=True, text=True, cwd="/app")
    real_data['disk_percent'] = disk.stdout.strip() if disk.stdout else "?"
    
    cpu = subprocess.run("uptime | awk '{print $10}'", shell=True, capture_output=True, text=True, cwd="/app")
    real_data['load'] = cpu.stdout.strip().rstrip(',') if cpu.stdout else "?"
    
    # Format based on question type
    format_prompt = f"""Create HTML response using ONLY this REAL data:
- Containers running: {real_data['containers']}/7
- Memory: {real_data.get('mem_used', '?')}/{real_data.get('mem_total', '?')}
- Disk usage: {real_data.get('disk_percent', '?')}
- CPU load: {real_data.get('load', '?')}

User asked: {msg.message}

HTML format (use EXACT format):
<h4 style="color:#1e40af;margin:10px 0">Title Here</h4>
<div style="margin:8px 0;font-size:15px">content</div>

DO NOT make up numbers. Use ONLY the data provided above.
Keep response to 8-10 lines.
Be accurate - if containers are less than 7, mention it."""

    response = await ai_client.generate_response(format_prompt, {"mode": "assistant", "temperature": 0.1})
    
    # Ensure it's HTML
    html = response["response"]
    if not html.startswith("<"):
        html = f"<div>{html}</div>"
    
    return {"response": html, "format": "html"}

@router.get("/status")
async def status():
    return {"status": "online"}
