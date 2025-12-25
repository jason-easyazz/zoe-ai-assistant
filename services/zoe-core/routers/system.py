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
import logging
from datetime import datetime

logger = logging.getLogger(__name__)
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


# Phase 4 Enhancement: Storage monitoring endpoint
@router.get("/storage")
async def get_storage_analysis():
    """
    Get comprehensive storage analysis.
    Shows Docker images, databases, Ollama models (monitoring only), and logs.
    """
    try:
        # Import inline to handle path correctly
        import subprocess
        
        # Get database sizes
        db_sizes = {}
        for db_file in ["zoe.db", "memory.db", "training.db"]:
            db_path = f"/app/data/{db_file}"
            if os.path.exists(db_path):
                size_bytes = os.path.getsize(db_path)
                db_sizes[db_file] = {
                    "size_bytes": size_bytes,
                    "size_mb": round(size_bytes / (1024 * 1024), 2)
                }
        
        total_db_size = sum(db['size_mb'] for db in db_sizes.values())
        
        # Get disk usage
        try:
            disk_result = subprocess.run(
                ["df", "-h", "/app/data"],
                capture_output=True,
                text=True
            )
            disk_lines = disk_result.stdout.strip().split('\n')
            disk_info = {}
            if len(disk_lines) > 1:
                parts = disk_lines[1].split()
                disk_info = {
                    "size": parts[1],
                    "used": parts[2],
                    "available": parts[3],
                    "use_percent": parts[4]
                }
        except:
            disk_info = {"available": False}
        
        # Get Ollama models (if accessible)
        model_info = {"note": "Monitoring only - NO deletion (user requirement)"}
        try:
            import httpx
            async with httpx.AsyncClient() as client:
                response = await client.get("http://zoe-ollama:11434/api/tags", timeout=5.0)
                if response.status_code == 200:
                    data = response.json()
                    models = data.get("models", [])
                    model_info = {
                        "count": len(models),
                        "models": [{"name": m.get("name"), "size_gb": round(m.get("size", 0) / (1024**3), 2)} for m in models[:10]],
                        "note": "Monitoring only - NO deletion (user requirement)"
                    }
        except:
            pass
        
        return {
            "timestamp": datetime.now().isoformat(),
            "databases": {
                "files": db_sizes,
                "total_size_mb": round(total_db_size, 2)
            },
            "total_disk": disk_info,
            "ollama_models": model_info,
            "recommendations": []
        }
        
    except Exception as e:
        logger.error(f"Storage analysis failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Storage analysis failed: {str(e)}")

@router.get("/platform")
async def get_platform():
    """Get platform information"""
    try:
        return {
            "platform": platform.system(),
            "platform_release": platform.release(),
            "platform_version": platform.version(),
            "architecture": platform.machine(),
            "processor": platform.processor(),
            "python_version": platform.python_version()
        }
    except Exception as e:
        logger.error(f"Failed to get platform info: {e}")
        raise HTTPException(status_code=500, detail=str(e))
