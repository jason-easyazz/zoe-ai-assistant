#!/bin/bash
# FIX_ZACK_CODE_GENERATION.sh
# Location: scripts/maintenance/fix_zack_code_generation.sh
# Purpose: Transform Zack from advice-giver to actual code generator

set -e

echo "🔧 TRANSFORMING ZACK INTO CODE-GENERATING LEAD DEVELOPER"
echo "========================================================="
echo ""
echo "This will fix Zack to generate ACTUAL code, not advice"
echo "Press Enter to continue..."
read

cd /home/pi/zoe

# Backup first
echo -e "\n📦 Creating backup..."
mkdir -p backups/$(date +%Y%m%d_%H%M%S)
cp -r services/zoe-core backups/$(date +%Y%m%d_%H%M%S)/

# Step 1: Create the enhanced AI client with explicit code generation prompts
echo -e "\n📝 Creating enhanced AI client with code generation..."
cat > services/zoe-core/ai_client_enhanced.py << 'EOF'
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
EOF

# Step 2: Create the new developer router with code generation workflow
echo -e "\n📝 Creating code-generating developer router..."
cat > services/zoe-core/routers/developer.py << 'EOF'
"""
Developer Router - Zack Code Generation System
Generates ACTUAL code, not advice
"""

from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
import subprocess
import json
import os
import sys
from datetime import datetime
import logging
import asyncio

# Add parent directory to path for imports
sys.path.append('/app')

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/developer", tags=["developer"])

# Import the enhanced AI client
try:
    from ai_client_enhanced import ai_client
except ImportError:
    logger.error("Could not import enhanced AI client, using fallback")
    from ai_client import ai_client

class DeveloperChat(BaseModel):
    message: str
    force_code: bool = True  # Always force code generation

class DeveloperTask(BaseModel):
    title: str
    description: str
    type: str = "feature"  # feature, fix, optimization, etc.

class CodeImplementation(BaseModel):
    task_id: str
    code: str
    file_path: str
    test_command: Optional[str] = None

# In-memory task storage (replace with DB in production)
developer_tasks = {}

@router.get("/status")
async def get_status():
    """Check developer system status"""
    
    # Get system metrics
    try:
        result = subprocess.run(
            "docker ps | grep zoe- | wc -l",
            shell=True, capture_output=True, text=True
        )
        container_count = int(result.stdout.strip())
        
        return {
            "status": "operational",
            "mode": "code-generator",
            "personality": "Zack",
            "containers_running": container_count,
            "capabilities": [
                "generate_code",
                "create_endpoints",
                "fix_bugs",
                "optimize_performance",
                "create_scripts"
            ]
        }
    except Exception as e:
        logger.error(f"Status check failed: {e}")
        return {"status": "degraded", "error": str(e)}

@router.post("/chat")
async def developer_chat(request: DeveloperChat):
    """
    Chat with Zack - ALWAYS generates code, not advice
    """
    try:
        # Force code generation context
        context = {
            "mode": "developer",
            "force_code": True,
            "instruction": "Generate complete, executable code. No explanations, just code."
        }
        
        # Get code from Zack
        response = await ai_client.generate_response(request.message, context)
        
        # Extract code if wrapped in backticks
        code = None
        if "```" in response["response"]:
            import re
            matches = re.findall(r'```(?:python|bash|javascript|yaml)?\n(.*?)```', 
                                response["response"], re.DOTALL)
            if matches:
                code = matches[0]
        
        return {
            "response": response["response"],
            "code": code,
            "model": response.get("model", "unknown"),
            "mode": "code-generator"
        }
        
    except Exception as e:
        logger.error(f"Developer chat error: {e}")
        # Even on error, provide code template
        return {
            "response": f"Error: {e}\nHere's a template to start with:",
            "code": """# Template when AI is offline
from fastapi import APIRouter
router = APIRouter(prefix="/api/new")

@router.get("/")
async def get_items():
    return {"items": []}""",
            "model": "fallback"
        }

@router.post("/plan")
async def create_plan(task: DeveloperTask):
    """
    Create a development plan that generates actual implementation
    """
    task_id = f"task_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    
    # Ask Zack to create implementation plan with code
    prompt = f"""Task: {task.title}
Description: {task.description}
Type: {task.type}

Generate a complete implementation with:
1. The main code file
2. Any required dependencies
3. Test commands
4. Database changes if needed

Start with the actual code:"""
    
    context = {
        "mode": "developer",
        "task_type": task.type,
        "force_code": True
    }
    
    response = await ai_client.generate_response(prompt, context)
    
    # Store the task
    developer_tasks[task_id] = {
        "id": task_id,
        "title": task.title,
        "description": task.description,
        "type": task.type,
        "implementation": response["response"],
        "status": "planned",
        "created_at": datetime.now().isoformat()
    }
    
    return {
        "task_id": task_id,
        "plan": response["response"],
        "status": "ready_to_implement"
    }

@router.get("/tasks")
async def list_tasks():
    """List all developer tasks"""
    return {
        "tasks": list(developer_tasks.values()),
        "count": len(developer_tasks)
    }

@router.post("/implement/{task_id}")
async def implement_task(task_id: str, background_tasks: BackgroundTasks):
    """
    Actually implement the task - write code to files
    """
    if task_id not in developer_tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    
    task = developer_tasks[task_id]
    
    # Parse the implementation to extract code
    implementation = task["implementation"]
    
    # Extract file paths and code blocks
    import re
    file_pattern = r'# File: (.*?)\n'
    code_pattern = r'```(?:python|bash|javascript|yaml)?\n(.*?)```'
    
    files = re.findall(file_pattern, implementation)
    codes = re.findall(code_pattern, implementation, re.DOTALL)
    
    results = []
    
    for file_path, code in zip(files, codes):
        # Security: only allow writing to specific directories
        if file_path.startswith("/app/routers/"):
            safe_path = file_path
        elif file_path.startswith("/home/pi/zoe/scripts/"):
            safe_path = file_path
        else:
            results.append({
                "file": file_path,
                "status": "skipped",
                "reason": "unsafe path"
            })
            continue
        
        try:
            # Create directories if needed
            os.makedirs(os.path.dirname(safe_path), exist_ok=True)
            
            # Write the code
            with open(safe_path, 'w') as f:
                f.write(code)
            
            # Make scripts executable
            if safe_path.endswith('.sh'):
                os.chmod(safe_path, 0o755)
            
            results.append({
                "file": safe_path,
                "status": "created",
                "size": len(code)
            })
            
        except Exception as e:
            results.append({
                "file": file_path,
                "status": "error",
                "error": str(e)
            })
    
    # Update task status
    task["status"] = "implemented"
    task["implementation_results"] = results
    task["implemented_at"] = datetime.now().isoformat()
    
    return {
        "task_id": task_id,
        "status": "implemented",
        "files_created": len([r for r in results if r["status"] == "created"]),
        "results": results
    }

@router.post("/test/{task_id}")
async def test_implementation(task_id: str):
    """
    Test the implemented code
    """
    if task_id not in developer_tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    
    task = developer_tasks[task_id]
    
    # Run basic tests
    test_results = []
    
    # Check if files exist
    for result in task.get("implementation_results", []):
        if result["status"] == "created":
            file_path = result["file"]
            exists = os.path.exists(file_path)
            test_results.append({
                "test": f"File exists: {file_path}",
                "passed": exists
            })
    
    # If it's an API endpoint, test it
    if "/routers/" in str(task.get("implementation_results", [])):
        try:
            # Restart the service to load new routes
            subprocess.run("docker compose restart zoe-core", shell=True, check=True)
            await asyncio.sleep(5)  # Wait for restart
            
            # Test health endpoint
            async with httpx.AsyncClient() as client:
                response = await client.get("http://localhost:8000/health")
                test_results.append({
                    "test": "API still healthy after changes",
                    "passed": response.status_code == 200
                })
        except Exception as e:
            test_results.append({
                "test": "Service restart",
                "passed": False,
                "error": str(e)
            })
    
    # Update task with test results
    task["test_results"] = test_results
    task["tested_at"] = datetime.now().isoformat()
    
    passed = sum(1 for t in test_results if t["passed"])
    total = len(test_results)
    
    return {
        "task_id": task_id,
        "tests_run": total,
        "tests_passed": passed,
        "success_rate": f"{(passed/total*100):.0f}%" if total > 0 else "0%",
        "results": test_results
    }

@router.post("/analyze")
async def analyze_system():
    """
    Analyze system and generate improvement code
    """
    # Gather system state
    system_data = {}
    
    try:
        # Check Python files
        result = subprocess.run(
            'find /app -name "*.py" | wc -l',
            shell=True, capture_output=True, text=True
        )
        system_data["python_files"] = int(result.stdout.strip())
        
        # Check routers
        result = subprocess.run(
            'ls /app/routers/*.py 2>/dev/null | wc -l',
            shell=True, capture_output=True, text=True
        )
        system_data["routers"] = int(result.stdout.strip())
        
        # Check disk usage
        result = subprocess.run(
            'df -h / | tail -1 | awk \'{print $5}\'',
            shell=True, capture_output=True, text=True
        )
        system_data["disk_usage"] = result.stdout.strip()
        
    except Exception as e:
        logger.error(f"System analysis error: {e}")
    
    # Ask Zack to generate optimization code
    prompt = f"""System Analysis:
- Python files: {system_data.get('python_files', 'unknown')}
- API routers: {system_data.get('routers', 'unknown')}
- Disk usage: {system_data.get('disk_usage', 'unknown')}

Generate code to optimize the system. Create actual scripts, not suggestions."""
    
    response = await ai_client.generate_response(
        prompt,
        {"mode": "developer", "force_code": True}
    )
    
    return {
        "analysis": system_data,
        "optimization_code": response["response"],
        "model": response.get("model", "unknown")
    }

# Error handler for the router
@router.exception_handler(Exception)
async def developer_exception_handler(request, exc):
    logger.error(f"Developer router exception: {exc}")
    return {
        "error": str(exc),
        "fallback": "Check logs at: docker logs zoe-core --tail 50",
        "status": "error"
    }
EOF

# Step 3: Update main.py to use the enhanced AI client
echo -e "\n📝 Updating main.py to use enhanced AI..."
cat > services/zoe-core/main_patch.py << 'EOF'
import sys
import os

# Read main.py
main_path = "/app/main.py"
if os.path.exists(main_path):
    with open(main_path, 'r') as f:
        content = f.read()
    
    # Update AI client import
    if "from ai_client import" in content:
        content = content.replace(
            "from ai_client import",
            "from ai_client_enhanced import"
        )
    elif "import ai_client" in content:
        content = content.replace(
            "import ai_client",
            "import ai_client_enhanced as ai_client"
        )
    
    # Ensure developer router is included
    if "routers.developer" not in content:
        # Add import
        content = content.replace(
            "from routers import",
            "from routers import developer,"
        )
        # Add router inclusion
        if "app.include_router(developer.router)" not in content:
            content = content.replace(
                "# Include routers",
                "# Include routers\napp.include_router(developer.router)"
            )
    
    # Write back
    with open(main_path, 'w') as f:
        f.write(content)
    
    print("✅ main.py updated")
else:
    print("⚠️ main.py not found, creating minimal version")
    # Create minimal main.py
    with open(main_path, 'w') as f:
        f.write("""from fastapi import FastAPI
from routers import developer

app = FastAPI(title="Zoe AI")

app.include_router(developer.router)

@app.get("/health")
async def health():
    return {"status": "healthy"}
""")
EOF

docker exec zoe-core python3 /app/main_patch.py

# Step 4: Copy files to container
echo -e "\n📦 Deploying to container..."
docker cp services/zoe-core/ai_client_enhanced.py zoe-core:/app/
docker cp services/zoe-core/routers/developer.py zoe-core:/app/routers/
docker cp services/zoe-core/main_patch.py zoe-core:/app/

# Step 5: Restart service
echo -e "\n🔄 Restarting service..."
docker compose restart zoe-core
sleep 10

# Step 6: Test the new code generation
echo -e "\n🧪 Testing Zack's code generation..."

echo "Test 1: Ask Zack to generate code"
curl -X POST http://localhost:8000/api/developer/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Build a health monitoring endpoint that returns CPU, memory, and disk usage"}' \
  | jq '.code' | head -20

echo -e "\n✅ Complete! Zack will now generate actual code instead of giving advice."
echo ""
echo "Try these commands to test:"
echo ""
echo "1. Ask for code generation:"
echo '   curl -X POST http://localhost:8000/api/developer/chat \'
echo '     -d '\''{"message": "Build a backup endpoint"}'\'''
echo ""
echo "2. Create a development task:"
echo '   curl -X POST http://localhost:8000/api/developer/plan \'
echo '     -d '\''{"title": "Add backup system", "description": "Create endpoints for backup and restore"}'\'''
echo ""
echo "3. Check system analysis:"
echo '   curl -X POST http://localhost:8000/api/developer/analyze'
echo ""
echo "Zack is now a true lead developer who writes code! 🚀"
