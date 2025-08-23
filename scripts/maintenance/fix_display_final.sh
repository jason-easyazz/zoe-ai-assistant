#!/bin/bash
# Final fix for message display

echo "🎨 FINAL DISPLAY FIX"
echo "==================="

cd /home/pi/zoe

# Backup current developer.js
cp services/zoe-ui/dist/developer/js/developer.js services/zoe-ui/dist/developer/js/developer.js.backup

# Create a fixed version
cat > services/zoe-ui/dist/developer/js/developer_fixed.js << 'EOF'
const API_BASE = 'http://localhost:8000/api';

// Fixed message display with proper formatting
function addMessage(message, sender) {
    const messagesDiv = document.getElementById('messages');
    const messageDiv = document.createElement('div');
    messageDiv.className = `message ${sender}`;
    
    // Process markdown to HTML
    let formatted = message
        // Headers first
        .replace(/^### (.*?)$/gm, '</p><h4>$1</h4><p>')
        .replace(/^## (.*?)$/gm, '</p><h3>$1</h3><p>')
        
        // Bold text
        .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
        
        // Lists
        .replace(/^[•\-] (.*?)$/gm, '<li>$1</li>')
        
        // Wrap lists in ul tags
        .replace(/(<li>.*<\/li>\n?)+/g, function(match) {
            return '</p><ul>' + match + '</ul><p>';
        })
        
        // Line breaks
        .replace(/\n\n/g, '</p><p>')
        .replace(/\n/g, '<br>');
    
    // Wrap in paragraph tags
    formatted = '<p>' + formatted + '</p>';
    
    // Clean up empty paragraphs
    formatted = formatted.replace(/<p><\/p>/g, '');
    
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

// Add better styling
document.addEventListener('DOMContentLoaded', function() {
    const style = document.createElement('style');
    style.textContent = `
        .message-content h3 {
            color: #1e40af;
            font-size: 1.2em;
            margin: 10px 0 5px 0;
            font-weight: 600;
        }
        .message-content h4 {
            color: #2563eb;
            font-size: 1.1em;
            margin: 8px 0 5px 0;
            font-weight: 600;
        }
        .message-content ul {
            margin: 5px 0;
            padding-left: 20px;
            list-style: none;
        }
        .message-content li {
            margin: 3px 0;
            position: relative;
        }
        .message-content li:before {
            content: "•";
            position: absolute;
            left: -15px;
            color: #6b7280;
        }
        .message-content p {
            margin: 5px 0;
            line-height: 1.5;
        }
        .message-content strong {
            font-weight: 600;
            color: #111827;
        }
        .message.claude .message-content {
            background: #f9fafb;
            padding: 12px 15px;
            border-radius: 8px;
            border-left: 3px solid #3b82f6;
        }
    `;
    document.head.appendChild(style);
    
    // Enter key sends message
    document.getElementById('messageInput').addEventListener('keypress', function(e) {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    });
});
EOF

# Replace the developer.js
cp services/zoe-ui/dist/developer/js/developer_fixed.js services/zoe-ui/dist/developer/js/developer.js

echo "✅ Display completely fixed!"
echo ""
echo "Do a HARD REFRESH (Ctrl+Shift+R) and you'll see:"
echo "• Blue headers"
echo "• Proper bullet points"
echo "• Bold text"
echo "• Clean spacing"
echo "• Professional formatting"
