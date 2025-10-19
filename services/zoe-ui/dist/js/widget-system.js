/**
 * Zoe Widget System - Core Framework
 * Based on MagicMirror², Grafana, and Home Assistant patterns
 * Version: 1.0.0
 */

/**
 * Widget Registry - Central registry for all widget modules
 * Manages widget registration, versioning, and dependency tracking
 */
const WidgetRegistry = {
    widgets: new Map(),
    versions: new Map(),
    dependencies: new Map(),
    
    /**
     * Register a widget module
     * @param {string} name - Widget identifier
     * @param {WidgetModule} widgetModule - Widget instance
     */
    register(name, widgetModule) {
        console.log(`📦 Registering widget: ${name} v${widgetModule.version}`);
        this.widgets.set(name, widgetModule);
        this.versions.set(name, widgetModule.version);
        if (widgetModule.dependencies) {
            this.dependencies.set(name, widgetModule.dependencies);
        }
    },
    
    /**
     * Get widget module by name
     * @param {string} name - Widget identifier
     * @returns {WidgetModule|undefined}
     */
    get(name) {
        return this.widgets.get(name);
    },
    
    /**
     * Get all registered widget names
     * @returns {string[]}
     */
    getAll() {
        return Array.from(this.widgets.keys());
    },
    
    /**
     * Check if widget exists
     * @param {string} name - Widget identifier
     * @returns {boolean}
     */
    has(name) {
        return this.widgets.has(name);
    },
    
    /**
     * Get widget version
     * @param {string} name - Widget identifier
     * @returns {string|undefined}
     */
    getVersion(name) {
        return this.versions.get(name);
    },
    
    /**
     * Update existing widget
     * @param {string} name - Widget identifier
     * @param {WidgetModule} newModule - New widget instance
     * @returns {boolean}
     */
    update(name, newModule) {
        if (this.widgets.has(name)) {
            console.log(`🔄 Updating widget: ${name} v${newModule.version}`);
            this.widgets.set(name, newModule);
            this.versions.set(name, newModule.version);
            return true;
        }
        return false;
    },
    
    /**
     * Unregister a widget
     * @param {string} name - Widget identifier
     * @returns {boolean}
     */
    unregister(name) {
        if (this.widgets.has(name)) {
            console.log(`🗑️ Unregistering widget: ${name}`);
            this.widgets.delete(name);
            this.versions.delete(name);
            this.dependencies.delete(name);
            return true;
        }
        return false;
    },
    
    /**
     * Get widget metadata
     * @param {string} name - Widget identifier
     * @returns {object}
     */
    getMetadata(name) {
        const widget = this.get(name);
        if (!widget) return null;
        
        return {
            name: widget.name,
            version: widget.version,
            dependencies: widget.dependencies || [],
            defaultSize: widget.defaultSize,
            updateInterval: widget.updateInterval,
            isEnabled: widget.isEnabled
        };
    }
};

/**
 * Widget Manager - Handles widget lifecycle and DOM management
 */
const WidgetManager = {
    activeWidgets: new Map(),
    
    /**
     * Create and add widget to grid
     * @param {string} type - Widget type/name
     * @param {HTMLElement} grid - Container element
     * @returns {HTMLElement|null}
     */
    createWidget(type, grid) {
        const widgetModule = WidgetRegistry.get(type);
        if (!widgetModule) {
            console.error(`Widget type '${type}' not found in registry`);
            return null;
        }
        
        const element = document.createElement('div');
        element.className = 'widget new';
        
        // Initialize the widget
        widgetModule.init(element);
        
        // Add to grid
        grid.appendChild(element);
        
        // Track active widget
        this.activeWidgets.set(element, widgetModule);
        
        // Setup drag and drop if function exists
        if (typeof setupDragAndDrop === 'function') {
            this.setupWidgetInteractions(element);
        }
        
        // Remove 'new' class after animation
        setTimeout(() => element.classList.remove('new'), 500);
        
        console.log(`✅ Created widget: ${type}`);
        return element;
    },
    
    /**
     * Remove widget from DOM and cleanup
     * @param {HTMLElement} element - Widget element
     */
    removeWidget(element) {
        const widgetModule = this.activeWidgets.get(element);
        if (widgetModule) {
            widgetModule.destroy();
            this.activeWidgets.delete(element);
        }
        element.remove();
    },
    
    /**
     * Resize widget
     * @param {HTMLElement} element - Widget element
     * @param {string} newSize - Size class (size-small, size-medium, etc.)
     */
    resizeWidget(element, newSize) {
        const widgetModule = this.activeWidgets.get(element);
        if (widgetModule && widgetModule.resize) {
            widgetModule.resize(newSize);
        } else {
            // Fallback: direct DOM manipulation
            const currentSize = Array.from(element.classList).find(cls => cls.startsWith('size-'));
            if (currentSize) {
                element.classList.remove(currentSize);
            }
            element.classList.add(newSize);
            
            // Update size indicator
            const sizeIndicator = element.querySelector('.size-indicator');
            if (sizeIndicator) {
                sizeIndicator.textContent = newSize.replace('size-', '').charAt(0).toUpperCase() + 
                                          newSize.replace('size-', '').slice(1);
            }
        }
    },
    
    /**
     * Setup widget interactions (drag, drop, click)
     * @param {HTMLElement} element - Widget element
     */
    setupWidgetInteractions(element) {
        // Desktop drag and drop
        element.draggable = true;
        element.addEventListener('dragstart', handleDragStart);
        element.addEventListener('dragend', handleDragEnd);
        
        // Touch handlers
        element.addEventListener('touchstart', handleTouchStart, { passive: false });
        element.addEventListener('touchmove', handleTouchMove, { passive: false });
        element.addEventListener('touchend', handleTouchEnd, { passive: false });
    },
    
    /**
     * Get all active widget elements
     * @returns {HTMLElement[]}
     */
    getAllActive() {
        return Array.from(this.activeWidgets.keys());
    },
    
    /**
     * Update all active widgets
     */
    updateAll() {
        this.activeWidgets.forEach((module, element) => {
            if (module.update) {
                module.update();
            }
        });
    },
    
    /**
     * Get widget instance from element
     * @param {HTMLElement} element - Widget element
     * @returns {WidgetModule|undefined}
     */
    getWidget(element) {
        return this.activeWidgets.get(element);
    },
    
    /**
     * Find widgets by type
     * @param {string} type - Widget type/name
     * @returns {HTMLElement[]}
     */
    findWidgetsByType(type) {
        const widgets = [];
        this.activeWidgets.forEach((module, element) => {
            if (module.name === type) {
                widgets.push(element);
            }
        });
        return widgets;
    }
};

/**
 * Widget Updater - Handles widget version updates and notifications
 */
const WidgetUpdater = {
    /**
     * Check for widget updates from backend
     */
    async checkForUpdates() {
        console.log('🔄 Checking for widget updates...');
        try {
            const response = await fetch('/api/widgets/updates');
            const updates = await response.json();
            
            if (updates.length > 0) {
                console.log(`📦 Found ${updates.length} widget updates:`, updates);
                this.showUpdateNotification(updates);
            } else {
                console.log('✅ All widgets are up to date');
            }
        } catch (error) {
            console.error('Failed to check for updates:', error);
        }
    },
    
    /**
     * Show update notification UI
     * @param {Array} updates - Array of available updates
     */
    showUpdateNotification(updates) {
        const notification = document.createElement('div');
        notification.className = 'update-notification';
        notification.innerHTML = `
            <div class="update-content">
                <h3>📦 Widget Updates Available</h3>
                <ul>
                    ${updates.map(update => `
                        <li>
                            <strong>${update.name}</strong> 
                            v${update.currentVersion} → v${update.newVersion}
                            <button onclick="WidgetUpdater.updateWidget('${update.name}')">Update</button>
                        </li>
                    `).join('')}
                </ul>
                <div class="update-actions">
                    <button onclick="WidgetUpdater.updateAll()">Update All</button>
                    <button onclick="this.parentElement.parentElement.parentElement.remove()">Dismiss</button>
                </div>
            </div>
        `;
        
        // Add styles
        notification.style.cssText = `
            position: fixed; top: 20px; right: 20px; z-index: 10000;
            background: white; border: 1px solid #ddd; border-radius: 8px;
            box-shadow: 0 4px 20px rgba(0,0,0,0.15); padding: 20px;
            max-width: 400px; font-family: -apple-system, BlinkMacSystemFont, sans-serif;
        `;
        
        document.body.appendChild(notification);
        
        // Auto-dismiss after 30 seconds
        setTimeout(() => {
            if (notification.parentElement) {
                notification.remove();
            }
        }, 30000);
    },
    
    /**
     * Update a specific widget
     * @param {string} widgetName - Widget identifier
     */
    async updateWidget(widgetName) {
        try {
            console.log(`🔄 Updating widget: ${widgetName}`);
            const response = await fetch(`/api/widgets/update/${widgetName}`, {
                method: 'POST'
            });
            
            if (response.ok) {
                const newModule = await response.json();
                WidgetRegistry.update(widgetName, newModule);
                
                // Recreate active widgets with new version
                this.recreateActiveWidget(widgetName);
                
                console.log(`✅ Widget ${widgetName} updated successfully`);
                this.showUpdateSuccess(widgetName);
            } else {
                throw new Error('Update failed');
            }
        } catch (error) {
            console.error(`Failed to update widget ${widgetName}:`, error);
            this.showUpdateError(widgetName);
        }
    },
    
    /**
     * Update all widgets
     */
    async updateAll() {
        console.log('🔄 Updating all widgets...');
        try {
            const response = await fetch('/api/widgets/update-all', {
                method: 'POST'
            });
            
            if (response.ok) {
                const updates = await response.json();
                
                // Update registry
                updates.forEach(update => {
                    WidgetRegistry.update(update.name, update.module);
                });
                
                // Recreate all active widgets
                this.recreateAllActiveWidgets();
                
                console.log('✅ All widgets updated successfully');
                this.showUpdateSuccess('All widgets');
            } else {
                throw new Error('Bulk update failed');
            }
        } catch (error) {
            console.error('Failed to update all widgets:', error);
            this.showUpdateError('All widgets');
        }
    },
    
    /**
     * Recreate active widget with new version
     * @param {string} widgetName - Widget identifier
     */
    recreateActiveWidget(widgetName) {
        const activeWidgets = WidgetManager.getAllActive();
        activeWidgets.forEach(element => {
            if (element.getAttribute('data-widget-type') === widgetName) {
                const newModule = WidgetRegistry.get(widgetName);
                if (newModule) {
                    // Destroy old widget
                    WidgetManager.removeWidget(element);
                    
                    // Create new widget
                    const grid = document.getElementById('widgetGrid');
                    if (grid) {
                        WidgetManager.createWidget(widgetName, grid);
                    }
                }
            }
        });
    },
    
    /**
     * Recreate all active widgets
     */
    recreateAllActiveWidgets() {
        const activeWidgets = WidgetManager.getAllActive();
        const widgetTypes = activeWidgets.map(el => el.getAttribute('data-widget-type'));
        
        // Clear grid
        const grid = document.getElementById('widgetGrid');
        if (grid) {
            grid.innerHTML = '';
            
            // Recreate all widgets
            widgetTypes.forEach(type => {
                WidgetManager.createWidget(type, grid);
            });
            
            // Save layout if function exists
            if (typeof saveWidgetLayout === 'function') {
                saveWidgetLayout();
            }
        }
    },
    
    /**
     * Show update success message
     * @param {string} widgetName - Widget identifier
     */
    showUpdateSuccess(widgetName) {
        const notification = document.createElement('div');
        notification.className = 'update-success';
        notification.textContent = `✅ ${widgetName} updated successfully!`;
        notification.style.cssText = `
            position: fixed; top: 20px; right: 20px; z-index: 10000;
            background: #4CAF50; color: white; padding: 12px 20px;
            border-radius: 6px; font-family: -apple-system, BlinkMacSystemFont, sans-serif;
        `;
        
        document.body.appendChild(notification);
        setTimeout(() => notification.remove(), 3000);
    },
    
    /**
     * Show update error message
     * @param {string} widgetName - Widget identifier
     */
    showUpdateError(widgetName) {
        const notification = document.createElement('div');
        notification.className = 'update-error';
        notification.textContent = `❌ Failed to update ${widgetName}`;
        notification.style.cssText = `
            position: fixed; top: 20px; right: 20px; z-index: 10000;
            background: #f44336; color: white; padding: 12px 20px;
            border-radius: 6px; font-family: -apple-system, BlinkMacSystemFont, sans-serif;
        `;
        
        document.body.appendChild(notification);
        setTimeout(() => notification.remove(), 5000);
    }
};

// Export for use in modules
if (typeof module !== 'undefined' && module.exports) {
    module.exports = { WidgetRegistry, WidgetManager, WidgetUpdater };
}




