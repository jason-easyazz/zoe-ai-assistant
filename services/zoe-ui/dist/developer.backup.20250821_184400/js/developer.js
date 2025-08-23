// Developer Dashboard JavaScript
// Dynamically determine API URL based on current location
const API_BASE = window.location.hostname === 'localhost' 
    ? 'http://localhost:8000' 
    : `http://${window.location.hostname}:8000`;

console.log('🚀 Zoe Developer Dashboard Initializing...');
console.log('📡 API Base URL:', API_BASE);

// Initialize on page load
document.addEventListener('DOMContentLoaded', () => {
    console.log('📋 DOM Loaded - Starting initialization');
    initializeDashboard();
});

async function initializeDashboard() {
    console.log('🔧 Initializing dashboard components...');
    
    // Initial status check
    await checkSystemStatus();
    
    // Set up chat interface
    setupChatInterface();
    
    // Load AI usage
    await loadAIUsage();
    
    // Set up periodic updates
    setInterval(checkSystemStatus, 30000);
    setInterval(loadAIUsage, 60000);
    
    // Add welcome message
    addMessage('ai', 'Welcome to Zoe AI Developer Dashboard! How can I help you today?', 'system');
}

async function checkSystemStatus() {
    console.log('🔍 Checking system status...');
    const statusEl = document.getElementById('system-status');
    
    try {
        const response = await fetch(`${API_BASE}/api/developer/status`);
        console.log('Status response:', response.status);
        
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = await response.json();
        console.log('Status data:', data);
        
        updateStatusDisplay(data);
        
        // Try to get detailed system status
        try {
            const sysResponse = await fetch(`${API_BASE}/api/developer/system/status`);
            if (sysResponse.ok) {
                const sysData = await sysResponse.json();
                updateSystemMetrics(sysData);
            }
        } catch (e) {
            console.log('System status endpoint not available');
        }
        
    } catch (error) {
        console.error('❌ Failed to check system status:', error);
        statusEl.innerHTML = `<div class="status-error">Connection Error: ${error.message}</div>`;
    }
}

function updateStatusDisplay(data) {
    const statusEl = document.getElementById('system-status');
    if (!statusEl) return;
    
    const statusClass = data.status === 'operational' ? 'status-good' : 'status-error';
    const aiModels = data.ai_models || {};
    const activeModels = Object.entries(aiModels)
        .filter(([_, active]) => active)
        .map(([name, _]) => name);
    
    statusEl.innerHTML = `
        <div class="${statusClass}">
            System: ${data.status.toUpperCase()}
        </div>
        <div class="metric">
            <strong>API:</strong> ${data.services?.api || 'unknown'}
        </div>
        <div class="metric">
            <strong>AI:</strong> ${data.services?.ai || 'unknown'}
        </div>
        <div class="metric">
            <strong>Models:</strong> ${activeModels.join(', ') || 'none'}
        </div>
    `;
}

function updateSystemMetrics(data) {
    const metricsEl = document.getElementById('system-metrics');
    if (!metricsEl || !data.memory) return;
    
    const memTotal = parseInt(data.memory.MemTotal) / 1024 / 1024;
    const memAvailable = parseInt(data.memory.MemAvailable) / 1024 / 1024;
    const memUsed = memTotal - memAvailable;
    const memPercent = ((memUsed / memTotal) * 100).toFixed(1);
    
    metricsEl.innerHTML = `
        <h3>💾 Memory</h3>
        <div class="metric">
            Used: ${memUsed.toFixed(1)}GB / ${memTotal.toFixed(1)}GB (${memPercent}%)
        </div>
    `;
}

async function loadAIUsage() {
    console.log('📊 Loading AI usage...');
    const usageEl = document.getElementById('ai-usage');
    
    try {
        const response = await fetch(`${API_BASE}/api/developer/ai/usage`);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = await response.json();
        
        usageEl.innerHTML = `
            <div class="metric">
                <strong>Daily Budget:</strong> $${data.daily_budget}
            </div>
            <div class="metric">
                <strong>Used Today:</strong> $${data.used_today}
            </div>
            <div class="metric">
                <strong>Remaining:</strong> $${data.remaining}
            </div>
        `;
    } catch (error) {
        console.error('Failed to load AI usage:', error);
        usageEl.innerHTML = '<div class="metric">Unable to load usage data</div>';
    }
}

function setupChatInterface() {
    console.log('💬 Setting up chat interface...');
    const form = document.getElementById('chat-form');
    const input = document.getElementById('chat-input');
    
    if (!form || !input) {
        console.error('Chat interface elements not found');
        return;
    }
    
    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        
        const message = input.value.trim();
        if (!message) return;
        
        console.log('📤 Sending message:', message);
        
        // Add user message
        addMessage('user', message);
        input.value = '';
        
        // Show typing indicator
        const typingId = showTypingIndicator();
        
        try {
            const response = await fetch(`${API_BASE}/api/developer/chat`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ message })
            });
            
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const data = await response.json();
            
            console.log('📥 Received response:', data);
            
            // Remove typing indicator
            removeTypingIndicator(typingId);
            
            // Add AI response
            addMessage('ai', data.response, data.model_used);
            
        } catch (error) {
            console.error('Chat error:', error);
            removeTypingIndicator(typingId);
            addMessage('error', `Error: ${error.message}`);
        }
    });
    
    // Focus input
    input.focus();
}

function addMessage(type, content, model = null) {
    const messagesEl = document.getElementById('chat-messages');
    if (!messagesEl) return;
    
    const messageDiv = document.createElement('div');
    messageDiv.className = `message ${type}-message`;
    
    const timestamp = new Date().toLocaleTimeString();
    const sender = type === 'user' ? 'You' : type === 'error' ? 'Error' : `AI${model ? ' (' + model + ')' : ''}`;
    
    messageDiv.innerHTML = `
        <div style="font-weight: bold; margin-bottom: 5px;">
            ${sender} <span style="font-weight: normal; opacity: 0.7; font-size: 0.9em;">${timestamp}</span>
        </div>
        <div>${escapeHtml(content)}</div>
    `;
    
    messagesEl.appendChild(messageDiv);
    messagesEl.scrollTop = messagesEl.scrollHeight;
}

function showTypingIndicator() {
    const messagesEl = document.getElementById('chat-messages');
    if (!messagesEl) return null;
    
    const typingDiv = document.createElement('div');
    const id = 'typing-' + Date.now();
    typingDiv.id = id;
    typingDiv.className = 'typing-indicator';
    typingDiv.innerHTML = '🤖 AI is thinking...';
    
    messagesEl.appendChild(typingDiv);
    messagesEl.scrollTop = messagesEl.scrollHeight;
    
    return id;
}

function removeTypingIndicator(id) {
    if (!id) return;
    const element = document.getElementById(id);
    if (element) element.remove();
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// Quick action functions
window.checkSystem = async function() {
    console.log('🔍 Running system check...');
    await checkSystemStatus();
    await loadAIUsage();
    alert('✅ System check complete!');
};

window.testAI = async function() {
    console.log('🧪 Testing AI models...');
    const input = document.getElementById('chat-input');
    input.value = 'Test all AI models - respond with which model you are using';
    document.getElementById('chat-form').dispatchEvent(new Event('submit'));
};

window.viewLogs = async function() {
    console.log('📋 Fetching logs...');
    const input = document.getElementById('chat-input');
    input.value = 'Show me the last 10 system log entries';
    document.getElementById('chat-form').dispatchEvent(new Event('submit'));
};

window.createBackup = async function() {
    console.log('💾 Creating backup...');
    try {
        const response = await fetch(`${API_BASE}/api/developer/backup`, {
            method: 'POST'
        });
        const data = await response.json();
        alert(data.message || '✅ Backup initiated');
    } catch (error) {
        alert('❌ Backup failed: ' + error.message);
    }
};

window.runDiagnostics = async function() {
    console.log('🔧 Running diagnostics...');
    const input = document.getElementById('chat-input');
    input.value = 'Run a complete system diagnostic and report any issues';
    document.getElementById('chat-form').dispatchEvent(new Event('submit'));
};

console.log('✅ Developer Dashboard JavaScript loaded successfully');
