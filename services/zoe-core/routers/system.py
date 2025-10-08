from auth_integration import require_permission, validate_session
"""
System Status and Monitoring
Provides system information and health status
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Dict, Any, List, Optional
import sqlite3
import psutil
import os
import platform
from datetime import datetime

router = APIRouter(prefix="/api/system", tags=["system"])

class SystemStatus(BaseModel):
    status: str
    uptime: str
    cpu_usage: float
    memory_usage: float
    disk_usage: float
    platform: str
    python_version: str
    services: List[Dict[str, Any]]

@router.get("/status")
async def get_system_status():
    """Get system status and health information"""
    try:
        # Get system information
        uptime_seconds = psutil.boot_time()
        uptime = datetime.now().timestamp() - uptime_seconds
        
        # Format uptime
        days = int(uptime // 86400)
        hours = int((uptime % 86400) // 3600)
        minutes = int((uptime % 3600) // 60)
        uptime_str = f"{days}d {hours}h {minutes}m"
        
        # Get resource usage
        cpu_usage = psutil.cpu_percent(interval=1)
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        
        # Mock services status (in a real system, you'd check actual services)
        services = [
            {"name": "Database", "status": "running", "uptime": "2h 15m"},
            {"name": "API Server", "status": "running", "uptime": "1h 30m"},
            {"name": "Background Tasks", "status": "running", "uptime": "2h 15m"},
            {"name": "Weather Service", "status": "running", "uptime": "1h 30m"},
            {"name": "Home Assistant", "status": "connected", "uptime": "5h 20m"}
        ]
        
        return {
            "status": {
                "api_online": True,
                "uptime": uptime_str,
                "version": "5.0",
                "cpu_usage": round(cpu_usage, 1),
                "memory_usage": round(memory.percent, 1),
                "disk_usage": round(disk.percent, 1),
                "platform": platform.system(),
                "python_version": platform.python_version(),
                "services": services
            },
            "timestamp": datetime.now().isoformat()
        }
        
    except Exception as e:
        # Fallback to basic status if psutil fails
        return {
            "status": {
                "api_online": False,
                "uptime": "unknown",
                "version": "5.0",
                "cpu_usage": 0.0,
                "memory_usage": 0.0,
                "disk_usage": 0.0,
                "platform": platform.system(),
                "python_version": platform.python_version(),
                "services": [
                    {"name": "API Server", "status": "running", "uptime": "unknown"}
                ]
            },
            "timestamp": datetime.now().isoformat(),
            "error": str(e)
        }

@router.get("/health")
async def health_check():
    """Simple health check endpoint"""
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "version": "1.0.0"
    }

@router.get("/info")
async def get_system_info():
    """Get detailed system information"""
    try:
        return {
            "platform": {
                "system": platform.system(),
                "release": platform.release(),
                "version": platform.version(),
                "machine": platform.machine(),
                "processor": platform.processor()
            },
            "python": {
                "version": platform.python_version(),
                "implementation": platform.python_implementation()
            },
            "environment": {
                "working_directory": os.getcwd(),
                "environment_variables": len(os.environ)
            },
            "resources": {
                "cpu_count": psutil.cpu_count(),
                "memory_total": psutil.virtual_memory().total,
                "disk_total": psutil.disk_usage('/').total
            }
        }
    except Exception as e:
        return {
            "platform": {
                "system": platform.system(),
                "release": platform.release(),
                "version": platform.version(),
                "machine": platform.machine(),
                "processor": platform.processor()
            },
            "python": {
                "version": platform.python_version(),
                "implementation": platform.python_implementation()
            },
            "error": str(e)
        }

@router.get("/logs")
async def get_system_logs(limit: int = 50):
    """Get recent system logs"""
    # In a real system, this would read from log files
    # For now, return mock log entries
    mock_logs = [
        {
            "timestamp": datetime.now().isoformat(),
            "level": "INFO",
            "message": "System started successfully",
            "service": "main"
        },
        {
            "timestamp": datetime.now().isoformat(),
            "level": "INFO", 
            "message": "All routers loaded",
            "service": "main"
        },
        {
            "timestamp": datetime.now().isoformat(),
            "level": "INFO",
            "message": "Database connection established",
            "service": "database"
        }
    ]
    
    return {"logs": mock_logs[:limit]}

# ------------------------------------------------------------------
# Response quality feedback logging
# ------------------------------------------------------------------
class FeedbackPayload(BaseModel):
    message: str
    model: str
    routing_type: str
    helpful: bool
    notes: Optional[str] = None

@router.post("/feedback")
async def log_feedback(payload: FeedbackPayload):
    try:
        conn = sqlite3.connect('/app/data/zoe.db')
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS response_feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message TEXT,
                model TEXT,
                routing_type TEXT,
                helpful INTEGER,
                notes TEXT,
                created_at TEXT
            )
            """
        )
        cur.execute(
            """
            INSERT INTO response_feedback (message, model, routing_type, helpful, notes, created_at)
            VALUES (?, ?, ?, ?, ?, datetime('now'))
            """,
            (payload.message, payload.model, payload.routing_type, 1 if payload.helpful else 0, payload.notes),
        )
        conn.commit()
        conn.close()
        return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
