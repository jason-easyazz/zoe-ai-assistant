"""
Developer Router - Compatible with task_type column
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

# Data Models - map type to task_type
class DevelopmentTask(BaseModel):
    title: str
    description: str
    type: str = "feature"  # This will be mapped to task_type in DB
    priority: str = "medium"

class DeveloperChat(BaseModel):
    message: str
    context: Optional[Dict[str, Any]] = {}

# Core functions
def execute_command(cmd: str, timeout: int = 30, cwd: str = "/app") -> dict:
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
    try:
        cpu = psutil.cpu_percent(interval=1)
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        
        return {
            "metrics": {
                "cpu_percent": cpu,
                "memory_percent": round(mem.percent, 1),
                "disk_percent": round(disk.percent, 1)
            },
            "health_score": 100 - (cpu/4 + mem.percent/4 + disk.percent/4),
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        return {"error": str(e)}

@router.post("/tasks")
async def create_task(task: DevelopmentTask):
    """Create task - handles both type and task_type columns"""
    try:
        task_id = f"TASK-{uuid.uuid4().hex[:8].upper()}"
        
        conn = sqlite3.connect("/app/data/zoe.db")
        cursor = conn.cursor()
        
        # First, check which column exists
        cursor.execute("PRAGMA table_info(tasks)")
        columns = [col[1] for col in cursor.fetchall()]
        
        if 'type' in columns:
            # Use type column
            cursor.execute("""
                INSERT INTO tasks (task_id, title, description, type, priority, status)
                VALUES (?, ?, ?, ?, ?, 'pending')
            """, (task_id, task.title, task.description, task.type, task.priority))
        elif 'task_type' in columns:
            # Use task_type column
            cursor.execute("""
                INSERT INTO tasks (task_id, title, description, task_type, priority, status)
                VALUES (?, ?, ?, ?, ?, 'pending')
            """, (task_id, task.title, task.description, task.type, task.priority))
        else:
            # Create table if it doesn't exist properly
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    description TEXT,
                    type TEXT DEFAULT 'feature',
                    priority TEXT DEFAULT 'medium',
                    status TEXT DEFAULT 'pending',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
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
    """Get tasks - handles both column names"""
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
        
        tasks = []
        for row in cursor.fetchall():
            task = dict(row)
            # Normalize task_type to type for API response
            if 'task_type' in task and 'type' not in task:
                task['type'] = task['task_type']
            tasks.append(task)
        
        conn.close()
        return {"tasks": tasks, "count": len(tasks)}
        
    except Exception as e:
        logger.error(f"Task retrieval error: {e}")
        return {"tasks": [], "error": str(e)}

@router.post("/chat")
async def developer_chat(request: DeveloperChat):
    """Chat endpoint"""
    message_lower = request.message.lower()
    system_state = analyze_for_optimization()
    
    response_parts = []
    
    if 'task' in message_lower:
        tasks = await get_tasks()
        response_parts.append(f"## Tasks")
        response_parts.append(f"Total: {tasks['count']} tasks")
        if tasks['tasks']:
            for task in tasks['tasks'][:5]:
                task_type = task.get('type', task.get('task_type', 'unknown'))
                response_parts.append(f"- [{task['priority']}] {task['title']} (type: {task_type})")
    else:
        response_parts.append(f"System health: {system_state.get('health_score', 0):.0f}%")
    
    return {"response": "\n".join(response_parts)}

@router.get("/status")
async def get_status():
    return {"status": "operational", "version": "compatible"}

@router.get("/metrics")
async def get_metrics():
    return analyze_for_optimization()
