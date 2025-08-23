#!/bin/bash
# Complete UI replacement with working markdown

echo "🔧 COMPLETE UI FIX"
echo "================="

cd /home/pi/zoe

# Completely replace developer.js
cat > services/zoe-ui/dist/developer/js/developer.js << 'EOF'
const API_BASE = 'http://localhost:8000/api';

function renderMarkdown(text) {
    return text
        .replace(/^### (.*?)$/gm, '<h4 style="color:#1e40af;margin:10px 0 5px">$1</h4>')
        .replace(/^## (.*?)$/gm, '<h3 style="color:#0f172a;margin:15px 0 8px">$1</h3>')
        .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
        .replace(/^[•\-] (.*?)$/gm, '<div style="margin:3px 0;padding-left:20px">• $1</div>')
        .replace(/\n/g, '<br>');
}

function addMessage(message, sender) {
    const messagesDiv = document.getElementById('messages');
    const messageDiv = document.createElement('div');
    messageDiv.className = `message ${sender}`;
    
    const formatted = sender === 'claude' ? renderMarkdown(message) : message;
    
    messageDiv.innerHTML = `
        <div class="message-icon">${sender === 'user' ? '👤' : '🧠'}</div>
        <div class="message-content">${formatted}</div>
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

document.addEventListener('DOMContentLoaded', function() {
    document.getElementById('messageInput').addEventListener('keypress', function(e) {
        if (e.key === 'Enter') {
            e.preventDefault();
            sendMessage();
        }
    });
});
EOF

# Clear browser cache by adding version to HTML
sed -i 's/developer.js/developer.js?v='$(date +%s)'/g' services/zoe-ui/dist/developer/index.html

echo "✅ Complete fix applied!"
echo ""
echo "Now:"
echo "1. Close the browser tab completely"
echo "2. Open a NEW tab"
echo "3. Go to http://192.168.1.60:8080/developer/"
echo ""
echo "The formatting will now work!"
