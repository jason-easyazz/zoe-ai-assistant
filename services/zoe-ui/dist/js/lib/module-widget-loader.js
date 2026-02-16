/**
 * Module Widget Loader
 * ====================
 * 
 * Dynamically discovers and loads widgets from enabled modules.
 * Part of Zoe's self-contained module architecture.
 * 
 * Features:
 * - Automatic widget discovery from modules
 * - Dynamic script/CSS loading
 * - Widget registration
 * - Dependency management
 * 
 * Usage:
 *   const loader = new ModuleWidgetLoader();
 *   await loader.init();
 *   const widgets = loader.getAvailableWidgets();
 * 
 * Version: 1.0.0
 */

class ModuleWidgetLoader {
    constructor(options = {}) {
        this.mcp = null;
        this.widgets = new Map();
        this.modules = new Map();
        this.loadedScripts = new Set();
        this.loadedStyles = new Set();
        this.baseUrl = options.baseUrl || '';
        this.initialized = false;
    }

    /**
     * Initialize the loader
     */
    async init() {
        console.log('🔧 ModuleWidgetLoader: Initializing...');
        
        try {
            // Initialize MCP client (use relative URL for proper routing)
            this.mcp = new MCPClient({
                mcpServerUrl: '/api/mcp'
            });
            
            await this.mcp.init();
            
            // Discover widgets from enabled modules
            await this.discoverWidgets();
            
            this.initialized = true;
            console.log(`✅ ModuleWidgetLoader: Discovered ${this.widgets.size} widgets from ${this.modules.size} modules`);
            
            return true;
        } catch (error) {
            console.error('❌ ModuleWidgetLoader: Initialization failed:', error);
            throw error;
        }
    }

    /**
     * Discover widgets from all enabled modules
     */
    async discoverWidgets() {
        // Phase 1b: Dynamic module discovery via API
        let enabledModules = [];
        try {
            const modulesResp = await fetch('/api/modules/enabled', {
                headers: { 'X-Session-ID': window.sessionId || 'dev-localhost' }
            });
            if (modulesResp.ok) {
                const data = await modulesResp.json();
                enabledModules = (data.modules || []).filter(m => m.has_widgets);
                console.log(`🔍 Dynamic discovery: ${enabledModules.length} modules with widgets`);
            } else {
                console.warn('⚠️ Module discovery API unavailable, using fallback');
            }
        } catch (e) {
            console.warn('⚠️ Module discovery failed:', e);
        }
        
        // Fallback: no hardcoded modules needed
        console.log('🔍 Discovering widgets from modules:', enabledModules.map(m => m.name));
        
        for (const mod of enabledModules) {
            const moduleName = mod.name;
            const config = { port: mod.port, name: moduleName };
            
            try {
                // Fetch manifest through nginx proxy (not direct localhost)
                const manifestUrl = `/modules/${moduleName}/widget/manifest`;
                const response = await fetch(manifestUrl);
                
                if (!response.ok) {
                    console.warn(`⚠️  Module ${moduleName}: No widget manifest available`);
                    continue;
                }
                
                const manifest = await response.json();
                this.modules.set(moduleName, manifest);
                
                console.log(`📦 Module ${moduleName}: Found ${manifest.widgets?.length || 0} widgets`);
                
                // Load dependencies first
                if (manifest.dependencies) {
                    for (const dep of manifest.dependencies) {
                        await this.loadDependency(moduleName, dep);
                    }
                }
                
                // Register widgets
                if (manifest.widgets) {
                    for (const widget of manifest.widgets) {
                        widget.module = moduleName;
                        widget.moduleName = manifest.name || config.name;
                        this.widgets.set(widget.id, widget);
                    }
                }
                
            } catch (error) {
                console.error(`❌ Failed to load widgets from ${moduleName}:`, error);
            }
        }
    }

    /**
     * Load a dependency (shared script)
     */
    async loadDependency(moduleName, dependency) {
        const scriptUrl = `/modules/${moduleName}${dependency.script}`;
        
        if (this.loadedScripts.has(scriptUrl)) {
            return; // Already loaded
        }
        
        console.log(`📥 Loading dependency: ${dependency.name}`);
        // Add cache-busting parameter to force fresh load
        const cacheBustedUrl = `${scriptUrl}?v=${Date.now()}`;
        await this.loadScript(cacheBustedUrl);
        this.loadedScripts.add(scriptUrl);
    }

    /**
     * Load a widget (script and CSS)
     */
    async loadWidget(widgetId) {
        const widget = this.widgets.get(widgetId);
        if (!widget) {
            throw new Error(`Widget ${widgetId} not found`);
        }
        
        if (widget.loaded) {
            return widget; // Already loaded
        }
        
        console.log(`📥 Loading widget: ${widget.name} (${widgetId})`);
        
        try {
            // Load script through nginx proxy
            const scriptUrl = `/modules/${widget.module}${widget.script}`;
            if (!this.loadedScripts.has(scriptUrl)) {
                // Add cache-busting parameter to force fresh load
                const cacheBustedUrl = `${scriptUrl}?v=${Date.now()}`;
                await this.loadScript(cacheBustedUrl);
                this.loadedScripts.add(scriptUrl);
            }
            
            // Load CSS if specified
            if (widget.styles) {
                const cssUrl = `/modules/${widget.module}${widget.styles}`;
                if (!this.loadedStyles.has(cssUrl)) {
                    await this.loadStyle(cssUrl);
                    this.loadedStyles.add(cssUrl);
                }
            }
            
            widget.loaded = true;
            console.log(`✅ Widget loaded: ${widget.name}`);
            
            return widget;
            
        } catch (error) {
            console.error(`❌ Failed to load widget ${widgetId}:`, error);
            throw error;
        }
    }

    /**
     * Load all available widgets
     */
    async loadAllWidgets() {
        const widgets = Array.from(this.widgets.keys());
        const results = await Promise.allSettled(
            widgets.map(id => this.loadWidget(id))
        );
        
        const loaded = results.filter(r => r.status === 'fulfilled').length;
        const failed = results.filter(r => r.status === 'rejected').length;
        
        console.log(`📊 Widget loading complete: ${loaded} loaded, ${failed} failed`);
        
        return { loaded, failed };
    }

    /**
     * Dynamically load a script
     */
    loadScript(url) {
        return new Promise((resolve, reject) => {
            const script = document.createElement('script');
            script.src = url;
            script.async = true;
            
            script.onload = () => {
                console.log(`✅ Script loaded: ${url}`);
                resolve();
            };
            
            script.onerror = () => {
                console.error(`❌ Script failed to load: ${url}`);
                reject(new Error(`Failed to load script: ${url}`));
            };
            
            document.head.appendChild(script);
        });
    }

    /**
     * Dynamically load a stylesheet
     */
    loadStyle(url) {
        return new Promise((resolve, reject) => {
            const link = document.createElement('link');
            link.rel = 'stylesheet';
            link.href = url;
            
            link.onload = () => {
                console.log(`✅ Stylesheet loaded: ${url}`);
                resolve();
            };
            
            link.onerror = () => {
                console.error(`❌ Stylesheet failed to load: ${url}`);
                reject(new Error(`Failed to load stylesheet: ${url}`));
            };
            
            document.head.appendChild(link);
        });
    }

    /**
     * Get all available widgets
     */
    getAvailableWidgets() {
        return Array.from(this.widgets.values());
    }

    /**
     * Get widgets by category
     */
    getWidgetsByCategory(category) {
        return this.getAvailableWidgets().filter(w => w.category === category);
    }

    /**
     * Get widget by ID
     */
    getWidget(widgetId) {
        return this.widgets.get(widgetId);
    }

    /**
     * Check if widget is available
     */
    hasWidget(widgetId) {
        return this.widgets.has(widgetId);
    }

    /**
     * Get all module manifests
     */
    getModules() {
        return Array.from(this.modules.values());
    }

    /**
     * Get enabled module names
     */
    getEnabledModules() {
        return this.mcp ? this.mcp.getEnabledModules() : [];
    }

    /**
     * Check if loader is initialized
     */
    isInitialized() {
        return this.initialized;
    }
}

// Export for use in other scripts
if (typeof window !== 'undefined') {
    window.ModuleWidgetLoader = ModuleWidgetLoader;
}

// Export for module systems
if (typeof module !== 'undefined' && module.exports) {
    module.exports = ModuleWidgetLoader;
}
