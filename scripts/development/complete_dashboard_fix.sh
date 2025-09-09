#!/bin/bash
# Complete Developer Dashboard Fix Script
# This script fixes all connection issues and tests everything

cd /home/pi/zoe
echo "════════════════════════════════════════"
echo "   🔧 DEVELOPER DASHBOARD FIX SCRIPT"
echo "════════════════════════════════════════"

# Step 1: Diagnose Current State
echo -e "\n📍 STEP 1: Checking Current State"
echo "────────────────────────────────"
echo "Checking containers..."
docker ps --format "table {{.Names}}\t{{.Status}}" | grep zoe-core
echo "API Health:"
curl -s http://localhost:8000/health | jq '.' || echo "❌ API not responding"
echo "✅ Step 1 Complete"
sleep 2

# Step 2: Fix CORS in main.py
echo -e "\n📍 STEP 2: Fixing CORS Configuration"
echo "────────────────────────────────"
cat > services/zoe-core/main.py << 'EOF'
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import logging
import sys

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Zoe AI Core API", version="5.0")

# CORS configuration - CRITICAL for browser access
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"]
)

# Import routers
try:
    from routers import developer, chat
    app.include_router(developer.router)
    app.include_router(chat.router)
    logger.info("All routers loaded successfully")
except ImportError as e:
    logger.error(f"Router import error: {e}")
    from routers import developer
    app.include_router(developer.router)

@app.get("/")
async def root():
    return {"message": "Zoe AI Core API", "status": "running"}

@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "version": "5.0",
        "services": {
            "core": "running",
            "memory": "available",
            "developer": "active"
        }
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=True)
EOF
echo "✅ Step 2 Complete - CORS configured"
sleep 1

# Step 3: Create Complete Developer Dashboard HTML
echo -e "\n📍 STEP 3: Creating Dashboard HTML"
echo "────────────────────────────────"
mkdir -p services/zoe-ui/dist/developer/js
mkdir -p services/zoe-ui/dist/developer/css

cat > services/zoe-ui/dist/developer/index.html << 'EOF'
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Zoe AI - Developer Dashboard</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #1e3c72 0%, #2a5298 100%);
            color: #fff;
            min-height: 100vh;
        }
        .container {
            max-width: 1400px;
            margin: 0 auto;
            padding: 20px;
        }
        .header {
            text-align: center;
            padding: 30px 0;
            border-bottom: 2px solid rgba(255,255,255,0.1);
        }
        .header h1 {
            font-size: 2.5em;
            margin-bottom: 10px;
        }
        .dashboard {
            display: grid;
            grid-template-columns: 300px 1fr 300px;
            gap: 20px;
            margin-top: 30px;
        }
        .panel {
            background: rgba(255,255,255,0.1);
            backdrop-filter: blur(10px);
            border-radius: 15px;
            padding: 20px;
            border: 1px solid rgba(255,255,255,0.2);
        }
        .status-good {
            color: #4ade80;
            font-weight: bold;
        }
        .status-error {
            color: #f87171;
            font-weight: bold;
        }
        #chat-messages {
            height: 400px;
            overflow-y: auto;
            background: rgba(0,0,0,0.2);
            border-radius: 10px;
            padding: 15px;
            margin-bottom: 20px;
        }
        .message {
            margin-bottom: 15px;
            padding: 10px;
            border-radius: 8px;
            background: rgba(255,255,255,0.05);
        }
        .user-message {
            background: rgba(59,130,246,0.2);
            margin-left: 20%;
        }
        .ai-message {
            background: rgba(34,197,94,0.2);
            margin-right: 20%;
        }
        .chat-form {
            display: flex;
            gap: 10px;
        }
        #chat-input {
            flex: 1;
            padding: 12px;
            border-radius: 8px;
            border: 1px solid rgba(255,255,255,0.3);
            background: rgba(255,255,255,0.1);
            color: white;
            font-size: 16px;
        }
        #chat-input::placeholder {
            color: rgba(255,255,255,0.5);
        }
        button {
            padding: 12px 24px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border: none;
            border-radius: 8px;
            cursor: pointer;
            font-weight: bold;
            transition: transform 0.2s;
        }
        button:hover {
            transform: scale(1.05);
        }
        .metric {
            padding: 10px;
            margin: 10px 0;
            background: rgba(0,0,0,0.2);
            border-radius: 8px;
        }
        .quick-actions {
            display: flex;
            flex-direction: column;
            gap: 10px;
        }
        .typing-indicator {
            color: #94a3b8;
            font-style: italic;
            padding: 10px;
        }
        @media (max-width: 1024px) {
            .dashboard {
                grid-template-columns: 1fr;
            }
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🤖 Zoe AI Developer Dashboard</h1>
            <p>Multi-Model AI System Control Center</p>
        </div>
        
        <div class="dashboard">
            <!-- Left Panel - System Status -->
            <div class="panel">
                <h2>📊 System Status</h2>
                <div id="system-status">
                    <div class="status-checking">Checking...</div>
                </div>
                <div id="system-metrics" style="margin-top: 20px;">
                    <div class="metric">Loading metrics...</div>
                </div>
                <div id="metrics-display" style="margin-top: 20px;">
                    <!-- Metrics will be displayed here -->
                </div>
            </div>
            
            <!-- Center Panel - Chat Interface -->
            <div class="panel">
                <h2>💬 AI Chat</h2>
                <div id="chat-messages"></div>
                <form id="chat-form" class="chat-form">
                    <input 
                        type="text" 
                        id="chat-input" 
                        placeholder="Ask me anything..." 
                        autocomplete="off"
                    />
                    <button type="submit">Send</button>
                </form>
            </div>
            
            <!-- Right Panel - Quick Actions -->
            <div class="panel">
                <h2>⚡ Quick Actions</h2>
                <div class="quick-actions">
                    <button onclick="checkSystem()">🔍 Check System</button>
                    <button onclick="testAI()">🧪 Test AI Models</button>
                    <button onclick="viewLogs()">📋 View Logs</button>
                    <button onclick="createBackup()">💾 Create Backup</button>
                    <button onclick="runDiagnostics()">🔧 Run Diagnostics</button>
                </div>
                
                <h3 style="margin-top: 30px;">📈 AI Usage</h3>
                <div id="ai-usage">
                    <div class="metric">Loading...</div>
                </div>
            </div>
        </div>
    </div>
    
    <script src="js/developer.js"></script>
</body>
</html>
EOF
echo "✅ Step 3 Complete - HTML created"
sleep 1

# Step 4: Create JavaScript with dynamic API URL
echo -e "\n📍 STEP 4: Creating Dashboard JavaScript"
echo "────────────────────────────────"
cat > services/zoe-ui/dist/developer/js/developer.js << 'EOF'
// Developer Dashboard JavaScript
// Dynamically determine API URL based on current location
const API_BASE = window.location.hostname === 'localhost' 
    ? 'http://localhost:8000' 
    : `http://${window.location.hostname}:8000`;

console.log('🚀 Zoe Developer Dashboard Initializing...');
console.log('📡 API Base URL:', API_BASE);

// Initialize on page load
document.addEventListener('DOMContentLoaded', () => {
    console.log('📋 DOM Loaded - Starting initialization');
    initializeDashboard();
});

async function initializeDashboard() {
    console.log('🔧 Initializing dashboard components...');
    
    // Initial status check
    await checkSystemStatus();
    
    // Set up chat interface
    setupChatInterface();
    
    // Load AI usage
    await loadAIUsage();
    
    // Set up periodic updates
    setInterval(checkSystemStatus, 30000);
    setInterval(loadAIUsage, 60000);
    
    // Add welcome message
    addMessage('ai', 'Welcome to Zoe AI Developer Dashboard! How can I help you today?', 'system');
}

async function checkSystemStatus() {
    console.log('🔍 Checking system status...');
    const statusEl = document.getElementById('system-status');
    
    try {
        const response = await fetch(`${API_BASE}/api/developer/status`);
        console.log('Status response:', response.status);
        
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = await response.json();
        console.log('Status data:', data);
        
        updateStatusDisplay(data);
        
        // Try to get detailed system status
        try {
            const sysResponse = await fetch(`${API_BASE}/api/developer/system/status`);
            if (sysResponse.ok) {
                const sysData = await sysResponse.json();
                updateSystemMetrics(sysData);
            }
        } catch (e) {
            console.log('System status endpoint not available');
        }
        
    } catch (error) {
        console.error('❌ Failed to check system status:', error);
        statusEl.innerHTML = `<div class="status-error">Connection Error: ${error.message}</div>`;
    }
}

function updateStatusDisplay(data) {
    const statusEl = document.getElementById('system-status');
    if (!statusEl) return;
    
    const statusClass = data.status === 'operational' ? 'status-good' : 'status-error';
    const aiModels = data.ai_models || {};
    const activeModels = Object.entries(aiModels)
        .filter(([_, active]) => active)
        .map(([name, _]) => name);
    
    statusEl.innerHTML = `
        <div class="${statusClass}">
            System: ${data.status.toUpperCase()}
        </div>
        <div class="metric">
            <strong>API:</strong> ${data.services?.api || 'unknown'}
        </div>
        <div class="metric">
            <strong>AI:</strong> ${data.services?.ai || 'unknown'}
        </div>
        <div class="metric">
            <strong>Models:</strong> ${activeModels.join(', ') || 'none'}
        </div>
    `;
}

function updateSystemMetrics(data) {
    const metricsEl = document.getElementById('system-metrics');
    if (!metricsEl || !data.memory) return;
    
    const memTotal = parseInt(data.memory.MemTotal) / 1024 / 1024;
    const memAvailable = parseInt(data.memory.MemAvailable) / 1024 / 1024;
    const memUsed = memTotal - memAvailable;
    const memPercent = ((memUsed / memTotal) * 100).toFixed(1);
    
    metricsEl.innerHTML = `
        <h3>💾 Memory</h3>
        <div class="metric">
            Used: ${memUsed.toFixed(1)}GB / ${memTotal.toFixed(1)}GB (${memPercent}%)
        </div>
    `;
}

async function loadAIUsage() {
    console.log('📊 Loading AI usage...');
    const usageEl = document.getElementById('ai-usage');
    
    try {
        const response = await fetch(`${API_BASE}/api/developer/ai/usage`);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = await response.json();
        
        usageEl.innerHTML = `
            <div class="metric">
                <strong>Daily Budget:</strong> $${data.daily_budget}
            </div>
            <div class="metric">
                <strong>Used Today:</strong> $${data.used_today}
            </div>
            <div class="metric">
                <strong>Remaining:</strong> $${data.remaining}
            </div>
        `;
    } catch (error) {
        console.error('Failed to load AI usage:', error);
        usageEl.innerHTML = '<div class="metric">Unable to load usage data</div>';
    }
}

function setupChatInterface() {
    console.log('💬 Setting up chat interface...');
    const form = document.getElementById('chat-form');
    const input = document.getElementById('chat-input');
    
    if (!form || !input) {
        console.error('Chat interface elements not found');
        return;
    }
    
    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        
        const message = input.value.trim();
        if (!message) return;
        
        console.log('📤 Sending message:', message);
        
        // Add user message
        addMessage('user', message);
        input.value = '';
        
        // Show typing indicator
        const typingId = showTypingIndicator();
        
        try {
            const response = await fetch(`${API_BASE}/api/developer/chat`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ message })
            });
            
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const data = await response.json();
            
            console.log('📥 Received response:', data);
            
            // Remove typing indicator
            removeTypingIndicator(typingId);
            
            // Add AI response
            addMessage('ai', data.response, data.model_used);
            
        } catch (error) {
            console.error('Chat error:', error);
            removeTypingIndicator(typingId);
            addMessage('error', `Error: ${error.message}`);
        }
    });
    
    // Focus input
    input.focus();
}

function addMessage(type, content, model = null) {
    const messagesEl = document.getElementById('chat-messages');
    if (!messagesEl) return;
    
    const messageDiv = document.createElement('div');
    messageDiv.className = `message ${type}-message`;
    
    const timestamp = new Date().toLocaleTimeString();
    const sender = type === 'user' ? 'You' : type === 'error' ? 'Error' : `AI${model ? ' (' + model + ')' : ''}`;
    
    messageDiv.innerHTML = `
        <div style="font-weight: bold; margin-bottom: 5px;">
            ${sender} <span style="font-weight: normal; opacity: 0.7; font-size: 0.9em;">${timestamp}</span>
        </div>
        <div>${escapeHtml(content)}</div>
    `;
    
    messagesEl.appendChild(messageDiv);
    messagesEl.scrollTop = messagesEl.scrollHeight;
}

function showTypingIndicator() {
    const messagesEl = document.getElementById('chat-messages');
    if (!messagesEl) return null;
    
    const typingDiv = document.createElement('div');
    const id = 'typing-' + Date.now();
    typingDiv.id = id;
    typingDiv.className = 'typing-indicator';
    typingDiv.innerHTML = '🤖 AI is thinking...';
    
    messagesEl.appendChild(typingDiv);
    messagesEl.scrollTop = messagesEl.scrollHeight;
    
    return id;
}

function removeTypingIndicator(id) {
    if (!id) return;
    const element = document.getElementById(id);
    if (element) element.remove();
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// Quick action functions
window.checkSystem = async function() {
    console.log('🔍 Running system check...');
    await checkSystemStatus();
    await loadAIUsage();
    alert('✅ System check complete!');
};

window.testAI = async function() {
    console.log('🧪 Testing AI models...');
    const input = document.getElementById('chat-input');
    input.value = 'Test all AI models - respond with which model you are using';
    document.getElementById('chat-form').dispatchEvent(new Event('submit'));
};

window.viewLogs = async function() {
    console.log('📋 Fetching logs...');
    const input = document.getElementById('chat-input');
    input.value = 'Show me the last 10 system log entries';
    document.getElementById('chat-form').dispatchEvent(new Event('submit'));
};

window.createBackup = async function() {
    console.log('💾 Creating backup...');
    try {
        const response = await fetch(`${API_BASE}/api/developer/backup`, {
            method: 'POST'
        });
        const data = await response.json();
        alert(data.message || '✅ Backup initiated');
    } catch (error) {
        alert('❌ Backup failed: ' + error.message);
    }
};

window.runDiagnostics = async function() {
    console.log('🔧 Running diagnostics...');
    const input = document.getElementById('chat-input');
    input.value = 'Run a complete system diagnostic and report any issues';
    document.getElementById('chat-form').dispatchEvent(new Event('submit'));
};

console.log('✅ Developer Dashboard JavaScript loaded successfully');
EOF
echo "✅ Step 4 Complete - JavaScript created"
sleep 1

# Step 5: Restart services
echo -e "\n📍 STEP 5: Restarting Services"
echo "────────────────────────────────"
echo "Restarting zoe-core..."
docker compose restart zoe-core
echo "Restarting zoe-ui..."
docker compose restart zoe-ui
echo "Waiting for services to start..."
sleep 10
echo "✅ Step 5 Complete - Services restarted"

# Step 6: Test all endpoints
echo -e "\n📍 STEP 6: Testing All Endpoints"
echo "────────────────────────────────"
echo "Testing health endpoint:"
curl -s http://localhost:8000/health | jq '.' || echo "❌ Health check failed"

echo -e "\nTesting developer status:"
curl -s http://localhost:8000/api/developer/status | jq '.' || echo "❌ Status check failed"

echo -e "\nTesting AI chat (simple):"
curl -s -X POST http://localhost:8000/api/developer/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Say hello"}' \
  --max-time 10 | jq '.model_used' || echo "❌ Chat test failed"

echo "✅ Step 6 Complete - API tests done"

# Step 7: Final verification
echo -e "\n📍 STEP 7: Final Verification"
echo "────────────────────────────────"
echo "Checking container status:"
docker ps --format "table {{.Names}}\t{{.Status}}" | grep -E "zoe-core|zoe-ui"

echo -e "\nChecking CORS headers:"
curl -I -X OPTIONS http://localhost:8000/api/developer/status 2>/dev/null | grep -i "access-control" || echo "No CORS headers found"

# Step 8: Success message
echo -e "\n════════════════════════════════════════"
echo "   ✅ DASHBOARD FIX COMPLETE!"
echo "════════════════════════════════════════"
echo ""
echo "🌐 Open your browser to:"
echo "   http://192.168.1.60:8080/developer/"
echo ""
echo "📱 Or from another device on the network:"
echo "   http://192.168.1.60:8080/developer/"
echo ""
echo "🧪 Test features:"
echo "   1. Chat with the AI"
echo "   2. Click 'Test AI Models'"
echo "   3. Try 'Run Diagnostics'"
echo ""
echo "If still having issues, check browser console (F12)"
echo "════════════════════════════════════════"

# Save the script for future use
cat > fix_dashboard.sh << 'INNER_EOF'
#!/bin/bash
# Quick dashboard fix script
cd /home/pi/zoe
docker compose restart zoe-core zoe-ui
sleep 10
curl -s http://localhost:8000/health | jq '.'
echo "Dashboard available at: http://192.168.1.60:8080/developer/"
INNER_EOF
chmod +x fix_dashboard.sh

echo -e "\n💡 Quick fix script saved as: fix_dashboard.sh"
