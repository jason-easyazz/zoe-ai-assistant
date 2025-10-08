"""
GENIUS ZACK - Proactively Intelligent Developer System
This version doesn't just respond - it THINKS, ANALYZES, and SUGGESTS
"""

from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
import subprocess
import sqlite3
import json
import sys
import os
from datetime import datetime
import psutil
import logging
import uuid
import asyncio
import re

sys.path.append("/app")
logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/developer", tags=["developer"])

# Models
class DeveloperChat(BaseModel):
    message: str
    context: Optional[Dict[str, Any]] = {}

class DevelopmentTask(BaseModel):
    title: str
    description: str
    type: str = "feature"
    priority: str = "medium"

# ============================================
# SYSTEM INTELLIGENCE ENGINE
# ============================================

def execute_command(cmd: str, timeout: int = 30, cwd: str = "/app") -> dict:
    """Execute system commands with full visibility"""
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True,
            timeout=timeout, cwd=cwd
        )
        return {
            "stdout": result.stdout[:10000],
            "stderr": result.stderr[:2000],
            "returncode": result.returncode,
            "success": result.returncode == 0
        }
    except Exception as e:
        return {"stdout": "", "stderr": str(e), "returncode": -1, "success": False}

def analyze_system_deeply() -> dict:
    """Deep system analysis with intelligence"""
    analysis = {
        "metrics": {},
        "health_score": 100,
        "issues": [],
        "opportunities": [],
        "recommendations": [],
        "suggested_features": []
    }
    
    try:
        # Get comprehensive metrics
        cpu = psutil.cpu_percent(interval=1)
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        
        analysis["metrics"] = {
            "cpu_percent": cpu,
            "memory_percent": round(mem.percent, 1),
            "memory_available_gb": round(mem.available / (1024**3), 2),
            "disk_percent": round(disk.percent, 1),
            "disk_free_gb": round(disk.free / (1024**3), 2),
        }
        
        # Check Docker health
        docker_result = execute_command("docker ps -a --format '{{.Names}}|{{.Status}}'")
        if docker_result["success"]:
            containers = docker_result["stdout"].strip().split('\n')
            running = [c for c in containers if 'Up' in c]
            analysis["metrics"]["containers_running"] = len(running)
            analysis["metrics"]["containers_total"] = len(containers)
            
            # Check for stopped containers
            if len(running) < len(containers):
                analysis["issues"].append({
                    "type": "container_stopped",
                    "severity": "medium",
                    "containers": [c.split('|')[0] for c in containers if 'Up' not in c]
                })
                analysis["health_score"] -= 10
        
        # Analyze logs for errors
        log_result = execute_command("docker logs zoe-core --tail 100 2>&1 | grep -c ERROR || echo 0")
        error_count = int(log_result["stdout"].strip())
        if error_count > 0:
            analysis["issues"].append({
                "type": "errors_in_logs",
                "severity": "low",
                "count": error_count
            })
            analysis["health_score"] -= 5
        
        # Performance analysis
        if cpu > 80:
            analysis["issues"].append({
                "type": "high_cpu",
                "severity": "high",
                "value": cpu
            })
            analysis["health_score"] -= 20
            
        if mem.percent > 85:
            analysis["issues"].append({
                "type": "high_memory",
                "severity": "high",
                "value": mem.percent
            })
            analysis["health_score"] -= 20
            
        if disk.percent > 90:
            analysis["issues"].append({
                "type": "low_disk",
                "severity": "critical",
                "value": disk.percent
            })
            analysis["health_score"] -= 30
        
        # Generate intelligent recommendations
        if analysis["health_score"] < 100:
            if any(i["type"] == "high_memory" for i in analysis["issues"]):
                analysis["recommendations"].append({
                    "action": "restart_heavy_containers",
                    "command": "docker restart zoe-ollama",
                    "reason": "Ollama often accumulates memory over time"
                })
            
            if any(i["type"] == "low_disk" for i in analysis["issues"]):
                analysis["recommendations"].append({
                    "action": "clean_docker",
                    "command": "docker system prune -af --volumes",
                    "reason": "Remove unused Docker resources"
                })
        
        # Proactive feature suggestions based on system state
        analysis["opportunities"].append({
            "type": "optimization",
            "title": "Implement Resource Monitoring Dashboard",
            "reason": f"System is at {analysis['health_score']}% health",
            "complexity": "medium"
        })
        
        # Check what features might be missing
        features_check = {
            "voice": execute_command("docker ps | grep -c whisper || echo 0"),
            "automation": execute_command("docker ps | grep -c n8n || echo 0"),
            "cache": execute_command("docker ps | grep -c redis || echo 0")
        }
        
        for feature, result in features_check.items():
            if result["stdout"].strip() == "0":
                analysis["suggested_features"].append({
                    "feature": feature,
                    "benefit": f"Enable {feature} capabilities",
                    "command": f"docker compose up -d zoe-{feature}"
                })
        
    except Exception as e:
        analysis["error"] = str(e)
    
    return analysis

def generate_implementation_plan(request: str) -> dict:
    """Generate detailed implementation plan for requests"""
    plan = {
        "request": request,
        "steps": [],
        "estimated_time": "unknown",
        "files_to_modify": [],
        "tests_needed": [],
        "risks": []
    }
    
    request_lower = request.lower()
    
    # Analyze request type
    if any(word in request_lower for word in ['api', 'endpoint']):
        plan["steps"] = [
            "1. Create new router file in /app/routers/",
            "2. Define Pydantic models for request/response",
            "3. Implement endpoint logic",
            "4. Add router to main.py",
            "5. Create tests",
            "6. Update documentation"
        ]
        plan["files_to_modify"] = [
            "/app/routers/new_feature.py",
            "/app/main.py",
            "/app/tests/test_new_feature.py"
        ]
        plan["estimated_time"] = "30 minutes"
        
    elif any(word in request_lower for word in ['ui', 'interface', 'page']):
        plan["steps"] = [
            "1. Create HTML page in /usr/share/nginx/html/",
            "2. Add glass-morphic styling",
            "3. Implement JavaScript functionality",
            "4. Connect to backend API",
            "5. Add navigation links",
            "6. Test responsiveness"
        ]
        plan["files_to_modify"] = [
            "/usr/share/nginx/html/new_page.html",
            "/usr/share/nginx/html/js/new_page.js"
        ]
        plan["estimated_time"] = "45 minutes"
        
    elif any(word in request_lower for word in ['fix', 'bug', 'error']):
        plan["steps"] = [
            "1. Reproduce the issue",
            "2. Analyze logs for errors",
            "3. Identify root cause",
            "4. Implement fix",
            "5. Test thoroughly",
            "6. Deploy and monitor"
        ]
        plan["estimated_time"] = "15-60 minutes"
        plan["risks"] = ["May affect other components", "Requires careful testing"]
    
    return plan

# ============================================
# GENIUS CHAT ENDPOINT
# ============================================

@router.post("/chat")
async def developer_chat(request: DeveloperChat):
    """Genius-level developer chat with proactive intelligence"""
    
    # Import AI client
    try:
        from ai_client import get_ai_response
        ai_available = True
    except:
        ai_available = False
    
    message_lower = request.message.lower()
    
    # Perform deep analysis
    system_analysis = analyze_system_deeply()
    
    # Build comprehensive context
    context = {
        "mode": "developer",
        "system_analysis": system_analysis,
        "timestamp": datetime.now().isoformat()
    }
    
    # If AI is available, use it for intelligent responses
    if ai_available:
        # Build a rich prompt for the AI
        ai_prompt = f"""You are Zack, a genius-level AI developer with full system access.

CURRENT SYSTEM STATE:
- Health Score: {system_analysis['health_score']}%
- CPU: {system_analysis['metrics']['cpu_percent']}%
- Memory: {system_analysis['metrics']['memory_percent']}%
- Disk: {system_analysis['metrics']['disk_percent']}%
- Issues: {len(system_analysis['issues'])} detected
- Opportunities: {len(system_analysis['opportunities'])} identified

USER REQUEST: {request.message}

INSTRUCTIONS:
1. Provide a comprehensive, intelligent response
2. If there are system issues, suggest fixes
3. Proactively recommend improvements
4. If asked to build something, provide actual code
5. Think beyond the immediate request
6. Suggest related features or optimizations

Be creative, thorough, and proactive. Don't just answer - THINK and INNOVATE."""

        try:
            ai_response = await get_ai_response(ai_prompt, context)
            
            # Enhance AI response with system data
            response_parts = []
            
            # Add system status if relevant
            if any(word in message_lower for word in ['status', 'health', 'how', 'check']):
                response_parts.append(f"## Current System Analysis\n")
                response_parts.append(f"**Health Score:** {system_analysis['health_score']}%\n")
                response_parts.append(f"**Performance:** CPU {system_analysis['metrics']['cpu_percent']}% | Memory {system_analysis['metrics']['memory_percent']}% | Disk {system_analysis['metrics']['disk_percent']}%\n")
                
                if system_analysis['issues']:
                    response_parts.append("\n### ⚠️ Detected Issues:")
                    for issue in system_analysis['issues']:
                        response_parts.append(f"- **{issue['type']}**: Severity {issue['severity']}")
                
                if system_analysis['recommendations']:
                    response_parts.append("\n### 🔧 Recommendations:")
                    for rec in system_analysis['recommendations']:
                        response_parts.append(f"- **{rec['action']}**: `{rec['command']}`")
                        response_parts.append(f"  Reason: {rec['reason']}")
            
            # Add AI response
            response_parts.append("\n" + (ai_response if isinstance(ai_response, str) else ai_response.get('response', '')))
            
            # Add proactive suggestions
            if system_analysis['suggested_features']:
                response_parts.append("\n### 💡 Proactive Suggestions:")
                for feature in system_analysis['suggested_features']:
                    response_parts.append(f"- Enable **{feature['feature']}**: {feature['benefit']}")
            
            response = "\n".join(response_parts)
            
        except Exception as e:
            logger.error(f"AI error: {e}")
            # Fallback to intelligent static response
            response = generate_static_intelligent_response(request.message, system_analysis)
    else:
        # No AI available - use intelligent static responses
        response = generate_static_intelligent_response(request.message, system_analysis)
    
    # If this is a build request, create a task
    if any(word in message_lower for word in ['create', 'build', 'implement', 'add']):
        task_id = await create_and_track_task(request.message)
        response += f"\n\n📋 **Task Created:** `{task_id}`\nI'll track this implementation for you."
    
    return {
        "response": response,
        "system_analysis": system_analysis,
        "health_score": system_analysis['health_score']
    }

def generate_static_intelligent_response(message: str, analysis: dict) -> str:
    """Generate intelligent response without AI"""
    message_lower = message.lower()
    response_parts = []
    
    # Provide deep analysis for status requests
    if any(word in message_lower for word in ['status', 'health', 'system']):
        response_parts.append(f"## Comprehensive System Analysis")
        response_parts.append(f"\n**Overall Health:** {analysis['health_score']}%")
        response_parts.append(f"\n### Performance Metrics:")
        response_parts.append(f"- CPU: {analysis['metrics']['cpu_percent']}% utilized")
        response_parts.append(f"- Memory: {analysis['metrics']['memory_available_gb']}GB available")
        response_parts.append(f"- Disk: {analysis['metrics']['disk_free_gb']}GB free")
        
        if analysis['issues']:
            response_parts.append("\n### Issues Detected:")
            for issue in analysis['issues']:
                response_parts.append(f"- {issue['type']}: {issue.get('severity', 'unknown')} severity")
        else:
            response_parts.append("\n✅ No issues detected - system is healthy!")
    
    # Generate implementation plans
    elif any(word in message_lower for word in ['create', 'build', 'implement']):
        plan = generate_implementation_plan(message)
        response_parts.append("## Implementation Plan")
        response_parts.append(f"\n**Request:** {plan['request']}")
        response_parts.append(f"\n**Estimated Time:** {plan['estimated_time']}")
        response_parts.append("\n### Steps:")
        for step in plan['steps']:
            response_parts.append(f"- {step}")
        if plan['files_to_modify']:
            response_parts.append("\n### Files to Modify:")
            for file in plan['files_to_modify']:
                response_parts.append(f"- `{file}`")
    
    else:
        # Default intelligent response
        response_parts.append("## Zack - Genius Developer Assistant")
        response_parts.append(f"\nSystem Health: {analysis['health_score']}%")
        response_parts.append("\n### How can I help you innovate today?")
        response_parts.append("- 🚀 Build new features")
        response_parts.append("- 🔧 Optimize performance")
        response_parts.append("- 🐛 Fix issues")
        response_parts.append("- 📊 Analyze system")
        response_parts.append("- 💡 Suggest improvements")
    
    return "\n".join(response_parts)

async def create_and_track_task(description: str) -> str:
    """Create and track implementation tasks"""
    task_id = f"TASK-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    
    conn = sqlite3.connect("/app/data/zoe.db")
    cursor = conn.cursor()
    
    cursor.execute("""
        INSERT INTO tasks (task_id, title, description, type, priority, status)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (task_id, description[:100], description, "feature", "medium", "pending"))
    
    conn.commit()
    conn.close()
    
    return task_id

# ============================================
# ENHANCED ENDPOINTS
# ============================================

@router.get("/status")
async def get_status():
    """Enhanced status with intelligence metrics"""
    analysis = analyze_system_deeply()
    return {
        "status": "genius-level",
        "personality": "Zack",
        "health_score": analysis['health_score'],
        "capabilities": [
            "proactive_analysis",
            "autonomous_development",
            "system_optimization",
            "creative_solutions"
        ],
        "intelligence_level": "maximum"
    }

@router.get("/metrics")
async def get_metrics():
    """Get deep system metrics"""
    return analyze_system_deeply()

@router.post("/tasks")
async def create_task(task: DevelopmentTask):
    """Create task with proper JSON response"""
    task_id = f"TASK-{uuid.uuid4().hex[:8].upper()}"
    
    conn = sqlite3.connect("/app/data/zoe.db")
    cursor = conn.cursor()
    
    cursor.execute("""
        INSERT INTO tasks (task_id, title, description, type, priority)
        VALUES (?, ?, ?, ?, ?)
    """, (task_id, task.title, task.description, task.type, task.priority))
    
    conn.commit()
    conn.close()
    
    # Return proper JSON
    return {"task_id": task_id, "status": "created"}

@router.get("/analyze")
async def analyze_system():
    """Endpoint for deep system analysis"""
    return analyze_system_deeply()

@router.post("/suggest")
async def suggest_improvements():
    """Proactively suggest system improvements"""
    analysis = analyze_system_deeply()
    
    suggestions = {
        "immediate_actions": [],
        "improvements": [],
        "new_features": []
    }
    
    # Immediate actions based on health
    if analysis['health_score'] < 80:
        suggestions["immediate_actions"].extend([
            rec["command"] for rec in analysis.get("recommendations", [])
        ])
    
    # Improvements based on metrics
    suggestions["improvements"] = analysis.get("opportunities", [])
    
    # New features
    suggestions["new_features"] = analysis.get("suggested_features", [])
    
    return suggestions
