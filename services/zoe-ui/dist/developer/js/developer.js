// Zoe Developer Dashboard JavaScript
const API_BASE = '/api';
let chatHistory = [];
let systemStatus = {};
let isProcessing = false;

// Initialize on load
document.addEventListener('DOMContentLoaded', () => {
    console.log('🚀 Developer Dashboard Initializing...');
    initializeDashboard();
    setInterval(updateTime, 1000);
    setInterval(checkSystemStatus, 30000);
});

async function initializeDashboard() {
    updateTime();
    await checkClaudeStatus();
    await checkSystemStatus();
    await loadRecentTasks();
    
    // Focus on input
    document.getElementById('messageInput').focus();
}

function updateTime() {
    const now = new Date();
    document.getElementById('currentTime').textContent = 
        now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

async function checkClaudeStatus() {
    try {
        const response = await fetch(`${API_BASE}/developer/status`);
        const statusEl = document.getElementById('claudeStatus');
        
        if (response.ok) {
            statusEl.className = 'status-indicator';
            statusEl.innerHTML = '<div class="status-dot"></div><span>Claude Online</span>';
        } else {
            throw new Error('Claude offline');
        }
    } catch (error) {
        const statusEl = document.getElementById('claudeStatus');
        statusEl.className = 'status-indicator offline';
        statusEl.innerHTML = '<div class="status-dot"></div><span>Claude Offline</span>';
    }
}

async function checkSystemStatus() {
    try {
        const response = await fetch(`${API_BASE}/developer/system/status`);
        if (response.ok) {
            const data = await response.json();
            updateSystemStatusDisplay(data);
        }
    } catch (error) {
        console.error('Failed to check system status:', error);
    }
}

function updateSystemStatusDisplay(status) {
    const container = document.getElementById('systemStatus');
    const services = [
        { name: 'CORE', key: 'core', icon: '✅' },
        { name: 'UI', key: 'ui', icon: '✅' },
        { name: 'AI', key: 'ollama', icon: '✅' },
        { name: 'CACHE', key: 'redis', icon: '✅' },
        { name: 'STT', key: 'whisper', icon: '⚠️' },
        { name: 'TTS', key: 'tts', icon: '⚠️' }
    ];
    
    container.innerHTML = services.map(service => {
        const state = status[service.key] || 'unknown';
        const icon = state === 'healthy' ? '✅' : state === 'warning' ? '⚠️' : '❌';
        
        return `
            <div class="status-item" onclick="checkService('${service.key}')">
                <div class="status-icon">${icon}</div>
                <div>${service.name}</div>
            </div>
        `;
    }).join('');
}

async function sendMessage() {
    const input = document.getElementById('messageInput');
    const message = input.value.trim();
    
    if (!message || isProcessing) return;
    
    // Add user message
    addMessage(message, 'user');
    input.value = '';
    isProcessing = true;
    
    try {
        // Send to developer chat endpoint
        const response = await fetch(`${API_BASE}/developer/chat`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message: message })
        });
        
        if (response.ok) {
            const data = await response.json();
            addMessage(data.response || "I'm processing that request...", 'claude');
        } else {
            throw new Error('Chat request failed');
        }
    } catch (error) {
        console.error('Chat error:', error);
        addMessage('Error: Could not connect to backend. Check if zoe-core is running.', 'claude');
    } finally {
        isProcessing = false;
    }
}

function addMessage(content, sender) {
    const container = document.getElementById('chatMessages');
    const messageDiv = document.createElement('div');
    messageDiv.className = `message ${sender}`;
    
    const icon = sender === 'claude' ? '🧠' : '👤';
    
    // Allow HTML in Claude's responses for formatting
    messageDiv.innerHTML = `
        <span class="message-icon">${icon}</span>
        <div class="message-content">${content}</div>
    `;
    
    container.appendChild(messageDiv);
    container.scrollTop = container.scrollHeight;
    
    // Store in history
    chatHistory.push({ sender, content, timestamp: new Date() });
    if (chatHistory.length > 50) chatHistory.shift();
}

function handleInputKeyDown(event) {
    if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        sendMessage();
    }
}

async function quickAction(action) {
    const actions = {
        systemCheck: "Run system health check",
        fixIssues: "Fix any system issues",
        backup: "Create a backup"
    };
    
    const message = actions[action];
    if (message) {
        document.getElementById('messageInput').value = message;
        sendMessage();
    }
}

async function checkService(service) {
    const message = `Check ${service} service status`;
    document.getElementById('messageInput').value = message;
    sendMessage();
}

async function loadRecentTasks() {
    const container = document.getElementById('recentTasks');
    try {
        const response = await fetch(`${API_BASE}/developer/tasks/recent`);
        if (response.ok) {
            const tasks = await response.json();
            container.innerHTML = tasks.slice(0, 5).map(task => 
                `<div>✅ ${task.title}</div>`
            ).join('');
        }
    } catch (error) {
        console.log('Could not load tasks');
    }
}
