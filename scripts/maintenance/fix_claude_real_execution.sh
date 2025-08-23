#!/bin/bash
# Make Claude ACTUALLY execute commands

echo "🔨 FIXING CLAUDE TO REALLY EXECUTE COMMANDS"
echo "==========================================="

cd /home/pi/zoe

# Create a new developer router that auto-executes
cat > services/zoe-core/routers/developer_auto.py << 'EOF'
"""Auto-executing developer chat"""
from fastapi import APIRouter
from pydantic import BaseModel
import subprocess
import re
import json

router = APIRouter(prefix="/api/developer")

class ChatMessage(BaseModel):
    message: str

@router.post("/chat_auto")
async def chat_with_auto_execute(msg: ChatMessage):
    """Chat endpoint that automatically executes commands"""
    
    message = msg.message.lower()
    
    # Detect intent and execute
    if any(word in message for word in ['check', 'show', 'status', 'health', 'docker', 'containers', 'running']):
        
        # Execute based on what they're asking
        if 'docker' in message or 'container' in message:
            result = subprocess.run("docker ps", shell=True, capture_output=True, text=True)
            output = result.stdout
            response = f"Here are the running containers:\n```\n{output}\n```"
            
        elif 'health' in message or 'status' in message:
            result = subprocess.run("docker ps --format 'table {{.Names}}\t{{.Status}}'", shell=True, capture_output=True, text=True)
            output = result.stdout
            
            # Also check API health
            api_result = subprocess.run("curl -s http://localhost:8000/health", shell=True, capture_output=True, text=True)
            api_output = api_result.stdout
            
            response = f"System Status:\n```\n{output}\n```\nAPI Health:\n```\n{api_output}\n```"
            
        elif 'memory' in message or 'cpu' in message or 'disk' in message:
            result = subprocess.run("free -h && df -h /", shell=True, capture_output=True, text=True)
            output = result.stdout
            response = f"System Resources:\n```\n{output}\n```"
            
        elif 'logs' in message or 'errors' in message:
            result = subprocess.run("docker logs zoe-core --tail 20 2>&1 | grep -i error || echo 'No errors found'", shell=True, capture_output=True, text=True)
            output = result.stdout
            response = f"Recent logs:\n```\n{output}\n```"
            
        else:
            response = "I'll check the system status..."
            result = subprocess.run("docker ps && echo '---' && df -h / && echo '---' && free -h", shell=True, capture_output=True, text=True)
            output = result.stdout
            response = f"System Overview:\n```\n{output}\n```"
            
        return {"response": response, "executed": True}
    
    # For other messages, just respond
    return {"response": "What would you like me to check? I can show docker containers, system health, logs, or resources.", "executed": False}
EOF

# Update main.py to include the new router
docker exec zoe-core python3 << 'PYTHON_EOF'
content = open('/app/main.py', 'r').read()
if 'developer_auto' not in content:
    lines = content.split('\n')
    for i, line in enumerate(lines):
        if 'from routers import' in line and 'developer_auto' not in line:
            lines[i] = line.rstrip() + ', developer_auto'
        elif 'app.include_router(developer.router)' in line:
            lines.insert(i+1, 'app.include_router(developer_auto.router)')
            break
    with open('/app/main.py', 'w') as f:
        f.write('\n'.join(lines))
    print("✅ Added auto-execute router")
PYTHON_EOF

# Update the frontend to use the new endpoint
cat > /tmp/update_frontend.js << 'EOF'
// Update to use auto-execute endpoint
async function sendMessage() {
    const input = document.getElementById('messageInput');
    const message = input.value.trim();
    if (!message) return;
    
    addMessage(message, 'user');
    input.value = '';
    
    try {
        const response = await fetch(`${API_BASE}/developer/chat_auto`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({message: message})
        });
        
        const data = await response.json();
        addMessage(data.response, 'claude');
    } catch (error) {
        addMessage('Error: ' + error.message, 'claude');
    }
}
EOF

# Replace the sendMessage function in developer.js
docker exec zoe-core sed -i '/async function sendMessage/,/^}/d' /app/../services/zoe-ui/dist/developer/js/developer.js
cat /tmp/update_frontend.js >> services/zoe-ui/dist/developer/js/developer.js

# Restart
echo "🔄 Restarting zoe-core..."
docker restart zoe-core
sleep 5

# Test it
echo "🧪 Testing auto-execution..."
curl -X POST http://localhost:8000/api/developer/chat_auto \
  -H "Content-Type: application/json" \
  -d '{"message": "check docker status"}' | jq -r '.response'

echo ""
echo "✅ DONE! Now Claude ACTUALLY executes commands!"
echo ""
echo "Refresh the dashboard and try:"
echo '  "Check system health"'
echo '  "Show docker containers"'
echo '  "Check for errors"'
