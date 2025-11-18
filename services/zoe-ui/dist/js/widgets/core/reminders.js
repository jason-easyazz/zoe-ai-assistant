/**
 * Reminders Widget
 * Displays and manages time-based reminders with priority filtering
 * Version: 1.0.0
 */

class RemindersWidget extends WidgetModule {
    constructor() {
        super('reminders', {
            version: '1.0.0',
            defaultSize: 'size-small',
            updateInterval: 60000 // Update every minute
        });
        this.reminders = [];
        this.currentFilter = 'all';
        this.userId = null;
    }
    
    getTemplate() {
        return `
            <div class="widget-header">
                <div class="widget-title">🔔 Reminders</div>
                <div class="widget-badge" id="reminders-count">0</div>
            </div>
            <div class="widget-content" style="flex: 1; overflow-y: auto; display: flex; flex-direction: column;">
                <div style="display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 12px; padding-bottom: 8px; border-bottom: 1px solid rgba(255, 255, 255, 0.3);">
                    <button class="filter-btn active" data-priority="all" onclick="event.stopPropagation(); this.closest('.widget').widgetInstance.setFilter('all')" style="padding: 4px 8px; border: none; background: rgba(123, 97, 255, 0.2); border-radius: 6px; cursor: pointer; font-size: 11px; font-weight: 500; color: #7B61FF;">All</button>
                    <button class="filter-btn" data-priority="critical" onclick="event.stopPropagation(); this.closest('.widget').widgetInstance.setFilter('critical')" style="padding: 4px 8px; border: none; background: rgba(255, 255, 255, 0.6); border-radius: 6px; cursor: pointer; font-size: 11px; font-weight: 500; color: #666;">🔴 Critical</button>
                    <button class="filter-btn" data-priority="high" onclick="event.stopPropagation(); this.closest('.widget').widgetInstance.setFilter('high')" style="padding: 4px 8px; border: none; background: rgba(255, 255, 255, 0.6); border-radius: 6px; cursor: pointer; font-size: 11px; font-weight: 500; color: #666;">🟠 High</button>
                    <button class="filter-btn" data-priority="medium" onclick="event.stopPropagation(); this.closest('.widget').widgetInstance.setFilter('medium')" style="padding: 4px 8px; border: none; background: rgba(255, 255, 255, 0.6); border-radius: 6px; cursor: pointer; font-size: 11px; font-weight: 500; color: #666;">🔵 Medium</button>
                    <button class="filter-btn" data-priority="low" onclick="event.stopPropagation(); this.closest('.widget').widgetInstance.setFilter('low')" style="padding: 4px 8px; border: none; background: rgba(255, 255, 255, 0.6); border-radius: 6px; cursor: pointer; font-size: 11px; font-weight: 500; color: #666;">⚪ Low</button>
                </div>
                <div id="reminders-items" style="flex: 1; overflow-y: auto;"></div>
            </div>
        `;
    }
    
    init(element) {
        super.init(element);
        
        const session = window.zoeAuth?.getCurrentSession();
        this.userId = session?.user_info?.user_id || session?.user_id || 'default';
        
        this.element.widgetInstance = this;
        this.loadReminders();
    }
    
    update() {
        // Re-fetch user ID in case session loaded after init
        const session = window.zoeAuth?.getCurrentSession();
        this.userId = session?.user_info?.user_id || session?.user_id || 'default';
        this.loadReminders();
    }
    
    async loadReminders() {
        try {
            const session = window.zoeAuth?.getCurrentSession();
            const headers = session ? { 'X-Session-ID': session.session_id } : {};
            
            const response = await apiRequest('/reminders/', { headers });
            this.reminders = response.reminders || [];
            this.render();
        } catch (error) {
            console.error('Failed to load reminders:', error);
            this.reminders = [];
            this.render();
        }
    }
    
    setFilter(priority) {
        this.currentFilter = priority;
        
        // Update filter button styles
        const filterBtns = this.element.querySelectorAll('.filter-btn');
        filterBtns.forEach(btn => {
            if (btn.dataset.priority === priority) {
                btn.classList.add('active');
                btn.style.background = 'rgba(123, 97, 255, 0.2)';
                btn.style.color = '#7B61FF';
            } else {
                btn.classList.remove('active');
                btn.style.background = 'rgba(255, 255, 255, 0.6)';
                btn.style.color = '#666';
            }
        });
        
        this.render();
    }
    
    render() {
        const container = this.element.querySelector('#reminders-items');
        const countBadge = this.element.querySelector('#reminders-count');
        
        // Filter reminders
        const filteredReminders = this.currentFilter === 'all' 
            ? this.reminders 
            : this.reminders.filter(r => r.priority === this.currentFilter);
        
        if (countBadge) {
            countBadge.textContent = this.reminders.length;
        }
        
        if (!container) return;
        
        if (filteredReminders.length === 0) {
            container.innerHTML = '<div style="text-align: center; color: #666; font-size: 12px; padding: 20px;">No reminders found</div>';
            return;
        }
        
        container.innerHTML = filteredReminders.map(reminder => {
            const priorityColors = {
                critical: { bg: 'rgba(239, 68, 68, 0.05)', border: '#EF4444' },
                high: { bg: 'rgba(245, 158, 11, 0.05)', border: '#F59E0B' },
                medium: { bg: 'rgba(59, 130, 246, 0.05)', border: '#3B82F6' },
                low: { bg: 'rgba(107, 114, 128, 0.05)', border: '#6B7280' }
            };
            
            const colors = priorityColors[reminder.priority] || priorityColors.medium;
            const timeDisplay = reminder.due_time ? this.formatTime(reminder.due_time) : 'No time';
            const dateDisplay = reminder.due_date ? this.formatReminderDate(reminder.due_date) : 'No date';
            
            return `
                <div class="list-item" data-id="${reminder.id}" 
                     draggable="true"
                     style="display: flex; align-items: flex-start; gap: 8px; padding: 10px; border-bottom: 1px solid rgba(255, 255, 255, 0.2); border-left: 3px solid ${colors.border}; background: ${colors.bg}; margin-bottom: 4px; border-radius: 4px; cursor: grab;">
                    <div class="item-checkbox" 
                         onclick="event.stopPropagation(); this.closest('.widget').widgetInstance.completeReminder(${reminder.id})"
                         style="width: 20px; height: 20px; border: 2px solid ${colors.border}; border-radius: 4px; cursor: pointer; transition: all 0.2s ease; display: flex; align-items: center; justify-content: center; flex-shrink: 0; background: white; margin-top: 2px;"></div>
                    <div style="flex: 1; min-width: 0;">
                        <div style="font-size: 13px; color: #333; font-weight: 500; margin-bottom: 4px;">${reminder.title}</div>
                        <div style="font-size: 11px; color: #666;">⏰ ${timeDisplay} 📅 ${dateDisplay}</div>
                    </div>
                    <button class="item-delete" 
                            onclick="event.stopPropagation(); this.closest('.widget').widgetInstance.deleteReminder(${reminder.id})"
                            title="Delete"
                            style="width: 24px; height: 24px; border: none; background: rgba(244, 67, 54, 0.1); color: #f44336; border-radius: 4px; cursor: pointer; font-size: 12px;">×</button>
                </div>
            `;
        }).join('');
        
        // Setup drag for calendar integration
        container.querySelectorAll('.list-item[draggable]').forEach(item => {
            item.addEventListener('dragstart', (e) => {
                const reminderId = parseInt(item.dataset.id);
                const reminder = this.reminders.find(r => r.id === reminderId);
                if (reminder) {
                    e.dataTransfer.setData('application/json', JSON.stringify({
                        type: 'reminder',
                        id: reminderId,
                        title: reminder.title,
                        description: reminder.description,
                        category: reminder.category,
                        priority: reminder.priority
                    }));
                    e.dataTransfer.effectAllowed = 'copy';
                }
            });
        });
    }
    
    formatTime(timeStr) {
        if (!timeStr) return 'No time';
        return timeStr.substring(0, 5);
    }
    
    formatReminderDate(dateString) {
        const date = new Date(dateString);
        const today = new Date();
        const tomorrow = new Date(today);
        tomorrow.setDate(tomorrow.getDate() + 1);

        if (date.toDateString() === today.toDateString()) {
            return 'Today';
        } else if (date.toDateString() === tomorrow.toDateString()) {
            return 'Tomorrow';
        } else {
            return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
        }
    }
    
    async completeReminder(reminderId) {
        try {
            const session = window.zoeAuth?.getCurrentSession();
            const headers = session ? { 'X-Session-ID': session.session_id } : {};
            
            await apiRequest(`/reminders/${reminderId}/acknowledge`, {
                method: 'POST',
                headers
            });
            
            this.reminders = this.reminders.filter(r => r.id !== reminderId);
            this.render();
            
            if (typeof showNotification === 'function') {
                showNotification('Reminder completed', 'success');
            }
        } catch (error) {
            console.error('Failed to complete reminder:', error);
        }
    }
    
    async deleteReminder(reminderId) {
        try {
            const session = window.zoeAuth?.getCurrentSession();
            const headers = session ? { 'X-Session-ID': session.session_id } : {};
            
            await apiRequest(`/reminders/${reminderId}`, {
                method: 'DELETE',
                headers
            });
            
            this.reminders = this.reminders.filter(r => r.id !== reminderId);
            this.render();
            
            if (typeof showNotification === 'function') {
                showNotification('Reminder deleted', 'success');
            }
        } catch (error) {
            console.error('Failed to delete reminder:', error);
        }
    }
}

// Expose to global scope for WidgetManager
window.RemindersWidget = RemindersWidget;

// Register widget
if (typeof WidgetRegistry !== 'undefined') {
    WidgetRegistry.register('reminders', new RemindersWidget());
}






