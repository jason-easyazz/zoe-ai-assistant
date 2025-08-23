#!/bin/bash
# Direct CSS fix - find and replace purple

echo "🔍 DIRECT CSS FIX"
echo "================"

cd /home/pi/zoe

# First, let's see what's in the developer CSS
echo "Current CSS files:"
ls -la services/zoe-ui/dist/developer/css/

# Directly edit developer.css to remove ALL backgrounds
cat > services/zoe-ui/dist/developer/css/developer.css << 'EOF'
/* Developer Dashboard - Clean Style */
* {
    margin: 0;
    padding: 0;
    box-sizing: border-box;
}

body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
    min-height: 100vh;
}

.container {
    max-width: 1400px;
    margin: 0 auto;
    padding: 20px;
}

.header {
    background: rgba(255, 255, 255, 0.95);
    border-radius: 16px;
    padding: 20px;
    margin-bottom: 20px;
}

.main-content {
    display: grid;
    grid-template-columns: 1fr 2fr;
    gap: 20px;
}

.status-panel {
    background: rgba(255, 255, 255, 0.95);
    border-radius: 16px;
    padding: 20px;
}

.chat-panel {
    background: rgba(255, 255, 255, 0.95);
    border-radius: 16px;
    padding: 20px;
}

/* MESSAGES - NO COLORED BACKGROUNDS */
#messages {
    height: 400px;
    overflow-y: auto;
    padding: 10px;
    margin-bottom: 20px;
}

.message {
    margin: 10px 0;
    display: flex;
    align-items: flex-start;
}

.message-icon {
    font-size: 24px;
    margin-right: 10px;
}

/* CLEAN WHITE BACKGROUNDS FOR ALL MESSAGES */
.message-content {
    background: #ffffff !important;
    border: 1px solid #e5e7eb !important;
    padding: 12px !important;
    border-radius: 8px !important;
    max-width: 85%;
    line-height: 1.6;
}

.message.claude .message-content {
    border-left: 3px solid #3b82f6 !important;
    margin-left: 0;
}

.message.user {
    justify-content: flex-end;
}

.message.user .message-content {
    border-right: 3px solid #10b981 !important;
    margin-right: 0;
}

.message.user .message-icon {
    order: 2;
    margin-left: 10px;
    margin-right: 0;
}

/* NO GLASS MORPHISM OR PURPLE */
.glass-morphic {
    background: transparent !important;
}

/* Input area */
.input-area {
    display: flex;
    gap: 10px;
}

#messageInput {
    flex: 1;
    padding: 12px;
    border: 1px solid #e5e7eb;
    border-radius: 8px;
    font-size: 14px;
}

button {
    padding: 12px 24px;
    background: #3b82f6;
    color: white;
    border: none;
    border-radius: 8px;
    cursor: pointer;
}

button:hover {
    background: #2563eb;
}
EOF

# Also ensure the HTML is rendered properly in developer.js
cat > services/zoe-ui/dist/developer/js/render_fix.js << 'EOF'
// Override message display to ensure HTML renders
function addMessage(message, sender) {
    const messagesDiv = document.getElementById('messages');
    const messageDiv = document.createElement('div');
    messageDiv.className = `message ${sender}`;
    
    // Ensure HTML is rendered, not escaped
    messageDiv.innerHTML = `
        <div class="message-icon">${sender === 'user' ? '👤' : '🧠'}</div>
        <div class="message-content">${message}</div>
    `;
    
    messagesDiv.appendChild(messageDiv);
    messagesDiv.scrollTop = messagesDiv.scrollHeight;
}
EOF

# Append the fix to developer.js
cat services/zoe-ui/dist/developer/js/render_fix.js >> services/zoe-ui/dist/developer/js/developer.js

echo "✅ Direct CSS fix applied!"
echo ""
echo "To ensure it works:"
echo "1. Close ALL browser tabs"
echo "2. Clear cache: Ctrl+Shift+Delete"
echo "3. Open new incognito/private window"
echo "4. Go to http://192.168.1.60:8080/developer/"
echo ""
echo "Messages will be clean white with blue/green borders only!"
