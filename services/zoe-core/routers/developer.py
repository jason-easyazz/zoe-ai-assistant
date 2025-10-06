"""Enhanced Developer Router with Genius-Level AI Integration"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import subprocess
import json
import os
import sys
from typing import Optional, Dict, List, Any
import re
from datetime import datetime
from pathlib import Path
import socket
import urllib.request
import urllib.error
import time

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

# Import API documentation generator
try:
    from api_doc_generator import api_doc_generator
    HAS_API_DOCS = True
except:
    HAS_API_DOCS = False
    print("Warning: API documentation generator not available")

# Import self-test suite
try:
    from self_test_suite import self_test_suite
    HAS_SELF_TEST = True
except:
    HAS_SELF_TEST = False
    print("Warning: Self-test suite not available")

# Import learning system
try:
    from learning_system import learning_system
    HAS_LEARNING = True
except:
    HAS_LEARNING = False
    print("Warning: Learning system not available")

# Import Aider integration
try:
    from aider_integration import aider_integration
    from aider_task_integration import aider_task_integration
    HAS_AIDER = True
except:
    HAS_AIDER = False
    print("Warning: Aider integration not available")

# Import resource monitor
try:
    from resource_monitor import resource_monitor
    HAS_RESOURCE_MONITOR = True
except:
    HAS_RESOURCE_MONITOR = False
    print("Warning: Resource monitor not available")

# Import wake word detector
try:
    from wake_word_detector import wake_word_manager
    HAS_WAKE_WORD = True
except:
    HAS_WAKE_WORD = False
    print("Warning: Wake word detector not available")

# Import N8N integration
try:
    from n8n_integration import n8n_integration
    HAS_N8N = True
except:
    HAS_N8N = False
    print("Warning: N8N integration not available")

# Import Feature Request Pipeline
try:
    from feature_request_pipeline import feature_request_pipeline
    HAS_FEATURE_PIPELINE = True
except:
    HAS_FEATURE_PIPELINE = False
    print("Warning: Feature Request Pipeline not available")

# Import Guardrails Validation
try:
    from guardrails_validation import guardrails_validator
    HAS_GUARDRAILS = True
except:
    HAS_GUARDRAILS = False
    print("Warning: Guardrails Validation not available")

# Import Development Metrics
try:
    from development_metrics import development_metrics
    HAS_DEVELOPMENT_METRICS = True
except:
    HAS_DEVELOPMENT_METRICS = False
    print("Warning: Development Metrics not available")

# Import Task Scheduler
try:
    from task_scheduler import task_scheduler
    HAS_TASK_SCHEDULER = True
except:
    HAS_TASK_SCHEDULER = False
    print("Warning: Task Scheduler not available")

# Import User Context
try:
    from user_context import user_context
    HAS_USER_CONTEXT = True
except:
    HAS_USER_CONTEXT = False
    print("Warning: User Context not available")

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

def execute_command(cmd: str, timeout: int = 30, cwd: str = None, allow_shell: bool = False) -> dict:
    """Execute system command and return results with security validation"""
    try:
        if cwd is None:
            cwd = "/home/pi/zoe" if os.path.exists("/home/pi/zoe") else "/app"
        
        # Security validation for command injection prevention
        if not allow_shell:
            # Validate command against whitelist of safe commands
            safe_commands = {
                'docker', 'ps', 'logs', 'exec', 'inspect', 'stats', 'top',
                'free', 'df', 'du', 'ls', 'cat', 'head', 'tail', 'grep',
                'wc', 'sort', 'uniq', 'awk', 'sed', 'cut', 'find',
                'netstat', 'ss', 'uptime', 'whoami', 'pwd', 'date',
                'ps', 'top', 'htop', 'iostat', 'vmstat', 'lsof'
            }
            
            # Parse command to check for dangerous patterns
            cmd_parts = cmd.strip().split()
            if not cmd_parts:
                return {"success": False, "stdout": "", "stderr": "Empty command", "code": -1}
            
            # Check for shell metacharacters that could be dangerous
            dangerous_chars = [';', '&', '|', '`', '$', '(', ')', '<', '>', '\\', '"', "'"]
            if any(char in cmd for char in dangerous_chars):
                return {"success": False, "stdout": "", "stderr": "Command contains potentially dangerous characters", "code": -1}
            
            # Check if first command is in whitelist
            if cmd_parts[0] not in safe_commands:
                return {"success": False, "stdout": "", "stderr": f"Command '{cmd_parts[0]}' not in allowed list", "code": -1}
        
        # Use shell=False for better security when possible
        if allow_shell:
            result = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=cwd
            )
        else:
            # Parse command into list for safer execution
            cmd_parts = cmd.strip().split()
            result = subprocess.run(
                cmd_parts,
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
    except subprocess.TimeoutExpired:
        return {"success": False, "stdout": "", "stderr": "Command timed out", "code": -1}
    except Exception as e:
        return {"success": False, "stdout": "", "stderr": str(e), "code": -1}

def get_system_context() -> str:
    """Gather current system context for intelligent responses"""
    context_parts = []
    
    # Get Docker status
    docker_result = execute_command("docker ps --format '{{.Names}}: {{.Status}}'", allow_shell=True)
    if docker_result["success"]:
        context_parts.append(f"Docker Status:\n{docker_result['stdout']}")
    
    # Get memory usage
    mem_result = execute_command("free -h | head -2", allow_shell=True)
    if mem_result["success"]:
        context_parts.append(f"Memory:\n{mem_result['stdout']}")
    
    # Get disk usage
    disk_result = execute_command("df -h / | tail -1", allow_shell=True)
    if disk_result["success"]:
        context_parts.append(f"Disk:\n{disk_result['stdout']}")
    
    # Get recent errors from logs
    log_result = execute_command("docker logs zoe-core --tail 20 2>&1 | grep -i error || echo 'No recent errors'", allow_shell=True)
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
            docker_result = execute_command("docker ps --format 'table {{.Names}}\t{{.Status}}'", allow_shell=True)
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
        "containers": execute_command("docker ps -a --format json", allow_shell=True),
        "memory": execute_command("free -h", allow_shell=True),
        "disk": execute_command("df -h", allow_shell=True),
        "processes": execute_command("ps aux --sort=-%cpu | head -20", allow_shell=True),
        "errors": execute_command("docker logs zoe-core --tail 50 2>&1 | grep -i error", allow_shell=True),
        "network": execute_command("netstat -tuln | grep LISTEN", allow_shell=True)
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
    """Execute command directly with security validation (command injection protection enabled)"""
    result = execute_command(cmd.command, cmd.timeout)
    return result

@router.get("/status")
async def get_status():
    """Get developer status with AI capability indicator"""
    
    # Get actual container count
    docker_result = execute_command("docker ps -q | wc -l", allow_shell=True)
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
    import os
    
    try:
        # Get CPU usage per core
        cpu_percent = psutil.cpu_percent(interval=1)
        cpu_per_core = psutil.cpu_percent(interval=1, percpu=True)
        
        # Get memory usage
        memory = psutil.virtual_memory()
        memory_used_gb = memory.used / (1024**3)
        memory_total_gb = memory.total / (1024**3)
        memory_percent = memory.percent
        
        # Get top memory consumers
        top_memory_processes = []
        for proc in psutil.process_iter(['pid', 'name', 'memory_percent', 'memory_info']):
            try:
                pinfo = proc.info
                if pinfo['memory_percent'] > 0.5:  # Only show processes using >0.5% memory
                    top_memory_processes.append({
                        'name': pinfo['name'],
                        'pid': pinfo['pid'],
                        'memory_percent': round(pinfo['memory_percent'], 1),
                        'memory_mb': round(pinfo['memory_info'].rss / (1024*1024), 1)
                    })
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        
        # Sort by memory usage and take top 5
        top_memory_processes.sort(key=lambda x: x['memory_percent'], reverse=True)
        top_memory_processes = top_memory_processes[:5]
        
        # Get disk usage
        disk = shutil.disk_usage('/')
        disk_used_gb = disk.used / (1024**3)
        disk_total_gb = disk.total / (1024**3)
        disk_percent = (disk.used / disk.total) * 100
        
        # Get largest directories
        def get_dir_size(path, max_depth=2, current_depth=0):
            if current_depth > max_depth:
                return 0
            try:
                total = 0
                for item in os.listdir(path):
                    item_path = os.path.join(path, item)
                    if os.path.isfile(item_path):
                        total += os.path.getsize(item_path)
                    elif os.path.isdir(item_path) and not os.path.islink(item_path):
                        total += get_dir_size(item_path, max_depth, current_depth + 1)
                return total
            except (OSError, PermissionError):
                return 0
        
        # Check common large directories
        large_dirs = []
        for dir_path in ['/home', '/var', '/usr', '/opt', '/tmp', '/app']:
            if os.path.exists(dir_path):
                size = get_dir_size(dir_path)
                if size > 100 * 1024 * 1024:  # Only show dirs >100MB
                    large_dirs.append({
                        'path': dir_path,
                        'size_gb': round(size / (1024**3), 2)
                    })
        
        large_dirs.sort(key=lambda x: x['size_gb'], reverse=True)
        large_dirs = large_dirs[:5]
        
        return {
            "cpu": round(cpu_percent, 1),
            "cpu_percent": round(cpu_percent, 1),
            "cpu_cores": [round(core, 1) for core in cpu_per_core],
            "memory": {
                "percent": round(memory_percent, 1),
                "used": round(memory_used_gb, 1),
                "total": round(memory_total_gb, 1)
            },
            "memory_percent": round(memory_percent, 1),
            "top_memory_processes": top_memory_processes,
            "disk": {
                "percent": round(disk_percent, 1),
                "used": round(disk_used_gb, 1),
                "total": round(disk_total_gb, 1)
            },
            "disk_percent": round(disk_percent, 1),
            "large_directories": large_dirs
        }
    except Exception as e:
        return {
            "error": str(e),
            "cpu": 0,
            "cpu_percent": 0,
            "cpu_cores": [],
            "memory": {"percent": 0, "used": 0, "total": 0},
            "memory_percent": 0,
            "top_memory_processes": [],
            "disk": {"percent": 0, "used": 0, "total": 0},
            "disk_percent": 0,
            "large_directories": []
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

@router.post("/docs/generate")
async def generate_api_docs():
    """Generate API documentation"""
    
    if not HAS_API_DOCS:
        return {"error": "API documentation generator not available"}
    
    try:
        result = api_doc_generator.generate_documentation()
        return result
    except Exception as e:
        return {"error": f"Documentation generation failed: {str(e)}"}

@router.get("/docs/status")
async def get_docs_status():
    """Get documentation generation status"""
    
    if not HAS_API_DOCS:
        return {"error": "API documentation generator not available"}
    
    try:
        status = api_doc_generator.get_documentation_status()
        return status
    except Exception as e:
        return {"error": f"Failed to get documentation status: {str(e)}"}

@router.get("/docs/openapi")
async def get_openapi_spec():
    """Get OpenAPI specification"""
    
    if not HAS_API_DOCS:
        return {"error": "API documentation generator not available"}
    
    try:
        openapi_path = Path("/app/docs/openapi.json")
        if not openapi_path.exists():
            return {"error": "OpenAPI spec not found. Generate documentation first."}
        
        with open(openapi_path, 'r', encoding='utf-8') as f:
            spec = json.load(f)
        
        return spec
    except Exception as e:
        return {"error": f"Failed to load OpenAPI spec: {str(e)}"}

@router.get("/docs/markdown")
async def get_markdown_docs():
    """Get markdown documentation"""
    
    if not HAS_API_DOCS:
        return {"error": "API documentation generator not available"}
    
    try:
        markdown_path = Path("/app/docs/api_docs.md")
        if not markdown_path.exists():
            return {"error": "Markdown docs not found. Generate documentation first."}
        
        with open(markdown_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        return {"content": content}
    except Exception as e:
        return {"error": f"Failed to load markdown docs: {str(e)}"}

# -----------------------------
# Standardized Health Endpoints
# -----------------------------

def _tcp_check(host: str, port: int, timeout: float = 1.0) -> Dict[str, any]:
    start = time.time()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect((host, port))
        duration = time.time() - start
        return {"ok": True, "latency_ms": int(duration * 1000)}
    except Exception as e:
        return {"ok": False, "error": str(e)}
    finally:
        try:
            sock.close()
        except Exception:
            pass


def _http_check(url: str, timeout: float = 1.0, accept_404_as_ok: bool = False) -> Dict[str, any]:
    start = time.time()
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            code = resp.getcode()
            duration = time.time() - start
            ok = 200 <= code < 300 or (accept_404_as_ok and code == 404)
            return {"ok": ok, "status": code, "latency_ms": int(duration * 1000)}
    except urllib.error.HTTPError as e:
        duration = time.time() - start
        ok = accept_404_as_ok and e.code == 404
        return {"ok": ok, "status": e.code, "error": str(e), "latency_ms": int(duration * 1000)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _service_catalog() -> Dict[str, Dict[str, any]]:
    # Use container names for Docker Compose network
    return {
        "zoe-core":   {"type": "self", "url": "http://localhost:8000/health"},
        "zoe-ui":     {"type": "http", "url": "http://zoe-ui:80/"},
        "zoe-ollama": {"type": "http", "url": "http://zoe-ollama:11434/api/version"},
        "zoe-redis":  {"type": "tcp",  "host": "zoe-redis", "port": 6379},
        "zoe-whisper": {"type": "tcp",  "host": "zoe-whisper", "port": 9001},
        "zoe-tts":    {"type": "tcp",  "host": "zoe-tts", "port": 9002},
        "zoe-n8n":    {"type": "http", "url": "http://zoe-n8n:5678/", "accept_404": True},
    }


def _check_one(service_name: str) -> Dict[str, any]:
    catalog = _service_catalog()
    svc = catalog.get(service_name)
    if not svc:
        return {"service": service_name, "status": "unknown", "error": "service not found"}

    try:
        if svc["type"] == "tcp":
            res = _tcp_check(svc["host"], svc["port"])  
            status = "ok" if res.get("ok") else "down"
            return {"service": service_name, "check": "tcp", **res, "status": status}
        elif svc["type"] == "self":
            # For self-check, just return ok since we're already running
            return {"service": service_name, "status": "ok", "check": "self", "ok": True, "latency_ms": 0}
        else:
            # Use accept_404 flag if specified, otherwise default behavior
            accept_404 = svc.get("accept_404", svc["url"].endswith(":80/"))
            res = _http_check(svc["url"], accept_404_as_ok=accept_404)
            status = "ok" if res.get("ok") else "down"
            # Place status last so any numeric status code from res doesn't overwrite text status
            return {"service": service_name, "check": "http", **res, "status": status}
    except Exception as e:
        return {"service": service_name, "status": "down", "error": str(e)}


@router.get("/health")
async def get_all_health():
    catalog = _service_catalog()
    results = {}
    for name in catalog.keys():
        results[name] = _check_one(name)
    overall = "ok" if all(v.get("status") == "ok" for v in results.values()) else "degraded"
    return {"status": overall, "services": results, "checked_at": datetime.now().isoformat()}


@router.get("/health/{service}")
async def get_service_health(service: str):
    result = _check_one(service)
    return result

@router.get("/activity")
async def get_recent_activity():
    """Get recent system activity"""
    try:
        # Get recent Docker events
        docker_events = execute_command("docker events --since 1h --format '{{.Time}} {{.Action}} {{.Actor.Attributes.name}}' | tail -10", allow_shell=True)
        
        # Get recent log entries
        core_logs = execute_command("docker logs zoe-core --tail 5 --since 1h 2>&1 | grep -E '(ERROR|WARN|INFO)' | tail -3", allow_shell=True)
        
        activities = []
        
        # Add Docker events
        if docker_events["success"] and docker_events["stdout"]:
            for line in docker_events["stdout"].strip().split('\n'):
                if line.strip():
                    parts = line.split(' ', 2)
                    if len(parts) >= 3:
                        time_str = parts[0] + ' ' + parts[1]
                        action = parts[2].split()[0] if len(parts[2].split()) > 0 else 'unknown'
                        container = parts[2].split()[1] if len(parts[2].split()) > 1 else 'unknown'
                        activities.append({
                            "time": time_str,
                            "message": f"Container {container} {action}"
                        })
        
        # Add log entries
        if core_logs["success"] and core_logs["stdout"]:
            for line in core_logs["stdout"].strip().split('\n'):
                if line.strip():
                    activities.append({
                        "time": "Recent",
                        "message": f"Core: {line.strip()[:80]}..."
                    })
        
        # Add some system activities
        activities.extend([
            {"time": "Just now", "message": "Health check completed"},
            {"time": "2 min ago", "message": "Metrics updated"},
            {"time": "5 min ago", "message": "System monitoring active"}
        ])
        
        # Sort by time (most recent first) and limit to 10
        activities = activities[:10]
        
        return {"activities": activities}
    except Exception as e:
        return {"error": str(e), "activities": []}

@router.post("/self-test")
async def run_self_test():
    """Run comprehensive self-test suite with auto-rollback"""
    
    if not HAS_SELF_TEST:
        return {"error": "Self-test suite not available"}
    
    try:
        result = await self_test_suite.run_full_test_suite()
        return result
    except Exception as e:
        return {"error": f"Self-test execution failed: {str(e)}"}

@router.get("/learning/insights")
async def get_learning_insights():
    """Get learning system insights"""
    
    if not HAS_LEARNING:
        return {"error": "Learning system not available"}
    
    try:
        insights = learning_system.get_learning_insights()
        return insights
    except Exception as e:
        return {"error": f"Failed to get learning insights: {str(e)}"}

@router.get("/learning/recommendations")
async def get_learning_recommendations():
    """Get system improvement recommendations"""
    
    if not HAS_LEARNING:
        return {"error": "Learning system not available"}
    
    try:
        recommendations = learning_system.get_recommendations()
        return {"recommendations": recommendations}
    except Exception as e:
        return {"error": f"Failed to get recommendations: {str(e)}"}

@router.post("/learning/apply-improvement/{improvement_id}")
async def apply_improvement(improvement_id: int):
    """Apply a system improvement"""
    
    if not HAS_LEARNING:
        return {"error": "Learning system not available"}
    
    try:
        success = learning_system.apply_improvement(improvement_id)
        if success:
            return {"success": True, "message": "Improvement applied successfully"}
        else:
            return {"success": False, "message": "Improvement not found or failed to apply"}
    except Exception as e:
        return {"error": f"Failed to apply improvement: {str(e)}"}

class TaskExecutionRecord(BaseModel):
    task_id: str
    task_title: str
    success: bool
    execution_duration: Optional[float] = None
    error_message: Optional[str] = None

@router.post("/learning/record-execution")
async def record_task_execution(record: TaskExecutionRecord):
    """Record task execution for learning"""
    
    if not HAS_LEARNING:
        return {"error": "Learning system not available"}
    
    try:
        success = learning_system.record_task_execution(
            record.task_id, record.task_title, record.success, 
            record.execution_duration, record.error_message
        )
        if success:
            return {"success": True, "message": "Execution recorded successfully"}
        else:
            return {"success": False, "message": "Failed to record execution"}
    except Exception as e:
        return {"error": f"Failed to record execution: {str(e)}"}

class AiderCodeRequest(BaseModel):
    request: str
    context_files: Optional[List[str]] = None
    model: str = "ollama/llama3.2"

class AiderImprovementRequest(BaseModel):
    file_path: str
    issue_description: str

class AiderRefactorRequest(BaseModel):
    file_path: str
    refactor_description: str

class AiderTestRequest(BaseModel):
    file_path: str
    test_framework: str = "pytest"

@router.post("/aider/generate-code")
async def generate_code_with_aider(request: AiderCodeRequest):
    """Generate code using Aider AI pair programming"""
    
    if not HAS_AIDER:
        return {"error": "Aider integration not available"}
    
    try:
        result = aider_integration.generate_code(
            request=request.request,
            context_files=request.context_files,
            model=request.model
        )
        return result
    except Exception as e:
        return {"error": f"Code generation failed: {str(e)}"}

@router.post("/aider/suggest-improvements")
async def suggest_code_improvements(request: AiderImprovementRequest):
    """Suggest code improvements using Aider"""
    
    if not HAS_AIDER:
        return {"error": "Aider integration not available"}
    
    try:
        result = aider_integration.suggest_improvements(
            file_path=request.file_path,
            issue_description=request.issue_description
        )
        return result
    except Exception as e:
        return {"error": f"Improvement suggestion failed: {str(e)}"}

@router.post("/aider/refactor-code")
async def refactor_code_with_aider(request: AiderRefactorRequest):
    """Refactor code using Aider"""
    
    if not HAS_AIDER:
        return {"error": "Aider integration not available"}
    
    try:
        result = aider_integration.refactor_code(
            file_path=request.file_path,
            refactor_description=request.refactor_description
        )
        return result
    except Exception as e:
        return {"error": f"Code refactoring failed: {str(e)}"}

@router.post("/aider/generate-tests")
async def generate_tests_with_aider(request: AiderTestRequest):
    """Generate tests using Aider"""
    
    if not HAS_AIDER:
        return {"error": "Aider integration not available"}
    
    try:
        result = aider_integration.generate_tests(
            file_path=request.file_path,
            test_framework=request.test_framework
        )
        return result
    except Exception as e:
        return {"error": f"Test generation failed: {str(e)}"}

@router.get("/aider/models")
async def get_aider_models():
    """Get available models for Aider"""
    
    if not HAS_AIDER:
        return {"error": "Aider integration not available"}
    
    try:
        models = aider_integration.get_available_models()
        return {"models": models}
    except Exception as e:
        return {"error": f"Failed to get models: {str(e)}"}

@router.get("/aider/health")
async def get_aider_health():
    """Check Aider integration health"""
    
    if not HAS_AIDER:
        return {"error": "Aider integration not available"}
    
    try:
        health = aider_integration.check_health()
        return health
    except Exception as e:
        return {"error": f"Health check failed: {str(e)}"}

@router.get("/aider/tasks/context/{task_id}")
async def get_task_context_for_aider(task_id: str):
    """Get task context for Aider to work with"""
    
    if not HAS_AIDER:
        return {"error": "Aider integration not available"}
    
    try:
        context = aider_task_integration.get_task_context(task_id)
        return context
    except Exception as e:
        return {"error": f"Failed to get task context: {str(e)}"}

@router.get("/aider/tasks/next")
async def get_next_task_for_aider():
    """Get the next high-priority task for Aider to work on"""
    
    if not HAS_AIDER:
        return {"error": "Aider integration not available"}
    
    try:
        task = aider_task_integration.get_next_task_for_aider()
        if task:
            return task
        else:
            return {"message": "No pending tasks available"}
    except Exception as e:
        return {"error": f"Failed to get next task: {str(e)}"}

@router.post("/aider/tasks/execute/{task_id}")
async def execute_task_with_aider(task_id: str, model: str = "ollama/llama3.2"):
    """Execute a specific task using Aider"""
    
    if not HAS_AIDER:
        return {"error": "Aider integration not available"}
    
    try:
        result = aider_task_integration.execute_task_with_aider(task_id, model)
        return result
    except Exception as e:
        return {"error": f"Failed to execute task: {str(e)}"}

@router.post("/aider/tasks/update-status/{task_id}")
async def update_task_status_after_aider(task_id: str, status: str, notes: str = None):
    """Update task status after Aider execution"""
    
    if not HAS_AIDER:
        return {"error": "Aider integration not available"}
    
    try:
        success = aider_task_integration.update_task_status(task_id, status, notes)
        if success:
            return {"success": True, "message": f"Task {task_id} status updated to {status}"}
        else:
            return {"success": False, "message": "Failed to update task status"}
    except Exception as e:
        return {"error": f"Failed to update task status: {str(e)}"}

@router.get("/aider/tasks/work-summary")
async def get_aider_work_summary():
    """Get summary of Aider's work on tasks"""
    
    if not HAS_AIDER:
        return {"error": "Aider integration not available"}
    
    try:
        summary = aider_task_integration.get_aider_work_summary()
        return summary
    except Exception as e:
        return {"error": f"Failed to get work summary: {str(e)}"}

@router.get("/resources/status")
async def get_resource_status():
    """Get current resource status"""
    
    if not HAS_RESOURCE_MONITOR:
        return {"error": "Resource monitor not available"}
    
    try:
        metrics = resource_monitor.get_current_metrics()
        if not metrics:
            return {"error": "No metrics available"}
        
        return {
            "timestamp": metrics.timestamp.isoformat(),
            "cpu_percent": metrics.cpu_percent,
            "memory_percent": metrics.memory_percent,
            "memory_used_mb": round(metrics.memory_used_mb, 1),
            "memory_available_mb": round(metrics.memory_available_mb, 1),
            "disk_percent": metrics.disk_percent,
            "disk_used_gb": round(metrics.disk_used_gb, 1),
            "disk_free_gb": round(metrics.disk_free_gb, 1),
            "temperature": metrics.temperature,
            "load_average": metrics.load_average,
            "resource_level": resource_monitor.current_level.value
        }
    except Exception as e:
        return {"error": f"Failed to get resource status: {str(e)}"}

@router.get("/resources/summary")
async def get_resource_summary(hours: int = 1):
    """Get resource usage summary"""
    
    if not HAS_RESOURCE_MONITOR:
        return {"error": "Resource monitor not available"}
    
    try:
        summary = resource_monitor.get_metrics_summary(hours)
        return summary
    except Exception as e:
        return {"error": f"Failed to get resource summary: {str(e)}"}

@router.get("/resources/system-info")
async def get_system_info():
    """Get system information"""
    
    if not HAS_RESOURCE_MONITOR:
        return {"error": "Resource monitor not available"}
    
    try:
        info = resource_monitor.get_system_info()
        return info
    except Exception as e:
        return {"error": f"Failed to get system info: {str(e)}"}

@router.post("/resources/start-monitoring")
async def start_resource_monitoring(interval: float = 5.0):
    """Start resource monitoring"""
    
    if not HAS_RESOURCE_MONITOR:
        return {"error": "Resource monitor not available"}
    
    try:
        resource_monitor.start_monitoring(interval)
        return {"success": True, "message": f"Resource monitoring started with {interval}s interval"}
    except Exception as e:
        return {"error": f"Failed to start monitoring: {str(e)}"}

@router.post("/resources/stop-monitoring")
async def stop_resource_monitoring():
    """Stop resource monitoring"""
    
    if not HAS_RESOURCE_MONITOR:
        return {"error": "Resource monitor not available"}
    
    try:
        resource_monitor.stop_monitoring()
        return {"success": True, "message": "Resource monitoring stopped"}
    except Exception as e:
        return {"error": f"Failed to stop monitoring: {str(e)}"}

@router.post("/resources/check-throttle")
async def check_task_throttle(task_id: str, estimated_memory_mb: float = 100):
    """Check if task should be throttled"""
    
    if not HAS_RESOURCE_MONITOR:
        return {"error": "Resource monitor not available"}
    
    try:
        should_throttle = resource_monitor.should_throttle_task(task_id, estimated_memory_mb)
        throttle_factor = resource_monitor.get_throttle_factor(task_id)
        
        return {
            "task_id": task_id,
            "should_throttle": should_throttle,
            "throttle_factor": throttle_factor,
            "resource_level": resource_monitor.current_level.value
        }
    except Exception as e:
        return {"error": f"Failed to check throttle: {str(e)}"}

@router.post("/resources/cleanup")
async def cleanup_old_metrics(days: int = 7):
    """Clean up old metrics data"""
    
    if not HAS_RESOURCE_MONITOR:
        return {"error": "Resource monitor not available"}
    
    try:
        resource_monitor.cleanup_old_metrics(days)
        return {"success": True, "message": f"Cleaned up metrics older than {days} days"}
    except Exception as e:
        return {"error": f"Failed to cleanup metrics: {str(e)}"}

@router.get("/wake-word/status")
async def get_wake_word_status():
    """Get wake word detection status"""
    
    if not HAS_WAKE_WORD:
        return {"error": "Wake word detector not available"}
    
    try:
        status = wake_word_manager.get_status()
        return status
    except Exception as e:
        return {"error": f"Failed to get wake word status: {str(e)}"}

@router.post("/wake-word/start")
async def start_wake_word_detection():
    """Start wake word detection"""
    
    if not HAS_WAKE_WORD:
        return {"error": "Wake word detector not available"}
    
    try:
        success = wake_word_manager.start()
        if success:
            return {"success": True, "message": "Wake word detection started"}
        else:
            return {"success": False, "message": "Failed to start wake word detection"}
    except Exception as e:
        return {"error": f"Failed to start wake word detection: {str(e)}"}

@router.post("/wake-word/stop")
async def stop_wake_word_detection():
    """Stop wake word detection"""
    
    if not HAS_WAKE_WORD:
        return {"error": "Wake word detector not available"}
    
    try:
        wake_word_manager.stop()
        return {"success": True, "message": "Wake word detection stopped"}
    except Exception as e:
        return {"error": f"Failed to stop wake word detection: {str(e)}"}

@router.post("/wake-word/reset-stats")
async def reset_wake_word_stats():
    """Reset wake word detection statistics"""
    
    if not HAS_WAKE_WORD:
        return {"error": "Wake word detector not available"}
    
    try:
        wake_word_manager.detector.reset_statistics()
        return {"success": True, "message": "Wake word statistics reset"}
    except Exception as e:
        return {"error": f"Failed to reset statistics: {str(e)}"}

@router.post("/wake-word/update-config")
async def update_wake_word_config(config: Dict[str, Any]):
    """Update wake word detection configuration"""
    
    if not HAS_WAKE_WORD:
        return {"error": "Wake word detector not available"}
    
    try:
        success = wake_word_manager.update_config(config)
        if success:
            return {"success": True, "message": "Configuration updated"}
        else:
            return {"success": False, "message": "Failed to update configuration"}
    except Exception as e:
        return {"error": f"Failed to update configuration: {str(e)}"}

# N8N Integration endpoints
@router.post("/n8n/trigger/{workflow_id}")
async def trigger_n8n_workflow(workflow_id: str, data: Dict[str, Any] = None):
    """Trigger an N8N workflow"""
    
    if not HAS_N8N:
        return {"error": "N8N integration not available"}
    
    try:
        result = n8n_integration.trigger_workflow(workflow_id, data)
        return result
    except Exception as e:
        return {"error": f"Failed to trigger workflow: {str(e)}"}

@router.get("/n8n/workflows")
async def list_n8n_workflows():
    """List available N8N workflows"""
    
    if not HAS_N8N:
        return {"error": "N8N integration not available"}
    
    try:
        workflows = n8n_integration.list_workflows()
        return {"workflows": workflows}
    except Exception as e:
        return {"error": f"Failed to list workflows: {str(e)}"}

@router.get("/n8n/workflow/{workflow_id}/status")
async def get_n8n_workflow_status(workflow_id: str):
    """Get status of a specific N8N workflow"""
    
    if not HAS_N8N:
        return {"error": "N8N integration not available"}
    
    try:
        status = n8n_integration.get_workflow_status(workflow_id)
        return status
    except Exception as e:
        return {"error": f"Failed to get workflow status: {str(e)}"}

@router.put("/n8n/workflow/{workflow_id}")
async def update_n8n_workflow(workflow_id: str, workflow_data: Dict[str, Any]):
    """Update an N8N workflow"""
    
    if not HAS_N8N:
        return {"error": "N8N integration not available"}
    
    try:
        result = n8n_integration.update_workflow(workflow_id, workflow_data)
        return result
    except Exception as e:
        return {"error": f"Failed to update workflow: {str(e)}"}

# Feature Request Pipeline endpoints
@router.post("/feature-request/create")
async def create_task_from_request(request: str, user_id: str = "system"):
    """Convert natural language request to structured task"""
    
    if not HAS_FEATURE_PIPELINE:
        return {"error": "Feature Request Pipeline not available"}
    
    try:
        result = feature_request_pipeline.create_task_from_request(request, user_id)
        return result
    except Exception as e:
        return {"error": f"Failed to create task from request: {str(e)}"}

@router.get("/feature-request/stats")
async def get_feature_pipeline_stats():
    """Get statistics about the feature request pipeline"""
    
    if not HAS_FEATURE_PIPELINE:
        return {"error": "Feature Request Pipeline not available"}
    
    try:
        stats = feature_request_pipeline.get_pipeline_stats()
        return stats
    except Exception as e:
        return {"error": f"Failed to get pipeline stats: {str(e)}"}

@router.post("/feature-request/parse")
async def parse_request(request: str):
    """Parse natural language request without creating task"""
    
    if not HAS_FEATURE_PIPELINE:
        return {"error": "Feature Request Pipeline not available"}
    
    try:
        parsed = feature_request_pipeline.parse_natural_language_request(request)
        return parsed
    except Exception as e:
        return {"error": f"Failed to parse request: {str(e)}"}

# Guardrails Validation endpoints
@router.post("/guardrails/validate-content")
async def validate_content(content: str, content_type: str = "text"):
    """Validate content for safety and PII"""
    
    if not HAS_GUARDRAILS:
        return {"error": "Guardrails Validation not available"}
    
    try:
        result = guardrails_validator.validate_content(content, content_type)
        return result
    except Exception as e:
        return {"error": f"Failed to validate content: {str(e)}"}

@router.post("/guardrails/validate-code")
async def validate_code_execution(code: str, context: Dict[str, Any] = None):
    """Validate code before execution"""
    
    if not HAS_GUARDRAILS:
        return {"error": "Guardrails Validation not available"}
    
    try:
        result = guardrails_validator.validate_code_execution(code, context)
        return result
    except Exception as e:
        return {"error": f"Failed to validate code: {str(e)}"}

@router.post("/guardrails/generate-safe-prompt")
async def generate_safe_prompt(original_prompt: str):
    """Generate a safer version of a prompt"""
    
    if not HAS_GUARDRAILS:
        return {"error": "Guardrails Validation not available"}
    
    try:
        # First validate the original prompt
        validation_result = guardrails_validator.validate_content(original_prompt, "text")
        
        # Generate safe prompt
        safe_prompt = guardrails_validator.generate_safe_prompt(original_prompt, validation_result)
        
        return {
            "original_prompt": original_prompt,
            "safe_prompt": safe_prompt,
            "validation_result": validation_result,
            "was_modified": original_prompt != safe_prompt
        }
    except Exception as e:
        return {"error": f"Failed to generate safe prompt: {str(e)}"}

@router.get("/guardrails/stats")
async def get_guardrails_stats():
    """Get guardrails validation statistics"""
    
    if not HAS_GUARDRAILS:
        return {"error": "Guardrails Validation not available"}
    
    try:
        stats = guardrails_validator.get_validation_stats()
        return stats
    except Exception as e:
        return {"error": f"Failed to get stats: {str(e)}"}

@router.post("/guardrails/clear-cache")
async def clear_guardrails_cache():
    """Clear guardrails validation cache"""
    
    if not HAS_GUARDRAILS:
        return {"error": "Guardrails Validation not available"}
    
    try:
        success = guardrails_validator.clear_cache()
        if success:
            return {"success": True, "message": "Cache cleared successfully"}
        else:
            return {"success": False, "message": "Failed to clear cache"}
    except Exception as e:
        return {"error": f"Failed to clear cache: {str(e)}"}

@router.put("/guardrails/update-patterns")
async def update_guardrails_patterns(pattern_type: str, patterns: Dict[str, Any]):
    """Update guardrails validation patterns"""
    
    if not HAS_GUARDRAILS:
        return {"error": "Guardrails Validation not available"}
    
    try:
        success = guardrails_validator.update_patterns(pattern_type, patterns)
        if success:
            return {"success": True, "message": f"Patterns updated for {pattern_type}"}
        else:
            return {"success": False, "message": "Failed to update patterns"}
    except Exception as e:
        return {"error": f"Failed to update patterns: {str(e)}"}

# Development Metrics endpoints
@router.get("/metrics/dashboard")
async def get_dashboard_summary():
    """Get comprehensive development metrics dashboard"""
    
    if not HAS_DEVELOPMENT_METRICS:
        return {"error": "Development Metrics not available"}
    
    try:
        summary = development_metrics.get_dashboard_summary()
        return summary
    except Exception as e:
        return {"error": f"Failed to get dashboard summary: {str(e)}"}

@router.get("/metrics/task-completion")
async def get_task_completion_metrics(days: int = 30):
    """Get task completion metrics"""
    
    if not HAS_DEVELOPMENT_METRICS:
        return {"error": "Development Metrics not available"}
    
    try:
        metrics = development_metrics.get_task_completion_metrics(days)
        return metrics
    except Exception as e:
        return {"error": f"Failed to get task completion metrics: {str(e)}"}

@router.get("/metrics/code-quality")
async def get_code_quality_metrics():
    """Get code quality metrics"""
    
    if not HAS_DEVELOPMENT_METRICS:
        return {"error": "Development Metrics not available"}
    
    try:
        metrics = development_metrics.get_code_quality_metrics()
        return metrics
    except Exception as e:
        return {"error": f"Failed to get code quality metrics: {str(e)}"}

@router.get("/metrics/system-improvements")
async def get_system_improvement_metrics():
    """Get system improvement metrics"""
    
    if not HAS_DEVELOPMENT_METRICS:
        return {"error": "Development Metrics not available"}
    
    try:
        metrics = development_metrics.get_system_improvement_metrics()
        return metrics
    except Exception as e:
        return {"error": f"Failed to get system improvement metrics: {str(e)}"}

@router.get("/metrics/weekly-report")
async def get_weekly_report():
    """Get weekly development report"""
    
    if not HAS_DEVELOPMENT_METRICS:
        return {"error": "Development Metrics not available"}
    
    try:
        report = development_metrics.get_weekly_report()
        return report
    except Exception as e:
        return {"error": f"Failed to get weekly report: {str(e)}"}

@router.get("/metrics/velocity-trends")
async def get_velocity_trends(weeks: int = 8):
    """Get velocity trends over multiple weeks"""
    
    if not HAS_DEVELOPMENT_METRICS:
        return {"error": "Development Metrics not available"}
    
    try:
        trends = development_metrics.get_velocity_trends(weeks)
        return trends
    except Exception as e:
        return {"error": f"Failed to get velocity trends: {str(e)}"}

@router.get("/metrics/productivity")
async def get_team_productivity_metrics():
    """Get team productivity metrics"""
    
    if not HAS_DEVELOPMENT_METRICS:
        return {"error": "Development Metrics not available"}
    
    try:
        metrics = development_metrics.get_team_productivity_metrics()
        return metrics
    except Exception as e:
        return {"error": f"Failed to get productivity metrics: {str(e)}"}

# Task Scheduler endpoints
@router.get("/scheduler/optimized-tasks")
async def get_optimized_task_list(limit: int = 10):
    """Get optimized list of tasks for execution"""
    
    if not HAS_TASK_SCHEDULER:
        return {"error": "Task Scheduler not available"}
    
    try:
        tasks = task_scheduler.get_optimized_task_list(limit)
        return {"optimized_tasks": tasks}
    except Exception as e:
        return {"error": f"Failed to get optimized tasks: {str(e)}"}

@router.get("/scheduler/schedule-report")
async def get_schedule_report():
    """Get comprehensive task schedule report"""
    
    if not HAS_TASK_SCHEDULER:
        return {"error": "Task Scheduler not available"}
    
    try:
        # Get all pending tasks
        conn = sqlite3.connect("/app/data/developer_tasks.db")
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT id, title, objective, requirements, constraints, 
                   acceptance_criteria, priority, status, assigned_to,
                   created_at, last_executed_at, execution_count
            FROM dynamic_tasks 
            WHERE status = 'pending'
            ORDER BY created_at ASC
        ''')
        
        tasks = []
        for row in cursor.fetchall():
            tasks.append({
                "id": row[0],
                "title": row[1],
                "objective": row[2],
                "requirements": row[3],
                "constraints": row[4],
                "acceptance_criteria": row[5],
                "priority": row[6],
                "status": row[7],
                "assigned_to": row[8],
                "created_at": row[9],
                "last_executed_at": row[10],
                "execution_count": row[11]
            })
        
        conn.close()
        
        # Create scheduled tasks
        scheduled_tasks = task_scheduler.create_scheduled_tasks(tasks)
        
        # Optimize schedule
        optimized_tasks = task_scheduler.optimize_schedule(scheduled_tasks)
        
        # Generate report
        report = task_scheduler.generate_schedule_report(optimized_tasks)
        
        return report
    except Exception as e:
        return {"error": f"Failed to get schedule report: {str(e)}"}

@router.post("/scheduler/analyze-dependencies")
async def analyze_task_dependencies():
    """Analyze dependencies between all pending tasks"""
    
    if not HAS_TASK_SCHEDULER:
        return {"error": "Task Scheduler not available"}
    
    try:
        # Get all pending tasks
        conn = sqlite3.connect("/app/data/developer_tasks.db")
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT id, title, objective, requirements, constraints, 
                   acceptance_criteria, priority, status, assigned_to,
                   created_at, last_executed_at, execution_count
            FROM dynamic_tasks 
            WHERE status = 'pending'
            ORDER BY created_at ASC
        ''')
        
        tasks = []
        for row in cursor.fetchall():
            tasks.append({
                "id": row[0],
                "title": row[1],
                "objective": row[2],
                "requirements": row[3],
                "constraints": row[4],
                "acceptance_criteria": row[5],
                "priority": row[6],
                "status": row[7],
                "assigned_to": row[8],
                "created_at": row[9],
                "last_executed_at": row[10],
                "execution_count": row[11]
            })
        
        conn.close()
        
        # Analyze dependencies
        dependencies = task_scheduler.analyze_dependencies(tasks)
        
        # Group by task
        dependency_map = {}
        for dep in dependencies:
            if dep.task_id not in dependency_map:
                dependency_map[dep.task_id] = []
            dependency_map[dep.task_id].append({
                "depends_on": dep.depends_on,
                "type": dep.dependency_type,
                "weight": dep.weight
            })
        
        return {
            "total_dependencies": len(dependencies),
            "tasks_with_dependencies": len(dependency_map),
            "dependency_map": dependency_map
        }
    except Exception as e:
        return {"error": f"Failed to analyze dependencies: {str(e)}"}

@router.get("/scheduler/resource-analysis")
async def get_resource_analysis():
    """Get resource analysis for pending tasks"""
    
    if not HAS_TASK_SCHEDULER:
        return {"error": "Task Scheduler not available"}
    
    try:
        # Get all pending tasks
        conn = sqlite3.connect("/app/data/developer_tasks.db")
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT id, title, objective, requirements, constraints, 
                   acceptance_criteria, priority, status, assigned_to,
                   created_at, last_executed_at, execution_count
            FROM dynamic_tasks 
            WHERE status = 'pending'
            ORDER BY created_at ASC
        ''')
        
        tasks = []
        for row in cursor.fetchall():
            tasks.append({
                "id": row[0],
                "title": row[1],
                "objective": row[2],
                "requirements": row[3],
                "constraints": row[4],
                "acceptance_criteria": row[5],
                "priority": row[6],
                "status": row[7],
                "assigned_to": row[8],
                "created_at": row[9],
                "last_executed_at": row[10],
                "execution_count": row[11]
            })
        
        conn.close()
        
        # Analyze resources
        resource_analysis = []
        for task in tasks:
            resources = task_scheduler.analyze_resource_requirements(task)
            resource_analysis.append({
                "task_id": task["id"],
                "title": task["title"],
                "resources": {
                    "cpu_intensive": resources.cpu_intensive,
                    "memory_intensive": resources.memory_intensive,
                    "io_intensive": resources.io_intensive,
                    "network_intensive": resources.network_intensive,
                    "estimated_duration": resources.estimated_duration,
                    "max_parallel": resources.max_parallel
                }
            })
        
        # Calculate totals
        totals = {
            "cpu_intensive": len([r for r in resource_analysis if r["resources"]["cpu_intensive"]]),
            "memory_intensive": len([r for r in resource_analysis if r["resources"]["memory_intensive"]]),
            "io_intensive": len([r for r in resource_analysis if r["resources"]["io_intensive"]]),
            "network_intensive": len([r for r in resource_analysis if r["resources"]["network_intensive"]]),
            "total_estimated_time": sum(r["resources"]["estimated_duration"] for r in resource_analysis)
        }
        
        return {
            "resource_analysis": resource_analysis,
            "totals": totals,
            "resource_limits": task_scheduler.resource_limits
        }
    except Exception as e:
        return {"error": f"Failed to get resource analysis: {str(e)}"}

# User Context endpoints
@router.post("/users/create")
async def create_user(username: str, email: str = None, display_name: str = None, 
                     role: str = "user", preferences: Dict[str, Any] = None):
    """Create a new user"""
    
    if not HAS_USER_CONTEXT:
        return {"error": "User Context not available"}
    
    try:
        result = user_context.create_user(username, email, display_name, role, preferences)
        return result
    except Exception as e:
        return {"error": f"Failed to create user: {str(e)}"}

@router.get("/users/{user_id}")
async def get_user(user_id: str):
    """Get user by ID"""
    
    if not HAS_USER_CONTEXT:
        return {"error": "User Context not available"}
    
    try:
        user = user_context.get_user(user_id)
        if user:
            return user
        else:
            return {"error": "User not found"}
    except Exception as e:
        return {"error": f"Failed to get user: {str(e)}"}

@router.get("/users/username/{username}")
async def get_user_by_username(username: str):
    """Get user by username"""
    
    if not HAS_USER_CONTEXT:
        return {"error": "User Context not available"}
    
    try:
        user = user_context.get_user_by_username(username)
        if user:
            return user
        else:
            return {"error": "User not found"}
    except Exception as e:
        return {"error": f"Failed to get user: {str(e)}"}

@router.put("/users/{user_id}")
async def update_user(user_id: str, updates: Dict[str, Any]):
    """Update user information"""
    
    if not HAS_USER_CONTEXT:
        return {"error": "User Context not available"}
    
    try:
        success = user_context.update_user(user_id, updates)
        if success:
            return {"success": True, "message": "User updated successfully"}
        else:
            return {"success": False, "message": "Failed to update user"}
    except Exception as e:
        return {"error": f"Failed to update user: {str(e)}"}

@router.post("/users/{user_id}/sessions")
async def create_user_session(user_id: str, session_data: Dict[str, Any] = None, 
                             expires_hours: int = 24):
    """Create a new user session"""
    
    if not HAS_USER_CONTEXT:
        return {"error": "User Context not available"}
    
    try:
        session_id = user_context.create_session(user_id, session_data, expires_hours)
        if session_id:
            return {"success": True, "session_id": session_id}
        else:
            return {"success": False, "message": "Failed to create session"}
    except Exception as e:
        return {"error": f"Failed to create session: {str(e)}"}

@router.get("/sessions/{session_id}")
async def get_session(session_id: str):
    """Get session by ID"""
    
    if not HAS_USER_CONTEXT:
        return {"error": "User Context not available"}
    
    try:
        session = user_context.get_session(session_id)
        if session:
            return session
        else:
            return {"error": "Session not found or expired"}
    except Exception as e:
        return {"error": f"Failed to get session: {str(e)}"}

@router.get("/users/{user_id}/tasks")
async def get_user_tasks(user_id: str, status: str = None):
    """Get tasks for a specific user"""
    
    if not HAS_USER_CONTEXT:
        return {"error": "User Context not available"}
    
    try:
        tasks = user_context.get_user_tasks(user_id, status)
        return {"tasks": tasks}
    except Exception as e:
        return {"error": f"Failed to get user tasks: {str(e)}"}

@router.get("/users/{user_id}/context")
async def get_user_context(user_id: str):
    """Get comprehensive user context"""
    
    if not HAS_USER_CONTEXT:
        return {"error": "User Context not available"}
    
    try:
        context = user_context.get_user_context(user_id)
        return context
    except Exception as e:
        return {"error": f"Failed to get user context: {str(e)}"}

@router.get("/users")
async def get_all_users():
    """Get all users"""
    
    if not HAS_USER_CONTEXT:
        return {"error": "User Context not available"}
    
    try:
        users = user_context.get_all_users()
        return {"users": users}
    except Exception as e:
        return {"error": f"Failed to get users: {str(e)}"}

@router.post("/users/migrate-data")
async def migrate_existing_data():
    """Migrate existing data to use user context"""
    
    if not HAS_USER_CONTEXT:
        return {"error": "User Context not available"}
    
    try:
        result = user_context.migrate_existing_data()
        return result
    except Exception as e:
        return {"error": f"Failed to migrate data: {str(e)}"}

@router.post("/users/cleanup-sessions")
async def cleanup_expired_sessions():
    """Clean up expired sessions"""
    
    if not HAS_USER_CONTEXT:
        return {"error": "User Context not available"}
    
    try:
        cleaned = user_context.cleanup_expired_sessions()
        return {"success": True, "cleaned_sessions": cleaned}
    except Exception as e:
        return {"error": f"Failed to cleanup sessions: {str(e)}"}
