/**
 * List Configurations and Color System
 * Version: 1.0.0
 * 
 * Defines list colors, icons, and size constraints
 * Colors are consistent across dashboard, lists, and calendar pages
 */

const LIST_VERSION = '1.0.0';

// List color palette - synced with calendar.html
const LIST_COLORS = {
    blue: {
        name: 'Blue',
        bg: 'rgba(59, 130, 246, 0.15)',
        bgHover: 'rgba(59, 130, 246, 0.25)',
        border: '#2563eb',
        text: '#2563eb',
        gradient: 'linear-gradient(135deg, rgba(59, 130, 246, 0.1) 0%, rgba(96, 165, 250, 0.1) 100%)'
    },
    purple: {
        name: 'Purple',
        bg: 'rgba(147, 51, 234, 0.15)',
        bgHover: 'rgba(147, 51, 234, 0.25)',
        border: '#9333ea',
        text: '#9333ea',
        gradient: 'linear-gradient(135deg, rgba(147, 51, 234, 0.1) 0%, rgba(168, 85, 247, 0.1) 100%)'
    },
    green: {
        name: 'Green',
        bg: 'rgba(16, 185, 129, 0.15)',
        bgHover: 'rgba(16, 185, 129, 0.25)',
        border: '#059669',
        text: '#059669',
        gradient: 'linear-gradient(135deg, rgba(16, 185, 129, 0.1) 0%, rgba(34, 197, 94, 0.1) 100%)'
    },
    orange: {
        name: 'Orange',
        bg: 'rgba(251, 146, 60, 0.15)',
        bgHover: 'rgba(251, 146, 60, 0.25)',
        border: '#ea580c',
        text: '#ea580c',
        gradient: 'linear-gradient(135deg, rgba(251, 146, 60, 0.1) 0%, rgba(249, 115, 22, 0.1) 100%)'
    },
    red: {
        name: 'Red',
        bg: 'rgba(239, 68, 68, 0.15)',
        bgHover: 'rgba(239, 68, 68, 0.25)',
        border: '#ef4444',
        text: '#ef4444',
        gradient: 'linear-gradient(135deg, rgba(239, 68, 68, 0.1) 0%, rgba(248, 113, 113, 0.1) 100%)'
    },
    pink: {
        name: 'Pink',
        bg: 'rgba(236, 72, 153, 0.15)',
        bgHover: 'rgba(236, 72, 153, 0.25)',
        border: '#ec4899',
        text: '#ec4899',
        gradient: 'linear-gradient(135deg, rgba(236, 72, 153, 0.1) 0%, rgba(244, 114, 182, 0.1) 100%)'
    },
    yellow: {
        name: 'Yellow',
        bg: 'rgba(234, 179, 8, 0.15)',
        bgHover: 'rgba(234, 179, 8, 0.25)',
        border: '#eab308',
        text: '#ca8a04',
        gradient: 'linear-gradient(135deg, rgba(234, 179, 8, 0.1) 0%, rgba(250, 204, 21, 0.1) 100%)'
    },
    teal: {
        name: 'Teal',
        bg: 'rgba(20, 184, 166, 0.15)',
        bgHover: 'rgba(20, 184, 166, 0.25)',
        border: '#14b8a6',
        text: '#0f766e',
        gradient: 'linear-gradient(135deg, rgba(20, 184, 166, 0.1) 0%, rgba(45, 212, 191, 0.1) 100%)'
    },
    indigo: {
        name: 'Indigo',
        bg: 'rgba(99, 102, 241, 0.15)',
        bgHover: 'rgba(99, 102, 241, 0.25)',
        border: '#6366f1',
        text: '#4f46e5',
        gradient: 'linear-gradient(135deg, rgba(99, 102, 241, 0.1) 0%, rgba(129, 140, 248, 0.1) 100%)'
    },
    gray: {
        name: 'Gray',
        bg: 'rgba(107, 114, 128, 0.15)',
        bgHover: 'rgba(107, 114, 128, 0.25)',
        border: '#6b7280',
        text: '#4b5563',
        gradient: 'linear-gradient(135deg, rgba(107, 114, 128, 0.1) 0%, rgba(156, 163, 175, 0.1) 100%)'
    }
};

// Default list type configurations
const LIST_TYPE_DEFAULTS = {
    shopping: {
        name: 'Shopping List',
        icon: '🛒',
        color: 'orange',
        defaultW: 4,
        defaultH: 6
    },
    personal: {
        name: 'Personal',
        icon: '📝',
        color: 'purple',
        defaultW: 4,
        defaultH: 6
    },
    work: {
        name: 'Work Tasks',
        icon: '💼',
        color: 'blue',
        defaultW: 4,
        defaultH: 6
    },
    bucket: {
        name: 'Bucket List',
        icon: '🎯',
        color: 'green',
        defaultW: 4,
        defaultH: 8
    },
    reminders: {
        name: 'Reminders',
        icon: '🔔',
        color: 'red',
        defaultW: 4,
        defaultH: 5
    },
    custom: {
        name: 'Custom List',
        icon: '📋',
        color: 'gray',
        defaultW: 4,
        defaultH: 6
    }
};

// Gridstack size constraints for all lists - Compact defaults
const LIST_SIZE_CONFIG = {
    minW: 3,        // Minimum 3 columns
    maxW: 12,       // Maximum full width
    minH: 3,        // Minimum 3 rows (header + few items)
    maxH: 16,       // Maximum 16 rows (super long lists!)
    defaultW: 3,    // Default 3 columns (compact)
    defaultH: 4     // Default 4 rows (smaller)
};

// Common emoji icons for lists
const LIST_ICONS = [
    '🛒', '📝', '💼', '🎯', '🔔', '🏠', '💡', '📚', 
    '🎨', '🏋️', '🍔', '✈️', '💰', '🎵', '📱', '🎮',
    '🌱', '📊', '🔧', '🎁', '📷', '⚽', '🎬', '🍕'
];

// Export for use in other modules
window.LIST_COLORS = LIST_COLORS;
window.LIST_TYPE_DEFAULTS = LIST_TYPE_DEFAULTS;
window.LIST_SIZE_CONFIG = LIST_SIZE_CONFIG;
window.LIST_ICONS = LIST_ICONS;

console.log(`✅ List Configs v${LIST_VERSION} loaded - ${Object.keys(LIST_COLORS).length} colors, ${LIST_ICONS.length} icons`);

