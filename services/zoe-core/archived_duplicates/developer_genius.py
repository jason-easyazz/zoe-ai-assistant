"""
GENIUS DEVELOPER SYSTEM - Complete Restoration
All capabilities unified in one file
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
import asyncio
import uuid
import hashlib

sys.path.append("/app")
logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/developer", tags=["developer"])

# Models for compatibility
class DeveloperChat(BaseModel):
    message: str
    context: Optional[Dict[str, Any]] = {}

class DevelopmentTask(BaseModel):
    title: str
    description: str
    type: str = "feature"
    priority: str = "medium"

# ============================================
# CORE EXECUTION ENGINE
# ============================================

def execute_command(cmd: str, timeout: int = 30, cwd: str = "/app") -> dict:
    """Execute system commands with full visibility"""
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
    """Get real system metrics"""
    try:
        return {
            "metrics": {
                "cpu_percent": psutil.cpu_percent(interval=1),
                "memory": psutil.virtual_memory()._asdict(),
                "disk": psutil.disk_usage('/')._asdict(),
                "containers": execute_command("docker ps --format '{{.Names}}'")["stdout"].strip().split('\n')
            },
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        return {"error": str(e)}

# ============================================
# TASK MANAGEMENT
# ============================================

@router.post("/tasks")
async def create_task(task: DevelopmentTask):
    """Create development task"""
    task_id = f"TASK-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
    
    conn = sqlite3.connect("/app/data/zoe.db")
    cursor = conn.cursor()
    
    # Ensure table exists
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            task_id TEXT PRIMARY KEY,
            title TEXT,
            description TEXT,
            type TEXT,
            priority TEXT,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    cursor.execute(
        "INSERT INTO tasks (task_id, title, description, type, priority) VALUES (?, ?, ?, ?, ?)",
        (task_id, task.title, task.description, task.type, task.priority)
    )
    conn.commit()
    conn.close()
    
    return {"task_id": task_id, "status": "created"}

@router.get("/tasks")
async def get_tasks():
    """Get all tasks"""
    conn = sqlite3.connect("/app/data/zoe.db")
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    try:
        cursor.execute("SELECT * FROM tasks ORDER BY created_at DESC LIMIT 50")
        tasks = [dict(row) for row in cursor.fetchall()]
    except:
        tasks = []
    
    conn.close()
    return {"tasks": tasks}

# ============================================
# MAIN CHAT ENDPOINT
# ============================================

@router.post("/chat")
async def developer_chat(request: DeveloperChat):
    """Main developer chat with full system access"""
    message_lower = request.message.lower()
    
    # Import AI dynamically
    try:
        from ai_client import get_ai_response
    except:
        # Fallback if AI not available
        get_ai_response = lambda m, c: {"response": "AI temporarily unavailable"}
    
    # Get system state
    system_state = analyze_for_optimization()
    
    # Build context for AI
    context = {
        "mode": "developer",
        "system_state": system_state,
        "message": request.message
    }
    
    # Handle specific requests with real data
    if any(word in message_lower for word in ['status', 'health', 'system']):
        metrics = system_state.get('metrics', {})
        response = f"""## System Status
        
**CPU:** {metrics.get('cpu_percent', 'N/A')}%
**Memory:** {metrics.get('memory', {}).get('percent', 'N/A')}%
**Disk:** {metrics.get('disk', {}).get('percent', 'N/A')}%
**Containers:** {len(metrics.get('containers', []))} running

Everything is operational."""
        
    elif 'docker' in message_lower or 'container' in message_lower:
        result = execute_command("docker ps --format 'table {{.Names}}\t{{.Status}}'")
        response = f"## Docker Containers\n```\n{result['stdout']}\n```"
        
    elif 'task' in message_lower:
        tasks = await get_tasks()
        response = f"## Tasks\nTotal: {len(tasks['tasks'])} tasks"
        
    else:
        # Use AI for complex requests
        try:
            ai_response = await get_ai_response(request.message, context)
            response = ai_response if isinstance(ai_response, str) else ai_response.get('response', 'Processing...')
        except:
            response = "I can help with system status, docker containers, and task management."
    
    return {"response": response, "system_state": system_state}

@router.get("/status")
async def get_status():
    """Get developer system status"""
    return {
        "status": "operational",
        "personality": "Zack",
        "capabilities": ["full_system_access", "task_management", "real_metrics"],
        "version": "genius-restored"
    }

@router.get("/metrics")
async def get_metrics():
    """Get real-time metrics"""
    return analyze_for_optimization()
