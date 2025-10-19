/**
 * Widget Module Base Class
 * All widgets extend from this class to inherit core lifecycle management
 * Version: 1.0.0
 */

class WidgetModule {
    /**
     * Constructor for widget module
     * @param {string} name - Widget identifier
     * @param {object} config - Widget configuration
     */
    constructor(name, config = {}) {
        this.name = name;
        this.version = config.version || '1.0.0';
        this.dependencies = config.dependencies || [];
        this.defaultSize = config.defaultSize || 'size-small';
        this.updateInterval = config.updateInterval || null;
        this.isEnabled = true;
        this.element = null;
        this.updateTimer = null;
        this.config = config;
    }
    
    /**
     * Template method - must be implemented by widgets
     * @returns {string} HTML template for widget
     */
    getTemplate() {
        throw new Error(`Widget ${this.name} must implement getTemplate()`);
    }
    
    /**
     * Initialize widget
     * @param {HTMLElement} element - Container element for widget
     */
    init(element) {
        this.element = element;
        this.element.setAttribute('data-widget-type', this.name);
        this.element.classList.add(this.defaultSize);
        
        // Apply template
        this.element.innerHTML = this.getTemplate();
        
        // Setup update interval if specified
        if (this.updateInterval) {
            this.startUpdates();
        }
        
        console.log(`✅ Widget ${this.name} initialized`);
    }
    
    /**
     * Start automatic updates
     */
    startUpdates() {
        if (this.updateTimer) {
            clearInterval(this.updateTimer);
        }
        this.updateTimer = setInterval(() => {
            this.update();
        }, this.updateInterval);
    }
    
    /**
     * Stop automatic updates
     */
    stopUpdates() {
        if (this.updateTimer) {
            clearInterval(this.updateTimer);
            this.updateTimer = null;
        }
    }
    
    /**
     * Update method - can be overridden by widgets
     * Called automatically if updateInterval is set
     */
    update() {
        // Default implementation does nothing
        // Widgets should override this method
    }
    
    /**
     * Destroy widget and cleanup resources
     */
    destroy() {
        this.stopUpdates();
        if (this.element) {
            this.element.remove();
            this.element = null;
        }
    }
    
    /**
     * Resize widget to new size
     * @param {string} newSize - Size class (size-small, size-medium, size-large, size-xlarge)
     */
    resize(newSize) {
        if (this.element) {
            // Remove old size class
            const currentSize = Array.from(this.element.classList).find(cls => cls.startsWith('size-'));
            if (currentSize) {
                this.element.classList.remove(currentSize);
            }
            // Add new size class
            this.element.classList.add(newSize);
            
            // Update size indicator
            const sizeIndicator = this.element.querySelector('.size-indicator');
            if (sizeIndicator) {
                sizeIndicator.textContent = newSize.replace('size-', '').charAt(0).toUpperCase() + 
                                          newSize.replace('size-', '').slice(1);
            }
        }
    }
    
    /**
     * Get widget configuration
     * @returns {object}
     */
    getConfig() {
        return this.config;
    }
    
    /**
     * Update widget configuration
     * @param {object} newConfig - New configuration object
     */
    updateConfig(newConfig) {
        this.config = { ...this.config, ...newConfig };
        
        // Update relevant properties
        if (newConfig.defaultSize) this.defaultSize = newConfig.defaultSize;
        if (newConfig.updateInterval !== undefined) {
            this.updateInterval = newConfig.updateInterval;
            if (this.updateInterval) {
                this.startUpdates();
            } else {
                this.stopUpdates();
            }
        }
    }
    
    /**
     * Emit custom event from widget
     * @param {string} eventName - Event name
     * @param {*} data - Event data
     */
    emit(eventName, data) {
        if (this.element) {
            const event = new CustomEvent(`widget:${eventName}`, {
                detail: { widgetName: this.name, data },
                bubbles: true
            });
            this.element.dispatchEvent(event);
        }
    }
    
    /**
     * Listen to widget events
     * @param {string} eventName - Event name
     * @param {Function} handler - Event handler function
     */
    on(eventName, handler) {
        if (this.element) {
            this.element.addEventListener(`widget:${eventName}`, (e) => {
                handler(e.detail.data);
            });
        }
    }
    
    /**
     * Set widget loading state
     * @param {boolean} isLoading - Loading state
     */
    setLoading(isLoading) {
        if (this.element) {
            if (isLoading) {
                this.element.classList.add('loading');
            } else {
                this.element.classList.remove('loading');
            }
        }
    }
    
    /**
     * Set widget error state
     * @param {string} error - Error message
     */
    setError(error) {
        if (this.element) {
            this.element.classList.add('error');
            const errorEl = this.element.querySelector('.widget-error') || document.createElement('div');
            errorEl.className = 'widget-error';
            errorEl.textContent = error;
            if (!this.element.querySelector('.widget-error')) {
                this.element.appendChild(errorEl);
            }
        }
    }
    
    /**
     * Clear widget error state
     */
    clearError() {
        if (this.element) {
            this.element.classList.remove('error');
            const errorEl = this.element.querySelector('.widget-error');
            if (errorEl) {
                errorEl.remove();
            }
        }
    }
    
    /**
     * Refresh widget data
     * Convenience method that calls update()
     */
    refresh() {
        this.update();
    }
}

// Export for use in modules
if (typeof module !== 'undefined' && module.exports) {
    module.exports = WidgetModule;
}




