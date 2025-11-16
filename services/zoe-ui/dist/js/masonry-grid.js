/**
 * Masonry Grid System with Device-Specific Layout Persistence
 * Provides smart content-based widget sizing with no wasted space
 * Version: 1.0.0
 */

const MasonryGrid = {
    currentDeviceProfile: null,
    resizeObservers: new Map(),
    
    /**
     * Get device profile for layout persistence
     * @returns {string} Device profile identifier
     */
    getDeviceProfile() {
        const width = window.innerWidth;
        const height = window.innerHeight;
        const isPortrait = height > width;
        const isMobile = width < 768;
        const isTablet = width >= 768 && width < 1024;
        const isDesktop = width >= 1024;
        
        // Generate unique device profile key
        const deviceType = isMobile ? 'mobile' : isTablet ? 'tablet' : 'desktop';
        const orientation = isPortrait ? 'portrait' : 'landscape';
        const userAgent = navigator.userAgent.includes('iPhone') ? 'iphone' : 
                          navigator.userAgent.includes('iPad') ? 'ipad' :
                          navigator.userAgent.includes('Android') ? 'android' : 'desktop';
        
        return `${userAgent}-${deviceType}-${orientation}`;
    },
    
    /**
     * Initialize masonry grid
     * @param {HTMLElement} gridElement - Grid container element
     */
    init(gridElement) {
        if (!gridElement) {
            console.error('Grid element not found');
            return;
        }
        
        // Set initial device profile
        this.currentDeviceProfile = this.getDeviceProfile();
        console.log('📱 Device profile:', this.currentDeviceProfile);
        
        // Apply masonry styles
        this.applyMasonryStyles(gridElement);
        
        // Don't setup resize observers - let CSS Grid handle layout naturally
        // Only set initial heights
        this.setInitialWidgetHeights(gridElement);
        
        // Listen for window resize/orientation change
        window.addEventListener('resize', () => this.handleResize(gridElement));
        window.addEventListener('orientationchange', () => this.handleResize(gridElement));
    },
    
    /**
     * Calculate and set grid row spans for masonry layout
     * @param {HTMLElement} gridElement - Grid container
     */
    setInitialWidgetHeights(gridElement) {
        const widgets = gridElement.querySelectorAll('.widget');
        const rowHeight = 10; // Must match grid-auto-rows in CSS
        const gap = 16; // Must match gap in CSS
        
        widgets.forEach(widget => {
            // Force layout calculation
            widget.style.gridRowEnd = 'auto';
            
            // Get actual height after content is rendered
            setTimeout(() => {
                const height = widget.getBoundingClientRect().height;
                const rowSpan = Math.ceil((height + gap) / (rowHeight + gap));
                widget.style.gridRowEnd = `span ${rowSpan}`;
            }, 50);
        });
    },
    
    /**
     * Apply masonry CSS styles
     * @param {HTMLElement} gridElement - Grid container
     */
    applyMasonryStyles(gridElement) {
        gridElement.style.display = 'grid';
        gridElement.style.gridTemplateColumns = 'repeat(auto-fit, minmax(280px, 1fr))';
        gridElement.style.gridAutoRows = 'minmax(min-content, max-content)';
        gridElement.style.gridAutoFlow = 'dense';
        gridElement.style.gap = '16px';
        gridElement.style.padding = '20px';
    },
    
    /**
     * Setup resize observers for dynamic height adjustment
     * @param {HTMLElement} gridElement - Grid container
     */
    setupResizeObservers(gridElement) {
        const widgets = gridElement.querySelectorAll('.widget');
        
        widgets.forEach(widget => {
            if (this.resizeObservers.has(widget)) return;
            
            // Debounce to prevent infinite loops
            let resizeTimeout;
            const observer = new ResizeObserver(entries => {
                clearTimeout(resizeTimeout);
                resizeTimeout = setTimeout(() => {
                    for (let entry of entries) {
                        this.adjustWidgetHeight(entry.target);
                    }
                }, 100);
            });
            
            observer.observe(widget);
            this.resizeObservers.set(widget, observer);
        });
    },
    
    /**
     * Adjust widget height based on content
     * @param {HTMLElement} widget - Widget element
     */
    adjustWidgetHeight(widget) {
        const content = widget.querySelector('.widget-content');
        if (!content) return;
        
        const contentHeight = content.scrollHeight;
        const headerHeight = 80; // Approximate header height with controls
        const padding = 40; // Padding
        const totalHeight = contentHeight + headerHeight + padding;
        
        // Apply min and max constraints
        const minHeight = 250;
        const maxHeight = 600;
        const constrainedHeight = Math.max(minHeight, Math.min(totalHeight, maxHeight));
        
        // Only update if significantly different to prevent loop
        const currentHeight = parseInt(widget.style.minHeight) || 0;
        if (Math.abs(currentHeight - constrainedHeight) > 10) {
            widget.style.minHeight = `${constrainedHeight}px`;
        }
    },
    
    /**
     * Handle window resize/orientation change
     * @param {HTMLElement} gridElement - Grid container
     */
    handleResize(gridElement) {
        const newProfile = this.getDeviceProfile();
        
        // If device profile changed, load appropriate layout
        if (newProfile !== this.currentDeviceProfile) {
            console.log(`📱 Device profile changed: ${this.currentDeviceProfile} → ${newProfile}`);
            this.currentDeviceProfile = newProfile;
            
            // Trigger layout reload if function exists
            if (typeof loadLayout === 'function') {
                loadLayout();
            }
        }
        
        // Re-apply masonry styles
        this.applyMasonryStyles(gridElement);
    },
    
    /**
     * Observe new widget added to grid
     * @param {HTMLElement} widget - Widget element
     */
    observeWidget(widget) {
        const rowHeight = 10;
        const gap = 16;
        
        // Calculate row span for new widget
        setTimeout(() => {
            const height = widget.getBoundingClientRect().height;
            const rowSpan = Math.ceil((height + gap) / (rowHeight + gap));
            widget.style.gridRowEnd = `span ${rowSpan}`;
        }, 100);
    },
    
    /**
     * Stop observing widget
     * @param {HTMLElement} widget - Widget element
     */
    unobserveWidget(widget) {
        const observer = this.resizeObservers.get(widget);
        if (observer) {
            observer.disconnect();
            this.resizeObservers.delete(widget);
        }
    },
    
    /**
     * Get storage key for current device
     * @param {string} prefix - Key prefix
     * @returns {string}
     */
    getStorageKey(prefix = 'zoe_layout') {
        return `${prefix}_${this.currentDeviceProfile}`;
    },
    
    /**
     * Save layout for current device
     * @param {Array} layout - Layout configuration
     * @param {string} key - Storage key prefix
     */
    saveLayout(layout, key = 'zoe_layout') {
        const storageKey = this.getStorageKey(key);
        localStorage.setItem(storageKey, JSON.stringify(layout));
        console.log(`💾 Layout saved for ${this.currentDeviceProfile}`);
    },
    
    /**
     * Load layout for current device
     * @param {string} key - Storage key prefix
     * @returns {Array|null}
     */
    loadLayout(key = 'zoe_layout') {
        const storageKey = this.getStorageKey(key);
        const saved = localStorage.getItem(storageKey);
        
        if (saved) {
            try {
                const layout = JSON.parse(saved);
                console.log(`📋 Layout loaded for ${this.currentDeviceProfile}:`, layout.length, 'widgets');
                return layout;
            } catch (e) {
                console.error('Failed to parse layout:', e);
                return null;
            }
        }
        
        return null;
    },
    
    /**
     * Clean up all observers
     */
    destroy() {
        this.resizeObservers.forEach(observer => observer.disconnect());
        this.resizeObservers.clear();
    }
};

// Export for use in modules
if (typeof module !== 'undefined' && module.exports) {
    module.exports = MasonryGrid;
}

