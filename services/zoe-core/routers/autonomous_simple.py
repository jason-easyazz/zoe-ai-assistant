"""
Simplified Autonomous Developer System
Works without Docker socket access
"""

from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import Optional, Dict, Any, List
import os
import json
import sqlite3
import subprocess
from datetime import datetime
import logging
from pathlib import Path

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/developer", tags=["developer"])

# Request models
class TaskRequest(BaseModel):
    task_id: Optional[str] = None
    action: str

# ============================================
# SIMPLIFIED ENDPOINTS (No Docker Required)
# ============================================

@router.get("/system/overview")
async def get_system_overview():
    """Simplified system overview without Docker"""
    import psutil
    
    return {
        "timestamp": datetime.now().isoformat(),
        "system": {
            "cpu_percent": psutil.cpu_percent(interval=1),
            "memory": dict(psutil.virtual_memory()._asdict()),
            "disk": dict(psutil.disk_usage('/')._asdict())
        },
        "message": "Docker monitoring disabled - socket not mounted"
    }

@router.get("/tasks")
async def get_tasks(status: Optional[str] = None, limit: int = 50):
    """Get developer tasks from database"""
    try:
        conn = sqlite3.connect("/app/data/developer_tasks.db")
        cursor = conn.cursor()
        
        if status:
            cursor.execute("""
                SELECT * FROM tasks 
                WHERE status = ? 
                ORDER BY 
                    CASE priority 
                        WHEN 'critical' THEN 1 
                        WHEN 'high' THEN 2 
                        WHEN 'medium' THEN 3 
                        WHEN 'low' THEN 4 
                    END,
                    created_at DESC 
                LIMIT ?
            """, (status, limit))
        else:
            cursor.execute("""
                SELECT * FROM tasks 
                ORDER BY 
                    CASE priority 
                        WHEN 'critical' THEN 1 
                        WHEN 'high' THEN 2 
                        WHEN 'medium' THEN 3 
                        WHEN 'low' THEN 4 
                    END,
                    created_at DESC 
                LIMIT ?
            """, (limit,))
        
        tasks = []
        for row in cursor.fetchall():
            tasks.append({
                "id": row[0],
                "task_id": row[1],
                "title": row[2],
                "description": row[3],
                "category": row[4],
                "priority": row[5],
                "status": row[6],
                "created_at": row[8] if len(row) > 8 else None
            })
        
        conn.close()
        return {"tasks": tasks}
    except Exception as e:
        logger.error(f"Error fetching tasks: {e}")
        return {"tasks": [], "error": str(e)}

@router.get("/system/diagnostics")
async def run_diagnostics():
    """Basic diagnostics without Docker"""
    import psutil
    
    diagnostics = {
        "timestamp": datetime.now().isoformat(),
        "checks": {
            "api_health": True,
            "database": False,
            "memory_ok": psutil.virtual_memory().percent < 90,
            "disk_ok": psutil.disk_usage('/').percent < 85
        },
        "issues": [],
        "recommendations": []
    }
    
    # Check database
    try:
        conn = sqlite3.connect("/app/data/zoe.db")
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'")
        diagnostics["checks"]["database"] = cursor.fetchone()[0] > 0
        conn.close()
    except:
        diagnostics["issues"].append("Database not accessible")
    
    return diagnostics
