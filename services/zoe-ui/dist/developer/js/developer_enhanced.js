// Enhanced developer.js - Preserves existing design
const API_BASE = window.location.hostname === 'localhost' 
    ? 'http://localhost:8000' 
    : `http://${window.location.hostname}:8000`;

let currentPlan = null;
let currentConversationId = null;
let originalRequest = '';

// Update time display (existing function)
function updateTime() {
    const timeEl = document.getElementById('currentTime');
    if (timeEl) {
        timeEl.textContent = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    }
}
updateTime();
setInterval(updateTime, 1000);

// Handle chat input (existing function enhanced)
function handleChatKey(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
    }
}

// Enhanced message sending with plan support
async function sendMessage() {
    const input = document.getElementById('chatInput');
    const message = input.value.trim();
    if (!message) return;

    originalRequest = message;
    addMessage(message, 'user');
    input.value = '';

    try {
        const response = await fetch(`${API_BASE}/api/developer/chat`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message })
        });

        if (response.ok) {
            const data = await response.json();
            
            // Add formatted response to chat
            addMessage(data.response, 'zack');
            
            // Display plan in artifact panel (now plan panel)
            if (data.plan) {
                displayPlan(data.plan);
                currentPlan = data.plan;
                currentConversationId = data.conversation_id;
                
                // Update the artifact title to show it's a plan
                const titleEl = document.getElementById('artifactTitle');
                if (titleEl) {
                    titleEl.textContent = '📋 Strategic Plan';
                }
            }
        } else {
            throw new Error('API error');
        }
    } catch (error) {
        addMessage('Connection error. Please check the backend.', 'zack');
    }
}

// Enhanced message display with proper markdown
function addMessage(text, sender) {
    const messagesDiv = document.getElementById('chatMessages');
    const messageDiv = document.createElement('div');
    messageDiv.className = `message ${sender}`;
    
    // Format the text with proper markdown handling
    let formattedHtml = text;
    
    if (sender === 'zack') {
        // Convert markdown to HTML
        formattedHtml = text
            // Bold text
            .replace(/\*\*(.*?)\*\*/g, '<strong style="color: #1a1a1a;">$1</strong>')
            // Headers (if any)
            .replace(/^### (.*?)$/gm, '<h4 style="color: #1e40af; margin: 8px 0 4px 0;">$1</h4>')
            .replace(/^## (.*?)$/gm, '<h3 style="color: #0f172a; margin: 10px 0 6px 0;">$1</h3>')
            // Bullets
            .replace(/^[•\-] (.*?)$/gm, '<div style="margin: 4px 0; padding-left: 16px;">• $1</div>')
            // Status indicators
            .replace(/✅/g, '<span style="color: #22c55e;">✅</span>')
            .replace(/⚠️/g, '<span style="color: #f59e0b;">⚠️</span>')
            .replace(/❌/g, '<span style="color: #ef4444;">❌</span>')
            // Line breaks
            .replace(/\n\n/g, '<div style="height: 8px;"></div>')
            .replace(/\n/g, '<br>');
    }
    
    messageDiv.innerHTML = `
        <div class="message-icon">${sender === 'user' ? '👤' : '🧠'}</div>
        <div class="message-content">${formattedHtml}</div>
    `;
    
    messagesDiv.appendChild(messageDiv);
    messagesDiv.scrollTop = messagesDiv.scrollHeight;
}

// Display plan in the artifact panel (keep existing styling)
function displayPlan(plan) {
    const contentDiv = document.getElementById('artifactContent');
    if (!contentDiv) return;
    
    let html = '<div style="color: #fff; font-family: Monaco, Consolas, monospace; line-height: 1.6;">';
    
    // Plan title
    html += `<div style="color: #5AE0E0; font-size: 16px; margin-bottom: 16px;">
        📋 ${plan.title}
    </div>`;
    
    // Plan type and metadata
    html += `<div style="background: rgba(255,255,255,0.1); padding: 12px; border-radius: 6px; margin-bottom: 16px;">
        <div style="color: #7B61FF;">Type: ${plan.type}</div>
        <div style="color: #7B61FF;">Duration: ${plan.metadata?.estimated_time || 'TBD'}</div>
        <div style="color: #7B61FF;">Risk: ${plan.metadata?.risk_level || 'low'}</div>
    </div>`;
    
    // Implementation phases
    html += '<div style="margin-bottom: 16px;">';
    html += '<div style="color: #5AE0E0; margin-bottom: 8px;">Implementation Phases:</div>';
    
    plan.phases.forEach(phase => {
        const stepColor = phase.status === 'pending' ? '#999' : '#5AE0E0';
        html += `<div style="padding: 8px; margin: 4px 0; background: rgba(255,255,255,0.05); border-radius: 4px;">
            <span style="color: #7B61FF; font-weight: bold;">Step ${phase.step}:</span>
            <span style="color: ${stepColor};"> ${phase.action}</span>
            <span style="color: #666; float: right;">[${phase.status}]</span>
        </div>`;
    });
    
    html += '</div>';
    
    // Add action note
    html += `<div style="color: #5AE0E0; margin-top: 20px; padding: 12px; background: rgba(122,97,255,0.1); border-radius: 6px;">
        💡 Use "Create Task" button to convert this plan into an executable task
    </div>`;
    
    html += '</div>';
    
    contentDiv.innerHTML = html;
}

// Quick prompt buttons (existing)
function quickPrompt(prompt) {
    document.getElementById('chatInput').value = prompt;
    sendMessage();
}

// Artifact/Plan actions
function copyArtifact() {
    if (currentPlan) {
        const planText = JSON.stringify(currentPlan, null, 2);
        navigator.clipboard.writeText(planText);
        alert('Plan copied to clipboard!');
    } else {
        alert('No plan to copy');
    }
}

function saveArtifact() {
    if (currentPlan) {
        const planText = JSON.stringify(currentPlan, null, 2);
        const blob = new Blob([planText], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `plan-${Date.now()}.json`;
        a.click();
        URL.revokeObjectURL(url);
    } else {
        alert('No plan to save');
    }
}

// Enhanced task creation from plan
async function createTask() {
    if (!currentPlan || !currentConversationId) {
        alert('No plan available. Please generate a plan first.');
        return;
    }
    
    const title = prompt('Task title:', currentPlan.title);
    if (!title) return;
    
    try {
        const response = await fetch(`${API_BASE}/api/developer/tasks/from-plan`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                title,
                description: `Created from plan: ${currentPlan.title}`,
                plan: currentPlan,
                conversation_id: currentConversationId,
                original_request: originalRequest
            })
        });
        
        if (response.ok) {
            const data = await response.json();
            addMessage(`✅ Task ${data.task_id} created successfully! Ready for code generation.`, 'zack');
            clearArtifact();
        } else {
            throw new Error('Failed to create task');
        }
    } catch (error) {
        alert('Error creating task: ' + error.message);
    }
}

// Clear artifact/plan panel
function clearArtifact() {
    currentPlan = null;
    currentConversationId = null;
    
    const titleEl = document.getElementById('artifactTitle');
    if (titleEl) {
        titleEl.textContent = 'Generated Code';
    }
    
    const contentEl = document.getElementById('artifactContent');
    if (contentEl) {
        contentEl.innerHTML = '<div style="color: #666; text-align: center; margin-top: 100px;">Generated code and scripts will appear here</div>';
    }
}

// Initialize on page load
document.addEventListener('DOMContentLoaded', function() {
    const input = document.getElementById('chatInput');
    if (input) {
        input.addEventListener('keydown', handleChatKey);
    }
    
    // Check for message in URL
    const params = new URLSearchParams(window.location.search);
    const urlMessage = params.get('message');
    if (urlMessage) {
        document.getElementById('chatInput').value = urlMessage;
        sendMessage();
    }
});
