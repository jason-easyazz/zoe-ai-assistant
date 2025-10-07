"""
AI Router Module - Intelligent routing between Claude and local models
"""

import os
import httpx
import json
import logging
import subprocess
from typing import Optional, Dict, Any
from datetime import datetime
from api_key_loader import loaded_keys


logger = logging.getLogger(__name__)

# Load configuration from environment
USE_CLAUDE = os.getenv("USE_CLAUDE_FOR_DEVELOPER", "true").lower() == "true"
CLAUDE_LIMIT = int(os.getenv("CLAUDE_DAILY_LIMIT", "100"))
OPENAI_KEY = os.getenv("OPENAI_API_KEY", "")
ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY", "")
# Updated model configuration for optimal performance
LOCAL_MODEL_ULTRA_FAST = "llama3.2:1b"  # Ultra-fast responses
LOCAL_MODEL_BALANCED = "qwen2.5:3b"     # Balanced performance  
LOCAL_MODEL_CODE = "phi3:mini"          # Code generation
LOCAL_MODEL_COMPLEX = "mistral:latest"  # Complex reasoning
LOCAL_MODEL_SIMPLE = LOCAL_MODEL_ULTRA_FAST  # Define simple/default model

# Usage tracking
usage_file = "/app/data/ai_usage.json"
daily_usage = {"claude": 0, "local": 0, "date": datetime.now().date().isoformat()}

class AIRouter:
    def __init__(self):
        self.claude_client = None
        self.setup_claude()
    
    def setup_claude(self):
        """Initialize Claude client if API keys available"""
        if ANTHROPIC_KEY and ANTHROPIC_KEY != "your-anthropic-key-here":
            try:
                import anthropic
                self.claude_client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
                logger.info("✅ Claude (Anthropic) initialized")
            except ImportError:
                logger.warning("Anthropic library not installed")
        elif OPENAI_KEY and OPENAI_KEY != "your-openai-key-here":
            logger.info("✅ OpenAI API available for GPT-4 as Claude substitute")
    
    async def route_request(self, message: str, context: Dict, temperature: float) -> Dict:
        """Route request to appropriate AI based on context and complexity"""
        
        # Assess request complexity
        complexity = self._assess_complexity(message, context)
        
        # Developer mode always tries Claude first
        if context.get("mode") == "developer":
            context["system_info"] = await self._gather_system_context()
            if self.claude_client and USE_CLAUDE:
                return await self._use_claude(message, context, temperature)
            elif OPENAI_KEY and OPENAI_KEY != "your-openai-key-here":
                return await self._use_openai_as_claude(message, context, temperature)
        
        # Route based on complexity
        if complexity == "high" and self.claude_client and USE_CLAUDE:
            return await self._use_claude(message, context, temperature)
        else:
            return await self._use_local_model(message, context, temperature, complexity)
    
    def _assess_complexity(self, message: str, context: Dict) -> str:
        """Determine request complexity"""
        
        indicators_high = ["analyze", "debug", "architect", "optimize", "integrate"]
        indicators_medium = ["create", "fix", "monitor", "backup", "configure"]
        
        message_lower = message.lower()
        
        if any(word in message_lower for word in indicators_high):
            return "high"
        elif any(word in message_lower for word in indicators_medium):
            return "medium"
        return "low"
    
    async def _use_claude(self, message: str, context: Dict, temperature: float) -> Dict:
        """Use real Claude API"""
        try:
            system_prompt = self._build_claude_prompt(context)
            
            response = self.claude_client.messages.create(
                model="claude-3-opus-20240229",
                max_tokens=2000,
                temperature=temperature,
                system=system_prompt,
                messages=[{"role": "user", "content": message}]
            )
            
            self._track_usage("claude")
            
            return {
                "response": response.content[0].text,
                "model": "claude-3-opus",
                "complexity": "high"
            }
        except Exception as e:
            logger.error(f"Claude API error: {e}")
            return await self._use_local_model(message, context, temperature, "high")
    
    async def _use_openai_as_claude(self, message: str, context: Dict, temperature: float) -> Dict:
        """Use OpenAI GPT-4 with Claude personality"""
        try:
            import openai
            
            client = openai.OpenAI(api_key=OPENAI_KEY)
            system_prompt = self._build_claude_prompt(context)
            
            response = client.chat.completions.create(
                model="gpt-4-turbo",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": message}
                ],
                temperature=temperature,
                max_tokens=2000
            )
            
            self._track_usage("claude")
            
            return {
                "response": response.choices[0].message.content,
                "model": "gpt-4-claude",
                "complexity": "high"
            }
        except Exception as e:
            logger.error(f"OpenAI API error: {e}")
            return await self._use_local_model(message, context, temperature, "high")
    
    async def _use_local_model(self, message: str, context: Dict, temperature: float, complexity: str) -> Dict:
        """Use local Ollama model"""
        
        model = LOCAL_MODEL_COMPLEX if complexity == "high" else LOCAL_MODEL_SIMPLE
        prompt = self._build_local_prompt(message, context, complexity)
        
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    "http://zoe-ollama:11434/api/generate",
                    json={
                        "model": model,
                        "prompt": prompt,
                        "temperature": temperature,
                        "stream": False
                    }
                )
                
                if response.status_code == 200:
                    data = response.json()
                    self._track_usage("local")
                    
                    return {
                        "response": data.get("response", ""),
                        "model": model,
                        "complexity": complexity
                    }
        except Exception as e:
            logger.error(f"Local model error: {e}")
            return {
                "response": "AI service temporarily unavailable",
                "model": "error",
                "complexity": "error"
            }
    
    def _build_claude_prompt(self, context: Dict) -> str:
        """Build Claude developer prompt with context"""
        
        base_prompt = """You are Zack, a senior DevOps engineer and development assistant for the Zoe AI system.

Technical Expertise:
- Expert in Python, FastAPI, Docker, Linux system administration
- Deep knowledge of Raspberry Pi optimization and ARM architecture
- Skilled in bash scripting, system monitoring, and performance tuning
- Experienced with Git, CI/CD, and infrastructure as code

Current System:
- Platform: Raspberry Pi 5 (ARM64, 8GB RAM)
- Location: /home/pi/zoe
- Services: zoe-core (FastAPI), zoe-ui (Nginx), zoe-ollama, zoe-redis
- Ports: API=8000, UI=8080, Ollama=11434, Redis=6379

Your Approach:
- Always provide complete, executable scripts
- Include comprehensive error handling
- Consider resource constraints (8GB RAM, SD card wear)
- Test all commands before suggesting
- Document clearly with examples
- Think defensively about edge cases"""

        if context.get("system_info"):
            base_prompt += f"\n\nCurrent System State:\n{json.dumps(context['system_info'], indent=2)}"
        
        return base_prompt
    
    def _build_local_prompt(self, message: str, context: Dict, complexity: str) -> str:
        """Build prompt for local model"""
        
        if context.get("mode") == "developer":
            if complexity == "high":
                prefix = "You are a technical assistant. Provide detailed technical solutions with code examples."
            else:
                prefix = "You are a helpful technical assistant. Provide clear, concise answers."
        else:
            prefix = "You are Zoe, a warm and friendly AI assistant."
        
        return f"{prefix}\n\nUser: {message}\nAssistant:"
    
    async def _gather_system_context(self) -> Dict:
        """Gather system information for Claude"""
        context = {}
        
        try:
            # Container status
            result = subprocess.run(
                ["docker", "ps", "--format", "json"],
                capture_output=True,
                text=True
            )
            if result.returncode == 0 and result.stdout:
                context["containers"] = "All containers running"
            
            # System resources: run df once and reuse the result
            df_result = subprocess.run(
                ["df", "-h", "/"],
                capture_output=True,
                text=True
            )
            if df_result.returncode == 0 and df_result.stdout:
                lines = df_result.stdout.split('\n')
                context["disk_usage"] = lines[1] if len(lines) > 1 else df_result.stdout.strip()
            else:
                context["disk_usage"] = "Unknown"
            
        except Exception as e:
            logger.error(f"Error gathering context: {e}")
        
        return context
    
    def _track_usage(self, model_type: str):
        """Track AI usage"""
        global daily_usage
        
        today = datetime.now().date().isoformat()
        if daily_usage["date"] != today:
            daily_usage = {"claude": 0, "local": 0, "date": today}
        
        daily_usage[model_type] += 1
        
        try:
            with open(usage_file, 'w') as f:
                json.dump(daily_usage, f)
        except:
            pass

# Global instance
ai_router = AIRouter()

async def get_ai_response(message: str, context: Dict, temperature: float = 0.7) -> str:
    """Main entry point for AI responses"""
    result = await ai_router.route_request(message, context, temperature)
    return result["response"]
