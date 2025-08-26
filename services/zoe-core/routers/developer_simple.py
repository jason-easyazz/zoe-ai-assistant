"""Dead simple - execute basics, let LLM do everything"""
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
    # Always grab basic system info
    commands = [
        "docker ps",
        "free -h",
        "df -h",
        "uptime",
        "docker logs zoe-core --tail 10 2>&1 | head -20"
    ]
    
    system_data = []
    for cmd in commands:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, cwd="/app")
        system_data.append(f"Command: {cmd}\nOutput:\n{result.stdout}\n")
    
    # Give EVERYTHING to LLM
    prompt = f"""You are Zack, a system administrator for Zoe AI.

User request: {msg.message}

Current system data:
{'-'*40}
{''.join(system_data)}
{'-'*40}

Respond professionally with proper formatting. Use markdown, line breaks, and emojis where helpful.
If they ask for something not in the data, execute commands using subprocess.run() and show the results."""

    response = await ai_client.generate_response(prompt, {"mode": "assistant"})
    return {"response": response["response"]}

@router.get("/status")
async def status():
    return {"status": "online"}
