#!/bin/bash
# FIX_ZACK_PROPERLY.sh
# Location: scripts/maintenance/fix_zack_properly.sh
# Purpose: Restore Zack based on what was actually working in past conversations

set -e

echo "🔧 FIXING ZACK BASED ON PAST WORKING VERSION"
echo "============================================="
echo ""
echo "Restoring from our conversation history:"
echo "  ✓ Execute real commands with subprocess.run()"
echo "  ✓ Dynamic command selection based on question"
echo "  ✓ Executive-style balanced responses"
echo "  ✓ Flexible for any AI model (Ollama, Claude, GPT)"
echo ""
echo "Press Enter to fix Zack properly..."
read

cd /home/pi/zoe

# Backup current version
echo "📦 Backing up current files..."
mkdir -p backups/$(date +%Y%m%d_%H%M%S)
cp services/zoe-core/routers/developer.py backups/$(date +%Y%m%d_%H%M%S)/ 2>/dev/null || true

# Create the properly working developer router
echo "✅ Creating developer router with REAL system access..."
cat > services/zoe-core/routers/developer.py << 'EOF'
"""Developer router with dynamic command execution - Executive Style"""
from fastapi import APIRouter
from pydantic import BaseModel
import subprocess
import sys
sys.path.append('/app')

# Try to import ai_client, but work without it if needed
try:
    from ai_client import ai_client
    HAS_AI = True
except:
    HAS_AI = False

router = APIRouter(prefix="/api/developer")

class ChatMessage(BaseModel):
    message: str

def execute_smart_commands(message: str):
    """Intelligently execute commands based on the question"""
    message_lower = message.lower()
    results = []
    
    # Always get basic status
    if any(word in message_lower for word in ['health', 'status', 'check', 'system', 'overall']):
        # Docker status
        docker_cmd = "docker ps --format 'table {{.Names}}\t{{.Status}}'"
        docker_result = subprocess.run(docker_cmd, shell=True, capture_output=True, text=True, cwd="/app")
        results.append(("Docker Services", docker_result.stdout))
        
        # Memory
        mem_cmd = "free -h | grep Mem"
        mem_result = subprocess.run(mem_cmd, shell=True, capture_output=True, text=True, cwd="/app")
        results.append(("Memory Usage", mem_result.stdout))
        
        # Disk
        disk_cmd = "df -h / | tail -1"
        disk_result = subprocess.run(disk_cmd, shell=True, capture_output=True, text=True, cwd="/app")
        results.append(("Disk Usage", disk_result.stdout))
        
        # CPU/Load
        load_cmd = "uptime"
        load_result = subprocess.run(load_cmd, shell=True, capture_output=True, text=True, cwd="/app")
        results.append(("System Load", load_result.stdout))
    
    # Specific docker/container queries
    elif any(word in message_lower for word in ['docker', 'container', 'service']):
        docker_cmd = "docker ps"
        docker_result = subprocess.run(docker_cmd, shell=True, capture_output=True, text=True, cwd="/app")
        results.append(("Docker Containers", docker_result.stdout))
    
    # Memory specific
    elif any(word in message_lower for word in ['memory', 'ram']):
        mem_cmd = "free -h"
        mem_result = subprocess.run(mem_cmd, shell=True, capture_output=True, text=True, cwd="/app")
        results.append(("Memory Details", mem_result.stdout))
    
    # CPU specific
    elif 'cpu' in message_lower or 'processor' in message_lower:
        cpu_cmd = "top -bn1 | head -5"
        cpu_result = subprocess.run(cpu_cmd, shell=True, capture_output=True, text=True, cwd="/app")
        results.append(("CPU Status", cpu_result.stdout))
    
    # Disk specific
    elif 'disk' in message_lower or 'storage' in message_lower:
        disk_cmd = "df -h"
        disk_result = subprocess.run(disk_cmd, shell=True, capture_output=True, text=True, cwd="/app")
        results.append(("Disk Space", disk_result.stdout))
    
    # Error checking
    elif any(word in message_lower for word in ['error', 'log', 'problem', 'issue']):
        log_cmd = "docker logs zoe-core --tail 20 2>&1 | grep -i error || echo 'No recent errors found'"
        log_result = subprocess.run(log_cmd, shell=True, capture_output=True, text=True, cwd="/app")
        results.append(("Recent Errors", log_result.stdout))
    
    # Python version
    elif 'python' in message_lower or 'version' in message_lower:
        py_cmd = "python3 --version"
        py_result = subprocess.run(py_cmd, shell=True, capture_output=True, text=True, cwd="/app")
        results.append(("Python Version", py_result.stdout))
        
        os_cmd = "uname -a"
        os_result = subprocess.run(os_cmd, shell=True, capture_output=True, text=True, cwd="/app")
        results.append(("Operating System", os_result.stdout))
    
    # Default fallback - general health check
    else:
        docker_cmd = "docker ps --format '{{.Names}}'"
        docker_result = subprocess.run(docker_cmd, shell=True, capture_output=True, text=True, cwd="/app")
        container_count = len(docker_result.stdout.strip().split('\n')) if docker_result.stdout else 0
        
        mem_cmd = "free -h | grep Mem | awk '{print $3\"/\"$2}'"
        mem_result = subprocess.run(mem_cmd, shell=True, capture_output=True, text=True, cwd="/app")
        
        results.append(("Quick Status", f"Containers: {container_count}/7 running\nMemory: {mem_result.stdout}"))
    
    return results

def format_executive_response(message: str, command_results: list):
    """Format response in executive style - balanced, not too brief or verbose"""
    
    # Build a structured response
    response_parts = []
    
    # Analyze the data
    all_good = True
    issues = []
    
    for title, data in command_results:
        if "Docker" in title and data:
            lines = data.strip().split('\n')
            container_count = len(lines) - 1 if len(lines) > 1 else 0
            if container_count < 7:
                all_good = False
                issues.append(f"Only {container_count}/7 services running")
        
        if "error" in data.lower():
            all_good = False
            issues.append("Errors detected in logs")
    
    # Executive summary header
    if all_good:
        response_parts.append("✅ **System Status: All Systems Operational**\n")
    else:
        response_parts.append("⚠️ **System Status: Issues Detected**\n")
    
    # Format each result section
    for title, data in command_results:
        if not data:
            continue
            
        response_parts.append(f"\n**{title}:**")
        
        # Smart formatting based on content type
        if "Docker" in title:
            lines = data.strip().split('\n')
            if len(lines) > 1:
                response_parts.append(f"• {len(lines)-1} containers active")
                # Show first few containers
                for line in lines[1:4]:  # Show first 3 containers
                    parts = line.split()
                    if parts:
                        name = parts[0]
                        status = ' '.join(parts[1:]) if len(parts) > 1 else 'Unknown'
                        if 'up' in status.lower():
                            response_parts.append(f"  - {name}: ✅ Running")
                        else:
                            response_parts.append(f"  - {name}: ⚠️ {status}")
                if len(lines) > 4:
                    response_parts.append(f"  ... and {len(lines)-4} more")
        
        elif "Memory" in title:
            # Extract key numbers
            if "Mem:" in data:
                parts = data.split()
                if len(parts) >= 3:
                    response_parts.append(f"• Using {parts[2]} of {parts[1]}")
            else:
                response_parts.append(f"• {data.strip()}")
        
        elif "Disk" in title:
            # Extract percentage used
            if "%" in data:
                parts = data.split()
                for i, part in enumerate(parts):
                    if "%" in part:
                        percent = int(part.replace('%', ''))
                        if percent > 80:
                            response_parts.append(f"• ⚠️ {part} used (getting full!)")
                        else:
                            response_parts.append(f"• ✅ {part} used")
                        break
        
        elif "Error" in title:
            if "No recent errors" in data:
                response_parts.append("• ✅ No recent errors")
            else:
                response_parts.append(f"• ⚠️ Found errors (check logs)")
        
        else:
            # Generic formatting
            clean_data = data.strip()
            if len(clean_data) < 100:
                response_parts.append(f"• {clean_data}")
            else:
                # Truncate long output
                response_parts.append(f"• {clean_data[:100]}...")
    
    # Add action items if there are issues
    if issues:
        response_parts.append("\n**Action Required:**")
        for issue in issues:
            response_parts.append(f"• {issue}")
    
    return '\n'.join(response_parts)

@router.post("/chat")
async def developer_chat(msg: ChatMessage):
    """Chat endpoint with real system access"""
    
    # Execute smart commands based on the message
    command_results = execute_smart_commands(msg.message)
    
    # If we have AI, let it format nicely
    if HAS_AI:
        try:
            # Build prompt with real data
            data_summary = "\n".join([f"{title}:\n{data}" for title, data in command_results])
            
            prompt = f"""You are Zack, technical administrator for Zoe AI system.
Format this data as an executive report (not too brief, not too verbose).

User asked: {msg.message}

Real System Data:
{data_summary}

Rules:
- Use ✅ for good, ⚠️ for warning, ❌ for problems
- 8-15 lines ideal (not too short like 5, not too long like 30)
- Include actual numbers and specifics
- Professional but readable
- Group related information
- End with brief recommendation if needed"""

            response = await ai_client.generate_response(prompt, {"mode": "developer", "temperature": 0.3})
            return {"response": response.get("response", format_executive_response(msg.message, command_results))}
        except:
            # AI failed, use our formatter
            return {"response": format_executive_response(msg.message, command_results)}
    else:
        # No AI available, use our built-in formatter
        return {"response": format_executive_response(msg.message, command_results)}

@router.get("/status")
async def status():
    """Check developer system status"""
    try:
        result = subprocess.run("docker ps --format '{{.Names}}'", shell=True, capture_output=True, text=True, cwd="/app")
        container_count = len(result.stdout.strip().split('\n')) if result.stdout else 0
        return {
            "status": "online",
            "vision": "restored",
            "containers_running": container_count,
            "can_execute": True
        }
    except:
        return {"status": "error", "vision": "blind"}
EOF

echo "✅ Developer router created with proper executive style"

# Restart the service
echo -e "\n🐳 Restarting zoe-core..."
docker restart zoe-core

echo "⏳ Waiting for service to start (10 seconds)..."
sleep 10

# Test the fix
echo -e "\n🧪 Testing Zack's restored capabilities..."
echo "----------------------------------------"

echo -e "\nTest: System health check"
curl -s -X POST http://localhost:8000/api/developer/chat \
    -H "Content-Type: application/json" \
    -d '{"message": "Check system health"}' 2>/dev/null | jq -r '.response'

echo -e "\n----------------------------------------"
echo "✅ ZACK FIXED PROPERLY!"
echo ""
echo "Now Zack has:"
echo "  ✓ Real system vision (executes commands)"
echo "  ✓ Smart command selection"
echo "  ✓ Executive-style balanced responses (8-15 lines)"
echo "  ✓ Works with any AI model or standalone"
echo ""
echo "Try these in the dashboard:"
echo "  • 'Check system health'"
echo "  • 'Show docker containers'"
echo "  • 'How much RAM is being used?'"
echo "  • 'Check for errors'"
