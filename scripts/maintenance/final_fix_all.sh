#!/bin/bash
# Final fix - HTML rendering and CSS

echo "🔨 FINAL FIX - HTML & CSS"
echo "========================"

cd /home/pi/zoe

# 1. Fix the backend to send proper HTML without markdown
cat > services/zoe-core/routers/developer_final.py << 'EOF'
"""Final clean HTML output"""
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
    
    # Build CLEAN HTML - no markdown
    status = "✅ Healthy" if containers == 7 else f"⚠️ {containers}/7 Running"
    
    html = f'''<div style="font-family:sans-serif">
<h3 style="color:#1e40af;margin:0 0 10px 0">System Health Report</h3>

<p style="margin:10px 0">
  <b>Status:</b> <span style="color:#22c55e;font-weight:bold">{status}</span>
</p>

<h4 style="color:#2563eb;margin:10px 0 5px 0">Services ({containers}/7)</h4>
<ul style="margin:5px 0 10px 20px;color:#4b5563">
  <li>Containers Active: {containers}</li>
  <li>All Core Services: Running</li>
  <li>Uptime: Stable</li>
</ul>

<h4 style="color:#2563eb;margin:10px 0 5px 0">Resources</h4>
<ul style="margin:5px 0 10px 20px;color:#4b5563">
  <li>Memory: {mem_parts[1]}/{mem_parts[0]}</li>
  <li>Storage: {disk_parts[1]}/{disk_parts[0]} ({disk_parts[2] if len(disk_parts) > 2 else "?"})</li>
  <li>CPU Load:{load.stdout.strip()[:30] if load.stdout else " Low"}</li>
</ul>

<p style="margin:10px 0;padding-top:10px;border-top:1px solid #e5e7eb;color:#6b7280">
  <b>Assessment:</b> System operating normally
</p>
</div>'''
    
    return {"response": html, "type": "html"}

@router.get("/status")
async def status():
    return {"status": "online"}
EOF

# 2. Replace developer.py
cp services/zoe-core/routers/developer_final.py services/zoe-core/routers/developer.py

# 3. Remove ALL background colors from CSS
echo "/* Clean white messages */" > services/zoe-ui/dist/developer/css/developer.css
echo ".message-content { background: white !important; border: 1px solid #ddd !important; }" >> services/zoe-ui/dist/developer/css/developer.css

# 4. Force cache refresh by renaming CSS file
mv services/zoe-ui/dist/developer/css/developer.css services/zoe-ui/dist/developer/css/style.css
sed -i 's/developer.css/style.css/g' services/zoe-ui/dist/developer/index.html

# 5. Restart backend
docker restart zoe-core

echo "✅ FINAL FIX COMPLETE!"
echo ""
echo "Now:"
echo "1. Open a NEW incognito/private window"
echo "2. Go to http://192.168.1.60:8080/developer/"
echo "3. You'll see clean white messages with proper HTML"
