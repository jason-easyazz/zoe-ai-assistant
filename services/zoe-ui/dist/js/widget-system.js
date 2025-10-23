/**
 * Widget System Manager
 * Handles widget creation, registration, and lifecycle
 */

const WidgetManager = {
    modules: {},
    
    register(module) {
        this.modules[module.type] = module;
        console.log(`Registered widget: ${module.type}`);
    },
    
    createWidget(type, container) {
        const module = this.modules[type];
        if (!module) {
            console.error(`Widget module not found: ${type}`);
            return null;
        }
        
        // Create widget element
        const widget = document.createElement('div');
        
        // Use module's defaultSize if specified, otherwise size-small
        const defaultSize = module.options?.defaultSize || 'size-small';
        widget.className = `widget ${defaultSize}`;
        widget.setAttribute('data-widget-type', type);
        widget.innerHTML = module.getTemplate();
        
        // Add to container
        container.appendChild(widget);
        
        // Initialize the module
        module.init(widget);
        
        console.log(`Created widget: ${type} (${defaultSize})`);
        return widget;
    },
    
    resizeWidget(widget, sizeClass) {
        // Remove existing size classes
        widget.classList.remove('size-small', 'size-medium', 'size-large', 'size-xlarge');
        // Add new size class
        widget.classList.add(sizeClass);
        console.log(`Resized widget to: ${sizeClass}`);
    },
    
    destroyWidget(widget) {
        const type = widget.getAttribute('data-widget-type');
        const module = this.modules[type];
        if (module && module.destroy) {
            module.destroy();
        }
        widget.remove();
        console.log(`Destroyed widget: ${type}`);
    },
    
    updateAll() {
        // Update all registered widget modules that have an update method
        console.log('🔄 Updating all widgets...');
        Object.values(this.modules).forEach(module => {
            if (module && typeof module.update === 'function') {
                try {
                    module.update();
                } catch (error) {
                    console.error(`Failed to update ${module.type}:`, error);
                }
            }
        });
    }
};

// Manual registration function - call this after all widget scripts load
function registerAllWidgets() {
    const widgetClasses = [
        'EventsWidget', 'TasksWidget', 'TimeWidget', 'WeatherWidget',
        'HomeWidget', 'SystemWidget', 'NotesWidget', 'ZoeOrbWidget',
        'ShoppingWidget', 'PersonalWidget', 'WorkWidget', 'BucketWidget',
        'RemindersWidget', 'DynamicListWidget'
    ];
    
    let registered = 0;
    widgetClasses.forEach(className => {
        if (typeof window[className] !== 'undefined') {
            WidgetManager.register(new window[className]());
            registered++;
        } else {
            console.warn(`Widget class not found: ${className}`);
        }
    });
    
    console.log('✅ Registered', registered, 'of', widgetClasses.length, 'widget modules');
    return registered;
}
