"""Fixed developer router that auto-executes"""
from fastapi import APIRouter
from pydantic import BaseModel
import subprocess
import json

router = APIRouter(prefix="/api/developer")

class ChatMessage(BaseModel):
    message: str

@router.post("/chat")
async def developer_chat(msg: ChatMessage):
    """Auto-executing chat"""
    message = msg.message.lower()
    
    # Auto-execute for common commands
    if 'docker' in message or 'container' in message:
        result = subprocess.run("docker ps", shell=True, capture_output=True, text=True, cwd="/app")
        return {"response": f"Docker containers:\n```\n{result.stdout}\n```"}
    
    elif 'health' in message or 'status' in message:
        result = subprocess.run("docker ps --format 'table {{.Names}}\t{{.Status}}'", shell=True, capture_output=True, text=True, cwd="/app")
        return {"response": f"System status:\n```\n{result.stdout}\n```"}
    
    elif 'memory' in message or 'cpu' in message:
        result = subprocess.run("free -h", shell=True, capture_output=True, text=True, cwd="/app")
        return {"response": f"Memory usage:\n```\n{result.stdout}\n```"}
    
    elif 'disk' in message:
        result = subprocess.run("df -h /", shell=True, capture_output=True, text=True, cwd="/app")
        return {"response": f"Disk usage:\n```\n{result.stdout}\n```"}
    
    else:
        return {"response": "Ask me to check: docker containers, system health, memory, cpu, or disk"}

@router.get("/status")
async def status():
    return {"status": "online", "full_access": True}
