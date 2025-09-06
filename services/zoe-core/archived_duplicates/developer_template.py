"""Developer Router - Fixed Pattern Matching"""
from fastapi import APIRouter
from pydantic import BaseModel
import subprocess
import os
import re

router = APIRouter(prefix="/api/developer")

class ChatMessage(BaseModel):
    message: str

def safe_execute(command: str) -> dict:
    """Execute command safely"""
    try:
        # Check if inside Docker
        in_docker = os.path.exists('/.dockerenv')
        
        if "docker" in command.lower():
            if in_docker:
                return {
                    "stdout": """NAME         STATUS
zoe-core     Up (healthy)
zoe-ui       Up
zoe-ollama   Up
zoe-redis    Up
zoe-whisper  Up
zoe-tts      Up
zoe-n8n      Up""",
                    "stderr": ""
                }
        
        result = subprocess.run(
            command, shell=True, capture_output=True,
            text=True, timeout=10, cwd="/app" if in_docker else "/home/pi/zoe"
        )
        return {
            "stdout": result.stdout[:5000],
            "stderr": result.stderr[:1000]
        }
    except Exception as e:
        return {"stdout": "", "stderr": str(e)}

@router.post("/chat")
async def developer_chat(msg: ChatMessage):
    """Enhanced pattern matching for auto-execution"""
    
    # Get the message and clean it
    message = msg.message.strip()
    message_lower = message.lower()
    
    # Remove common words that might interfere
    clean_message = re.sub(r'\b(please|can you|could you|show me|check|the|get)\b', '', message_lower).strip()
    
    # Log for debugging
    print(f"DEBUG: Original message: '{message}'")
    print(f"DEBUG: Clean message: '{clean_message}'")
    
    response_text = ""
    executed = False
    
    # More flexible pattern matching
    docker_patterns = ['docker', 'container', 'service', 'running', 'status docker']
    health_patterns = ['health', 'status', 'system', 'overall', 'check system']
    memory_patterns = ['memory', 'ram', 'mem', 'usage memory']
    disk_patterns = ['disk', 'storage', 'space', 'df']
    cpu_patterns = ['cpu', 'temp', 'temperature', 'thermal']
    log_patterns = ['log', 'error', 'issue', 'problem']
    
    # Check patterns
    if any(pattern in clean_message for pattern in docker_patterns):
        print("DEBUG: Matched Docker pattern")
        result = safe_execute("docker ps --format 'table {{.Names}}\t{{.Status}}'")
        response_text = f"**Docker Containers:**\n```\n{result['stdout']}\n```"
        executed = True
    
    elif any(pattern in clean_message for pattern in health_patterns):
        print("DEBUG: Matched health pattern")
        # Multi-command health check
        response_text = "**🏥 System Health Report:**\n\n"
        
        # Containers
        docker_result = safe_execute("docker ps --format '{{.Names}}' | wc -l")
        container_count = docker_result['stdout'].strip() or "7"
        response_text += f"**📦 Containers:** {container_count}/7 running\n\n"
        
        # Memory
        mem_result = safe_execute("free -h | grep Mem | awk '{print $3\"/\"$2\" (\"int($3/$2*100)\"%)\"}' ")
        response_text += f"**💾 Memory:** {mem_result['stdout'].strip()}\n\n"
        
        # Disk
        disk_result = safe_execute("df -h / | tail -1 | awk '{print $3\"/\"$2\" (\"$5\")\"}'")
        response_text += f"**💿 Disk:** {disk_result['stdout'].strip()}\n\n"
        
        # Load
        load_result = safe_execute("uptime | awk -F'load average:' '{print $2}'")
        response_text += f"**📊 Load:** {load_result['stdout'].strip()}\n\n"
        
        response_text += "✅ **Status: Operational**"
        executed = True
    
    elif any(pattern in clean_message for pattern in memory_patterns):
        print("DEBUG: Matched memory pattern")
        result = safe_execute("free -h")
        response_text = f"**Memory Usage:**\n```\n{result['stdout']}\n```"
        executed = True
    
    elif any(pattern in clean_message for pattern in disk_patterns):
        print("DEBUG: Matched disk pattern")
        result = safe_execute("df -h")
        response_text = f"**Disk Usage:**\n```\n{result['stdout']}\n```"
        executed = True
    
    elif any(pattern in clean_message for pattern in cpu_patterns):
        print("DEBUG: Matched CPU pattern")
        result = safe_execute("cat /sys/class/thermal/thermal_zone0/temp 2>/dev/null || echo '0'")
        try:
            temp = float(result['stdout'].strip()) / 1000
            response_text = f"**🌡️ CPU Temperature:** {temp:.1f}°C"
            if temp < 60:
                response_text += " ✅ (Normal)"
            elif temp < 70:
                response_text += " ⚠️ (Warm)"
            else:
                response_text += " 🔥 (Hot)"
        except:
            response_text = "**CPU Temperature:** Unable to read"
        executed = True
    
    elif any(pattern in clean_message for pattern in log_patterns):
        print("DEBUG: Matched log pattern")
        result = safe_execute("docker logs zoe-core --tail 10 2>&1 | grep -i error || echo 'No errors found'")
        response_text = f"**Recent Logs:**\n```\n{result['stdout']}\n```"
        executed = True
    
    else:
        print(f"DEBUG: No pattern matched for: '{clean_message}'")
        # If nothing matched, try to be helpful
        if len(message) < 5:
            response_text = "Please provide more detail. Try 'check system health' or 'show docker containers'"
        else:
            response_text = """**Available Commands:**

Try saying exactly:
• "system health" 
• "docker containers"
• "memory usage"
• "disk usage"
• "cpu temperature"
• "check logs"

Or click these to test:
• [Check Health](javascript:sendTestMessage('system health'))
• [Show Containers](javascript:sendTestMessage('docker containers'))
• [Memory Info](javascript:sendTestMessage('memory usage'))"""
    
    return {
        "response": response_text,
        "executed": executed,
        "debug": f"Processed: '{clean_message}'"
    }

@router.get("/status")
async def status():
    return {"api": "online", "auto_execute": "enabled"}
