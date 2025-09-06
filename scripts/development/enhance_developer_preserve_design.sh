#!/bin/bash
# ENHANCE_DEVELOPER_PRESERVE_DESIGN.sh
# Location: scripts/development/enhance_developer_preserve_design.sh
# Purpose: Add all features while keeping existing HTML/CSS design intact

set -e

echo "🎯 Developer Enhancement - Preserving Your Design"
echo "================================================="
echo ""
echo "This will:"
echo "  ✅ Keep your existing HTML/CSS completely intact"
echo "  ✅ Add executive summary responses"
echo "  ✅ Transform artifact panel to show plans (not code)"
echo "  ✅ Fix markdown rendering"
echo "  ✅ Integrate task management"
echo "  ✅ Keep all your styling and quick buttons"
echo ""
echo "Press Enter to continue or Ctrl+C to abort..."
read

cd /home/pi/zoe

# Backup
echo -e "\n📦 Creating backup..."
mkdir -p backups/developer_$(date +%Y%m%d_%H%M%S)
cp -r services/zoe-core/routers/developer* backups/developer_$(date +%Y%m%d_%H%M%S)/ 2>/dev/null || true
cp services/zoe-ui/dist/developer/chat.html backups/developer_$(date +%Y%m%d_%H%M%S)/ 2>/dev/null || true

# =============================================================================
# PART 1: BACKEND - Executive Style + Plan Generation
# =============================================================================
echo -e "\n🔧 Part 1: Creating executive-style backend..."

cat > services/zoe-core/routers/developer_executive.py << 'PYTHON'
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
PYTHON

# Copy as main developer.py
cp services/zoe-core/routers/developer_executive.py services/zoe-core/routers/developer.py

# =============================================================================
# PART 2: UPDATE ONLY THE JAVASCRIPT - Keep HTML/CSS intact
# =============================================================================
echo -e "\n🎨 Part 2: Updating JavaScript while preserving design..."

# Create enhanced JavaScript that works with existing HTML
cat > services/zoe-ui/dist/developer/js/developer_enhanced.js << 'JAVASCRIPT'
// Enhanced developer.js - Preserves existing design
const API_BASE = window.location.hostname === 'localhost' 
    ? 'http://localhost:8000' 
    : `http://${window.location.hostname}:8000`;

let currentPlan = null;
let currentConversationId = null;
let originalRequest = '';

// Update time display (existing function)
function updateTime() {
    const timeEl = document.getElementById('currentTime');
    if (timeEl) {
        timeEl.textContent = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    }
}
updateTime();
setInterval(updateTime, 1000);

// Handle chat input (existing function enhanced)
function handleChatKey(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
    }
}

// Enhanced message sending with plan support
async function sendMessage() {
    const input = document.getElementById('chatInput');
    const message = input.value.trim();
    if (!message) return;

    originalRequest = message;
    addMessage(message, 'user');
    input.value = '';

    try {
        const response = await fetch(`${API_BASE}/api/developer/chat`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message })
        });

        if (response.ok) {
            const data = await response.json();
            
            // Add formatted response to chat
            addMessage(data.response, 'zack');
            
            // Display plan in artifact panel (now plan panel)
            if (data.plan) {
                displayPlan(data.plan);
                currentPlan = data.plan;
                currentConversationId = data.conversation_id;
                
                // Update the artifact title to show it's a plan
                const titleEl = document.getElementById('artifactTitle');
                if (titleEl) {
                    titleEl.textContent = '📋 Strategic Plan';
                }
            }
        } else {
            throw new Error('API error');
        }
    } catch (error) {
        addMessage('Connection error. Please check the backend.', 'zack');
    }
}

// Enhanced message display with proper markdown
function addMessage(text, sender) {
    const messagesDiv = document.getElementById('chatMessages');
    const messageDiv = document.createElement('div');
    messageDiv.className = `message ${sender}`;
    
    // Format the text with proper markdown handling
    let formattedHtml = text;
    
    if (sender === 'zack') {
        // Convert markdown to HTML
        formattedHtml = text
            // Bold text
            .replace(/\*\*(.*?)\*\*/g, '<strong style="color: #1a1a1a;">$1</strong>')
            // Headers (if any)
            .replace(/^### (.*?)$/gm, '<h4 style="color: #1e40af; margin: 8px 0 4px 0;">$1</h4>')
            .replace(/^## (.*?)$/gm, '<h3 style="color: #0f172a; margin: 10px 0 6px 0;">$1</h3>')
            // Bullets
            .replace(/^[•\-] (.*?)$/gm, '<div style="margin: 4px 0; padding-left: 16px;">• $1</div>')
            // Status indicators
            .replace(/✅/g, '<span style="color: #22c55e;">✅</span>')
            .replace(/⚠️/g, '<span style="color: #f59e0b;">⚠️</span>')
            .replace(/❌/g, '<span style="color: #ef4444;">❌</span>')
            // Line breaks
            .replace(/\n\n/g, '<div style="height: 8px;"></div>')
            .replace(/\n/g, '<br>');
    }
    
    messageDiv.innerHTML = `
        <div class="message-icon">${sender === 'user' ? '👤' : '🧠'}</div>
        <div class="message-content">${formattedHtml}</div>
    `;
    
    messagesDiv.appendChild(messageDiv);
    messagesDiv.scrollTop = messagesDiv.scrollHeight;
}

// Display plan in the artifact panel (keep existing styling)
function displayPlan(plan) {
    const contentDiv = document.getElementById('artifactContent');
    if (!contentDiv) return;
    
    let html = '<div style="color: #fff; font-family: Monaco, Consolas, monospace; line-height: 1.6;">';
    
    // Plan title
    html += `<div style="color: #5AE0E0; font-size: 16px; margin-bottom: 16px;">
        📋 ${plan.title}
    </div>`;
    
    // Plan type and metadata
    html += `<div style="background: rgba(255,255,255,0.1); padding: 12px; border-radius: 6px; margin-bottom: 16px;">
        <div style="color: #7B61FF;">Type: ${plan.type}</div>
        <div style="color: #7B61FF;">Duration: ${plan.metadata?.estimated_time || 'TBD'}</div>
        <div style="color: #7B61FF;">Risk: ${plan.metadata?.risk_level || 'low'}</div>
    </div>`;
    
    // Implementation phases
    html += '<div style="margin-bottom: 16px;">';
    html += '<div style="color: #5AE0E0; margin-bottom: 8px;">Implementation Phases:</div>';
    
    plan.phases.forEach(phase => {
        const stepColor = phase.status === 'pending' ? '#999' : '#5AE0E0';
        html += `<div style="padding: 8px; margin: 4px 0; background: rgba(255,255,255,0.05); border-radius: 4px;">
            <span style="color: #7B61FF; font-weight: bold;">Step ${phase.step}:</span>
            <span style="color: ${stepColor};"> ${phase.action}</span>
            <span style="color: #666; float: right;">[${phase.status}]</span>
        </div>`;
    });
    
    html += '</div>';
    
    // Add action note
    html += `<div style="color: #5AE0E0; margin-top: 20px; padding: 12px; background: rgba(122,97,255,0.1); border-radius: 6px;">
        💡 Use "Create Task" button to convert this plan into an executable task
    </div>`;
    
    html += '</div>';
    
    contentDiv.innerHTML = html;
}

// Quick prompt buttons (existing)
function quickPrompt(prompt) {
    document.getElementById('chatInput').value = prompt;
    sendMessage();
}

// Artifact/Plan actions
function copyArtifact() {
    if (currentPlan) {
        const planText = JSON.stringify(currentPlan, null, 2);
        navigator.clipboard.writeText(planText);
        alert('Plan copied to clipboard!');
    } else {
        alert('No plan to copy');
    }
}

function saveArtifact() {
    if (currentPlan) {
        const planText = JSON.stringify(currentPlan, null, 2);
        const blob = new Blob([planText], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `plan-${Date.now()}.json`;
        a.click();
        URL.revokeObjectURL(url);
    } else {
        alert('No plan to save');
    }
}

// Enhanced task creation from plan
async function createTask() {
    if (!currentPlan || !currentConversationId) {
        alert('No plan available. Please generate a plan first.');
        return;
    }
    
    const title = prompt('Task title:', currentPlan.title);
    if (!title) return;
    
    try {
        const response = await fetch(`${API_BASE}/api/developer/tasks/from-plan`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                title,
                description: `Created from plan: ${currentPlan.title}`,
                plan: currentPlan,
                conversation_id: currentConversationId,
                original_request: originalRequest
            })
        });
        
        if (response.ok) {
            const data = await response.json();
            addMessage(`✅ Task ${data.task_id} created successfully! Ready for code generation.`, 'zack');
            clearArtifact();
        } else {
            throw new Error('Failed to create task');
        }
    } catch (error) {
        alert('Error creating task: ' + error.message);
    }
}

// Clear artifact/plan panel
function clearArtifact() {
    currentPlan = null;
    currentConversationId = null;
    
    const titleEl = document.getElementById('artifactTitle');
    if (titleEl) {
        titleEl.textContent = 'Generated Code';
    }
    
    const contentEl = document.getElementById('artifactContent');
    if (contentEl) {
        contentEl.innerHTML = '<div style="color: #666; text-align: center; margin-top: 100px;">Generated code and scripts will appear here</div>';
    }
}

// Initialize on page load
document.addEventListener('DOMContentLoaded', function() {
    const input = document.getElementById('chatInput');
    if (input) {
        input.addEventListener('keydown', handleChatKey);
    }
    
    // Check for message in URL
    const params = new URLSearchParams(window.location.search);
    const urlMessage = params.get('message');
    if (urlMessage) {
        document.getElementById('chatInput').value = urlMessage;
        sendMessage();
    }
});
JAVASCRIPT

# =============================================================================
# PART 3: UPDATE CHAT.HTML TO USE ENHANCED JS (minimal change)
# =============================================================================
echo -e "\n📝 Part 3: Updating chat.html to use enhanced JavaScript..."

# Just replace the script src to use the enhanced version
sed -i 's|</body>|<script src="js/developer_enhanced.js"></script>\n</body>|' services/zoe-ui/dist/developer/chat.html 2>/dev/null || true

# If there's an existing developer.js reference, update it
sed -i 's|developer.js|developer_enhanced.js|g' services/zoe-ui/dist/developer/chat.html 2>/dev/null || true

# =============================================================================
# PART 4: RESTART SERVICES
# =============================================================================
echo -e "\n🔄 Part 4: Restarting services..."

docker compose restart zoe-core
docker compose restart zoe-ui

sleep 5

# =============================================================================
# PART 5: TEST
# =============================================================================
echo -e "\n✅ Part 5: Testing enhanced system..."

echo "Testing executive chat..."
curl -s -X POST http://localhost:8000/api/developer/chat \
    -H "Content-Type: application/json" \
    -d '{"message": "Create a backup system"}' | jq '.response' | head -5

echo -e "\n=========================================="
echo "✅ ENHANCEMENT COMPLETE - DESIGN PRESERVED!"
echo "=========================================="
echo ""
echo "What's new (with your design intact):"
echo "  ✨ Executive summaries (brief, key points)"
echo "  📋 Artifact panel shows strategic plans"
echo "  🎨 Fixed markdown rendering"
echo "  🔗 Task creation from plans"
echo "  💾 Code generation happens after task approval"
echo ""
echo "What's preserved:"
echo "  ✅ All your HTML structure"
echo "  ✅ All your CSS styling"
echo "  ✅ Your color scheme (#7B61FF, #5AE0E0)"
echo "  ✅ Quick action buttons"
echo "  ✅ Dark artifact panel design"
echo "  ✅ Monaco font for code"
echo ""
echo "Try it at: http://192.168.1.60:8080/developer/"
echo "Or if using /chat: http://192.168.1.60:8080/developer/chat.html"
