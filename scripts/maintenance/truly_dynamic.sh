#!/bin/bash
# Truly dynamic - handle ANY question

echo "🎯 TRULY DYNAMIC SYSTEM"
echo "======================"

cd /home/pi/zoe

cat > services/zoe-core/routers/developer_dynamic.py << 'EOF'
"""Handle ANY question dynamically"""
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
    # First, let LLM decide what commands to run
    decide_prompt = f"""You are a Linux system admin. User asked: {msg.message}

What Linux commands would answer this? List up to 5 commands.
Format: One command per line, no explanations.

Examples:
- For system health: docker ps, free -h, df -h
- For CPU temp: cat /sys/class/thermal/thermal_zone0/temp
- For network: netstat -tulpn
- For errors: journalctl -xe --no-pager | tail -20
- For processes: ps aux | head -10

Just list the commands, nothing else."""

    # Get LLM to decide commands
    decision = await ai_client.generate_response(decide_prompt, {"mode": "assistant", "temperature": 0.1})
    commands = [cmd.strip() for cmd in decision["response"].split('\n') if cmd.strip() and not cmd.startswith('#')][:5]
    
    # Execute the commands
    results = []
    for cmd in commands:
        try:
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True, cwd="/app", timeout=5)
            if result.stdout:
                results.append(f"Command: {cmd}\nOutput:\n{result.stdout[:500]}")
        except:
            results.append(f"Command: {cmd}\nOutput: Failed to execute")
    
    # Let LLM format the response nicely
    format_prompt = f"""User asked: {msg.message}

I ran these commands and got this data:
{'-'*40}
{chr(10).join(results)}
{'-'*40}

Create a clean HTML response that answers their question.
Use: <h4> for headers, <div> for content, <strong> for emphasis
Keep it concise and relevant to what they asked.
Don't show raw command output - interpret it and present nicely."""

    response = await ai_client.generate_response(format_prompt, {"mode": "assistant", "temperature": 0.2})
    return {"response": response["response"]}

@router.get("/status")
async def status():
    return {"status": "online"}
EOF

cp services/zoe-core/routers/developer_dynamic.py services/zoe-core/routers/developer.py
docker restart zoe-core

echo "✅ TRULY DYNAMIC system ready!"
echo ""
echo "Now try ANY question:"
echo "• What's the CPU temperature?"
echo "• Show network connections"
echo "• How long has Redis been running?"
echo "• What Python version is installed?"
echo "• Show disk I/O stats"
echo "• List biggest files"
echo ""
echo "The LLM will figure out what to run!"
