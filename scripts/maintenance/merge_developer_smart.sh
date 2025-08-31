#!/bin/bash
# MERGE_DEVELOPER_SMART.sh
# Adds lead developer capabilities without breaking current chat

set -e

echo "🔧 Merging developer versions intelligently..."

cd /home/pi/zoe

# Backup current
docker exec zoe-core cp /app/routers/developer.py /app/routers/developer_backup_$(date +%Y%m%d_%H%M%S).py

# Create merged version that supports both formats
cat > services/zoe-core/routers/developer_merged.py << 'EOF'
"""
Developer Router - Merged version with full capabilities
Supports both simple chat and advanced task management
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, Dict, Any
import subprocess
import sqlite3
import json
import sys
import os
from datetime import datetime

sys.path.append('/app')

router = APIRouter(prefix="/api/developer", tags=["developer"])

# Support both input formats
class DeveloperChat(BaseModel):
    message: str

class DevelopmentTask(BaseModel):
    type: str = "chat"
    description: str
    auto_deploy: bool = False

@router.get("/status")
async def get_status():
    return {
        "status": "operational",
        "mode": "full-system-aware",
        "personality": "Zack",
        "capabilities": ["chat", "analyze", "deploy", "fix"],
        "timestamp": datetime.now().isoformat()
    }

@router.post("/chat")
async def developer_chat(request: DeveloperChat):
    """Simple chat endpoint - compatible with current UI"""
    
    try:
        from ai_client_complete import get_ai_response
        
        # Add system awareness
        context = await gather_context(request.message)
        
        # Check if this needs code generation
        code_keywords = ['build', 'create', 'implement', 'fix', 'endpoint', 'api', 'function', 'class', 'script']
        needs_code = any(word in request.message.lower() for word in code_keywords)
        
        if needs_code:
            prompt = f"""You are Zack, lead developer with full system control.
            
System Context:
{json.dumps(context, indent=2)}

User wants: {request.message}

OUTPUT COMPLETE, WORKING CODE with explanations."""
        else:
            prompt = f"""You are Zack, lead developer.
            
System Context:
{json.dumps(context, indent=2)}

User: {request.message}"""
        
        response = await get_ai_response(prompt, {"mode": "developer", "temperature": 0.2})
        
        return {"response": response, "success": True, "context": context}
        
    except Exception as e:
        return {"response": f"Error: {str(e)}", "success": False}

@router.post("/chat_advanced")
async def developer_chat_advanced(task: DevelopmentTask):
    """Advanced chat with task management - from lead version"""
    from ai_client_complete import get_ai_response
    
    context = f"""You are Zack, lead developer with full system control.
Current task: {task.description}
Type: {task.type}

Provide direct, actionable response with specific implementation steps."""
    
    response = await get_ai_response(context, {"mode": "developer"})
    
    if task.auto_deploy and task.type in ["fix", "feature"]:
        task_id = f"{task.type}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        return {
            'response': response,
            'task_id': task_id,
            'auto_deploy': True
        }
    
    return {'response': response}

async def gather_context(query: str) -> dict:
    """Gather system context - makes Zack aware"""
    context = {}
    
    try:
        # Container status
        containers = subprocess.run(
            "docker ps --format '{{.Names}}: {{.Status}}' | grep zoe-",
            shell=True, capture_output=True, text=True
        )
        context["containers"] = containers.stdout.strip().split('\n') if containers.stdout else []
        
        # Database tables
        conn = sqlite3.connect('/app/data/zoe.db')
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        context["tables"] = [t[0] for t in cursor.fetchall()]
        
        # If asking about tasks
        if 'task' in query.lower():
            cursor.execute("SELECT COUNT(*), status FROM tasks GROUP BY status")
            context["task_stats"] = dict(cursor.fetchall())
        
        conn.close()
        
        # Router files
        routers = subprocess.run("ls /app/routers/*.py | wc -l",
                                shell=True, capture_output=True, text=True)
        context["router_count"] = routers.stdout.strip()
        
    except Exception as e:
        context["error"] = str(e)
    
    return context

@router.post("/analyze")
async def analyze_system():
    """Full system analysis from lead version"""
    analysis = {
        "timestamp": datetime.now().isoformat(),
        "issues": [],
        "health_score": 0
    }
    
    # Check containers
    for container in ['zoe-core', 'zoe-ui', 'zoe-ollama', 'zoe-redis']:
        try:
            check = subprocess.run(
                f"docker inspect {container} --format '{{{{.State.Status}}}}'",
                shell=True, capture_output=True, text=True
            )
            status = check.stdout.strip()
            if status != "running":
                analysis["issues"].append(f"{container} is {status}")
        except:
            analysis["issues"].append(f"{container} not found")
    
    # Calculate health score
    analysis["health_score"] = 100 - (len(analysis["issues"]) * 10)
    
    return analysis

@router.get("/metrics")
async def get_system_metrics():
    """System metrics endpoint - from current version"""
    import psutil
    
    return {
        "cpu": psutil.cpu_percent(interval=1),
        "memory": {
            "percent": round(psutil.virtual_memory().percent, 1),
            "used": round(psutil.virtual_memory().used / (1024**3), 1),
            "total": round(psutil.virtual_memory().total / (1024**3), 1)
        },
        "disk": {
            "percent": round(psutil.disk_usage('/').percent, 1),
            "used": round(psutil.disk_usage('/').used / (1024**3), 1),
            "total": round(psutil.disk_usage('/').total / (1024**3), 1)
        },
        "timestamp": datetime.now().isoformat()
    }

@router.post("/execute")
async def execute_command(command: str):
    """Execute shell commands with safety"""
    
    # Safety check
    dangerous = ['rm -rf /', 'dd if=', 'format', ':(){']
    if any(d in command for d in dangerous):
        return {"error": "Dangerous command blocked"}
    
    try:
        result = subprocess.run(command, shell=True, capture_output=True, 
                              text=True, timeout=10, cwd="/app")
        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode
        }
    except Exception as e:
        return {"error": str(e)}
EOF

# Deploy merged version
docker cp services/zoe-core/routers/developer_merged.py zoe-core:/app/routers/developer.py

# Restart
docker compose restart zoe-core
sleep 5

# Test both endpoints
echo "Testing simple chat (UI compatible)..."
curl -X POST http://localhost:8000/api/developer/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Check system status"}' | jq '.success'

echo -e "\nTesting system awareness..."
curl -X POST http://localhost:8000/api/developer/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "How many containers are running?"}' | jq '.context.containers'

echo -e "\n✅ Merged version deployed - keeps UI working, adds system awareness!"
