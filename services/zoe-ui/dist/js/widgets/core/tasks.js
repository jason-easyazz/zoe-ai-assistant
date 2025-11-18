/**
 * Tasks Widget
 * Displays today's tasks from various lists
 * Version: 1.0.0
 */

class TasksWidget extends WidgetModule {
    constructor() {
        super('tasks', {
            version: '1.0.0',
            defaultSize: 'size-small',
            updateInterval: 60000 // Update every minute
        });
        this.userId = null;
    }
    
    getTemplate() {
        return `
            <div class="widget-header">
                <div class="widget-title">✅ Tasks</div>
                <div class="widget-badge" id="tasksCount">0</div>
            </div>
            <div class="widget-content">
                <div id="tasksContent" class="loading-widget">
                    <div class="spinner"></div>
                    Loading tasks...
                </div>
            </div>
        `;
    }
    
    init(element) {
        super.init(element);
        
        // Get user session
        const session = window.zoeAuth?.getCurrentSession();
        this.userId = session?.user_info?.user_id || session?.user_id || 'default';
        
        // Load tasks immediately after initialization
        this.loadTasks();
    }
    
    update() {
        // Re-fetch user ID in case session loaded after init
        const session = window.zoeAuth?.getCurrentSession();
        this.userId = session?.user_info?.user_id || session?.user_id || 'default';
        this.loadTasks();
    }
    
    async loadTasks() {
        try {
            const response = await fetch(`/api/lists/tasks?user_id=${this.userId}`);
            const data = await response.json();
            
            // Handle the response structure from lists API
            let tasks = [];
            if (data.tasks && Array.isArray(data.tasks)) {
                // Transform the tasks to match expected format
                tasks = data.tasks.map(task => ({
                    id: task.id,
                    title: task.text,
                    name: task.text,
                    status: 'pending',
                    priority: task.priority || 'medium',
                    list_name: task.list_name,
                    category: task.list_category
                }));
            } else if (Array.isArray(data)) {
                tasks = data;
            }
            
            this.updateTasks(tasks);
        } catch (error) {
            console.error('Failed to load tasks:', error);
            this.updateTasks([]);
        }
    }
    
    updateTasks(tasks) {
        const content = this.element.querySelector('#tasksContent');
        const count = this.element.querySelector('#tasksCount');
        
        // Remove loading widget class
        if (content) {
            content.classList.remove('loading-widget');
        }
        
        // Ensure tasks is an array
        if (!Array.isArray(tasks)) {
            tasks = [];
        }
        
        const pendingTasks = tasks.filter(task => task.status !== 'completed' && task.status !== 'done');
        
        if (count) {
            count.textContent = pendingTasks.length;
        }
        
        if (content) {
            if (pendingTasks.length === 0) {
                content.innerHTML = '<div style="text-align: center; color: #666; font-style: italic;">All tasks completed! 🎉</div>';
                return;
            }
            
            content.innerHTML = pendingTasks.slice(0, 5).map(task => `
                <div class="task-item ${task.category || 'personal'}" style="padding: 12px; border-radius: 8px; margin-bottom: 8px; cursor: grab; transition: all 0.3s; display: flex; align-items: center; gap: 10px; box-shadow: 0 1px 3px rgba(0, 0, 0, 0.1);">
                    <input type="checkbox" class="task-checkbox" onchange="toggleTask(this, '${task.id}')" style="width: 18px; height: 18px; cursor: pointer;">
                    <div class="task-text" style="flex: 1; font-size: 14px; font-weight: 500;">${task.title || task.name || 'Untitled Task'}</div>
                    <div style="font-size: 12px; color: ${this.getPriorityColor(task.priority)}; font-weight: 600;">${task.priority || 'Medium'}</div>
                </div>
            `).join('');
        }
    }
    
    getPriorityColor(priority) {
        switch(priority?.toLowerCase()) {
            case 'high': return '#dc2626';
            case 'medium': return '#ea580c';
            case 'low': return '#22c55e';
            default: return '#666';
        }
    }
}

// Expose to global scope for WidgetManager
window.TasksWidget = TasksWidget;

// Register widget
if (typeof WidgetRegistry !== 'undefined') {
    WidgetRegistry.register('tasks', new TasksWidget());
}




