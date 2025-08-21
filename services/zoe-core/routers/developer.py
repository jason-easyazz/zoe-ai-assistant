"""
Developer API Router - Claude Integration for Development Tasks
Provides distinct AI personality and capabilities for development
"""

from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
import subprocess
import json
import os
import psutil
import docker
from datetime import datetime
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/developer", tags=["developer"])

# Developer Claude System Prompt
DEVELOPER_SYSTEM_PROMPT = """
You are Claude, a senior DevOps engineer and development assistant for the Zoe AI system.

Your personality:
- Technical, precise, and solution-focused
- You provide complete, working terminal scripts
- You explain complex issues clearly
- You think defensively and consider edge cases
- You're proactive about preventing issues

Your capabilities:
- Generate bash scripts and Python code
- Diagnose and fix system issues
- Optimize performance and resource usage
- Manage Docker containers and services
- Handle Git operations and backups
- Analyze logs and errors

Your approach:
- Always provide complete, executable scripts (not fragments)
- Include error handling and rollback strategies
- Test commands before suggesting them
- Document what each command does
- Prioritize system stability and data safety

Current system:
- Platform: Raspberry Pi 5 (ARM64, 8GB RAM)
- Location: /home/pi/zoe
- Containers: zoe-core, zoe-ui, zoe-ollama, zoe-redis
- Main API: Port 8000, UI: Port 8080

Remember: You're helping a developer maintain and improve Zoe. Be technical but clear.
"""

# Safe command whitelist for execution
SAFE_COMMANDS = [
    "docker ps",
    "docker logs",
    "docker stats",
    "git status",
    "git log",
    "df -h",
    "free -m",
    "uptime",
    "systemctl status",
    "curl http://localhost:8000/health",
    "ls -la",
    "cat CLAUDE_CURRENT_STATE.md"
]

class DeveloperChatRequest(BaseModel):
    message: str
    context: Optional[Dict[str, Any]] = {}

class DeveloperChatResponse(BaseModel):
    response: str
    actions: Optional[List[Dict[str, Any]]] = []
    script: Optional[str] = None

class SystemCommand(BaseModel):
    command: str
    safe_mode: bool = True

@router.get("/status")
async def developer_status():
    """Check if developer services are online"""
    return {
        "status": "online",
        "mode": "developer",
        "capabilities": ["chat", "execute", "monitor", "backup"],
        "claude_available": True
    }

@router.post("/chat")
async def developer_chat(request: DeveloperChatRequest):
    """
    Developer-specific chat with Claude
    Uses technical personality and has access to system commands
    """
    try:
        # Import the Ollama client
        from ..ai_client import get_ai_response
        
        # Prepare developer context
        developer_context = {
            "mode": "developer",
            "system_prompt": DEVELOPER_SYSTEM_PROMPT,
            "capabilities": ["execute_scripts", "system_analysis", "code_generation"],
            **request.context
        }
        
        # Get response from AI with developer personality
        response = await get_ai_response(
            message=request.message,
            system_prompt=DEVELOPER_SYSTEM_PROMPT,
            context=developer_context,
            temperature=0.3  # Lower temperature for more deterministic technical responses
        )
        
        # Extract any scripts from the response
        script = None
        if "```bash" in response or "```python" in response:
            # Extract script for easy execution
            import re
            script_match = re.search(r'```(?:bash|python)\n(.*?)```', response, re.DOTALL)
            if script_match:
                script = script_match.group(1)
        
        # Determine if any actions should be suggested
        actions = []
        if "fix" in request.message.lower() or "repair" in request.message.lower():
            actions.append({
                "type": "execute",
                "label": "🔧 Run Fix Script",
                "script": script
            })
        
        if "backup" in request.message.lower():
            actions.append({
                "type": "backup",
                "label": "💾 Create Backup",
                "command": "backup_system"
            })
        
        return DeveloperChatResponse(
            response=response,
            actions=actions,
            script=script
        )
        
    except Exception as e:
        logger.error(f"Developer chat error: {e}")
        # Fallback response when AI is unavailable
        return DeveloperChatResponse(
            response=f"I encountered an error: {str(e)}\n\nAs a fallback, here are some debugging steps:\n1. Check if all containers are running: `docker ps`\n2. Check API health: `curl http://localhost:8000/health`\n3. Review logs: `docker logs zoe-core --tail 50`",
            actions=[{
                "type": "diagnostic",
                "label": "🔍 Run Diagnostics",
                "command": "run_diagnostics"
            }]
        )

@router.get("/system/status")
async def get_system_status():
    """Get comprehensive system status"""
    try:
        client = docker.from_env()
        
        # Check container status
        container_status = {}
        for container in client.containers.list(all=True):
            if container.name.startswith('zoe-'):
                service = container.name.replace('zoe-', '')
                container_status[service] = 'healthy' if container.status == 'running' else 'error'
        
        # Get system metrics
        cpu_percent = psutil.cpu_percent(interval=1)
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        
        return {
            **container_status,
            "metrics": {
                "cpu": cpu_percent,
                "memory": memory.percent,
                "disk": disk.percent,
                "uptime": datetime.now().isoformat()
            }
        }
    except Exception as e:
        logger.error(f"System status error: {e}")
        return {
            "error": str(e),
            "core": "unknown",
            "ui": "unknown",
            "ollama": "unknown",
            "redis": "unknown"
        }

@router.post("/execute")
async def execute_command(command: SystemCommand):
    """
    Execute safe system commands
    Only whitelisted commands are allowed in safe mode
    """
    try:
        # Check if command is safe
        if command.safe_mode:
            if not any(command.command.startswith(safe) for safe in SAFE_COMMANDS):
                raise HTTPException(
                    status_code=403,
                    detail=f"Command not in safe list. Allowed commands: {SAFE_COMMANDS}"
                )
        
        # Execute command
        result = subprocess.run(
            command.command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=30,
            cwd="/home/pi/zoe"
        )
        
        return {
            "success": result.returncode == 0,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "return_code": result.returncode
        }
        
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "Command timeout"}
    except Exception as e:
        return {"success": False, "error": str(e)}

@router.get("/tasks/recent")
async def get_recent_tasks():
    """Get recent developer tasks"""
    # This would connect to a task database in production
    return [
        {"title": "System initialized", "status": "completed", "time": "2 min ago"},
        {"title": "Developer UI deployed", "status": "completed", "time": "5 min ago"},
        {"title": "Backend endpoints created", "status": "running", "time": "now"},
    ]

@router.get("/metrics")
async def get_performance_metrics():
    """Get system performance metrics"""
    try:
        cpu = psutil.cpu_percent(interval=1)
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        
        # Calculate uptime
        boot_time = datetime.fromtimestamp(psutil.boot_time())
        uptime = datetime.now() - boot_time
        hours = int(uptime.total_seconds() // 3600)
        
        return {
            "cpu": f"{cpu:.1f}",
            "memory": f"{memory.percent:.1f}",
            "disk": f"{disk.percent:.1f}",
            "uptime": f"{hours}h"
        }
    except Exception as e:
        logger.error(f"Metrics error: {e}")
        return {
            "cpu": "0",
            "memory": "0", 
            "disk": "0",
            "uptime": "0h"
        }

@router.post("/backup")
async def create_backup(background_tasks: BackgroundTasks):
    """Create system backup"""
    def run_backup():
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = f"/home/pi/zoe/backups/backup_{timestamp}"
        
        commands = [
            f"mkdir -p {backup_path}",
            f"cp -r /home/pi/zoe/services {backup_path}/",
            f"cp /home/pi/zoe/docker-compose.yml {backup_path}/",
            f"docker exec zoe-core python -c 'import sqlite3; conn=sqlite3.connect(\"/app/data/zoe.db\"); conn.backup(open(\"/app/data/zoe_backup.db\", \"wb\"))'",
            f"cp /home/pi/zoe/data/zoe_backup.db {backup_path}/",
            f"echo 'Backup created at {timestamp}' >> {backup_path}/backup.log"
        ]
        
        for cmd in commands:
            subprocess.run(cmd, shell=True)
    
    background_tasks.add_task(run_backup)
    return {"status": "Backup started", "message": "Check /backups folder in a few moments"}

# Health check endpoint
@router.get("/health")
async def developer_health():
    """Health check for developer services"""
    return {
        "status": "healthy",
        "service": "developer",
        "timestamp": datetime.now().isoformat()
    }
