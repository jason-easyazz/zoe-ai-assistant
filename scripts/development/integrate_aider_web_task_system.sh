#!/bin/bash
# INTEGRATE_AIDER_WEB_TASK_SYSTEM.sh
# Location: scripts/development/integrate_aider_web_task_system.sh
# Purpose: Create web-based Aider integration with task management

set -e

echo "🤖 INTEGRATING AIDER WITH WEB GUI & TASK SYSTEM"
echo "================================================="
echo ""
echo "This will create:"
echo "  1. Aider as a backend service"
echo "  2. Web chat interface in developer dashboard"
echo "  3. Task system integration"
echo "  4. Code execution capabilities"
echo "  5. Git integration"
echo ""
echo "Press Enter to continue..."
read

cd /home/pi/zoe

# ============================================================================
# STEP 1: INSTALL AIDER BACKEND
# ============================================================================

echo -e "\n📦 Step 1: Installing Aider backend..."

# First install basic Aider
if [ ! -d "tools/aider-env" ]; then
    echo "Installing Aider..."
    mkdir -p tools
    cd tools
    python3 -m venv aider-env
    source aider-env/bin/activate
    pip install --upgrade pip
    pip install aider-chat flask flask-cors
    cd ..
else
    echo "Aider already installed"
fi

# ============================================================================
# STEP 2: CREATE AIDER SERVICE WRAPPER
# ============================================================================

echo -e "\n🔧 Step 2: Creating Aider service wrapper..."

cat > services/zoe-core/aider_service.py << 'PYTHON_EOF'
"""
Aider Service Wrapper - Integrates Aider with Zoe's task system
Provides web API for Aider interactions
"""
import os
import sys
import json
import sqlite3
import subprocess
import asyncio
import tempfile
from datetime import datetime
from typing import Dict, List, Optional
from pathlib import Path
import threading
import queue

# Add Aider to path
sys.path.append('/home/pi/zoe/tools/aider-env/lib/python3.11/site-packages')

class AiderWebService:
    def __init__(self):
        self.sessions = {}
        self.task_db = "/app/data/tasks.db"
        self.conversation_db = "/app/data/aider_conversations.db"
        self.setup_database()
        
    def setup_database(self):
        """Create tables for Aider conversations and task links"""
        conn = sqlite3.connect(self.conversation_db)
        c = conn.cursor()
        
        c.execute('''CREATE TABLE IF NOT EXISTS aider_sessions (
            session_id TEXT PRIMARY KEY,
            task_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_active TIMESTAMP,
            status TEXT DEFAULT 'active',
            files_context TEXT,
            conversation_history TEXT
        )''')
        
        c.execute('''CREATE TABLE IF NOT EXISTS aider_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            role TEXT,
            content TEXT,
            code_changes TEXT,
            files_modified TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (session_id) REFERENCES aider_sessions(session_id)
        )''')
        
        conn.commit()
        conn.close()
    
    async def create_session(self, task_id: Optional[str] = None, files: List[str] = None) -> str:
        """Create new Aider session, optionally linked to a task"""
        session_id = f"aider_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        
        # Determine which model to use
        model = self._select_model()
        
        # Create session record
        conn = sqlite3.connect(self.conversation_db)
        c = conn.cursor()
        c.execute("""
            INSERT INTO aider_sessions (session_id, task_id, files_context, status)
            VALUES (?, ?, ?, 'active')
        """, (session_id, task_id, json.dumps(files or [])))
        conn.commit()
        conn.close()
        
        # Initialize Aider process
        self.sessions[session_id] = {
            "task_id": task_id,
            "files": files or [],
            "model": model,
            "process": None,
            "queue": queue.Queue()
        }
        
        return session_id
    
    def _select_model(self) -> str:
        """Select best available model"""
        # Check for API keys
        if os.getenv("ANTHROPIC_API_KEY"):
            return "claude-3-opus-20240229"
        elif os.getenv("OPENAI_API_KEY"):
            return "gpt-4-turbo-preview"
        else:
            # Use local Ollama
            return "ollama/llama3.2:3b"
    
    async def send_message(self, session_id: str, message: str, auto_execute: bool = False) -> Dict:
        """Send message to Aider and get response"""
        if session_id not in self.sessions:
            return {"error": "Session not found"}
        
        session = self.sessions[session_id]
        
        # Prepare Aider command
        cmd = self._build_aider_command(session, message, auto_execute)
        
        # Execute Aider
        result = await self._run_aider_command(cmd, session)
        
        # Parse response
        response = self._parse_aider_response(result)
        
        # Save to database
        self._save_message(session_id, "user", message)
        self._save_message(session_id, "assistant", response.get("content", ""), 
                          response.get("code_changes"))
        
        # If linked to task, update task progress
        if session["task_id"]:
            self._update_task_progress(session["task_id"], response)
        
        return response
    
    def _build_aider_command(self, session: Dict, message: str, auto_execute: bool) -> List[str]:
        """Build Aider command with appropriate flags"""
        cmd = [
            "/home/pi/zoe/tools/aider-env/bin/aider",
            "--model", session["model"],
            "--yes-always" if auto_execute else "--no-auto-commits",
        ]
        
        # Add files to context
        for file in session["files"]:
            cmd.extend(["--file", file])
        
        # Add message as input
        cmd.extend(["--message", message])
        
        return cmd
    
    async def _run_aider_command(self, cmd: List[str], session: Dict) -> str:
        """Execute Aider command and capture output"""
        try:
            # Run in Zoe directory for git context
            result = subprocess.run(
                cmd,
                cwd="/home/pi/zoe",
                capture_output=True,
                text=True,
                timeout=60
            )
            
            return result.stdout + result.stderr
            
        except subprocess.TimeoutExpired:
            return "Aider command timed out after 60 seconds"
        except Exception as e:
            return f"Error running Aider: {str(e)}"
    
    def _parse_aider_response(self, output: str) -> Dict:
        """Parse Aider output to extract code changes and explanations"""
        response = {
            "content": output,
            "code_changes": [],
            "files_modified": [],
            "git_commit": None
        }
        
        # Extract code blocks
        import re
        code_blocks = re.findall(r'```[\w]*\n(.*?)\n```', output, re.DOTALL)
        if code_blocks:
            response["code_changes"] = code_blocks
        
        # Extract modified files
        file_pattern = r'Modified: (.*?\.[\w]+)'
        files = re.findall(file_pattern, output)
        response["files_modified"] = files
        
        # Check for git commit
        commit_pattern = r'Commit: ([a-f0-9]{7,})'
        commit = re.search(commit_pattern, output)
        if commit:
            response["git_commit"] = commit.group(1)
        
        return response
    
    def _save_message(self, session_id: str, role: str, content: str, 
                     code_changes: Optional[List] = None):
        """Save message to conversation history"""
        conn = sqlite3.connect(self.conversation_db)
        c = conn.cursor()
        
        c.execute("""
            INSERT INTO aider_messages (session_id, role, content, code_changes)
            VALUES (?, ?, ?, ?)
        """, (session_id, role, content, 
              json.dumps(code_changes) if code_changes else None))
        
        # Update session last active
        c.execute("""
            UPDATE aider_sessions 
            SET last_active = CURRENT_TIMESTAMP 
            WHERE session_id = ?
        """, (session_id,))
        
        conn.commit()
        conn.close()
    
    def _update_task_progress(self, task_id: str, response: Dict):
        """Update linked task with Aider progress"""
        conn = sqlite3.connect(self.task_db)
        c = conn.cursor()
        
        # Add to task conversation
        c.execute("""
            INSERT INTO task_conversations (task_id, role, message)
            VALUES (?, 'aider', ?)
        """, (task_id, json.dumps(response)))
        
        # Update task status if code was generated
        if response.get("code_changes"):
            c.execute("""
                UPDATE tasks 
                SET code_generated = ?, 
                    implementation_path = ?
                WHERE task_id = ?
            """, (json.dumps(response["code_changes"]), 
                  json.dumps(response["files_modified"]),
                  task_id))
        
        conn.commit()
        conn.close()
    
    async def get_session_history(self, session_id: str) -> List[Dict]:
        """Get conversation history for a session"""
        conn = sqlite3.connect(self.conversation_db)
        c = conn.cursor()
        
        c.execute("""
            SELECT role, content, code_changes, timestamp
            FROM aider_messages
            WHERE session_id = ?
            ORDER BY timestamp
        """, (session_id,))
        
        messages = []
        for row in c.fetchall():
            messages.append({
                "role": row[0],
                "content": row[1],
                "code_changes": json.loads(row[2]) if row[2] else None,
                "timestamp": row[3]
            })
        
        conn.close()
        return messages
    
    async def link_to_task(self, session_id: str, task_id: str):
        """Link an Aider session to a task"""
        conn = sqlite3.connect(self.conversation_db)
        c = conn.cursor()
        c.execute("""
            UPDATE aider_sessions 
            SET task_id = ? 
            WHERE session_id = ?
        """, (task_id, session_id))
        conn.commit()
        conn.close()
        
        if session_id in self.sessions:
            self.sessions[session_id]["task_id"] = task_id

# Initialize service
aider_service = AiderWebService()
PYTHON_EOF

# ============================================================================
# STEP 3: ADD AIDER API ENDPOINTS
# ============================================================================

echo -e "\n📝 Step 3: Adding Aider API endpoints..."

cat > services/zoe-core/routers/aider.py << 'PYTHON_EOF'
"""
Aider Router - Web API for Aider interactions
"""
from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import Optional, List, Dict
import sys
import os

sys.path.append('/app')
from aider_service import aider_service

router = APIRouter(prefix="/api/aider", tags=["aider"])

class AiderSessionCreate(BaseModel):
    task_id: Optional[str] = None
    files: Optional[List[str]] = []
    
class AiderMessage(BaseModel):
    session_id: str
    message: str
    auto_execute: bool = False

class TaskLink(BaseModel):
    session_id: str
    task_id: str

@router.post("/session")
async def create_aider_session(request: AiderSessionCreate):
    """Create new Aider coding session"""
    session_id = await aider_service.create_session(
        task_id=request.task_id,
        files=request.files
    )
    
    return {
        "session_id": session_id,
        "status": "created",
        "model": aider_service.sessions[session_id]["model"]
    }

@router.post("/chat")
async def chat_with_aider(request: AiderMessage):
    """Send message to Aider and get response"""
    response = await aider_service.send_message(
        session_id=request.session_id,
        message=request.message,
        auto_execute=request.auto_execute
    )
    
    return response

@router.get("/session/{session_id}/history")
async def get_session_history(session_id: str):
    """Get conversation history for an Aider session"""
    history = await aider_service.get_session_history(session_id)
    return {"session_id": session_id, "messages": history}

@router.post("/link-task")
async def link_session_to_task(request: TaskLink):
    """Link an Aider session to a task"""
    await aider_service.link_to_task(request.session_id, request.task_id)
    return {"status": "linked", "session_id": request.session_id, "task_id": request.task_id}

@router.get("/sessions")
async def list_aider_sessions():
    """List all Aider sessions"""
    import sqlite3
    conn = sqlite3.connect("/app/data/aider_conversations.db")
    c = conn.cursor()
    
    c.execute("""
        SELECT session_id, task_id, created_at, last_active, status
        FROM aider_sessions
        ORDER BY last_active DESC
        LIMIT 20
    """)
    
    sessions = []
    for row in c.fetchall():
        sessions.append({
            "session_id": row[0],
            "task_id": row[1],
            "created_at": row[2],
            "last_active": row[3],
            "status": row[4]
        })
    
    conn.close()
    return {"sessions": sessions}
PYTHON_EOF

# ============================================================================
# STEP 4: UPDATE DEVELOPER DASHBOARD HTML
# ============================================================================

echo -e "\n🎨 Step 4: Updating developer dashboard with Aider chat..."

cat > services/zoe-ui/dist/developer/aider.html << 'HTML_EOF'
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Zoe AI - Aider Code Assistant</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        body {
            font-family: 'SF Pro Display', -apple-system, system-ui, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            display: flex;
            color: white;
        }
        
        /* Sidebar */
        .sidebar {
            width: 300px;
            background: rgba(255, 255, 255, 0.1);
            backdrop-filter: blur(20px);
            border-right: 1px solid rgba(255, 255, 255, 0.2);
            padding: 20px;
            overflow-y: auto;
        }
        
        .sidebar h2 {
            margin-bottom: 20px;
            font-size: 1.2rem;
        }
        
        /* Main Chat Area */
        .main-container {
            flex: 1;
            display: flex;
            flex-direction: column;
            padding: 20px;
        }
        
        .header {
            background: rgba(255, 255, 255, 0.1);
            backdrop-filter: blur(20px);
            border-radius: 16px;
            padding: 20px;
            margin-bottom: 20px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        
        .chat-container {
            flex: 1;
            background: rgba(255, 255, 255, 0.1);
            backdrop-filter: blur(20px);
            border-radius: 16px;
            padding: 20px;
            overflow-y: auto;
            margin-bottom: 20px;
        }
        
        .message {
            margin-bottom: 20px;
            padding: 15px;
            border-radius: 12px;
            animation: slideIn 0.3s ease;
        }
        
        .user-message {
            background: rgba(255, 255, 255, 0.2);
            margin-left: 20%;
        }
        
        .aider-message {
            background: rgba(0, 0, 0, 0.2);
            margin-right: 20%;
        }
        
        .code-block {
            background: rgba(0, 0, 0, 0.3);
            border-radius: 8px;
            padding: 10px;
            margin: 10px 0;
            font-family: 'Monaco', monospace;
            font-size: 0.9rem;
            overflow-x: auto;
        }
        
        .input-area {
            background: rgba(255, 255, 255, 0.1);
            backdrop-filter: blur(20px);
            border-radius: 16px;
            padding: 20px;
            display: flex;
            gap: 10px;
        }
        
        .message-input {
            flex: 1;
            background: rgba(255, 255, 255, 0.2);
            border: 1px solid rgba(255, 255, 255, 0.3);
            border-radius: 8px;
            padding: 12px;
            color: white;
            font-size: 1rem;
        }
        
        .message-input::placeholder {
            color: rgba(255, 255, 255, 0.6);
        }
        
        .btn {
            background: rgba(255, 255, 255, 0.2);
            border: 1px solid rgba(255, 255, 255, 0.3);
            border-radius: 8px;
            padding: 12px 24px;
            color: white;
            cursor: pointer;
            transition: all 0.3s;
            font-weight: 500;
        }
        
        .btn:hover {
            background: rgba(255, 255, 255, 0.3);
            transform: translateY(-2px);
        }
        
        .btn-primary {
            background: linear-gradient(135deg, #667eea, #764ba2);
        }
        
        /* Task List */
        .task-item {
            background: rgba(255, 255, 255, 0.1);
            border-radius: 8px;
            padding: 10px;
            margin-bottom: 10px;
            cursor: pointer;
            transition: all 0.3s;
        }
        
        .task-item:hover {
            background: rgba(255, 255, 255, 0.2);
        }
        
        /* File Context */
        .file-list {
            background: rgba(0, 0, 0, 0.2);
            border-radius: 8px;
            padding: 10px;
            margin-top: 10px;
        }
        
        .file-item {
            padding: 5px;
            margin: 2px 0;
            font-size: 0.9rem;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        
        .remove-file {
            cursor: pointer;
            color: rgba(255, 100, 100, 0.8);
        }
        
        /* Status Indicators */
        .status {
            display: inline-block;
            padding: 4px 8px;
            border-radius: 4px;
            font-size: 0.8rem;
            margin-left: 10px;
        }
        
        .status-active {
            background: rgba(0, 255, 0, 0.3);
        }
        
        .status-thinking {
            background: rgba(255, 255, 0, 0.3);
            animation: pulse 1s infinite;
        }
        
        @keyframes slideIn {
            from {
                opacity: 0;
                transform: translateY(10px);
            }
            to {
                opacity: 1;
                transform: translateY(0);
            }
        }
        
        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.5; }
        }
        
        /* Quick Actions */
        .quick-actions {
            display: flex;
            gap: 10px;
            margin-top: 20px;
            flex-wrap: wrap;
        }
        
        .quick-action {
            background: rgba(255, 255, 255, 0.1);
            border: 1px solid rgba(255, 255, 255, 0.2);
            border-radius: 6px;
            padding: 8px 12px;
            font-size: 0.85rem;
            cursor: pointer;
            transition: all 0.3s;
        }
        
        .quick-action:hover {
            background: rgba(255, 255, 255, 0.2);
        }
    </style>
</head>
<body>
    <!-- Sidebar -->
    <div class="sidebar">
        <h2>🤖 Aider Sessions</h2>
        
        <button class="btn btn-primary" onclick="createNewSession()" style="width: 100%; margin-bottom: 20px;">
            + New Session
        </button>
        
        <h3 style="margin-bottom: 10px;">Recent Sessions</h3>
        <div id="sessionList">
            <!-- Sessions will load here -->
        </div>
        
        <h3 style="margin-top: 20px; margin-bottom: 10px;">Linked Tasks</h3>
        <div id="taskList">
            <!-- Tasks will load here -->
        </div>
        
        <h3 style="margin-top: 20px; margin-bottom: 10px;">Files in Context</h3>
        <div class="file-list" id="fileList">
            <div class="file-item">No files selected</div>
        </div>
        
        <button class="btn" onclick="addFiles()" style="width: 100%; margin-top: 10px;">
            + Add Files
        </button>
    </div>
    
    <!-- Main Chat Area -->
    <div class="main-container">
        <!-- Header -->
        <div class="header">
            <div>
                <h1>Aider Code Assistant</h1>
                <span id="sessionInfo">No session active</span>
                <span id="modelInfo" class="status status-active"></span>
            </div>
            <div>
                <button class="btn" onclick="window.location.href='/developer/'">
                    ← Back to Dashboard
                </button>
            </div>
        </div>
        
        <!-- Quick Actions -->
        <div class="quick-actions">
            <div class="quick-action" onclick="sendQuickMessage('Show me the project structure')">
                📁 Project Structure
            </div>
            <div class="quick-action" onclick="sendQuickMessage('What files have been modified recently?')">
                🔍 Recent Changes
            </div>
            <div class="quick-action" onclick="sendQuickMessage('Run the tests')">
                🧪 Run Tests
            </div>
            <div class="quick-action" onclick="sendQuickMessage('Create a new API endpoint')">
                🔧 New Endpoint
            </div>
            <div class="quick-action" onclick="sendQuickMessage('Fix the last error')">
                🐛 Fix Error
            </div>
            <div class="quick-action" onclick="sendQuickMessage('Refactor this code for better performance')">
                ⚡ Refactor
            </div>
        </div>
        
        <!-- Chat Container -->
        <div class="chat-container" id="chatContainer">
            <div class="message aider-message">
                👋 Hi! I'm Aider, your AI coding assistant. I can:
                <ul style="margin-top: 10px; margin-left: 20px;">
                    <li>Edit your code directly</li>
                    <li>Create new features</li>
                    <li>Fix bugs</li>
                    <li>Refactor existing code</li>
                    <li>Write tests</li>
                    <li>Create documentation</li>
                </ul>
                <br>
                Start by telling me what you'd like to work on, or add some files to the context!
            </div>
        </div>
        
        <!-- Input Area -->
        <div class="input-area">
            <input 
                type="text" 
                class="message-input" 
                id="messageInput" 
                placeholder="Ask Aider to write code, fix bugs, or create features..."
                onkeypress="if(event.key === 'Enter') sendMessage()"
            >
            <label style="display: flex; align-items: center; gap: 5px;">
                <input type="checkbox" id="autoExecute">
                Auto-execute
            </label>
            <button class="btn btn-primary" onclick="sendMessage()">
                Send
            </button>
        </div>
    </div>
    
    <script>
        let currentSession = null;
        let currentFiles = [];
        
        async function createNewSession() {
            const response = await fetch('/api/aider/session', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    files: currentFiles
                })
            });
            
            const data = await response.json();
            currentSession = data.session_id;
            
            document.getElementById('sessionInfo').textContent = `Session: ${currentSession}`;
            document.getElementById('modelInfo').textContent = `Model: ${data.model}`;
            
            loadSessions();
            clearChat();
        }
        
        async function sendMessage() {
            const input = document.getElementById('messageInput');
            const message = input.value.trim();
            if (!message) return;
            
            if (!currentSession) {
                await createNewSession();
            }
            
            // Add user message to chat
            addMessage(message, 'user');
            input.value = '';
            
            // Show thinking status
            const thinkingMsg = addMessage('Thinking...', 'aider');
            thinkingMsg.classList.add('status-thinking');
            
            // Send to Aider
            const autoExecute = document.getElementById('autoExecute').checked;
            
            const response = await fetch('/api/aider/chat', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    session_id: currentSession,
                    message: message,
                    auto_execute: autoExecute
                })
            });
            
            const data = await response.json();
            
            // Remove thinking message
            thinkingMsg.remove();
            
            // Add Aider response
            addAiderResponse(data);
        }
        
        function addMessage(text, sender) {
            const messageDiv = document.createElement('div');
            messageDiv.className = `message ${sender}-message`;
            messageDiv.textContent = text;
            document.getElementById('chatContainer').appendChild(messageDiv);
            messageDiv.scrollIntoView({behavior: 'smooth'});
            return messageDiv;
        }
        
        function addAiderResponse(data) {
            const messageDiv = document.createElement('div');
            messageDiv.className = 'message aider-message';
            
            // Add main content
            const contentDiv = document.createElement('div');
            contentDiv.textContent = data.content || 'Done!';
            messageDiv.appendChild(contentDiv);
            
            // Add code changes if any
            if (data.code_changes && data.code_changes.length > 0) {
                data.code_changes.forEach(code => {
                    const codeBlock = document.createElement('pre');
                    codeBlock.className = 'code-block';
                    codeBlock.textContent = code;
                    messageDiv.appendChild(codeBlock);
                });
            }
            
            // Add modified files
            if (data.files_modified && data.files_modified.length > 0) {
                const filesDiv = document.createElement('div');
                filesDiv.style.marginTop = '10px';
                filesDiv.innerHTML = '<strong>Modified files:</strong> ' + 
                    data.files_modified.join(', ');
                messageDiv.appendChild(filesDiv);
            }
            
            // Add git commit if any
            if (data.git_commit) {
                const commitDiv = document.createElement('div');
                commitDiv.style.marginTop = '10px';
                commitDiv.innerHTML = `<strong>Git commit:</strong> ${data.git_commit}`;
                messageDiv.appendChild(commitDiv);
            }
            
            document.getElementById('chatContainer').appendChild(messageDiv);
            messageDiv.scrollIntoView({behavior: 'smooth'});
        }
        
        function sendQuickMessage(message) {
            document.getElementById('messageInput').value = message;
            sendMessage();
        }
        
        async function loadSessions() {
            const response = await fetch('/api/aider/sessions');
            const data = await response.json();
            
            const sessionList = document.getElementById('sessionList');
            sessionList.innerHTML = '';
            
            data.sessions.forEach(session => {
                const item = document.createElement('div');
                item.className = 'task-item';
                item.innerHTML = `
                    <div>${session.session_id}</div>
                    <div style="font-size: 0.8rem; opacity: 0.7;">
                        ${new Date(session.last_active).toLocaleString()}
                    </div>
                `;
                item.onclick = () => loadSession(session.session_id);
                sessionList.appendChild(item);
            });
        }
        
        async function loadSession(sessionId) {
            currentSession = sessionId;
            document.getElementById('sessionInfo').textContent = `Session: ${sessionId}`;
            
            // Load history
            const response = await fetch(`/api/aider/session/${sessionId}/history`);
            const data = await response.json();
            
            // Clear chat and load history
            clearChat();
            data.messages.forEach(msg => {
                if (msg.role === 'user') {
                    addMessage(msg.content, 'user');
                } else {
                    addAiderResponse(msg);
                }
            });
        }
        
        function clearChat() {
            const container = document.getElementById('chatContainer');
            container.innerHTML = '';
        }
        
        function addFiles() {
            const files = prompt('Enter file paths (comma-separated):\nExample: services/zoe-core/main.py, services/zoe-ui/dist/index.html');
            if (files) {
                currentFiles = files.split(',').map(f => f.trim());
                updateFileList();
            }
        }
        
        function updateFileList() {
            const fileList = document.getElementById('fileList');
            if (currentFiles.length === 0) {
                fileList.innerHTML = '<div class="file-item">No files selected</div>';
            } else {
                fileList.innerHTML = currentFiles.map((file, i) => `
                    <div class="file-item">
                        <span>${file}</span>
                        <span class="remove-file" onclick="removeFile(${i})">×</span>
                    </div>
                `).join('');
            }
        }
        
        function removeFile(index) {
            currentFiles.splice(index, 1);
            updateFileList();
        }
        
        // Load sessions on startup
        loadSessions();
    </script>
</body>
</html>
HTML_EOF

# ============================================================================
# STEP 5: UPDATE MAIN.PY TO INCLUDE AIDER ROUTER
# ============================================================================

echo -e "\n📝 Step 5: Adding Aider router to main.py..."

# Check if aider router is already included
if ! grep -q "from routers import aider" services/zoe-core/main.py; then
    sed -i '/from routers import/s/$/, aider/' services/zoe-core/main.py
    sed -i '/app.include_router(developer.router)/a app.include_router(aider.router)' services/zoe-core/main.py
fi

# ============================================================================
# STEP 6: CREATE SYSTEMD SERVICE (OPTIONAL)
# ============================================================================

echo -e "\n🔧 Step 6: Creating systemd service for Aider (optional)..."

cat > /tmp/zoe-aider.service << 'SERVICE_EOF'
[Unit]
Description=Zoe Aider Service
After=docker.service
Requires=docker.service

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/zoe
Environment="PATH=/home/pi/zoe/tools/aider-env/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
ExecStart=/home/pi/zoe/tools/aider-env/bin/python /home/pi/zoe/services/zoe-core/aider_service.py
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
SERVICE_EOF

echo "Systemd service created at /tmp/zoe-aider.service"
echo "To install: sudo cp /tmp/zoe-aider.service /etc/systemd/system/"
echo "Then: sudo systemctl enable zoe-aider && sudo systemctl start zoe-aider"

# ============================================================================
# STEP 7: CREATE INTEGRATION TEST SCRIPT
# ============================================================================

echo -e "\n🧪 Step 7: Creating integration test script..."

cat > test_aider_integration.sh << 'TEST_EOF'
#!/bin/bash
# Test Aider Web Integration

echo "🧪 TESTING AIDER WEB INTEGRATION"
echo "================================"

# Test 1: Create session
echo -e "\n1. Creating Aider session..."
SESSION=$(curl -s -X POST http://localhost:8000/api/aider/session \
    -H "Content-Type: application/json" \
    -d '{"files": ["services/zoe-core/main.py"]}' | jq -r '.session_id')

if [ ! -z "$SESSION" ]; then
    echo "✅ Session created: $SESSION"
else
    echo "❌ Failed to create session"
    exit 1
fi

# Test 2: Send message
echo -e "\n2. Sending test message..."
RESPONSE=$(curl -s -X POST http://localhost:8000/api/aider/chat \
    -H "Content-Type: application/json" \
    -d "{\"session_id\": \"$SESSION\", \"message\": \"Show me the structure of main.py\"}")

if [ ! -z "$RESPONSE" ]; then
    echo "✅ Got response from Aider"
    echo "$RESPONSE" | jq '.content' | head -20
else
    echo "❌ No response from Aider"
fi

# Test 3: Check task integration
echo -e "\n3. Testing task integration..."
curl -s http://localhost:8000/api/tasks | jq '.tasks[0]' || echo "No tasks yet"

echo -e "\n✅ Integration test complete!"
echo "Access Aider at: http://192.168.1.60:8080/developer/aider.html"
TEST_EOF

chmod +x test_aider_integration.sh

# ============================================================================
# STEP 8: REBUILD AND RESTART
# ============================================================================

echo -e "\n🐳 Step 8: Rebuilding and restarting services..."

docker compose up -d --build zoe-core
sleep 5

# ============================================================================
# COMPLETION
# ============================================================================

echo -e "\n✅ AIDER WEB INTEGRATION COMPLETE!"
echo "===================================="
echo ""
echo "🎉 What's now available:"
echo ""
echo "1. 🌐 Web Interface:"
echo "   http://192.168.1.60:8080/developer/aider.html"
echo ""
echo "2. 🤖 Features:"
echo "   - Chat with Aider through web browser"
echo "   - Link Aider sessions to tasks"
echo "   - Auto-execute code changes"
echo "   - File context management"
echo "   - Session history"
echo "   - Quick action buttons"
echo ""
echo "3. 📋 Task Integration:"
echo "   - Create tasks that Aider can work on"
echo "   - Track code generation per task"
echo "   - Review and approve changes"
echo ""
echo "4. 🔌 API Endpoints:"
echo "   POST /api/aider/session - Create session"
echo "   POST /api/aider/chat - Send message"
echo "   GET  /api/aider/sessions - List sessions"
echo "   POST /api/aider/link-task - Link to task"
echo ""
echo "5. 🧪 Test it now:"
echo "   ./test_aider_integration.sh"
echo ""
echo "📚 Usage:"
echo "1. Open http://192.168.1.60:8080/developer/aider.html"
echo "2. Click 'New Session'"
echo "3. Add files you want to work on"
echo "4. Start chatting with Aider!"
echo ""
echo "💡 Example prompts:"
echo "- 'Add a notes feature to Zoe'"
echo "- 'Fix the timeout issue in the chat endpoint'"
echo "- 'Refactor the AI client for better performance'"
echo "- 'Create unit tests for the task system'"
