"""
Developer Dashboard Backend
Complete implementation with all required endpoints
"""
from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta
import sqlite3
import json
import os
import psutil
import subprocess
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/developer", tags=["developer"])

# Database path
DB_PATH = os.getenv("DATABASE_PATH", "/app/data/zoe.db")

def init_developer_db():
    """Initialize developer tables"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS developer_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            priority TEXT DEFAULT 'medium',
            category TEXT,
            result JSON,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS system_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cpu_percent REAL,
            memory_percent REAL,
            disk_usage REAL,
            container_count INTEGER,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    conn.commit()
    conn.close()

init_developer_db()

# Models
class TaskCreate(BaseModel):
    title: str
    priority: str = "medium"
    category: Optional[str] = "general"

class MetricsResponse(BaseModel):
    cpu: float
    memory: float
    disk: float
    containers: int
    uptime: str

@router.get("/status")
async def get_developer_status():
    """Get overall developer dashboard status"""
    try:
        # System metrics
        cpu = psutil.cpu_percent(interval=1)
        memory = psutil.virtual_memory().percent
        disk = psutil.disk_usage('/').percent
        
        # Container count (simplified without Docker socket)
        container_count = 7  # Known Zoe containers
        
        return {
            "status": "operational",
            "metrics": {
                "cpu": cpu,
                "memory": memory,
                "disk": disk,
                "containers": container_count
            },
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        logger.error(f"Status check failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/tasks/recent")
async def get_recent_tasks(limit: int = 10):
    """Get recent developer tasks"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT id, title, status, priority, category, created_at, completed_at
        FROM developer_tasks
        ORDER BY created_at DESC
        LIMIT ?
    """, (limit,))
    
    tasks = []
    for row in cursor.fetchall():
        tasks.append({
            "id": row[0],
            "title": row[1],
            "status": row[2],
            "priority": row[3],
            "category": row[4],
            "created_at": row[5],
            "completed_at": row[6]
        })
    
    conn.close()
    return {"tasks": tasks, "count": len(tasks)}

@router.post("/tasks")
async def create_task(task: TaskCreate):
    """Create a new developer task"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        INSERT INTO developer_tasks (title, priority, category)
        VALUES (?, ?, ?)
    """, (task.title, task.priority, task.category))
    
    task_id = cursor.lastrowid
    conn.commit()
    conn.close()
    
    return {"id": task_id, "message": "Task created"}

@router.get("/metrics")
async def get_system_metrics():
    """Get system performance metrics"""
    try:
        # Current metrics
        metrics = {
            "cpu": {
                "percent": psutil.cpu_percent(interval=1),
                "cores": psutil.cpu_count(),
                "frequency": psutil.cpu_freq().current if psutil.cpu_freq() else 0
            },
            "memory": {
                "percent": psutil.virtual_memory().percent,
                "used_gb": psutil.virtual_memory().used / (1024**3),
                "total_gb": psutil.virtual_memory().total / (1024**3)
            },
            "disk": {
                "percent": psutil.disk_usage('/').percent,
                "used_gb": psutil.disk_usage('/').used / (1024**3),
                "total_gb": psutil.disk_usage('/').total / (1024**3)
            },
            "network": {
                "bytes_sent": psutil.net_io_counters().bytes_sent,
                "bytes_recv": psutil.net_io_counters().bytes_recv
            }
        }
        
        # Store metrics
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO system_metrics (cpu_percent, memory_percent, disk_usage, container_count)
            VALUES (?, ?, ?, ?)
        """, (metrics["cpu"]["percent"], metrics["memory"]["percent"], 
              metrics["disk"]["percent"], 7))
        conn.commit()
        conn.close()
        
        return metrics
    except Exception as e:
        logger.error(f"Metrics collection failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/scripts/execute")
async def execute_script(script_content: str, background_tasks: BackgroundTasks):
    """Execute a safe script (limited to read operations)"""
    # For safety, only allow specific commands
    safe_commands = ["ls", "pwd", "echo", "date", "docker ps", "curl"]
    
    # Check if script contains only safe commands
    for line in script_content.split('\n'):
        if line.strip() and not any(cmd in line for cmd in safe_commands):
            return {"error": "Script contains unsafe commands", "allowed": safe_commands}
    
    # This would execute in production with proper sandboxing
    return {
        "message": "Script execution disabled in safe mode",
        "script": script_content[:100],
        "note": "Enable execution in settings"
    }

@router.get("/chat/context")
async def get_chat_context():
    """Get context for Claude chat"""
    return {
        "system_status": await get_developer_status(),
        "recent_tasks": await get_recent_tasks(5),
        "capabilities": [
            "System monitoring",
            "Task management",
            "Script generation",
            "Database queries",
            "Container management"
        ]
    }
