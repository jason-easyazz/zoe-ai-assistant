"""Developer dashboard API endpoints"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Dict, Any, Optional
import subprocess
import json
import os
from datetime import datetime
import logging

# Import the AI client
import sys
sys.path.append('/app')
from ai_client import ai_client

router = APIRouter(prefix="/api/developer")
logger = logging.getLogger(__name__)

class ChatRequest(BaseModel):
    message: str
    context: Optional[Dict] = None

class CommandRequest(BaseModel):
    command: str

@router.get("/status")
async def get_status():
    """Get system status"""
    try:
        # Get container status
        result = subprocess.run(
            ["docker", "ps", "--format", "json"],
            capture_output=True,
            text=True
        )
        
        containers = []
        if result.returncode == 0 and result.stdout:
            for line in result.stdout.strip().split('\n'):
                if line:
                    try:
                        container = json.loads(line)
                        if container.get('Names', '').startswith('zoe-'):
                            containers.append({
                                'name': container.get('Names', ''),
                                'status': container.get('State', ''),
                                'uptime': container.get('Status', '')
                            })
                    except:
                        pass
        
        return {
            "status": "operational",
            "containers": containers,
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        logger.error(f"Status error: {e}")
        return {"status": "error", "message": str(e)}

@router.post("/chat")
async def developer_chat(request: ChatRequest):
    """Chat with Claude developer personality"""
    try:
        # Add developer context
        developer_context = {
            "mode": "developer",
            "focus": "technical",
            "style": "precise"
        }
        
        if request.context:
            developer_context.update(request.context)
        
        # Get response from AI
        result = await ai_client.generate_response(
            request.message,
            developer_context
        )
        
        return result
        
    except Exception as e:
        logger.error(f"Chat error: {e}")
        return {
            "response": f"Error: {str(e)}",
            "model_used": "none",
            "complexity": "unknown"
        }

@router.get("/ai/usage")
async def get_ai_usage():
    """Get AI usage statistics"""
    return ai_client.get_usage_stats()

@router.post("/execute")
async def execute_command(request: CommandRequest):
    """Execute safe system commands"""
    safe_commands = [
        "docker ps",
        "docker logs",
        "df -h",
        "free -m",
        "uptime"
    ]
    
    # Check if command is safe
    if not any(request.command.startswith(cmd) for cmd in safe_commands):
        raise HTTPException(status_code=403, detail="Command not allowed")
    
    try:
        result = subprocess.run(
            request.command.split(),
            capture_output=True,
            text=True,
            timeout=10
        )
        
        return {
            "output": result.stdout,
            "error": result.stderr,
            "return_code": result.returncode
        }
    except Exception as e:
        return {"error": str(e)}

@router.get("/system/metrics")
async def get_metrics():
    """Get system performance metrics"""
    try:
        # CPU usage
        cpu_result = subprocess.run(
            ["top", "-bn1"],
            capture_output=True,
            text=True
        )
        cpu_line = cpu_result.stdout.split('\n')[2] if cpu_result.stdout else ""
        
        # Memory usage
        mem_result = subprocess.run(
            ["free", "-m"],
            capture_output=True,
            text=True
        )
        
        # Disk usage
        disk_result = subprocess.run(
            ["df", "-h", "/"],
            capture_output=True,
            text=True
        )
        
        return {
            "cpu": cpu_line,
            "memory": mem_result.stdout,
            "disk": disk_result.stdout,
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        return {"error": str(e)}
