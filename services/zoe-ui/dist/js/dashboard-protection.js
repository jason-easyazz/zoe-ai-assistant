/**
 * Dashboard Layout Protection & Recovery System
 * Prevents corrupted layouts from being saved or loaded
 * Version: 1.0.0
 */

const LAYOUT_VERSION = '1.0';

class LayoutProtection {
    /**
     * Validate a layout object before saving
     */
    static validateForSave(layout) {
        if (!Array.isArray(layout)) {
            console.error('❌ Invalid layout: not an array');
            return false;
        }

        for (let i = 0; i < layout.length; i++) {
            const item = layout[i];
            
            // Check required fields
            if (!item.type || item.type === 'undefined' || item.type === 'null') {
                console.error(`❌ Invalid widget at index ${i}: missing or invalid type`, item);
                return false;
            }
            
            // Check numeric fields
            if (typeof item.x !== 'number' || typeof item.y !== 'number' ||
                typeof item.w !== 'number' || typeof item.h !== 'number') {
                console.error(`❌ Invalid widget at index ${i}: invalid dimensions`, item);
                return false;
            }
        }

        return true;
    }

    /**
     * Validate and sanitize a layout when loading
     */
    static validateForLoad(layout) {
        if (!Array.isArray(layout)) {
            console.error('❌ Invalid layout format: not an array');
            return null;
        }

        // Filter out invalid widgets
        const valid = layout.filter((item, index) => {
            if (!item.type || item.type === 'undefined' || item.type === 'null') {
                console.warn(`⚠️ Skipping invalid widget at index ${index}: missing type`);
                return false;
            }
            
            if (typeof item.x !== 'number' || typeof item.y !== 'number' ||
                typeof item.w !== 'number' || typeof item.h !== 'number') {
                console.warn(`⚠️ Skipping invalid widget at index ${index}: invalid dimensions`);
                return false;
            }
            
            return true;
        });

        if (valid.length === 0) {
            console.error('❌ No valid widgets in layout');
            return null;
        }

        if (valid.length < layout.length) {
            console.warn(`⚠️ Filtered ${layout.length - valid.length} invalid widgets`);
        }

        return valid;
    }

    /**
     * Save layout with validation and versioning
     */
    static saveLayout(storageKey, layout) {
        // Validate before saving
        if (!this.validateForSave(layout)) {
            console.error('❌ Layout validation failed - NOT saving corrupted data');
            return false;
        }

        const data = {
            version: LAYOUT_VERSION,
            timestamp: new Date().toISOString(),
            layout: layout
        };

        try {
            localStorage.setItem(storageKey, JSON.stringify(data));
            console.log(`✅ Layout saved: ${layout.length} widgets (v${LAYOUT_VERSION})`);
            return true;
        } catch (e) {
            console.error('❌ Failed to save layout:', e);
            return false;
        }
    }

    /**
     * Load layout with validation and version checking
     */
    static loadLayout(storageKey) {
        const saved = localStorage.getItem(storageKey);
        
        if (!saved) {
            console.log('ℹ️ No saved layout found');
            return null;
        }

        try {
            const data = JSON.parse(saved);
            
            // Handle old format (array directly) vs new format (object with version)
            let layout;
            if (Array.isArray(data)) {
                console.warn('⚠️ Old layout format detected - migrating...');
                layout = data;
            } else if (data.layout) {
                console.log(`📦 Loading layout v${data.version || 'unknown'}`);
                layout = data.layout;
            } else {
                console.error('❌ Invalid layout structure');
                return null;
            }

            // Validate and sanitize
            const validated = this.validateForLoad(layout);
            
            if (!validated) {
                console.error('❌ Layout validation failed');
                return null;
            }

            return validated;
            
        } catch (e) {
            console.error('❌ Failed to parse layout:', e);
            return null;
        }
    }

    /**
     * Clear corrupted layout and reset
     */
    static resetLayout(storageKey) {
        console.warn('🔄 Resetting corrupted layout');
        localStorage.removeItem(storageKey);
    }
}

// Make available globally
window.LayoutProtection = LayoutProtection;

