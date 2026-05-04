/**
 * Zoe Dashboard - Clean Gridstack Implementation
 * Version: 1.0.0
 * 
 * Uses native Gridstack.js features with proper size constraints
 * No custom drag/drop hacks - industry standard solution
 */

const DASHBOARD_VERSION = '1.0.0';
console.log(`🎯 Zoe Dashboard v${DASHBOARD_VERSION} - Initializing...`);

// Touch dashboard-specific defaults so each widget opens at a practical size
// for kiosk interaction and placement.
const TOUCH_WIDGET_DEFAULTS = {
    time:        { defaultW: 4, defaultH: 3, minW: 3, minH: 2 },
    weather:     { defaultW: 4, defaultH: 3, minW: 3, minH: 2 },
    events:      { defaultW: 6, defaultH: 4, minW: 4, minH: 3 },
    tasks:       { defaultW: 6, defaultH: 4, minW: 4, minH: 3 },
    notes:       { defaultW: 6, defaultH: 4, minW: 4, minH: 3 },
    journal:     { defaultW: 6, defaultH: 4, minW: 4, minH: 3 },
    home:        { defaultW: 4, defaultH: 3, minW: 3, minH: 2 },
    system:      { defaultW: 4, defaultH: 3, minW: 3, minH: 2 },
    reminders:   { defaultW: 6, defaultH: 4, minW: 4, minH: 3 },
    shopping:    { defaultW: 6, defaultH: 4, minW: 4, minH: 3 },
    work:        { defaultW: 6, defaultH: 4, minW: 4, minH: 3 },
    personal:    { defaultW: 6, defaultH: 4, minW: 4, minH: 3 },
    bucket:      { defaultW: 6, defaultH: 4, minW: 4, minH: 3 },
    'dynamic-list': { defaultW: 6, defaultH: 4, minW: 4, minH: 3 },
    project:     { defaultW: 6, defaultH: 5, minW: 4, minH: 4 },
    'week-planner': { defaultW: 8, defaultH: 4, minW: 6, minH: 3 },
    music:       { defaultW: 6, defaultH: 4, minW: 4, minH: 3 },
    'music-player': { defaultW: 6, defaultH: 5, minW: 4, minH: 4 },
    'music-library': { defaultW: 8, defaultH: 5, minW: 6, minH: 4 },
    'music-search': { defaultW: 8, defaultH: 5, minW: 6, minH: 4 },
    'music-queue': { defaultW: 6, defaultH: 5, minW: 4, minH: 4 },
    'music-playlists': { defaultW: 6, defaultH: 5, minW: 4, minH: 4 },
    'music-suggestions': { defaultW: 8, defaultH: 5, minW: 6, minH: 4 }
};

/**
 * Get widget configuration from manifest
 * Falls back to defaults if manifest not loaded
 */
function getWidgetConfig(widgetId) {
    const manifestEntry = WidgetManager.getWidgetConfig(widgetId);
    if (manifestEntry) {
        // Backward compatibility: older manifests exposed a nested `config` object.
        if (manifestEntry.config) {
            const legacy = manifestEntry.config;
            return {
                minW: legacy.minW || 2,
                maxW: legacy.maxW || 12,
                minH: legacy.minH || 2,
                maxH: legacy.maxH || 12,
                defaultW: legacy.defaultW || 3,
                defaultH: legacy.defaultH || 3
            };
        }

        // Current manifest schema stores sizing fields at the top level.
        const defaultSize = manifestEntry.defaultSize || {};
        const minSize = manifestEntry.minSize || {};
        const maxSize = manifestEntry.maxSize || {};
        return {
            minW: minSize.w || 2,
            maxW: maxSize.w || 12,
            minH: minSize.h || 2,
            maxH: maxSize.h || 12,
            defaultW: defaultSize.w || 3,
            defaultH: defaultSize.h || 3
        };
    }
    
    // Fallback defaults - allow narrow widgets
    return {
        minW: 2, maxW: 12, minH: 2, maxH: 12,
        defaultW: 3, defaultH: 3
    };
}

function applyTouchWidgetDefaults(widgetId, config) {
    if (!document.body.classList.contains('touch-dashboard')) return config;
    const override = TOUCH_WIDGET_DEFAULTS[widgetId];
    if (!override) return config;
    return {
        ...config,
        defaultW: override.defaultW ?? config.defaultW,
        defaultH: override.defaultH ?? config.defaultH,
        minW: override.minW ?? config.minW,
        minH: override.minH ?? config.minH
    };
}

class Dashboard {
    constructor() {
        this.grid = null;
        this.isEditMode = false;
        this.storageKey = 'zoe_desktop_widgets';
    }
    
    init() {
        console.log('🏗️ Initializing Clean Gridstack Dashboard');
        
        const gridElement = document.getElementById('widgetGrid');
        if (!gridElement) {
            console.error('Widget grid not found');
            return;
        }
        
        const isTouchDashboard = document.body.classList.contains('touch-dashboard');
        // Detect if mobile for compact mode
        const isMobile = window.matchMedia('(max-width: 768px)').matches;
        const isTouchSurface = isTouchDashboard || isMobile;
        
        // Initialize Gridstack with NATIVE features - no custom hacks!
        this.grid = GridStack.init({
            column: 12,
            cellHeight: 70,
            margin: 16,
            float: isTouchDashboard ? true : !isMobile,  // Keep touch dashboard free-form
            animate: true,
            // Enable NATIVE resize with handles (like the official demo)
            // On mobile: only bottom resize (height), Desktop: all sides
            resizable: {
                handles: 'e, se, s, sw, w'
            },
            // Keep touch dashboard on a stable 12-col coordinate system.
            disableOneColumnMode: isTouchDashboard,
            columnOpts: isTouchDashboard ? undefined : {
                breakpoints: [
                    {w: 768, c: 6},   // Tablet: 6 columns
                    {w: 480, c: 4}    // Mobile: 4 columns
                ]
            },
            // Start in view mode (locked)
            staticGrid: true,
            // Enhanced drag settings for better mobile touch
            draggable: {
                scroll: isTouchDashboard ? false : true,
                appendTo: 'body',
                cancel: 'button, input, select, textarea, a',
                // Better touch handling
                handle: '.grid-stack-item-content',
                // Start drag sooner on touch surfaces to reduce "stuck" feel.
                distance: isTouchSurface ? 2 : 5,
                delay: isTouchSurface ? 100 : 300,
                // Smooth scrolling during drag
                scrollSensitivity: 20,
                scrollSpeed: 10
            },
            // Enable native remove button (X in corner)
            removable: '.trash-zone',  // Can drag to trash
            removeTimeout: 100
        }, gridElement);
        
        // Save layout on any change
        this.grid.on('change', () => {
            if (this.isEditMode) {
                this.saveLayout();
            }
        });
        
        // Resize event for responsive adjustments
        this.grid.on('resizestop', (event, el) => {
            const widget = el.querySelector('.widget');
            if (widget) {
                this.updateListColumns(widget);
            }
            if (this.isEditMode) this.saveLayout();
        });

        this.grid.on('dragstart', () => {
            document.body.classList.add('widget-drag-active');
        });

        this.grid.on('dragstop', () => {
            document.body.classList.remove('widget-drag-active');
            if (this.isEditMode) this.saveLayout();
        });
        
        // Expose globally for other functions
        window.grid = this.grid;
        window.touchDash = this;
        if (isTouchDashboard) {
            // Hard-force stable placement math on touch dashboard.
            this.grid.float(true);
            this.grid.column(12, 'none');
        }
        
        // Handle screen rotation / resize for mobile compact mode
        let resizeTimer;
        window.addEventListener('resize', () => {
            clearTimeout(resizeTimer);
            resizeTimer = setTimeout(() => {
                const nowMobile = window.matchMedia('(max-width: 768px)').matches;
                if (isTouchDashboard) {
                    this.grid.float(true);
                    console.log('📱 Screen resize: touch dashboard free-form mode');
                } else {
                    // Update float mode based on screen size
                    this.grid.float(!nowMobile);
                    console.log(`📱 Screen resize: ${nowMobile ? 'Mobile compact mode' : 'Desktop free-form mode'}`);
                }
            }, 250);
        });
        
        console.log(`✅ Gridstack initialized with native drag & resize (${isMobile ? 'Mobile compact' : 'Desktop free-form'})`);
        
        // Load saved layout
        this.loadLayout();
    }
    
    toggleEditMode() {
        this.isEditMode = !this.isEditMode;
        document.body.classList.toggle('edit-mode', this.isEditMode);
        window.dispatchEvent(new CustomEvent('dashboard-edit-mode-changed', {
            detail: { isEditMode: this.isEditMode }
        }));
        
        // Enable/disable Gridstack dragging & resizing
        this.grid.setStatic(!this.isEditMode);
        if (this.isEditMode && document.body.classList.contains('touch-dashboard')) {
            this.grid.float(true);
            this.grid.column(12, 'none');
        }
        
        // Show/hide widget library button
        const addBtn = document.querySelector('.fab-add-widget');
        if (addBtn) {
            addBtn.style.opacity = this.isEditMode ? '1' : '0';
            addBtn.style.pointerEvents = this.isEditMode ? 'auto' : 'none';
        }
        
        // Update UI button
        const fabBtn = document.getElementById('fabEditBtn');
        const fabIcon = document.getElementById('fabEditIcon');
        
        const navBtn = document.getElementById('navEditBtn');
        const navIcon = document.getElementById('navEditIcon');

        if (this.isEditMode) {
            fabBtn?.classList.add('active');
            navBtn?.classList.add('editing');
            if (fabIcon) fabIcon.textContent = '✓';
            if (navIcon) navIcon.textContent = '✓';
            if (fabBtn) fabBtn.title = 'Done Editing';
            if (navBtn) navBtn.title = 'Done Editing';
            console.log('✏️ Edit mode: drag, resize & remove enabled');
        } else {
            fabBtn?.classList.remove('active');
            navBtn?.classList.remove('editing');
            if (fabIcon) fabIcon.textContent = '✏️';
            if (navIcon) navIcon.textContent = '✏️';
            if (fabBtn) fabBtn.title = 'Edit Dashboard';
            if (navBtn) navBtn.title = 'Edit Dashboard';
            this.saveLayout();
            console.log('💾 View mode: layout saved');
        }
    }
    
    addWidget(type, options = {}) {
        console.log('🎯 Dashboard.addWidget called with type:', type, 'options:', options);
        
        if (!this.grid) {
            console.error('❌ Grid not initialized');
            return null;
        }
        
        console.log('✅ Grid exists');
        
        if (!WidgetManager) {
            console.error('❌ WidgetManager not found');
            return null;
        }
        
        console.log('✅ WidgetManager exists, modules:', Object.keys(WidgetManager.modules));
        
        if (!WidgetManager.modules[type]) {
            console.error('❌ Widget type not found:', type);
            console.log('Available types:', Object.keys(WidgetManager.modules));
            return null;
        }
        
        console.log('✅ Widget type found:', type);
        
        const config = applyTouchWidgetDefaults(type, getWidgetConfig(type));
        
        const module = WidgetManager.modules[type];
        
        // Determine size class based on grid width
        let sizeClass = 'size-medium'; // Default
        if (config.defaultW <= 3) {
            sizeClass = 'size-small';
        } else if (config.defaultW >= 7) {
            sizeClass = 'size-large';
        }
        
        const widgetHTML = `
            <div class="widget ${sizeClass}" data-widget-type="${type}">
                ${module.getTemplate()}
            </div>
        `;
        
        // Add widget with size constraints (Gridstack native feature!)
        const widgetOptions = {
            w: config.defaultW,
            h: config.defaultH,
            minW: config.minW,
            maxW: config.maxW,
            minH: config.minH,
            maxH: config.maxH,
            content: widgetHTML,
            autoPosition: true
        };
        
        const gridItem = this.grid.addWidget(widgetOptions);
        
        // Add remove button to the grid item
        this.addRemoveButton(gridItem);
        
        // Initialize widget module with options
        const widget = gridItem.querySelector('.widget');
        if (widget) {
            module.init(widget, options);
            this.updateListColumns(widget);
        }
        
        console.log(`✅ Added ${type}: ${config.defaultW}×${config.defaultH} (constraints: ${config.minW}-${config.maxW} × ${config.minH}-${config.maxH})`);
        
        this.saveLayout();
        return gridItem;
    }
    
    addRemoveButton(gridItem) {
        // Don't add if already exists
        if (gridItem.querySelector('.widget-remove-btn')) return;
        
        const removeBtn = document.createElement('button');
        removeBtn.className = 'widget-remove-btn';
        removeBtn.innerHTML = '×';
        removeBtn.title = 'Remove Widget';
        removeBtn.onclick = (e) => {
            e.stopPropagation();
            if (confirm('Remove this widget?')) {
                this.grid.removeWidget(gridItem);
                this.saveLayout();
            }
        };
        
        gridItem.appendChild(removeBtn);

        this.addGearButton(gridItem);
    }

    addGearButton(gridItem) {
        if (gridItem.querySelector('.widget-gear-btn')) return;
        const gearBtn = document.createElement('button');
        gearBtn.className = 'widget-gear-btn';
        gearBtn.innerHTML = '⚙';
        gearBtn.title = 'Widget Settings';
        gearBtn.onclick = (e) => {
            e.stopPropagation();
            e.preventDefault();
            const widget = gridItem.querySelector('.widget');
            const type = widget && widget.dataset.widgetType;
            if (!type) return;
            if (window.WidgetSettingsSheet) {
                window.WidgetSettingsSheet.open(type, widget, gridItem, this);
            }
        };
        gridItem.appendChild(gearBtn);
    }
    
    removeWidget(widget) {
        const gridItem = widget.closest('.grid-stack-item');
        if (gridItem && this.grid) {
            this.grid.removeWidget(gridItem);
            this.saveLayout();
            console.log('🗑️ Widget removed');
        }
    }
    
    // Update list widget columns based on width
    updateListColumns(widget) {
        const widgetType = widget.getAttribute('data-widget-type');
        if (!widgetType || (!widgetType.includes('list') && !widgetType.includes('tasks') && !widgetType.includes('shopping'))) {
            return;
        }
        
        const gridItem = widget.closest('.grid-stack-item');
        const width = gridItem ? parseInt(gridItem.getAttribute('gs-w')) || 3 : 3;
        
        // Calculate columns based on width
        let columns = 1;
        if (width >= 9) columns = 3;
        else if (width >= 6) columns = 2;
        else columns = 1;
        
        const content = widget.querySelector('.widget-content');
        if (content) {
            content.style.gridTemplateColumns = `repeat(${columns}, 1fr)`;
            console.log(`📊 List widget ${widgetType}: ${columns} columns (width: ${width})`);
        }
    }
    
    saveLayout() {
        if (!this.grid) return;
        
        const layout = [];
        this.grid.engine.nodes.forEach((node, index) => {
            const widget = node.el.querySelector('.widget');
            if (widget) {
                layout.push({
                    type: widget.getAttribute('data-widget-type'),
                    x: node.x,
                    y: node.y,
                    w: node.w,
                    h: node.h,
                    order: index
                });
            }
        });
        
        localStorage.setItem(this.storageKey, JSON.stringify(layout));
        console.log('💾 Layout saved:', layout.length, 'widgets');
        
        // TODO: Save to backend API
    }
    
    loadLayout() {
        const saved = localStorage.getItem(this.storageKey);
        
        if (saved) {
            try {
                const layout = JSON.parse(saved);
                if (layout && layout.length > 0) {
                    this.loadFromData(layout);
                } else {
                    this.createDefaultLayout();
                }
            } catch (e) {
                console.error('Failed to load layout:', e);
                this.createDefaultLayout();
            }
        } else {
            this.createDefaultLayout();
        }
    }
    
    loadFromData(layout) {
        console.log('📋 Loading layout:', layout.length, 'widgets');
        
        this.grid.removeAll();
        
        layout.forEach(item => {
            const config = applyTouchWidgetDefaults(item.type, getWidgetConfig(item.type));
            const module = WidgetManager.modules[item.type];
            
            if (!module) {
                console.warn('Widget type not found:', item.type);
                return;
            }
            
            // Determine size class based on widget width
            let sizeClass = 'size-medium'; // Default
            if (item.w <= 3) {
                sizeClass = 'size-small';
            } else if (item.w >= 7) {
                sizeClass = 'size-large';
            }
            
            const widgetHTML = `
                <div class="widget ${sizeClass}" data-widget-type="${item.type}">
                    ${module.getTemplate()}
                </div>
            `;
            
            const gridItem = this.grid.addWidget({
                x: item.x,
                y: item.y,
                w: item.w,
                h: item.h,
                minW: config.minW,
                maxW: config.maxW,
                minH: config.minH,
                maxH: config.maxH,
                content: widgetHTML
            });
            
            // Add remove button
            this.addRemoveButton(gridItem);
            
            const widget = gridItem.querySelector('.widget');
            if (widget) {
                module.init(widget);
                this.updateListColumns(widget);
            }
        });
        
        console.log('✅ Layout loaded');
    }
    
    createDefaultLayout() {
        console.log('📐 Creating default layout');

        // Touch kiosk: compact 4-widget layout that fits a 600px viewport
        // (home + reminders removed — they push the grid too tall for kiosk).
        const touchDefaults = [
            { type: 'time',    x: 0, y: 0, w: 4, h: 3 },
            { type: 'weather', x: 4, y: 0, w: 4, h: 3 },
            { type: 'events',  x: 0, y: 3, w: 4, h: 4 },
            { type: 'notes',   x: 4, y: 3, w: 4, h: 4 },
        ];

        // Desktop: richer 6-widget default. Plenty of room for Home and Tasks.
        const desktopDefaults = [
            { type: 'time',    x: 0, y: 0, w: 4, h: 3 },
            { type: 'weather', x: 4, y: 0, w: 4, h: 3 },
            { type: 'home',    x: 8, y: 0, w: 4, h: 3 },
            { type: 'events',  x: 0, y: 3, w: 4, h: 4 },
            { type: 'tasks',   x: 4, y: 3, w: 4, h: 4 },
            { type: 'notes',   x: 8, y: 3, w: 4, h: 4 },
        ];

        const isTouch = document.body && document.body.classList.contains('touch-dashboard');
        const defaults = isTouch ? touchDefaults : desktopDefaults;

        this.loadFromData(defaults);
        this.saveLayout();
    }
}

// Initialize dashboard when DOM is ready AND widgets are registered
let dashboard = null;
let initializationAttempted = false;

function initializeDashboard() {
    if (initializationAttempted) {
        console.log('⚠️ Initialization already attempted, skipping');
        return;
    }
    
    initializationAttempted = true;
    
    if (typeof WidgetManager !== 'undefined' && window.widgetsRegistered) {
        dashboard = new Dashboard();
        dashboard.init();
        window.dashboard = dashboard;
        console.log('🎯 Dashboard ready with native Gridstack features!');
        
        // Initialize WebSocket for real-time updates
        const session = window.zoeAuth?.getCurrentSession();
        const userId = session?.user_info?.user_id || session?.user_id;
        if (typeof ZoeWebSockets !== 'undefined') {
            ZoeWebSockets.init(userId);
            console.log('🔌 WebSocket sync initialized for user:', userId);
        }
    } else {
        console.error('❌ Cannot initialize dashboard: WidgetManager not loaded or widgets not registered');
        console.error('  WidgetManager exists:', typeof WidgetManager !== 'undefined');
        console.error('  widgetsRegistered:', window.widgetsRegistered);
        initializationAttempted = false; // Allow retry
    }
}

// Set up event listener IMMEDIATELY when this script loads (before anything else)
console.log('📝 Dashboard.js loading, setting up widget registration listener...');
window.addEventListener('widgets-registered', () => {
    console.log('🎯 Received widgets-registered event');
    if (!initializationAttempted) {
        setTimeout(() => initializeDashboard(), 50);
    }
}, { once: true });

// Reinitialize on BFCache restore or soft navigation where the page is resumed
window.addEventListener('pageshow', (event) => {
    // If coming from bfcache or if dashboard not ready, ensure init runs
    if (event.persisted || !window.dashboard) {
        console.log('🔄 pageshow detected (BFCache or resume) - ensuring dashboard initialization');
        if (typeof WidgetManager !== 'undefined' && (window.widgetsRegistered || WidgetManager.manifestLoaded)) {
            initializationAttempted = false;
            setTimeout(() => initializeDashboard(), 50);
        }
    }
});

function setupDashboardInitialization() {
    console.log('📄 DOMContentLoaded - checking widget registration state');
    console.log('  widgetsRegistered:', window.widgetsRegistered);
    console.log('  WidgetManager exists:', typeof WidgetManager !== 'undefined');
    
    // Check current state
    if (window.widgetsRegistered && typeof WidgetManager !== 'undefined') {
        console.log('✅ Widgets already registered, initializing immediately');
        setTimeout(() => initializeDashboard(), 50);
    } else {
        console.log('⏳ Waiting for widgets-registered event or timeout...');
        
        // Fallback timeout - initialize dashboard after 2 seconds
        setTimeout(() => {
            if (!dashboard) {
                console.warn('⚠️ Widget registration timeout - initializing dashboard anyway');
                window.widgetsRegistered = true;
                initializeDashboard();
            }
        }, 2000);
    }
}

// Set up DOMContentLoaded handler
if (document.readyState === 'loading') {
    window.addEventListener('DOMContentLoaded', setupDashboardInitialization);
} else {
    // DOM already loaded, run immediately
    console.log('⚠️ DOM already loaded when dashboard.js ran');
    setupDashboardInitialization();
}

// Expose functions for HTML onclick handlers
window.toggleEditMode = () => dashboard?.toggleEditMode();
window.addWidget = (type, options) => dashboard?.addWidget(type, options);
window.removeWidget = (widget) => dashboard?.removeWidget(widget);

// Pull-to-refresh cache clearing
function forceRefreshCache() {
    if ('serviceWorker' in navigator && navigator.serviceWorker.controller) {
        navigator.serviceWorker.controller.postMessage({
            type: 'CLEAR_CACHE'
        });
        
        setTimeout(() => {
            window.location.reload(true);
        }, 500);
    } else {
        window.location.reload(true);
    }
}

// Pull-to-refresh functionality
function initPullToRefresh() {
    let startY = 0;
    let currentY = 0;
    let isPulling = false;
    const threshold = 80;
    const indicator = document.getElementById('pullToRefreshIndicator');
    
    if (!indicator) {
        console.warn('⚠️ Pull-to-refresh indicator not found');
        return;
    }
    
    console.log('📱 Pull-to-refresh initialized');
    
    // Use the main container for better Android compatibility
    const container = document.querySelector('.main-container') || document.body;
    
    container.addEventListener('touchstart', (e) => {
        // Check if at top of page
        const scrollTop = window.pageYOffset || document.documentElement.scrollTop;
        if (scrollTop === 0) {
            startY = e.touches[0].clientY;
            isPulling = true;
        }
    }, { passive: true });
    
    container.addEventListener('touchmove', (e) => {
        if (!isPulling) return;
        
        const scrollTop = window.pageYOffset || document.documentElement.scrollTop;
        if (scrollTop > 0) {
            isPulling = false;
            return;
        }
        
        currentY = e.touches[0].clientY;
        const pullDistance = currentY - startY;
        
        if (pullDistance > 0) {
            // Prevent default scrolling when pulling down
            if (pullDistance > 10) {
                e.preventDefault();
            }
            
            const displayDistance = Math.min(pullDistance * 0.5, threshold);
            const rotation = Math.min(pullDistance * 3, 360);
            const opacity = Math.min(pullDistance / threshold, 1);
            
            indicator.style.transform = `translateX(-50%) translateY(${displayDistance}px) rotate(${rotation}deg)`;
            indicator.style.opacity = opacity;
            
            // Change indicator appearance when threshold reached
            if (pullDistance > threshold) {
                indicator.textContent = '🔄';
                indicator.style.background = 'rgba(90, 224, 224, 0.9)';
            } else {
                indicator.textContent = '⬇️';
                indicator.style.background = 'rgba(123, 97, 255, 0.9)';
            }
        }
    }, { passive: false });
    
    container.addEventListener('touchend', () => {
        if (!isPulling) return;
        
        const pullDistance = currentY - startY;
        
        if (pullDistance > threshold) {
            // Trigger refresh
            indicator.textContent = '✓';
            indicator.style.background = 'rgba(76, 175, 80, 0.9)';
            
            setTimeout(() => {
                indicator.style.transform = 'translateX(-50%) translateY(-100px)';
                indicator.style.opacity = '0';
                forceRefreshCache();
            }, 500);
        } else {
            // Reset without refresh
            indicator.style.transform = 'translateX(-50%) translateY(-100px)';
            indicator.style.opacity = '0';
        }
        
        isPulling = false;
        startY = 0;
        currentY = 0;
    }, { passive: true });
}

// Initialize pull-to-refresh on mobile, but disable in kiosk mode to avoid
// accidental full-page reloads from touch drags near the top edge.
const _isKioskMode = (() => {
    try {
        return new URLSearchParams(window.location.search).get('kiosk') === '1';
    } catch (_) {
        return false;
    }
})();
if (window.matchMedia('(max-width: 768px)').matches && !_isKioskMode) {
    initPullToRefresh();
}

window.forceRefreshCache = forceRefreshCache;

