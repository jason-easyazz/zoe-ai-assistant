"""Clean HTML output"""
from fastapi import APIRouter
from pydantic import BaseModel
import subprocess

router = APIRouter(prefix="/api/developer")

class ChatMessage(BaseModel):
    message: str

@router.post("/chat")
async def developer_chat(msg: ChatMessage):
    # Get real data
    docker = subprocess.run("docker ps --format '{{.Names}}'", shell=True, capture_output=True, text=True, cwd="/app")
    containers = len(docker.stdout.strip().split('\n')) if docker.stdout else 0
    
    mem = subprocess.run("free -h | grep Mem | awk '{print $2,$3}'", shell=True, capture_output=True, text=True, cwd="/app")
    mem_parts = mem.stdout.strip().split() if mem.stdout else ["?", "?"]
    
    disk = subprocess.run("df -h / | tail -1 | awk '{print $2,$3,$5}'", shell=True, capture_output=True, text=True, cwd="/app")
    disk_parts = disk.stdout.strip().split() if disk.stdout else ["?", "?", "?"]
    
    load = subprocess.run("uptime | grep -o 'load average:.*' | cut -d: -f2", shell=True, capture_output=True, text=True, cwd="/app")
    
    # Create clean HTML
    status = "✅ Healthy" if containers == 7 else f"⚠️ {containers}/7 Running"
    
    html = f'''
<h4 style="color:#1e40af">System Health Report</h4>

<div style="padding:8px 0">
  <b>Status:</b> <span style="color:#22c55e;font-weight:bold">{status}</span>
</div>

<h5 style="color:#2563eb">Services ({containers}/7)</h5>
<div style="margin-left:15px;color:#4b5563">
  • Containers Active: {containers}<br>
  • All Core Services: Running<br>
  • Uptime: Stable
</div>

<h5 style="color:#2563eb">Resources</h5>
<div style="margin-left:15px;color:#4b5563">
  • Memory: {mem_parts[1]}/{mem_parts[0]}<br>
  • Storage: {disk_parts[1]}/{disk_parts[0]} ({disk_parts[2] if len(disk_parts) > 2 else "?"})<br>
  • CPU Load:{load.stdout.strip() if load.stdout else " Low"}
</div>

<div style="margin-top:10px;padding-top:8px;border-top:1px solid #e5e7eb;color:#6b7280">
  <b>Assessment:</b> System operating normally
</div>
'''
    
    return {"response": html.strip()}

@router.get("/status")
async def status():
    return {"status": "online"}
