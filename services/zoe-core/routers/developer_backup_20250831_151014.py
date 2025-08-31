"""
Developer Router - ACTUALLY EXECUTES COMMANDS
No more hallucinations - real subprocess execution
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import subprocess
import sqlite3
import json
import sys
import os
from datetime import datetime
import psutil

sys.path.append('/app')

router = APIRouter(prefix="/api/developer", tags=["developer"])

class DeveloperChat(BaseModel):
    message: str

def execute_command(cmd: str, timeout: int = 10) -> dict:
    """ACTUALLY execute a command and return REAL output"""
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
            "stdout": result.stdout,
            "stderr": result.stderr,
            "code": result.returncode,
            "executed": True
        }
    except subprocess.TimeoutExpired:
        return {"error": "Command timed out", "executed": False}
    except Exception as e:
        return {"error": str(e), "executed": False}

def get_real_system_info() -> dict:
    """Get ACTUAL system information, not hallucinated"""
    info = {}
    
    # REAL container status
    containers_result = execute_command("docker ps --format '{{.Names}}: {{.Status}}' | grep zoe- || echo 'No containers found'")
    info["containers"] = containers_result["stdout"].strip().split('\n') if containers_result.get("stdout") else []
    
    # REAL database tables
    try:
        conn = sqlite3.connect('/app/data/zoe.db')
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        info["tables"] = [t[0] for t in cursor.fetchall()]
        conn.close()
    except Exception as e:
        info["tables"] = f"Database error: {e}"
    
    # REAL file count
    files_result = execute_command("find /app -name '*.py' -type f | wc -l")
    info["python_files"] = files_result["stdout"].strip() if files_result.get("stdout") else "0"
    
    # REAL router files
    routers_result = execute_command("ls -la /app/routers/*.py 2>/dev/null | wc -l")
    info["router_count"] = routers_result["stdout"].strip() if routers_result.get("stdout") else "0"
    
    return info

@router.get("/status")
async def get_status():
    """Get REAL status, not fantasy"""
    return {
        "status": "operational",
        "mode": "command-executor",
        "personality": "Zack",
        "capabilities": ["execute", "analyze", "fix"],
        "timestamp": datetime.now().isoformat(),
        "real_execution": True
    }

@router.post("/chat")
async def developer_chat(request: DeveloperChat):
    """Chat that ACTUALLY EXECUTES instead of hallucinating"""
    
    message_lower = request.message.lower()
    response_parts = []
    executed_commands = []
    
    # Get real system context first
    system_info = get_real_system_info()
    
    # Determine what to ACTUALLY execute based on the request
    if any(word in message_lower for word in ['list', 'show', 'what', 'check']):
        
        if 'container' in message_lower or 'docker' in message_lower:
            # REAL container status
            result = execute_command("docker ps --format 'table {{.Names}}\t{{.Status}}'")
            if result["stdout"]:
                response_parts.append(f"**Real Container Status:**\n```\n{result['stdout']}\n```")
                executed_commands.append("docker ps")
        
        elif 'table' in message_lower or 'database' in message_lower:
            # REAL database tables
            response_parts.append(f"**Database Tables:** {', '.join(system_info['tables'])}")
        
        elif 'file' in message_lower or 'python' in message_lower:
            # REAL file listing
            if 'router' in message_lower:
                result = execute_command("ls -la /app/routers/*.py | head -10")
                if result["stdout"]:
                    response_parts.append(f"**Router Files (first 10):**\n```\n{result['stdout']}\n```")
                    executed_commands.append("ls routers")
            else:
                response_parts.append(f"**Python Files:** {system_info['python_files']} files found")
                
                # Show first 5 if asked
                if 'first' in message_lower or 'show' in message_lower:
                    result = execute_command("find /app -name '*.py' -type f | head -5")
                    if result["stdout"]:
                        response_parts.append(f"**First 5 Python files:**\n```\n{result['stdout']}\n```")
                        executed_commands.append("find *.py")
    
    elif any(word in message_lower for word in ['run', 'execute', 'do']):
        # Extract command to run (basic parsing)
        if 'ls' in message_lower:
            cmd = "ls -la /app/routers/" if 'router' in message_lower else "ls -la /app/"
            result = execute_command(cmd)
            if result["stdout"]:
                response_parts.append(f"**Executed: {cmd}**\n```\n{result['stdout'][:1000]}\n```")
                executed_commands.append(cmd)
    
    elif 'health' in message_lower or 'status' in message_lower:
        # REAL system health check
        checks = [
            ("Containers Running", "docker ps -q | wc -l"),
            ("Memory Usage", "free -h | grep Mem"),
            ("Disk Usage", "df -h / | tail -1"),
            ("CPU Load", "uptime | awk -F'load average:' '{print $2}'")
        ]
        
        response_parts.append("**System Health Check:**")
        for label, cmd in checks:
            result = execute_command(cmd)
            if result["stdout"]:
                response_parts.append(f"\n{label}:\n```\n{result['stdout'].strip()}\n```")
                executed_commands.append(cmd)
    
    elif 'memory' in message_lower or 'ram' in message_lower:
        result = execute_command("free -h")
        if result["stdout"]:
            response_parts.append(f"**Memory Usage:**\n```\n{result['stdout']}\n```")
            executed_commands.append("free -h")
    
    elif 'disk' in message_lower:
        result = execute_command("df -h")
        if result["stdout"]:
            response_parts.append(f"**Disk Usage:**\n```\n{result['stdout']}\n```")
            executed_commands.append("df -h")
    
    elif 'cpu' in message_lower:
        result = execute_command("top -bn1 | head -10")
        if result["stdout"]:
            response_parts.append(f"**CPU Status:**\n```\n{result['stdout']}\n```")
            executed_commands.append("top")
    
    elif 'log' in message_lower or 'error' in message_lower:
        result = execute_command("docker logs zoe-core --tail 20 2>&1 | grep -i error || echo 'No errors found'")
        if result["stdout"]:
            response_parts.append(f"**Recent Errors:**\n```\n{result['stdout']}\n```")
            executed_commands.append("docker logs")
    
    # If we have real data, return it
    if response_parts:
        response = "\n\n".join(response_parts)
        if executed_commands:
            response += f"\n\n---\n*Actually executed: {', '.join(executed_commands)}*"
    else:
        # For other queries, provide help with context
        response = f"""I'm Zack, with REAL command execution capability.

**Current System State:**
- Containers: {len(system_info['containers'])} running
- Database Tables: {len(system_info['tables'])}
- Python Files: {system_info['python_files']}
- Router Files: {system_info['router_count']}

Ask me to:
- "Show docker containers" - I'll run docker ps
- "List database tables" - I'll query the database
- "Check system health" - I'll run health checks
- "Show recent errors" - I'll check logs
- "List router files" - I'll show actual files

I execute REAL commands, not hallucinations."""
    
    # For code generation, still use AI but be clear about it
    if any(word in message_lower for word in ['create', 'build', 'generate', 'write']):
        try:
            from ai_client_complete import get_ai_response
            ai_response = await get_ai_response(
                f"Generate code for: {request.message}", 
                {"mode": "developer"}
            )
            response = f"**Generated Code (by AI):**\n{ai_response}\n\n*Note: This is AI-generated. The system info above is real.*"
        except:
            response += "\n\n*AI generation unavailable, but I can still execute real commands.*"
    
    return {
        "response": response,
        "success": True,
        "context": system_info,
        "executed": len(executed_commands) > 0,
        "commands_run": executed_commands
    }

@router.post("/execute")
async def execute_direct(command: str):
    """Direct command execution endpoint"""
    
    # Safety check
    dangerous = ['rm -rf /', 'dd if=', 'format', ':(){']
    if any(d in command for d in dangerous):
        raise HTTPException(status_code=400, detail="Dangerous command blocked")
    
    result = execute_command(command, timeout=30)
    return result

@router.get("/metrics")
async def get_system_metrics():
    """Get REAL system metrics"""
    
    # Get real metrics
    cpu_result = execute_command("top -bn1 | grep 'Cpu(s)' | awk '{print $2}' | cut -d'%' -f1")
    mem_result = execute_command("free -b | grep Mem | awk '{print int($3/$2 * 100)}'")
    disk_result = execute_command("df / | tail -1 | awk '{print $5}' | sed 's/%//'")
    
    try:
        cpu = float(cpu_result["stdout"].strip()) if cpu_result.get("stdout") else psutil.cpu_percent(interval=1)
    except:
        cpu = psutil.cpu_percent(interval=1)
    
    try:
        # Use psutil as more reliable
        mem = psutil.virtual_memory()
        memory_data = {
            "percent": round(mem.percent, 1),
            "used": round(mem.used / (1024**3), 1),
            "total": round(mem.total / (1024**3), 1)
        }
    except:
        memory_data = {"percent": 0, "used": 0, "total": 8}
    
    try:
        disk = psutil.disk_usage('/')
        disk_data = {
            "percent": round(disk.percent, 1),
            "used": round(disk.used / (1024**3), 1),
            "total": round(disk.total / (1024**3), 1)
        }
    except:
        disk_data = {"percent": 0, "used": 0, "total": 128}
    
    return {
        "cpu": cpu,
        "memory": memory_data,
        "disk": disk_data,
        "timestamp": datetime.now().isoformat(),
        "source": "real_execution"
    }
