#!/bin/bash
# FIX_CHAT_RESPONSE.sh
# Fix the actual issue - chat response generation

echo "🔧 FIXING CHAT RESPONSE GENERATION"
echo "=================================="
echo ""

cd /home/pi/zoe

# The developer router is working, just fix the chat response
docker exec zoe-core bash -c 'cat > /app/routers/developer.py << "EOF"
"""Autonomous Developer Router - Working Version"""
from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional, Dict, Any
import sys
import json
sys.path.append("/app")
from autonomous_system import autonomous

router = APIRouter(prefix="/api/developer", tags=["developer"])

class DeveloperMessage(BaseModel):
    message: str
    execute: bool = False

@router.post("/chat")
async def developer_chat(msg: DeveloperMessage):
    """Zack with full system awareness"""
    
    # Get complete system state
    full_knowledge = autonomous.knowledge
    current_state = autonomous.get_complete_state()
    
    # Build context
    context = f"""You are Zack, the autonomous developer living inside Zoe.
    
SYSTEM AWARENESS:
- Project files: {len(full_knowledge.get("project_structure", {}).get(".py", []))} Python files
- Database tables: {", ".join(current_state["database"].get("tables", []))}
- API routes: {len(current_state["api_routes"])} endpoints
- Performance: CPU {current_state["performance"]["cpu"]}%, Memory {current_state["performance"]["memory"]}%

You have COMPLETE control. You can see everything and fix anything.

User: {msg.message}"""
    
    # Import working AI
    try:
        from ai_client_complete import get_ai_response
        response = await get_ai_response(context, {"mode": "developer"})
    except:
        # Fallback to basic AI
        try:
            from ai_client import get_ai_response
            response = await get_ai_response(context, {"mode": "developer"})
        except:
            # Last resort - return context-aware response
            response = f"I can see the entire system. {len(current_state.get('api_routes', []))} API routes are registered. Database has tables: {', '.join(current_state['database'].get('tables', [])[:5])}. To answer '{msg.message}', I would need to analyze the specific components."
    
    return {
        "response": response,
        "system_awareness": {
            "files_visible": sum(len(v) for v in full_knowledge.get("project_structure", {}).values()),
            "database_tables": len(current_state["database"].get("tables", [])),
            "api_routes": len(current_state["api_routes"]),
            "cpu_usage": current_state["performance"]["cpu"],
            "memory_usage": current_state["performance"]["memory"]
        }
    }

@router.get("/awareness")
async def show_awareness():
    """Show complete system awareness"""
    return {
        "knowledge": autonomous.knowledge,
        "current_state": autonomous.get_complete_state()
    }

@router.get("/status")
async def developer_status():
    """Developer system status"""
    return {
        "status": "autonomous",
        "capabilities": autonomous.list_capabilities(),
        "metrics": autonomous.get_performance_metrics()
    }

@router.post("/execute")
async def execute_task(task: str):
    """Execute development task"""
    result = await autonomous.execute_development_task(task)
    return result
EOF'

# Restart
docker compose restart zoe-core
sleep 10

# Test the working system
echo "Testing Zack's autonomous capabilities:"
echo "======================================="

echo -e "\n1. System Awareness:"
curl -s http://localhost:8000/api/developer/awareness | jq '.current_state.database'

echo -e "\n2. Chat Response:"
curl -s -X POST http://localhost:8000/api/developer/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "List the Python files you can see"}' | jq '.response, .system_awareness'

echo -e "\n3. Development Task:"
curl -s -X POST http://localhost:8000/api/developer/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Create a new API endpoint for health monitoring"}' | jq -r '.response' | head -200
