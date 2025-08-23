#!/bin/bash
# Make Claude respond with clean, scannable formatting

echo "📊 CLEAN CLAUDE FORMATTING"
echo "=========================="

cd /home/pi/zoe

# Update the router with better LLM prompt
cat > services/zoe-core/routers/developer_clean.py << 'EOF'
"""Clean, efficient developer responses"""
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
    """Execute and format cleanly"""
    message = msg.message.lower()
    
    commands_to_run = []
    
    if any(word in message for word in ['docker', 'container', 'running', 'services']):
        commands_to_run.append(("docker ps --format 'table {{.Names}}\t{{.Status}}'", "Containers"))
    
    if any(word in message for word in ['health', 'status', 'check', 'system']):
        commands_to_run.append(("docker ps --format '{{.Names}}:{{.Status}}'", "Services"))
        commands_to_run.append(("free -h | grep Mem", "Memory"))
        commands_to_run.append(("df -h / | tail -1", "Disk"))
        commands_to_run.append(("uptime | cut -d',' -f1", "Uptime"))
    
    if 'memory' in message or 'ram' in message:
        commands_to_run.append(("free -h", "Memory"))
    
    if 'disk' in message or 'storage' in message:
        commands_to_run.append(("df -h", "Disk"))
    
    if any(word in message for word in ['log', 'error', 'problem']):
        commands_to_run.append(("docker logs zoe-core --tail 5 2>&1 | grep -i error || echo 'None'", "Errors"))
    
    if commands_to_run:
        results = []
        for cmd, label in commands_to_run:
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True, cwd="/app")
            results.append(f"{label}:\n{result.stdout}")
        
        raw_data = "\n".join(results)
        
        llm_prompt = f"""Format this system data cleanly. Be brief and scannable.

User asked: {msg.message}

Data:
{raw_data}

Rules:
- Use emojis as visual indicators (✅ good, ⚠️ warning, ❌ error, 🔄 loading)
- Format as bullet points or short lines
- Numbers and percentages prominent
- Skip explanations unless there's a problem
- One line summary at end
- Maximum 10 lines total"""

        response = await ai_client.generate_response(llm_prompt, {"mode": "assistant", "temperature": 0.3})
        return {"response": response["response"]}
    
    # Default
    return {"response": "What to check? Try: health, containers, memory, disk, errors"}

@router.get("/status")
async def status():
    return {"status": "online"}
EOF

cp services/zoe-core/routers/developer_clean.py services/zoe-core/routers/developer.py

docker restart zoe-core
sleep 10

echo "✅ Clean formatting enabled"
echo ""
echo "Now responses will be:"
echo "  • Quick and scannable"
echo "  • Visual with emojis"
echo "  • No unnecessary words"
