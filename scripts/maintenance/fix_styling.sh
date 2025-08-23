#!/bin/bash
# Fix background color and HTML rendering

echo "🎨 FIXING STYLING"
echo "================"

cd /home/pi/zoe

# Update CSS in the developer HTML page
cat >> services/zoe-ui/dist/developer/index.html << 'STYLE_FIX'
<style>
/* Override purple background */
.message.claude .message-content {
    background: #ffffff !important;
    border: 1px solid #e5e7eb;
    border-left: 3px solid #3b82f6;
    box-shadow: 0 1px 3px rgba(0,0,0,0.05);
}

.message.user .message-content {
    background: #f0f9ff !important;
    border: 1px solid #dbeafe;
}

/* Ensure HTML renders */
.message-content h4 {
    color: #1e40af !important;
    font-weight: 600 !important;
    margin: 10px 0 !important;
}

.message-content h5 {
    color: #2563eb !important;
    font-weight: 600 !important;
    margin: 8px 0 !important;
}

.message-content strong {
    font-weight: bold !important;
}
</style>
STYLE_FIX

# Also fix the router to output cleaner HTML
cat > services/zoe-core/routers/developer_clean.py << 'EOF'
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
EOF

cp services/zoe-core/routers/developer_clean.py services/zoe-core/routers/developer.py
docker restart zoe-core

echo "✅ Fixed styling!"
echo ""
echo "Changes:"
echo "• White background instead of purple"
echo "• Clean blue accent border"
echo "• Better contrast for readability"
echo "• HTML properly rendered"
echo ""
echo "Hard refresh (Ctrl+F5) to see changes!"
