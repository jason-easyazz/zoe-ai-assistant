"""Developer Router with REAL AI code generation"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, Dict, Any
import httpx
import json
import os
import sys
from datetime import datetime
import sqlite3
import logging

sys.path.append("/app")
logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/developer", tags=["developer'])

# Task storage
developer_tasks = {}

class DeveloperChat(BaseModel):
    message: str

class AICodeGenerator:
    """Actually uses AI to generate code"""
    
    def __init__(self):
        self.api_keys = self._load_api_keys()
        
    def _load_api_keys(self):
        """Load API keys from database"""
        keys = {}
        try:
            conn = sqlite3.connect("/app/data/zoe.db")
            cursor = conn.cursor()
            cursor.execute("SELECT service, encrypted_key FROM api_keys WHERE is_active = 1")
            for service, key in cursor.fetchall():
                keys[service] = key  # In production, decrypt here
            conn.close()
        except Exception as e:
            logger.error(f"Could not load API keys: {e}")
        return keys
    
    async def generate_code(self, request: str) -> str:
        """Generate code using AI"""
        
        # Build the prompt for code generation
        prompt = f"""You are Zack, a senior developer creating production code for the Zoe AI system.

System context:
- FastAPI backend at /app/routers/
- SQLite database at /app/data/zoe.db
- Docker containers with zoe- prefix
- Raspberry Pi 5 host

Request: {request}

Generate COMPLETE, PRODUCTION-READY code with:
1. All imports
2. Error handling  
3. Logging
4. Documentation
5. Test endpoints

Return ONLY the code starting with # File: /app/routers/[name].py
No explanations, just code."""

        # Try Claude first
        if "anthropic" in self.api_keys:
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.post(
                        "https://api.anthropic.com/v1/messages",
                        headers={
                            "x-api-key": self.api_keys['anthropic'],
                            "anthropic-version": "2023-06-01",
                            "content-type": "application/json"
                        },
                        json={
                            "model": "claude-3-haiku-20240307",
                            "max_tokens": 4000,
                            "temperature": 0.2,
                            "messages": [{"role": "user", "content": prompt}]
                        }
                    )
                    
                    if response.status_code == 200:
                        data = response.json()
                        return data["content'][0]["text']
            except Exception as e:
                logger.error(f"Claude error: {e}")
        
        # Try OpenAI
        if "openai" in self.api_keys:
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.post(
                        "https://api.openai.com/v1/chat/completions",
                        headers={
                            "Authorization": f"Bearer {self.api_keys['openai']}",
                            "Content-Type": "application/json"
                        },
                        json={
                            "model": "gpt-3.5-turbo",
                            "messages": [{"role": "system", "content": prompt}],
                            "temperature": 0.2,
                            "max_tokens": 3000
                        }
                    )
                    
                    if response.status_code == 200:
                        data = response.json()
                        return data["choices'][0]["message']["content']
            except Exception as e:
                logger.error(f"OpenAI error: {e}")
        
        # Fallback to Ollama
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    "http://zoe-ollama:11434/api/generate",
                    json={
                        "model": "llama3.2:3b",
                        "prompt": prompt,
                        "temperature": 0.2,
                        "stream": False
                    }
                )
                
                if response.status_code == 200:
                    data = response.json()
                    return data.get("response", "")
        except Exception as e:
            logger.error(f"Ollama error: {e}")
        
        # Emergency fallback
        return f"""# File: /app/routers/generated_feature.py
# AI services unavailable - template provided
from fastapi import APIRouter
router = APIRouter(prefix="/api/feature")

@router.get("/")
async def get_feature():
    return {{"message": "Implement for: {request}"}}"""

# Global code generator
code_generator = AICodeGenerator()

@router.get("/status")
async def get_status():
    """Check developer system status"""
    return {
        "status": "operational",
        "mode": "ai-powered-code-generator",
        "personality": "Zack",
        "ai_providers": list(code_generator.api_keys.keys()) or ["ollama'],
        "timestamp": datetime.now().isoformat()
    }

@router.post("/chat")
async def developer_chat(request: DeveloperChat):
    """Generate code using actual AI"""
    
    # Generate code using AI
    code = await code_generator.generate_code(request.message)
    
    # Store as task
    task_id = f"task_{datetime.now().strftime("%Y%m%d_%H%M%S")}"
    developer_tasks[task_id] = {
        "id": task_id,
        "request": request.message,
        "code": code,
        "created_at": datetime.now().isoformat()
    }
    
    return {
        "response": f"Generated code for: {request.message}",
        "code": code,
        "task_id": task_id,
        "ai_generated": True
    }

@router.get("/tasks")
async def list_tasks():
    """List all generated tasks"""
    return {"tasks": list(developer_tasks.values()), "count": len(developer_tasks)}

@router.post("/implement/{task_id}")
async def implement_task(task_id: str):
    """Write generated code to file"""
    
    if task_id not in developer_tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    
    task = developer_tasks[task_id]
    code = task["code']
    
    # Extract file path
    import re
    file_match = re.search(r"# File: (.*?)\n", code)
    if not file_match:
        return {"error": "No file path in code"}
    
    file_path = file_match.group(1)
    
    # Write file
    try:
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, "w") as f:
            code_clean = re.sub(r"# File: .*?\n", "", code)
            f.write(code_clean)
        
        return {
            "status": "implemented",
            "file_path": file_path,
            "task_id": task_id
        }
    except Exception as e:
        return {"error": str(e)}
