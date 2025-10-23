/**
 * Personal Todos Widget
 * Displays and manages personal todo items
 * Version: 1.0.0
 */

class PersonalWidget extends WidgetModule {
    constructor() {
        super('personal', {
            version: '1.0.0',
            defaultSize: 'size-small',
            updateInterval: null
        });
        this.items = [];
        this.archivedItems = [];
        this.userId = null;
    }
    
    getTemplate() {
        return `
            <div class="widget-header">
                <div class="widget-title">📝 Personal</div>
                <div class="widget-badge" 
                     id="personal-count" 
                     onclick="event.stopPropagation(); this.closest('.widget').widgetInstance.toggleArchiveView()"
                     style="cursor: pointer;"
                     title="Click to view archived items">0</div>
            </div>
            <div class="widget-content">
                <input type="text" 
                       class="add-item-input" 
                       placeholder="Add task..." 
                       id="personal-input">
                <div id="personal-items"></div>
                
                <!-- Archive viewer modal -->
                <div id="personal-archive-modal" style="display: none; position: absolute; top: 0; left: 0; right: 0; bottom: 0; background: rgba(255, 255, 255, 0.98); backdrop-filter: blur(10px); border-radius: 12px; padding: 16px; z-index: 1000; overflow-y: auto;">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; padding-bottom: 12px; border-bottom: 2px solid rgba(123, 97, 255, 0.2);">
                        <h3 style="margin: 0; font-size: 16px; color: #7B61FF;">📦 Archived Items</h3>
                        <button onclick="event.stopPropagation(); this.closest('.widget').widgetInstance.toggleArchiveView()" 
                                style="width: 32px; height: 32px; border: none; background: rgba(123, 97, 255, 0.1); color: #7B61FF; border-radius: 8px; cursor: pointer; font-size: 20px; font-weight: bold;">×</button>
                    </div>
                    <div id="personal-archive-items"></div>
                </div>
            </div>
        `;
    }
    
    init(element) {
        super.init(element);
        
        const session = window.zoeAuth?.getCurrentSession();
        this.userId = session?.user_info?.user_id || session?.user_id || 'default';
        
        const input = element.querySelector('#personal-input');
        if (input) {
            input.addEventListener('keypress', (e) => {
                if (e.key === 'Enter') {
                    this.addItem(input.value.trim());
                    input.value = '';
                }
            });
        }
        
        this.loadArchivedItems();
        this.loadItems();
        
        // Auto-archive completed items older than 24 hours (check every hour)
        this.archiveInterval = setInterval(() => {
            this.archiveOldCompletedItems();
        }, 60 * 60 * 1000); // Check every hour
        
        // Also check on init
        setTimeout(() => this.archiveOldCompletedItems(), 5000);
    }
    
    update() {
        this.loadItems();
    }
    
    async loadItems() {
        try {
            const response = await apiRequest(`/lists/personal_todos?user_id=${this.userId}`);
            const lists = response.lists || [];
            
            if (lists.length > 0) {
                this.items = lists[0].items || [];
            } else {
                this.items = [];
            }
            
            this.render();
        } catch (error) {
            console.error('Failed to load personal todos:', error);
            this.items = [];
            this.render();
        }
    }
    
    render() {
        const container = this.element.querySelector('#personal-items');
        const countBadge = this.element.querySelector('#personal-count');
        
        // Sort items: incomplete first, then completed (sorted by completion time)
        const sortedItems = [...this.items].sort((a, b) => {
            if (a.completed === b.completed) {
                // If both completed, sort by completion time (oldest first for archiving)
                if (a.completed && a.completedAt && b.completedAt) {
                    return a.completedAt - b.completedAt;
                }
                return 0;
            }
            return a.completed ? 1 : -1; // Incomplete items first
        });
        
        // Count only incomplete items
        const incompleteCount = this.items.filter(i => !i.completed).length;
        
        if (countBadge) {
            countBadge.textContent = incompleteCount;
        }
        
        if (!container) return;
        
        if (this.items.length === 0) {
            container.innerHTML = '<div style="text-align: center; color: #666; font-size: 12px; padding: 20px;">No tasks yet</div>';
            return;
        }
        
        container.innerHTML = sortedItems.map(item => `
            <div class="list-item" data-id="${item.id}" style="display: flex; align-items: center; gap: 8px; padding: 8px 0; border-bottom: 1px solid rgba(255, 255, 255, 0.2);">
                <div class="item-checkbox ${item.completed ? 'checked' : ''}" 
                     onclick="event.stopPropagation(); this.closest('.widget').widgetInstance.toggleItem(${item.id})"
                     style="width: 20px; height: 20px; border: 2px solid #7B61FF; border-radius: 4px; cursor: pointer; transition: all 0.2s ease; display: flex; align-items: center; justify-content: center; flex-shrink: 0; background: ${item.completed ? 'linear-gradient(135deg, #7B61FF 0%, #5AE0E0 100%)' : 'white'};">
                    ${item.completed ? '<span style="color: white; font-size: 12px; font-weight: bold;">✓</span>' : ''}
                </div>
                <div class="item-text" style="flex: 1; font-size: 13px; color: #333; ${item.completed ? 'text-decoration: line-through; opacity: 0.5;' : ''}">${item.text}</div>
                <button class="item-delete" 
                        onclick="event.stopPropagation(); this.closest('.widget').widgetInstance.deleteItem(${item.id})"
                        title="Delete"
                        style="width: 24px; height: 24px; border: none; background: rgba(244, 67, 54, 0.1); color: #f44336; border-radius: 4px; cursor: pointer; font-size: 12px; opacity: 0; transition: opacity 0.2s;">×</button>
            </div>
        `).join('');
        
        this.element.widgetInstance = this;
        
        container.querySelectorAll('.list-item').forEach(item => {
            item.addEventListener('mouseenter', () => {
                const deleteBtn = item.querySelector('.item-delete');
                if (deleteBtn) deleteBtn.style.opacity = '1';
            });
            item.addEventListener('mouseleave', () => {
                const deleteBtn = item.querySelector('.item-delete');
                if (deleteBtn) deleteBtn.style.opacity = '0';
            });
        });
    }
    
    async addItem(text) {
        if (!text) return;
        
        const newItem = {
            id: Date.now(),
            text: text,
            completed: false
        };
        
        this.items.push(newItem);
        this.render();
        await this.saveItems();
    }
    
    async toggleItem(itemId) {
        const item = this.items.find(i => i.id === itemId);
        if (item) {
            item.completed = !item.completed;
            
            // Add completion timestamp when marking as complete
            if (item.completed) {
                item.completedAt = Date.now();
            } else {
                // Remove timestamp if unchecking
                delete item.completedAt;
            }
            
            this.render();
            await this.saveItems();
        }
    }
    
    async deleteItem(itemId) {
        this.items = this.items.filter(i => i.id !== itemId);
        this.render();
        await this.saveItems();
    }
    
    async saveItems() {
        try {
            const response = await apiRequest(`/lists/personal_todos?user_id=${this.userId}`);
            const existingLists = response.lists || [];
            
            if (existingLists.length > 0) {
                const listId = existingLists[0].id;
                await apiRequest(`/lists/personal_todos/${listId}?user_id=${this.userId}`, {
                    method: 'PUT',
                    body: JSON.stringify({ items: this.items })
                });
            } else {
                await apiRequest(`/lists/personal_todos?user_id=${this.userId}`, {
                    method: 'POST',
                    body: JSON.stringify({
                        list_type: 'personal_todos',
                        category: 'personal',
                        name: 'Personal Todos',
                        items: this.items
                    })
                });
            }
        } catch (error) {
            console.error('Failed to save personal todos:', error);
        }
    }
    
    async archiveOldCompletedItems() {
        const now = Date.now();
        const oneDayMs = 24 * 60 * 60 * 1000;
        
        const itemsToArchive = [];
        const itemsToKeep = this.items.filter(item => {
            // Keep incomplete items
            if (!item.completed) return true;
            
            // Keep completed items less than 24 hours old
            if (item.completedAt && (now - item.completedAt) < oneDayMs) {
                return true;
            }
            
            // Archive items completed more than 24 hours ago
            console.log(`📦 Archiving completed item: ${item.text}`);
            item.archivedAt = now;
            itemsToArchive.push(item);
            return false;
        });
        
        // Only save if items were archived
        if (itemsToArchive.length > 0) {
            this.archivedItems = [...itemsToArchive, ...this.archivedItems];
            
            // Keep only last 50 archived items
            if (this.archivedItems.length > 50) {
                this.archivedItems = this.archivedItems.slice(0, 50);
            }
            
            this.items = itemsToKeep;
            this.render();
            await this.saveItems();
            await this.saveArchivedItems();
            console.log(`📦 Archived ${itemsToArchive.length} completed personal items`);
        }
    }
    
    toggleArchiveView() {
        const modal = this.element.querySelector('#personal-archive-modal');
        if (modal) {
            const isHidden = modal.style.display === 'none';
            modal.style.display = isHidden ? 'block' : 'none';
            
            if (isHidden) {
                this.renderArchive();
            }
        }
    }
    
    renderArchive() {
        const container = this.element.querySelector('#personal-archive-items');
        if (!container) return;
        
        if (this.archivedItems.length === 0) {
            container.innerHTML = '<div style="text-align: center; color: #666; font-size: 13px; padding: 40px;">No archived items</div>';
            return;
        }
        
        container.innerHTML = this.archivedItems.map(item => {
            const daysAgo = Math.floor((Date.now() - item.archivedAt) / (1000 * 60 * 60 * 24));
            
            return `
                <div class="archive-item" data-id="${item.id}" style="display: flex; align-items: center; gap: 8px; padding: 12px; margin-bottom: 8px; background: rgba(123, 97, 255, 0.05); border-radius: 8px; border-left: 3px solid rgba(123, 97, 255, 0.3);">
                    <div style="flex: 1;">
                        <div style="font-size: 13px; color: #333; text-decoration: line-through; opacity: 0.7;">${item.text}</div>
                        <div style="font-size: 11px; color: #999; margin-top: 4px;">Archived ${daysAgo} day${daysAgo !== 1 ? 's' : ''} ago</div>
                    </div>
                    <button class="restore-btn" 
                            onclick="event.stopPropagation(); this.closest('.widget').widgetInstance.restoreItem(${item.id})"
                            title="Restore"
                            style="padding: 6px 12px; border: none; background: rgba(123, 97, 255, 0.2); color: #7B61FF; border-radius: 6px; cursor: pointer; font-size: 12px; font-weight: 600;">↩</button>
                    <button class="delete-btn" 
                            onclick="event.stopPropagation(); this.closest('.widget').widgetInstance.deleteArchivedItem(${item.id})"
                            title="Delete permanently"
                            style="padding: 6px 12px; border: none; background: rgba(244, 67, 54, 0.1); color: #f44336; border-radius: 6px; cursor: pointer; font-size: 12px; font-weight: 600;">🗑️</button>
                </div>
            `;
        }).join('');
    }
    
    async restoreItem(itemId) {
        const item = this.archivedItems.find(i => i.id === itemId);
        if (item) {
            // Remove from archive
            this.archivedItems = this.archivedItems.filter(i => i.id !== itemId);
            
            // Restore to active items (uncompleted)
            item.completed = false;
            delete item.completedAt;
            delete item.archivedAt;
            this.items.push(item);
            
            this.render();
            this.renderArchive();
            await this.saveItems();
            await this.saveArchivedItems();
            console.log(`↩ Restored item: ${item.text}`);
        }
    }
    
    async deleteArchivedItem(itemId) {
        if (confirm('Permanently delete this archived item?')) {
            this.archivedItems = this.archivedItems.filter(i => i.id !== itemId);
            this.renderArchive();
            await this.saveArchivedItems();
            console.log(`🗑️ Permanently deleted archived item`);
        }
    }
    
    async saveArchivedItems() {
        try {
            localStorage.setItem(`personal_archived_${this.userId}`, JSON.stringify(this.archivedItems));
        } catch (error) {
            console.error('Failed to save archived items:', error);
        }
    }
    
    async loadArchivedItems() {
        try {
            const saved = localStorage.getItem(`personal_archived_${this.userId}`);
            if (saved) {
                this.archivedItems = JSON.parse(saved);
            }
        } catch (error) {
            console.error('Failed to load archived items:', error);
            this.archivedItems = [];
        }
    }
    
    destroy() {
        if (this.archiveInterval) {
            clearInterval(this.archiveInterval);
        }
        super.destroy();
    }
}

// Expose to global scope for WidgetManager
window.PersonalWidget = PersonalWidget;

// Register widget
if (typeof WidgetRegistry !== 'undefined') {
    WidgetRegistry.register('personal', new PersonalWidget());
}

