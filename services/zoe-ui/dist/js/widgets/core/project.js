/**
 * Project List Widget with Stages
 * Multi-stage workflow management for complex projects
 * Version: 1.0.0
 */

class ProjectsWidget extends WidgetModule {
    constructor() {
        super('project', {
            version: '1.0.0',
            defaultSize: 'size-medium',
            updateInterval: null // Manual refresh only
        });
        this.projectId = null; // Store specific project ID for this widget instance
        this.currentProject = null;
        this.currentStage = null;
        this.userId = null;
        this.expandedItems = new Set(); // Track expanded sub-items
    }
    
    getTemplate() {
        return `
            <div class="widget-header">
                <div class="widget-title" id="project-title" style="cursor: pointer;">📋 Project</div>
                <div class="widget-badge" id="project-count">0</div>
            </div>
            <div class="widget-content" style="padding: 12px;">
                <!-- Stage navigation bar -->
                <div id="project-stage-nav" class="stage-nav" style="display: flex; gap: 6px; padding: 8px 0; overflow-x: auto; border-bottom: 1px solid rgba(123, 97, 255, 0.2); margin-bottom: 12px;"></div>
                
                <!-- Current stage view -->
                <div id="project-current-stage" class="stage-view">
                    <div class="stage-header" style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
                        <h3 id="current-stage-name" style="margin: 0; font-size: 14px; color: #7B61FF;"></h3>
                        <div class="stage-progress" id="stage-progress" style="font-size: 11px; color: #666;"></div>
                    </div>
                    <input type="text" 
                           class="add-item-input" 
                           placeholder="Add item to this stage..." 
                           id="project-input"
                           style="width: calc(100% - 24px); padding: 8px 12px; border: 1px solid rgba(123, 97, 255, 0.3); border-radius: 6px; font-size: 13px; margin-bottom: 12px;">
                    <div id="project-items"></div>
                </div>
                
                <!-- Next stage preview (for large widgets) -->
                <div id="project-next-stage" class="stage-view stage-preview" style="opacity: 0.5; display: none; margin-top: 16px; padding-top: 16px; border-top: 1px solid rgba(123, 97, 255, 0.1);">
                    <div class="stage-header" style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
                        <h3 id="next-stage-name" style="margin: 0; font-size: 13px; color: #999;">Next: <span></span></h3>
                    </div>
                    <div id="next-stage-items" style="font-size: 12px; color: #999;"></div>
                </div>
            </div>
        `;
    }
    
    async init(element, options = {}) {
        super.init(element);
        
        // Get user session
        const session = window.zoeAuth?.getCurrentSession();
        this.userId = session?.user_info?.user_id || session?.user_id || 'default';
        
        // Get project ID from options
        this.projectId = options.projectId || options.id;
        
        // Setup title click handler for inline editing
        const titleEl = element.querySelector('#project-title');
        if (titleEl) {
            titleEl.addEventListener('click', () => {
                console.log('Title clicked, currentProject:', this.currentProject);
                if (!this.currentProject) {
                    console.warn('No project loaded yet');
                    return;
                }
                const currentName = this.currentProject.name || 'Untitled Project';
                const newName = prompt('Rename project:', currentName);
                if (newName && newName !== currentName) {
                    this.renameProject(newName);
                }
            });
        }
        
        // Setup input handler
        const input = element.querySelector('#project-input');
        if (input) {
            input.addEventListener('keypress', (e) => {
                if (e.key === 'Enter' && this.currentProject && this.currentStage) {
                    this.addItem(input.value.trim());
                    input.value = '';
                }
            });
        }
        
        // Load project or show create dialog
        if (this.projectId) {
            await this.loadProject(this.projectId);
        } else {
            // No project ID - show create dialog
            const success = await this.showCreateProjectDialog();
            if (!success) {
                // User cancelled - widget should be removed
                console.warn('Project creation cancelled');
            }
        }
    }
    
    async renameProject(newName) {
        if (!this.projectId) return;
        
        try {
            await apiRequest(`/api/projects/${this.projectId}`, {
                method: 'PATCH',
                body: JSON.stringify({ name: newName })
            });
            
            // Update title
            const titleEl = this.element.querySelector('#project-title');
            if (titleEl) {
                titleEl.textContent = `📋 ${newName}`;
            }
            
            if (this.currentProject) {
                this.currentProject.name = newName;
            }
        } catch (error) {
            console.error('Failed to rename project:', error);
            alert('Failed to rename project');
        }
    }
    
    async loadProject(projectId) {
        console.log('🔄 Loading project:', projectId);
        try {
            const response = await apiRequest(`/api/projects/${projectId}`);
            console.log('✅ Project loaded:', response);
            
            this.currentProject = response;
            this.projectId = response.id;
            this.currentStage = response.stages?.find(s => s.id === response.current_stage_id);
            
            console.log('Current stage:', this.currentStage);
            console.log('All stages:', response.stages);
            
            // Update title with project name
            const titleEl = this.element.querySelector('#project-title');
            if (titleEl) {
                titleEl.textContent = `📋 ${response.name}`;
                console.log('✅ Title updated to:', response.name);
            }
            
            // Update badge with item count
            const countBadge = this.element.querySelector('#project-count');
            if (countBadge && this.currentStage) {
                const itemCount = this.currentStage.items?.length || 0;
                countBadge.textContent = itemCount;
            }
            
            console.log('🎨 Calling render...');
            this.render();
            console.log('✅ Render complete');
        } catch (error) {
            console.error('❌ Failed to load project:', error);
        }
    }
    
    async showCreateProjectDialog() {
        const name = prompt('Project name:');
        if (!name) {
            return false;
        }
        
        const stagesInput = prompt('Enter stages separated by commas (e.g., Planning, Design, Development, Testing, Deployment):');
        if (!stagesInput) {
            return false;
        }
        
        const stages = stagesInput.split(',').map(s => s.trim()).filter(s => s.length > 0);
        if (stages.length === 0) {
            alert('Please enter at least one stage');
            return false;
        }
        
        const project = await this.createProject(name, stages);
        if (project) {
            this.projectId = project.id;
            await this.loadProject(this.projectId);
            return true;
        }
        
        return false;
    }
    
    async createProject(name, stages) {
        try {
            const response = await apiRequest('/api/projects', {
                method: 'POST',
                body: JSON.stringify({ name, stages })
            });
            
            return response;
        } catch (error) {
            console.error('Failed to create project:', error);
            alert('Failed to create project. Please try again.');
            return null;
        }
    }
    
    render() {
        console.log('🎨 render() called, currentProject:', !!this.currentProject, 'currentStage:', !!this.currentStage);
        if (!this.currentProject || !this.currentStage) {
            console.warn('⚠️ Cannot render: missing project or stage');
            return;
        }
        
        this.renderStageNav();
        this.renderCurrentStage();
        this.renderNextStagePreview();
    }
    
    renderStageNav() {
        console.log('🎨 renderStageNav() called');
        const container = this.element.querySelector('#project-stage-nav');
        if (!container) {
            console.warn('⚠️ Stage nav container not found');
            return;
        }
        
        container.innerHTML = '';
        
        console.log('Rendering', this.currentProject.stages.length, 'stages');
        this.currentProject.stages.forEach(stage => {
            const stageEl = document.createElement('div');
            stageEl.className = 'stage-nav-item';
            stageEl.textContent = stage.name;
            stageEl.style.cssText = `
                padding: 6px 12px;
                border-radius: 6px;
                font-size: 12px;
                white-space: nowrap;
                cursor: pointer;
                transition: all 0.2s;
                ${stage.id === this.currentStage.id ? 
                    'background: linear-gradient(135deg, #7B61FF 0%, #5AE0E0 100%); color: white; font-weight: bold;' : 
                    stage.completed ? 
                        'background: rgba(123, 97, 255, 0.1); color: #7B61FF;' : 
                        'background: rgba(0, 0, 0, 0.05); color: #666;'}
            `;
            
            if (stage.completed) {
                stageEl.textContent += ' ✓';
            }
            
            stageEl.onclick = () => this.navigateToStage(stage.id);
            container.appendChild(stageEl);
        });
    }
    
    renderCurrentStage() {
        const nameEl = this.element.querySelector('#current-stage-name');
        const progressEl = this.element.querySelector('#stage-progress');
        const container = this.element.querySelector('#project-items');
        
        if (!container || !this.currentStage) return;
        
        // Update stage name
        if (nameEl) {
            nameEl.textContent = this.currentStage.name;
        }
        
        // Get items for current stage
        const items = this.currentStage.items || [];
        const completedCount = items.filter(i => i.completed).length;
        const totalCount = items.length;
        
        // Update progress
        if (progressEl) {
            if (totalCount > 0) {
                const percentage = Math.round((completedCount / totalCount) * 100);
                progressEl.textContent = `${completedCount}/${totalCount} (${percentage}%)`;
            } else {
                progressEl.textContent = 'No items yet';
            }
        }
        
        // Render items
        container.innerHTML = '';
        
        if (items.length === 0) {
            container.innerHTML = '<div style="text-align: center; color: #666; font-size: 12px; padding: 20px;">No items in this stage</div>';
            return;
        }
        
        // Render hierarchical items (reuse logic from shopping.js)
        const renderItem = (item, level = 0) => {
            const isExpanded = this.expandedItems.has(item.id);
            const hasSubItems = item.sub_items && item.sub_items.length > 0;
            
            const itemEl = document.createElement('div');
            itemEl.className = 'list-item hierarchical-item';
            itemEl.dataset.itemId = item.id;
            itemEl.dataset.depth = level;
            itemEl.style.cssText = `display: flex; align-items: center; gap: 4px; padding: 8px 0; border-bottom: 1px solid rgba(255, 255, 255, 0.2);`;
            
            // Expand/collapse arrow
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
                const spacer = document.createElement('span');
                spacer.style.cssText = 'width: 16px; flex-shrink: 0;';
                itemEl.appendChild(spacer);
            }
            
            // Indent sub-items
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
            
            // Actions
            const actions = document.createElement('div');
            actions.className = 'item-actions';
            actions.style.cssText = `
                display: flex;
                gap: 2px;
                opacity: 0;
                transition: opacity 0.2s;
                flex-shrink: 0;
            `;
            
            // Move to stage button
            const moveBtn = document.createElement('button');
            moveBtn.innerHTML = '↗️';
            moveBtn.title = 'Move to stage';
            moveBtn.style.cssText = `
                width: 24px;
                height: 24px;
                border: none;
                background: none;
                cursor: pointer;
                font-size: 13px;
                border-radius: 4px;
                padding: 0;
                transition: background 0.15s;
            `;
            moveBtn.onmouseenter = () => moveBtn.style.background = 'rgba(123, 97, 255, 0.1)';
            moveBtn.onmouseleave = () => moveBtn.style.background = 'none';
            moveBtn.onclick = (e) => {
                e.stopPropagation();
                this.showMoveToStageMenu(e, item.id);
            };
            actions.appendChild(moveBtn);
            
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
                this.deleteItem(item.id);
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
        
        items.forEach(item => renderItem(item));
    }
    
    renderNextStagePreview() {
        const container = this.element.querySelector('#project-next-stage');
        const nameSpan = this.element.querySelector('#next-stage-name span');
        const itemsContainer = this.element.querySelector('#next-stage-items');
        
        if (!container || !this.currentProject) return;
        
        // Check if widget is large enough
        const widgetSize = this.element.closest('.widget')?.classList;
        const isLarge = widgetSize && (widgetSize.contains('size-large') || widgetSize.contains('size-xlarge'));
        
        if (!isLarge) {
            container.style.display = 'none';
            return;
        }
        
        // Find next stage
        const currentOrder = this.currentStage.stage_order;
        const nextStage = this.currentProject.stages.find(s => s.stage_order === currentOrder + 1);
        
        if (!nextStage) {
            container.style.display = 'none';
            return;
        }
        
        // Show preview
        container.style.display = 'block';
        if (nameSpan) {
            nameSpan.textContent = nextStage.name;
        }
        
        if (itemsContainer) {
            const itemCount = nextStage.items?.length || 0;
            itemsContainer.textContent = itemCount > 0 ? 
                `${itemCount} item${itemCount !== 1 ? 's' : ''} waiting` : 
                'No items yet';
        }
    }
    
    toggleExpand(itemId) {
        if (this.expandedItems.has(itemId)) {
            this.expandedItems.delete(itemId);
        } else {
            this.expandedItems.add(itemId);
        }
        this.render();
    }
    
    async addItem(text) {
        if (!text || !this.currentProject || !this.currentStage) return;
        
        try {
            const response = await apiRequest(
                `/api/projects/${this.currentProject.id}/stages/${this.currentStage.id}/items`,
                {
                    method: 'POST',
                    body: JSON.stringify({
                        task_text: text,
                        priority: 'medium'
                    })
                }
            );
            
            // Check for auto-advance
            if (response.auto_advanced) {
                this.showAutoAdvanceNotification(response.new_stage_id);
            }
            
            // Reload project
            await this.loadProject(this.currentProject.id);
        } catch (error) {
            console.error('Failed to add item:', error);
        }
    }
    
    async toggleItem(itemId) {
        if (!this.currentProject) return;
        
        // Find item
        const item = this.findItemById(itemId);
        if (!item) return;
        
        try {
            const response = await apiRequest(
                `/api/projects/${this.currentProject.id}/items/${itemId}`,
                {
                    method: 'PUT',
                    body: JSON.stringify({
                        completed: !item.completed
                    })
                }
            );
            
            // Check for auto-advance
            if (response.auto_advanced) {
                this.showAutoAdvanceNotification(response.new_stage_id);
            }
            
            if (response.project_complete) {
                this.showProjectCompleteNotification();
            }
            
            // Reload project
            await this.loadProject(this.currentProject.id);
        } catch (error) {
            console.error('Failed to toggle item:', error);
        }
    }
    
    async deleteItem(itemId) {
        if (!this.currentProject) return;
        
        try {
            await apiRequest(
                `/api/projects/${this.currentProject.id}/items/${itemId}`,
                {
                    method: 'DELETE'
                }
            );
            
            // Reload project
            await this.loadProject(this.currentProject.id);
        } catch (error) {
            console.error('Failed to delete item:', error);
        }
    }
    
    async navigateToStage(stageId) {
        if (!this.currentProject) return;
        
        try {
            await apiRequest(
                `/api/projects/${this.currentProject.id}/navigate`,
                {
                    method: 'PATCH',
                    body: JSON.stringify({ stage_id: stageId })
                }
            );
            
            // Reload project
            await this.loadProject(this.currentProject.id);
        } catch (error) {
            console.error('Failed to navigate to stage:', error);
        }
    }
    
    showMoveToStageMenu(event, itemId) {
        // Remove any existing menu
        const existingMenu = document.querySelector('.move-stage-menu');
        if (existingMenu) existingMenu.remove();
        
        const menu = document.createElement('div');
        menu.className = 'move-stage-menu';
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
        
        this.currentProject.stages.forEach(stage => {
            const btn = document.createElement('button');
            btn.textContent = stage.name;
            if (stage.id === this.currentStage.id) {
                btn.textContent += ' (current)';
                btn.disabled = true;
            }
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
                ${stage.id === this.currentStage.id ? 'opacity: 0.5; cursor: not-allowed;' : ''}
            `;
            if (stage.id !== this.currentStage.id) {
                btn.onmouseenter = () => {
                    btn.style.background = 'rgba(123, 97, 255, 0.1)';
                };
                btn.onmouseleave = () => {
                    btn.style.background = 'none';
                };
                btn.onclick = () => {
                    this.moveItemToStage(itemId, stage.id);
                    menu.remove();
                };
            }
            menu.appendChild(btn);
        });
        
        document.body.appendChild(menu);
        
        // Smart positioning
        const menuRect = menu.getBoundingClientRect();
        const viewportWidth = window.innerWidth;
        const viewportHeight = window.innerHeight;
        
        let left = event.clientX;
        let top = event.clientY;
        
        if (left + menuRect.width > viewportWidth - 10) {
            left = event.clientX - menuRect.width;
        }
        if (top + menuRect.height > viewportHeight - 10) {
            top = event.clientY - menuRect.height;
        }
        if (left < 10) left = 10;
        if (top < 10) top = 10;
        
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
    
    async moveItemToStage(itemId, targetStageId) {
        if (!this.currentProject) return;
        
        try {
            const response = await apiRequest(
                `/api/projects/${this.currentProject.id}/items/${itemId}/move`,
                {
                    method: 'PATCH',
                    body: JSON.stringify({ target_stage_id: targetStageId })
                }
            );
            
            // Check for auto-advance
            if (response.auto_advanced) {
                this.showAutoAdvanceNotification(response.new_stage_id);
            }
            
            // Reload project
            await this.loadProject(this.currentProject.id);
        } catch (error) {
            console.error('Failed to move item:', error);
        }
    }
    
    showAutoAdvanceNotification(newStageId) {
        const newStage = this.currentProject.stages.find(s => s.id === newStageId);
        if (!newStage) return;
        
        // Show celebration notification
        const notification = document.createElement('div');
        notification.style.cssText = `
            position: fixed;
            top: 50%;
            left: 50%;
            transform: translate(-50%, -50%);
            background: linear-gradient(135deg, #7B61FF 0%, #5AE0E0 100%);
            color: white;
            padding: 24px 32px;
            border-radius: 12px;
            box-shadow: 0 8px 24px rgba(0, 0, 0, 0.3);
            z-index: 10001;
            font-size: 16px;
            font-weight: bold;
            text-align: center;
            animation: fadeInOut 3s ease-in-out;
        `;
        notification.innerHTML = `
            <div style="font-size: 32px; margin-bottom: 8px;">🎉</div>
            <div>Stage Complete!</div>
            <div style="font-size: 14px; font-weight: normal; margin-top: 8px;">Moving to: ${newStage.name}</div>
        `;
        
        document.body.appendChild(notification);
        
        setTimeout(() => {
            notification.remove();
        }, 3000);
    }
    
    showProjectCompleteNotification() {
        const notification = document.createElement('div');
        notification.style.cssText = `
            position: fixed;
            top: 50%;
            left: 50%;
            transform: translate(-50%, -50%);
            background: linear-gradient(135deg, #7B61FF 0%, #5AE0E0 100%);
            color: white;
            padding: 32px 48px;
            border-radius: 12px;
            box-shadow: 0 8px 24px rgba(0, 0, 0, 0.3);
            z-index: 10001;
            font-size: 18px;
            font-weight: bold;
            text-align: center;
        `;
        notification.innerHTML = `
            <div style="font-size: 48px; margin-bottom: 12px;">🎊</div>
            <div>Project Complete!</div>
            <div style="font-size: 14px; font-weight: normal; margin-top: 12px;">All stages finished! 🚀</div>
        `;
        
        document.body.appendChild(notification);
        
        setTimeout(() => {
            notification.remove();
        }, 4000);
    }
    
    findItemById(itemId) {
        if (!this.currentStage || !this.currentStage.items) return null;
        
        const search = (items) => {
            for (const item of items) {
                if (item.id === itemId) return item;
                if (item.sub_items) {
                    const found = search(item.sub_items);
                    if (found) return found;
                }
            }
            return null;
        };
        
        return search(this.currentStage.items);
    }
    
    update() {
        // Reload current project if one is selected
        if (this.currentProject) {
            this.loadProject(this.currentProject.id);
        } else {
            this.loadProjects();
        }
    }
}

// Expose to window for widget system
window.ProjectsWidget = ProjectsWidget;

// Register widget
if (typeof WidgetRegistry !== 'undefined') {
    WidgetRegistry.register('project', new ProjectsWidget());
}

