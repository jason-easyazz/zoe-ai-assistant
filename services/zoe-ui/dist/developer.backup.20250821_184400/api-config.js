// API Configuration for Developer Dashboard
window.API_CONFIG = {
    base: '',  // Use relative paths
    endpoints: {
        health: '/api/health',
        dashboard: '/api/developer/dashboard',
        tasks: '/api/developer/tasks',
        systemOverview: '/api/developer/system/overview',
        systemDiagnostics: '/api/developer/system/diagnostics',
        chat: '/api/chat'
    }
};

// Override fetch to use correct base URL
window.apiCall = async function(endpoint, options = {}) {
    try {
        const response = await fetch(endpoint, options);
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        return await response.json();
    } catch (error) {
        console.error('API call failed:', error);
        throw error;
    }
};

// Fix the checkSystemStatus function
window.checkSystemStatus = async function() {
    try {
        const data = await apiCall('/api/health');
        console.log('System status:', data);
        return data;
    } catch (error) {
        console.error('Status check failed:', error);
        // Don't crash, just return default
        return { status: 'unknown' };
    }
};

// Fix loadTasks function
window.loadTasks = async function() {
    try {
        const data = await apiCall('/api/developer/tasks');
        console.log('Tasks loaded:', data.tasks);
        
        // Update UI with tasks
        const tasksEl = document.getElementById('developerTasks') || 
                       document.getElementById('recentTasks');
        if (tasksEl && data.tasks) {
            let html = '';
            data.tasks.forEach(task => {
                const icon = task.priority === 'high' ? '🔴' : 
                           task.priority === 'medium' ? '🟡' : '🟢';
                html += `
                    <div class="task-item">
                        <div class="task-icon">${icon}</div>
                        <div>${task.task_id}: ${task.title}</div>
                    </div>
                `;
            });
            tasksEl.innerHTML = html;
        }
        return data.tasks;
    } catch (error) {
        console.error('Failed to load tasks:', error);
        return [];
    }
};

console.log('✅ API configuration loaded');
