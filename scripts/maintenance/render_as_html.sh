#!/bin/bash
# Force HTML rendering in messages

echo "🎨 FORCE HTML RENDERING"
echo "======================"

cd /home/pi/zoe

# Update developer.js to render HTML properly
cat > services/zoe-ui/dist/developer/js/developer.js << 'EOF'
const API_BASE = 'http://localhost:8000/api';

function addMessage(message, sender) {
    const messagesDiv = document.getElementById('messages');
    const messageDiv = document.createElement('div');
    messageDiv.className = `message ${sender}`;
    
    // For Claude messages, treat as HTML
    // For user messages, escape HTML
    const content = sender === 'claude' ? message : message.replace(/</g, '&lt;').replace(/>/g, '&gt;');
    
    messageDiv.innerHTML = `
        <div class="message-icon">${sender === 'user' ? '👤' : '🧠'}</div>
        <div class="message-content">${content}</div>
    `;
    
    messagesDiv.appendChild(messageDiv);
    messagesDiv.scrollTop = messagesDiv.scrollHeight;
}

async function sendMessage() {
    const input = document.getElementById('messageInput');
    const message = input.value.trim();
    if (!message) return;
    
    addMessage(message, 'user');
    input.value = '';
    
    try {
        const response = await fetch(`${API_BASE}/developer/chat`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({message: message})
        });
        const data = await response.json();
        addMessage(data.response || 'No response', 'claude');
    } catch (error) {
        addMessage('Error: ' + error.message, 'claude');
    }
}

// Initialize
document.addEventListener('DOMContentLoaded', function() {
    // Add CSS for better formatting
    const style = document.createElement('style');
    style.textContent = `
        .message-content h4 {
            color: #1e40af;
            margin: 10px 0 5px 0;
            font-weight: 600;
        }
        .message-content strong {
            font-weight: 600;
        }
        .message.claude .message-content {
            background: #f9fafb;
            padding: 12px;
            border-radius: 8px;
            line-height: 1.6;
        }
    `;
    document.head.appendChild(style);
    
    document.getElementById('messageInput').addEventListener('keypress', function(e) {
        if (e.key === 'Enter') {
            e.preventDefault();
            sendMessage();
        }
    });
});
EOF

# Force cache clear
timestamp=$(date +%s)
sed -i "s/developer\.js[^\"']*/developer.js?v=$timestamp/g" services/zoe-ui/dist/developer/index.html

echo "✅ HTML rendering fixed!"
echo ""
echo "1. Press Ctrl+F5 to force refresh"
echo "2. Messages will now display with proper formatting"
