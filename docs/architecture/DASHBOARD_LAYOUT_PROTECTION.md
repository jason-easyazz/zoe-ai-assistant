# Dashboard Layout Protection System

## Overview
The Dashboard Layout Protection system prevents corrupted layout data from being saved to localStorage and provides automatic recovery when invalid data is detected.

## Problem This Solves

### What Happened (Oct 23, 2025)
During router failures, dashboard widgets partially loaded without proper `data-widget-type` attributes. When users toggled edit mode or moved widgets, the system saved layouts with `type: undefined`, breaking the dashboard on reload.

### Root Cause
```javascript
// saveLayout() was blindly saving whatever it found:
type: widget.getAttribute('data-widget-type')  // Could be null/undefined!
```

If the DOM element didn't have the attribute (due to failed initialization), it saved `undefined`, creating corrupted localStorage data.

## Solution Architecture

### 1. Validation Before Save
**Location**: `lists-dashboard.js` line 325-331

```javascript
const widgetType = widget.getAttribute('data-widget-type');

// PROTECTION: Don't save widgets with invalid types
if (!widgetType || widgetType === 'undefined' || widgetType === 'null') {
    console.warn('⚠️ Skipping widget with invalid type:', widgetType);
    return;  // Skip this widget
}
```

**Benefit**: Prevents corrupted data from entering localStorage in the first place.

### 2. Empty Layout Detection
**Location**: `lists-dashboard.js` line 344-347

```javascript
// PROTECTION: Don't save if no valid widgets
if (layout.length === 0) {
    console.error('❌ No valid widgets to save - layout corrupt, not saving');
    return;
}
```

**Benefit**: If all widgets are invalid, don't overwrite existing good data with empty array.

### 3. Advanced Protection Layer
**Location**: `dashboard-protection.js`

Provides:
- **Version tracking** - Future-proof for layout format changes
- **Comprehensive validation** - Checks all required fields and types
- **Data sanitization** - Filters out invalid widgets while keeping good ones
- **Automatic recovery** - Falls back to defaults if validation fails
- **Migration support** - Handles old format → new format conversion

### 4. Loading with Recovery
**Location**: `lists-dashboard.js` line 359-389

```javascript
let layout = null;

if (window.LayoutProtection) {
    layout = LayoutProtection.loadLayout(this.storageKey);
    
    if (layout === null) {
        // Validation failed - clear corrupted data
        console.warn('🔄 Clearing corrupted layout');
        localStorage.removeItem(this.storageKey);
    }
}

if (layout && Array.isArray(layout) && layout.length > 0) {
    this.loadFromData(layout);
} else {
    console.log('📐 No valid layout found - creating default');
    this.createDefaultLayout();
}
```

**Benefit**: Corrupted data is automatically detected, cleared, and replaced with defaults.

## How It Prevents Future Issues

### Scenario 1: Router Failure During Page Load
1. Page loads, widgets fail to initialize properly
2. User toggles edit mode (triggers saveLayout)
3. **OLD BEHAVIOR**: Saves `type: undefined`, breaks dashboard
4. **NEW BEHAVIOR**: Detects invalid type, skips widget, logs warning

### Scenario 2: Partial Widget Corruption
1. 3 widgets load correctly, 2 fail
2. User moves a widget (triggers saveLayout)
3. **OLD BEHAVIOR**: Saves all 5 widgets, 2 with `undefined`
4. **NEW BEHAVIOR**: Saves only the 3 valid widgets, filters out 2 invalid

### Scenario 3: Complete Corruption
1. All widgets fail to load (e.g., JS error)
2. User toggles edit mode
3. **OLD BEHAVIOR**: Saves empty array or all-undefined array
4. **NEW BEHAVIOR**: Detects zero valid widgets, aborts save, preserves previous good data

### Scenario 4: Loading Corrupted Data
1. User opens dashboard with corrupted localStorage (from before fix)
2. **OLD BEHAVIOR**: Crashes with "module not found" error
3. **NEW BEHAVIOR**: Validates data, detects corruption, clears it, loads defaults

## Files Modified

### Created
- `services/zoe-ui/dist/js/dashboard-protection.js` - Protection layer (181 lines)

### Updated
- `services/zoe-ui/dist/js/lists-dashboard.js` - Added validation to save/load
- `services/zoe-ui/dist/lists.html` - Included protection script

### To Update (Future)
- `services/zoe-ui/dist/js/calendar-dashboard.js` - Apply same protection
- `services/zoe-ui/dist/calendar.html` - Include protection script

## Testing

### Manual Test: Invalid Widget Detection
```javascript
// In browser console on lists page:
const dashboard = window.listsDashboard;

// Try to save layout with invalid widget
dashboard.grid.engine.nodes[0].el.querySelector('.widget').removeAttribute('data-widget-type');
dashboard.saveLayout();
// Should see: "⚠️ Skipping widget with invalid type: null"
```

### Manual Test: Corrupted Data Recovery
```javascript
// Corrupt the localStorage
localStorage.setItem('lists-dashboard-layout', JSON.stringify([
    {type: undefined, x: 0, y: 0, w: 4, h: 3}
]));

// Reload page
location.reload();
// Should see: "🔄 Clearing corrupted layout" and "📐 No valid layout found - creating default"
```

### Manual Test: Version Migration
```javascript
// Simulate old format (array directly)
localStorage.setItem('lists-dashboard-layout', JSON.stringify([
    {type: 'shopping-list', x: 0, y: 0, w: 4, h: 3}
]));

// Reload page
location.reload();
// Should see: "⚠️ Old layout format detected - migrating..."
```

## Monitoring

### Console Warnings to Watch
- `⚠️ Skipping widget with invalid type:` - Widget initialization failed
- `❌ No valid widgets to save` - Catastrophic failure, investigate immediately
- `⚠️ Filtered N invalid widgets` - Partial corruption, review widget loading

### Success Messages
- `✅ Layout saved: N widgets (v1.0)` - Using new protection system
- `💾 Layout saved: N widgets` - Using fallback (protection script not loaded)

## Future Enhancements

### Backend Sync (Planned)
```javascript
// TODO in lists-dashboard.js line 339
// Save to backend API
```

When implemented, add similar validation on backend:
- Validate layout structure before persisting to database
- Return validation errors to frontend
- Implement conflict resolution (local vs server)

### Multi-Dashboard Support
If adding more dashboards (calendar, settings, etc.):
1. Include `dashboard-protection.js` in HTML
2. Use `LayoutProtection.saveLayout()` and `LayoutProtection.loadLayout()`
3. Implement same validation in saveLayout() method
4. Add dashboard-specific validation rules to `LayoutProtection` class

### Enhanced Validation
Future additions to `LayoutProtection.validateForSave()`:
- Check widget type exists in `WidgetManager.modules`
- Validate widget dimensions are within allowed ranges
- Check for overlapping widgets
- Validate grid position doesn't exceed bounds

## Related Documentation
- `docs/architecture/FRONTEND_ARCHITECTURE.md` - Overall frontend design
- `docs/guides/DASHBOARD_CUSTOMIZATION.md` - User guide for dashboards
- Project Structure Rules - File organization standards

## Changelog
- **2025-10-23**: Initial implementation - Prevents undefined widget types




