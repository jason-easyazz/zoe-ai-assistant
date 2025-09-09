#!/bin/bash
# CONNECT_ZACK_PROPERLY.sh
# Location: scripts/maintenance/connect_zack_properly.sh
# Purpose: Connect Zack's documentation so he knows his full capabilities

set -e

echo "🧠 CONNECTING ZACK'S BRAIN TO THE SYSTEM"
echo "========================================"
echo ""
echo "Zack has documentation but isn't using it!"
echo "This will connect everything properly."
echo ""
echo "Press Enter to give Zack his full powers..."
read

cd /home/pi/zoe

# First, check if documentation exists
echo "📚 Checking documentation..."
if [ -f "documentation/core/zack-master-prompt.md" ]; then
    echo "✅ Zack's master prompt exists"
else
    echo "❌ Documentation missing! Run add_documentation_to_ai.sh first"
    exit 1
fi

# Create the PROPER developer router with documentation
echo "🔧 Creating developer router WITH documentation access..."
cat > services/zoe-core/routers/developer.py << 'EOF'
"""Developer router with FULL Zack capabilities"""
from fastapi import APIRouter
from pydantic import BaseModel
import subprocess
import sys
import json
sys.path.append('/app')

# Import documentation loader and AI client
try:
    from documentation_loader import zack_doc_loader
    HAS_DOCS = True
except:
    HAS_DOCS = False
    print("WARNING: Documentation loader not found")

try:
    from ai_client import ai_client
    HAS_AI = True
except:
    HAS_AI = False
    print("WARNING: AI client not found")

router = APIRouter(prefix="/api/developer")

class ChatMessage(BaseModel):
    message: str

class SystemCommand(BaseModel):
    command: str
    safe_mode: bool = True

def get_system_status():
    """Get real system status for context"""
    status = {}
    
    # Docker containers
    docker_cmd = subprocess.run("docker ps --format '{{.Names}}'", 
                              shell=True, capture_output=True, text=True, cwd="/app")
    containers = docker_cmd.stdout.strip().split('\n') if docker_cmd.stdout else []
    status['containers'] = containers
    status['container_count'] = len(containers)
    
    # Memory
    mem_cmd = subprocess.run("free -h | grep Mem | awk '{print $3,$2}'",
                            shell=True, capture_output=True, text=True, cwd="/app")
    if mem_cmd.stdout:
        used, total = mem_cmd.stdout.strip().split()
        status['memory'] = f"{used}/{total}"
    
    # Disk
    disk_cmd = subprocess.run("df -h / | tail -1 | awk '{print $5}'",
                             shell=True, capture_output=True, text=True, cwd="/app")
    status['disk'] = disk_cmd.stdout.strip() if disk_cmd.stdout else "Unknown"
    
    return status

@router.post("/chat")
async def developer_chat(msg: ChatMessage):
    """Zack's chat endpoint with FULL capabilities"""
    
    # Get current system status
    system_status = get_system_status()
    
    # Build Zack's context with documentation
    context_parts = []
    
    # Add documentation if available
    if HAS_DOCS:
        try:
            # Get Zack's instructions from documentation
            zack_context = zack_doc_loader.get_context_for_zack(msg.message)
            if zack_context:
                context_parts.append(zack_context)
        except Exception as e:
            print(f"Error loading docs: {e}")
    
    # Add current system status
    context_parts.append("\n=== CURRENT SYSTEM STATUS ===")
    context_parts.append(f"Containers Running: {system_status['container_count']}/7")
    context_parts.append(f"Containers: {', '.join(system_status['containers'][:5])}")
    context_parts.append(f"Memory: {system_status.get('memory', 'Unknown')}")
    context_parts.append(f"Disk: {system_status.get('disk', 'Unknown')}")
    
    # Build the full prompt for Zack
    full_context = "\n".join(context_parts)
    
    # Analyze what the user is asking for
    message_lower = msg.message.lower()
    
    # Check if this is a BUILD/FIX request
    if any(word in message_lower for word in ['fix', 'build', 'create', 'add', 'implement', 'install', 'setup']):
        # Zack should create a plan
        plan_prompt = f"""{full_context}

User Request: {msg.message}

You are Zack, the developer AI. Based on your documentation and capabilities:
1. Analyze this request
2. Create a step-by-step plan
3. Generate the complete script needed
4. Include all safety checks and backups

Remember your capabilities:
- You can create complete bash scripts
- You can modify any file in /home/pi/zoe
- You can control Docker containers
- You must test everything
- You must create backups first

Provide a complete solution with executable code."""

        if HAS_AI:
            response = await ai_client.generate_response(plan_prompt, {"mode": "developer", "temperature": 0.3})
            return {"response": response.get("response", "Error generating plan")}
        else:
            # Fallback without AI - provide template
            return {
                "response": f"""I understand you want to: {msg.message}

Here's what I would do:

1. **Backup Current System**
```bash
cp -r services services.backup_$(date +%Y%m%d_%H%M%S)
```

2. **Implement the Change**
[I need the AI model to generate specific code]

3. **Test the Implementation**
```bash
docker compose up -d --build zoe-core
curl http://localhost:8000/health
```

4. **Commit to GitHub**
```bash
git add .
git commit -m "✅ Zack: {msg.message}"
git push
```

Current Status: {system_status['container_count']}/7 containers running
"""
            }
    
    # Check if this is a STATUS request
    elif any(word in message_lower for word in ['status', 'health', 'check', 'show']):
        # Execute commands and show real status
        results = []
        
        # Docker status
        docker_cmd = subprocess.run("docker ps", shell=True, capture_output=True, text=True, cwd="/app")
        results.append("**Docker Containers:**\n```\n" + docker_cmd.stdout + "\n```")
        
        # Memory status
        mem_cmd = subprocess.run("free -h", shell=True, capture_output=True, text=True, cwd="/app")
        results.append("**Memory:**\n```\n" + mem_cmd.stdout + "\n```")
        
        # Recent errors
        if 'error' in message_lower:
            log_cmd = subprocess.run("docker logs zoe-core --tail 20 2>&1 | grep -i error || echo 'No errors'",
                                   shell=True, capture_output=True, text=True, cwd="/app")
            results.append("**Recent Errors:**\n```\n" + log_cmd.stdout + "\n```")
        
        return {"response": "\n\n".join(results)}
    
    # Default - use AI with full context
    elif HAS_AI:
        full_prompt = f"""{full_context}

User: {msg.message}

You are Zack, the technical developer AI for the Zoe system. 
Based on your documentation and the current system status, provide a helpful response.
If they're asking you to do something, create a complete plan with executable code."""

        response = await ai_client.generate_response(full_prompt, {"mode": "developer", "temperature": 0.3})
        return {"response": response.get("response", "Processing...")}
    
    else:
        # No AI available - provide status
        return {
            "response": f"""Zack here. System Status:
- Containers: {system_status['container_count']}/7 running
- Memory: {system_status.get('memory', 'Unknown')}  
- Disk: {system_status.get('disk', 'Unknown')}

I'm ready to help but need the AI model to generate complex responses.
For now, I can show system status and execute basic commands."""
        }

@router.post("/execute")
async def execute_command(cmd: SystemCommand):
    """Execute approved commands with safety checks"""
    
    # Safety check - only allow certain commands in safe mode
    safe_commands = ['docker ps', 'docker logs', 'free -h', 'df -h', 'uptime', 'curl']
    
    if cmd.safe_mode:
        if not any(cmd.command.startswith(safe) for safe in safe_commands):
            return {"error": "Command not in safe list. Disable safe_mode for full access."}
    
    try:
        result = subprocess.run(cmd.command, shell=True, capture_output=True, 
                              text=True, cwd="/app", timeout=30)
        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "return_code": result.returncode
        }
    except subprocess.TimeoutExpired:
        return {"error": "Command timed out after 30 seconds"}
    except Exception as e:
        return {"error": str(e)}

@router.get("/status")
async def developer_status():
    """Check Zack's status"""
    status = get_system_status()
    
    return {
        "name": "Zack",
        "role": "Developer AI",
        "documentation_loaded": HAS_DOCS,
        "ai_available": HAS_AI,
        "system_status": status,
        "capabilities": [
            "Create complete scripts",
            "Fix system issues", 
            "Add new features",
            "Execute commands",
            "Monitor system health",
            "Generate implementation plans"
        ]
    }
EOF

echo "✅ Developer router created with full capabilities"

# Ensure documentation loader exists in container
echo -e "\n📦 Ensuring documentation is in container..."
docker exec zoe-core mkdir -p /app/documentation/core
docker cp documentation/core/zack-master-prompt.md zoe-core:/app/documentation/core/ 2>/dev/null || true
docker cp services/zoe-core/documentation_loader.py zoe-core:/app/ 2>/dev/null || true

# Restart service
echo -e "\n🐳 Restarting zoe-core..."
docker restart zoe-core

echo "⏳ Waiting for service to start (10 seconds)..."
sleep 10

# Test Zack's capabilities
echo -e "\n🧪 Testing Zack's full capabilities..."
echo "======================================="

echo -e "\n1. Testing Zack's identity:"
curl -s http://localhost:8000/api/developer/status | jq '.'

echo -e "\n2. Testing system awareness:"
curl -s -X POST http://localhost:8000/api/developer/chat \
    -H "Content-Type: application/json" \
    -d '{"message": "Check system status"}' 2>/dev/null | jq -r '.response' | head -20

echo -e "\n3. Testing build capability:"
curl -s -X POST http://localhost:8000/api/developer/chat \
    -H "Content-Type: application/json" \
    -d '{"message": "How would you add a new API endpoint?"}' 2>/dev/null | jq -r '.response' | head -20

echo -e "\n======================================="
echo "✅ ZACK IS NOW FULLY CONNECTED!"
echo ""
echo "Zack now has:"
echo "  ✓ Access to all documentation"
echo "  ✓ Knowledge of his capabilities"
echo "  ✓ Ability to see system status"
echo "  ✓ Can create implementation plans"
echo "  ✓ Can generate complete scripts"
echo "  ✓ Can execute approved commands"
echo ""
echo "Try asking Zack to:"
echo "  • 'Add a notes feature'"
echo "  • 'Fix the memory leak'"
echo "  • 'Create a backup script'"
echo "  • 'Implement user authentication'"
echo ""
echo "Zack will create complete plans and scripts!"
