/**
 * Shopping List Widget
 * Displays and manages shopping list items
 * Version: 1.0.0
 */

class ShoppingWidget extends WidgetModule {
    constructor() {
        super('shopping', {
            version: '1.0.0',
            defaultSize: 'size-small',
            updateInterval: null // Manual refresh only
        });
        this.items = [];
        this.archivedItems = [];
        this.userId = null;
        this.listId = null;
        this.expandedItems = new Set(); // Track expanded sub-items
        this.listCommon = null; // Will load list-common.js
    }
    
    getTemplate() {
        return `
            <div class="widget-header">
                <div class="widget-title" id="shopping-title" style="cursor: pointer;">🛒 Shopping</div>
                <div class="widget-badge" 
                     id="shopping-count" 
                     onclick="event.stopPropagation(); this.closest('.widget').widgetInstance.toggleArchiveView()"
                     style="cursor: pointer;"
                     title="Click to view archived items">0</div>
            </div>
            <div class="widget-content">
                <input type="text" 
                       class="add-item-input" 
                       placeholder="Add item..." 
                       id="shopping-input">
                <div id="shopping-items"></div>
                
                <!-- Archive viewer modal -->
                <div id="shopping-archive-modal" style="display: none; position: absolute; top: 0; left: 0; right: 0; bottom: 0; background: rgba(255, 255, 255, 0.98); backdrop-filter: blur(10px); border-radius: 12px; padding: 16px; z-index: 1000; overflow-y: auto;">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; padding-bottom: 12px; border-bottom: 2px solid rgba(123, 97, 255, 0.2);">
                        <h3 style="margin: 0; font-size: 16px; color: #7B61FF;">📦 Archived Items</h3>
                        <button onclick="event.stopPropagation(); this.closest('.widget').widgetInstance.toggleArchiveView()" 
                                style="width: 32px; height: 32px; border: none; background: rgba(123, 97, 255, 0.1); color: #7B61FF; border-radius: 8px; cursor: pointer; font-size: 20px; font-weight: bold;">×</button>
                    </div>
                    <div id="shopping-archive-items"></div>
                </div>
            </div>
        `;
    }
    
    async init(element) {
        super.init(element);
        
        // Load list-common.js utilities
        try {
            // Try to load as module if available
            if (typeof require !== 'undefined') {
                this.listCommon = require('./list-common.js');
            } else {
                // Load from script tag if module system not available
                await this.loadListCommon();
            }
        } catch (error) {
            console.warn('Could not load list-common.js:', error);
        }
        
        // Get user session
        const session = window.zoeAuth?.getCurrentSession();
        this.userId = session?.user_info?.user_id || session?.user_id || 'default';
        
        // ✅ NEW: Setup WebSocket sync for real-time updates
        this.setupWebSocketSync();
        
        // Setup inline rename for title (optional, graceful if list-common not loaded)
        const titleEl = element.querySelector('#shopping-title');
        if (titleEl) {
            if (this.listCommon && typeof this.listCommon.createInlineEdit === 'function') {
                this.listCommon.createInlineEdit(titleEl, (newName) => {
                    this.renameList(newName);
                });
            } else {
                // Fallback: simple click to rename
                titleEl.addEventListener('click', () => {
                    const newName = prompt('Rename list:', titleEl.textContent.replace('🛒 ', ''));
                    if (newName) {
                        this.renameList(newName);
                    }
                });
            }
        }
        
        // Setup input handler
        const input = element.querySelector('#shopping-input');
        if (input) {
            input.addEventListener('keypress', (e) => {
                if (e.key === 'Enter') {
                    this.addItem(input.value.trim());
                    input.value = '';
                }
            });
        }
        
        // Load items
        this.loadArchivedItems();
        await this.loadItems();
        
        // Auto-archive completed items older than 24 hours
        this.archiveInterval = setInterval(() => {
            this.archiveOldCompletedItems();
        }, 60 * 60 * 1000);
        setTimeout(() => this.archiveOldCompletedItems(), 5000);
    }
    
    async loadListCommon() {
        return new Promise((resolve, reject) => {
            const script = document.createElement('script');
            script.src = '/js/widgets/core/list-common.js';
            script.onload = () => {
                // Access functions from global scope or window.listCommon
                this.listCommon = window.listCommon || {};
                resolve();
            };
            script.onerror = reject;
            document.head.appendChild(script);
        });
    }
    
    update() {
        // Reload items from backend
        console.log('🛒 Shopping widget update() called');
        console.log('  - this.element:', this.element);
        console.log('  - this.userId:', this.userId);
        this.loadItems();
    }
    
    async loadItems() {
        try {
            console.log('🛒 Loading items from API...');
            console.log(`🛒 Current listId BEFORE load: ${this.listId}`);
            const response = await apiRequest(`/api/lists/shopping`);
            const lists = response.lists || [];
            
            console.log(`🛒 API returned ${lists.length} list(s):`, lists.map(l => `ID ${l.id} with ${l.items?.length || 0} items`));
            
            if (lists.length > 0) {
                const oldListId = this.listId;
                this.listId = lists[0].id;
                console.log(`🛒 Setting listId from ${oldListId} → ${this.listId}`);
                // API now returns hierarchical structure with sub_items
                this.items = lists[0].items || [];
                // Flatten items for backward compatibility, but preserve hierarchy
                this.flattenItems();
            } else {
                this.items = [];
                this.listId = null;
            }
            
            console.log(`🛒 Loaded ${this.items.length} items, rendering...`);
            this.render();
        } catch (error) {
            console.error('Failed to load shopping list:', error);
            this.items = [];
            this.listId = null;
            this.render();
        }
    }
    
    flattenItems() {
        // Convert hierarchical structure to flat list with depth info for rendering
        const flattened = [];
        const processItems = (items, parentId = null, depth = 0) => {
            items.forEach(item => {
                flattened.push({
                    ...item,
                    parent_id: parentId,
                    depth: depth,
                    sub_items: item.sub_items || []
                });
                if (item.sub_items && item.sub_items.length > 0) {
                    processItems(item.sub_items, item.id, depth + 1);
                }
            });
        };
        
        const hierarchical = this.items.filter(item => !item.parent_id);
        const flatList = [...flattened];
        processItems(hierarchical);
        
        // Store both representations
        this.items = flattened.length > 0 ? flattened : this.items;
        this.hierarchicalItems = hierarchical.length > 0 ? hierarchical : this.items;
    }
    
    render() {
        console.log('🛒 render() called');
        const container = this.element.querySelector('#shopping-items');
        const countBadge = this.element.querySelector('#shopping-count');
        console.log('  - container:', container);
        console.log('  - countBadge:', countBadge);
        
        // Count incomplete items (including sub-items)
        const countIncomplete = (items) => {
            let count = 0;
            items.forEach(item => {
                if (!item.completed) count++;
                if (item.sub_items && item.sub_items.length > 0) {
                    count += countIncomplete(item.sub_items);
                }
            });
            return count;
        };
        
        const hierarchical = this.hierarchicalItems || this.items.filter(item => !item.parent_id);
        const incompleteCount = countIncomplete(hierarchical);
        
        if (countBadge) {
            countBadge.textContent = incompleteCount;
            console.log(`  - Updated badge to: ${incompleteCount}`);
        }
        
        if (!container) {
            console.warn('⚠️ Container #shopping-items not found!');
            return;
        }
        
        if (hierarchical.length === 0) {
            container.innerHTML = '<div style="text-align: center; color: #666; font-size: 12px; padding: 20px;">No items yet</div>';
            return;
        }
        
        // Render hierarchical items
        container.innerHTML = '';
        const renderItem = (item, level = 0) => {
            const isExpanded = this.expandedItems.has(item.id);
            const hasSubItems = item.sub_items && item.sub_items.length > 0;
            
            const itemEl = document.createElement('div');
            itemEl.className = 'list-item hierarchical-item';
            itemEl.dataset.itemId = item.id;
            itemEl.dataset.depth = level;
            // Base styling - no left padding, reduced gap between elements
            itemEl.style.cssText = `display: flex; align-items: center; gap: 4px; padding: 8px 0; border-bottom: 1px solid rgba(255, 255, 255, 0.2);`;
            
            // Expand/collapse arrow (inline, only if has sub-items)
            if (hasSubItems) {
                const expandBtn = document.createElement('span');
                expandBtn.textContent = isExpanded ? '▼' : '▶';
                expandBtn.style.cssText = 'width: 16px; cursor: pointer; font-size: 10px; color: #7B61FF; user-select: none; flex-shrink: 0;';
                expandBtn.onclick = (e) => {
                    e.stopPropagation();
                    this.toggleExpand(item.id);
                };
                itemEl.appendChild(expandBtn);
            } else {
                // Spacer for items without arrow (so all checkboxes align)
                const spacer = document.createElement('span');
                spacer.style.cssText = 'width: 16px; flex-shrink: 0;';
                itemEl.appendChild(spacer);
            }
            
            // Indent sub-items by adding left margin
            if (level > 0) {
                itemEl.style.marginLeft = `${level * 24}px`;
            }
            
            // Checkbox
            const checkbox = document.createElement('div');
            checkbox.className = `item-checkbox ${item.completed ? 'checked' : ''}`;
            checkbox.onclick = (e) => {
                e.stopPropagation();
                this.toggleItem(item.id);
            };
            checkbox.style.cssText = `
                width: 18px;
                height: 18px;
                border: 2px solid #7B61FF;
                border-radius: 4px;
                cursor: pointer;
                transition: all 0.2s ease;
                display: flex;
                align-items: center;
                justify-content: center;
                flex-shrink: 0;
                background: ${item.completed ? 'linear-gradient(135deg, #7B61FF 0%, #5AE0E0 100%)' : 'white'};
            `;
            if (item.completed) {
                checkbox.innerHTML = '<span style="color: white; font-size: 12px; font-weight: bold;">✓</span>';
            }
            itemEl.appendChild(checkbox);
            
            // Item text
            const text = document.createElement('div');
            text.className = 'item-text';
            text.textContent = item.text;
            text.style.cssText = `
                flex: 1;
                font-size: 13px;
                color: #333;
                ${item.completed ? 'text-decoration: line-through; opacity: 0.5;' : ''}
            `;
            itemEl.appendChild(text);
            
            // Reminder/due date indicators
            if (item.reminder_time || item.due_date) {
                const indicators = document.createElement('span');
                indicators.style.cssText = 'margin-left: 8px; font-size: 12px;';
                if (item.reminder_time) {
                    indicators.innerHTML += '⏰';
                    indicators.title = `Reminder: ${item.reminder_time}`;
                }
                if (item.due_date) {
                    indicators.innerHTML += '📅';
                    indicators.title = `Due: ${item.due_date}`;
                }
                itemEl.appendChild(indicators);
            }
            
            // Actions button (replaced with inline icon buttons)
            const actions = document.createElement('div');
            actions.className = 'item-actions';
            actions.style.cssText = `
                display: flex;
                gap: 2px;
                opacity: 0;
                transition: opacity 0.2s;
                flex-shrink: 0;
            `;
            
            // Add sub-item button
            const addBtn = document.createElement('button');
            addBtn.innerHTML = '➕';
            addBtn.title = 'Add sub-item';
            addBtn.style.cssText = `
                width: 24px;
                height: 24px;
                border: none;
                background: none;
                cursor: pointer;
                font-size: 13px;
                color: #7B61FF;
                border-radius: 4px;
                padding: 0;
                transition: background 0.15s;
            `;
            addBtn.onmouseenter = () => addBtn.style.background = 'rgba(123, 97, 255, 0.1)';
            addBtn.onmouseleave = () => addBtn.style.background = 'none';
            addBtn.onclick = (e) => {
                e.stopPropagation();
                this.addSubItem(item.id);
            };
            actions.appendChild(addBtn);
            
            // Clock button (reminder/due date)
            const clockBtn = document.createElement('button');
            clockBtn.innerHTML = '🕐';
            clockBtn.title = 'Set reminder or due date';
            clockBtn.style.cssText = `
                width: 24px;
                height: 24px;
                border: none;
                background: none;
                cursor: pointer;
                font-size: 13px;
                color: #7B61FF;
                border-radius: 4px;
                padding: 0;
                transition: background 0.15s;
            `;
            clockBtn.onmouseenter = () => clockBtn.style.background = 'rgba(123, 97, 255, 0.1)';
            clockBtn.onmouseleave = () => clockBtn.style.background = 'none';
            clockBtn.onclick = (e) => {
                e.stopPropagation();
                this.showTimeMenu(e, item.id);
            };
            actions.appendChild(clockBtn);
            
            // Delete button
            const deleteBtn = document.createElement('button');
            deleteBtn.innerHTML = '×';
            deleteBtn.title = 'Delete';
            deleteBtn.style.cssText = `
                width: 24px;
                height: 24px;
                border: none;
                background: none;
                cursor: pointer;
                font-size: 20px;
                color: #f44336;
                border-radius: 4px;
                padding: 0;
                line-height: 1;
                transition: background 0.15s;
            `;
            deleteBtn.onmouseenter = () => deleteBtn.style.background = 'rgba(244, 67, 54, 0.1)';
            deleteBtn.onmouseleave = () => deleteBtn.style.background = 'none';
            deleteBtn.onclick = (e) => {
                e.stopPropagation();
                this.deleteItem(item.id); // Direct delete, no confirmation
            };
            actions.appendChild(deleteBtn);
            
            itemEl.appendChild(actions);
            
            // Show actions on hover
            itemEl.addEventListener('mouseenter', () => {
                actions.style.opacity = '1';
            });
            itemEl.addEventListener('mouseleave', () => {
                actions.style.opacity = '0';
            });
            
            container.appendChild(itemEl);
            
            // Render sub-items if expanded
            if (hasSubItems && isExpanded) {
                item.sub_items.forEach(subItem => {
                    renderItem(subItem, level + 1);
                });
            }
        };
        
        hierarchical.forEach(item => renderItem(item));
        
        // Store instance reference
        this.element.widgetInstance = this;
        
        console.log(`✅ Rendered hierarchical items`);
    }
    
    toggleExpand(itemId) {
        if (this.expandedItems.has(itemId)) {
            this.expandedItems.delete(itemId);
        } else {
            this.expandedItems.add(itemId);
        }
        this.render();
    }
    
    showTimeMenu(event, itemId) {
        // Remove any existing menu
        const existingMenu = document.querySelector('.time-menu');
        if (existingMenu) existingMenu.remove();
        
        const menu = document.createElement('div');
        menu.className = 'time-menu';
        
        // Initial positioning (will adjust after appending)
        menu.style.cssText = `
            position: fixed;
            background: rgba(255, 255, 255, 0.98);
            backdrop-filter: blur(10px);
            border: 1px solid rgba(0, 0, 0, 0.1);
            border-radius: 8px;
            padding: 8px 0;
            min-width: 160px;
            box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
            z-index: 10000;
            visibility: hidden;
        `;
        
        const actions = [
            { label: 'Set Reminder', icon: '⏰', action: () => this.setReminder(itemId) },
            { label: 'Set Due Date', icon: '📅', action: () => this.setDueDate(itemId) }
        ];
        
        actions.forEach(action => {
            const btn = document.createElement('button');
            btn.innerHTML = `${action.icon} ${action.label}`;
            btn.style.cssText = `
                width: 100%;
                padding: 10px 16px;
                text-align: left;
                background: none;
                border: none;
                cursor: pointer;
                font-size: 14px;
                color: #333;
                transition: background 0.2s;
            `;
            btn.onmouseenter = () => {
                btn.style.background = 'rgba(123, 97, 255, 0.1)';
            };
            btn.onmouseleave = () => {
                btn.style.background = 'none';
            };
            btn.onclick = () => {
                action.action();
                menu.remove();
            };
            menu.appendChild(btn);
        });
        
        document.body.appendChild(menu);
        
        // Smart positioning: check if menu would go off-screen
        const menuRect = menu.getBoundingClientRect();
        const viewportWidth = window.innerWidth;
        const viewportHeight = window.innerHeight;
        
        let left = event.clientX;
        let top = event.clientY;
        
        // If menu would extend past right edge, position to left of cursor
        if (left + menuRect.width > viewportWidth - 10) {
            left = event.clientX - menuRect.width;
        }
        
        // If menu would extend past bottom edge, position above cursor
        if (top + menuRect.height > viewportHeight - 10) {
            top = event.clientY - menuRect.height;
        }
        
        // Ensure menu doesn't go off left edge
        if (left < 10) {
            left = 10;
        }
        
        // Ensure menu doesn't go off top edge
        if (top < 10) {
            top = 10;
        }
        
        menu.style.left = `${left}px`;
        menu.style.top = `${top}px`;
        menu.style.visibility = 'visible';
        
        // Close on click outside
        setTimeout(() => {
            const closeMenu = (e) => {
                if (!menu.contains(e.target)) {
                    menu.remove();
                    document.removeEventListener('click', closeMenu);
                }
            };
            document.addEventListener('click', closeMenu);
        }, 100);
    }
    
    showItemActions(itemId, itemEl) {
        // Simple actions menu for now - can be enhanced with list-common.js
        const menu = document.createElement('div');
        menu.style.cssText = `
            position: absolute;
            background: white;
            border: 1px solid #ddd;
            border-radius: 8px;
            padding: 8px 0;
            min-width: 150px;
            box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
            z-index: 1000;
            right: 0;
            top: 100%;
            margin-top: 4px;
        `;
        
        const actions = [
            { label: 'Add Sub-item', action: () => this.addSubItem(itemId) },
            { label: 'Set Reminder', action: () => this.setReminder(itemId) },
            { label: 'Set Due Date', action: () => this.setDueDate(itemId) },
            { label: 'Delete', action: () => this.deleteItem(itemId) }
        ];
        
        actions.forEach(action => {
            const btn = document.createElement('button');
            btn.textContent = action.label;
            btn.style.cssText = `
                width: 100%;
                padding: 8px 16px;
                text-align: left;
                border: none;
                background: none;
                cursor: pointer;
                font-size: 13px;
                color: #333;
            `;
            btn.onclick = (e) => {
                e.stopPropagation();
                action.action();
                document.body.removeChild(menu);
            };
            btn.onmouseenter = () => btn.style.background = 'rgba(123, 97, 255, 0.1)';
            btn.onmouseleave = () => btn.style.background = 'none';
            menu.appendChild(btn);
        });
        
        document.body.appendChild(menu);
        
        // Position menu
        const rect = itemEl.getBoundingClientRect();
        menu.style.left = `${rect.right - menu.offsetWidth}px`;
        menu.style.top = `${rect.bottom + 4}px`;
        
        // Close on outside click
        setTimeout(() => {
            const closeHandler = (e) => {
                if (!menu.contains(e.target) && e.target !== itemEl) {
                    document.body.removeChild(menu);
                    document.removeEventListener('click', closeHandler);
                }
            };
            document.addEventListener('click', closeHandler);
        }, 0);
    }
    
    addSubItem(parentId) {
        // Show inline input for sub-item
        const container = this.element.querySelector('#shopping-items');
        const parentItem = container.querySelector(`[data-item-id="${parentId}"]`);
        
        if (!parentItem) return;
        
        // Check if input already exists
        if (parentItem.querySelector('.sub-item-input')) return;
        
        // Create inline input
        const inputWrapper = document.createElement('div');
        inputWrapper.className = 'sub-item-input';
        inputWrapper.style.cssText = `
            display: flex;
            align-items: center;
            gap: 8px;
            padding: 8px 0;
            margin-left: 40px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.2);
        `;
        
        const input = document.createElement('input');
        input.type = 'text';
        input.placeholder = 'Enter sub-item...';
        input.style.cssText = `
            flex: 1;
            padding: 8px 12px;
            border: 2px solid rgba(123, 97, 255, 0.3);
            border-radius: 8px;
            font-size: 13px;
            background: white;
        `;
        
        input.onkeydown = async (e) => {
            if (e.key === 'Enter' && input.value.trim()) {
                await this.addItem(input.value.trim(), parentId);
                inputWrapper.remove();
            } else if (e.key === 'Escape') {
                inputWrapper.remove();
            }
        };
        
        input.onblur = () => {
            setTimeout(() => inputWrapper.remove(), 200);
        };
        
        inputWrapper.appendChild(input);
        parentItem.after(inputWrapper);
        input.focus();
    }
    
    setReminder(itemId) {
        if (this.listCommon && this.listCommon.createReminderPicker) {
            this.listCommon.createReminderPicker(async (reminderTime) => {
                await this.updateItemField(itemId, 'reminder_time', reminderTime);
            });
        } else {
            const dateTime = prompt('Enter reminder time (YYYY-MM-DD HH:MM):');
            if (dateTime) {
                this.updateItemField(itemId, 'reminder_time', dateTime + ':00');
            }
        }
    }
    
    setDueDate(itemId) {
        if (this.listCommon && this.listCommon.createDatePicker) {
            this.listCommon.createDatePicker(async (date, time) => {
                await this.updateItemField(itemId, 'due_date', date);
                if (time) {
                    await this.updateItemField(itemId, 'due_time', time);
                }
            });
        } else {
            const date = prompt('Enter due date (YYYY-MM-DD):');
            if (date) {
                this.updateItemField(itemId, 'due_date', date);
            }
        }
    }
    
    async updateItemField(itemId, field, value) {
        try {
            const response = await apiRequest(`/lists/shopping/${this.listId}/items/${itemId}?${field}=${encodeURIComponent(value)}`, {
                method: 'PUT'
            });
            
            if (response) {
                await this.loadItems();
            }
        } catch (error) {
            console.error(`Failed to update ${field}:`, error);
            alert(`Failed to update ${field}`);
        }
    }
    
    async renameList(newName) {
        if (!this.listId) return;
        try {
            await apiRequest(`/api/lists/shopping/${this.listId}`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name: newName })
            });
            const titleEl = this.element.querySelector('#shopping-title');
            if (titleEl) {
                titleEl.textContent = `🛒 ${newName}`;
            }
        } catch (error) {
            console.error('Failed to rename list:', error);
        }
    }
    
    async addItem(text, parentId = null) {
        if (!text) return;
        
        if (!this.listId) {
            // Create list first
            try {
                const response = await apiRequest('/api/lists/shopping', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        list_type: 'shopping',
                        category: 'personal',
                        name: 'Shopping List',
                        items: []
                    })
                });
                this.listId = response.id;
            } catch (error) {
                console.error('Failed to create list:', error);
                return;
            }
        }
        
        try {
            const response = await apiRequest(`/api/lists/shopping/${this.listId}/items?task_text=${encodeURIComponent(text)}${parentId ? `&parent_id=${parentId}` : ''}`, {
                method: 'POST'
            });
            
            await this.loadItems();
        } catch (error) {
            console.error('Failed to add item:', error);
        }
    }
    
    async toggleItem(itemId) {
        if (!this.listId) return;
        
        // Find item to get current state
        const findItem = (items) => {
            for (const item of items) {
                if (item.id === itemId) return item;
                if (item.sub_items) {
                    const found = findItem(item.sub_items);
                    if (found) return found;
                }
            }
            return null;
        };
        
        const item = findItem(this.hierarchicalItems || this.items);
        if (!item) return;
        
        try {
            await apiRequest(`/api/lists/shopping/${this.listId}/items/${itemId}?completed=${!item.completed}`, {
                method: 'PUT'
            });
            
            await this.loadItems();
        } catch (error) {
            console.error('Failed to toggle item:', error);
        }
    }
    
    async deleteItem(itemId) {
        if (!this.listId) return;
        
        // Direct delete - no confirmation
        try {
            await apiRequest(`/api/lists/shopping/${this.listId}/items/${itemId}`, {
                method: 'DELETE'
            });
            
            await this.loadItems();
        } catch (error) {
            console.error('Failed to delete item:', error);
        }
    }
    
    async archiveOldCompletedItems() {
        const now = Date.now();
        const oneDayMs = 24 * 60 * 60 * 1000;
        
        const itemsToArchive = [];
        const itemsToKeep = this.items.filter(item => {
            if (!item.completed) return true;
            if (item.completedAt && (now - item.completedAt) < oneDayMs) return true;
            console.log(`📦 Archiving completed shopping item: ${item.text}`);
            item.archivedAt = now;
            itemsToArchive.push(item);
            return false;
        });
        
        if (itemsToArchive.length > 0) {
            this.archivedItems = [...itemsToArchive, ...this.archivedItems];
            
            if (this.archivedItems.length > 50) {
                this.archivedItems = this.archivedItems.slice(0, 50);
            }
            
            this.items = itemsToKeep;
            this.render();
            await this.saveItems();
            await this.saveArchivedItems();
            console.log(`📦 Archived ${itemsToArchive.length} completed shopping items`);
        }
    }
    
    toggleArchiveView() {
        const modal = this.element.querySelector('#shopping-archive-modal');
        if (modal) {
            const isHidden = modal.style.display === 'none';
            modal.style.display = isHidden ? 'block' : 'none';
            
            if (isHidden) {
                this.renderArchive();
            }
        }
    }
    
    renderArchive() {
        const container = this.element.querySelector('#shopping-archive-items');
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
            this.archivedItems = this.archivedItems.filter(i => i.id !== itemId);
            
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
            localStorage.setItem(`shopping_archived_${this.userId}`, JSON.stringify(this.archivedItems));
        } catch (error) {
            console.error('Failed to save archived items:', error);
        }
    }
    
    async loadArchivedItems() {
        try {
            const saved = localStorage.getItem(`shopping_archived_${this.userId}`);
            if (saved) {
                this.archivedItems = JSON.parse(saved);
            }
        } catch (error) {
            console.error('Failed to load archived items:', error);
            this.archivedItems = [];
        }
    }
    
    setupWebSocketSync() {
        // ✅ Setup WebSocket for real-time shopping list updates
        if (typeof ZoeWebSocketSync === 'undefined') {
            console.warn('⚠️ WebSocket sync not available, using polling fallback');
            // Fallback: poll every 30 seconds
            this.pollInterval = setInterval(() => {
                this.loadItems();
            }, 30000);
            return;
        }
        
        console.log('🔌 Setting up WebSocket sync for shopping list');
        this.wsSync = new ZoeWebSocketSync('/api/lists/ws', this.userId);
        
        // Listen for list updates
        this.wsSync.on('list_updated', (data) => {
            console.log('📥 Shopping list updated via WebSocket:', data);
            if (data.list_type === 'shopping' || data.list_id === this.listId) {
                console.log('✅ Reloading shopping list items');
                this.loadItems();
            }
        });
        
        // Listen for item changes
        this.wsSync.on('item_added', (data) => {
            console.log('📥 Item added via WebSocket:', data);
            if (data.list_type === 'shopping' || data.list_id === this.listId) {
                this.loadItems();
            }
        });
        
        this.wsSync.on('item_updated', (data) => {
            console.log('📥 Item updated via WebSocket:', data);
            if (data.list_type === 'shopping' || data.list_id === this.listId) {
                this.loadItems();
            }
        });
        
        this.wsSync.on('item_deleted', (data) => {
            console.log('📥 Item deleted via WebSocket:', data);
            if (data.list_type === 'shopping' || data.list_id === this.listId) {
                this.loadItems();
            }
        });
        
        // Connect
        this.wsSync.connect();
    }
    
    destroy() {
        // ✅ Cleanup WebSocket
        if (this.wsSync) {
            this.wsSync.disconnect();
            this.wsSync = null;
        }
        
        // ✅ Cleanup polling fallback
        if (this.pollInterval) {
            clearInterval(this.pollInterval);
            this.pollInterval = null;
        }
        
        if (this.archiveInterval) {
            clearInterval(this.archiveInterval);
        }
        super.destroy();
    }
}

// Expose to global scope for WidgetManager
window.ShoppingWidget = ShoppingWidget;

// Register widget
if (typeof WidgetRegistry !== 'undefined') {
    WidgetRegistry.register('shopping', new ShoppingWidget());
}

