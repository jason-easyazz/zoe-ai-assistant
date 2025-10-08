"""
Clean Developer Router with Working Task System
"""

from fastapi import APIRouter, HTTPException
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
import uuid

sys.path.append("/app")
logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/developer", tags=["developer"])

# Data Models
class DeveloperChat(BaseModel):
    message: str
    context: Optional[Dict[str, Any]] = {}

class DevelopmentTask(BaseModel):
    title: str
    description: str
    type: str = "feature"  # This maps to 'type' column in DB
    priority: str = "medium"

# Core Functions
def execute_command(cmd: str, timeout: int = 30, cwd: str = "/app") -> dict:
    """Execute system commands"""
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True,
            timeout=timeout, cwd=cwd
        )
        return {
            "stdout": result.stdout[:10000],
            "stderr": result.stderr[:2000],
            "returncode": result.returncode,
            "success": result.returncode == 0
        }
    except Exception as e:
        return {"stdout": "", "stderr": str(e), "returncode": -1, "success": False}

def analyze_for_optimization() -> dict:
    """Get system metrics"""
    try:
        cpu = psutil.cpu_percent(interval=1)
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        
        return {
            "metrics": {
                "cpu_percent": cpu,
                "memory_percent": round(mem.percent, 1),
                "disk_percent": round(disk.percent, 1),
                "containers": execute_command("docker ps --format '{{.Names}}'")["stdout"].strip().split('\n')
            },
            "health_score": 100 - (cpu/4 + mem.percent/4 + disk.percent/4),
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        return {"error": str(e)}

# Task Endpoints
@router.post("/tasks")
async def create_task(task: DevelopmentTask):
    """Create a new task"""
    try:
        task_id = f"TASK-{uuid.uuid4().hex[:8].upper()}"
        
        conn = sqlite3.connect("/app/data/zoe.db")
        cursor = conn.cursor()
        
        # Insert with correct column names
        cursor.execute("""
            INSERT INTO tasks (task_id, title, description, type, priority, status)
            VALUES (?, ?, ?, ?, ?, 'pending')
        """, (task_id, task.title, task.description, task.type, task.priority))
        
        conn.commit()
        conn.close()
        
        return {"task_id": task_id, "status": "created", "title": task.title}
        
    except Exception as e:
        logger.error(f"Task creation error: {e}")
        return {"error": str(e), "status": "failed"}

@router.get("/tasks")
async def get_tasks(status: Optional[str] = None):
    """Get all tasks"""
    try:
        conn = sqlite3.connect("/app/data/zoe.db")
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        if status:
            cursor.execute(
                "SELECT * FROM tasks WHERE status = ? ORDER BY created_at DESC",
                (status,)
            )
        else:
            cursor.execute("SELECT * FROM tasks ORDER BY created_at DESC")
        
        tasks = [dict(row) for row in cursor.fetchall()]
        conn.close()
        
        return {"tasks": tasks, "count": len(tasks)}
        
    except Exception as e:
        logger.error(f"Task retrieval error: {e}")
        return {"tasks": [], "error": str(e)}

# Chat Endpoint
@router.post("/chat")
async def developer_chat(request: DeveloperChat):
    """Developer chat endpoint"""
    message_lower = request.message.lower()
    system_state = analyze_for_optimization()
    
    # Build response
    response_parts = []
    
    if any(word in message_lower for word in ['status', 'health', 'system']):
        response_parts.append(f"## System Status")
        response_parts.append(f"Health Score: {system_state.get('health_score', 0):.0f}%")
        response_parts.append(f"CPU: {system_state['metrics']['cpu_percent']}%")
        response_parts.append(f"Memory: {system_state['metrics']['memory_percent']}%")
        response_parts.append(f"Disk: {system_state['metrics']['disk_percent']}%")
    
    elif 'task' in message_lower:
        tasks = await get_tasks()
        response_parts.append(f"## Tasks")
        response_parts.append(f"Total: {tasks['count']} tasks")
        if tasks['tasks']:
            response_parts.append("\nRecent tasks:")
            for task in tasks['tasks'][:5]:
                response_parts.append(f"- [{task['priority']}] {task['title']} ({task['task_id']})")
    
    else:
        response_parts.append("I'm Zack, your AI developer assistant.")
        response_parts.append(f"System health: {system_state.get('health_score', 0):.0f}%")
    
    return {"response": "\n".join(response_parts), "system_state": system_state}

# Status Endpoints
@router.get("/status")
async def get_status():
    """Get developer status"""
    return {
        "status": "operational",
        "personality": "Zack",
        "capabilities": ["task_management", "system_monitoring", "code_generation"],
        "version": "2.0"
    }

@router.get("/metrics")
async def get_metrics():
    """Get system metrics"""
    return analyze_for_optimization()
