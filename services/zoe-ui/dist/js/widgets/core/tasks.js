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
        // `GET /api/lists/tasks` returns {lists:[…]} with NO items — reading
        // data.tasks off it silently yielded [] and rendered a permanent
        // "All tasks completed!". Use the shared two-step helper instead.
        // (?user_id was dropped: identity comes from the session, and the
        // server has no user_id query param to read.)
        try {
            const { lists, ok } = await window.zoeFetchListsWithItems('tasks');
            if (!ok) { this.updateTasks(null); return; }

            // A list whose items failed to load is unknown, not empty — if any
            // are unknown, say so rather than under-reporting the task count.
            if (lists.some(list => list.items == null)) { this.updateTasks(null); return; }

            const tasks = [];
            lists.forEach((list) => {
                list.items
                    .filter(item => !item.completed)
                    .forEach(item => tasks.push({
                        id: item.id,
                        // list coordinates are required to write the item back
                        list_id: list.id,
                        list_type: list.list_type,
                        title: item.text,
                        name: item.text,
                        status: 'pending',
                        priority: item.priority || 'medium',
                        list_name: list.name,
                        category: item.category || list.list_type
                    }));
            });

            this.updateTasks(tasks);
        } catch (error) {
            console.error('Failed to load tasks:', error);
            this.updateTasks(null);
        }
    }
    
    updateTasks(tasks) {
        const content = this.element.querySelector('#tasksContent');
        const count = this.element.querySelector('#tasksCount');
        
        // Remove loading widget class
        if (content) {
            content.classList.remove('loading-widget');
        }
        
        // null = we could not load the tasks. That is NOT the same as having
        // none, and must never render as "All tasks completed!" — a backend
        // hiccup previously looked identical to an empty list.
        if (tasks === null) {
            if (count) count.textContent = '–';
            if (content) {
                content.innerHTML = '<div style="text-align: center; color: #666; font-style: italic;">Couldn\'t load your tasks</div>';
            }
            return;
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
                    <input type="checkbox" class="task-checkbox" data-task-id="${task.id}" data-list-id="${task.list_id}" data-list-type="${task.list_type}" style="width: 18px; height: 18px; cursor: pointer;">
                    <div class="task-text" style="flex: 1; font-size: 14px; font-weight: 500;">${task.title || task.name || 'Untitled Task'}</div>
                    <div style="font-size: 12px; color: ${this.getPriorityColor(task.priority)}; font-weight: 600;">${task.priority || 'Medium'}</div>
                </div>
            `).join('');

            // Delegated, because content.innerHTML is replaced on every refresh
            // and a per-element listener would not survive it. The previous
            // markup called a global toggleTask() that is defined NOWHERE in
            // the tree — it only went unnoticed because the widget never
            // rendered a task, so the checkbox was unreachable.
            if (!content.dataset.tasksToggleWired) {
                content.dataset.tasksToggleWired = '1';
                content.addEventListener('change', (ev) => {
                    const box = ev.target.closest('.task-checkbox');
                    if (box) this.toggleTask(box);
                });
            }
        }
    }

    /**
     * Complete/uncomplete a task item. Optimistic with rollback, the estate's
     * pattern (touch/home.html:2624-2627): flip immediately, write, and put it
     * back if the write fails rather than leaving the UI lying about state.
     */
    async toggleTask(checkbox) {
        const { taskId, listId, listType } = checkbox.dataset;
        if (!taskId || !listId || !listType) return;

        const completed = checkbox.checked;
        checkbox.disabled = true;
        try {
            await apiRequest(`/api/lists/${listType}/${listId}/items/${taskId}`, {
                method: 'PUT',
                body: JSON.stringify({ completed })
            });
            this.loadTasks();   // re-read rather than trusting local state
        } catch (err) {
            console.error('Failed to update task:', err);
            checkbox.checked = !completed;   // roll back
            if (typeof showNotification === 'function') {
                showNotification("Couldn't update that task", 'error');
            }
        } finally {
            checkbox.disabled = false;
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




