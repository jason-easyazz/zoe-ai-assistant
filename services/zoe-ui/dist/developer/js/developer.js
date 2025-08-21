// Zoe Developer Dashboard JavaScript
const API_BASE = 'http://localhost:8000/api';
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
    await loadPerformanceMetrics();
    
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
        { name: 'Core', key: 'core', icon: '🧠' },
        { name: 'UI', key: 'ui', icon: '🎨' },
        { name: 'Ollama', key: 'ollama', icon: '🤖' },
        { name: 'Redis', key: 'redis', icon: '💾' },
        { name: 'Voice', key: 'voice', icon: '🎤' },
        { name: 'API', key: 'api', icon: '🔌' }
    ];
    
    container.innerHTML = services.map(service => {
        const state = status[service.key] || 'unknown';
        const statusClass = state === 'healthy' ? 'healthy' : 
                          state === 'warning' ? 'warning' : 'error';
        
        return `
            <div class="status-item ${statusClass}" onclick="checkService('${service.key}')">
                <div>${service.icon}</div>
                <div style="font-size: 11px;">${service.name}</div>
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
            body: JSON.stringify({
                message: message,
                context: {
                    mode: 'developer',
                    system_status: systemStatus,
                    chat_history: chatHistory.slice(-5)
                }
            })
        });
        
        if (response.ok) {
            const data = await response.json();
            addMessage(data.response, 'claude', data.actions);
            
            // Execute any returned actions
            if (data.actions) {
                executeActions(data.actions);
            }
        } else {
            throw new Error('Chat request failed');
        }
    } catch (error) {
        console.error('Chat error:', error);
        addMessage('Error: Could not connect to Claude. Check the backend.', 'claude');
    } finally {
        isProcessing = false;
    }
}

function addMessage(content, sender, actions = null) {
    const container = document.getElementById('chatMessages');
    const messageDiv = document.createElement('div');
    messageDiv.className = `message ${sender}`;
    
    const icon = sender === 'claude' ? '🧠' : '👤';
    messageDiv.innerHTML = `
        <span class="message-icon">${icon}</span>
        <div class="message-content">${formatContent(content)}</div>
    `;
    
    // Add action buttons if provided
    if (actions && actions.length > 0) {
        const actionsDiv = document.createElement('div');
        actionsDiv.className = 'message-actions';
        actionsDiv.style.marginTop = '10px';
        
        actions.forEach(action => {
            const btn = document.createElement('button');
            btn.className = 'action-btn';
            btn.textContent = action.label;
            btn.onclick = () => executeAction(action);
            actionsDiv.appendChild(btn);
        });
        
        messageDiv.appendChild(actionsDiv);
    }
    
    container.appendChild(messageDiv);
    container.scrollTop = container.scrollHeight;
    
    // Store in history
    chatHistory.push({ content, sender, timestamp: new Date() });
}

function formatContent(content) {
    // Convert code blocks
    content = content.replace(/```(\w+)?\n([\s\S]*?)```/g, 
        '<pre style="background: #1e293b; color: #e2e8f0; padding: 10px; border-radius: 6px; margin: 10px 0; overflow-x: auto;"><code>$2</code></pre>');
    
    // Convert inline code
    content = content.replace(/`([^`]+)`/g, 
        '<code style="background: #e5e7eb; padding: 2px 6px; border-radius: 3px;">$1</code>');
    
    return content;
}

function handleInputKeyDown(event) {
    if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        sendMessage();
    }
}

async function quickAction(action) {
    const actions = {
        systemCheck: "Run a complete system health check and report any issues",
        fixIssues: "Scan for and automatically fix any detected system issues",
        backup: "Create a full system backup with timestamp",
        githubSync: "Sync all changes to GitHub repository"
    };
    
    const message = actions[action];
    if (message) {
        document.getElementById('messageInput').value = message;
        sendMessage();
    }
}

async function checkService(service) {
    const message = `Check the ${service} service status and fix any issues`;
    document.getElementById('messageInput').value = message;
    sendMessage();
}

async function loadRecentTasks() {
    const container = document.getElementById('recentTasks');
    try {
        const response = await fetch(`${API_BASE}/developer/tasks/recent`);
        if (response.ok) {
            const tasks = await response.json();
            container.innerHTML = tasks.slice(0, 5).map(task => `
                <div class="task-item">
                    ${task.status === 'completed' ? '✅' : '⏳'} ${task.title}
                </div>
            `).join('');
        }
    } catch (error) {
        container.innerHTML = '<div class="task-item">No recent tasks</div>';
    }
}

async function loadPerformanceMetrics() {
    const container = document.getElementById('performanceMetrics');
    try {
        const response = await fetch(`${API_BASE}/developer/metrics`);
        if (response.ok) {
            const metrics = await response.json();
            container.innerHTML = `
                <div class="metric-item">
                    <span>CPU Usage</span>
                    <span class="metric-value">${metrics.cpu || '0'}%</span>
                </div>
                <div class="metric-item">
                    <span>Memory</span>
                    <span class="metric-value">${metrics.memory || '0'}%</span>
                </div>
                <div class="metric-item">
                    <span>Disk</span>
                    <span class="metric-value">${metrics.disk || '0'}%</span>
                </div>
                <div class="metric-item">
                    <span>Uptime</span>
                    <span class="metric-value">${metrics.uptime || '0h'}</span>
                </div>
            `;
        }
    } catch (error) {
        container.innerHTML = '<div>Metrics unavailable</div>';
    }
}

function handleFileUpload(event) {
    const files = event.target.files;
    Array.from(files).forEach(file => {
        const reader = new FileReader();
        reader.onload = (e) => {
            const content = e.target.result;
            addMessage(`📎 Uploaded: ${file.name}`, 'user');
            
            // Send for analysis
            document.getElementById('messageInput').value = 
                `Analyze this file and suggest improvements:\n\`\`\`\n${content.substring(0, 1000)}...\n\`\`\``;
            sendMessage();
        };
        reader.readAsText(file);
    });
}

function triggerFileInput() {
    document.getElementById('fileInput').click();
}

function toggleVoiceInput() {
    addMessage('🎤 Voice input will be available soon!', 'claude');
}

function executeAction(action) {
    console.log('Executing action:', action);
    // Implementation for action execution
}

function executeActions(actions) {
    actions.forEach(action => {
        console.log('Auto-executing:', action);
        // Auto-execute safe actions
    });
}
