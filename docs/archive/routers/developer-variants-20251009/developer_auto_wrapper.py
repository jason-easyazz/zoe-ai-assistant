"""Simple wrapper to ensure auto-execution on top of existing working code"""

# Import the existing working developer module
import sys
import os
sys.path.insert(0, '/app/routers')

# Import existing functions from current developer.py
from developer import router as original_router
from developer import safe_execute, execute_command

# Re-export everything so nothing breaks
router = original_router

# Just ensure the chat endpoint auto-executes
from fastapi import APIRouter
from pydantic import BaseModel

class ChatMessage(BaseModel):
    message: str

# Override just the chat endpoint to ensure execution
@router.post("/chat", include_in_schema=False)
async def enhanced_developer_chat(msg: ChatMessage):
    """Enhanced chat that ensures auto-execution"""
    
    message_lower = msg.message.lower()
    
    # Keywords that trigger auto-execution
    execute_keywords = [
        'check', 'show', 'display', 'list', 'get',
        'docker', 'container', 'status', 'health',
        'memory', 'cpu', 'disk', 'system',
        'fix', 'restart', 'logs', 'error'
    ]
    
    # If message contains execution keywords, ensure we run commands
    if any(keyword in message_lower for keyword in execute_keywords):
        
        # Build response with actual command outputs
        response_parts = []
        
        # Docker containers
        if any(word in message_lower for word in ['docker', 'container', 'service']):
            result = safe_execute("docker ps --format 'table {{.Names}}\t{{.Status}}'")
            if result["stdout"]:
                response_parts.append(f"**Docker Containers:**\n```\n{result['stdout']}\n```")
        
        # System health
        elif any(word in message_lower for word in ['health', 'status', 'system']):
            docker_result = safe_execute("docker ps --format 'table {{.Names}}\t{{.Status}}'")
            mem_result = safe_execute("free -h")
            disk_result = safe_execute("df -h /")
            
            response_parts.append("**System Status:**")
            response_parts.append(f"\n📦 **Containers:**\n```\n{docker_result['stdout']}\n```")
            response_parts.append(f"\n💾 **Memory:**\n```\n{mem_result['stdout']}\n```")
            response_parts.append(f"\n💿 **Disk:**\n```\n{disk_result['stdout']}\n```")
        
        # Memory
        elif 'memory' in message_lower or 'ram' in message_lower:
            result = safe_execute("free -h")
            response_parts.append(f"**Memory Usage:**\n```\n{result['stdout']}\n```")
        
        # Disk
        elif 'disk' in message_lower or 'storage' in message_lower:
            result = safe_execute("df -h")
            response_parts.append(f"**Disk Usage:**\n```\n{result['stdout']}\n```")
        
        # Logs
        elif any(word in message_lower for word in ['log', 'error', 'debug']):
            container = "zoe-core"
            if "ui" in message_lower:
                container = "zoe-ui"
            result = safe_execute(f"docker logs {container} --tail 20")
            response_parts.append(f"**Recent Logs ({container}):**\n```\n{result['stdout'][:2000]}\n```")
        
        # Fix/restart
        elif any(word in message_lower for word in ['fix', 'restart', 'repair']):
            response_parts.append("**Checking for issues...**\n")
            
            # Check stopped containers
            ps_result = safe_execute("docker ps -a --format '{{.Names}}\t{{.Status}}'")
            stopped = [line for line in ps_result['stdout'].split('\n') 
                      if 'Exited' in line and line.startswith('zoe-')]
            
            if stopped:
                response_parts.append(f"Found {len(stopped)} stopped containers. Restarting...")
                for line in stopped:
                    container_name = line.split('\t')[0]
                    restart_result = safe_execute(f"docker restart {container_name}")
                    if restart_result['code'] == 0:
                        response_parts.append(f"✅ Restarted {container_name}")
            else:
                response_parts.append("✅ All containers running!")
        
        if response_parts:
            return {"response": "\n".join(response_parts), "executed": True}
    
    # For non-execution queries, use the original handler
    # Import and call the original function
    from developer import developer_chat as original_chat
    return await original_chat(msg)

# Keep all other endpoints as-is - they're already working!
