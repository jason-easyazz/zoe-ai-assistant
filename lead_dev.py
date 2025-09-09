from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
import subprocess
import sqlite3
import json
import sys
from datetime import datetime

sys.path.append("/app")

router = APIRouter(prefix="/api/developer", tags=["developer"])

class DevelopmentTask(BaseModel):
    task_id: Optional[str] = None
    type: str
    description: str
    auto_deploy: bool = False

tasks = {}

class LeadDeveloper:
    def __init__(self):
        self.project_root = "/app"
        
    def analyze_project(self):
        issues = []
        
        # Check containers
        result = subprocess.run("docker ps -a | grep zoe-", shell=True, capture_output=True, text=True)
        for line in result.stdout.strip().split('\n'):
            if line and "Exited" in line:
                issues.append({"type": "container", "severity": "high", "issue": f"Container down: {line[:30]}"})
        
        # Check database
        try:
            conn = sqlite3.connect("/app/data/zoe.db")
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'")
            table_count = cursor.fetchone()[0]
            conn.close()
        except Exception as e:
            issues.append({"type": "database", "severity": "high", "issue": str(e)})
        
        # Check for code issues
        result = subprocess.run("grep -r 'TODO\\|FIXME' /app --include='*.py' | wc -l", shell=True, capture_output=True, text=True)
        todo_count = int(result.stdout.strip() or 0)
        if todo_count > 10:
            issues.append({"type": "code", "severity": "low", "issue": f"{todo_count} TODOs found"})
        
        health_score = max(0, 100 - (len(issues) * 10))
        
        return {
            "timestamp": datetime.now().isoformat(),
            "issues": issues,
            "health_score": health_score,
            "table_count": table_count if 'table_count' in locals() else 0
        }
    
    async def create_solution(self, issues):
        from ai_client_complete import get_ai_response
        
        context = f"You are Zack, lead developer. Create specific fixes for: {json.dumps(issues)}"
        response = await get_ai_response(context, {"mode": "developer"})
        
        return {"solution": response, "ready": True}

lead = LeadDeveloper()

@router.post("/analyze")
async def analyze_project():
    analysis = lead.analyze_project()
    task_id = f"task_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    tasks[task_id] = {"status": "analyzed", "analysis": analysis}
    return {
        "task_id": task_id,
        "health_score": analysis["health_score"],
        "issues_count": len(analysis["issues"]),
        "issues": analysis["issues"]
    }

@router.post("/propose-fix/{task_id}")
async def propose_fix(task_id: str):
    if task_id not in tasks:
        raise HTTPException(404, "Task not found")
    
    task = tasks[task_id]
    solution = await lead.create_solution(task["analysis"]["issues"])
    task["solution"] = solution
    
    return {"task_id": task_id, "solution_ready": True}

@router.get("/status")
async def status():
    return {
        "status": "operational",
        "role": "lead_developer",
        "capabilities": ["analysis", "solution", "deployment"],
        "active_tasks": len(tasks)
    }

@router.post("/chat")
async def chat(msg: DevelopmentTask):
    from ai_client_complete import get_ai_response
    response = await get_ai_response(f"Lead developer Zack: {msg.description}", {"mode": "developer"})
    return {"response": response}
