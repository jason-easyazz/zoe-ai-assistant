"""
Enhanced AI Client - Forces actual code generation for Zack
"""

import httpx
import json
import logging
import sqlite3
from typing import Optional, Dict, Any
from datetime import datetime

logger = logging.getLogger(__name__)

# CRITICAL: Zack's prompt that FORCES code generation
ZACK_CODE_GENERATOR_PROMPT = """You are Zack, the lead developer for the Zoe AI system.

CRITICAL INSTRUCTIONS - YOU MUST GENERATE ACTUAL CODE:
- When asked to build something, WRITE THE COMPLETE CODE
- When asked to fix something, PROVIDE THE EXACT FIX
- When asked to create an endpoint, GENERATE THE FULL ROUTER FILE
- NEVER explain HOW to do it - ACTUALLY DO IT

SYSTEM CONTEXT:
- Backend: FastAPI at /app/routers/ (container) or /home/pi/zoe/services/zoe-core/routers/ (host)
- Database: SQLite at /app/data/zoe.db with tables: events, tasks, lists, memories, developer_tasks, etc.
- Scripts: Go in /home/pi/zoe/scripts/[category]/
- Docker: All containers use zoe- prefix
- Ports: API=8000, UI=8080

RESPONSE FORMAT:
1. Start with the complete file path
2. Generate the ENTIRE, EXECUTABLE code
3. Include all imports, error handling, and testing
4. Make it production-ready

EXAMPLE - When asked "Build a backup endpoint":
```python
# File: /app/routers/backup.py
from fastapi import APIRouter, HTTPException
import shutil
import os
from datetime import datetime

router = APIRouter(prefix="/api/backup")

@router.post("/")
async def create_backup():
    try:
        backup_dir = f"/app/data/backups/{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        os.makedirs(backup_dir, exist_ok=True)
        shutil.copy2("/app/data/zoe.db", f"{backup_dir}/zoe.db")
        return {"status": "success", "path": backup_dir}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
```

REMEMBER: Generate ACTUAL, WORKING CODE. Not instructions. Not explanations. CODE."""

# Zoe's friendly prompt remains unchanged
ZOE_FRIENDLY_PROMPT = """You are Zoe, a warm and friendly AI assistant.
Be cheerful, supportive, and conversational. Use emojis occasionally.
Help with daily tasks, calendars, reminders, and be a companion."""

class EnhancedAI:
    def __init__(self):
        self.ollama_url = "http://zoe-ollama:11434"
        self.anthropic_key = None
        self.openai_key = None
        self._load_api_keys()
    
    def _load_api_keys(self):
        """Load API keys from database"""
        try:
            conn = sqlite3.connect("/app/data/zoe.db")
            cursor = conn.cursor()
            cursor.execute("SELECT service, encrypted_key FROM api_keys WHERE is_active = 1")
            for service, key in cursor.fetchall():
                if service == "anthropic":
                    self.anthropic_key = key  # Should be decrypted in production
                elif service == "openai":
                    self.openai_key = key
            conn.close()
        except Exception as e:
            logger.error(f"Could not load API keys: {e}")
    
    async def generate_response(
        self,
        message: str,
        context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Generate response with appropriate personality"""
        
        # Determine mode and select prompt
        is_developer = context and context.get("mode") == "developer"
        
        if is_developer:
            system_prompt = ZACK_CODE_GENERATOR_PROMPT
            temperature = 0.2  # Lower for precise code
            
            # Add system awareness to context
            system_info = await self._get_system_info()
            full_prompt = f"{system_prompt}\n\nCurrent System State:\n{json.dumps(system_info, indent=2)}\n\nUser Request: {message}\n\nGenerate the complete code now:"
        else:
            system_prompt = ZOE_FRIENDLY_PROMPT
            temperature = 0.7
            full_prompt = f"{system_prompt}\n\nUser: {message}\nZoe:"
        
        # Try providers in order
        response = await self._try_providers(full_prompt, temperature, is_developer)
        return response
    
    async def _get_system_info(self) -> Dict:
        """Get current system state for context"""
        info = {
            "timestamp": datetime.now().isoformat(),
            "containers": [],
            "api_health": "unknown"
        }
        
        try:
            # Check Docker containers
            import subprocess
            result = subprocess.run(
                "docker ps --format '{{.Names}}:{{.Status}}'",
                shell=True, capture_output=True, text=True
            )
            info["containers"] = result.stdout.strip().split('\n')
            
            # Check API health
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get("http://localhost:8000/health")
                if resp.status_code == 200:
                    info["api_health"] = "healthy"
        except Exception as e:
            logger.error(f"Could not get system info: {e}")
        
        return info
    
    async def _try_providers(self, prompt: str, temperature: float, is_developer: bool) -> Dict:
        """Try AI providers in order"""
        
        # For developer mode, try Anthropic first (Claude is best at code)
        if is_developer and self.anthropic_key:
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.post(
                        "https://api.anthropic.com/v1/messages",
                        headers={
                            "x-api-key": self.anthropic_key,
                            "anthropic-version": "2023-06-01",
                            "content-type": "application/json"
                        },
                        json={
                            "model": "claude-3-haiku-20240307",
                            "max_tokens": 4000,
                            "temperature": temperature,
                            "messages": [
                                {"role": "user", "content": prompt}
                            ]
                        }
                    )
                    
                    if response.status_code == 200:
                        data = response.json()
                        return {
                            "response": data["content"][0]["text"],
                            "model": "claude-3-haiku"
                        }
            except Exception as e:
                logger.error(f"Anthropic error: {e}")
        
        # Try OpenAI
        if self.openai_key:
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.post(
                        "https://api.openai.com/v1/chat/completions",
                        headers={
                            "Authorization": f"Bearer {self.openai_key}",
                            "Content-Type": "application/json"
                        },
                        json={
                            "model": "gpt-3.5-turbo",
                            "messages": [
                                {"role": "system", "content": prompt}
                            ],
                            "temperature": temperature,
                            "max_tokens": 2000
                        }
                    )
                    
                    if response.status_code == 200:
                        data = response.json()
                        return {
                            "response": data["choices"][0]["message"]["content"],
                            "model": "gpt-3.5-turbo"
                        }
            except Exception as e:
                logger.error(f"OpenAI error: {e}")
        
        # Fallback to Ollama
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    f"{self.ollama_url}/api/generate",
                    json={
                        "model": "llama3.2:3b",
                        "prompt": prompt,
                        "temperature": temperature,
                        "stream": False
                    }
                )
                
                if response.status_code == 200:
                    data = response.json()
                    return {
                        "response": data.get("response", ""),
                        "model": "llama3.2:3b"
                    }
        except Exception as e:
            logger.error(f"Ollama error: {e}")
        
        # Emergency fallback for developer mode
        if is_developer:
            return {
                "response": """# File: /app/routers/emergency.py
from fastapi import APIRouter
router = APIRouter(prefix="/api/emergency")

@router.get("/status")
async def status():
    return {"status": "AI offline, but here's a template to get started"}""",
                "model": "template"
            }
        
        return {
            "response": "I'm having trouble connecting to the AI service. Please check the system status.",
            "model": "error"
        }

# Global instance
ai_client = EnhancedAI()

# Backward compatibility
async def get_ai_response(message: str, context: Dict = None) -> str:
    """Legacy function for compatibility"""
    result = await ai_client.generate_response(message, context)
    return result.get("response", "")
