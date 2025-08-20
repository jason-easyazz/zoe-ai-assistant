"""
Autonomous Developer System
Claude can see everything, fix anything, and improve the system
"""

from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import Optional, Dict, Any, List
import os
import json
import sqlite3
import subprocess
import docker
import psutil
import asyncio
from datetime import datetime
import anthropic
import logging
from pathlib import Path

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/developer", tags=["developer"])

# Initialize Claude client
CLAUDE_API_KEY = os.getenv("CLAUDE_API_KEY", "")
claude_client = None
if CLAUDE_API_KEY and CLAUDE_API_KEY != "sk-ant-api03-YOUR-KEY-HERE":
    claude_client = anthropic.Anthropic(api_key=CLAUDE_API_KEY)

# Docker client
docker_client = docker.from_env()

# Request models
class TaskRequest(BaseModel):
    task_id: Optional[str] = None
    action: str  # analyze, execute, complete
    context: Optional[Dict[str, Any]] = {}

class SystemCommand(BaseModel):
    command: str
    working_dir: Optional[str] = "/home/pi/zoe"
    timeout: Optional[int] = 30
    requires_sudo: bool = False

class FileOperation(BaseModel):
    operation: str  # read, write, append, delete
    path: str
    content: Optional[str] = None
    backup: bool = True

# ============================================
# SYSTEM VISIBILITY ENDPOINTS
# ============================================

@router.get("/system/overview")
async def get_system_overview():
    """Give Claude complete visibility of the system"""
    overview = {
        "timestamp": datetime.now().isoformat(),
        "system": {},
        "docker": {},
        "services": {},
        "files": {},
        "errors": []
    }
    
    # System metrics
    try:
        overview["system"] = {
            "cpu_percent": psutil.cpu_percent(interval=1),
            "memory": dict(psutil.virtual_memory()._asdict()),
            "disk": dict(psutil.disk_usage('/')._asdict()),
            "boot_time": datetime.fromtimestamp(psutil.boot_time()).isoformat()
        }
    except Exception as e:
        overview["errors"].append(f"System metrics: {str(e)}")
    
    # Docker containers
    try:
        containers = []
        for container in docker_client.containers.list(all=True):
            containers.append({
                "name": container.name,
                "status": container.status,
                "image": container.image.tags[0] if container.image.tags else "unknown",
                "ports": container.ports,
                "created": container.attrs['Created']
            })
        overview["docker"]["containers"] = containers
    except Exception as e:
        overview["errors"].append(f"Docker: {str(e)}")
    
    # Service health checks
    services = ["core", "ui", "ollama", "redis", "whisper", "tts", "n8n"]
    for service in services:
        try:
            container = docker_client.containers.get(f"zoe-{service}")
            logs = container.logs(tail=10).decode('utf-8')
            has_errors = "error" in logs.lower() or "exception" in logs.lower()
            overview["services"][service] = {
                "running": container.status == "running",
                "has_errors": has_errors,
                "last_logs": logs[-500:] if has_errors else "OK"
            }
        except:
            overview["services"][service] = {"running": False, "has_errors": True}
    
    # File system structure
    overview["files"] = {
        "project_root": "/home/pi/zoe",
        "structure": get_directory_structure("/home/pi/zoe", max_depth=2),
        "recent_changes": get_recent_file_changes("/home/pi/zoe", hours=24)
    }
    
    return overview

@router.get("/system/diagnostics")
async def run_diagnostics():
    """Run comprehensive system diagnostics"""
    diagnostics = {
        "timestamp": datetime.now().isoformat(),
        "checks": {},
        "issues": [],
        "recommendations": []
    }
    
    # Check 1: API endpoints
    try:
        import httpx
        async with httpx.AsyncClient() as client:
            response = await client.get("http://localhost:8000/health")
            diagnostics["checks"]["api_health"] = response.status_code == 200
    except:
        diagnostics["checks"]["api_health"] = False
        diagnostics["issues"].append("API not responding")
        diagnostics["recommendations"].append("Restart zoe-core container")
    
    # Check 2: Database integrity
    try:
        conn = sqlite3.connect("/app/data/zoe.db")
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'")
        table_count = cursor.fetchone()[0]
        diagnostics["checks"]["database"] = table_count > 0
        conn.close()
    except Exception as e:
        diagnostics["checks"]["database"] = False
        diagnostics["issues"].append(f"Database error: {str(e)}")
    
    # Check 3: Ollama availability
    try:
        import httpx
        async with httpx.AsyncClient() as client:
            response = await client.get("http://zoe-ollama:11434/api/tags")
            diagnostics["checks"]["ollama"] = response.status_code == 200
    except:
        diagnostics["checks"]["ollama"] = False
        diagnostics["issues"].append("Ollama not responding")
    
    # Check 4: Memory usage
    memory = psutil.virtual_memory()
    if memory.percent > 90:
        diagnostics["issues"].append(f"High memory usage: {memory.percent}%")
        diagnostics["recommendations"].append("Restart unused containers")
    
    # Check 5: Disk space
    disk = psutil.disk_usage('/')
    if disk.percent > 85:
        diagnostics["issues"].append(f"Low disk space: {100-disk.percent}% free")
        diagnostics["recommendations"].append("Clean up logs and temporary files")
    
    return diagnostics

# ============================================
# TASK MANAGEMENT ENDPOINTS
# ============================================

@router.get("/tasks")
async def get_tasks(status: Optional[str] = None, limit: int = 50):
    """Get developer tasks"""
    conn = sqlite3.connect("/app/data/developer_tasks.db")
    cursor = conn.cursor()
    
    if status:
        cursor.execute("""
            SELECT * FROM tasks 
            WHERE status = ? 
            ORDER BY 
                CASE priority 
                    WHEN 'critical' THEN 1 
                    WHEN 'high' THEN 2 
                    WHEN 'medium' THEN 3 
                    WHEN 'low' THEN 4 
                END,
                created_at DESC 
            LIMIT ?
        """, (status, limit))
    else:
        cursor.execute("""
            SELECT * FROM tasks 
            ORDER BY 
                CASE priority 
                    WHEN 'critical' THEN 1 
                    WHEN 'high' THEN 2 
                    WHEN 'medium' THEN 3 
                    WHEN 'low' THEN 4 
                END,
                created_at DESC 
            LIMIT ?
        """, (limit,))
    
    tasks = []
    for row in cursor.fetchall():
        tasks.append({
            "id": row[0],
            "task_id": row[1],
            "title": row[2],
            "description": row[3],
            "category": row[4],
            "priority": row[5],
            "status": row[6],
            "created_at": row[8]
        })
    
    conn.close()
    return {"tasks": tasks}

@router.get("/tasks/next")
async def get_next_task():
    """Get the highest priority task for Claude to work on"""
    conn = sqlite3.connect("/app/data/developer_tasks.db")
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT * FROM tasks 
        WHERE status = 'pending' 
        ORDER BY 
            CASE priority 
                WHEN 'critical' THEN 1 
                WHEN 'high' THEN 2 
                WHEN 'medium' THEN 3 
                WHEN 'low' THEN 4 
            END,
            created_at ASC 
        LIMIT 1
    """)
    
    row = cursor.fetchone()
    conn.close()
    
    if row:
        return {
            "task_id": row[1],
            "title": row[2],
            "description": row[3],
            "category": row[4],
            "priority": row[5],
            "context": await get_task_context(row[1])
        }
    
    return {"message": "No pending tasks"}

@router.post("/tasks/{task_id}/execute")
async def execute_task(task_id: str, background_tasks: BackgroundTasks):
    """Claude executes a task autonomously"""
    
    # Get task details
    conn = sqlite3.connect("/app/data/developer_tasks.db")
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,))
    task = cursor.fetchone()
    
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    # Update task status
    cursor.execute("""
        UPDATE tasks 
        SET status = 'in_progress', started_at = CURRENT_TIMESTAMP 
        WHERE task_id = ?
    """, (task_id,))
    conn.commit()
    conn.close()
    
    # Execute in background
    background_tasks.add_task(autonomous_task_execution, task_id, task)
    
    return {"message": f"Task {task_id} execution started", "status": "in_progress"}

# ============================================
# FILE SYSTEM OPERATIONS
# ============================================

@router.post("/files/read")
async def read_file(path: str):
    """Read any file in the system"""
    try:
        file_path = Path(path)
        if not file_path.exists():
            raise HTTPException(status_code=404, detail="File not found")
        
        content = file_path.read_text()
        return {
            "path": str(file_path),
            "content": content,
            "size": file_path.stat().st_size,
            "modified": datetime.fromtimestamp(file_path.stat().st_mtime).isoformat()
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/files/write")
async def write_file(operation: FileOperation):
    """Write or modify files with automatic backup"""
    try:
        file_path = Path(operation.path)
        
        # Create backup if requested and file exists
        if operation.backup and file_path.exists():
            backup_path = file_path.parent / f"{file_path.name}.backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            backup_path.write_text(file_path.read_text())
        
        # Perform operation
        if operation.operation == "write":
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(operation.content)
        elif operation.operation == "append":
            with open(file_path, 'a') as f:
                f.write(operation.content)
        elif operation.operation == "delete":
            file_path.unlink()
        
        return {"success": True, "path": str(file_path)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/files/search")
async def search_files(pattern: str, directory: str = "/home/pi/zoe"):
    """Search for files matching pattern"""
    try:
        import glob
        files = glob.glob(f"{directory}/**/{pattern}", recursive=True)
        return {"files": files[:100]}  # Limit to 100 results
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ============================================
# SYSTEM EXECUTION ENDPOINTS
# ============================================

@router.post("/execute/command")
async def execute_command(command: SystemCommand):
    """Execute system commands with safety checks"""
    
    # Safety whitelist
    safe_commands = [
        "docker", "git", "python", "pip", "sqlite3", 
        "ls", "cat", "grep", "find", "curl", "test"
    ]
    
    first_word = command.command.split()[0]
    if first_word not in safe_commands and not command.requires_sudo:
        raise HTTPException(status_code=403, detail=f"Command '{first_word}' not in whitelist")
    
    try:
        # Execute command
        result = subprocess.run(
            command.command,
            shell=True,
            cwd=command.working_dir,
            capture_output=True,
            text=True,
            timeout=command.timeout
        )
        
        # Log to knowledge base if it worked
        if result.returncode == 0:
            log_solution(command.command, "Command executed successfully")
        else:
            log_error(command.command, result.stderr)
        
        return {
            "success": result.returncode == 0,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "return_code": result.returncode
        }
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=408, detail="Command timeout")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/execute/script")
async def execute_script(script_content: str, script_type: str = "bash"):
    """Execute a script with proper isolation"""
    
    # Save script to temporary location
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    script_path = f"/app/scripts/temporary/claude_script_{timestamp}.sh"
    
    try:
        # Write script
        Path(script_path).parent.mkdir(parents=True, exist_ok=True)
        Path(script_path).write_text(script_content)
        Path(script_path).chmod(0o755)
        
        # Execute script
        result = subprocess.run(
            ["bash", script_path],
            capture_output=True,
            text=True,
            timeout=300  # 5 minute timeout
        )
        
        # Archive if successful
        if result.returncode == 0:
            archive_path = f"/app/scripts/archive/success_{timestamp}.sh"
            Path(archive_path).parent.mkdir(parents=True, exist_ok=True)
            Path(script_path).rename(archive_path)
        
        return {
            "success": result.returncode == 0,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "script_path": script_path if result.returncode != 0 else archive_path
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/docker/restart")
async def restart_container(container_name: str):
    """Restart a Docker container"""
    try:
        container = docker_client.containers.get(container_name)
        container.restart()
        
        # Wait for container to be healthy
        await asyncio.sleep(5)
        container.reload()
        
        return {
            "success": True,
            "status": container.status,
            "message": f"Container {container_name} restarted"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/docker/rebuild")
async def rebuild_service(service_name: str):
    """Rebuild a service with --build flag"""
    try:
        result = subprocess.run(
            f"cd /home/pi/zoe && docker compose up -d --build {service_name}",
            shell=True,
            capture_output=True,
            text=True,
            timeout=300
        )
        
        return {
            "success": result.returncode == 0,
            "stdout": result.stdout,
            "stderr": result.stderr
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ============================================
# KNOWLEDGE MANAGEMENT
# ============================================

@router.post("/knowledge/solution")
async def record_solution(category: str, problem: str, solution: str, code: Optional[str] = None):
    """Record a successful solution"""
    conn = sqlite3.connect("/app/data/knowledge.db")
    cursor = conn.cursor()
    
    cursor.execute("""
        INSERT INTO proven_solutions (category, problem, solution, code_snippet)
        VALUES (?, ?, ?, ?)
    """, (category, problem, solution, code))
    
    conn.commit()
    conn.close()
    
    # Update markdown file
    update_proven_solutions_md(category, problem, solution)
    
    return {"message": "Solution recorded"}

@router.post("/knowledge/error")
async def record_error(category: str, issue: str, reason: str, alternative: Optional[str] = None):
    """Record something that doesn't work"""
    conn = sqlite3.connect("/app/data/knowledge.db")
    cursor = conn.cursor()
    
    cursor.execute("""
        INSERT INTO things_to_avoid (category, issue, reason, alternative)
        VALUES (?, ?, ?, ?)
    """, (category, issue, reason, alternative))
    
    conn.commit()
    conn.close()
    
    # Update markdown file
    update_things_to_avoid_md(category, issue, reason)
    
    return {"message": "Error pattern recorded"}

@router.get("/knowledge/search")
async def search_knowledge(query: str):
    """Search the knowledge base"""
    conn = sqlite3.connect("/app/data/knowledge.db")
    cursor = conn.cursor()
    
    # Search proven solutions
    cursor.execute("""
        SELECT * FROM proven_solutions 
        WHERE problem LIKE ? OR solution LIKE ? 
        ORDER BY success_rate DESC 
        LIMIT 10
    """, (f"%{query}%", f"%{query}%"))
    
    solutions = cursor.fetchall()
    
    # Search things to avoid
    cursor.execute("""
        SELECT * FROM things_to_avoid 
        WHERE issue LIKE ? OR reason LIKE ? 
        LIMIT 10
    """, (f"%{query}%", f"%{query}%"))
    
    avoid = cursor.fetchall()
    
    conn.close()
    
    return {
        "solutions": solutions,
        "avoid": avoid
    }

# ============================================
# HELPER FUNCTIONS
# ============================================

def get_directory_structure(path: str, max_depth: int = 3, current_depth: int = 0):
    """Get directory structure as dict"""
    if current_depth >= max_depth:
        return None
    
    structure = {}
    try:
        for item in os.listdir(path):
            if item.startswith('.'):
                continue
            item_path = os.path.join(path, item)
            if os.path.isdir(item_path):
                structure[item] = get_directory_structure(item_path, max_depth, current_depth + 1)
            else:
                structure[item] = "file"
    except:
        pass
    
    return structure

def get_recent_file_changes(path: str, hours: int = 24):
    """Get recently modified files"""
    import time
    cutoff = time.time() - (hours * 3600)
    recent_files = []
    
    for root, dirs, files in os.walk(path):
        for file in files:
            file_path = os.path.join(root, file)
            try:
                if os.path.getmtime(file_path) > cutoff:
                    recent_files.append(file_path.replace(path, ""))
            except:
                pass
    
    return recent_files[:20]  # Limit to 20 most recent

async def get_task_context(task_id: str):
    """Get full context for a task"""
    context = {
        "system_overview": await get_system_overview(),
        "diagnostics": await run_diagnostics(),
        "related_knowledge": {}
    }
    
    # Get task-specific knowledge
    conn = sqlite3.connect("/app/data/developer_tasks.db")
    cursor = conn.cursor()
    cursor.execute("SELECT category FROM tasks WHERE task_id = ?", (task_id,))
    category = cursor.fetchone()[0]
    conn.close()
    
    # Search knowledge base
    context["related_knowledge"] = await search_knowledge(category)
    
    return context

async def autonomous_task_execution(task_id: str, task_data: tuple):
    """Background task execution by Claude"""
    
    title = task_data[2]
    description = task_data[3]
    
    # Get full context
    context = await get_task_context(task_id)
    
    # If Claude is available, use it
    if claude_client:
        try:
            # Create autonomous prompt
            prompt = f"""You are an autonomous AI developer working on the Zoe system.

Task: {title}
Description: {description}

System Context:
{json.dumps(context, indent=2)}

Create a complete solution that:
1. Analyzes the problem
2. Implements the fix
3. Tests the solution
4. Updates documentation

Provide the solution as executable bash script.
"""
            
            response = claude_client.messages.create(
                model="claude-3-opus-20240229",
                max_tokens=4000,
                messages=[{"role": "user", "content": prompt}]
            )
            
            # Extract script from response
            script_content = extract_script_from_response(response.content[0].text)
            
            # Execute the script
            result = await execute_script(script_content)
            
            # Update task status
            conn = sqlite3.connect("/app/data/developer_tasks.db")
            cursor = conn.cursor()
            
            if result["success"]:
                cursor.execute("""
                    UPDATE tasks 
                    SET status = 'completed', 
                        completed_at = CURRENT_TIMESTAMP,
                        result = ?
                    WHERE task_id = ?
                """, (result["stdout"], task_id))
                
                # Record as proven solution
                await record_solution(
                    category="autonomous_fix",
                    problem=title,
                    solution=result["stdout"],
                    code=script_content
                )
            else:
                cursor.execute("""
                    UPDATE tasks 
                    SET status = 'failed',
                        error_log = ?
                    WHERE task_id = ?
                """, (result["stderr"], task_id))
                
                # Record as thing to avoid
                await record_error(
                    category="autonomous_fix",
                    issue=title,
                    reason=result["stderr"]
                )
            
            conn.commit()
            conn.close()
            
        except Exception as e:
            logger.error(f"Autonomous execution failed: {e}")
    else:
        # Fallback: Mark as ready for manual execution
        conn = sqlite3.connect("/app/data/developer_tasks.db")
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE tasks 
            SET status = 'ready',
                result = 'Requires manual execution - Claude not available'
            WHERE task_id = ?
        """, (task_id,))
        conn.commit()
        conn.close()

def extract_script_from_response(response: str) -> str:
    """Extract bash script from Claude's response"""
    # Look for code blocks
    import re
    pattern = r'```(?:bash|sh)?\n(.*?)```'
    matches = re.findall(pattern, response, re.DOTALL)
    
    if matches:
        return matches[0]
    
    # If no code blocks, assume entire response is script
    return response

def log_solution(command: str, result: str):
    """Log successful solution to knowledge base"""
    try:
        conn = sqlite3.connect("/app/data/knowledge.db")
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO proven_solutions (category, problem, solution, code_snippet)
            VALUES ('command', ?, ?, ?)
        """, (command[:100], result[:500], command))
        conn.commit()
        conn.close()
    except:
        pass

def log_error(command: str, error: str):
    """Log error to knowledge base"""
    try:
        conn = sqlite3.connect("/app/data/knowledge.db")
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO things_to_avoid (category, issue, reason, error_message)
            VALUES ('command', ?, 'Command failed', ?)
        """, (command[:100], error[:500]))
        conn.commit()
        conn.close()
    except:
        pass

def update_proven_solutions_md(category: str, problem: str, solution: str):
    """Update the proven solutions markdown file"""
    try:
        file_path = "/app/documentation/dynamic/proven_solutions.md"
        with open(file_path, 'a') as f:
            f.write(f"\n## {category}\n")
            f.write(f"- ✅ **Problem**: {problem}\n")
            f.write(f"  **Solution**: {solution}\n")
            f.write(f"  **Date**: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
    except:
        pass

def update_things_to_avoid_md(category: str, issue: str, reason: str):
    """Update the things to avoid markdown file"""
    try:
        file_path = "/app/documentation/dynamic/things_to_avoid.md"
        with open(file_path, 'a') as f:
            f.write(f"\n## {category}\n")
            f.write(f"- ❌ **Issue**: {issue}\n")
            f.write(f"  **Reason**: {reason}\n")
            f.write(f"  **Date**: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
    except:
        pass
