#!/bin/bash
# RESTORE ORIGINAL DEVELOPER DASHBOARD FROM PROJECT KNOWLEDGE

echo "🔄 RESTORING ORIGINAL DEVELOPER DASHBOARD"
echo "========================================"

cd /home/pi/zoe

# Create directories if they don't exist
mkdir -p services/zoe-ui/dist/developer/js
mkdir -p services/zoe-ui/dist/developer/css

# ============================================================================
# RESTORE ORIGINAL HTML
# ============================================================================
cat > services/zoe-ui/dist/developer/index.html << 'HTML_EOF'
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Zoe Developer Dashboard</title>
    <link rel="stylesheet" href="css/developer.css">
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🧠 Developer Dashboard</h1>
            <div class="status-bar">
                <span id="claudeStatus" class="status-indicator">
                    <div class="status-dot"></div>
                    <span>Claude</span>
                </span>
                <span id="currentTime"></span>
            </div>
        </div>

        <div class="main-content">
            <div class="sidebar">
                <div class="status-panel">
                    <h3>📊 System Status</h3>
                    <div class="system-status" id="systemStatus">
                        <div class="status-item">
                            <div class="status-icon">✅</div>
                            <div>CORE</div>
                        </div>
                        <div class="status-item">
                            <div class="status-icon">✅</div>
                            <div>UI</div>
                        </div>
                        <div class="status-item">
                            <div class="status-icon">✅</div>
                            <div>AI</div>
                        </div>
                        <div class="status-item">
                            <div class="status-icon">✅</div>
                            <div>CACHE</div>
                        </div>
                        <div class="status-item">
                            <div class="status-icon">⚠️</div>
                            <div>STT</div>
                        </div>
                        <div class="status-item">
                            <div class="status-icon">⚠️</div>
                            <div>TTS</div>
                        </div>
                    </div>
                </div>

                <div class="quick-actions">
                    <h3>⚡ Quick Actions</h3>
                    <button onclick="quickAction('systemCheck')">🔍 Check</button>
                    <button onclick="quickAction('fixIssues')">🔧 Fix</button>
                    <button onclick="quickAction('backup')">💾 Backup</button>
                </div>

                <div class="recent-tasks">
                    <h3>📋 Recent Tasks</h3>
                    <div id="recentTasks">
                        <div>✅ System initialized</div>
                        <div>✅ UI loaded</div>
                    </div>
                </div>
            </div>

            <div class="chat-panel">
                <h3>💬 Claude Development Assistant</h3>
                <div id="chatMessages" class="messages">
                    <div class="message claude">
                        <span class="message-icon">🧠</span>
                        <div class="message-content">
                            Hi! I'm Claude, your development assistant. I can help you fix issues, manage your Zoe system, and provide step-by-step terminal scripts. What would you like to work on today?
                        </div>
                    </div>
                </div>
                <div class="input-area">
                    <input type="text" id="messageInput" placeholder="Ask Claude..." onkeydown="handleInputKeyDown(event)" />
                    <button onclick="sendMessage()" class="send-button">Send</button>
                </div>
            </div>
        </div>
    </div>

    <script src="js/developer.js"></script>
</body>
</html>
HTML_EOF

# ============================================================================
# RESTORE ORIGINAL JAVASCRIPT (WORKING VERSION)
# ============================================================================
cat > services/zoe-ui/dist/developer/js/developer.js << 'JS_EOF'
// Zoe Developer Dashboard JavaScript
const API_BASE = 'http://localhost:8000/api';
let chatHistory = [];
let systemStatus = {};
let isProcessing = false;

// Initialize on load
document.addEventListener('DOMContentLoaded', () => {
    console.log('🚀 Developer Dashboard Initializing...');
    initializeDashboard();
    setInterval(updateTime, 1000);
    setInterval(checkSystemStatus, 30000);
});

async function initializeDashboard() {
    updateTime();
    await checkClaudeStatus();
    await checkSystemStatus();
    await loadRecentTasks();
    
    // Focus on input
    document.getElementById('messageInput').focus();
}

function updateTime() {
    const now = new Date();
    document.getElementById('currentTime').textContent = 
        now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

async function checkClaudeStatus() {
    try {
        const response = await fetch(`${API_BASE}/developer/status`);
        const statusEl = document.getElementById('claudeStatus');
        
        if (response.ok) {
            statusEl.className = 'status-indicator';
            statusEl.innerHTML = '<div class="status-dot"></div><span>Claude Online</span>';
        } else {
            throw new Error('Claude offline');
        }
    } catch (error) {
        const statusEl = document.getElementById('claudeStatus');
        statusEl.className = 'status-indicator offline';
        statusEl.innerHTML = '<div class="status-dot"></div><span>Claude Offline</span>';
    }
}

async function checkSystemStatus() {
    try {
        const response = await fetch(`${API_BASE}/developer/system/status`);
        if (response.ok) {
            const data = await response.json();
            updateSystemStatusDisplay(data);
        }
    } catch (error) {
        console.error('Failed to check system status:', error);
    }
}

function updateSystemStatusDisplay(status) {
    const container = document.getElementById('systemStatus');
    const services = [
        { name: 'CORE', key: 'core', icon: '✅' },
        { name: 'UI', key: 'ui', icon: '✅' },
        { name: 'AI', key: 'ollama', icon: '✅' },
        { name: 'CACHE', key: 'redis', icon: '✅' },
        { name: 'STT', key: 'whisper', icon: '⚠️' },
        { name: 'TTS', key: 'tts', icon: '⚠️' }
    ];
    
    container.innerHTML = services.map(service => {
        const state = status[service.key] || 'unknown';
        const icon = state === 'healthy' ? '✅' : state === 'warning' ? '⚠️' : '❌';
        
        return `
            <div class="status-item" onclick="checkService('${service.key}')">
                <div class="status-icon">${icon}</div>
                <div>${service.name}</div>
            </div>
        `;
    }).join('');
}

async function sendMessage() {
    const input = document.getElementById('messageInput');
    const message = input.value.trim();
    
    if (!message || isProcessing) return;
    
    // Add user message
    addMessage(message, 'user');
    input.value = '';
    isProcessing = true;
    
    try {
        // Send to developer chat endpoint
        const response = await fetch(`${API_BASE}/developer/chat`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message: message })
        });
        
        if (response.ok) {
            const data = await response.json();
            addMessage(data.response || "I'm processing that request...", 'claude');
        } else {
            throw new Error('Chat request failed');
        }
    } catch (error) {
        console.error('Chat error:', error);
        addMessage('Error: Could not connect to backend. Check if zoe-core is running.', 'claude');
    } finally {
        isProcessing = false;
    }
}

function addMessage(content, sender) {
    const container = document.getElementById('chatMessages');
    const messageDiv = document.createElement('div');
    messageDiv.className = `message ${sender}`;
    
    const icon = sender === 'claude' ? '🧠' : '👤';
    
    // Allow HTML in Claude's responses for formatting
    messageDiv.innerHTML = `
        <span class="message-icon">${icon}</span>
        <div class="message-content">${content}</div>
    `;
    
    container.appendChild(messageDiv);
    container.scrollTop = container.scrollHeight;
    
    // Store in history
    chatHistory.push({ sender, content, timestamp: new Date() });
    if (chatHistory.length > 50) chatHistory.shift();
}

function handleInputKeyDown(event) {
    if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        sendMessage();
    }
}

async function quickAction(action) {
    const actions = {
        systemCheck: "Run system health check",
        fixIssues: "Fix any system issues",
        backup: "Create a backup"
    };
    
    const message = actions[action];
    if (message) {
        document.getElementById('messageInput').value = message;
        sendMessage();
    }
}

async function checkService(service) {
    const message = `Check ${service} service status`;
    document.getElementById('messageInput').value = message;
    sendMessage();
}

async function loadRecentTasks() {
    const container = document.getElementById('recentTasks');
    try {
        const response = await fetch(`${API_BASE}/developer/tasks/recent`);
        if (response.ok) {
            const tasks = await response.json();
            container.innerHTML = tasks.slice(0, 5).map(task => 
                `<div>✅ ${task.title}</div>`
            ).join('');
        }
    } catch (error) {
        console.log('Could not load tasks');
    }
}
JS_EOF

# ============================================================================
# RESTORE ORIGINAL CSS (WITH GLASS-MORPHIC DESIGN)
# ============================================================================
cat > services/zoe-ui/dist/developer/css/developer.css << 'CSS_EOF'
/* Developer Dashboard - Glass Morphic Design */
* {
    margin: 0;
    padding: 0;
    box-sizing: border-box;
}

body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
    min-height: 100vh;
    color: #1f2937;
}

.container {
    max-width: 1400px;
    margin: 0 auto;
    padding: 20px;
}

.header {
    background: rgba(255, 255, 255, 0.95);
    backdrop-filter: blur(10px);
    border-radius: 16px;
    padding: 25px;
    margin-bottom: 20px;
    box-shadow: 0 10px 30px rgba(0, 0, 0, 0.1);
}

.header h1 {
    color: #1e40af;
    font-size: 28px;
    display: inline-block;
}

.status-bar {
    float: right;
    display: flex;
    gap: 20px;
    align-items: center;
}

.status-indicator {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 6px 12px;
    background: #f0fdf4;
    border-radius: 20px;
    color: #16a34a;
}

.status-indicator.offline {
    background: #fef2f2;
    color: #dc2626;
}

.status-dot {
    width: 8px;
    height: 8px;
    background: currentColor;
    border-radius: 50%;
    animation: pulse 2s infinite;
}

@keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.5; }
}

.main-content {
    display: grid;
    grid-template-columns: 350px 1fr;
    gap: 20px;
}

.sidebar {
    display: flex;
    flex-direction: column;
    gap: 20px;
}

.status-panel,
.quick-actions,
.recent-tasks,
.chat-panel {
    background: rgba(255, 255, 255, 0.95);
    backdrop-filter: blur(10px);
    border-radius: 16px;
    padding: 20px;
    box-shadow: 0 10px 30px rgba(0, 0, 0, 0.1);
}

.status-panel h3,
.quick-actions h3,
.recent-tasks h3,
.chat-panel h3 {
    color: #1e40af;
    margin-bottom: 15px;
    font-size: 18px;
}

.system-status {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 15px;
}

.status-item {
    text-align: center;
    padding: 15px 10px;
    background: linear-gradient(135deg, #f3f4f6 0%, #e5e7eb 100%);
    border-radius: 12px;
    cursor: pointer;
    transition: transform 0.2s;
}

.status-item:hover {
    transform: translateY(-2px);
}

.status-icon {
    font-size: 28px;
    margin-bottom: 5px;
}

.quick-actions button {
    width: 100%;
    padding: 12px;
    margin: 5px 0;
    background: linear-gradient(135deg, #3b82f6 0%, #2563eb 100%);
    color: white;
    border: none;
    border-radius: 10px;
    cursor: pointer;
    font-size: 15px;
    font-weight: 500;
    transition: transform 0.2s;
}

.quick-actions button:hover {
    transform: translateY(-2px);
}

.messages {
    height: 450px;
    overflow-y: auto;
    padding: 15px;
    margin-bottom: 20px;
    background: #f9fafb;
    border-radius: 12px;
    border: 1px solid #e5e7eb;
}

.message {
    margin: 12px 0;
    display: flex;
    align-items: flex-start;
}

.message-icon {
    font-size: 24px;
    margin-right: 10px;
}

.message-content {
    background: white;
    border: 1px solid #e5e7eb;
    padding: 12px 16px;
    border-radius: 12px;
    max-width: 85%;
    line-height: 1.6;
}

.message.claude .message-content {
    border-left: 3px solid #3b82f6;
}

.message.user {
    justify-content: flex-end;
}

.message.user .message-content {
    background: #f0f9ff;
    border-right: 3px solid #10b981;
}

.message.user .message-icon {
    order: 2;
    margin-left: 10px;
    margin-right: 0;
}

.input-area {
    display: flex;
    gap: 10px;
}

#messageInput {
    flex: 1;
    padding: 14px;
    border: 2px solid #e5e7eb;
    border-radius: 12px;
    font-size: 15px;
}

#messageInput:focus {
    outline: none;
    border-color: #3b82f6;
}

.send-button {
    padding: 14px 28px;
    background: linear-gradient(135deg, #3b82f6 0%, #2563eb 100%);
    color: white;
    border: none;
    border-radius: 12px;
    cursor: pointer;
    font-size: 15px;
    font-weight: 500;
}

.send-button:hover {
    transform: translateY(-2px);
}

/* Recent tasks styling */
#recentTasks div {
    padding: 8px 0;
    border-bottom: 1px solid #e5e7eb;
}

#recentTasks div:last-child {
    border-bottom: none;
}
CSS_EOF

echo "✅ Original Developer Dashboard Restored!"
echo ""
echo "Now restart the backend to make sure everything works:"
echo "  docker restart zoe-core"
echo ""
echo "Then access the dashboard at:"
echo "  http://192.168.1.60:8080/developer/"
echo ""
echo "The dashboard is now restored to its original state with:"
echo "• Glass-morphic design"
echo "• Purple gradient background"
echo "• Clean white message bubbles"
echo "• Working chat functionality"
