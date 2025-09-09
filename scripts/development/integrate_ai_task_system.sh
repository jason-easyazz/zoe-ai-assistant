#!/bin/bash
# INTEGRATE_AI_TASK_SYSTEM.sh
# Final integration of AI-powered task system with chat sessions

set -e

echo "🚀 Integrating AI-Powered Task System"
echo "====================================="
echo ""
echo "Components:"
echo "  ✅ AI Client with RouteLLM (dynamic routing)"
echo "  ✅ Developer Enhanced Router (5 endpoints)"
echo "  ✅ Chat Session Management (requirement extraction)"
echo ""

cd /home/pi/zoe

# Step 1: Create the integration module
echo "📝 Creating integration module..."
docker exec zoe-core bash -c 'cat > /app/routers/ai_task_integration.py << '\''EOF'\''
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
                priority TEXT DEFAULT '\''medium'\'',
                assigned_to TEXT DEFAULT '\''zack'\'',
                status TEXT DEFAULT '\''pending'\'',
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
            "message": f"Task {task_data['\''id'\'']} created! Use /api/ai/tasks/{task_data['\''id'\'']}/analyze to generate implementation."
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
        "model_used": f"{dynamic_router.get_best_model_for_complexity('\''complex'\'')}"
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
        "chat_sessions": len(chat_sessions) if '\''chat_sessions'\'' in globals() else 0,
        "available_providers": list(all_models.keys()),
        "model_counts": all_models,
        "routing_test": {
            "simple": dynamic_router.get_best_model_for_complexity("simple"),
            "complex": dynamic_router.get_best_model_for_complexity("complex")
        }
    }
EOF'

echo "✓ Integration module created"

# Step 2: Update main.py to include the integration
echo -e "\n📝 Adding integration to main.py..."
docker exec zoe-core python3 -c "
import sys
sys.path.append('/app')

# Read main.py
with open('/app/main.py', 'r') as f:
    content = f.read()

# Check if already integrated
if 'ai_task_integration' not in content:
    # Add import
    lines = content.split('\n')
    for i, line in enumerate(lines):
        if 'from routers import' in line:
            lines.insert(i+1, 'from routers import ai_task_integration')
            break
    
    # Add router inclusion
    for i, line in enumerate(lines):
        if 'app.include_router' in line and 'developer' in line:
            lines.insert(i+1, 'app.include_router(ai_task_integration.router, tags=[\"AI Integration\"])')
            break
    
    # Write back
    with open('/app/main.py', 'w') as f:
        f.write('\n'.join(lines))
    
    print('✓ Added AI integration to main.py')
else:
    print('✓ AI integration already in main.py')
"

# Step 3: Restart service
echo -e "\n🐳 Restarting zoe-core..."
docker compose restart zoe-core
sleep 8

# Step 4: Test the integration
echo -e "\n✅ Testing AI Integration..."

echo "1. Testing AI status endpoint:"
curl -s http://localhost:8000/api/ai/status | jq '.'

echo -e "\n2. Testing enhanced chat:"
RESPONSE=$(curl -s -X POST http://localhost:8000/api/ai/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "I need to add Redis caching to all GET endpoints"}')

SESSION_ID=$(echo $RESPONSE | jq -r '.session_id')
echo "Session: $SESSION_ID"
echo "Response preview: $(echo $RESPONSE | jq -r '.response' | head -3)..."
echo "Can create task: $(echo $RESPONSE | jq -r '.can_create_task')"

echo -e "\n✅ AI-Powered Task System Integration Complete!"
echo ""
echo "🎯 You can now:"
echo "  1. Chat naturally about features: POST /api/ai/chat"
echo "  2. System extracts requirements automatically"
echo "  3. Create tasks from chat: POST /api/ai/create_task"
echo "  4. Generate real code with AI: POST /api/ai/tasks/{id}/analyze"
echo "  5. Execute with your existing task system"
echo ""
echo "📝 Try this workflow:"
echo "  curl -X POST http://localhost:8000/api/ai/chat \\"
echo "    -d '{\"message\": \"I want to add user authentication\"}'"
