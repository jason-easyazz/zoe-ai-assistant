/**
 * Zoe Dashboard - Clean Gridstack Implementation
 * Version: 1.0.0
 * 
 * Uses native Gridstack.js features with proper size constraints
 * No custom drag/drop hacks - industry standard solution
 */

const DASHBOARD_VERSION = '1.0.0';
console.log(`🎯 Zoe Dashboard v${DASHBOARD_VERSION} - Initializing...`);

/**
 * List of valid list widget types for the lists page
 * Only these widgets should appear on the lists page
 */
const LIST_WIDGET_TYPES = [
    'shopping',
    'work',
    'personal',
    'bucket',
    'project',
    'reminders',
    'dynamic-list'
];

/**
 * Check if a widget type is a valid list widget
 */
function isListWidget(widgetType) {
    return LIST_WIDGET_TYPES.includes(widgetType);
}

/**
 * Get widget configuration from manifest
 * Falls back to defaults if manifest not loaded
 * For list widgets, allows unlimited height
 */
function getWidgetConfig(widgetId) {
    const config = WidgetManager.getWidgetConfig(widgetId);
    
    // Check if this is a list widget
    const isList = isListWidget(widgetId);
    
    // Convert manifest format to expected format
    if (config) {
        // Manifest has defaultSize: {w, h} and minSize: {w, h}
        const defaultSize = config.defaultSize || { w: 3, h: 3 };
        const minSize = config.minSize || { w: 2, h: 2 };
        
        const widgetConfig = {
            defaultW: defaultSize.w || 3,
            defaultH: defaultSize.h || 3,
            minW: minSize.w || 2,
            minH: minSize.h || 2,
            maxW: 12, // Full width allowed
            maxH: isList ? 999 : 8 // Unlimited height for lists (999 rows = ~70,000px), 8 for others
        };
        
        return widgetConfig;
    }
    
    // Fallback defaults - unlimited height for list widgets
    return {
        minW: 2, 
        maxW: 12, 
        minH: 2, 
        maxH: isList ? 999 : 8, // Unlimited height for lists, 8 for others
        defaultW: 3, 
        defaultH: 3
    };
}

class Dashboard {
    constructor() {
        this.grid = null;
        this.isEditMode = false;
        // Widgets that were in the saved layout but whose module wasn't registered
        // yet at load time. They'll be placed when `widgets-registered` fires.
        this._pendingLayout = [];
        this._deferredListenerAttached = false;
        this.storageKey = this._computeStorageKey();
        this._migrateLegacyKey();
    }

    /**
     * User-scoped storage key so multiple users on the same kiosk don't clobber
     * each other. Falls back to the legacy global key when no session is known.
     */
    _computeStorageKey() {
        try {
            // Prefer localStorage session first (available synchronously at boot),
            // then fall back to zoeAuth. This avoids key drift when auth hydration
            // races dashboard init on touch pages.
            let session = null;
            try {
                const raw = localStorage.getItem('zoe_session');
                if (raw) session = JSON.parse(raw);
            } catch (_) { /* ignore malformed cache */ }
            if (!session && window.zoeAuth && typeof window.zoeAuth.getCurrentSession === 'function') {
                session = window.zoeAuth.getCurrentSession();
            }
            const userId = session && (session.user_info?.user_id || session.user_id);
            if (userId) return `zoe_lists_layout:${userId}`;
        } catch (_) { /* fall through */ }
        return 'zoe_lists_layout';
    }

    /**
     * One-shot migration from the legacy global key to the user-scoped key.
     * Preserves the legacy copy so we can recover if anything goes wrong.
     */
    _migrateLegacyKey() {
        try {
            if (this.storageKey === 'zoe_lists_layout') return;
            const legacyRaw = localStorage.getItem('zoe_lists_layout');
            const newRaw = localStorage.getItem(this.storageKey);
            if (legacyRaw && !newRaw) {
                console.log('🔄 Migrating legacy lists layout → user-scoped key:', this.storageKey);
                localStorage.setItem(this.storageKey, legacyRaw);
            }
        } catch (_) { /* storage errors: ignore */ }
    }
    
    init() {
        console.log('🏗️ Initializing Clean Gridstack Dashboard');
        
        const gridElement = document.getElementById('listsGrid');
        if (!gridElement) {
            console.error('Widget grid not found');
            return;
        }
        
        // Detect if mobile for compact mode
        const isMobile = window.matchMedia('(max-width: 768px)').matches;
        
        // Initialize Gridstack with NATIVE features - no custom hacks!
        this.grid = GridStack.init({
            column: 12,
            cellHeight: 70,
            margin: 16,
            // Lists must preserve exact manual placement across reloads on touch panels.
            // Compact mode (float=false) repacks items and fights explicit positioning.
            float: true,
            animate: true,
            // Enable NATIVE resize with handles (like the official demo)
            // On mobile: only bottom resize (height), Desktop: all sides
            resizable: {
                handles: isMobile ? 's' : 'e, se, s, sw, w'
            },
            // Mobile-friendly responsive columns
            // Keep a stable 12-col coordinate space on touch panels so saved
            // x/y positions are not remapped by responsive column compaction.
            disableOneColumnMode: true,
            // Start in view mode (locked)
            staticGrid: true,
            // Enhanced drag settings for better mobile touch
            draggable: {
                scroll: true,
                appendTo: 'body',
                cancel: 'button, input, select, textarea, a',
                // Better touch handling
                handle: '.grid-stack-item-content',
                // Increase touch tolerance for easier dragging
                distance: 2,  // Start drag sooner to reduce "sticky" feel
                delay: 100,   // Short hold: smoother touch drag without long press lag
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

        // Persist final coordinates at end of drag. `change` can fire multiple
        // intermediate states during touch movement; this captures the settled one.
        this.grid.on('dragstart', () => {
            document.body.classList.add('widget-drag-active');
        });

        this.grid.on('dragstop', () => {
            document.body.classList.remove('widget-drag-active');
            if (this.isEditMode) this.saveLayout();
        });
        
        // Expose globally for other functions
        window.grid = this.grid;
        
        // Handle screen rotation / resize for mobile compact mode
        let resizeTimer;
        window.addEventListener('resize', () => {
            clearTimeout(resizeTimer);
            resizeTimer = setTimeout(() => {
                const nowMobile = window.matchMedia('(max-width: 768px)').matches;
                // Keep free-form layout on all breakpoints for predictable persistence.
                this.grid.float(true);
                console.log(`📱 Screen resize: ${nowMobile ? 'Mobile free-form mode' : 'Desktop free-form mode'}`);
            }, 250);
        });
        
        console.log(`✅ Gridstack initialized with native drag & resize (${isMobile ? 'Mobile compact' : 'Desktop free-form'})`);
        
        // Clean up any non-list widgets that might already be displayed
        this.cleanupNonListWidgets();
        
        // Load saved layout
        this.loadLayout();
        
        // Update maxH for all list widgets to allow unlimited height
        this.updateListWidgetHeights();
    }
    
    /**
     * Remove any non-list widgets that might already be in the grid
     * This ensures the lists page only shows list widgets
     */
    cleanupNonListWidgets() {
        if (!this.grid) return;
        
        const nodesToRemove = [];
        this.grid.engine.nodes.forEach((node) => {
            const widget = node.el.querySelector('.widget');
            if (widget) {
                const widgetType = widget.getAttribute('data-widget-type');
                if (widgetType && !isListWidget(widgetType)) {
                    console.warn(`🗑️ Removing non-list widget "${widgetType}" from lists page`);
                    nodesToRemove.push(node.el);
                }
            }
        });
        
        // Remove non-list widgets
        nodesToRemove.forEach(el => {
            this.grid.removeWidget(el, false); // false = don't trigger change event
        });
        
        if (nodesToRemove.length > 0) {
            console.log(`✅ Cleaned up ${nodesToRemove.length} non-list widget(s)`);
            // Save the cleaned layout
            this.saveLayout();
        }
    }
    
    /**
     * Update maxH constraint for all list widgets to allow unlimited height
     * This ensures existing widgets can be resized as tall as needed
     */
    updateListWidgetHeights() {
        if (!this.grid) return;
        
        this.grid.engine.nodes.forEach((node) => {
            const widget = node.el.querySelector('.widget');
            if (widget) {
                const widgetType = widget.getAttribute('data-widget-type');
                if (widgetType && isListWidget(widgetType)) {
                    // Update maxH to 999 (effectively unlimited)
                    node.maxH = 999;
                    console.log(`📏 Updated maxH for list widget "${widgetType}" to unlimited (999 rows)`);
                }
            }
        });
    }
    
    toggleEditMode() {
        this.isEditMode = !this.isEditMode;
        document.body.classList.toggle('edit-mode', this.isEditMode);
        
        // Enable/disable Gridstack dragging & resizing
        this.grid.setStatic(!this.isEditMode);
        
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
            if (fabBtn) fabBtn.title = 'Edit Lists';
            if (navBtn) navBtn.title = 'Edit Lists';
            this.saveLayout();
            console.log('💾 View mode: layout saved');
        }
    }
    
    addWidget(type) {
        console.log('🎯 Dashboard.addWidget called with type:', type);
        
        // FILTER: Only allow list widgets on the lists page
        if (!isListWidget(type)) {
            console.warn(`⚠️ Widget type "${type}" is not a list widget. Only list widgets are allowed on the lists page.`);
            console.log('Valid list widgets:', LIST_WIDGET_TYPES);
            return null;
        }
        
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
        
        const config = getWidgetConfig(type);
        
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
        
        // Initialize widget module
        const widget = gridItem.querySelector('.widget');
        if (widget) {
            module.init(widget);
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
            if (!widget) return;

            const widgetType = widget.getAttribute('data-widget-type');
            if (!widgetType || widgetType === 'undefined' || widgetType === 'null') {
                console.warn('⚠️ Skipping widget with invalid type:', widgetType);
                return;
            }
            if (!isListWidget(widgetType)) {
                console.warn(`⚠️ Skipping non-list widget "${widgetType}" - only list widgets are saved on lists page`);
                return;
            }

            layout.push({
                type: widgetType,
                x: node.x,
                y: node.y,
                w: node.w,
                h: node.h,
                order: index
            });
        });

        // CRITICAL: merge in widgets that were loaded from storage but whose module
        // wasn't registered yet. Without this, the first saveLayout() after a racey
        // load would permanently drop those widgets from localStorage.
        if (Array.isArray(this._pendingLayout) && this._pendingLayout.length > 0) {
            this._pendingLayout.forEach(item => {
                if (!item || !item.type) return;
                if (!isListWidget(item.type)) return;
                // Don't double-add if somehow the deferred widget also ended up placed.
                const already = layout.some(l => l.type === item.type && l.x === item.x && l.y === item.y);
                if (already) return;
                layout.push({
                    type: item.type,
                    x: item.x, y: item.y, w: item.w, h: item.h,
                    order: (typeof item.order === 'number') ? item.order : layout.length
                });
            });
        }

        if (layout.length === 0) {
            console.error('❌ No valid widgets to save - layout corrupt, not saving');
            return;
        }

        if (window.LayoutProtection) {
            LayoutProtection.saveLayout(this.storageKey, layout);
        } else {
            localStorage.setItem(this.storageKey, JSON.stringify(layout));
            console.log('💾 Layout saved:', layout.length, 'list widgets');
        }

        // Compatibility write: keep legacy key in sync so a transient auth/state
        // mismatch cannot make the next reload appear to "lose" placement.
        try {
            if (this.storageKey !== 'zoe_lists_layout') {
                const current = localStorage.getItem(this.storageKey);
                if (current) localStorage.setItem('zoe_lists_layout', current);
            }
        } catch (_) { /* best effort */ }
    }
    
    loadLayout() {
        let layout = null;
        const rawSaved = localStorage.getItem(this.storageKey);

        if (window.LayoutProtection) {
            layout = LayoutProtection.loadLayout(this.storageKey);

            if (layout === null && rawSaved) {
                // Validation failed BUT there was stored data. Don't nuke it — back it up so
                // the user (or we) can recover. This path used to permanently overwrite the
                // real layout with the default set on any transient parse/validation blip.
                console.warn('⚠️ Saved layout failed validation; backing up to .bak and continuing with defaults');
                try {
                    localStorage.setItem(this.storageKey + '.bak', rawSaved);
                    localStorage.setItem(this.storageKey + '.bak.ts', new Date().toISOString());
                } catch (_) { /* storage full / quota: ignore */ }
                // We still have to clear the bad entry so the default path can run without
                // looping, but the backup above gives us a recovery route.
                try { localStorage.removeItem(this.storageKey); } catch (_) {}
            }
        } else {
            if (rawSaved) {
                try {
                    layout = JSON.parse(rawSaved);
                } catch (e) {
                    console.error('Failed to parse layout:', e);
                    try { localStorage.setItem(this.storageKey + '.bak', rawSaved); } catch (_) {}
                }
            }
        }

        if (layout && Array.isArray(layout) && layout.length > 0) {
            this.loadFromData(layout);
        } else if (rawSaved) {
            // Had data but it didn't validate; don't create defaults (which would overwrite the
            // backup we just made once the user interacts). Start empty and let the user add
            // widgets back — their .bak is recoverable if they need it.
            console.warn('📐 Saved layout present but unusable; starting empty (backup retained at ' + this.storageKey + '.bak)');
        } else {
            console.log('📐 No saved layout found - creating default');
            this.createDefaultLayout();
        }
    }
    
    loadFromData(layout) {
        console.log('📋 Loading layout:', layout.length, 'widgets');

        this.grid.removeAll();
        this._pendingLayout = [];

        let loadedCount = 0;
        let filteredCount = 0;
        let deferredCount = 0;

        layout.forEach(item => {
            // FILTER: Only load list widgets
            if (!isListWidget(item.type)) {
                console.warn(`⚠️ Filtering out non-list widget "${item.type}" from saved layout`);
                filteredCount++;
                return;
            }

            const module = (typeof WidgetManager !== 'undefined') ? WidgetManager.modules[item.type] : null;
            if (!module) {
                // Module hasn't registered yet. Queue and retry when widgets-registered fires.
                // We DO NOT drop it — dropping here was the root cause of "lists don't remember
                // their position": subsequent saveLayout() calls would overwrite storage with a
                // reduced set, permanently erasing these widgets.
                console.warn(`⏳ Module not registered yet for "${item.type}" — deferring`);
                this._pendingLayout.push(item);
                deferredCount++;
                return;
            }

            if (this._placeWidget(item, module)) loadedCount++;
        });

        // Attach a one-shot listener so deferred widgets get placed as soon as registration
        // completes. Idempotent: won't double-attach across re-loads.
        if (deferredCount > 0) this._attachDeferredPlacer();

        if (filteredCount > 0 && deferredCount === 0) {
            console.log(`✅ Layout loaded: ${loadedCount} list widgets (${filteredCount} non-list filtered)`);
            // Only safe to re-save here once nothing is deferred; saving with deferred pending
            // widgets is handled by saveLayout() which merges them back in.
            this.saveLayout();
        } else {
            console.log(`✅ Layout loaded: ${loadedCount} placed, ${deferredCount} deferred, ${filteredCount} filtered`);
        }
    }

    /**
     * Place a single saved widget into the grid. Returns true on success.
     * Extracted so loadFromData and the deferred-placer can share logic.
     */
    _placeWidget(item, module) {
        try {
            const config = getWidgetConfig(item.type);

            let sizeClass = 'size-medium';
            if (item.w <= 3) sizeClass = 'size-small';
            else if (item.w >= 7) sizeClass = 'size-large';

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

            this.addRemoveButton(gridItem);

            const widget = gridItem.querySelector('.widget');
            if (widget) {
                module.init(widget);
                this.updateListColumns(widget);
            }
            return true;
        } catch (err) {
            console.error(`❌ Failed to place widget "${item.type}":`, err);
            return false;
        }
    }

    /**
     * Idempotently register a one-shot listener that retries any widgets that were
     * deferred because their module wasn't registered at load time.
     */
    _attachDeferredPlacer() {
        if (this._deferredListenerAttached) return;
        this._deferredListenerAttached = true;
        const retry = () => {
            if (!this._pendingLayout || this._pendingLayout.length === 0) return;
            const stillPending = [];
            this._pendingLayout.forEach(item => {
                const module = (typeof WidgetManager !== 'undefined') ? WidgetManager.modules[item.type] : null;
                if (!module) { stillPending.push(item); return; }
                if (!this._placeWidget(item, module)) stillPending.push(item);
            });
            const placed = this._pendingLayout.length - stillPending.length;
            this._pendingLayout = stillPending;
            if (placed > 0) {
                console.log(`✅ Placed ${placed} deferred widget(s); ${stillPending.length} still pending`);
                // Only save once all deferreds are placed (otherwise saveLayout merges them anyway,
                // but an intermediate save is a waste).
                if (stillPending.length === 0) this.saveLayout();
            }
        };
        window.addEventListener('widgets-registered', retry, { once: true });
        // Safety net: even if widgets-registered never fires (e.g. synchronous register completed
        // before we attached), poll once after a short delay.
        setTimeout(retry, 500);
    }
    
    createDefaultLayout() {
        console.log('📐 Creating default lists layout');
        
        // Get lists-specific widgets from manifest
        const availableWidgets = WidgetManager.getAvailableWidgets('lists');
        const defaults = availableWidgets.slice(0, 5).map(w => ({ type: w.id }));
        
        if (defaults.length === 0) {
            // Fallback if manifest not loaded
            const fallback = ['shopping', 'work', 'personal', 'reminders', 'bucket'];
            defaults.push(...fallback.map(type => ({ type })));
        }
        
        defaults.forEach(item => {
            this.addWidget(item.type);
        });
        
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
console.log('📝 Lists-dashboard.js loading, setting up widget registration listener...');
window.addEventListener('widgets-registered', () => {
    console.log('🎯 Received widgets-registered event');
    if (!initializationAttempted) {
        setTimeout(() => initializeDashboard(), 50);
        return;
    }
    // Dashboard already initialized (probably via the fallback timeout) — re-hydrate
    // any widgets that were deferred because their module wasn't ready at load time.
    // Without this, a slow-registering Pi would silently drop those widgets from the grid
    // and the next saveLayout() would erase them from storage.
    try {
        if (window.dashboard && typeof window.dashboard._attachDeferredPlacer === 'function') {
            window.dashboard._attachDeferredPlacer();
        }
    } catch (e) { console.warn('Deferred placer failed:', e); }
}, { once: false });

// Reinitialize on BFCache restore or soft navigation where the page is resumed
window.addEventListener('pageshow', (event) => {
    // If coming from bfcache or if dashboard not ready, ensure init runs
    if (event.persisted || !window.dashboard) {
        console.log('🔄 pageshow detected (BFCache or resume) - ensuring lists dashboard initialization');
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

        // Fallback timeout — was 2s but on slower touch panels widget registration routinely
        // takes 2.5-4s (20 widgets × up to 250ms retry). Initializing before registration
        // finishes caused widgets to be silently dropped from the grid and then erased from
        // storage by the next save. 10s gives plenty of headroom while still recovering from
        // a truly stalled registration.
        setTimeout(() => {
            if (!dashboard) {
                console.warn('⚠️ Widget registration timeout (10s) - initializing dashboard anyway');
                window.widgetsRegistered = true;
                initializeDashboard();
            }
        }, 10000);
    }
}

// Set up DOMContentLoaded handler
if (document.readyState === 'loading') {
    window.addEventListener('DOMContentLoaded', setupDashboardInitialization);
} else {
    // DOM already loaded, run immediately
    console.log('⚠️ DOM already loaded when lists-dashboard.js ran');
    setupDashboardInitialization();
}

// Expose functions for HTML onclick handlers
window.toggleEditMode = () => dashboard?.toggleEditMode();
window.addWidget = (type) => dashboard?.addWidget(type);
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

