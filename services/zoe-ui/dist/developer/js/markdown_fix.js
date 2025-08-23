// Override addMessage to render markdown properly
function addMessage(message, sender) {
    const messagesDiv = document.getElementById('messages');
    const messageDiv = document.createElement('div');
    messageDiv.className = `message ${sender}`;
    
    // Convert markdown to HTML
    let html = message
        // Headers
        .replace(/### (.*?)$/gm, '<h4 style="margin: 10px 0 5px 0; color: #2563eb;">$1</h4>')
        .replace(/## (.*?)$/gm, '<h3 style="margin: 15px 0 10px 0; color: #1e40af;">$1</h3>')
        
        // Bold
        .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
        
        // Bullets with proper spacing
        .replace(/^- (.*?)$/gm, '<div style="margin: 3px 0; padding-left: 20px;">• $1</div>')
        .replace(/^• (.*?)$/gm, '<div style="margin: 3px 0; padding-left: 20px;">• $1</div>')
        
        // Line breaks
        .replace(/\n\n/g, '<div style="height: 10px;"></div>')
        .replace(/\n/g, '<br>')
        
        // Status colors
        .replace(/✅/g, '<span style="color: #10b981;">✅</span>')
        .replace(/⚠️/g, '<span style="color: #f59e0b;">⚠️</span>')
        .replace(/❌/g, '<span style="color: #ef4444;">❌</span>')
        .replace(/🟢/g, '<span style="color: #10b981;">🟢</span>')
        .replace(/🔴/g, '<span style="color: #ef4444;">🔴</span>');
    
    messageDiv.innerHTML = `
        <div class="message-icon">${sender === 'user' ? '👤' : '🧠'}</div>
        <div class="message-content" style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;">
            ${html}
        </div>
    `;
    
    messagesDiv.appendChild(messageDiv);
    messagesDiv.scrollTop = messagesDiv.scrollHeight;
}

// Also update the CSS for better message styling
const style = document.createElement('style');
style.textContent = `
    .message-content {
        line-height: 1.6;
        color: #1f2937;
    }
    .message-content h3 {
        border-bottom: 2px solid #e5e7eb;
        padding-bottom: 5px;
    }
    .message-content h4 {
        font-weight: 600;
    }
    .message-content strong {
        color: #111827;
        font-weight: 600;
    }
    .message.claude .message-content {
        background: linear-gradient(135deg, #f9fafb 0%, #f3f4f6 100%);
        border-radius: 12px;
        padding: 15px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.1);
    }
`;
document.head.appendChild(style);
