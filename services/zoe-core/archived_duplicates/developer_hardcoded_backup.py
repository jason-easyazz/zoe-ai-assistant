"""
Intelligent Developer Router - Analyzes real system data for optimization
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

# Task storage
developer_tasks = {}

class DeveloperChat(BaseModel):
    message: str

class DeveloperTask(BaseModel):
    title: str
    description: str
    type: str = "feature"
    priority: str = "medium"

def execute_command(cmd: str, timeout: int = 10) -> dict:
    """Execute system commands safely"""
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
    
    # Docker containers
    docker_cmd = execute_command("docker ps --format 'table {{.Names}}\t{{.Status}}'")
    info["containers"] = docker_cmd["stdout"] if docker_cmd["success"] else "Error"
    
    # Memory
    mem_cmd = execute_command("free -h")
    info["memory"] = mem_cmd["stdout"] if mem_cmd["success"] else "Error"
    
    # Disk
    disk_cmd = execute_command("df -h /")
    info["disk"] = disk_cmd["stdout"] if disk_cmd["success"] else "Error"
    
    # CPU
    cpu_cmd = execute_command("top -bn1 | head -5")
    info["cpu"] = cpu_cmd["stdout"] if cpu_cmd["success"] else "Error"
    
    # Database
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

def analyze_for_optimization() -> dict:
    """Analyze system and provide REAL optimization recommendations"""
    
    analysis = {
        "metrics": {},
        "issues": [],
        "recommendations": []
    }
    
    # Get real metrics
    try:
        cpu_percent = psutil.cpu_percent(interval=1)
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        
        analysis["metrics"] = {
            "cpu_percent": cpu_percent,
            "memory_percent": memory.percent,
            "memory_available_gb": round(memory.available / (1024**3), 2),
            "disk_percent": disk.percent,
            "disk_free_gb": round(disk.free / (1024**3), 2)
        }
        
        # Analyze for issues
        if memory.percent > 80:
            analysis["issues"].append("⚠️ HIGH MEMORY USAGE")
            analysis["recommendations"].append("• Restart memory-intensive containers")
            analysis["recommendations"].append("• Check for memory leaks in zoe-core")
        
        if disk.percent > 80:
            analysis["issues"].append("⚠️ LOW DISK SPACE")
            analysis["recommendations"].append("• Clean Docker images: docker system prune -a")
            analysis["recommendations"].append("• Remove old logs: find /var/log -type f -name '*.log' -mtime +7 -delete")
        
        if cpu_percent > 70:
            analysis["issues"].append("⚠️ HIGH CPU USAGE")
            analysis["recommendations"].append("• Check for runaway processes: top")
            analysis["recommendations"].append("• Consider optimizing Ollama model size")
        
        # Check specific containers
        docker_stats = execute_command("docker stats --no-stream --format 'json'")
        if docker_stats["success"] and docker_stats["stdout"]:
            try:
                # Parse docker stats
                for line in docker_stats["stdout"].strip().split('\n'):
                    if line:
                        stats = json.loads(line)
                        if "zoe-ollama" in stats.get("Name", ""):
                            mem_usage = stats.get("MemPerc", "0%").replace("%", "")
                            if float(mem_usage) > 40:
                                analysis["recommendations"].append(f"• Ollama using {mem_usage}% memory - consider smaller model")
            except:
                pass
        
        # Check for stopped containers
        stopped = execute_command("docker ps -a --filter 'status=exited' --format '{{.Names}}'")
        if stopped["success"] and stopped["stdout"]:
            stopped_containers = stopped["stdout"].strip().split('\n')
            if stopped_containers and stopped_containers[0]:
                analysis["issues"].append(f"⚠️ {len(stopped_containers)} STOPPED CONTAINERS")
                analysis["recommendations"].append(f"• Remove stopped containers: docker container prune")
        
        # Check logs for errors
        error_check = execute_command("docker logs zoe-core --tail 100 2>&1 | grep -c ERROR")
        if error_check["success"] and error_check["stdout"].strip() != "0":
            error_count = error_check["stdout"].strip()
            analysis["issues"].append(f"⚠️ {error_count} ERRORS IN LOGS")
            analysis["recommendations"].append("• Review logs: docker logs zoe-core --tail 50")
        
        # Database optimization
        db_size = execute_command("du -h /app/data/zoe.db 2>/dev/null")
        if db_size["success"] and db_size["stdout"]:
            size = db_size["stdout"].split()[0]
            analysis["recommendations"].append(f"• Database size: {size} - run VACUUM if > 100MB")
        
        # If no issues found
        if not analysis["issues"]:
            analysis["issues"].append("✅ NO CRITICAL ISSUES FOUND")
            analysis["recommendations"].append("• System is running optimally")
            analysis["recommendations"].append("• Current usage is healthy")
            analysis["recommendations"].append("• Consider these preventive measures:")
            analysis["recommendations"].append("  - Set up daily log rotation")
            analysis["recommendations"].append("  - Schedule weekly docker prune")
            analysis["recommendations"].append("  - Monitor memory trends")
        
    except Exception as e:
        analysis["error"] = str(e)
    
    return analysis

@router.get("/status")
async def developer_status():
    """System status with real data"""
    system_info = get_real_system_info()
    container_count = system_info["containers"].count("Up") if isinstance(system_info["containers"], str) else 0
    
    return {
        "status": "operational",
        "mode": "intelligent-analysis",
        "personality": "Zack",
        "containers_running": container_count,
        "database_tables": len(system_info.get("database_tables", [])),
        "timestamp": datetime.now().isoformat()
    }

@router.post("/chat")
async def developer_chat(msg: DeveloperChat):
    """
    Intelligent developer chat that analyzes real data
    """
    
    message_lower = msg.message.lower()
    response_parts = []
    
    # OPTIMIZATION REQUESTS - Analyze real system
    if any(word in message_lower for word in ['optimize', 'performance', 'slow', 'improve', 'faster']):
        analysis = analyze_for_optimization()
        
        response_parts.append("**🔍 REAL SYSTEM ANALYSIS**")
        response_parts.append("")
        response_parts.append("**Current Metrics:**")
        response_parts.append(f"• CPU Usage: {analysis['metrics'].get('cpu_percent', 'N/A')}%")
        response_parts.append(f"• Memory Usage: {analysis['metrics'].get('memory_percent', 'N/A')}%")
        response_parts.append(f"• Disk Usage: {analysis['metrics'].get('disk_percent', 'N/A')}%")
        response_parts.append(f"• Memory Available: {analysis['metrics'].get('memory_available_gb', 'N/A')}GB")
        response_parts.append(f"• Disk Free: {analysis['metrics'].get('disk_free_gb', 'N/A')}GB")
        response_parts.append("")
        response_parts.append("**Issues Found:**")
        for issue in analysis['issues']:
            response_parts.append(issue)
        response_parts.append("")
        response_parts.append("**Specific Recommendations:**")
        for rec in analysis['recommendations']:
            response_parts.append(rec)
        
        # Add immediate actions
        response_parts.append("")
        response_parts.append("**🚀 Immediate Actions You Can Take:**")
        response_parts.append("```bash")
        response_parts.append("# 1. Check resource usage per container")
        response_parts.append("docker stats --no-stream")
        response_parts.append("")
        response_parts.append("# 2. Clean up Docker resources")
        response_parts.append("docker system prune -a --volumes")
        response_parts.append("")
        response_parts.append("# 3. Restart heavy containers")
        response_parts.append("docker restart zoe-ollama")
        response_parts.append("```")
    
    # Docker queries - existing functionality
    elif any(word in message_lower for word in ['docker', 'container', 'service']):
        result = execute_command("docker ps --format 'table {{.Names}}\t{{.Status}}'")
        if result["success"]:
            response_parts.append("**Real Container Status:**")
            response_parts.append(f"```\n{result['stdout']}\n```")
    
    # Memory queries
    elif any(word in message_lower for word in ['memory', 'ram', 'mem']):
        result = execute_command("free -h")
        if result["success"]:
            response_parts.append("**Real Memory Usage:**")
            response_parts.append(f"```\n{result['stdout']}\n```")
        mem_percent = execute_command("free | grep Mem | awk '{printf \"%.1f%% used (%.1fGB of %.1fGB)\", $3/$2*100, $3/1024/1024, $2/1024/1024}'")
        if mem_percent["success"]:
            response_parts.append(f"\n**Summary:** {mem_percent['stdout']}")
    
    # CPU queries
    elif any(word in message_lower for word in ['cpu', 'processor', 'load']):
        result = execute_command("top -bn1 | head -10")
        if result["success"]:
            response_parts.append("**Real CPU Status:**")
            response_parts.append(f"```\n{result['stdout']}\n```")
    
    # Disk queries
    elif any(word in message_lower for word in ['disk', 'storage', 'space']):
        result = execute_command("df -h")
        if result["success"]:
            response_parts.append("**Real Disk Usage:**")
            response_parts.append(f"```\n{result['stdout']}\n```")
    
    # Log queries
    elif any(word in message_lower for word in ['log', 'error', 'debug']):
        container = "zoe-core"
        if "ui" in message_lower:
            container = "zoe-ui"
        elif "ollama" in message_lower:
            container = "zoe-ollama"
        
        result = execute_command(f"docker logs {container} --tail 20 2>&1")
        if result["success"]:
            response_parts.append(f"**Recent Logs from {container}:**")
            response_parts.append(f"```\n{result['stdout']}\n```")
    
    # System health
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
    
    # Default
    else:
        response_parts.append("**I'm Zack with REAL system analysis. I can:**")
        response_parts.append("• **Optimize performance** - Analyze and fix bottlenecks")
        response_parts.append("• Show docker containers")
        response_parts.append("• Check memory usage")
        response_parts.append("• View CPU status")
        response_parts.append("• Check disk space")
        response_parts.append("• Show recent logs")
        response_parts.append("• Get system health")
        response_parts.append("\n**Try:** 'optimize performance' for real analysis!")
    
    return {"response": "\n".join(response_parts)}

# Keep all other endpoints (execute, metrics, tasks) as they were...
@router.post("/execute")
async def execute_direct(command: str):
    """Direct command execution endpoint"""
    result = execute_command(command)
    return {
        "command": command,
        "output": result["stdout"],
        "error": result["stderr"],
        "success": result["success"]
    }

@router.get("/metrics")
async def get_metrics():
    """Real-time system metrics"""
    try:
        cpu_percent = psutil.cpu_percent(interval=1)
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
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
    return {"task_id": task_id, "status": "created"}

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
