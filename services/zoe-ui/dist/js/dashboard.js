/**
 * Zoe Dashboard - Clean Gridstack Implementation
 * Version: 1.0.0
 * 
 * Uses native Gridstack.js features with proper size constraints
 * No custom drag/drop hacks - industry standard solution
 */

const DASHBOARD_VERSION = '1.0.0';
console.log(`🎯 Zoe Dashboard v${DASHBOARD_VERSION} - Initializing...`);

// Widget size constraints per type - Optimized for content display
const WIDGET_CONFIGS = {
    time: {
        minW: 3, maxW: 8, minH: 3, maxH: 5,  // Display time/date/weather
        defaultW: 4, defaultH: 4
    },
    weather: {
        minW: 3, maxW: 6, minH: 3, maxH: 5,  // Better for forecast display
        defaultW: 4, defaultH: 4
    },
    tasks: {
        minW: 3, maxW: 12, minH: 4, maxH: 12,  // Minimum 4 rows for header + items
        defaultW: 4, defaultH: 6
    },
    events: {
        minW: 3, maxW: 12, minH: 4, maxH: 12,  // Minimum 4 rows for header + events
        defaultW: 4, defaultH: 6
    },
    shopping: {
        minW: 3, maxW: 8, minH: 4, maxH: 10,  // Minimum 4 for list header + items
        defaultW: 4, defaultH: 5
    },
    'dynamic-list': {
        minW: 3, maxW: 12, minH: 4, maxH: 12,
        defaultW: 4, defaultH: 6
    },
    notes: {
        minW: 3, maxW: 12, minH: 4, maxH: 10,  // Needs space for writing
        defaultW: 4, defaultH: 5
    },
    home: {
        minW: 3, maxW: 5, minH: 3, maxH: 4,  // Bigger for controls
        defaultW: 3, defaultH: 3
    },
    reminders: {
        minW: 3, maxW: 8, minH: 4, maxH: 8,
        defaultW: 4, defaultH: 5
    },
    personal: {
        minW: 3, maxW: 8, minH: 4, maxH: 6,
        defaultW: 4, defaultH: 4
    },
    work: {
        minW: 3, maxW: 10, minH: 4, maxH: 8,
        defaultW: 4, defaultH: 5
    },
    bucket: {
        minW: 3, maxW: 10, minH: 4, maxH: 10,
        defaultW: 4, defaultH: 6
    },
    system: {
        minW: 3, maxW: 6, minH: 3, maxH: 5,
        defaultW: 3, defaultH: 3
    },
    'zoe-orb': {
        minW: 3, maxW: 5, minH: 3, maxH: 4,  // Needs space for orb visual
        defaultW: 3, defaultH: 3
    }
};

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
        
        // Detect if mobile for compact mode
        const isMobile = window.matchMedia('(max-width: 768px)').matches;
        
        // Initialize Gridstack with NATIVE features - no custom hacks!
        this.grid = GridStack.init({
            column: 12,
            cellHeight: 70,
            margin: 16,
            float: !isMobile,  // Mobile: compact mode (false = widgets stack), Desktop: free-form (true)
            animate: true,
            // Enable NATIVE resize with handles (like the official demo)
            // On mobile: only bottom resize (height), Desktop: all sides
            resizable: {
                handles: isMobile ? 's' : 'e, se, s, sw, w'
            },
            // Mobile-friendly responsive columns
            disableOneColumnMode: false,
            columnOpts: {
                breakpoints: [
                    {w: 768, c: 6},   // Tablet: 6 columns
                    {w: 480, c: 4}    // Mobile: 4 columns
                ]
            },
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
                distance: 5,  // Pixels to move before drag starts
                delay: 300,   // Milliseconds to hold before drag (helps distinguish from scroll)
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
        });
        
        // Expose globally for other functions
        window.grid = this.grid;
        
        // Handle screen rotation / resize for mobile compact mode
        let resizeTimer;
        window.addEventListener('resize', () => {
            clearTimeout(resizeTimer);
            resizeTimer = setTimeout(() => {
                const nowMobile = window.matchMedia('(max-width: 768px)').matches;
                // Update float mode based on screen size
                this.grid.float(!nowMobile);
                console.log(`📱 Screen resize: ${nowMobile ? 'Mobile compact mode' : 'Desktop free-form mode'}`);
            }, 250);
        });
        
        console.log(`✅ Gridstack initialized with native drag & resize (${isMobile ? 'Mobile compact' : 'Desktop free-form'})`);
        
        // Load saved layout
        this.loadLayout();
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
        
        if (this.isEditMode) {
            fabBtn?.classList.add('active');
            if (fabIcon) fabIcon.textContent = '✓';
            if (fabBtn) fabBtn.title = 'Done Editing';
            console.log('✏️ Edit mode: drag, resize & remove enabled');
        } else {
            fabBtn?.classList.remove('active');
            if (fabIcon) fabIcon.textContent = '✏️';
            if (fabBtn) fabBtn.title = 'Edit Dashboard';
            this.saveLayout();
            console.log('💾 View mode: layout saved');
        }
    }
    
    addWidget(type) {
        console.log('🎯 Dashboard.addWidget called with type:', type);
        
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
        
        const config = WIDGET_CONFIGS[type] || {
            minW: 2, maxW: 12, minH: 2, maxH: 8,
            defaultW: 3, defaultH: 3
        };
        
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
            const config = WIDGET_CONFIGS[item.type] || {
                minW: 2, maxW: 12, minH: 2, maxH: 8
            };
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
        
        const defaults = [
            {type: 'time'},
            {type: 'weather'},
            {type: 'events'},
            {type: 'tasks'},
            {type: 'home'}
        ];
        
        defaults.forEach(item => {
            this.addWidget(item.type);
        });
        
        this.saveLayout();
    }
}

// Initialize dashboard when DOM is ready
let dashboard = null;

window.addEventListener('DOMContentLoaded', () => {
    // Wait for WidgetManager to be ready
    setTimeout(() => {
        if (typeof WidgetManager !== 'undefined') {
            dashboard = new Dashboard();
            dashboard.init();
            window.dashboard = dashboard;
            console.log('🎯 Dashboard ready with native Gridstack features!');
            
            // Initialize WebSocket for real-time updates
            const session = window.zoeAuth?.getCurrentSession();
            const userId = session?.user_info?.user_id || session?.user_id || 'default';
            if (typeof ZoeWebSockets !== 'undefined') {
                ZoeWebSockets.init(userId);
                console.log('🔌 WebSocket sync initialized for user:', userId);
            }
        } else {
            console.error('WidgetManager not loaded');
        }
    }, 100);
});

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

// Initialize pull-to-refresh
if (window.matchMedia('(max-width: 768px)').matches) {
    initPullToRefresh();
}

window.forceRefreshCache = forceRefreshCache;

