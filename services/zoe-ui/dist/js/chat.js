class SimpleChat {
    constructor() {
        this.init();
    }
    
    init() {
        const input = document.getElementById('chat-input');
        const button = document.getElementById('send-btn');
        
        if (input) {
            input.addEventListener('keydown', (e) => {
                if (e.key === 'Enter') {
                    e.preventDefault();
                    this.sendMessage();
                }
            });
        }
        
        if (button) {
            button.addEventListener('click', () => this.sendMessage());
        }
    }
    
    async sendMessage() {
        const input = document.getElementById('chat-input');
        const message = input.value.trim();
        
        if (!message) return;
        
        this.addMessage('user', message);
        input.value = '';
        
        try {
            const response = await window.zoeAPI.sendMessage(message);
            if (response.error) {
                this.addMessage('assistant', '❌ ' + response.error);
            } else {
                this.addMessage('assistant', response.response);
            }
        } catch (error) {
            this.addMessage('assistant', '❌ Connection error');
        }
    }
    
    addMessage(role, content) {
        const container = document.getElementById('chat-messages');
        if (!container) return;
        
        const div = document.createElement('div');
        div.className = `message ${role}`;
        div.innerHTML = `
            <div class="message-avatar">${role === 'user' ? '👤' : '🎭'}</div>
            <div class="message-content">
                <div class="message-text">${content}</div>
                <div class="message-time">${new Date().toLocaleTimeString()}</div>
            </div>
        `;
        
        container.appendChild(div);
        container.scrollTop = container.scrollHeight;
        
        const welcome = document.getElementById('welcome-screen');
        if (welcome && role === 'user') {
            welcome.style.display = 'none';
        }
    }
}

document.addEventListener('DOMContentLoaded', () => {
    window.chat = new SimpleChat();
});
