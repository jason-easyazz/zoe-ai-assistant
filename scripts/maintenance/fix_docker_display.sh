#!/bin/bash
# FIX_DOCKER_DISPLAY.sh - Make Docker commands actually show containers

echo "🐳 FIXING DOCKER CONTAINER DISPLAY"
echo "==================================="

cd /home/pi/zoe

# Backup current developer.py
echo "📦 Backing up current developer.py..."
cp services/zoe-core/routers/developer.py services/zoe-core/routers/developer.backup_$(date +%Y%m%d_%H%M%S)

# Create a working version that shows Docker containers
echo "🔧 Creating fixed developer.py..."
cat > services/zoe-core/routers/developer_fixed.py << 'PYTHON_EOF'
"""Developer Router with Working Docker Display"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import subprocess
import json
import os
from typing import Optional, Dict, List

router = APIRouter(prefix="/api/developer", tags=["developer"])

class ChatMessage(BaseModel):
    message: str

class CommandRequest(BaseModel):
    command: str
    safe_mode: bool = True
    timeout: int = 30

class FileRequest(BaseModel):
    path: str
    content: Optional[str] = None

def execute_command(cmd: str, timeout: int = 30, cwd: str = None) -> dict:
    """Execute system command and return results"""
    try:
        if cwd is None:
            cwd = "/home/pi/zoe" if os.path.exists("/home/pi/zoe") else "/app"
        
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd
        )
        return {
            "success": result.returncode == 0,
            "stdout": result.stdout[:10000],
            "stderr": result.stderr[:5000],
            "code": result.returncode,
            "result": {
                "stdout": result.stdout[:10000],
                "stderr": result.stderr[:5000],
                "return_code": result.returncode
            }
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "stdout": "", "stderr": "Command timed out", "code": -1}
    except Exception as e:
        return {"success": False, "stdout": "", "stderr": str(e), "code": -1}

@router.post("/chat")
async def developer_chat(msg: ChatMessage):
    """Developer chat with ACTUAL Docker output"""
    
    message_lower = msg.message.lower()
    
    # Check for Docker/container queries
    if any(word in message_lower for word in ['docker', 'container', 'service', 'status']):
        # Get REAL Docker status
        docker_result = execute_command("docker ps -a --format 'table {{.Names}}\t{{.Status}}'")
        
        if docker_result["success"]:
            # Parse the Docker output
            lines = docker_result["stdout"].strip().split('\n')
            
            # Separate running and stopped
            running = []
            stopped = []
            
            for line in lines[1:]:  # Skip header
                if line and '\t' in line:
                    name, status = line.split('\t', 1)
                    if 'Up' in status:
                        running.append(f"• **{name}**: {status}")
                    else:
                        stopped.append(f"• **{name}**: {status}")
            
            # Build response
            response = "**🐳 Docker Container Status:**\n\n"
            
            if running:
                response += f"**✅ Running ({len(running)}):**\n"
                response += "\n".join(running) + "\n"
            else:
                response += "**⚠️ No containers running!**\n"
            
            if stopped:
                response += f"\n**🔴 Stopped ({len(stopped)}):**\n"
                response += "\n".join(stopped) + "\n"
                
                # Offer to restart
                response += "\n**🔧 To restart stopped containers:**\n"
                for line in stopped:
                    container_name = line.split('**')[1]
                    response += f"`docker restart {container_name}`\n"
            
            # Add summary
            total = len(running) + len(stopped)
            response += f"\n**📊 Summary:** {len(running)}/{total} containers running"
            
            return {"response": response, "executed": True}
    
    # Check for system health
    elif any(word in message_lower for word in ['health', 'system', 'check']):
        # Get comprehensive system info
        docker_cmd = execute_command("docker ps --format '{{.Names}}: {{.Status}}'")
        memory_cmd = execute_command("free -h | head -2")
        disk_cmd = execute_command("df -h / | tail -1")
        uptime_cmd = execute_command("uptime")
        
        response = "**🏥 System Health Check:**\n\n"
        
        # Docker status
        response += "**Docker Services:**\n```\n"
        response += docker_cmd["stdout"] if docker_cmd["success"] else "Unable to check"
        response += "\n```\n\n"
        
        # Memory
        response += "**Memory Usage:**\n```\n"
        response += memory_cmd["stdout"] if memory_cmd["success"] else "Unable to check"
        response += "\n```\n\n"
        
        # Disk
        response += "**Disk Usage:**\n```\n"
        response += disk_cmd["stdout"] if disk_cmd["success"] else "Unable to check"
        response += "\n```\n\n"
        
        # Uptime
        response += "**System Uptime:**\n```\n"
        response += uptime_cmd["stdout"] if uptime_cmd["success"] else "Unable to check"
        response += "\n```"
        
        return {"response": response, "executed": True}
    
    # Check for memory queries
    elif any(word in message_lower for word in ['memory', 'ram']):
        mem_result = execute_command("free -h")
        response = "**💾 Memory Status:**\n```\n" + mem_result["stdout"] + "\n```"
        return {"response": response, "executed": True}
    
    # Check for disk queries
    elif any(word in message_lower for word in ['disk', 'storage', 'space']):
        disk_result = execute_command("df -h")
        response = "**💿 Disk Usage:**\n```\n" + disk_result["stdout"] + "\n```"
        return {"response": response, "executed": True}
    
    # Default response
    else:
        return {
            "response": """**I'm Zack, your autonomous developer with FULL system access!**

I can help you with:
- **Docker Management**: Show/restart containers
- **System Health**: Check memory, disk, CPU
- **Execute Commands**: Run any system command
- **Fix Issues**: Detect and repair problems
- **View Logs**: Check container logs

Try asking:
- "Show all docker containers"
- "Check system health"
- "Show memory usage"
- "Display disk space"

Or use `/execute <command>` to run any command directly!""",
            "executed": False
        }

@router.post("/execute")
async def execute_direct(cmd: CommandRequest):
    """Execute command directly"""
    result = execute_command(cmd.command, cmd.timeout)
    return result

@router.get("/status")
async def get_status():
    """Get developer status with REAL container count"""
    
    # Get actual container count
    docker_result = execute_command("docker ps -q | wc -l")
    try:
        running_count = int(docker_result["stdout"].strip()) if docker_result["success"] else 0
    except:
        running_count = 0
    
    # Get memory
    mem_result = execute_command("free -b | grep Mem | awk '{print int($3/$2 * 100)}'")
    try:
        memory_percent = float(mem_result["stdout"].strip()) if mem_result["success"] else 0
    except:
        memory_percent = 0
    
    # Get disk
    disk_result = execute_command("df / | tail -1 | awk '{print int($5)}'")
    try:
        disk_percent = float(disk_result["stdout"].strip()) if disk_result["success"] else 0
    except:
        disk_percent = 0
    
    return {
        "status": "operational",
        "mode": "FULL_ACCESS",
        "metrics": {
            "containers": running_count,
            "containers_running": running_count,
            "memory_percent": memory_percent,
            "disk_percent": disk_percent
        },
        "abilities": {
            "docker_management": True,
            "file_system_access": True,
            "command_execution": True,
            "project_awareness": True,
            "auto_fix": True
        }
    }
PYTHON_EOF

# Replace the current developer.py with the fixed version
echo "📝 Applying fix..."
cp services/zoe-core/routers/developer_fixed.py services/zoe-core/routers/developer.py

# Restart the service
echo "🔄 Restarting zoe-core..."
docker restart zoe-core

echo "⏳ Waiting for service to start (10 seconds)..."
sleep 10

# Test the fix
echo ""
echo "🧪 TESTING DOCKER DISPLAY:"
echo "=========================="
curl -X POST http://localhost:8000/api/developer/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "show all docker containers"}' 2>/dev/null | jq -r '.response'

echo ""
echo "🧪 TESTING STATUS ENDPOINT:"
echo "==========================="
curl -s http://localhost:8000/api/developer/status | jq '.'

echo ""
echo "✅ FIX COMPLETE!"
echo ""
echo "The developer dashboard should now show:"
echo "  • All Docker containers with their actual status"
echo "  • Real container counts"
echo "  • Accurate system metrics"
echo ""
echo "If any issues, restore with:"
echo "  cp services/zoe-core/routers/developer.backup_* services/zoe-core/routers/developer.py"
echo "  docker restart zoe-core"
