/**
 * Widget Registry
 * ===============
 * 
 * Central registry for all available widgets (core + module-provided).
 * Widgets self-register when their scripts load.
 * 
 * Features:
 * - Widget registration and lookup
 * - Widget instantiation
 * - Category-based organization
 * - Module widget discovery
 * 
 * Usage:
 *   // Widget self-registers
 *   WidgetRegistry.register(MyWidgetClass);
 *   
 *   // Get available widgets
 *   const widgets = WidgetRegistry.getAll();
 *   
 *   // Instantiate widget
 *   const widget = WidgetRegistry.create('music-player', container);
 * 
 * Version: 1.0.0
 */

class WidgetRegistryClass {
    constructor() {
        this.widgets = new Map();
        this.instances = new Map();
        this.moduleWidgets = new Map();
    }

    /**
     * Register a widget class
     * @param {Function} WidgetClass - The widget class constructor
     * @param {Object} metadata - Widget metadata (optional if class has static metadata)
     */
    register(WidgetClass, metadata = null) {
        const meta = metadata || WidgetClass.metadata || {};
        
        if (!meta.id) {
            console.error('❌ Widget registration failed: No ID provided', WidgetClass);
            return false;
        }
        
        // Store widget class with metadata
        this.widgets.set(meta.id, {
            class: WidgetClass,
            metadata: meta,
            isModule: !!meta.module
        });
        
        console.log(`✅ Widget registered: ${meta.id} (${meta.name || meta.id})`);
        
        // Track module widgets separately
        if (meta.module) {
            if (!this.moduleWidgets.has(meta.module)) {
                this.moduleWidgets.set(meta.module, []);
            }
            this.moduleWidgets.get(meta.module).push(meta.id);
        }
        
        return true;
    }

    /**
     * Register a widget from metadata (for lazy loading)
     */
    registerMetadata(widgetMetadata) {
        // Store metadata only, class will be loaded later
        if (!widgetMetadata.id) {
            console.error('❌ Widget metadata registration failed: No ID', widgetMetadata);
            return false;
        }
        
        this.widgets.set(widgetMetadata.id, {
            class: null, // Will be loaded on demand
            metadata: widgetMetadata,
            isModule: !!widgetMetadata.module,
            lazyLoad: true
        });
        
        console.log(`📝 Widget metadata registered: ${widgetMetadata.id} (lazy load)`);
        
        if (widgetMetadata.module) {
            if (!this.moduleWidgets.has(widgetMetadata.module)) {
                this.moduleWidgets.set(widgetMetadata.module, []);
            }
            this.moduleWidgets.get(widgetMetadata.module).push(widgetMetadata.id);
        }
        
        return true;
    }

    /**
     * Create a widget instance
     */
    async create(widgetId, container, options = {}) {
        const entry = this.widgets.get(widgetId);
        if (!entry) {
            throw new Error(`Widget ${widgetId} not found in registry`);
        }
        
        // Lazy load if needed
        if (entry.lazyLoad && !entry.class) {
            console.log(`📥 Lazy loading widget: ${widgetId}`);
            await this.loadWidget(widgetId);
        }
        
        const WidgetClass = entry.class;
        if (!WidgetClass) {
            throw new Error(`Widget ${widgetId} class not loaded`);
        }
        
        // Instantiate widget
        const instance = new WidgetClass(options);
        
        // Store instance
        const instanceId = `${widgetId}-${Date.now()}`;
        this.instances.set(instanceId, {
            id: widgetId,
            instance,
            container
        });
        
        // Initialize widget
        if (instance.init) {
            await instance.init(container);
        }
        
        console.log(`✅ Widget created: ${widgetId}`);
        
        return {
            instanceId,
            instance,
            metadata: entry.metadata
        };
    }

    /**
     * Load a widget script (for lazy loading)
     */
    async loadWidget(widgetId) {
        const entry = this.widgets.get(widgetId);
        if (!entry || !entry.lazyLoad) {
            return;
        }
        
        const meta = entry.metadata;
        if (!window.ModuleWidgetLoader) {
            throw new Error('ModuleWidgetLoader not available');
        }
        
        const loader = window.moduleWidgetLoader;
        if (!loader) {
            throw new Error('ModuleWidgetLoader not initialized');
        }
        
        // Load the widget
        await loader.loadWidget(widgetId);
        
        // Widget should have self-registered by now
        const updated = this.widgets.get(widgetId);
        if (!updated.class) {
            throw new Error(`Widget ${widgetId} did not self-register after loading`);
        }
    }

    /**
     * Get widget by ID
     */
    get(widgetId) {
        const entry = this.widgets.get(widgetId);
        return entry ? entry.metadata : null;
    }

    /**
     * Get all registered widgets
     */
    getAll() {
        return Array.from(this.widgets.entries()).map(([id, entry]) => ({
            id,
            ...entry.metadata,
            loaded: !entry.lazyLoad || !!entry.class
        }));
    }

    /**
     * Get widgets by category
     */
    getByCategory(category) {
        return this.getAll().filter(w => w.category === category);
    }

    /**
     * Get widgets by module
     */
    getByModule(moduleName) {
        const widgetIds = this.moduleWidgets.get(moduleName) || [];
        return widgetIds.map(id => this.get(id)).filter(Boolean);
    }

    /**
     * Get all module widgets
     */
    getModuleWidgets() {
        return this.getAll().filter(w => w.module);
    }

    /**
     * Get all core widgets
     */
    getCoreWidgets() {
        return this.getAll().filter(w => !w.module);
    }

    /**
     * Check if widget exists
     */
    has(widgetId) {
        return this.widgets.has(widgetId);
    }

    /**
     * Unregister a widget
     */
    unregister(widgetId) {
        const entry = this.widgets.get(widgetId);
        if (!entry) {
            return false;
        }
        
        // Remove from module tracking
        if (entry.metadata.module) {
            const moduleWidgets = this.moduleWidgets.get(entry.metadata.module);
            if (moduleWidgets) {
                const index = moduleWidgets.indexOf(widgetId);
                if (index > -1) {
                    moduleWidgets.splice(index, 1);
                }
            }
        }
        
        this.widgets.delete(widgetId);
        console.log(`❌ Widget unregistered: ${widgetId}`);
        
        return true;
    }

    /**
     * Get widget instance by instance ID
     */
    getInstance(instanceId) {
        return this.instances.get(instanceId);
    }

    /**
     * Destroy a widget instance
     */
    destroyInstance(instanceId) {
        const entry = this.instances.get(instanceId);
        if (!entry) {
            return false;
        }
        
        // Call destroy if available
        if (entry.instance.destroy) {
            entry.instance.destroy();
        }
        
        this.instances.delete(instanceId);
        return true;
    }

    /**
     * Get categories
     */
    getCategories() {
        const categories = new Set();
        for (const widget of this.getAll()) {
            if (widget.category) {
                categories.add(widget.category);
            }
        }
        return Array.from(categories);
    }

    /**
     * Clear all registered widgets (for testing)
     */
    clear() {
        this.widgets.clear();
        this.instances.clear();
        this.moduleWidgets.clear();
        console.log('🧹 Widget registry cleared');
    }
}

// Create global singleton
const WidgetRegistry = new WidgetRegistryClass();

// Export
if (typeof window !== 'undefined') {
    window.WidgetRegistry = WidgetRegistry;
}

if (typeof module !== 'undefined' && module.exports) {
    module.exports = WidgetRegistry;
}
