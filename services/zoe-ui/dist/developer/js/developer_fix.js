// Fixed developer dashboard JavaScript
const API_BASE = window.location.protocol === 'https:' 
    ? `https://${window.location.hostname}/api`
    : `http://${window.location.hostname}/api`;

// Test function to send specific messages
function sendTestMessage(text) {
    document.getElementById('messageInput').value = text;
    sendMessage();
}

async function sendMessage() {
    const input = document.getElementById('messageInput');
    const message = input.value.trim();
    
    if (!message) return;
    
    // Add user message to chat
    addMessage(message, 'user');
    input.value = '';
    
    try {
        console.log('Sending message:', message);
        
        const response = await fetch(`${API_BASE}/developer/chat`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ message: message })
        });
        
        const data = await response.json();
        console.log('Response:', data);
        
        // Add response to chat
        addMessage(data.response, 'assistant');
        
        // Show debug info if available
        if (data.debug) {
            console.log('Debug:', data.debug);
        }
        
    } catch (error) {
        console.error('Error:', error);
        addMessage('Error: ' + error.message, 'error');
    }
}

function addMessage(content, sender) {
    const messages = document.getElementById('messages') || document.querySelector('.messages');
    if (!messages) {
        console.error('Messages container not found');
        return;
    }
    
    const messageDiv = document.createElement('div');
    messageDiv.className = `message ${sender}`;
    
    // Handle HTML content properly
    if (content.includes('**') || content.includes('```')) {
        // Convert markdown-style to HTML
        content = content
            .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
            .replace(/```(.*?)```/gs, '<pre>$1</pre>')
            .replace(/\n/g, '<br>');
    }
    
    messageDiv.innerHTML = content;
    messages.appendChild(messageDiv);
    messages.scrollTop = messages.scrollHeight;
}

// Add enter key support
document.addEventListener('DOMContentLoaded', function() {
    const input = document.getElementById('messageInput');
    if (input) {
        input.addEventListener('keypress', function(e) {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                sendMessage();
            }
        });
    }
});
