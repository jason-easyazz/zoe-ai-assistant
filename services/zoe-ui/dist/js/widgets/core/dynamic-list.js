/**
 * Dynamic List Widget
 * Generic widget for displaying user-created lists
 * Version: 1.0.0
 */

class DynamicListWidget extends WidgetModule {
    constructor(config = {}) {
        super('dynamic-list', {
            version: '1.0.0',
            defaultSize: 'size-small',
            updateInterval: null
        });
        this.listId = config.listId || null;
        this.listName = config.listName || 'Custom List';
        this.listType = config.listType || 'personal_todos';
        this.items = config.items || [];
        this.userId = null;
    }
    
    getTemplate() {
        return `
            <div class="widget-header">
                <div class="widget-title">🗂️ ${this.listName}</div>
                <div class="widget-badge" id="dynamic-list-${this.listId}-count">0</div>
            </div>
            <div class="widget-content" style="flex: 1; overflow-y: auto;">
                <input type="text" 
                       class="add-item-input" 
                       placeholder="Add item..." 
                       id="dynamic-list-${this.listId}-input"
                       style="width: 100%; border: 1px solid rgba(123, 97, 255, 0.3); border-radius: 8px; padding: 8px 12px; font-size: 13px; background: rgba(255, 255, 255, 0.5); margin-bottom: 12px;">
                <div id="dynamic-list-${this.listId}-items"></div>
            </div>
        `;
    }
    
    init(element) {
        super.init(element);
        
        const session = window.zoeAuth?.getCurrentSession();
        this.userId = session?.user_info?.user_id || session?.user_id || 'default';
        
        const input = element.querySelector(`#dynamic-list-${this.listId}-input`);
        if (input) {
            input.addEventListener('keypress', (e) => {
                if (e.key === 'Enter') {
                    this.addItem(input.value.trim());
                    input.value = '';
                }
            });
        }
        
        this.element.widgetInstance = this;
        
        // If we have a listId, load fresh data
        if (this.listId) {
            this.loadItems();
        } else {
            this.render();
        }
    }
    
    update() {
        if (this.listId) {
            this.loadItems();
        }
    }
    
    async loadItems() {
        if (!this.listId) return;
        
        try {
            const response = await apiRequest(`/lists/${this.listType}?user_id=${this.userId}`);
            const lists = response.lists || [];
            
            const thisList = lists.find(l => l.id === this.listId);
            if (thisList) {
                this.items = thisList.items || [];
                this.listName = thisList.name || this.listName;
            }
            
            this.render();
        } catch (error) {
            console.error('Failed to load dynamic list:', error);
            this.render();
        }
    }
    
    render() {
        const container = this.element.querySelector(`#dynamic-list-${this.listId}-items`);
        const countBadge = this.element.querySelector(`#dynamic-list-${this.listId}-count`);
        
        if (countBadge) {
            countBadge.textContent = this.items.length;
        }
        
        if (!container) return;
        
        if (this.items.length === 0) {
            container.innerHTML = '<div style="text-align: center; color: #666; font-size: 12px; padding: 20px;">No items yet</div>';
            return;
        }
        
        container.innerHTML = this.items.map(item => `
            <div class="list-item" data-id="${item.id}" style="display: flex; align-items: center; gap: 8px; padding: 8px 0; border-bottom: 1px solid rgba(255, 255, 255, 0.2);">
                <div class="item-checkbox ${item.completed ? 'checked' : ''}" 
                     onclick="event.stopPropagation(); this.closest('.widget').widgetInstance.toggleItem(${item.id})"
                     style="width: 20px; height: 20px; border: 2px solid #7B61FF; border-radius: 4px; cursor: pointer; transition: all 0.2s ease; display: flex; align-items: center; justify-content: center; flex-shrink: 0; background: ${item.completed ? 'linear-gradient(135deg, #7B61FF 0%, #5AE0E0 100%)' : 'white'};">
                    ${item.completed ? '<span style="color: white; font-size: 12px; font-weight: bold;">✓</span>' : ''}
                </div>
                <div class="item-text" style="flex: 1; font-size: 13px; color: #333; ${item.completed ? 'text-decoration: line-through; opacity: 0.5;' : ''}">${item.text || item.name || 'Untitled'}</div>
                <button class="item-delete" 
                        onclick="event.stopPropagation(); this.closest('.widget').widgetInstance.deleteItem(${item.id})"
                        title="Delete"
                        style="width: 24px; height: 24px; border: none; background: rgba(244, 67, 54, 0.1); color: #f44336; border-radius: 4px; cursor: pointer; font-size: 12px; opacity: 0; transition: opacity 0.2s;">×</button>
            </div>
        `).join('');
        
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
        if (!this.listId) return;
        
        try {
            await apiRequest(`/lists/${this.listType}/${this.listId}?user_id=${this.userId}`, {
                method: 'PUT',
                body: JSON.stringify({ items: this.items })
            });
        } catch (error) {
            console.error('Failed to save dynamic list:', error);
        }
    }
    
    /**
     * Factory method to create a dynamic list widget with config
     * @param {object} config - Widget configuration
     * @returns {DynamicListWidget}
     */
    static create(config) {
        return new DynamicListWidget(config);
    }
}

// Expose to global scope for WidgetManager
window.DynamicListWidget = DynamicListWidget;

// Register widget factory
if (typeof WidgetRegistry !== 'undefined') {
    WidgetRegistry.register('dynamic-list', DynamicListWidget);
}

