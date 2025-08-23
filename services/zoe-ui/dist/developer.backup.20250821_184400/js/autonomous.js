/**
 * Zoe Autonomous Developer System
 * Complete system control and task management
 */

// Initialize autonomous features
console.log('🤖 Autonomous Developer System loaded');

// Add autonomous functionality to existing dashboard
document.addEventListener('DOMContentLoaded', function() {
    // Add task panel to sidebar if it exists
    const sidebar = document.querySelector('.sidebar');
    if (sidebar) {
        const taskPanel = document.createElement('div');
        taskPanel.className = 'sidebar-card';
        taskPanel.innerHTML = `
            <div class="card-title">
                <span>📋</span> Developer Tasks
            </div>
            <div id="developerTasks">
                <div class="task-item">
                    <div class="task-icon success">📝</div>
                    <div>Loading tasks...</div>
                </div>
            </div>
            <button class="quick-btn" onclick="loadTasks()" style="width: 100%; margin-top: 10px;">
                <div class="quick-btn-icon">🔄</div>
                <div class="quick-btn-label">Refresh Tasks</div>
            </button>
        `;
        sidebar.appendChild(taskPanel);
    }
    
    // Load tasks on startup
    loadTasks();
});

// Load developer tasks
async function loadTasks() {
    try {
        const response = await fetch('/api/developer/tasks?status=pending');
        const data = await response.json();
        
        const tasksEl = document.getElementById('developerTasks');
        if (tasksEl && data.tasks) {
            let html = '';
            data.tasks.slice(0, 5).forEach(task => {
                const icon = task.priority === 'high' ? '🔴' : task.priority === 'medium' ? '🟡' : '🟢';
                html += `
                    <div class="task-item" style="cursor: pointer;" onclick="sendTaskToAI('${task.task_id}')">
                        <div class="task-icon">${icon}</div>
                        <div>
                            <strong>${task.task_id}</strong><br>
                            <small>${task.title}</small>
                        </div>
                    </div>
                `;
            });
            
            if (data.tasks.length === 0) {
                html = '<div class="task-item"><div class="task-icon">✅</div><div>No pending tasks</div></div>';
            }
            
            tasksEl.innerHTML = html;
        }
    } catch (error) {
        console.error('Failed to load tasks:', error);
    }
}

// Send task to AI
async function sendTaskToAI(taskId) {
    if (confirm(`Send task ${taskId} to Claude for autonomous execution?`)) {
        try {
            const response = await fetch(`/api/developer/tasks/${taskId}/execute`, {
                method: 'POST'
            });
            const result = await response.json();
            alert(`Task sent: ${result.message}`);
            loadTasks();
        } catch (error) {
            alert(`Error: ${error.message}`);
        }
    }
}

// Enhance existing chat to handle autonomous commands
const originalSendMessage = window.sendMessage;
if (originalSendMessage) {
    window.sendMessage = async function() {
        const input = document.getElementById('messageInput');
        const message = input.value.trim();
        
        // Check for autonomous keywords
        const autonomousKeywords = ['fix', 'deploy', 'debug', 'optimize', 'backup', 'complete task'];
        const isAutonomous = autonomousKeywords.some(keyword => 
            message.toLowerCase().includes(keyword)
        );
        
        if (isAutonomous) {
            // Add special handling for autonomous commands
            const chatMessages = document.getElementById('chatMessages');
            if (chatMessages) {
                const autoMsg = document.createElement('div');
                autoMsg.className = 'message claude';
                autoMsg.innerHTML = `<span class="message-icon">🤖</span>Analyzing system for autonomous execution...`;
                chatMessages.appendChild(autoMsg);
            }
        }
        
        // Call original function
        return originalSendMessage.apply(this, arguments);
    };
}

// Auto-refresh tasks every 30 seconds
setInterval(loadTasks, 30000);

console.log('✨ Autonomous features initialized!');
