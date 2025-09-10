"""Enhanced Developer Router with Genius-Level AI Integration"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import subprocess
import json
import os
import sys
from typing import Optional, Dict, List
import re
from datetime import datetime

# Add AI client to path
sys.path.append('/app')

router = APIRouter(prefix="/api/developer", tags=["developer"])

# Import AI capabilities
try:
    from ai_client import ai_client
    HAS_AI = True
except:
    HAS_AI = False
    print("Warning: AI client not available")

# Import code reviewer
try:
    from code_reviewer import code_reviewer
    HAS_CODE_REVIEW = True
except:
    HAS_CODE_REVIEW = False
    print("Warning: Code reviewer not available")

# Import backup system
try:
    from backup_system import backup_system
    HAS_BACKUP = True
except:
    HAS_BACKUP = False
    print("Warning: Backup system not available")

class ChatMessage(BaseModel):
    message: str

class CommandRequest(BaseModel):
    command: str
    safe_mode: bool = True
    timeout: int = 30

class TaskConfirmationRequest(BaseModel):
    title: str
    objective: str
    requirements: list

class CodeReviewRequest(BaseModel):
    code: str
    file_path: Optional[str] = None

# System knowledge base for Zack
SYSTEM_KNOWLEDGE = """
You are Zack, the LEAD DEVELOPER and architect of the Zoe AI Assistant system.

YOUR EXPERTISE:
- Full-stack development (Python, JavaScript, Docker, FastAPI)
- System architecture and design patterns
- Performance optimization and scaling
- Security best practices
- AI/ML integration
- Database design and optimization
- DevOps and CI/CD

SYSTEM YOU MANAGE:
- 7 Docker containers: zoe-core (API), zoe-ui (frontend), zoe-ollama (AI), zoe-redis (cache), zoe-whisper (STT), zoe-tts (TTS), zoe-n8n (automation)
- FastAPI backend with multiple routers (chat, calendar, lists, memory, settings, developer)
- Glass-morphic UI with 7 main pages
- SQLite database for persistence
- Ollama for local AI (llama3.2:3b model)
- Dual AI personalities: Zoe (friendly assistant) and you (developer)
- Running on Raspberry Pi 5 (8GB RAM, 128GB storage)

ADVANCED DYNAMIC TASK SYSTEM:
- Context-aware task management at `/api/developer/tasks/*`
- Tasks store requirements, not implementations
- Re-analyzes system state at execution time
- Generates adaptive plans based on current context
- Handles conflicts automatically
- Task UI available at `http://192.168.1.60:8080/developer/tasks.html`
- Can create, analyze, and execute dynamic tasks

YOUR CAPABILITIES:
- Execute any system command
- Analyze code and architecture
- Identify bugs and performance issues
- Suggest improvements and optimizations
- Implement fixes autonomously
- Deploy new features
- Monitor system health
- Manage Docker containers
- Access and modify any file
- Query and optimize databases
- Create and manage dynamic tasks
- Analyze system state for task execution
- Generate adaptive implementation plans

YOUR PERSONALITY:
- Brilliant and analytical
- Direct and efficient
- Proactive problem-solver
- Detail-oriented
- Security-conscious
- Performance-focused
- Always thinking about scalability

RESPONSE FORMATTING RULES:
- MAXIMUM 200 WORDS - COUNT YOUR WORDS
- Use **bold** for key points only
- Use `code` for file names and technical terms
- Use bullet points (-) for lists
- Use ```code blocks``` for actual code examples
- Lead with the most important information
- Be concise and actionable
- Structure: Problem → Solution → Action
- Include specific file paths when relevant
- NO rambling or excessive detail
- FOLLOW THE USER'S SPECIFIC INSTRUCTIONS EXACTLY
"""

def execute_command(cmd: str, timeout: int = 30, cwd: str = None) -> dict:
    """Execute system command and return results"""
    try:
        if cwd is None:
            cwd = "/home/pi/zoe" if os.path.exists("/home/pi/zoe") else "/app"
        
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd
        )
        return {
            "success": result.returncode == 0,
            "stdout": result.stdout[:10000],
            "stderr": result.stderr[:5000],
            "code": result.returncode
        }
    except Exception as e:
        return {"success": False, "stdout": "", "stderr": str(e), "code": -1}

def get_system_context() -> str:
    """Gather current system context for intelligent responses"""
    context_parts = []
    
    # Get Docker status
    docker_result = execute_command("docker ps --format '{{.Names}}: {{.Status}}'")
    if docker_result["success"]:
        context_parts.append(f"Docker Status:\n{docker_result['stdout']}")
    
    # Get memory usage
    mem_result = execute_command("free -h | head -2")
    if mem_result["success"]:
        context_parts.append(f"Memory:\n{mem_result['stdout']}")
    
    # Get disk usage
    disk_result = execute_command("df -h / | tail -1")
    if disk_result["success"]:
        context_parts.append(f"Disk:\n{disk_result['stdout']}")
    
    # Get recent errors from logs
    log_result = execute_command("docker logs zoe-core --tail 20 2>&1 | grep -i error || echo 'No recent errors'")
    if log_result["success"]:
        context_parts.append(f"Recent Logs:\n{log_result['stdout'][:500]}")
    
    # Get task system status
    try:
        import sqlite3
        conn = sqlite3.connect('/app/data/developer_tasks.db')
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM dynamic_tasks WHERE status = 'pending'")
        pending_tasks = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM dynamic_tasks WHERE status = 'executing'")
        executing_tasks = cursor.fetchone()[0]
        conn.close()
        context_parts.append(f"Task System: {pending_tasks} pending, {executing_tasks} executing")
    except:
        context_parts.append("Task System: Database not accessible")
    
    return "\n\n".join(context_parts)

def extract_task_from_conversation(user_message: str, ai_response: str) -> dict:
    """
    Extract task details from the conversation
    Make it simple and direct
    """
    # Extract a clear title from the user message
    title = user_message[:50] + "..." if len(user_message) > 50 else user_message
    
    # Simple requirements based on the user's request
    requirements = [
        "Implement the requested feature",
        "Ensure compatibility with existing system",
        "Add proper error handling",
        "Test the implementation"
    ]
    
    return {
        "title": title,
        "objective": user_message,
        "requirements": requirements,
        "ready_to_create": True
    }

def extract_title(user_message: str) -> str:
    """Extract a concise title from user message"""
    # Simple title extraction - take first 50 chars or first sentence
    if len(user_message) <= 50:
        return user_message
    else:
        # Try to find a natural break point
        for i in range(40, 60):
            if user_message[i] in '.!?':
                return user_message[:i+1]
        return user_message[:50] + "..."

def extract_requirements(ai_response: str) -> list:
    """Extract requirements from AI response"""
    # Look for bullet points, numbered lists, or key phrases
    requirements = []
    
    # Split by lines and look for requirements
    lines = ai_response.split('\n')
    for line in lines:
        line = line.strip()
        if line.startswith(('-', '*', '•')) or line.startswith(tuple('123456789')):
            # Clean up the line
            clean_line = re.sub(r'^[-*•\d\.\s]+', '', line).strip()
            if clean_line and len(clean_line) > 10:
                requirements.append(clean_line)
    
    # If no bullet points found, extract key sentences
    if not requirements:
        sentences = re.split(r'[.!?]+', ai_response)
        for sentence in sentences:
            sentence = sentence.strip()
            if len(sentence) > 20 and any(word in sentence.lower() for word in ['need', 'require', 'should', 'must', 'implement', 'add', 'create']):
                requirements.append(sentence)
    
    # Default requirements if none found
    if not requirements:
        requirements = [
            "Implement the requested feature",
            "Ensure compatibility with existing system",
            "Add proper error handling",
            "Test the implementation"
        ]
    
    return requirements[:5]  # Limit to 5 requirements

async def generate_multiple_responses(user_message: str, system_context: str) -> list:
    """Generate multiple response approaches for better quality"""
    import asyncio
    import time
    
    responses = []
    
    # Single focused approach - no more verbose multi-approach
    approaches = [
        {
            "name": "focused",
            "prompt": f"""{SYSTEM_KNOWLEDGE}

Current System State:
{system_context}

User: {user_message}

CRITICAL INSTRUCTIONS:
- MAXIMUM 200 WORDS - COUNT YOUR WORDS CAREFULLY
- If you exceed 200 words, your response will be TRUNCATED
- Give a SHORT, DIRECT response
- Focus on the most important point first
- Be actionable and specific
- If suggesting a task, be clear about it
- Use simple formatting
- FOLLOW THE USER'S SPECIFIC INSTRUCTIONS EXACTLY
- REMEMBER: SHORT AND SWEET, NOT LONG AND RAMBLING

Respond:"""
        }
    ]
    
    # Generate responses in parallel
    tasks = []
    for approach in approaches:
        task = generate_single_response(approach, user_message)
        tasks.append(task)
    
    # Wait for all responses
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    # Process results
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            continue
        if result:
            responses.append({
                "approach": approaches[i]["name"],
                "content": result["content"],
                "thinking_time": result["thinking_time"],
                "confidence": result["confidence"]
            })
    
    return responses

async def generate_single_response(approach: dict, user_message: str) -> dict:
    """Generate a single response with timing and confidence scoring"""
    import time
    
    start_time = time.time()
    
    try:
        # Get AI response
        ai_raw = await ai_client.generate_response(approach["prompt"])
        ai_response = ai_raw.get("response") if isinstance(ai_raw, dict) else ai_raw
        
        thinking_time = time.time() - start_time
        
        # Calculate confidence based on response characteristics
        confidence = calculate_confidence(ai_response, user_message)
        
        return {
            "content": ai_response,
            "thinking_time": thinking_time,
            "confidence": confidence
        }
        
    except Exception as e:
        return None

def calculate_confidence(response: str, user_message: str) -> float:
    """Calculate confidence score - EXTREMELY aggressive against long responses"""
    word_count = len(response.split())
    
    # EXTREME penalties for long responses
    if word_count <= 200:
        score = 0.9  # Very high score for short responses
    elif word_count <= 300:
        score = 0.6  # Medium score for close to limit
    elif word_count <= 400:
        score = 0.3  # Low score for exceeding limit
    else:
        score = 0.1  # Very low score for way over limit
    
    # Small bonuses for technical content
    technical_indicators = ['function', 'file', 'docker', 'api', 'code']
    technical_count = sum(1 for indicator in technical_indicators if indicator in response.lower())
    score += min(technical_count * 0.02, 0.05)
    
    return min(max(score, 0.0), 1.0)

def select_best_response(responses: list, user_message: str) -> dict:
    """Select the best response from multiple options"""
    if not responses:
        return {
            "content": "I need more time to analyze this properly. Could you provide more details?",
            "thinking_time": 0,
            "confidence": 0.3
        }
    
    # Score each response
    for response in responses:
        response["final_score"] = calculate_final_score(response, user_message)
    
    # Sort by final score and return the best
    best_response = max(responses, key=lambda x: x["final_score"])
    
    return best_response

def calculate_final_score(response: dict, user_message: str) -> float:
    """Calculate final score - EXTREMELY favor short responses"""
    content = response["content"]
    word_count = len(content.split())
    
    # Base score heavily weighted by word count
    if word_count <= 200:
        score = 0.95  # Almost perfect score for short responses
    elif word_count <= 300:
        score = 0.7   # Good score for close to limit
    elif word_count <= 400:
        score = 0.4   # Poor score for exceeding limit
    else:
        score = 0.1   # Terrible score for way over limit
    
    # Small bonus for structure
    if '**' in content:
        score += 0.05
    
    return min(max(score, 0.0), 1.0)

def enforce_word_limit(text: str, max_words: int) -> str:
    """Enforce strict word limit on response - AGGRESSIVE"""
    words = text.split()
    
    if len(words) <= max_words:
        return text
    
    # AGGRESSIVE truncation - just cut at max_words
    truncated = ' '.join(words[:max_words])
    
    # Add ellipsis if we truncated
    if len(words) > max_words:
        truncated += "..."
    
    return truncated

@router.post("/chat")
async def developer_chat(msg: ChatMessage):
    """
    Zack - Enhanced conversational developer assistant
    Deep thinking with multiple model evaluation for optimal responses
    """
    
    message_lower = msg.message.lower()
    
    # Quick command execution for specific requests
    if message_lower.startswith('/execute '):
        command = msg.message[9:]
        
        # Review command for safety if code review is available
        if HAS_CODE_REVIEW:
            review_result = code_reviewer.review_code(command)
            if review_result["should_block"]:
                return {
                    "response": f"❌ **Command blocked by code review:**\n\n{review_result['review_summary']}\n\n**Issues found:**\n" + 
                               "\n".join([f"- {issue['message']}" for issue in review_result['issues'][:3]]),
                    "executed": False,
                    "type": "blocked",
                    "code_review": review_result
                }
        
        result = execute_command(command)
        response = f"**Command:** `{command}`\n\n"
        if result["success"]:
            response += f"**Output:**\n```\n{result['stdout']}\n```"
        else:
            response += f"**Error:**\n```\n{result['stderr']}\n```"
        return {"response": response, "executed": True, "type": "command"}
    
    # For everything else, use enhanced AI with deep thinking
    if HAS_AI:
        # Check if user is explicitly asking for a task
        if any(phrase in message_lower for phrase in [
            "make a task", "create a task", "set up a task", "i need a task",
            "can you make a task", "please create a task", "task for"
        ]):
            # Direct task creation
            task_suggestion = {
                "title": msg.message[:50] + "..." if len(msg.message) > 50 else msg.message,
                "objective": msg.message,
                "requirements": [
                    "Implement the requested feature",
                    "Ensure compatibility with existing system",
                    "Add proper error handling",
                    "Test the implementation"
                ],
                "ready_to_create": True
            }
            
            response_text = f"**Task Ready**: {msg.message}\n\nI can create this task for you. Click the button below to confirm."
            response_text = enforce_word_limit(response_text, 200)
            
            return {
                "response": response_text,
                "suggested_task": task_suggestion,
                "type": "conversation_with_task",
                "ai_enhanced": True,
                "thinking_time": 0.1,
                "confidence": 0.9
            }
        
        # Gather comprehensive system context
        system_context = get_system_context()
        
        # Generate multiple response approaches
        responses = await generate_multiple_responses(msg.message, system_context)
        
        # Select the best response
        best_response = select_best_response(responses, msg.message)
        
        # Enforce word limit strictly
        original_word_count = len(best_response["content"].split())
        best_response["content"] = enforce_word_limit(best_response["content"], 200)
        final_word_count = len(best_response["content"].split())
        
        # Debug logging
        print(f"Zack response: {original_word_count} words -> {final_word_count} words")
        
        # Check if the response suggests creating a task
        task_suggestion = None
        if any(phrase in best_response["content"].lower() for phrase in [
            "create a task", "i can set up a task", "let me create a task",
            "i'll create a task", "should i create a task", "would you like me to create a task",
            "make a task", "set up a task", "i can create a task"
        ]):
            # Extract task details from the conversation
            task_suggestion = extract_task_from_conversation(msg.message, best_response["content"])
            
            return {
                "response": best_response["content"],
                "suggested_task": task_suggestion,
                "type": "conversation_with_task",
                "ai_enhanced": True,
                "thinking_time": best_response["thinking_time"],
                "confidence": best_response["confidence"]
            }
        else:
            return {
                "response": best_response["content"],
                "type": "conversation",
                "ai_enhanced": True,
                "thinking_time": best_response["thinking_time"],
                "confidence": best_response["confidence"]
            }
    
    else:
        # No AI available - provide helpful command-based response
        if any(word in message_lower for word in ['docker', 'container', 'status']):
            docker_result = execute_command("docker ps --format 'table {{.Names}}\t{{.Status}}'")
            return {
                "response": f"**Docker Status (AI not available):**\n```\n{docker_result['stdout']}\n```",
                "executed": True,
                "ai_enhanced": False,
                "type": "command"
            }
        else:
            return {
                "response": "AI integration not available. Use `/execute <command>` for direct command execution.",
                "executed": False,
                "ai_enhanced": False,
                "type": "error"
            }

@router.post("/chat/confirm-task")
async def confirm_task_creation(task_data: TaskConfirmationRequest):
    """
    User clicked 'Create Task' after Zack suggested it
    """
    try:
        # Import the task creation function
        from routers.developer_tasks import create_dynamic_task, TaskRequirements
        
        # Create task requirements
        requirements = TaskRequirements(
            title=task_data.title,
            objective=task_data.objective,
            requirements=task_data.requirements,
            constraints=["Don't break existing features", "Maintain system stability"],
            acceptance_criteria=["Feature works as described", "No regressions introduced"],
            priority="medium"
        )
        
        # Create the task
        result = await create_dynamic_task(requirements)
        
        return {
            "response": f"✅ Created task: {result['task_id']}\n\nI'll analyze the system when you're ready to execute.",
            "task_id": result["task_id"],
            "status": "created"
        }
        
    except Exception as e:
        return {
            "response": f"❌ Failed to create task: {str(e)}",
            "status": "error"
        }

@router.post("/analyze")
async def analyze_system():
    """Perform intelligent system analysis"""
    
    if not HAS_AI:
        return {"error": "AI not available for analysis"}
    
    # Gather comprehensive system data
    analysis_data = {
        "containers": execute_command("docker ps -a --format json"),
        "memory": execute_command("free -h"),
        "disk": execute_command("df -h"),
        "processes": execute_command("ps aux --sort=-%cpu | head -20"),
        "errors": execute_command("docker logs zoe-core --tail 50 2>&1 | grep -i error"),
        "network": execute_command("netstat -tuln | grep LISTEN")
    }
    
    # Build analysis prompt
    prompt = f"""{SYSTEM_KNOWLEDGE}

Perform a comprehensive system analysis based on this data:

{json.dumps(analysis_data, indent=2)}

Provide:
1. System health assessment (score 1-10)
2. Identified issues or concerns
3. Performance bottlenecks
4. Security considerations
5. Specific recommendations with priority
6. Immediate actions needed

Be thorough and technical."""
    
    try:
        analysis = await ai_client.generate_response(prompt)
        
        return {
            "analysis": analysis,
            "data": analysis_data,
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        return {"error": f"Analysis failed: {str(e)}"}

@router.get("/suggestions")
async def get_improvement_suggestions():
    """Get AI-powered improvement suggestions"""
    
    if not HAS_AI:
        return {"error": "AI not available for suggestions"}
    
    # Get current system state
    context = get_system_context()
    
    prompt = f"""{SYSTEM_KNOWLEDGE}

Based on the current system state:
{context}

Provide 5 specific, actionable improvements we should implement, ordered by priority.

For each suggestion include:
- What to improve
- Why it's important
- How to implement it
- Expected impact
- Any risks to consider

Focus on practical improvements that would have the most impact."""
    
    try:
        suggestions = await ai_client.generate_response(prompt)
        return {
            "suggestions": suggestions,
            "generated_at": datetime.now().isoformat()
        }
    except Exception as e:
        return {"error": f"Could not generate suggestions: {str(e)}"}

@router.post("/execute")
async def execute_direct(cmd: CommandRequest):
    """Execute command directly (unchanged)"""
    result = execute_command(cmd.command, cmd.timeout)
    return result

@router.get("/status")
async def get_status():
    """Get developer status with AI capability indicator"""
    
    # Get actual container count
    docker_result = execute_command("docker ps -q | wc -l")
    try:
        running_count = int(docker_result["stdout"].strip()) if docker_result["success"] else 0
    except:
        running_count = 0
    
    return {
        "status": "operational",
        "mode": "GENIUS_DEVELOPER",
        "ai_enabled": HAS_AI,
        "metrics": {
            "containers_running": running_count,
            "total_containers": 7
        },
        "capabilities": [
            "System Analysis",
            "Improvement Suggestions",
            "Code Review",
            "Architecture Design",
            "Performance Optimization",
            "Security Auditing",
            "Autonomous Fixes",
            "Strategic Planning"
        ]
    }

@router.get("/metrics")
async def get_system_metrics():
    """Get system metrics for the developer dashboard"""
    import psutil
    import shutil
    
    try:
        # Get CPU usage
        cpu_percent = psutil.cpu_percent(interval=1)
        
        # Get memory usage
        memory = psutil.virtual_memory()
        memory_used_gb = memory.used / (1024**3)
        memory_total_gb = memory.total / (1024**3)
        memory_percent = memory.percent
        
        # Get disk usage
        disk = shutil.disk_usage('/')
        disk_used_gb = disk.used / (1024**3)
        disk_total_gb = disk.total / (1024**3)
        disk_percent = (disk.used / disk.total) * 100
        
        return {
            "cpu": round(cpu_percent, 1),
            "cpu_percent": round(cpu_percent, 1),
            "memory": {
                "percent": round(memory_percent, 1),
                "used": round(memory_used_gb, 1),
                "total": round(memory_total_gb, 1)
            },
            "memory_percent": round(memory_percent, 1),
            "disk": {
                "percent": round(disk_percent, 1),
                "used": round(disk_used_gb, 1),
                "total": round(disk_total_gb, 1)
            },
            "disk_percent": round(disk_percent, 1)
        }
    except Exception as e:
        return {
            "error": str(e),
            "cpu": 0,
            "cpu_percent": 0,
            "memory": {"percent": 0, "used": 0, "total": 0},
            "memory_percent": 0,
            "disk": {"percent": 0, "used": 0, "total": 0},
            "disk_percent": 0
        }

@router.post("/restart-all")
async def restart_all_containers():
    """Restart all Docker containers"""
    try:
        import subprocess
        import asyncio
        
        # Run the restart command asynchronously
        process = await asyncio.create_subprocess_exec(
            "docker", "compose", "restart",
            cwd="/home/pi/zoe",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        # Wait for completion with timeout
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=60)
            if process.returncode == 0:
                return {"status": "success", "message": "All containers restarted successfully"}
            else:
                return {"status": "error", "message": f"Failed to restart containers: {stderr.decode()}"}
        except asyncio.TimeoutError:
            process.kill()
            return {"status": "error", "message": "Restart command timed out"}
            
    except Exception as e:
        return {"status": "error", "message": f"Error restarting containers: {str(e)}"}

@router.post("/clear-cache")
async def clear_redis_cache():
    """Clear Redis cache"""
    try:
        import subprocess
        result = subprocess.run(
            ["docker", "exec", "zoe-redis", "redis-cli", "FLUSHALL"],
            capture_output=True,
            text=True,
            timeout=10
        )
        
        if result.returncode == 0:
            return {"status": "success", "message": "Redis cache cleared successfully"}
        else:
            return {"status": "error", "message": f"Failed to clear cache: {result.stderr}"}
    except Exception as e:
        return {"status": "error", "message": f"Error clearing cache: {str(e)}"}

@router.post("/review-code")
async def review_code(request: CodeReviewRequest):
    """Review code for safety and quality"""
    
    if not HAS_CODE_REVIEW:
        return {"error": "Code review system not available"}
    
    try:
        review_result = code_reviewer.review_code(request.code, request.file_path)
        
        return {
            "review": review_result,
            "timestamp": datetime.now().isoformat(),
            "reviewer_version": "1.0"
        }
    except Exception as e:
        return {"error": f"Code review failed: {str(e)}"}

@router.post("/backup/create")
async def create_backup(description: str = "Manual backup"):
    """Create a full system backup"""
    
    if not HAS_BACKUP:
        return {"error": "Backup system not available"}
    
    try:
        result = backup_system.create_full_backup(description)
        return result
    except Exception as e:
        return {"error": f"Backup creation failed: {str(e)}"}

@router.post("/backup/pre-task/{task_id}")
async def create_pre_task_backup(task_id: str, description: str = "Pre-task backup"):
    """Create backup before task execution"""
    
    if not HAS_BACKUP:
        return {"error": "Backup system not available"}
    
    try:
        result = backup_system.create_pre_task_snapshot(task_id, description)
        return result
    except Exception as e:
        return {"error": f"Pre-task backup failed: {str(e)}"}

@router.post("/backup/restore")
async def restore_backup(backup_path: str, restore_location: str = None):
    """Restore from backup"""
    
    if not HAS_BACKUP:
        return {"error": "Backup system not available"}
    
    try:
        result = backup_system.restore_backup(backup_path, restore_location)
        return result
    except Exception as e:
        return {"error": f"Restore failed: {str(e)}"}

@router.get("/backup/list")
async def list_backups():
    """List all available backups"""
    
    if not HAS_BACKUP:
        return {"error": "Backup system not available"}
    
    try:
        backups = backup_system.list_backups()
        return {
            "backups": backups,
            "count": len(backups)
        }
    except Exception as e:
        return {"error": f"Failed to list backups: {str(e)}"}

@router.post("/backup/cleanup")
async def cleanup_backups(keep_count: int = 10):
    """Clean up old backups"""
    
    if not HAS_BACKUP:
        return {"error": "Backup system not available"}
    
    try:
        result = backup_system.cleanup_old_backups(keep_count)
        return result
    except Exception as e:
        return {"error": f"Cleanup failed: {str(e)}"}
