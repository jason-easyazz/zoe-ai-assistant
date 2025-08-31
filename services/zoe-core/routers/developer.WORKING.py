"""
Enhanced Developer Router - Fixes memory, logs, tasks while keeping Docker working
No more hallucinations - real subprocess execution for ALL queries
"""

from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
import subprocess
import sqlite3
import json
import sys
import os
from datetime import datetime
import psutil
import logging

sys.path.append("/app")
logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/developer", tags=["developer"])

# Task storage (was missing)
developer_tasks = {}

class DeveloperChat(BaseModel):
    message: str

class DeveloperTask(BaseModel):
    title: str
    description: str
    type: str = "feature"
    priority: str = "medium"

def execute_command(cmd: str, timeout: int = 10) -> dict:
    """Execute system commands safely - ALREADY WORKING, keeping as-is"""
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd="/app"
        )
        return {
            "stdout": result.stdout[:5000],
            "stderr": result.stderr[:1000],
            "code": result.returncode,
            "success": result.returncode == 0
        }
    except subprocess.TimeoutExpired:
        return {"stdout": "", "stderr": "Command timed out", "code": -1, "success": False}
    except Exception as e:
        return {"stdout": "", "stderr": str(e), "code": -1, "success": False}

def get_real_system_info() -> dict:
    """Get comprehensive REAL system information"""
    info = {}
    
    # Docker containers - WORKING, keep as-is
    docker_cmd = execute_command("docker ps --format 'table {{.Names}}\t{{.Status}}'")
    info["containers"] = docker_cmd["stdout"] if docker_cmd["success"] else "Error getting containers"
    
    # Memory - FIX THIS (was broken)
    mem_cmd = execute_command("free -h")
    info["memory"] = mem_cmd["stdout"] if mem_cmd["success"] else "Error getting memory"
    
    # Disk - ADD THIS
    disk_cmd = execute_command("df -h /")
    info["disk"] = disk_cmd["stdout"] if disk_cmd["success"] else "Error getting disk"
    
    # CPU - ADD THIS
    cpu_cmd = execute_command("top -bn1 | head -5")
    info["cpu"] = cpu_cmd["stdout"] if cpu_cmd["success"] else "Error getting CPU"
    
    # Database info
    try:
        conn = sqlite3.connect("/app/data/zoe.db")
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [t[0] for t in cursor.fetchall()]
        info["database_tables"] = tables
        conn.close()
    except Exception as e:
        info["database_tables"] = []
    
    return info

@router.get("/status")
async def developer_status():
    """System status with real data"""
    system_info = get_real_system_info()
    
    # Count running containers
    container_count = system_info["containers"].count("Up") if isinstance(system_info["containers"], str) else 0
    
    return {
        "status": "operational",
        "mode": "full-command-execution",
        "personality": "Zack",
        "containers_running": container_count,
        "database_tables": len(system_info.get("database_tables", [])),
        "task_management": "enabled",
        "timestamp": datetime.now().isoformat()
    }

@router.post("/chat")
async def developer_chat(msg: DeveloperChat):
    """
    Developer chat that ACTUALLY executes commands based on queries
    """
    
    message_lower = msg.message.lower()
    
    # Determine what commands to run based on the query
    commands_to_run = []
    response_parts = []
    
    # Docker queries - WORKING
    if any(word in message_lower for word in ['docker', 'container', 'service']):
        result = execute_command("docker ps --format 'table {{.Names}}\t{{.Status}}'")
        if result["success"]:
            response_parts.append("**Real Container Status:**")
            response_parts.append(f"```\n{result['stdout']}\n```")
    
    # Memory queries - FIX THIS
    elif any(word in message_lower for word in ['memory', 'ram', 'mem']):
        result = execute_command("free -h")
        if result["success"]:
            response_parts.append("**Real Memory Usage:**")
            response_parts.append(f"```\n{result['stdout']}\n```")
        
        # Also show memory percentage
        mem_percent = execute_command("free | grep Mem | awk '{printf \"%.1f%% used (%.1fGB of %.1fGB)\", $3/$2*100, $3/1024/1024, $2/1024/1024}'")
        if mem_percent["success"]:
            response_parts.append(f"\n**Summary:** {mem_percent['stdout']}")
    
    # CPU queries - ADD THIS
    elif any(word in message_lower for word in ['cpu', 'processor', 'load']):
        result = execute_command("top -bn1 | head -10")
        if result["success"]:
            response_parts.append("**Real CPU Status:**")
            response_parts.append(f"```\n{result['stdout']}\n```")
    
    # Disk queries - ADD THIS
    elif any(word in message_lower for word in ['disk', 'storage', 'space']):
        result = execute_command("df -h")
        if result["success"]:
            response_parts.append("**Real Disk Usage:**")
            response_parts.append(f"```\n{result['stdout']}\n```")
    
    # Log queries - FIX THIS
    elif any(word in message_lower for word in ['log', 'error', 'debug']):
        # Determine which container's logs to show
        container = "zoe-core"  # default
        if "ui" in message_lower:
            container = "zoe-ui"
        elif "ollama" in message_lower:
            container = "zoe-ollama"
        
        result = execute_command(f"docker logs {container} --tail 20 2>&1")
        if result["success"]:
            response_parts.append(f"**Recent Logs from {container}:**")
            response_parts.append(f"```\n{result['stdout']}\n```")
        
        # Also check for errors
        error_check = execute_command(f"docker logs {container} --tail 50 2>&1 | grep -i error | tail -5")
        if error_check["success"] and error_check["stdout"]:
            response_parts.append(f"\n**Recent Errors:**")
            response_parts.append(f"```\n{error_check['stdout']}\n```")
    
    # System health - COMPREHENSIVE
    elif any(word in message_lower for word in ['health', 'status', 'system']):
        system_info = get_real_system_info()
        
        response_parts.append("**Complete System Status:**")
        response_parts.append("\n📦 **Docker Containers:**")
        response_parts.append(f"```\n{system_info['containers']}\n```")
        response_parts.append("\n💾 **Memory:**")
        response_parts.append(f"```\n{system_info['memory']}\n```")
        response_parts.append("\n💿 **Disk:**")
        response_parts.append(f"```\n{system_info['disk']}\n```")
        response_parts.append("\n🔢 **Database Tables:**")
        response_parts.append(f"{', '.join(system_info['database_tables'])}")
    
    # Default - show what we can do
    else:
        response_parts.append("**I'm Zack with FULL system access. Ask me to:**")
        response_parts.append("• Show docker containers")
        response_parts.append("• Check memory usage")
        response_parts.append("• View CPU status")
        response_parts.append("• Check disk space")
        response_parts.append("• Show recent logs")
        response_parts.append("• Get system health")
        response_parts.append("\nOr run any command with: /execute <command>")
    
    # Try to enhance with AI if available
    try:
        from ai_client_complete import get_ai_response
        
        # Add AI enhancement if we have real data
        if response_parts and len(response_parts) > 1:
            ai_context = f"System data: {' '.join(response_parts)}\n\nUser asked: {msg.message}\n\nProvide brief analysis."
            ai_response = await get_ai_response(ai_context, {"mode": "developer"})
            if ai_response and not ai_response.startswith("Error"):
                response_parts.append(f"\n**Analysis:** {ai_response}")
    except:
        pass  # AI enhancement is optional
    
    return {"response": "\n".join(response_parts)}

@router.post("/execute")
async def execute_direct(command: str):
    """Direct command execution endpoint - WORKING"""
    result = execute_command(command)
    return {
        "command": command,
        "output": result["stdout"],
        "error": result["stderr"],
        "success": result["success"]
    }

@router.get("/metrics")
async def get_metrics():
    """Real-time system metrics - ENHANCED"""
    try:
        # Get real metrics
        cpu_percent = psutil.cpu_percent(interval=1)
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        
        # Get container count
        docker_result = execute_command("docker ps -q | wc -l")
        container_count = int(docker_result["stdout"].strip()) if docker_result["success"] else 0
        
        return {
            "cpu_percent": cpu_percent,
            "memory_percent": memory.percent,
            "memory_used_gb": round(memory.used / (1024**3), 2),
            "memory_total_gb": round(memory.total / (1024**3), 2),
            "disk_percent": disk.percent,
            "disk_used_gb": round(disk.used / (1024**3), 2),
            "disk_total_gb": round(disk.total / (1024**3), 2),
            "containers_running": container_count,
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        return {"error": str(e)}

# ===== ADD TASK MANAGEMENT (was completely missing) =====

@router.post("/tasks")
async def create_task(task: DeveloperTask):
    """Create a new development task"""
    task_id = f"task_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    
    developer_tasks[task_id] = {
        "id": task_id,
        "title": task.title,
        "description": task.description,
        "type": task.type,
        "priority": task.priority,
        "status": "pending",
        "created_at": datetime.now().isoformat()
    }
    
    return {
        "task_id": task_id,
        "status": "created",
        "message": f"Task '{task.title}' created successfully"
    }

@router.get("/tasks")
async def list_tasks():
    """List all developer tasks"""
    return {
        "tasks": list(developer_tasks.values()),
        "count": len(developer_tasks),
        "pending": len([t for t in developer_tasks.values() if t["status"] == "pending"]),
        "completed": len([t for t in developer_tasks.values() if t.get("status") == "completed"])
    }

@router.put("/tasks/{task_id}/status")
async def update_task_status(task_id: str, status: str):
    """Update task status"""
    if task_id not in developer_tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    
    developer_tasks[task_id]["status"] = status
    developer_tasks[task_id]["updated_at"] = datetime.now().isoformat()
    
    return {"task_id": task_id, "status": status, "updated": True}

@router.delete("/tasks/{task_id}")
async def delete_task(task_id: str):
    """Delete a task"""
    if task_id not in developer_tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    
    del developer_tasks[task_id]
    return {"deleted": True, "task_id": task_id}
