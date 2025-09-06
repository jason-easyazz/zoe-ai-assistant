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
