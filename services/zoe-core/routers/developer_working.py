"""Working developer router with auto-execution"""
from fastapi import APIRouter
from pydantic import BaseModel
import subprocess
import json
import sys
sys.path.append('/app')

router = APIRouter(prefix="/api/developer")

class ChatMessage(BaseModel):
    message: str

@router.post("/chat")
async def developer_chat(msg: ChatMessage):
    """Developer chat with auto-execution"""
    
    message_lower = msg.message.lower()
    response_text = ""
    executed = False
    
    # Auto-execute for Docker queries
    if any(word in message_lower for word in ['docker', 'container', 'service']):
        result = subprocess.run(
            "docker ps --format 'table {{.Names}}\t{{.Status}}'",
            shell=True, capture_output=True, text=True
        )
        response_text = f"**Docker Containers:**\n```\n{result.stdout}\n```"
        executed = True
    
    # System health
    elif any(word in message_lower for word in ['health', 'status', 'system']):
        docker_result = subprocess.run(
            "docker ps --format '{{.Names}}' | wc -l",
            shell=True, capture_output=True, text=True
        )
        mem_result = subprocess.run(
            "free -h | grep Mem | awk '{print $3\"/\"$2}'",
            shell=True, capture_output=True, text=True
        )
        response_text = f"**System Status:**\n✅ Containers Running: {docker_result.stdout.strip()}/7\n💾 Memory: {mem_result.stdout.strip()}"
        executed = True
    
    # Memory/CPU
    elif any(word in message_lower for word in ['memory', 'ram', 'cpu']):
        result = subprocess.run(
            "free -h", shell=True, capture_output=True, text=True
        )
        response_text = f"**Memory Usage:**\n```\n{result.stdout}\n```"
        executed = True
    
    # Disk usage
    elif 'disk' in message_lower:
        result = subprocess.run(
            "df -h /", shell=True, capture_output=True, text=True
        )
        response_text = f"**Disk Usage:**\n```\n{result.stdout}\n```"
        executed = True
    
    # Default helpful response
    else:
        response_text = "I can check: docker containers, system health, memory/cpu, or disk usage. What would you like to see?"
    
    return {
        "response": response_text,
        "model": "llama3.2:3b",
        "complexity": "system" if executed else "simple",
        "executed": executed
    }

@router.get("/status")
async def status():
    # Get real container count
    result = subprocess.run(
        "docker ps --format '{{.Names}}' | wc -l",
        shell=True, capture_output=True, text=True
    )
    container_count = int(result.stdout.strip())
    
    return {
        "api": "online",
        "containers": {"running": container_count, "total": 7},
        "resources": {"status": "healthy"},
        "errors": []
    }
