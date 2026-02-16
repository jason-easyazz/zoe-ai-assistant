from auth_integration import require_permission, validate_session
"""
System Status and Monitoring
Provides system information and health status

Phase -1 Fix 5: Replaced mock service statuses and fake logs with real HTTP
health endpoint checks. Does NOT use Docker socket (security concern). Instead
checks each service's HTTP health endpoint on the internal Docker network.
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Dict, Any, List, Optional
import sqlite3
import psutil
import os
import platform
import logging
import asyncio
from datetime import datetime

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/system", tags=["system"])

# Service health endpoints on the internal Docker network (zoe-network).
# Each service exposes an HTTP endpoint that returns 200 when healthy.
# No Docker socket required -- pure HTTP checks.
SERVICE_HEALTH_ENDPOINTS = {
    "zoe-core": {"url": "http://localhost:8000/health", "display": "Zoe Core API"},
    "zoe-auth": {"url": "http://zoe-auth:8002/health", "display": "Auth Service"},
    "zoe-mcp-server": {"url": "http://zoe-mcp-server:8003/health", "display": "MCP Server"},
    "zoe-mem-agent": {"url": "http://zoe-mem-agent:8000/health", "display": "Memory Agent"},
    "zoe-litellm": {"url": "http://zoe-litellm:8001/health", "display": "LiteLLM Proxy"},
    "zoe-llamacpp": {"url": "http://zoe-llamacpp:11434/api/tags", "display": "LLM Engine"},
    "zoe-code-execution": {"url": "http://zoe-code-execution:8010/health", "display": "Code Sandbox"},
    "homeassistant-mcp-bridge": {"url": "http://homeassistant-mcp-bridge:8007/", "display": "HA Bridge"},
    "n8n-mcp-bridge": {"url": "http://n8n-mcp-bridge:8009/", "display": "N8N Bridge"},
    "zoe-n8n": {"url": "http://zoe-n8n:5678/healthz/readiness", "display": "N8N Workflows"},
    "livekit": {"url": "http://livekit:7880", "display": "LiveKit"},
}


async def _check_service_health(service_name: str, config: dict) -> dict:
    """Check a single service's health via HTTP. Returns status dict."""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.get(config["url"])
            return {
                "name": config["display"],
                "service": service_name,
                "status": "running" if response.status_code < 500 else "degraded",
                "http_status": response.status_code,
            }
    except httpx.ConnectError:
        return {
            "name": config["display"],
            "service": service_name,
            "status": "stopped",
            "http_status": None,
        }
    except httpx.TimeoutException:
        return {
            "name": config["display"],
            "service": service_name,
            "status": "timeout",
            "http_status": None,
        }
    except Exception as e:
        return {
            "name": config["display"],
            "service": service_name,
            "status": "error",
            "http_status": None,
            "error": str(e),
        }

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
    """Get system status and health information.

    Phase -1 Fix 5: Services are checked via real HTTP health endpoints,
    not hardcoded mock data. Each service is queried in parallel with a
    3-second timeout.
    """
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
        cpu_usage = psutil.cpu_percent(interval=0.5)
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage('/')

        # Check all services in parallel via HTTP health endpoints
        health_tasks = [
            _check_service_health(name, config)
            for name, config in SERVICE_HEALTH_ENDPOINTS.items()
        ]
        services = await asyncio.gather(*health_tasks)

        # Count healthy vs unhealthy
        running_count = sum(1 for s in services if s["status"] == "running")
        total_count = len(services)

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
                "services": services,
                "services_summary": f"{running_count}/{total_count} healthy"
            },
            "timestamp": datetime.now().isoformat()
        }

    except Exception as e:
        logger.error(f"System status check failed: {e}")
        return {
            "status": {
                "api_online": True,
                "uptime": "unknown",
                "version": "5.0",
                "cpu_usage": 0.0,
                "memory_usage": 0.0,
                "disk_usage": 0.0,
                "platform": platform.system(),
                "python_version": platform.python_version(),
                "services": [],
                "services_summary": "check failed"
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


@router.get("/intent-stats")
async def get_intent_tier_stats():
    """Get intent classification tier hit rates.

    Phase -1 Fix 3: Measurement endpoint to verify deterministic coverage
    before/after module intent registration fix.
    """
    try:
        # Import the classifier that was initialized in chat.py at module level
        from routers.chat import intent_classifier
        if intent_classifier and hasattr(intent_classifier, 'get_tier_stats'):
            return {
                "tier_stats": intent_classifier.get_tier_stats(),
                "timestamp": datetime.now().isoformat()
            }
        else:
            return {
                "tier_stats": None,
                "message": "Intent system not active or no stats available",
                "timestamp": datetime.now().isoformat()
            }
    except Exception as e:
        return {
            "tier_stats": None,
            "error": str(e),
            "timestamp": datetime.now().isoformat()
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
    """Get recent system logs from the actual log file.

    Phase -1 Fix 5: Reads real log entries from zoe-core's log file instead
    of returning hardcoded mock entries.
    """
    log_paths = [
        "/app/data/zoe.log",
        "/app/logs/zoe.log",
        "/tmp/zoe.log",
    ]

    log_file = None
    for path in log_paths:
        if os.path.exists(path):
            log_file = path
            break

    if not log_file:
        # No log file found -- return empty with explanation
        return {
            "logs": [],
            "message": "No log file found. Logs are available via Docker: docker logs zoe-core",
            "searched_paths": log_paths
        }

    try:
        # Read the last N lines from the log file efficiently
        entries = []
        with open(log_file, 'r', encoding='utf-8', errors='replace') as f:
            # Read all lines and take the last `limit` lines
            lines = f.readlines()
            recent_lines = lines[-limit:] if len(lines) > limit else lines

        for line in recent_lines:
            line = line.strip()
            if not line:
                continue
            # Parse common log format: "2025-01-29 10:30:15 - module - LEVEL - message"
            entry = {"raw": line}
            parts = line.split(" - ", 3)
            if len(parts) >= 4:
                entry = {
                    "timestamp": parts[0].strip(),
                    "service": parts[1].strip(),
                    "level": parts[2].strip(),
                    "message": parts[3].strip(),
                }
            elif len(parts) >= 3:
                entry = {
                    "timestamp": parts[0].strip(),
                    "level": parts[1].strip(),
                    "message": parts[2].strip(),
                    "service": "main",
                }
            entries.append(entry)

        return {"logs": entries, "source": log_file, "count": len(entries)}

    except Exception as e:
        logger.error(f"Failed to read logs: {e}")
        return {
            "logs": [],
            "error": str(e),
            "message": "Failed to read log file. Use: docker logs zoe-core"
        }

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
