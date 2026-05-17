/**
 * Widget Settings Sheet — stub implementation.
 * Provides a minimal no-op so dashboard.js can call WidgetSettingsSheet.open()
 * without a 404 or a ReferenceError.  Full implementation to be added when
 * per-widget settings UI is built out.
 */
window.WidgetSettingsSheet = {
    open(type, widget, gridItem, context) {
        console.log('[WidgetSettingsSheet] open() called for widget type:', type);
    },
    close() {}
};
