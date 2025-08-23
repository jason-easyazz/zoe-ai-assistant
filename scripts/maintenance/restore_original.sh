#!/bin/bash
# Restore ORIGINAL developer dashboard

echo "🔄 RESTORING ORIGINAL DASHBOARD"
echo "==============================="

cd /home/pi/zoe

# First, let's see what CSS is actually referenced
echo "Current CSS references in HTML:"
grep -E 'link.*css|style' services/zoe-ui/dist/developer/index.html | head -5

# Restore the ORIGINAL developer HTML (from the beginning)
cat > services/zoe-ui/dist/developer/index.html << 'EOF'
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
                    </div>
                </div>

                <div class="quick-actions">
                    <h3>⚡ Quick Actions</h3>
                    <button onclick="checkSystem()">🔍 Check</button>
                    <button onclick="fixIssues()">🔧 Fix</button>
                    <button onclick="backup()">💾 Backup</button>
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
                <div id="messages"></div>
                <div class="input-area">
                    <input type="text" id="messageInput" placeholder="Ask Claude..." />
                    <button onclick="sendMessage()" class="send-button">Send</button>
                </div>
            </div>
        </div>
    </div>

    <script src="js/developer.js"></script>
</body>
</html>
EOF

# Now restore the GOOD CSS with purple page but white messages
cat > services/zoe-ui/dist/developer/css/developer.css << 'EOF'
/* Developer Dashboard - Original Design */
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

.status-dot {
    width: 8px;
    height: 8px;
    background: #16a34a;
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

#messages {
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
    margin: 0 10px;
}

/* WHITE message bubbles only */
.message-content {
    background: #ffffff;
    border: 1px solid #e5e7eb;
    padding: 12px 16px;
    border-radius: 12px;
    max-width: 85%;
    line-height: 1.6;
    color: #1f2937;
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
EOF

# Remove any conflicting styles
rm -f services/zoe-ui/dist/developer/css/clean_style.css
rm -f services/zoe-ui/dist/developer/css/style.css
rm -f services/zoe-ui/dist/developer/style_override.css

echo "✅ Original dashboard restored!"
echo ""
echo "Clear cache and hard refresh: Ctrl+Shift+R"
echo ""
echo "You should now see:"
echo "• Purple gradient background"
echo "• Glass-morphic panels"
echo "• White message bubbles"
echo "• All original styling"
