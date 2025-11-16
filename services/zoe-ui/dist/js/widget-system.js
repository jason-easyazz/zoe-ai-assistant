/**
 * Widget System Manager
 * Handles widget creation, registration, and lifecycle
 * Version: 2.0.0 - Now with manifest-based registration
 */

const WidgetManager = {
    modules: {},
    manifest: null,
    manifestLoaded: false,
    
    /**
     * Load widget manifest from JSON file
     */
    async loadManifest() {
        if (this.manifestLoaded) return this.manifest;
        
        try {
            const response = await fetch('/js/widgets/widget-manifest.json');
            if (!response.ok) throw new Error('Manifest not found');
            
            this.manifest = await response.json();
            this.manifestLoaded = true;
            console.log('📋 Widget manifest loaded:', this.manifest.widgets.length, 'widgets');
            return this.manifest;
        } catch (error) {
            console.warn('⚠️ Could not load widget manifest:', error);
            return null;
        }
    },
    
    /**
     * Register a widget module
     */
    register(module) {
        this.modules[module.type] = module;
        console.log(`✓ Registered widget: ${module.type}`);
    },
    
    /**
     * Get widget configuration from manifest
     */
    getWidgetConfig(widgetId) {
        if (!this.manifest) return null;
        return this.manifest.widgets.find(w => w.id === widgetId);
    },
    
    /**
     * Get available widgets for a specific page type
     */
    getAvailableWidgets(pageType = 'dashboard') {
        if (!this.manifest) return [];
        return this.manifest.widgets.filter(w => w[pageType] === true);
    },
    
    /**
     * Get all widget categories
     */
    getCategories() {
        if (!this.manifest) return [];
        return this.manifest.categories || [];
    },
    
    /**
     * Create widget element
     */
    createWidget(type, container) {
        const module = this.modules[type];
        if (!module) {
            console.error(`❌ Widget module not found: ${type}`);
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
        
        console.log(`✅ Created widget: ${type} (${defaultSize})`);
        return widget;
    },
    
    resizeWidget(widget, sizeClass) {
        // Remove existing size classes
        widget.classList.remove('size-small', 'size-medium', 'size-large', 'size-xlarge');
        // Add new size class
        widget.classList.add(sizeClass);
        console.log(`📏 Resized widget to: ${sizeClass}`);
    },
    
    destroyWidget(widget) {
        const type = widget.getAttribute('data-widget-type');
        const module = this.modules[type];
        if (module && module.destroy) {
            module.destroy();
        }
        widget.remove();
        console.log(`🗑️ Destroyed widget: ${type}`);
    },
    
    updateAll() {
        // Update all registered widget modules that have an update method
        console.log('🔄 Updating all widgets...');
        Object.values(this.modules).forEach(module => {
            if (module && typeof module.update === 'function') {
                try {
                    module.update();
                } catch (error) {
                    console.error(`❌ Failed to update ${module.type}:`, error);
                }
            }
        });
    }
};

/**
 * Map manifest widget name to actual class name
 * Converts display names like "Time & Date" to class names like "TimeWidget"
 */
function getWidgetClassName(manifestName) {
    // Special case mappings
    const specialCases = {
        'Time & Date': 'TimeWidget',
        'Zoe AI': 'ZoeOrbWidget',
        'Week Planner': 'WeekPlannerWidget',
        'Dynamic List': 'DynamicListWidget',
        'Calendar Events': 'EventsWidget',
        'Home Control': 'HomeWidget',
        'Quick Notes': 'NotesWidget',
        'Shopping List': 'ShoppingWidget',
        'System Status': 'SystemWidget',
        'Bucket List': 'BucketWidget'
    };
    
    // Check special cases first
    if (specialCases[manifestName]) {
        return specialCases[manifestName];
    }
    
    // Default: take the name, remove spaces/special chars, add "Widget"
    // For single words like "Weather", "Tasks", "Journal", "Reminders", "Personal", "Work"
    const cleaned = manifestName.replace(/[^a-zA-Z0-9]/g, '');
    return cleaned + 'Widget';
}

/**
 * Wait for a widget class to be available in window
 */
async function waitForWidgetClass(className, maxRetries = 10, delay = 100) {
    for (let i = 0; i < maxRetries; i++) {
        if (typeof window[className] !== 'undefined') {
            return true;
        }
        await new Promise(resolve => setTimeout(resolve, delay));
    }
    return false;
}

/**
 * Automatic widget registration
 * Scans for widget classes in window object and registers them
 */
async function registerAllWidgets() {
    // Load manifest first
    await WidgetManager.loadManifest();
    
    if (!WidgetManager.manifest) {
        console.error('❌ Cannot register widgets without manifest');
        return 0;
    }
    
    // Wait for DOM to be ready and scripts to execute
    if (document.readyState === 'loading') {
        await new Promise(resolve => {
            if (document.readyState === 'loading') {
                document.addEventListener('DOMContentLoaded', resolve);
            } else {
                resolve();
            }
        });
    }
    
    // Additional small delay to ensure all scripts have executed
    await new Promise(resolve => setTimeout(resolve, 50));
    
    // Debug: Log available widget classes
    const availableWidgetClasses = Object.keys(window).filter(key => 
        key.endsWith('Widget') && typeof window[key] === 'function'
    );
    console.debug('🔍 Available widget classes:', availableWidgetClasses);
    
    let registered = 0;
    const widgetManifest = WidgetManager.manifest.widgets;
    const missingWidgets = [];
    
    // Register widgets based on manifest
    for (const widgetMeta of widgetManifest) {
        const manifestName = widgetMeta.name;
        const className = getWidgetClassName(manifestName);
        
        // Wait for widget class to be available (with retry)
        const isAvailable = await waitForWidgetClass(className, 5, 50);
        
        if (isAvailable && typeof window[className] !== 'undefined') {
            try {
                const instance = new window[className]();
                WidgetManager.register(instance);
                registered++;
            } catch (error) {
                console.error(`❌ Failed to instantiate ${className} (${manifestName}):`, error);
                missingWidgets.push({ name: manifestName, className, error: error.message });
            }
        } else {
            console.warn(`⚠️ Widget class not found: ${className} (manifest name: ${manifestName})`);
            console.debug(`   Checking window.${className}, available: ${typeof window[className]}`);
            missingWidgets.push({ name: manifestName, className });
        }
    }
    
    if (missingWidgets.length > 0) {
        console.warn(`⚠️ ${missingWidgets.length} widgets failed to register:`, missingWidgets.map(w => w.name).join(', '));
        console.debug('Missing widget details:', missingWidgets);
    }
    
    console.log(`✅ Registered ${registered}/${widgetManifest.length} widget modules`);
    return registered;
}

/**
 * Get widget configuration by type (wrapper for backward compatibility)
 */
function getWidgetConfigs() {
    if (!WidgetManager.manifest) {
        console.warn('⚠️ Manifest not loaded, using fallback configs');
        return {};
    }
    
    const configs = {};
    WidgetManager.manifest.widgets.forEach(widget => {
        configs[widget.id] = widget.config;
    });
    
    return configs;
}
