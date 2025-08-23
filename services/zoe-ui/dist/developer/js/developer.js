// Developer Dashboard JavaScript
const API_BASE = window.location.hostname === 'localhost' 
    ? 'http://localhost:8000' 
    : `http://${window.location.hostname}:8000`;

let aiConnected = false;
let isProcessing = false;

// Initialize
document.addEventListener('DOMContentLoaded', function() {
    console.log('🚀 Developer Dashboard Initializing...');
    updateTime();
    setInterval(updateTime, 60000);
    checkAIStatus();
    loadSystemStatus();
    document.getElementById('messageInput').focus();
});

// Time updates
function updateTime() {
    const now = new Date();
    document.getElementById('currentTime').textContent = now.toLocaleTimeString([], { 
        hour: 'numeric', minute: '2-digit', hour12: true 
    });
}

// Check AI status
async function checkAIStatus() {
    try {
        const response = await fetch(`${API_BASE}/api/developer/status`);
        const statusEl = document.getElementById('aiStatus');
        
        if (response.ok) {
            const data = await response.json();
            aiConnected = true;
            statusEl.className = 'status-indicator';
            statusEl.innerHTML = '<div class="status-dot"></div><span>AI Online</span>';
            
            // Check for Claude
            if (data.ai_models && data.ai_models.claude) {
                statusEl.innerHTML = '<div class="status-dot"></div><span>Claude Ready</span>';
            } else if (data.ai_models && (data.ai_models['llama3.2:3b'] || data.ai_models['llama3.2:1b'])) {
                statusEl.innerHTML = '<div class="status-dot"></div><span>Local AI</span>';
            }
        }
    } catch (error) {
        aiConnected = false;
        const statusEl = document.getElementById('aiStatus');
        statusEl.className = 'status-indicator offline';
        statusEl.innerHTML = '<div class="status-dot"></div><span>AI Offline</span>';
    }
}

// Load system status
async function loadSystemStatus() {
    try {
        const response = await fetch(`${API_BASE}/api/developer/system/status`);
        if (response.ok) {
            const data = await response.json();
            updateSystemStatusDisplay(data);
        }
    } catch (error) {
        console.error('Failed to load system status:', error);
    }
}

function updateSystemStatusDisplay(data) {
    const statusGrid = document.getElementById('systemStatus');
    const services = data.services || {};
    
    statusGrid.innerHTML = `
        <div class="status-item">
            <div class="status-icon status-${services.api === 'running' ? 'healthy' : 'error'}">
                ${services.api === 'running' ? '✅' : '❌'}
            </div>
            <div class="status-label">API</div>
        </div>
        <div class="status-item">
            <div class="status-icon status-${services.ai === 'connected' ? 'healthy' : 'error'}">
                ${services.ai === 'connected' ? '✅' : '❌'}
            </div>
            <div class="status-label">AI</div>
        </div>
        <div class="status-item">
            <div class="status-icon status-${data.memory ? 'warning' : 'healthy'}">
                ${data.memory ? '⚠️' : '✅'}
            </div>
            <div class="status-label">Memory</div>
        </div>
    `;
}

// Chat functionality
function handleInputKeyDown(event) {
    if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        sendMessage();
    }
}

async function sendMessage() {
    const input = document.getElementById('messageInput');
    const message = input.value.trim();
    
    if (!message || isProcessing) return;
    
    // Add user message
    addMessage(message, 'user');
    input.value = '';
    
    // Show processing
    isProcessing = true;
    updateAIActivity('thinking', 'Processing...');
    
    try {
        const response = await fetch(`${API_BASE}/api/developer/chat`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message })
        });
        
        if (response.ok) {
            const result = await response.json();
            addMessage(result.response, 'ai', result.model_used);
        } else {
            throw new Error('API error');
        }
    } catch (error) {
        console.error('Chat error:', error);
        addMessage('Sorry, I encountered an error. Please check the service status.', 'ai');
    } finally {
        isProcessing = false;
        updateAIActivity('idle', 'Ready to help');
    }
}

function addMessage(content, sender, model) {
    const messagesContainer = document.getElementById('chatMessages');
    const messageDiv = document.createElement('div');
    messageDiv.className = `message ${sender}`;
    
    const icon = sender === 'ai' ? '🤖' : '👤';
    const modelInfo = model ? ` (${model})` : '';
    
    messageDiv.innerHTML = `<span class="message-icon">${icon}</span>${content}${modelInfo}`;
    
    messagesContainer.appendChild(messageDiv);
    messagesContainer.scrollTop = messagesContainer.scrollHeight;
}

function updateAIActivity(status, message) {
    const activityEl = document.getElementById('aiActivity');
    const icon = status === 'thinking' ? '🤔' : '💤';
    
    activityEl.innerHTML = `
        <div class="activity-item">
            <div class="activity-icon">${icon}</div>
            <div>${message}</div>
        </div>
    `;
}

function quickAction(action) {
    const actions = {
        'check': "Check system status",
        'test': "Test AI models"
    };
    
    const message = actions[action];
    if (message) {
        document.getElementById('messageInput').value = message;
        sendMessage();
    }
}
