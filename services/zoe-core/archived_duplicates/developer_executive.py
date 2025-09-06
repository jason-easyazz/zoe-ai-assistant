"""Executive-style developer chat with plan generation"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, Dict, Any, List
import subprocess
import sqlite3
import json
import uuid
from datetime import datetime
import re
import sys
sys.path.append('/app')

router = APIRouter(prefix="/api/developer")

class ChatMessage(BaseModel):
    message: str
    context: Optional[Dict[str, Any]] = {}

class ChatResponse(BaseModel):
    response: str
    plan: Optional[Dict[str, Any]] = None
    code: Optional[str] = None  # Keep for compatibility
    task_id: Optional[str] = None
    conversation_id: Optional[str] = None

def get_db_connection():
    conn = sqlite3.connect("/app/data/zoe.db")
    conn.row_factory = sqlite3.Row
    return conn

def analyze_request(message: str) -> str:
    """Determine request type"""
    message_lower = message.lower()
    
    if any(word in message_lower for word in ['create', 'build', 'implement', 'add', 'fix', 'generate', 'write']):
        return "development"
    elif any(word in message_lower for word in ['check', 'status', 'health', 'error', 'debug', 'show']):
        return "diagnostic"
    elif any(word in message_lower for word in ['optimize', 'improve', 'speed', 'performance']):
        return "optimization"
    else:
        return "inquiry"

def get_system_context() -> Dict[str, Any]:
    """Quick system check"""
    context = {"status": "operational"}
    
    try:
        # Container count
        result = subprocess.run(
            "docker ps --format '{{.Names}}' | grep -c zoe-",
            shell=True, capture_output=True, text=True, timeout=2
        )
        context["containers"] = int(result.stdout.strip()) if result.stdout.strip().isdigit() else 0
        
        # API health
        result = subprocess.run(
            "curl -s http://localhost:8000/health",
            shell=True, capture_output=True, text=True, timeout=2
        )
        context["api_healthy"] = "healthy" in result.stdout.lower()
        
    except:
        pass
    
    return context

def generate_plan(request_type: str, message: str) -> Dict[str, Any]:
    """Generate structured plan"""
    
    # Base plan structure
    plan = {
        "title": message[:60],
        "type": request_type,
        "phases": [],
        "metadata": {
            "estimated_time": "5-10 minutes",
            "risk_level": "low",
            "auto_approve": False
        }
    }
    
    # Define phases based on type
    if request_type == "development":
        plan["phases"] = [
            {"step": 1, "action": "Analyze requirements", "status": "pending"},
            {"step": 2, "action": "Design solution architecture", "status": "pending"},
            {"step": 3, "action": "Generate implementation code", "status": "pending"},
            {"step": 4, "action": "Create tests", "status": "pending"},
            {"step": 5, "action": "Deploy and verify", "status": "pending"}
        ]
        
    elif request_type == "diagnostic":
        plan["phases"] = [
            {"step": 1, "action": "Collect system metrics", "status": "pending"},
            {"step": 2, "action": "Analyze service health", "status": "pending"},
            {"step": 3, "action": "Review logs", "status": "pending"},
            {"step": 4, "action": "Identify issues", "status": "pending"}
        ]
        plan["metadata"]["estimated_time"] = "2-3 minutes"
        
    elif request_type == "optimization":
        plan["phases"] = [
            {"step": 1, "action": "Benchmark current performance", "status": "pending"},
            {"step": 2, "action": "Identify bottlenecks", "status": "pending"},
            {"step": 3, "action": "Design optimizations", "status": "pending"},
            {"step": 4, "action": "Implement improvements", "status": "pending"},
            {"step": 5, "action": "Measure results", "status": "pending"}
        ]
        plan["metadata"]["risk_level"] = "medium"
    
    else:  # inquiry
        plan["phases"] = [
            {"step": 1, "action": "Research information", "status": "pending"},
            {"step": 2, "action": "Compile findings", "status": "pending"}
        ]
        plan["metadata"]["estimated_time"] = "1-2 minutes"
    
    return plan

@router.post("/chat", response_model=ChatResponse)
async def developer_chat(msg: ChatMessage):
    """Executive-style chat returning plans not code"""
    
    # Generate IDs
    conversation_id = f"CONV-{uuid.uuid4().hex[:8].upper()}"
    
    # Analyze request
    request_type = analyze_request(msg.message)
    
    # Get system context
    system_context = get_system_context()
    
    # Generate plan
    plan = generate_plan(request_type, msg.message)
    
    # Create executive summary based on type
    if request_type == "development":
        response = f"""**Request:** {msg.message[:60]}...

**Assessment:** Development task identified
**Approach:** 5-phase implementation plan
**Duration:** {plan['metadata']['estimated_time']}

**Next:** Review plan → Create task → Execute"""
        
    elif request_type == "diagnostic":
        containers = system_context.get('containers', 0)
        api_status = "✅ Healthy" if system_context.get('api_healthy') else "⚠️ Check needed"
        
        response = f"""**Diagnostic Check:** {msg.message[:60]}...

**System:** {containers}/7 containers active
**API:** {api_status}

**Actions:** 4-step diagnostic plan ready"""
        
    elif request_type == "optimization":
        response = f"""**Optimization Target:** {msg.message[:60]}...

**Analysis:** Performance enhancement opportunity
**Risk:** {plan['metadata']['risk_level']}
**Phases:** 5-step optimization plan

**Recommendation:** Review plan before execution"""
        
    else:  # inquiry
        response = f"""**Query:** {msg.message[:60]}...

**Type:** Information request
**Action:** 2-step research plan created

**Next:** Execute plan for detailed answer"""
    
    # Store conversation
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS developer_conversations (
            conversation_id TEXT PRIMARY KEY,
            original_request TEXT,
            request_type TEXT,
            plan TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    cursor.execute("""
        INSERT INTO developer_conversations (conversation_id, original_request, request_type, plan)
        VALUES (?, ?, ?, ?)
    """, (conversation_id, msg.message, request_type, json.dumps(plan)))
    
    conn.commit()
    conn.close()
    
    return ChatResponse(
        response=response,
        plan=plan,
        conversation_id=conversation_id
    )

@router.post("/tasks/from-plan")
async def create_task_from_plan(request: Dict[str, Any]):
    """Create task from approved plan"""
    
    task_id = f"TASK-{uuid.uuid4().hex[:8].upper()}"
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Ensure tasks table exists
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            task_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            description TEXT,
            task_type TEXT DEFAULT 'feature',
            status TEXT DEFAULT 'pending',
            conversation_id TEXT,
            plan TEXT,
            original_request TEXT,
            code_generated TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    cursor.execute("""
        INSERT INTO tasks (task_id, title, description, task_type, conversation_id, plan, original_request, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'approved')
    """, (
        task_id,
        request.get('title', 'Untitled Task'),
        request.get('description', ''),
        request.get('plan', {}).get('type', 'development'),
        request.get('conversation_id'),
        json.dumps(request.get('plan', {})),
        request.get('original_request', '')
    ))
    
    conn.commit()
    conn.close()
    
    return {
        "task_id": task_id,
        "status": "created",
        "message": "Task created and ready for execution"
    }

@router.get("/status")
async def status():
    return {"status": "operational", "mode": "executive"}
