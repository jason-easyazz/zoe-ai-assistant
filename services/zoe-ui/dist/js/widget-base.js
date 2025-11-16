/**
 * Widget Base Module
 * Base class for all Zoe widgets
 */

class WidgetModule {
    constructor(type, options = {}) {
        this.type = type;
        this.options = options;
        this.element = null;
        this.updateTimer = null;
    }
    
    getTemplate() {
        return '<div>Base Widget</div>';
    }
    
    init(element) {
        this.element = element;
        if (this.options.updateInterval) {
            this.startAutoUpdate();
        }
    }
    
    update() {
        // Override in child classes
    }
    
    startAutoUpdate() {
        if (this.updateTimer) {
            clearInterval(this.updateTimer);
        }
        this.updateTimer = setInterval(() => this.update(), this.options.updateInterval);
    }
    
    destroy() {
        if (this.updateTimer) {
            clearInterval(this.updateTimer);
            this.updateTimer = null;
        }
    }
}
