"""AI Task Integration - Brings everything together"""
from fastapi import APIRouter, HTTPException
from typing import Optional, Dict, Any
import json
import sqlite3
from datetime import datetime
import sys
sys.path.append("/app")
sys.path.append("/app/routers")

# Import our components
from ai_client_enhanced import ai_client
from chat_sessions import (
    get_or_create_session, create_task_from_session,
    analyze_message_for_implementation, suggest_task_creation
)
from dynamic_router import dynamic_router

router = APIRouter(prefix="/api/ai", tags=["AI Integration"])

@router.post("/chat")
async def enhanced_chat(
    message: str,
    session_id: Optional[str] = None,
    mode: str = "developer"
):
    """Enhanced chat with session tracking and requirement extraction"""
    
    # Get or create session
    session = get_or_create_session(session_id)
    session.add_message("user", message)
    
    # Check if discussing implementation
    is_implementation = analyze_message_for_implementation(message)
    
    # Get appropriate AI response
    complexity = "simple"
    if is_implementation:
        complexity = "medium" if len(message) < 200 else "complex"
    
    # Route to best model
    provider, model = dynamic_router.get_best_model_for_complexity(complexity)
    print(f"Routing to: {provider}/{model} for {complexity} query")
    
    # Generate response
    try:
        response = await ai_client.chat_with_developer(message, session.messages)
    except Exception as e:
        response = f"I understand you want to: {message}. Let me help you break that down into requirements."
    
    # Add response to session
    session.add_message("assistant", response)
    
    # Extract requirements if implementation discussion
    if is_implementation:
        session.extract_requirements(message, response)
        
        # Suggest task creation if appropriate
        suggestion = suggest_task_creation(session)
        if suggestion:
            response += suggestion
    
    return {
        "response": response,
        "session_id": session.session_id,
        "can_create_task": session.can_create_task(),
        "requirements_count": len(session.extracted_requirements),
        "model_used": f"{provider}/{model}"
    }

@router.post("/create_task")
async def create_task_from_chat(
    session_id: str,
    title: Optional[str] = None
):
    """Convert chat session to executable task"""
    
    try:
        # Create task from session
        task_data = create_task_from_session(session_id, title)
        
        # Save to database
        conn = sqlite3.connect("/app/data/developer_tasks.db")
        cursor = conn.cursor()
        
        # Ensure table exists
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS dynamic_tasks (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                objective TEXT NOT NULL,
                requirements TEXT,
                constraints TEXT,
                acceptance_criteria TEXT,
                priority TEXT DEFAULT 'medium',
                assigned_to TEXT DEFAULT 'zack',
                status TEXT DEFAULT 'pending',
                context_snapshot TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                execution_count INTEGER DEFAULT 0
            )
        """)
        
        cursor.execute("""
            INSERT INTO dynamic_tasks 
            (id, title, objective, requirements, constraints, 
             acceptance_criteria, priority, assigned_to, status, context_snapshot)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            task_data["id"],
            task_data["title"],
            task_data["objective"],
            json.dumps(task_data["requirements"]),
            json.dumps(task_data.get("constraints", [])),
            json.dumps(task_data.get("acceptance_criteria", [])),
            task_data.get("priority", "medium"),
            task_data.get("assigned_to", "zack"),
            "pending",
            json.dumps(task_data.get("chat_context", []))
        ))
        
        conn.commit()
        conn.close()
        
        return {
            "task_id": task_data["id"],
            "title": task_data["title"],
            "requirements": len(task_data["requirements"]),
            "message": f"Task {task_data['id']} created! Use /api/ai/tasks/{task_data['id']}/analyze to generate implementation."
        }
        
    except Exception as e:
        raise HTTPException(400, str(e))

@router.post("/tasks/{task_id}/analyze")
async def analyze_task_with_ai(task_id: str):
    """Generate implementation plan using AI"""
    
    # Get task from database
    conn = sqlite3.connect("/app/data/developer_tasks.db")
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, title, objective, requirements, constraints, 
               acceptance_criteria, context_snapshot 
        FROM dynamic_tasks 
        WHERE id = ?
    """, (task_id,))
    task = cursor.fetchone()
    conn.close()
    
    if not task:
        raise HTTPException(404, "Task not found")
    
    # Parse task data
    task_data = {
        "id": task[0],
        "title": task[1],
        "objective": task[2],
        "requirements": json.loads(task[3]) if task[3] else [],
        "constraints": json.loads(task[4]) if task[4] else [],
        "acceptance_criteria": json.loads(task[5]) if task[5] else []
    }
    
    # Parse chat context if available
    chat_context = None
    if task[6]:
        try:
            chat_context = json.loads(task[6])
        except:
            pass
    
    # Generate implementation plan with AI
    plan = await ai_client.generate_implementation(task_data, chat_context)
    
    return {
        "task_id": task_id,
        "title": task_data["title"],
        "generated_at": datetime.now().isoformat(),
        "implementation": plan,
        "model_used": f"{dynamic_router.get_best_model_for_complexity('complex')}"
    }

@router.get("/status")
async def get_ai_status():
    """Get AI system status"""
    
    # Get model configuration
    all_models = {}
    try:
        import json
        with open("/app/data/llm_models.json") as f:
            config = json.load(f)
            for provider, data in config["providers"].items():
                if data.get("enabled"):
                    all_models[provider] = len(data.get("models", []))
    except:
        pass
    
    return {
        "ai_client": "ready",
        "dynamic_router": "configured",
        "chat_sessions": len(chat_sessions) if 'chat_sessions' in globals() else 0,
        "available_providers": list(all_models.keys()),
        "model_counts": all_models,
        "routing_test": {
            "simple": dynamic_router.get_best_model_for_complexity("simple"),
            "complex": dynamic_router.get_best_model_for_complexity("complex")
        }
    }
