"""Developer dashboard API endpoints - Complete"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Dict, Any, Optional, List
import subprocess
import json
import os
from datetime import datetime, timedelta
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
        return {"status": "error", "message": str(e)}

@router.get("/system/status")
async def get_system_status():
    """Get detailed system status"""
    try:
        # Get memory info
        memory_info = {}
        try:
            with open('/proc/meminfo', 'r') as f:
                for line in f.readlines()[:5]:
                    parts = line.strip().split(':')
                    if len(parts) == 2:
                        memory_info[parts[0]] = parts[1].strip()
        except:
            memory_info = {"error": "Could not read memory info"}
        
        return {
            "status": "operational",
            "services": {
                "api": "running",
                "ai": "connected" if ai_client.ollama_available else "disconnected",
                "ollama": ai_client.ollama_available,
                "anthropic": ai_client.anthropic_client is not None
            },
            "memory": memory_info,
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        logger.error(f"System status error: {e}")
        return {"status": "error", "message": str(e)}

@router.post("/chat")
async def developer_chat(request: ChatRequest):
    """Chat with developer AI"""
    try:
        developer_context = {
            "mode": "developer",
            "focus": "technical",
            "style": "precise"
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
    try:
        return ai_client.get_usage_stats()
    except Exception as e:
        return {"error": str(e)}

@router.get("/tasks/recent")
async def get_recent_tasks():
    """Get recent tasks (placeholder)"""
    # This is a placeholder - you can connect to a database later
    return {
        "tasks": [
            {
                "id": 1,
                "title": "System initialized",
                "status": "completed",
                "timestamp": datetime.now().isoformat()
            }
        ]
    }

@router.get("/metrics")
async def get_metrics():
    """Get system metrics"""
    try:
        metrics = {
            "cpu": {},
            "memory": {},
            "disk": {},
            "ai_usage": {}
        }
        
        # CPU load
        try:
            with open('/proc/loadavg', 'r') as f:
                load = f.read().strip().split()[:3]
                metrics["cpu"]["load_average"] = load
        except:
            pass
        
        # Memory
        try:
            with open('/proc/meminfo', 'r') as f:
                for line in f.readlines()[:5]:
                    if 'MemTotal' in line:
                        metrics["memory"]["total"] = line.split()[1]
                    elif 'MemAvailable' in line:
                        metrics["memory"]["available"] = line.split()[1]
        except:
            pass
        
        # AI Usage
        metrics["ai_usage"] = ai_client.get_usage_stats()
        metrics["timestamp"] = datetime.now().isoformat()
        
        return metrics
        
    except Exception as e:
        logger.error(f"Metrics error: {e}")
        return {"error": str(e)}

@router.post("/execute")
async def execute_command(request: CommandRequest):
    """Execute safe commands"""
    safe_commands = ["ls", "pwd", "date", "uptime", "df", "free", "echo"]
    
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

@router.post("/backup")
async def create_backup():
    """Create system backup (placeholder)"""
    return {
        "status": "success",
        "message": "Backup feature coming soon",
        "timestamp": datetime.now().isoformat()
    }
