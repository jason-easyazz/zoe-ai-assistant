from typing import Dict
"""Enhanced AI Client with RouteLLM and Full System Access"""
import os
import sys
import httpx
import json
import logging
import subprocess
import asyncio
from typing import Dict, Optional, List, Any
from pathlib import Path
from route_llm import router

logger = logging.getLogger(__name__)

class SystemAwareAI:
    """AI with full system visibility and control"""
    
    def __init__(self):
        self.router = router
        self.system_commands_whitelist = [
            "docker ps", "docker logs", "docker stats",
            "free -h", "df -h", "uptime", "systemctl status",
            "ls", "cat", "grep", "tail", "head",
            "curl http://localhost:8000/health"
        ]
    
    async def generate_response(self, message: str, context: Dict = None) -> Dict:
        """Generate AI response with routing and system awareness"""
        
        # Get routing decision
        routing = self.router.classify_query(message, context or {})
        
        # Add system context if needed
        if routing.get("needs_execution") or context.get("mode") == "developer":
            context = await self._gather_system_context(context or {})
        
        # Route to appropriate model
        if routing["provider"] == "anthropic":
            response = await self._use_claude(message, context, routing)
        else:
            response = await self._use_ollama(message, context, routing)
        
        # Execute commands if needed
        if routing.get("needs_execution"):
            response["execution_results"] = await self._execute_safe_commands(message)
        
        # Track usage
        self.router.track_usage(routing["provider"])
        
        return {
            "response": response.get("text", ""),
            "model": routing["model"],
            "provider": routing["provider"],
            "complexity": routing["complexity"],
            "confidence": routing["confidence"],
            "execution": response.get("execution_results")
        }
    
    async def _gather_system_context(self, context: Dict) -> Dict:
        """Gather comprehensive system information"""
        
        system_info = {}
        
        # Container status
        try:
            result = subprocess.run(
                "docker ps --format '{{json .}}'",
                shell=True, capture_output=True, text=True
            )
            containers = []
            for line in result.stdout.strip().split('\n'):
                if line:
                    containers.append(json.loads(line))
            system_info["containers"] = containers
        except:
            pass
        
        # System resources
        try:
            mem = subprocess.run("free -b", shell=True, capture_output=True, text=True)
            disk = subprocess.run("df -B1 /", shell=True, capture_output=True, text=True)
            system_info["memory"] = mem.stdout
            system_info["disk"] = disk.stdout
        except:
            pass
        
        # Recent logs
        try:
            logs = subprocess.run(
                "docker logs zoe-core --tail 20 2>&1",
                shell=True, capture_output=True, text=True, timeout=5
            )
            system_info["recent_logs"] = logs.stdout[-1000:]  # Last 1000 chars
        except:
            pass
        
        context["system"] = system_info
        return context
    
    async def _use_claude(self, message: str, context: Dict, routing: Dict) -> Dict:
        """Use Claude API with full context"""
        
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            return await self._use_ollama(message, context, routing)
        
        try:
            # Build system prompt with full awareness
            system_prompt = self._build_system_prompt(context, is_claude=True)
            
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": api_key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json"
                    },
                    json={
                        "model": routing["model"],
                        "max_tokens": 2000,
                        "temperature": routing.get("temperature", 0.7),
                        "system": system_prompt,
                        "messages": [{"role": "user", "content": message}]
                    }
                )
                
                if response.status_code == 200:
                    data = response.json()
                    return {"text": data["content"][0]["text"]}
        except Exception as e:
            logger.error(f"Claude error: {e}")
        
        # Fallback to Ollama
        return await self._use_ollama(message, context, routing)
    
    async def _use_ollama(self, message: str, context: Dict, routing: Dict) -> Dict:
        """Use local Ollama model"""
        
        try:
            system_prompt = self._build_system_prompt(context, is_claude=False)
            full_prompt = f"{system_prompt}\n\nUser: {message}\nAssistant:"
            
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    "http://zoe-ollama:11434/api/generate",
                    json={
                        "model": routing["model"],
                        "prompt": full_prompt,
                        "temperature": routing.get("temperature", 0.7),
                        "stream": False
                    }
                )
                
                if response.status_code == 200:
                    data = response.json()
                    return {"text": data.get("response", "")}
        except Exception as e:
            logger.error(f"Ollama error: {e}")
        
        return {"text": "I'm having trouble processing that request. Please try again."}
    
    async def _execute_safe_commands(self, message: str) -> List[Dict]:
        """Execute safe system commands based on query"""
        
        results = []
        msg_lower = message.lower()
        
        # Determine which commands to run
        commands_to_run = []
        
        if "docker" in msg_lower or "container" in msg_lower:
            commands_to_run.append("docker ps --format 'table {{.Names}}\t{{.Status}}'")
        
        if "memory" in msg_lower or "ram" in msg_lower:
            commands_to_run.append("free -h")
        
        if "disk" in msg_lower or "storage" in msg_lower:
            commands_to_run.append("df -h /")
        
        if "cpu" in msg_lower or "temperature" in msg_lower:
            commands_to_run.append("cat /sys/class/thermal/thermal_zone0/temp")
        
        if "log" in msg_lower or "error" in msg_lower:
            commands_to_run.append("docker logs zoe-core --tail 10 2>&1")
        
        # Execute commands
        for cmd in commands_to_run:
            try:
                result = subprocess.run(
                    cmd, shell=True, capture_output=True, 
                    text=True, timeout=5, cwd="/home/pi/zoe"
                )
                results.append({
                    "command": cmd,
                    "output": result.stdout[:500],
                    "error": result.stderr[:200] if result.stderr else None
                })
            except Exception as e:
                results.append({
                    "command": cmd,
                    "error": str(e)
                })
        
        return results
    
    def _build_system_prompt(self, context: Dict, is_claude: bool = False) -> str:
        """Build comprehensive system prompt"""
        
        mode = context.get("mode", "user")
        
        if mode == "developer":
            base = """You are Claude (in developer mode), a highly capable AI assistant with full system access.
You can see and control the Zoe AI system running on Raspberry Pi.

Your capabilities:
- View all container status and logs
- Execute safe system commands
- Analyze and debug issues
- Generate and modify code
- Monitor system resources
- Provide detailed technical solutions

Current System Context:
"""
            # Add system info if available
            if context.get("system"):
                if context["system"].get("containers"):
                    base += f"\nContainers: {len(context['system']['containers'])} running"
                if context["system"].get("memory"):
                    base += f"\nMemory: [Available in context]"
                if context["system"].get("recent_logs"):
                    base += f"\nRecent logs: [Available in context]"
            
            base += "\n\nProvide clear, technical, executable solutions."
            
        else:
            base = """You are Zoe, a friendly and helpful AI assistant.
You help users with daily tasks and provide warm, conversational support.
Be helpful, use emojis occasionally, and maintain a caring personality."""
        
        return base

# Global AI client
ai_client = SystemAwareAI()

# Backward compatibility
async def generate_response(message: str, context: Dict = None) -> str:
    """Legacy function for compatibility"""
    result = await ai_client.generate_response(message, context)
    return result

# ============= BACKWARD COMPATIBILITY =============
# Legacy function for routers/chat.py compatibility
async def get_ai_response(message: str, context: Dict = None) -> str:
    """Legacy function that chat.py expects"""
    try:
        # Use the new ai_client
        result = await ai_client.generate_response(message, context or {})
        
        # Extract response text from dict
        if isinstance(result, dict):
            return result.get('response', result.get('text', str(result)))
        return str(result)
    except Exception as e:
        logger.error(f'Legacy wrapper error: {e}')
        return 'I encountered an error. Please try again.'

# Also export for other possible imports
generate_ai_response = get_ai_response
# ============= END COMPATIBILITY =============
