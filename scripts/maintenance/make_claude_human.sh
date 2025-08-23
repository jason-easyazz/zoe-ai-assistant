#!/bin/bash
# Make Claude respond like a human with nice formatting

echo "🎨 MAKING CLAUDE RESPOND LIKE A HUMAN"
echo "====================================="

cd /home/pi/zoe

# Create better developer router with human responses
cat > services/zoe-core/routers/developer_human.py << 'EOF'
"""Developer router with human-like responses"""
from fastapi import APIRouter
from pydantic import BaseModel
import subprocess
import json
import re

router = APIRouter(prefix="/api/developer")

class ChatMessage(BaseModel):
    message: str

def format_docker_output(raw_output):
    """Convert docker output to human readable"""
    lines = raw_output.strip().split('\n')
    if len(lines) < 2:
        return "No containers found."
    
    containers = []
    for line in lines[1:]:  # Skip header
        parts = line.split()
        if len(parts) >= 2:
            name = parts[0]
            status = ' '.join(parts[1:])
            
            # Add emoji based on status
            if 'healthy' in status.lower():
                emoji = "✅"
            elif 'starting' in status.lower():
                emoji = "🔄"
            elif 'up' in status.lower():
                emoji = "🟢"
            else:
                emoji = "🔴"
            
            containers.append(f"{emoji} **{name}**: {status}")
    
    return "Here's what's running:\n\n" + '\n'.join(containers)

def format_memory_output(raw_output):
    """Convert free -h to human readable"""
    lines = raw_output.strip().split('\n')
    for line in lines:
        if 'Mem:' in line:
            parts = line.split()
            total = parts[1]
            used = parts[2]
            free = parts[3]
            return f"💾 **Memory Status:**\n- Total: {total}\n- Used: {used}\n- Free: {free}\n- Usage: About {int(float(used[:-1])/float(total[:-1])*100)}% in use"
    return "Could not read memory status"

def format_disk_output(raw_output):
    """Convert df -h to human readable"""
    lines = raw_output.strip().split('\n')
    for line in lines:
        if '/' in line and '%' in line:
            parts = line.split()
            if len(parts) >= 5:
                size = parts[1]
                used = parts[2]
                avail = parts[3]
                percent = parts[4]
                return f"💿 **Disk Space:**\n- Total: {size}\n- Used: {used} ({percent})\n- Available: {avail}\n- Status: {'⚠️ Getting full!' if int(percent[:-1]) > 80 else '✅ Plenty of space'}"
    return "Could not read disk status"

@router.post("/chat")
async def developer_chat(msg: ChatMessage):
    """Chat with human-like responses"""
    message = msg.message.lower()
    
    # Docker/Container check
    if any(word in message for word in ['docker', 'container', 'running', 'services']):
        result = subprocess.run("docker ps --format 'table {{.Names}}\t{{.Status}}'", 
                              shell=True, capture_output=True, text=True, cwd="/app")
        formatted = format_docker_output(result.stdout)
        
        # Count containers
        count = len(result.stdout.strip().split('\n')) - 1
        
        response = f"I checked all the Docker containers for you. {count} services are currently active.\n\n{formatted}"
        
        if 'starting' in result.stdout.lower():
            response += "\n\n⏳ Note: Some containers are still starting up. Give them a moment to become fully healthy."
        elif count == 7:
            response += "\n\n✨ Everything looks great! All expected services are running."
        
        return {"response": response}
    
    # Health/Status check
    elif any(word in message for word in ['health', 'status', 'ok', 'working']):
        # Get multiple checks
        docker_result = subprocess.run("docker ps --format '{{.Names}}'", 
                                     shell=True, capture_output=True, text=True, cwd="/app")
        mem_result = subprocess.run("free -h", shell=True, capture_output=True, text=True, cwd="/app")
        disk_result = subprocess.run("df -h /", shell=True, capture_output=True, text=True, cwd="/app")
        
        container_count = len(docker_result.stdout.strip().split('\n'))
        memory_info = format_memory_output(mem_result.stdout)
        disk_info = format_disk_output(disk_result.stdout)
        
        response = f"I've run a complete system health check for you!\n\n"
        response += f"🐳 **Docker Services:** {container_count}/7 containers running\n\n"
        response += f"{memory_info}\n\n"
        response += f"{disk_info}\n\n"
        
        if container_count == 7:
            response += "🎉 **Overall Status:** System is healthy and all services are operational!"
        else:
            response += f"⚠️ **Note:** Expected 7 containers but found {container_count}. Some services may need attention."
        
        return {"response": response}
    
    # Memory check
    elif any(word in message for word in ['memory', 'ram', 'mem']):
        result = subprocess.run("free -h", shell=True, capture_output=True, text=True, cwd="/app")
        formatted = format_memory_output(result.stdout)
        
        response = f"Let me check the memory usage for you.\n\n{formatted}\n\n"
        
        # Add advice
        if "Usage: About" in formatted:
            usage = int(re.search(r'About (\d+)%', formatted).group(1))
            if usage > 80:
                response += "⚠️ Memory usage is quite high. Consider restarting some services if performance is slow."
            else:
                response += "✅ Memory usage is healthy. The system has plenty of breathing room."
        
        return {"response": response}
    
    # Disk check
    elif any(word in message for word in ['disk', 'storage', 'space']):
        result = subprocess.run("df -h /", shell=True, capture_output=True, text=True, cwd="/app")
        formatted = format_disk_output(result.stdout)
        
        response = f"I'll check the disk space for you.\n\n{formatted}\n\n"
        response += "💡 Tip: If you need more space, old Docker images can be cleaned with `docker system prune`"
        
        return {"response": response}
    
    # Logs/Errors check
    elif any(word in message for word in ['log', 'error', 'problem', 'issue']):
        result = subprocess.run("docker logs zoe-core --tail 10 2>&1 | grep -i error || echo 'No errors'", 
                              shell=True, capture_output=True, text=True, cwd="/app")
        
        if 'No errors' in result.stdout or not result.stdout.strip():
            response = "Good news! I checked the recent logs and didn't find any errors. 🎉\n\n"
            response += "The system appears to be running smoothly. If you're experiencing issues, "
            response += "let me know what specific problem you're seeing and I can investigate further."
        else:
            response = "I found some errors in the logs. Let me show you:\n\n"
            response += f"```\n{result.stdout[:500]}\n```\n\n"
            response += "Would you like me to help fix these issues?"
        
        return {"response": response}
    
    # Default helpful response
    else:
        response = "I'm Claude, your Zoe system developer assistant! I can help you with:\n\n"
        response += "🐳 **Docker & Services:** Just ask 'show containers' or 'what's running?'\n"
        response += "💚 **System Health:** Try 'check system health' or 'is everything ok?'\n"
        response += "💾 **Resources:** Ask about 'memory usage' or 'disk space'\n"
        response += "📝 **Logs & Errors:** Say 'check for errors' or 'show recent logs'\n\n"
        response += "What would you like me to check?"
        
        return {"response": response}

@router.get("/status")
async def status():
    return {"status": "online", "full_access": True}
EOF

# Replace the developer router
cp services/zoe-core/routers/developer_human.py services/zoe-core/routers/developer.py

# Restart
docker restart zoe-core
sleep 8

# Test
echo "Testing human-like responses..."
echo ""
curl -s -X POST http://localhost:8000/api/developer/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "check system health"}' | jq -r '.response'

echo ""
echo "✅ Claude now responds like a helpful human!"
echo ""
echo "Try natural questions like:"
echo '  "Is everything working?"'
echo '  "Show me what services are running"'
echo '  "How is the memory usage?"'
echo '  "Any errors I should know about?"'
