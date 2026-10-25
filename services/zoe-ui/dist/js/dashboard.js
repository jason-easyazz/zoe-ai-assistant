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
    'music-library': { defaultW: 8, defaultH: 5, minW: 6, minH: 4 }
};

// Widget types whose markup calls back through a per-TYPE global (zoe-orb's
// window.zoeWidgets.get(type) in inline onclick handlers; journal's bare
// `journalWidget` global, which init() points at the newest card), so a board
// may hold only one card of each. Everything else is one object per card
// (uid-keyed, reached via .widget.widgetInstance).
const SINGLETON_WIDGET_TYPES = new Set(['zoe-orb', 'journal']);

// _fetchServerLayout() could not read the member's saved layout (HTTP error or
// network failure). Distinct from null ("nothing saved yet"): a failure must
// never be treated as permission to overwrite the server copy.
const SERVER_LAYOUT_UNREADABLE = Symbol('server-layout-unreadable');
const SERVER_SAVE_RETRY_MS = 5000;   // a failed layout PUT is retried until the server acknowledges it

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

// A card's instance key. crypto first (no Math.random on the dashboard: the
// widget harness forbids it under js/widgets/, and ids do not need it).
let _widgetUidCounter = 0;
function newWidgetUid() {
    try {
        if (window.crypto && typeof window.crypto.randomUUID === 'function') {
            return window.crypto.randomUUID().replace(/-/g, '').slice(0, 16);
        }
        if (window.crypto && typeof window.crypto.getRandomValues === 'function') {
            const a = new Uint32Array(2);
            window.crypto.getRandomValues(a);
            return a[0].toString(16) + a[1].toString(16);
        }
    } catch (_) { /* fall through */ }
    _widgetUidCounter += 1;
    return 'w' + Date.now().toString(36) + _widgetUidCounter;
}

// Same rule as the server (routers/dashboard.py _UID_RE): a uid lands in an
// attribute, so anything else from storage is replaced, never interpolated.
function isWidgetUid(v) {
    return typeof v === 'string' && /^[A-Za-z0-9_-]{1,64}$/.test(v);
}

class Dashboard {
    constructor() {
        this.grid = null;
        this.isEditMode = false;
        this.storageKey = 'zoe_desktop_widgets';
        this.cardOptions = new Map();   // uid -> options a card was created with
        if (typeof WidgetManager !== 'undefined' && typeof WidgetManager.useInstances === 'function') {
            WidgetManager.useInstances();
        }
        this.serverSync = !(document.body && document.body.classList.contains('touch-dashboard'));
        this._serverSaveTimer = null;
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

        // A card dragged to the trash (or removed any other way) stops its timers.
        this.grid.on('removed', (event, items) => {
            (items || []).forEach(node => node && node.el && this._releaseGridItem(node.el));
        });
        
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
        this.loadLayout().catch((e) => {
            console.error('Dashboard layout failed to load:', e);
            this.createDefaultLayout();
        });
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
    
    // ── Cards are widget INSTANCES (Wave 4b, 2026-10-19) ──────────────────────
    // Every card gets its own widget object (WidgetManager.createInstance) and a
    // uid that lives on the element and in the saved layout ({uid, type, x, y, w,
    // h, options?}). Before, every card of a type init()ed the ONE registered
    // module, so a second weather card stole the first one's element and timer,
    // and a layout of two same-type cards could not be told apart on restore.
    _mountCard(type, geometry = {}, options = null, uid = null) {
        if (!this.grid) {
            console.error('❌ Grid not initialized');
            return null;
        }
        // own property only: a stored type such as 'constructor' must not resolve to Object's
        if (typeof WidgetManager === 'undefined' || typeof type !== 'string'
            || !Object.prototype.hasOwnProperty.call(WidgetManager.modules, type)) {
            console.warn('Widget type not found:', type);
            return null;
        }
        // A singleton-dispatched type (inline handlers resolve it by TYPE, see
        // SINGLETON_WIDGET_TYPES) cannot have two cards: the second would take over
        // the first card's clicks, and removing one would unregister the other.
        if (SINGLETON_WIDGET_TYPES.has(type) && this._hasCardOfType(type)) {
            console.warn('Only one ' + type + ' card is allowed on the board');
            return null;
        }
        const instance = (typeof WidgetManager.createInstance === 'function')
            ? WidgetManager.createInstance(type)
            : WidgetManager.modules[type];
        if (!instance) return null;

        const config = applyTouchWidgetDefaults(type, getWidgetConfig(type));
        const w = Number.isInteger(geometry.w) ? geometry.w : config.defaultW;
        const h = Number.isInteger(geometry.h) ? geometry.h : config.defaultH;
        let sizeClass = 'size-medium';
        if (w <= 3) sizeClass = 'size-small';
        else if (w >= 7) sizeClass = 'size-large';

        const cardUid = isWidgetUid(uid) ? uid : newWidgetUid();
        const widgetHTML = `
            <div class="widget ${sizeClass}" data-widget-type="${type}" data-widget-uid="${cardUid}">
                ${instance.getTemplate()}
            </div>
        `;
        const opts = {
            w, h,
            minW: config.minW, maxW: config.maxW,
            minH: config.minH, maxH: config.maxH,
            content: widgetHTML,
            id: cardUid
        };
        if (Number.isInteger(geometry.x) && Number.isInteger(geometry.y)) {
            opts.x = geometry.x;
            opts.y = geometry.y;
        } else {
            opts.autoPosition = true;
        }

        const gridItem = this.grid.addWidget(opts);
        this.addRemoveButton(gridItem);

        const widget = gridItem.querySelector('.widget');
        if (widget) {
            if (typeof WidgetManager.trackInstance === 'function') {
                WidgetManager.trackInstance(cardUid, instance);
            }
            if (options && typeof options === 'object' && Object.keys(options).length) {
                this.cardOptions.set(cardUid, options);
            }
            instance.init(widget, options || {});
            this.updateListColumns(widget);
        }
        return gridItem;
    }

    _hasCardOfType(type) {
        if (!this.grid || !this.grid.engine) return false;
        return this.grid.engine.nodes.some((node) => {
            const widget = node.el && node.el.querySelector ? node.el.querySelector('.widget') : null;
            return !!widget && widget.getAttribute('data-widget-type') === type;
        });
    }

    _releaseGridItem(gridItem) {
        const widget = gridItem && gridItem.querySelector ? gridItem.querySelector('.widget') : null;
        const uid = widget ? widget.getAttribute('data-widget-uid') : null;
        if (!uid) return;
        this.cardOptions.delete(uid);
        if (typeof WidgetManager !== 'undefined' && typeof WidgetManager.releaseInstance === 'function') {
            WidgetManager.releaseInstance(uid);
        }
    }

    addWidget(type, options = {}) {
        const gridItem = this._mountCard(type, {}, options);
        if (gridItem) {
            console.log(`✅ Added ${type}`);
            this.saveLayout();
        }
        return gridItem;
    }

    addRemoveButton(gridItem) {
        // Don't add if already exists
        if (gridItem.querySelector('.widget-remove-btn')) return;

        const removeBtn = document.createElement('button');
        removeBtn.className = 'widget-remove-btn';
        removeBtn.innerHTML = '×';
        removeBtn.title = 'Remove Widget';
        removeBtn.setAttribute('aria-label', 'Remove widget');
        removeBtn.onclick = (e) => {
            e.stopPropagation();
            if (confirm('Remove this widget?')) {
                this._releaseGridItem(gridItem);
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
            this._releaseGridItem(gridItem);
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
    
    currentLayout() {
        const layout = [];
        if (!this.grid) return layout;
        this.grid.engine.nodes.forEach((node) => {
            const widget = node.el && node.el.querySelector('.widget');
            if (!widget) return;
            const uid = widget.getAttribute('data-widget-uid');
            const entry = {
                uid,
                type: widget.getAttribute('data-widget-type'),
                x: node.x,
                y: node.y,
                w: node.w,
                h: node.h
            };
            const options = this.cardOptions.get(uid);
            if (options) entry.options = options;
            layout.push(entry);
        });
        return layout;
    }

    saveLayout() {
        if (!this.grid) return;
        const layout = this.currentLayout();
        try {
            localStorage.setItem(this.storageKey, JSON.stringify(layout));
        } catch (_) { /* private mode / quota: the server copy still saves */ }
        console.log('💾 Layout saved:', layout.length, 'widgets');
        // Not on the server until a PUT is acknowledged: remember that across reloads, so a
        // failed save or a page left inside the debounce is recovered, not overwritten by the
        // older server copy.
        if (this.serverSync && !this._serverSaveBlocked) this._setUnsynced(true);
        this._queueServerSave(layout);
    }

    // The desktop board follows the member to any browser: GET/PUT
    // /api/dashboard/layout/ (instances, see routers/dashboard.py). The touch
    // board keeps its browser-local layout - a kiosk is a guest.
    _queueServerSave(layout) {
        if (!this.serverSync) return;
        // The saved layout could not be read this session, so we do not know what
        // is on the server: a PUT of the fallback board would overwrite it.
        if (this._serverSaveBlocked) return;
        this._pendingServerLayout = layout;
        clearTimeout(this._serverSaveTimer);
        this._serverSaveTimer = setTimeout(() => this._flushServerSave(), 600);
    }

    // At most one PUT in flight: an older request can otherwise land last and
    // overwrite a newer layout. A change that arrives meanwhile waits, and only
    // the latest layout is sent once the in-flight PUT settles.
    async _flushServerSave() {
        if (this._serverSaveInFlight || this._serverSaveBlocked) return;
        const layout = this._pendingServerLayout;
        if (!layout) return;
        this._pendingServerLayout = null;
        this._serverSaveInFlight = true;
        let retryLater = false;
        try {
            const r = await fetch('/api/dashboard/layout/', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'same-origin',
                body: JSON.stringify({ layout, base_version: this._serverVersion || 'none' })
            });
            if (r.ok) {
                let body = null;
                try { body = await r.json(); } catch (_) { /* the save still landed */ }
                this._serverVersion = (body && body.version) || null;
                this._syncedUids = new Set(layout.map(c => c.uid));
                if (!this._pendingServerLayout) this._setUnsynced(false);
            } else if (r.status === 409) {
                // Chat changed the saved layout since this board read it. Keep this board's
                // edits and bring in the cards it has not seen, then save again.
                let conflict = null;
                try { conflict = (await r.json()).detail; } catch (_) { /* fall through to retry */ }
                if (conflict && Array.isArray(conflict.layout)) {
                    this._adoptServerCards(conflict.layout);
                    this._serverVersion = conflict.version || null;
                    this._pendingServerLayout = this.currentLayout();
                } else {
                    retryLater = true;
                    if (!this._pendingServerLayout) this._pendingServerLayout = layout;
                }
            } else {
                console.warn('Dashboard layout not saved to the server: HTTP ' + r.status);
                retryLater = true;
                if (!this._pendingServerLayout) this._pendingServerLayout = layout;
            }
        } catch (e) {
            console.warn('Dashboard layout not saved to the server:', e);
            retryLater = true;
            if (!this._pendingServerLayout) this._pendingServerLayout = layout;
        } finally {
            this._serverSaveInFlight = false;
            if (retryLater) {
                clearTimeout(this._serverSaveTimer);
                this._serverSaveTimer = setTimeout(() => this._flushServerSave(), SERVER_SAVE_RETRY_MS);
            } else if (this._pendingServerLayout) {
                this._flushServerSave();
            }
        }
    }

    // Cards on the server that this board never saw (added by chat) join the board.
    _adoptServerCards(serverLayout) {
        const known = this._syncedUids || new Set();
        serverLayout.forEach(card => {
            if (!card || known.has(card.uid)) return;
            const w = this._mountCard(card.type, card, card.options || null, card.uid);
            if (w) known.add(card.uid);
        });
        this._syncedUids = known;
    }

    _setUnsynced(flag) {
        try {
            if (flag) localStorage.setItem(this.storageKey + '_unsynced', '1');
            else localStorage.removeItem(this.storageKey + '_unsynced');
        } catch (_) { /* no storage: nothing to remember */ }
    }

    _isUnsynced() {
        try { return localStorage.getItem(this.storageKey + '_unsynced') === '1'; } catch (_) { return false; }
    }

    async _fetchServerLayout() {
        if (!this.serverSync) return null;
        try {
            const r = await fetch('/api/dashboard/layout/', { credentials: 'same-origin' });
            if (!r.ok) return SERVER_LAYOUT_UNREADABLE;
            const data = await r.json();
            // null = nothing saved yet (fall back to this browser / the default);
            // [] = the member removed every card, which must stay empty - not be
            // resurrected from localStorage or the default and saved back.
            this._serverVersion = (data && data.version) || null;
            return (data && Array.isArray(data.layout)) ? data.layout : null;
        } catch (_) {
            return SERVER_LAYOUT_UNREADABLE;
        }
    }

    _readLocalLayout(allowEmpty = false) {
        try {
            const layout = JSON.parse(localStorage.getItem(this.storageKey) || 'null');
            return (Array.isArray(layout) && (layout.length || allowEmpty)) ? layout : null;
        } catch (e) {
            console.error('Failed to read the saved layout:', e);
            return null;
        }
    }

    async loadLayout() {
        let fromServer = await this._fetchServerLayout();
        if (fromServer === SERVER_LAYOUT_UNREADABLE) {
            // Show this browser's copy (or the default) but never write it up: the
            // member's real layout is still on the server, we just could not read it.
            this._serverSaveBlocked = true;
            fromServer = undefined;
        }
        // This browser holds edits the server never acknowledged (a failed PUT, or the page
        // was left inside the debounce): they are newer than the server copy, so recover and
        // re-save them (the version check still merges anything chat added meanwhile).
        const unsynced = (this.serverSync && !this._serverSaveBlocked && this._isUnsynced())
            ? this._readLocalLayout(true) : null;
        const layout = unsynced || (Array.isArray(fromServer) ? fromServer : this._readLocalLayout());
        if (!layout) {
            this.createDefaultLayout();
            return;
        }
        this.loadFromData(layout);
        this._syncedUids = new Set(Array.isArray(fromServer) ? fromServer.map(c => c.uid) : []);
        if (unsynced) { this.saveLayout(); return; }
        // A board saved only in this browser (before Wave 4b) moves to the server
        // once, with uids minted for its cards.
        if (fromServer === null && this.serverSync && !this._serverSaveBlocked) this.saveLayout();
    }

    loadFromData(layout) {
        console.log('📋 Loading layout:', layout.length, 'widgets');

        if (typeof WidgetManager !== 'undefined' && typeof WidgetManager.releaseAll === 'function') {
            WidgetManager.releaseAll();
        }
        this.cardOptions.clear();
        this.grid.removeAll();

        const seen = new Set();
        layout.forEach(item => {
            if (!item || typeof item !== 'object') return;
            const type = item.type || item.id;   // legacy entries named the type `id`
            // Two cards must never share a uid (a hand-edited or legacy layout).
            const uid = (isWidgetUid(item.uid) && !seen.has(item.uid)) ? item.uid : null;
            const gridItem = this._mountCard(type, item, item.options || null, uid);
            if (gridItem) {
                const w = gridItem.querySelector('.widget');
                if (w) seen.add(w.getAttribute('data-widget-uid'));
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
    // Only a BFCache restore needs a re-init. On a plain first load `pageshow` fires
    // before widget registration finishes, and `!window.dashboard` made this log a
    // spurious "Cannot initialize dashboard" error on every load.
    if (event.persisted) {
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

