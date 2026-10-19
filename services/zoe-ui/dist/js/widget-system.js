/**
 * Widget System Manager
 * Handles widget creation, registration, and lifecycle
 * Version: 2.0.0 - Now with manifest-based registration
 */

const WidgetManager = {
    modules: {},
    // Widget INSTANCES, one per card on the board (Wave 4b, 2026-10-19). `modules`
    // holds ONE object per type, and the dashboard used to init() that same object
    // for every card of the type - two weather cards shared one `this.element` and
    // one update timer, so the second clobbered the first. The dashboard now asks
    // createInstance() for a fresh object per card and tracks it here by its uid.
    // Pages that still use `modules` directly (lists) are unchanged.
    instances: new Map(),
    // Sticky once a page mounts cards as instances (the dashboard). It must not be
    // inferred from instances.size: after the last card is removed the map is
    // empty, and a refresh would then fall back to the never-init()ed templates.
    instanceMode: false,
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
     * A NEW widget object of this type - never the shared `modules[type]`.
     * Falls back to the registered object only for a module that is not a class
     * instance (a plain object cannot be constructed again).
     */
    createInstance(type) {
        if (!Object.prototype.hasOwnProperty.call(this.modules, type)) return null;
        const proto = this.modules[type];
        if (!proto) return null;
        const Ctor = proto.constructor;
        if (typeof Ctor === 'function' && Ctor !== Object) {
            try { return new Ctor(); } catch (e) {
                console.error('Could not create a ' + type + ' widget:', e);
                return null;
            }
        }
        return proto;
    },

    /** Called by a page that mounts one instance per card (js/dashboard.js). */
    useInstances() {
        this.instanceMode = true;
    },

    trackInstance(uid, instance) {
        this.instanceMode = true;
        if (uid && instance) this.instances.set(uid, instance);
    },

    /** Stop a card's timers/listeners and forget it. */
    releaseInstance(uid) {
        const inst = this.instances.get(uid);
        if (!inst) return;
        this.instances.delete(uid);
        if (typeof inst.destroy === 'function') {
            try { inst.destroy(); } catch (e) { console.warn('widget destroy failed:', e); }
        }
    },

    releaseAll() {
        Array.from(this.instances.keys()).forEach(uid => this.releaseInstance(uid));
    },

    /**
     * Every live object of this type. On a page that tracks instances (the
     * dashboard) that is its cards of the type - possibly none; the registered
     * module there is a never-init()ed template. Elsewhere it is the module.
     */
    liveOfType(type) {
        if (this.instanceMode) {
            const out = [];
            this.instances.forEach(inst => { if (inst && inst.type === type) out.push(inst); });
            return out;
        }
        return this.modules[type] ? [this.modules[type]] : [];
    },

    /**
     * Refresh every live card of a type. Returns true when the refresh is handled
     * (always, on a page that tracks instances - a type with no card has nothing
     * to refresh), so a caller never falls back to the template module.
     */
    updateType(type) {
        let handled = this.instanceMode;
        this.liveOfType(type).forEach(inst => {
            if (inst && typeof inst.update === 'function') {
                handled = true;
                try { inst.update(); } catch (e) { console.error('Failed to update ' + type + ':', e); }
            }
        });
        return handled;
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
        // Update every live card. With tracked instances (the dashboard) those are
        // the cards; the registered `modules` are never init()ed there, so updating
        // them would fetch into a null element. Pages without instances keep the
        // old behaviour.
        console.log('🔄 Updating all widgets...');
        const live = this.instanceMode ? Array.from(this.instances.values()) : Object.values(this.modules);
        live.forEach(module => {
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
        'Bucket List': 'BucketWidget',
        'Music Player': 'MusicWidget',
        'Music Player (New)': 'MusicPlayerWidget',
        'Music Library': 'MusicLibraryWidget',
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
            missingWidgets.push({ name: manifestName, className });
        }
    }
    
    if (missingWidgets.length > 0) {
        console.debug(`Widget scripts not loaded on this page: ${missingWidgets.map(w => w.name).join(', ')}`);
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
