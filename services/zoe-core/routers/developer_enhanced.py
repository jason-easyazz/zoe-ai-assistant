"""Enhanced Developer Router with Genius-Level AI Integration"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import subprocess
import json
import os
import sys
from typing import Optional, Dict, List
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

class ChatMessage(BaseModel):
    message: str

class CommandRequest(BaseModel):
    command: str
    safe_mode: bool = True
    timeout: int = 30

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

YOUR PERSONALITY:
- Brilliant and analytical
- Direct and efficient
- Proactive problem-solver
- Detail-oriented
- Security-conscious
- Performance-focused
- Always thinking about scalability
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
    
    return "\n\n".join(context_parts)

@router.post("/chat")
async def developer_chat(msg: ChatMessage):
    """Enhanced developer chat with genius-level AI"""
    
    message_lower = msg.message.lower()
    
    # Quick command execution for specific requests
    if message_lower.startswith('/execute '):
        command = msg.message[9:]
        result = execute_command(command)
        response = f"**Command:** `{command}`\n\n"
        if result["success"]:
            response += f"**Output:**\n```\n{result['stdout']}\n```"
        else:
            response += f"**Error:**\n```\n{result['stderr']}\n```"
        return {"response": response, "executed": True}
    
    # For everything else, use AI with full context
    if HAS_AI:
        # Gather system context
        system_context = get_system_context()
        
        # Check if user is asking for analysis/improvements/suggestions
        needs_analysis = any(word in message_lower for word in [
            'improve', 'suggest', 'analyze', 'review', 'optimize',
            'better', 'enhance', 'fix', 'issue', 'problem', 'why',
            'how', 'should', 'could', 'would', 'think', 'opinion'
        ])
        
        # Build the prompt for AI
        if needs_analysis:
            prompt = f"""{SYSTEM_KNOWLEDGE}

Current System State:
{system_context}

User Question: {msg.message}

Provide a detailed, technical response as the lead developer. Include:
1. Specific technical analysis
2. Concrete suggestions with code examples if relevant
3. Potential risks and how to mitigate them
4. Priority ranking of suggestions
5. Implementation approach

Be direct, technical, and actionable. Show your expertise."""
        else:
            # For status/info requests, include actual data
            prompt = f"""{SYSTEM_KNOWLEDGE}

Current System State:
{system_context}

User Question: {msg.message}

Respond with actual system data and technical insights. Be specific and include real metrics."""
        
        try:
            # Get AI response
            ai_response = await ai_client.generate_response(
                prompt,
                temperature=0.3,  # Lower temperature for technical accuracy
                max_tokens=2000
            )
            
            # If asking about Docker/system status, prepend real data
            if any(word in message_lower for word in ['docker', 'container', 'status', 'health']):
                docker_data = execute_command("docker ps --format 'table {{.Names}}\t{{.Status}}'")
                if docker_data["success"]:
                    return {
                        "response": f"**Current Docker Status:**\n```\n{docker_data['stdout']}\n```\n\n{ai_response}",
                        "executed": True,
                        "ai_enhanced": True
                    }
            
            return {
                "response": ai_response,
                "executed": False,
                "ai_enhanced": True
            }
            
        except Exception as e:
            # Fallback to command execution if AI fails
            return {
                "response": f"AI analysis temporarily unavailable. Error: {str(e)}\n\nExecuting diagnostic commands instead...",
                "executed": False,
                "ai_enhanced": False
            }
    
    else:
        # No AI available - provide helpful command-based response
        if any(word in message_lower for word in ['docker', 'container', 'status']):
            docker_result = execute_command("docker ps --format 'table {{.Names}}\t{{.Status}}'")
            return {
                "response": f"**Docker Status (AI not available):**\n```\n{docker_result['stdout']}\n```",
                "executed": True,
                "ai_enhanced": False
            }
        else:
            return {
                "response": "AI integration not available. Use `/execute <command>` for direct command execution.",
                "executed": False,
                "ai_enhanced": False
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
        analysis = await ai_client.generate_response(prompt, temperature=0.2, max_tokens=3000)
        
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
        suggestions = await ai_client.generate_response(prompt, temperature=0.4, max_tokens=2500)
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
