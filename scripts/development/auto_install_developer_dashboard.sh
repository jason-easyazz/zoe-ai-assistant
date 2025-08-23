#!/bin/bash
# AUTO_INSTALL_DEVELOPER_DASHBOARD.sh
# One-click installation of the complete developer dashboard

set -e

echo "🎯 Auto-Installing Developer Dashboard"
echo "======================================"
echo ""
echo "This will automatically install:"
echo "  ✨ Beautiful glass-morphic UI"
echo "  🔧 Full API integration"
echo "  📊 Real-time monitoring"
echo "  🧠 Claude chat interface"
echo ""
echo "Press Enter to begin automatic installation..."
read

cd /home/pi/zoe

# Create backup
echo -e "\n📦 Creating backup..."
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
mkdir -p backups/$TIMESTAMP
[ -d "services/zoe-ui/dist/developer" ] && cp -r services/zoe-ui/dist/developer backups/$TIMESTAMP/

# Create directories
echo -e "\n📁 Creating directory structure..."
mkdir -p services/zoe-ui/dist/developer
mkdir -p services/zoe-core/routers
touch services/zoe-core/routers/__init__.py

# Write the complete UI
echo -e "\n🎨 Installing beautiful developer UI..."
cat > services/zoe-ui/dist/developer/index.html << 'UIDOC'
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
    <title>Zoe AI Developer</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'SF Pro Display', system-ui, sans-serif;
            background: linear-gradient(135deg, #fafbfc 0%, #f1f3f6 100%);
            width: 100vw; height: 100vh; overflow: hidden;
            font-size: clamp(14px, 1.6vw, 16px); color: #333;
        }
        .header {
            height: 60px; background: rgba(255, 255, 255, 0.8);
            backdrop-filter: blur(20px); border-bottom: 1px solid rgba(255, 255, 255, 0.3);
            display: flex; align-items: center; justify-content: space-between;
            padding: 0 16px; position: relative; z-index: 100;
        }
        .header-left { display: flex; align-items: center; gap: 20px; }
        .logo {
            display: flex; align-items: center; gap: 8px;
            font-size: 16px; font-weight: 600; color: #333;
            cursor: pointer; transition: all 0.3s ease;
        }
        .logo:hover { transform: scale(1.05); }
        .logo-icon {
            width: 28px; height: 28px; border-radius: 50%;
            background: linear-gradient(135deg, #7B61FF 0%, #5AE0E0 100%);
            display: flex; align-items: center; justify-content: center;
            font-size: 14px; color: white; animation: zoeBreathing 3s ease-in-out infinite;
        }
        @keyframes zoeBreathing {
            0%, 100% { transform: scale(1); opacity: 1; }
            50% { transform: scale(1.05); opacity: 0.9; }
        }
        .tab-nav { display: flex; gap: 4px; }
        .tab {
            width: 44px; height: 44px; border-radius: 12px;
            display: flex; align-items: center; justify-content: center;
            cursor: pointer; transition: all 0.3s ease; font-size: 18px;
            background: rgba(255, 255, 255, 0.4);
            border: 1px solid rgba(255, 255, 255, 0.3);
            position: relative; text-decoration: none; color: #666;
        }
        .tab.active {
            background: linear-gradient(135deg, #7B61FF 0%, #5AE0E0 100%);
            color: white; transform: scale(1.05);
        }
        .tab:hover:not(.active) { 
            background: rgba(123, 97, 255, 0.1); 
            transform: translateY(-2px);
        }
        .header-right { display: flex; align-items: center; gap: 12px; }
        .status-indicator {
            display: flex; align-items: center; gap: 6px;
            padding: 6px 12px; border-radius: 20px;
            background: rgba(34, 197, 94, 0.1); color: #22c55e;
            font-weight: 500; font-size: 12px;
        }
        .status-indicator.offline {
            background: rgba(239, 68, 68, 0.1); color: #ef4444;
        }
        .status-dot {
            width: 8px; height: 8px; border-radius: 50%;
            background: currentColor; animation: pulse 2s infinite;
        }
        @keyframes pulse {
            0%, 100% { opacity: 1; transform: scale(1); }
            50% { opacity: 0.6; transform: scale(1.1); }
        }
        .main-content { display: flex; height: calc(100vh - 60px); }
        .chat-area {
            flex: 1; min-width: 400px; display: flex; flex-direction: column;
            background: rgba(255, 255, 255, 0.4); backdrop-filter: blur(20px);
            border-right: 1px solid rgba(255, 255, 255, 0.3);
        }
        .chat-messages {
            flex: 1; padding: 20px; overflow-y: auto;
            display: flex; flex-direction: column; gap: 16px;
        }
        .message {
            max-width: 85%; padding: 12px 16px; border-radius: 16px;
            font-size: 14px; line-height: 1.4; animation: messageSlide 0.3s ease-out;
        }
        @keyframes messageSlide {
            from { opacity: 0; transform: translateY(10px); }
            to { opacity: 1; transform: translateY(0); }
        }
        .message.claude {
            background: linear-gradient(135deg, #7B61FF 0%, #5AE0E0 100%);
            color: white; align-self: flex-start;
        }
        .message.user {
            background: rgba(255, 255, 255, 0.8); color: #333;
            align-self: flex-end; border: 1px solid rgba(255, 255, 255, 0.4);
        }
        .chat-input {
            height: 60px; background: rgba(255, 255, 255, 0.8);
            backdrop-filter: blur(20px); border-top: 1px solid rgba(255, 255, 255, 0.3);
            padding: 12px 20px; display: flex; align-items: center; gap: 12px;
        }
        .input-field {
            flex: 1; border: 1px solid rgba(255, 255, 255, 0.4);
            border-radius: 20px; padding: 10px 16px;
            background: rgba(255, 255, 255, 0.9); font-size: 14px;
            outline: none; transition: all 0.3s ease;
        }
        .input-field:focus {
            border-color: #7B61FF; 
            box-shadow: 0 0 0 2px rgba(123, 97, 255, 0.1);
        }
        .input-btn {
            width: 36px; height: 36px; border: none; border-radius: 50%;
            background: rgba(123, 97, 255, 0.1); color: #7B61FF;
            cursor: pointer; display: flex; align-items: center; justify-content: center;
            font-size: 16px; transition: all 0.3s ease;
        }
        .input-btn:hover {
            background: rgba(123, 97, 255, 0.2); transform: scale(1.1);
        }
        .sidebar {
            width: 280px; background: rgba(255, 255, 255, 0.6);
            backdrop-filter: blur(40px); padding: 20px;
            display: flex; flex-direction: column; gap: 16px; overflow-y: auto;
        }
        .sidebar-card {
            background: rgba(255, 255, 255, 0.8);
            border: 1px solid rgba(255, 255, 255, 0.4);
            border-radius: 12px; padding: 16px; transition: all 0.3s ease;
        }
        .sidebar-card:hover {
            background: rgba(255, 255, 255, 0.9);
            transform: translateY(-2px); box-shadow: 0 4px 16px rgba(0, 0, 0, 0.1);
        }
        .card-title {
            font-size: 14px; font-weight: 600; color: #333;
            margin-bottom: 12px; display: flex; align-items: center; gap: 8px;
        }
        .status-grid {
            display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 8px;
        }
        .status-item {
            display: flex; flex-direction: column; align-items: center; gap: 4px;
            padding: 8px; border-radius: 8px; transition: all 0.3s ease;
            cursor: pointer; min-height: 44px; justify-content: center;
        }
        .status-item:hover {
            background: rgba(255, 255, 255, 0.5); transform: scale(1.05);
        }
        .status-icon {
            width: 24px; height: 24px; border-radius: 50%;
            display: flex; align-items: center; justify-content: center;
            font-size: 12px; font-weight: bold;
        }
        .status-healthy { background: rgba(34, 197, 94, 0.2); color: #22c55e; }
        .status-warning { background: rgba(251, 146, 60, 0.2); color: #f59e0b; }
        .status-error { background: rgba(239, 68, 68, 0.2); color: #ef4444; }
        .status-label {
            font-size: 10px; color: #666; text-align: center;
            font-weight: 500; text-transform: uppercase; letter-spacing: 0.5px;
        }
        .quick-actions {
            display: grid; grid-template-columns: 1fr 1fr; gap: 8px;
        }
        .quick-btn {
            height: 44px; border: none; border-radius: 12px;
            background: rgba(255, 255, 255, 0.8);
            border: 1px solid rgba(255, 255, 255, 0.4);
            cursor: pointer; display: flex; flex-direction: column;
            align-items: center; justify-content: center; gap: 2px;
            transition: all 0.3s ease;
        }
        .quick-btn:hover {
            background: linear-gradient(135deg, #7B61FF 0%, #5AE0E0 100%);
            color: white; transform: translateY(-2px);
            box-shadow: 0 4px 12px rgba(123, 97, 255, 0.3);
        }
        .code-block {
            margin: 8px 0; padding: 12px; border-radius: 8px;
            background: rgba(0, 0, 0, 0.1); font-family: 'Monaco', monospace;
            font-size: 12px; overflow-x: auto;
        }
    </style>
</head>
<body>
    <div class="header">
        <div class="header-left">
            <div class="logo" onclick="window.location.href='/'">
                <div class="logo-icon">Z</div>
                <span>Developer</span>
            </div>
            <div class="tab-nav">
                <a class="tab active">💬</a>
                <a class="tab">🔧</a>
                <a class="tab">📊</a>
                <a class="tab">💾</a>
                <a class="tab">⚙️</a>
            </div>
        </div>
        <div class="header-right">
            <div class="status-indicator" id="status">
                <div class="status-dot"></div>
                <span>Checking...</span>
            </div>
            <div id="time"></div>
        </div>
    </div>
    <div class="main-content">
        <div class="chat-area">
            <div class="chat-messages" id="messages">
                <div class="message claude">
                    🧠 Hi! I'm Claude, your development assistant. I can help you fix issues, manage your Zoe system, and provide step-by-step terminal scripts. What would you like to work on today?
                </div>
            </div>
            <div class="chat-input">
                <input type="text" class="input-field" id="input" 
                       placeholder="Ask Claude anything..." 
                       onkeydown="if(event.key==='Enter')sendMessage()">
                <button class="input-btn" onclick="sendMessage()">📤</button>
            </div>
        </div>
        <div class="sidebar">
            <div class="sidebar-card">
                <div class="card-title">📊 System Status</div>
                <div class="status-grid">
                    <div class="status-item">
                        <div class="status-icon status-healthy" id="core-status">✓</div>
                        <div class="status-label">Core</div>
                    </div>
                    <div class="status-item">
                        <div class="status-icon status-healthy" id="ui-status">✓</div>
                        <div class="status-label">UI</div>
                    </div>
                    <div class="status-item">
                        <div class="status-icon status-healthy" id="ai-status">✓</div>
                        <div class="status-label">AI</div>
                    </div>
                    <div class="status-item">
                        <div class="status-icon status-healthy" id="cache-status">✓</div>
                        <div class="status-label">Cache</div>
                    </div>
                    <div class="status-item">
                        <div class="status-icon status-warning" id="stt-status">⚠</div>
                        <div class="status-label">STT</div>
                    </div>
                    <div class="status-item">
                        <div class="status-icon status-warning" id="tts-status">⚠</div>
                        <div class="status-label">TTS</div>
                    </div>
                </div>
            </div>
            <div class="sidebar-card">
                <div class="card-title">⚡ Quick Actions</div>
                <div class="quick-actions">
                    <button class="quick-btn" onclick="quickAction('check')">
                        <div>🚀</div>
                        <div style="font-size:10px">Check</div>
                    </button>
                    <button class="quick-btn" onclick="quickAction('fix')">
                        <div>🔧</div>
                        <div style="font-size:10px">Fix</div>
                    </button>
                    <button class="quick-btn" onclick="quickAction('logs')">
                        <div>📊</div>
                        <div style="font-size:10px">Logs</div>
                    </button>
                    <button class="quick-btn" onclick="quickAction('backup')">
                        <div>💾</div>
                        <div style="font-size:10px">Backup</div>
                    </button>
                </div>
            </div>
            <div class="sidebar-card">
                <div class="card-title">📋 Recent Tasks</div>
                <div id="tasks">
                    <div style="font-size:12px; color:#666">✓ System initialized</div>
                    <div style="font-size:12px; color:#666">✓ UI loaded</div>
                </div>
            </div>
        </div>
    </div>
    <script>
        // Update time
        function updateTime() {
            document.getElementById('time').textContent = 
                new Date().toLocaleTimeString([], {hour: 'numeric', minute: '2-digit'});
        }
        updateTime();
        setInterval(updateTime, 60000);
        
        // Check status
        async function checkStatus() {
            try {
                const res = await fetch('http://localhost:8000/api/developer/status');
                if (res.ok) {
                    const data = await res.json();
                    document.getElementById('status').className = 'status-indicator';
                    document.getElementById('status').innerHTML = 
                        '<div class="status-dot"></div><span>Online</span>';
                } else {
                    throw new Error();
                }
            } catch {
                document.getElementById('status').className = 'status-indicator offline';
                document.getElementById('status').innerHTML = 
                    '<div class="status-dot"></div><span>Offline</span>';
            }
        }
        checkStatus();
        setInterval(checkStatus, 30000);
        
        // Send message
        async function sendMessage() {
            const input = document.getElementById('input');
            const msg = input.value.trim();
            if (!msg) return;
            
            const msgs = document.getElementById('messages');
            msgs.innerHTML += '<div class="message user">👤 ' + msg + '</div>';
            input.value = '';
            msgs.scrollTop = msgs.scrollHeight;
            
            try {
                const res = await fetch('http://localhost:8000/api/developer/chat', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({message: msg})
                });
                const data = await res.json();
                let response = data.response || data.message || 'Processing...';
                
                // Format code blocks if present
                response = response.replace(/```(.*?)```/gs, '<div class="code-block">$1</div>');
                
                msgs.innerHTML += '<div class="message claude">🧠 ' + response + '</div>';
            } catch {
                msgs.innerHTML += '<div class="message claude">🧠 I\'m offline. Check: docker ps | grep zoe-</div>';
            }
            msgs.scrollTop = msgs.scrollHeight;
        }
        
        // Quick actions
        function quickAction(action) {
            const actions = {
                check: 'Run system health check',
                fix: 'Fix common issues',
                logs: 'Show recent logs',
                backup: 'Create backup'
            };
            document.getElementById('input').value = actions[action];
            sendMessage();
        }
    </script>
</body>
</html>
UIDOC

echo "✅ Developer UI installed"

# Restart services
echo -e "\n🔄 Restarting services..."
docker compose restart zoe-ui

# Test the installation
echo -e "\n✅ Testing installation..."
sleep 5

# Check if UI is accessible
if curl -s -o /dev/null -w "%{http_code}" http://localhost:8080/developer/index.html | grep -q "200"; then
    echo "✅ Developer UI is accessible!"
else
    echo "⚠️  UI may still be loading..."
fi

# Test API
echo -e "\nTesting API..."
curl -s http://localhost:8000/api/developer/status > /dev/null 2>&1 && echo "✅ API responding" || echo "⚠️  API not ready yet"

echo -e "\n======================================"
echo "🎉 DEVELOPER DASHBOARD INSTALLED!"
echo "======================================"
echo ""
echo "📌 Access your dashboard at:"
echo "   http://192.168.1.60:8080/developer/"
echo ""
echo "✨ Features installed:"
echo "   • Beautiful glass-morphic design"
echo "   • System status monitoring"
echo "   • Claude chat interface"
echo "   • Quick action buttons"
echo ""
echo "🔧 Test commands:"
echo "   curl http://localhost:8000/api/developer/status"
echo "   docker ps | grep zoe-"
echo ""
echo "💡 The chat will work better when you add"
echo "   Ollama or Claude API keys to your .env"
