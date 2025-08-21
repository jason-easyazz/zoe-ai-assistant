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
        # Simple status check without Docker commands (which might not work inside container)
        status_info = {
            "status": "operational",
            "ai_models": ai_client.get_usage_stats()["models_available"],
            "timestamp": datetime.now().isoformat(),
            "services": {
                "api": "running",
                "ai": "connected" if ai_client.ollama_available else "disconnected"
            }
        }
        return status_info
    except Exception as e:
        logger.error(f"Status error: {e}")
        return {"status": "partial", "error": str(e)}

@router.post("/chat")
async def developer_chat(request: ChatRequest):
    """Chat with developer AI personality"""
    try:
        developer_context = {
            "mode": "developer",
            "focus": "technical",
            "style": "precise",
            "max_length": 500  # Limit response length for speed
        }
        
        if request.context:
            developer_context.update(request.context)
        
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
        "ls", "pwd", "date", "uptime", "df", "free"
    ]
    
    cmd_parts = request.command.split()
    if not cmd_parts or cmd_parts[0] not in safe_commands:
        raise HTTPException(status_code=403, detail="Command not allowed")
    
    try:
        result = subprocess.run(
            cmd_parts,
            capture_output=True,
            text=True,
            timeout=5
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
        # Use simpler commands that work in container
        metrics = {}
        
        # Memory info
        with open('/proc/meminfo', 'r') as f:
            meminfo = f.read()
            for line in meminfo.split('\n'):
                if 'MemTotal' in line:
                    metrics['total_memory'] = line.split()[1]
                elif 'MemAvailable' in line:
                    metrics['available_memory'] = line.split()[1]
        
        # CPU info
        with open('/proc/loadavg', 'r') as f:
            loadavg = f.read().strip()
            metrics['load_average'] = loadavg.split()[:3]
        
        metrics['timestamp'] = datetime.now().isoformat()
        return metrics
        
    except Exception as e:
        return {"error": str(e)}
