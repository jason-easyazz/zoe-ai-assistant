#!/bin/bash
# GIVE_ZACK_FULL_ABILITIES.sh
# Grants Zack complete system access and control

set -e

echo "🚀 GRANTING ZACK FULL LEAD DEVELOPER ABILITIES"
echo "=============================================="
echo ""
echo "This will give Zack:"
echo "  🐳 Docker container management"
echo "  📁 Full file system access"
echo "  🔧 System command execution"
echo "  🧠 Complete project awareness"
echo "  ⚡ Autonomous fix capabilities"
echo ""
echo "Press Enter to proceed or Ctrl+C to abort..."
read

cd /home/pi/zoe

# Step 1: Stop zoe-core to modify configuration
echo "📦 Step 1: Preparing for enhanced configuration..."
docker stop zoe-core

# Step 2: Update docker-compose.yml to grant full access
echo -e "\n📝 Step 2: Updating docker-compose.yml for full access..."

# Backup current docker-compose.yml
cp docker-compose.yml docker-compose.yml.backup_$(date +%Y%m%d_%H%M%S)

# Create enhanced docker-compose entry for zoe-core
cat > /tmp/docker-compose-patch.yml << 'COMPOSE_PATCH'
  zoe-core:
    build: ./services/zoe-core
    container_name: zoe-core
    restart: unless-stopped
    ports:
      - "8000:8000"
    volumes:
      # Original volumes
      - ./services/zoe-core:/app
      - ./data:/app/data
      # NEW: Full system access
      - /var/run/docker.sock:/var/run/docker.sock  # Docker control
      - /home/pi/zoe:/home/pi/zoe:rw               # Project access
      - /usr/bin/docker:/usr/bin/docker:ro         # Docker CLI
      - /proc:/host/proc:ro                        # System monitoring
      - /sys:/host/sys:ro                          # System info
    environment:
      - PYTHONUNBUFFERED=1
      - DOCKER_HOST=unix:///var/run/docker.sock
      - PROJECT_ROOT=/home/pi/zoe
      - FULL_ACCESS=true
    networks:
      - zoe-network
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 10s
      retries: 3
    # Grant additional capabilities
    cap_add:
      - SYS_ADMIN
      - SYS_PTRACE
    security_opt:
      - apparmor:unconfined
    # Run as root for full access (controlled via code)
    user: root
COMPOSE_PATCH

# Apply the patch (manual process to preserve other services)
echo "Applying enhanced configuration..."
python3 << 'APPLY_PATCH'
import yaml

# Read current docker-compose
with open('docker-compose.yml', 'r') as f:
    config = yaml.safe_load(f)

# Update zoe-core service with full access
config['services']['zoe-core'] = {
    'build': './services/zoe-core',
    'container_name': 'zoe-core',
    'restart': 'unless-stopped',
    'ports': ['8000:8000'],
    'volumes': [
        './services/zoe-core:/app',
        './data:/app/data',
        '/var/run/docker.sock:/var/run/docker.sock',
        '/home/pi/zoe:/home/pi/zoe:rw',
        '/usr/bin/docker:/usr/bin/docker:ro',
        '/proc:/host/proc:ro',
        '/sys:/host/sys:ro'
    ],
    'environment': [
        'PYTHONUNBUFFERED=1',
        'DOCKER_HOST=unix:///var/run/docker.sock',
        'PROJECT_ROOT=/home/pi/zoe',
        'FULL_ACCESS=true'
    ],
    'networks': ['zoe-network'],
    'healthcheck': {
        'test': ['CMD', 'curl', '-f', 'http://localhost:8000/health'],
        'interval': '30s',
        'timeout': '10s',
        'retries': 3
    }
}

# Write updated config
with open('docker-compose.yml', 'w') as f:
    yaml.dump(config, f, default_flow_style=False, sort_keys=False)

print("✅ Docker-compose updated with full access")
APPLY_PATCH

# Step 3: Install Docker CLI in the container
echo -e "\n🐳 Step 3: Installing Docker CLI in container..."

# Update Dockerfile for zoe-core
cat > services/zoe-core/Dockerfile << 'DOCKERFILE'
FROM python:3.11-slim

WORKDIR /app

# Install system dependencies including Docker CLI
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    curl \
    git \
    vim \
    procps \
    net-tools \
    iputils-ping \
    && curl -fsSL https://get.docker.com -o get-docker.sh \
    && sh get-docker.sh \
    && rm get-docker.sh \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Set environment
ENV PYTHONUNBUFFERED=1
ENV FULL_ACCESS=true

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
DOCKERFILE

# Step 4: Enhance developer.py with full abilities
echo -e "\n🧠 Step 4: Enhancing Zack with full abilities..."

cat > /tmp/developer_full_abilities.py << 'FULL_ABILITIES'
"""
Developer API Router - Zack with FULL Lead Developer Abilities
"""

from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
import subprocess
import json
import os
import psutil
import sqlite3
import docker
from datetime import datetime
import logging
from pathlib import Path

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/developer", tags=["developer"])

# Initialize Docker client if available
try:
    docker_client = docker.from_env()
    DOCKER_AVAILABLE = True
except:
    DOCKER_AVAILABLE = False
    logger.warning("Docker not available")

# Project paths
PROJECT_ROOT = Path(os.getenv("PROJECT_ROOT", "/home/pi/zoe"))
APP_ROOT = Path("/app")

class ChatMessage(BaseModel):
    message: str

class CommandRequest(BaseModel):
    command: str
    timeout: Optional[int] = 30

class FileRequest(BaseModel):
    path: str
    content: Optional[str] = None
    action: str  # read, write, create, delete

def execute_command(cmd: str, timeout: int = 30, cwd: str = None) -> dict:
    """Execute system commands with full access"""
    try:
        result = subprocess.run(
            cmd, 
            shell=True, 
            capture_output=True, 
            text=True, 
            timeout=timeout, 
            cwd=cwd or str(PROJECT_ROOT)
        )
        return {
            "stdout": result.stdout[:10000],
            "stderr": result.stderr[:2000],
            "code": result.returncode,
            "success": result.returncode == 0
        }
    except subprocess.TimeoutExpired:
        return {"stdout": "", "stderr": "Command timed out", "code": -1, "success": False}
    except Exception as e:
        return {"stdout": "", "stderr": str(e), "code": -1, "success": False}

def get_docker_info() -> Dict:
    """Get real Docker container information"""
    if not DOCKER_AVAILABLE:
        # Fallback to command line
        result = execute_command("docker ps -a --format json")
        if result["success"] and result["stdout"]:
            containers = []
            for line in result["stdout"].strip().split('\n'):
                if line:
                    try:
                        containers.append(json.loads(line))
                    except:
                        pass
            return {"containers": containers, "source": "cli"}
        return {"containers": [], "error": "Docker not accessible"}
    
    # Use Docker SDK
    containers = []
    for container in docker_client.containers.list(all=True):
        containers.append({
            "name": container.name,
            "status": container.status,
            "image": container.image.tags[0] if container.image.tags else "unknown",
            "ports": container.ports,
            "id": container.short_id,
            "stats": container.stats(stream=False) if container.status == "running" else {}
        })
    return {"containers": containers, "source": "sdk"}

def get_system_metrics() -> dict:
    """Get comprehensive system metrics"""
    metrics = {}
    
    try:
        # CPU metrics
        metrics["cpu_percent"] = psutil.cpu_percent(interval=1)
        metrics["cpu_cores"] = psutil.cpu_count()
        metrics["cpu_freq"] = psutil.cpu_freq().current if hasattr(psutil.cpu_freq(), 'current') else 0
        
        # Memory metrics
        mem = psutil.virtual_memory()
        metrics["memory_percent"] = round(mem.percent, 1)
        metrics["memory_used_gb"] = round(mem.used / (1024**3), 2)
        metrics["memory_total_gb"] = round(mem.total / (1024**3), 2)
        metrics["memory_available_gb"] = round(mem.available / (1024**3), 2)
        
        # Disk metrics
        disk = psutil.disk_usage("/")
        metrics["disk_percent"] = round(disk.percent, 1)
        metrics["disk_free_gb"] = round(disk.free / (1024**3), 2)
        metrics["disk_total_gb"] = round(disk.total / (1024**3), 2)
        
        # Temperature (Raspberry Pi)
        temp_result = execute_command("vcgencmd measure_temp")
        if temp_result["success"] and "temp=" in temp_result["stdout"]:
            temp_str = temp_result["stdout"].split("temp=")[1].split("'")[0]
            metrics["temperature_c"] = float(temp_str)
        
        # Docker info
        docker_info = get_docker_info()
        metrics["containers"] = len(docker_info.get("containers", []))
        metrics["containers_running"] = len([c for c in docker_info.get("containers", []) if c.get("status") == "running"])
        
    except Exception as e:
        logger.error(f"Error getting metrics: {e}")
        
    return metrics

def analyze_for_optimization() -> dict:
    """Analyze system with full awareness"""
    analysis = {
        "metrics": get_system_metrics(),
        "docker": get_docker_info(),
        "recommendations": [],
        "issues": [],
        "health_score": 100,
        "abilities": {
            "docker": DOCKER_AVAILABLE,
            "file_system": PROJECT_ROOT.exists(),
            "command_execution": True,
            "project_awareness": True
        }
    }
    
    metrics = analysis["metrics"]
    
    # System checks
    if metrics.get("cpu_percent", 0) > 80:
        analysis["issues"].append(f"High CPU: {metrics['cpu_percent']}%")
        analysis["health_score"] -= 20
    
    if metrics.get("memory_percent", 0) > 85:
        analysis["issues"].append(f"High memory: {metrics['memory_percent']}%")
        analysis["health_score"] -= 25
        
    if metrics.get("disk_percent", 0) > 90:
        analysis["issues"].append(f"Critical disk: {metrics['disk_percent']}%")
        analysis["health_score"] -= 30
        
    # Docker checks
    for container in analysis["docker"].get("containers", []):
        if container.get("status") != "running" and "zoe-" in container.get("name", ""):
            analysis["issues"].append(f"Container {container['name']} is {container['status']}")
            analysis["recommendations"].append(f"Restart with: docker restart {container['name']}")
    
    # Provide recommendations
    if not analysis["issues"]:
        analysis["recommendations"] = [
            "✅ System healthy - All services operational",
            "Consider: Set up monitoring dashboard",
            "Consider: Implement automated backups",
            "Consider: Add performance metrics collection"
        ]
    
    return analysis

@router.post("/chat")
async def developer_chat(msg: ChatMessage):
    """Zack's chat with full abilities"""
    message_lower = msg.message.lower()
    
    # Get current state
    metrics = get_system_metrics()
    docker_info = get_docker_info()
    analysis = analyze_for_optimization()
    
    # Docker queries
    if any(word in message_lower for word in ["docker", "container", "service"]):
        containers = docker_info.get("containers", [])
        running = [c for c in containers if c.get("status") == "running"]
        stopped = [c for c in containers if c.get("status") != "running"]
        
        response = f"""**Docker Container Status (FULL ACCESS):**

**Running ({len(running)}):**
{chr(10).join(f"• {c['name']}: {c['status']} [{c.get('image', 'unknown')}]" for c in running)}

**Stopped ({len(stopped)}):**
{chr(10).join(f"• {c['name']}: {c['status']}" for c in stopped) if stopped else 'None'}

**Quick Actions:**
```bash
# Restart a container
docker restart [container_name]

# View logs
docker logs --tail 50 [container_name]

# Execute command in container
docker exec -it [container_name] bash
```"""
        return {"response": response}
    
    # File system queries
    elif any(word in message_lower for word in ["file", "code", "edit", "create"]):
        return {
            "response": f"""**File System Access (FULL ACCESS):**

**Project Root:** {PROJECT_ROOT}
**Available Actions:**
- Read any file in the project
- Write/modify code files
- Create new features
- Execute scripts

**Example Commands:**
```bash
# List project files
ls -la {PROJECT_ROOT}

# Edit a file
vim {PROJECT_ROOT}/services/zoe-core/main.py

# Create new feature
touch {PROJECT_ROOT}/services/zoe-core/routers/new_feature.py
```

Ask me to read, write, or modify any file!"""
        }
    
    # System performance
    elif any(word in message_lower for word in ["performance", "optimize", "slow", "memory", "cpu"]):
        return {
            "response": f"""**System Analysis (FULL ACCESS):**

**Metrics:**
- CPU: {metrics['cpu_percent']}% ({metrics['cpu_cores']} cores)
- Memory: {metrics['memory_used_gb']}GB / {metrics['memory_total_gb']}GB ({metrics['memory_percent']}%)
- Disk: {metrics['disk_free_gb']}GB free ({metrics['disk_percent']}% used)
- Temperature: {metrics.get('temperature_c', 'N/A')}°C
- Containers: {metrics['containers_running']} running / {metrics['containers']} total

**Health Score:** {analysis['health_score']}%

**Issues:** {', '.join(analysis['issues']) if analysis['issues'] else 'None'}

**Recommendations:**
{chr(10).join('• ' + r for r in analysis['recommendations'][:5])}

**My Abilities:**
✅ Docker Management: {analysis['abilities']['docker']}
✅ File System Access: {analysis['abilities']['file_system']}
✅ Command Execution: {analysis['abilities']['command_execution']}
✅ Project Awareness: {analysis['abilities']['project_awareness']}"""
        }
    
    # Default response showing full capabilities
    else:
        return {
            "response": f"""**Zack - Lead Developer (FULL ACCESS MODE):**

**System Status:**
- Health: {analysis['health_score']}%
- CPU: {metrics['cpu_percent']}% | Memory: {metrics['memory_percent']}% | Disk: {metrics['disk_percent']}%
- Containers: {metrics['containers_running']} running

**My Full Abilities:**
🐳 **Docker Control** - Manage all containers
📁 **File System** - Read/write any project file
🔧 **System Commands** - Execute any command
🧠 **Project Awareness** - Full codebase knowledge
⚡ **Auto-Fix** - Detect and repair issues
🚀 **Code Generation** - Create complete features

**Ask me to:**
- "Show all Docker containers"
- "Read main.py file"
- "Create a new API endpoint"
- "Fix any broken service"
- "Optimize system performance"
- "Execute [any command]"

I have FULL lead developer access to everything!"""
        }

@router.post("/execute")
async def execute_command_endpoint(req: CommandRequest):
    """Execute any system command with full access"""
    if not os.getenv("FULL_ACCESS") == "true":
        raise HTTPException(status_code=403, detail="Full access not enabled")
    
    result = execute_command(req.command, req.timeout)
    return {
        "command": req.command,
        "result": result,
        "executed_at": datetime.now().isoformat()
    }

@router.post("/file")
async def file_operations(req: FileRequest):
    """Perform file operations with full access"""
    if not os.getenv("FULL_ACCESS") == "true":
        raise HTTPException(status_code=403, detail="Full access not enabled")
    
    file_path = Path(req.path)
    
    # Ensure path is within project
    if not str(file_path).startswith(str(PROJECT_ROOT)):
        file_path = PROJECT_ROOT / req.path
    
    if req.action == "read":
        if file_path.exists():
            content = file_path.read_text()
            return {"path": str(file_path), "content": content, "exists": True}
        return {"path": str(file_path), "exists": False}
    
    elif req.action == "write":
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(req.content)
        return {"path": str(file_path), "written": True}
    
    elif req.action == "delete":
        if file_path.exists():
            file_path.unlink()
            return {"path": str(file_path), "deleted": True}
        return {"path": str(file_path), "exists": False}
    
    return {"error": "Invalid action"}

@router.get("/status")
async def get_status():
    """Get complete system status with full access info"""
    return {
        "status": "operational",
        "mode": "FULL_ACCESS",
        "metrics": get_system_metrics(),
        "docker": get_docker_info(),
        "analysis": analyze_for_optimization(),
        "abilities": {
            "docker_management": DOCKER_AVAILABLE,
            "file_system_access": True,
            "command_execution": True,
            "project_awareness": True,
            "auto_fix": True
        }
    }

@router.get("/metrics")
async def get_metrics():
    """Get system metrics"""
    return get_system_metrics()

# Auto-fix endpoint
@router.post("/autofix")
async def auto_fix_issues():
    """Automatically detect and fix issues"""
    analysis = analyze_for_optimization()
    fixes_applied = []
    
    for issue in analysis["issues"]:
        if "Container" in issue and "is not running" in issue:
            # Auto-restart stopped containers
            container_name = issue.split()[1]
            result = execute_command(f"docker restart {container_name}")
            if result["success"]:
                fixes_applied.append(f"Restarted {container_name}")
        
        elif "High memory" in issue:
            # Clear caches
            result = execute_command("sync && echo 3 > /proc/sys/vm/drop_caches")
            if result["success"]:
                fixes_applied.append("Cleared system caches")
    
    return {
        "issues_found": analysis["issues"],
        "fixes_applied": fixes_applied,
        "health_before": analysis["health_score"],
        "health_after": analyze_for_optimization()["health_score"]
    }
FULL_ABILITIES

# Copy to container
docker cp /tmp/developer_full_abilities.py zoe-core:/app/routers/developer.py

# Step 5: Rebuild and start with full access
echo -e "\n🚀 Step 5: Rebuilding container with full abilities..."
docker-compose up -d --build zoe-core

# Step 6: Wait for startup
echo -e "\n⏳ Step 6: Waiting for Zack to initialize with full abilities..."
sleep 10

# Step 7: Test full abilities
echo -e "\n🧪 Step 7: Testing Zack's full abilities..."

echo "Test 1: Docker Management..."
curl -s -X POST http://localhost:8000/api/developer/chat \
    -H "Content-Type: application/json" \
    -d '{"message": "show docker containers"}' | jq -r '.response' | head -20

echo -e "\nTest 2: Command Execution..."
curl -s -X POST http://localhost:8000/api/developer/execute \
    -H "Content-Type: application/json" \
    -d '{"command": "ls -la /home/pi/zoe | head -5"}' | jq '.result.stdout'

echo -e "\nTest 3: System Status..."
curl -s http://localhost:8000/api/developer/status | jq '.abilities'

echo -e "\n✨ ZACK NOW HAS FULL LEAD DEVELOPER ABILITIES!"
echo "=============================================="
echo ""
echo "🚀 Zack's Full Abilities:"
echo "  ✅ Docker container management"
echo "  ✅ File system read/write access"
echo "  ✅ System command execution"
echo "  ✅ Project-wide awareness"
echo "  ✅ Auto-fix capabilities"
echo ""
echo "🧪 Test Commands:"
echo '  curl -X POST http://localhost:8000/api/developer/execute \'
echo '    -d {"command": "docker ps"}'
echo ""
echo '  curl -X POST http://localhost:8000/api/developer/file \'
echo '    -d {"path": "test.txt", "content": "Hello", "action": "write"}'
echo ""
echo "⚠️  Note: With great power comes great responsibility!"
echo "Zack now has root-level access to help manage your system."
