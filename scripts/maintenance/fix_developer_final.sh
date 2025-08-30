#!/bin/bash
# FIX_DEVELOPER_FINAL.sh - Replace corrupted developer.py with working version

echo "🔧 FIXING DEVELOPER.PY COMPLETELY"
echo "================================="

cd /home/pi/zoe

# Create the fixed file
cat > /tmp/developer_fixed.py << 'PYEOF'
"""Developer Router with REAL AI code generation"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, Dict, Any
import httpx
import json
import os
import sys
from datetime import datetime
import logging
import base64
from pathlib import Path
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

sys.path.append("/app")
logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/developer", tags=["developer"])

# Task storage
developer_tasks = {}

class DeveloperChat(BaseModel):
    message: str

class AICodeGenerator:
    """Actually uses AI to generate code"""
    
    def __init__(self):
        self.api_keys = self._load_encrypted_keys()
        
    def _load_encrypted_keys(self):
        """Load from existing encrypted file at /app/data/api_keys.enc"""
        keys = {}
        
        # Your working encryption setup from Aug 23
        salt = b"zoe_api_key_salt_2024"
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100000
        )
        key = base64.urlsafe_b64encode(kdf.derive(b"zoe_secure_key_2024"))
        cipher = Fernet(key)
        
        enc_file = Path("/app/data/api_keys.enc")
        if enc_file.exists():
            try:
                with open(enc_file, "rb") as f:
                    encrypted = f.read()
                    decrypted = cipher.decrypt(encrypted)
                    keys = json.loads(decrypted)
                logger.info(f"Loaded {len(keys)} API keys from encrypted file")
            except Exception as e:
                logger.error(f"Failed to load encrypted keys: {e}")
        else:
            logger.warning("No encrypted keys file found")
        
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
        if "anthropic" in self.api_keys and self.api_keys["anthropic"]:
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.post(
                        "https://api.anthropic.com/v1/messages",
                        headers={
                            "x-api-key": self.api_keys["anthropic"],
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
                        return data["content"][0]["text"]
                    else:
                        logger.error(f"Claude API returned {response.status_code}")
            except Exception as e:
                logger.error(f"Claude error: {e}")
        
        # Try OpenAI
        if "openai" in self.api_keys and self.api_keys["openai"]:
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
                        return data["choices"][0]["message"]["content"]
                    else:
                        logger.error(f"OpenAI API returned {response.status_code}")
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
    
    # Get basic system info
    system_info = {
        "status": "operational",
        "mode": "ai-powered-code-generator",
        "personality": "Zack",
        "ai_providers": list(code_generator.api_keys.keys()) if code_generator.api_keys else ["ollama"],
        "timestamp": datetime.now().isoformat()
    }
    
    # Try to get container status
    try:
        import subprocess
        result = subprocess.run(
            "docker ps --format '{{.Names}}:{{.Status}}' | grep zoe-",
            shell=True, capture_output=True, text=True
        )
        if result.stdout:
            system_info["containers"] = result.stdout.strip().split('\n')
    except:
        pass
    
    return system_info

@router.post("/chat")
async def developer_chat(request: DeveloperChat):
    """Generate code using actual AI"""
    
    # Generate code using AI
    code = await code_generator.generate_code(request.message)
    
    # Store as task
    task_id = f"task_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
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
    code = task["code"]
    
    # Extract file path
    import re
    file_match = re.search(r"# File: (.*?)\n", code)
    if not file_match:
        return {"error": "No file path in code"}
    
    file_path = file_match.group(1)
    
    # Security check - only allow writing to /app/routers/
    if not file_path.startswith("/app/routers/"):
        return {"error": "Can only write to /app/routers/ directory"}
    
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

# Add the backup endpoints that were working
@router.post("/system-backup")
async def create_system_backup():
    """Create complete system backup"""
    import tarfile
    import shutil
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_id = f"system_{timestamp}"
    os.makedirs("/app/data/backups", exist_ok=True)
    backup_file = f"/app/data/backups/{backup_id}.tar.gz"
    
    try:
        with tarfile.open(backup_file, "w:gz") as tar:
            if os.path.exists("/app/data/zoe.db"):
                tar.add("/app/data/zoe.db", arcname="zoe.db")
            if os.path.exists("/app/routers"):
                tar.add("/app/routers", arcname="routers")
            if os.path.exists("/app/main.py"):
                tar.add("/app/main.py", arcname="main.py")
            if os.path.exists("/app/data/api_keys.enc"):
                tar.add("/app/data/api_keys.enc", arcname="api_keys.enc")
        
        return {
            "status": "success",
            "backup_id": backup_id,
            "file": backup_file,
            "size": os.path.getsize(backup_file)
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}

@router.get("/system-backups")
async def list_system_backups():
    """List all system backups"""
    backup_dir = "/app/data/backups"
    
    if not os.path.exists(backup_dir):
        return {"backups": [], "count": 0}
    
    backups = []
    for file in os.listdir(backup_dir):
        if file.startswith("system_") and file.endswith(".tar.gz"):
            file_path = f"{backup_dir}/{file}"
            backups.append({
                "id": file.replace(".tar.gz", ""),
                "file": file,
                "size": os.path.getsize(file_path),
                "created": os.path.getctime(file_path)
            })
    
    backups.sort(key=lambda x: x["created"], reverse=True)
    return {"backups": backups, "count": len(backups)}
PYEOF

echo "✅ Created fixed developer.py"

# Copy to container
docker cp /tmp/developer_fixed.py zoe-core:/app/routers/developer.py

# Restart
echo "🔄 Restarting service..."
docker compose restart zoe-core
sleep 8

# Test
echo -e "\n🧪 Testing fixed developer endpoint..."
curl -s http://localhost:8000/api/developer/status | jq '.'

echo -e "\n✅ Developer.py fixed and deployed!"
