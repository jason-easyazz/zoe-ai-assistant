/**
 * Work Todos Widget
 * Displays and manages work todo items
 * Version: 1.1.0
 */

class WorkWidget extends WidgetModule {
    constructor() {
        super('work', {
            version: '1.1.0',
            defaultSize: 'size-small',
            updateInterval: null
        });
        this.items = [];
        this.archivedItems = [];
        this.userId = null;
        this.listId = null;
    }

    getTemplate() {
        return `
            <div class="widget-header">
                <div class="widget-title">💼 Work</div>
                <div class="widget-badge"
                     id="work-count"
                     onclick="event.stopPropagation(); this.closest('.widget').widgetInstance.toggleArchiveView()"
                     style="cursor: pointer;"
                     title="Click to view archived items">0</div>
            </div>
            <div class="widget-content">
                <input type="text"
                       class="add-item-input"
                       placeholder="Add task..."
                       id="work-input">
                <div id="work-items"></div>

                <div id="work-archive-modal" style="display: none; position: absolute; top: 0; left: 0; right: 0; bottom: 0; background: rgba(255, 255, 255, 0.98); backdrop-filter: blur(10px); border-radius: 12px; padding: 16px; z-index: 1000; overflow-y: auto;">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; padding-bottom: 12px; border-bottom: 2px solid rgba(123, 97, 255, 0.2);">
                        <h3 style="margin: 0; font-size: 16px; color: #7B61FF;">📦 Archived Items</h3>
                        <button onclick="event.stopPropagation(); this.closest('.widget').widgetInstance.toggleArchiveView()"
                                style="width: 32px; height: 32px; border: none; background: rgba(123, 97, 255, 0.1); color: #7B61FF; border-radius: 8px; cursor: pointer; font-size: 20px; font-weight: bold;">×</button>
                    </div>
                    <div id="work-archive-items"></div>
                </div>
            </div>
        `;
    }

    init(element) {
        super.init(element);

        const session = window.zoeAuth?.getCurrentSession();
        this.userId = session?.user_info?.user_id || session?.user_id || 'default';

        const input = element.querySelector('#work-input');
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

        this.archiveInterval = setInterval(() => {
            this.archiveOldCompletedItems();
        }, 60 * 60 * 1000);
        setTimeout(() => this.archiveOldCompletedItems(), 5000);
    }

    update() {
        const session = window.zoeAuth?.getCurrentSession();
        this.userId = session?.user_info?.user_id || session?.user_id || 'default';
        this.loadItems();
    }

    async loadItems() {
        try {
            const listsResponse = await apiRequest(`/api/lists/work_todos`);
            const lists = listsResponse.lists || [];

            if (lists.length === 0) {
                this.items = [];
                this.listId = null;
                this.render();
                return;
            }

            this.listId = lists[0].id;
            const detail = await apiRequest(`/api/lists/work_todos/${this.listId}`);
            this.items = detail.items || [];
            this.render();
        } catch (error) {
            console.error('Failed to load work todos:', error);
            this.items = [];
            this.listId = null;
            this.render();
        }
    }

    render() {
        const container = this.element.querySelector('#work-items');
        const countBadge = this.element.querySelector('#work-count');

        const sortedItems = [...this.items].sort((a, b) => {
            if (a.completed === b.completed) {
                if (a.completed && a.completedAt && b.completedAt) {
                    return a.completedAt - b.completedAt;
                }
                return 0;
            }
            return a.completed ? 1 : -1;
        });

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
                     onclick="event.stopPropagation(); this.closest('.widget').widgetInstance.toggleItem('${item.id}')"
                     style="width: 20px; height: 20px; border: 2px solid #7B61FF; border-radius: 4px; cursor: pointer; transition: all 0.2s ease; display: flex; align-items: center; justify-content: center; flex-shrink: 0; background: ${item.completed ? 'linear-gradient(135deg, #7B61FF 0%, #5AE0E0 100%)' : 'white'};">
                    ${item.completed ? '<span style="color: white; font-size: 12px; font-weight: bold;">✓</span>' : ''}
                </div>
                <div class="item-text" style="flex: 1; font-size: 13px; color: #333; ${item.completed ? 'text-decoration: line-through; opacity: 0.5;' : ''}">${item.text}</div>
                <button class="item-delete"
                        onclick="event.stopPropagation(); this.closest('.widget').widgetInstance.deleteItem('${item.id}')"
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

    async ensureList() {
        if (this.listId) return this.listId;
        try {
            const response = await apiRequest('/api/lists/work_todos', {
                method: 'POST',
                body: JSON.stringify({
                    name: 'Work Todos',
                    list_type: 'work_todos'
                })
            });
            this.listId = response.id;
            return this.listId;
        } catch (error) {
            console.error('Failed to create work list:', error);
            return null;
        }
    }

    async addItem(text) {
        if (!text) return;
        const listId = await this.ensureList();
        if (!listId) return;

        try {
            await apiRequest(`/api/lists/work_todos/${listId}/items`, {
                method: 'POST',
                body: JSON.stringify({ text })
            });
            await this.loadItems();
        } catch (error) {
            console.error('Failed to add work item:', error);
        }
    }

    async toggleItem(itemId) {
        if (!this.listId) return;
        const item = this.items.find(i => String(i.id) === String(itemId));
        if (!item) return;
        try {
            await apiRequest(`/api/lists/work_todos/${this.listId}/items/${itemId}`, {
                method: 'PUT',
                body: JSON.stringify({ completed: !item.completed })
            });
            await this.loadItems();
        } catch (error) {
            console.error('Failed to toggle work item:', error);
        }
    }

    async deleteItem(itemId) {
        if (!this.listId) return;
        try {
            await apiRequest(`/api/lists/work_todos/${this.listId}/items/${itemId}`, {
                method: 'DELETE'
            });
            await this.loadItems();
        } catch (error) {
            console.error('Failed to delete work item:', error);
        }
    }

    async archiveOldCompletedItems() {
        const now = Date.now();
        const oneDayMs = 24 * 60 * 60 * 1000;

        const itemsToArchive = this.items.filter(item => {
            if (!item.completed) return false;
            const completedAt = item.completedAt
                || (item.updated_at ? new Date(item.updated_at).getTime() : null);
            if (!completedAt) return false;
            return (now - completedAt) >= oneDayMs;
        });

        if (itemsToArchive.length === 0) return;

        itemsToArchive.forEach(i => { i.archivedAt = now; });
        this.archivedItems = [...itemsToArchive, ...this.archivedItems].slice(0, 50);

        if (this.listId) {
            for (const item of itemsToArchive) {
                try {
                    await apiRequest(`/api/lists/work_todos/${this.listId}/items/${item.id}`, {
                        method: 'DELETE'
                    });
                } catch (err) {
                    console.warn('Failed to delete archived work item on server:', err);
                }
            }
        }
        await this.saveArchivedItems();
        await this.loadItems();
    }

    toggleArchiveView() {
        const modal = this.element.querySelector('#work-archive-modal');
        if (modal) {
            const isHidden = modal.style.display === 'none';
            modal.style.display = isHidden ? 'block' : 'none';
            if (isHidden) this.renderArchive();
        }
    }

    renderArchive() {
        const container = this.element.querySelector('#work-archive-items');
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
                            onclick="event.stopPropagation(); this.closest('.widget').widgetInstance.restoreItem('${item.id}')"
                            title="Restore"
                            style="padding: 6px 12px; border: none; background: rgba(123, 97, 255, 0.2); color: #7B61FF; border-radius: 6px; cursor: pointer; font-size: 12px; font-weight: 600;">↩</button>
                    <button class="delete-btn"
                            onclick="event.stopPropagation(); this.closest('.widget').widgetInstance.deleteArchivedItem('${item.id}')"
                            title="Delete permanently"
                            style="padding: 6px 12px; border: none; background: rgba(244, 67, 54, 0.1); color: #f44336; border-radius: 6px; cursor: pointer; font-size: 12px; font-weight: 600;">🗑️</button>
                </div>
            `;
        }).join('');
    }

    async restoreItem(itemId) {
        const item = this.archivedItems.find(i => String(i.id) === String(itemId));
        if (!item) return;
        this.archivedItems = this.archivedItems.filter(i => String(i.id) !== String(itemId));
        await this.addItem(item.text);
        this.renderArchive();
        await this.saveArchivedItems();
    }

    async deleteArchivedItem(itemId) {
        if (confirm('Permanently delete this archived item?')) {
            this.archivedItems = this.archivedItems.filter(i => String(i.id) !== String(itemId));
            this.renderArchive();
            await this.saveArchivedItems();
        }
    }

    async saveArchivedItems() {
        try {
            localStorage.setItem(`work_archived_${this.userId}`, JSON.stringify(this.archivedItems));
        } catch (error) {
            console.error('Failed to save archived items:', error);
        }
    }

    async loadArchivedItems() {
        try {
            const saved = localStorage.getItem(`work_archived_${this.userId}`);
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

window.WorkWidget = WorkWidget;

if (typeof WidgetRegistry !== 'undefined') {
    WidgetRegistry.register('work', new WorkWidget());
}
