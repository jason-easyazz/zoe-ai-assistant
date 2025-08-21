"""
Multi-Model AI Router - Intelligently routes to appropriate AI model
Supports Claude API, OpenAI, and local Ollama models
"""

import os
import httpx
import json
import logging
import subprocess
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta
from pathlib import Path
import asyncio

# Try to import Anthropic
try:
    import anthropic
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False
    print("Anthropic not installed - using local models only")

logger = logging.getLogger(__name__)

# Load environment variables
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
USE_CLAUDE = os.getenv("USE_CLAUDE_FOR_COMPLEX", "true").lower() == "true"
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-3-opus-20240229")
LOCAL_MODEL_SIMPLE = os.getenv("LOCAL_MODEL_SIMPLE", "llama3.2:1b")
LOCAL_MODEL_COMPLEX = os.getenv("LOCAL_MODEL_COMPLEX", "llama3.2:3b")

# Usage tracking
usage_file = Path("/app/data/ai_usage.json")
daily_usage = {"claude": 0, "local": 0, "date": datetime.now().date().isoformat()}

class AIRouter:
    """Routes requests to appropriate AI model based on complexity and cost"""
    
    def __init__(self):
        self.claude_client = None
        if ANTHROPIC_AVAILABLE and ANTHROPIC_API_KEY:
            self.claude_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
            logger.info("Claude API initialized")
        else:
            logger.warning("Claude API not available - using local models only")
    
    async def route_request(
        self,
        message: str,
        context: Dict[str, Any] = {},
        temperature: float = 0.7
    ) -> Dict[str, Any]:
        """
        Routes request to appropriate AI based on complexity
        Returns response with metadata about which model was used
        """
        
        # Determine complexity
        complexity = self._assess_complexity(message, context)
        
        # Get system context if this is a developer request
        if context.get("mode") == "developer" and context.get("include_system", True):
            system_context = await self._gather_system_context()
            context["system_info"] = system_context
        
        # Route to appropriate model
        if complexity == "high" and self.claude_client and USE_CLAUDE:
            return await self._use_claude(message, context, temperature)
        elif complexity == "medium":
            return await self._use_local_complex(message, context, temperature)
        else:
            return await self._use_local_simple(message, context, temperature)
    
    def _assess_complexity(self, message: str, context: Dict) -> str:
        """Assess request complexity to determine which model to use"""
        
        message_lower = message.lower()
        
        # High complexity - needs Claude
        high_indicators = [
            "analyze", "debug", "architecture", "implement", "design",
            "optimize", "refactor", "security", "complex", "integrate",
            "troubleshoot", "performance", "scale", "migrate"
        ]
        
        # Check for developer mode + complex request
        if context.get("mode") == "developer":
            if any(word in message_lower for word in high_indicators):
                return "high"
            if len(message) > 200:  # Long technical questions
                return "high"
        
        # Medium complexity - use better local model
        medium_indicators = [
            "create", "script", "fix", "check", "monitor",
            "backup", "update", "install", "configure"
        ]
        
        if any(word in message_lower for word in medium_indicators):
            return "medium"
        
        # Simple requests - use fast local model
        return "low"
    
    async def _use_claude(self, message: str, context: Dict, temperature: float) -> Dict:
        """Use Claude API for complex requests"""
        
        try:
            # Build system prompt with full context
            system_prompt = self._build_claude_prompt(context)
            
            logger.info("Using Claude API for complex request")
            
            # Add system files if developer mode
            if context.get("system_info"):
                message = f"""System Context:
{json.dumps(context['system_info'], indent=2)}

User Request: {message}"""
            
            # Call Claude API
            response = self.claude_client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=4000,
                temperature=temperature,
                system=system_prompt,
                messages=[{"role": "user", "content": message}]
            )
            
            # Track usage
            self._track_usage("claude")
            
            return {
                "response": response.content[0].text,
                "model": CLAUDE_MODEL,
                "complexity": "high",
                "cost_estimate": 0.01  # Rough estimate
            }
            
        except Exception as e:
            logger.error(f"Claude API error: {e}")
            # Fallback to local model
            return await self._use_local_complex(message, context, temperature)
    
    async def _use_local_complex(self, message: str, context: Dict, temperature: float) -> Dict:
        """Use complex local model for medium complexity"""
        
        logger.info(f"Using local model: {LOCAL_MODEL_COMPLEX}")
        
        prompt = self._build_local_prompt(message, context, "complex")
        
        try:
            async with httpx.AsyncClient(timeout=90.0) as client:
                response = await client.post(
                    "http://zoe-ollama:11434/api/generate",
                    json={
                        "model": LOCAL_MODEL_COMPLEX,
                        "prompt": prompt,
                        "temperature": temperature,
                        "stream": False
                    }
                )
                
                if response.status_code == 200:
                    data = response.json()
                    self._track_usage("local")
                    
                    return {
                        "response": data.get("response", "Error generating response"),
                        "model": LOCAL_MODEL_COMPLEX,
                        "complexity": "medium",
                        "cost_estimate": 0
                    }
        except Exception as e:
            logger.error(f"Local model error: {e}")
            return {
                "response": "AI service temporarily unavailable",
                "model": "none",
                "complexity": "error",
                "cost_estimate": 0
            }
    
    async def _use_local_simple(self, message: str, context: Dict, temperature: float) -> Dict:
        """Use simple local model for basic requests"""
        
        logger.info(f"Using simple local model: {LOCAL_MODEL_SIMPLE}")
        
        prompt = self._build_local_prompt(message, context, "simple")
        
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    "http://zoe-ollama:11434/api/generate",
                    json={
                        "model": LOCAL_MODEL_SIMPLE,
                        "prompt": prompt,
                        "temperature": temperature,
                        "stream": False
                    }
                )
                
                if response.status_code == 200:
                    data = response.json()
                    self._track_usage("local")
                    
                    return {
                        "response": data.get("response", "Error generating response"),
                        "model": LOCAL_MODEL_SIMPLE,
                        "complexity": "low",
                        "cost_estimate": 0
                    }
        except Exception as e:
            logger.error(f"Simple model error: {e}")
            return {
                "response": "AI service temporarily unavailable",
                "model": "none",
                "complexity": "error",
                "cost_estimate": 0
            }
    
    def _build_claude_prompt(self, context: Dict) -> str:
        """Build system prompt for Claude with full context"""
        
        if context.get("mode") == "developer":
            return f"""You are Claude, an expert AI assistant helping with the Zoe AI system on Raspberry Pi.

System Information:
- Platform: Raspberry Pi 5 (ARM64, 8GB RAM)
- Location: /home/pi/zoe
- Docker Containers: zoe-core, zoe-ui, zoe-ollama, zoe-redis, zoe-whisper, zoe-tts, zoe-n8n
- Architecture: FastAPI backend, Nginx frontend, SQLite database
- Your Role: Senior DevOps engineer and systems architect

You have access to:
- Full system logs and configuration
- Docker container status and logs
- File system at /home/pi/zoe
- Git repository status
- Performance metrics

Capabilities:
- Analyze complex system issues
- Design architectural improvements
- Create complete implementation scripts
- Debug performance problems
- Suggest optimizations
- Review security concerns

Always:
- Provide complete, working code
- Include error handling
- Consider Raspberry Pi limitations
- Test commands before suggesting
- Document your solutions clearly"""
        else:
            return """You are Zoe, a warm and friendly AI assistant.
Be conversational, helpful, and supportive.
Help with daily tasks, calendar events, and general questions.
Use a friendly tone and occasional emojis."""
    
    def _build_local_prompt(self, message: str, context: Dict, complexity: str) -> str:
        """Build prompt for local models"""
        
        if context.get("mode") == "developer":
            if complexity == "complex":
                prefix = "You are a technical assistant. Provide detailed technical solutions."
            else:
                prefix = "You are a helpful assistant. Provide clear, concise technical answers."
        else:
            prefix = "You are Zoe, a friendly AI assistant."
        
        return f"{prefix}\n\nUser: {message}\nAssistant:"
    
    async def _gather_system_context(self) -> Dict:
        """Gather comprehensive system information for Claude"""
        
        context = {}
        
        try:
            # Get container status
            result = subprocess.run(
                ["docker", "ps", "--format", "json"],
                capture_output=True,
                text=True
            )
            if result.returncode == 0:
                context["containers"] = json.loads(result.stdout)
            
            # Get disk usage
            result = subprocess.run(
                ["df", "-h", "/"],
                capture_output=True,
                text=True
            )
            context["disk_usage"] = result.stdout
            
            # Get memory usage
            result = subprocess.run(
                ["free", "-h"],
                capture_output=True,
                text=True
            )
            context["memory"] = result.stdout
            
            # Get recent logs (last 50 lines)
            result = subprocess.run(
                ["docker", "logs", "zoe-core", "--tail", "50"],
                capture_output=True,
                text=True
            )
            context["recent_logs"] = result.stdout[-2000:]  # Last 2000 chars
            
            # Get file structure
            result = subprocess.run(
                ["ls", "-la", "/home/pi/zoe/services/"],
                capture_output=True,
                text=True
            )
            context["file_structure"] = result.stdout
            
        except Exception as e:
            logger.error(f"Error gathering system context: {e}")
        
        return context
    
    def _track_usage(self, model_type: str):
        """Track AI usage for cost management"""
        global daily_usage
        
        # Reset if new day
        today = datetime.now().date().isoformat()
        if daily_usage["date"] != today:
            daily_usage = {"claude": 0, "local": 0, "date": today}
        
        daily_usage[model_type] += 1
        
        # Save to file
        try:
            with open(usage_file, 'w') as f:
                json.dump(daily_usage, f)
        except:
            pass

# Global router instance
ai_router = AIRouter()

async def get_ai_response(
    message: str,
    system_prompt: str = "",  # Ignored, handled by router
    context: Optional[Dict[str, Any]] = None,
    temperature: float = 0.7
) -> str:
    """Main entry point - routes to appropriate AI"""
    
    result = await ai_router.route_request(message, context or {}, temperature)
    
    # Log which model was used
    logger.info(f"Used model: {result['model']} (complexity: {result['complexity']})")
    
    return result["response"]
