# Widget System Architecture v2.0

**Status**: ✅ Implemented - October 2025  
**Version**: 2.0.0  
**Author**: Zoe Architecture Team

## Overview

The widget system has been upgraded from a hardcoded system to a manifest-based, dynamic discovery architecture. This makes it easier to add new widgets, configure page-specific availability, and maintain widget metadata.

## What Changed

### Before (v1.0)
- Hardcoded widget lists in multiple files
- Widget configs scattered across dashboard.js and lists-dashboard.js
- Manual registration requiring code changes
- No easy way to discover available widgets

### After (v2.0)
- **Centralized manifest** (`widget-manifest.json`) for all metadata
- **Automatic registration** from manifest
- **Dynamic discovery** via API endpoint
- **Page-specific widget filtering** (dashboard vs lists vs touch)
- **Easy extension** - just add JSON entry

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                     Widget Manifest                      │
│          /js/widgets/widget-manifest.json                │
│  - Widget metadata (id, name, displayName, etc)         │
│  - Page availability (dashboard, lists, touch)          │
│  - Size constraints (minW, maxW, minH, maxH)             │
│  - Categories and icons                                  │
└────────────────────┬────────────────────────────────────┘
                     │
                     ↓
┌─────────────────────────────────────────────────────────┐
│                  WidgetManager                           │
│              /js/widget-system.js                        │
│  • loadManifest() - Fetches manifest                    │
│  • register() - Registers widget instances             │
│  • getAvailableWidgets(pageType) - Filters by page     │
│  • getWidgetConfig(widgetId) - Gets size constraints    │
│  • createWidget() - Creates widget DOM                 │
└────────────────────┬────────────────────────────────────┘
                     │
                     ↓
┌─────────────────────────────────────────────────────────┐
│              Dashboard Loaders                           │
│         dashboard.js & lists-dashboard.js               │
│  • Dynamic config via getWidgetConfig()                 │
│  • Auto-discovers available widgets                     │
│  • Page-specific default layouts                        │
└──────────────────────────────────────────────────────────┘
```

## Key Components

### 1. Widget Manifest (`widget-manifest.json`)

Single source of truth for all widgets:

```json
{
  "version": "1.0.0",
  "widgets": [
    {
      "id": "tasks",
      "name": "TasksWidget",
      "displayName": "Tasks",
      "description": "Today's tasks from all lists",
      "category": "productivity",
      "icon": "✅",
      "file": "core/tasks.js",
      "defaultSize": "size-medium",
      "dashboard": true,    // Available on dashboard?
      "lists": true,        // Available on lists page?
      "touch": true,        // Available on touch mode?
      "config": {
        "minW": 3, "maxW": 12,
        "minH": 4, "maxH": 12,
        "defaultW": 4, "defaultH": 6
      }
    }
  ],
  "categories": ["calendar", "productivity", "utility", ...]
}
```

### 2. WidgetManager (`widget-system.js`)

Enhanced with manifest-based operations:

```javascript
// Load manifest
await WidgetManager.loadManifest();

// Get widgets for specific page
const widgets = WidgetManager.getAvailableWidgets('dashboard');

// Get widget config
const config = WidgetManager.getWidgetConfig('tasks');

// Get categories
const categories = WidgetManager.getCategories();
```

### 3. Dashboard Loaders

Now use dynamic configuration:

```javascript
// Old way (hardcoded)
const config = WIDGET_CONFIGS[type];

// New way (dynamic)
const config = getWidgetConfig(type);
```

Default layouts are now page-specific:
- Dashboard gets widgets where `dashboard: true`
- Lists gets widgets where `lists: true`
- Touch gets widgets where `touch: true`

## API Endpoints

New discovery endpoints in `widget_builder.py`:

### GET `/api/widgets/available`
Returns all available widgets from manifest:
```json
{
  "version": "1.0.0",
  "widgets": [...],
  "categories": [...]
}
```

### GET `/api/widgets/{widget_id}/info`
Returns specific widget info:
```json
{
  "id": "tasks",
  "name": "TasksWidget",
  "description": "...",
  "config": {...}
}
```

## Benefits

1. **Single Source of Truth**: All widget metadata in one JSON file
2. **Easy Extension**: Add widgets by updating manifest + creating class
3. **Page-Specific Widgets**: Different widgets for dashboard vs lists
4. **Dynamic Discovery**: Frontend can query available widgets via API
5. **No Code Changes**: Add widgets without modifying registration logic
6. **Centralized Config**: Size constraints in manifest, not scattered code

## Adding New Widgets

### Step 1: Create Widget Class
```javascript
// core/my-widget.js
class MyWidget extends WidgetModule {
    constructor() {
        super('my-widget', {
            version: '1.0.0',
            defaultSize: 'size-small'
        });
    }
    
    getTemplate() {
        return `<div>My Widget</div>`;
    }
}
```

### Step 2: Add to Manifest
```json
{
  "id": "my-widget",
  "name": "MyWidget",
  "displayName": "My Widget",
  "category": "utility",
  "icon": "🔧",
  "file": "core/my-widget.js",
  "defaultSize": "size-small",
  "dashboard": true,
  "lists": false,
  "touch": true,
  "config": {
    "minW": 3, "maxW": 6,
    "minH": 3, "maxH": 5,
    "defaultW": 3, "defaultH": 3
  }
}
```

### Step 3: Import in HTML
```html
<script src="js/widgets/core/my-widget.js"></script>
```

Done! Widget is automatically registered and available.

## Migration Notes

- Existing layouts continue to work (backward compatible)
- Manifest fallback if file not found
- Default configs if manifest not loaded
- All widget classes still use same base class

## Future Enhancements

- [ ] Lazy-load widget scripts on demand
- [ ] Widget marketplace integration with manifest
- [ ] User-created widgets stored in `user/` directory
- [ ] Widget versioning and updates
- [ ] Widget dependencies support
- [ ] Multi-language widget descriptions

## Files Changed

- ✅ `widget-manifest.json` - Created
- ✅ `widget-system.js` - Added manifest support
- ✅ `dashboard.js` - Uses dynamic config
- ✅ `lists-dashboard.js` - Uses dynamic config
- ✅ `widget_builder.py` - Added discovery endpoints
- ✅ `README.md` - Updated documentation

## Testing

Test manifest-based system:

```bash
# Check manifest loads
curl http://localhost:8000/api/widgets/available

# Check widget info
curl http://localhost:8000/api/widgets/tasks/info

# Verify dashboard widgets
# Open dashboard.html, check console for "Widget manifest loaded"
```

## Summary

The widget system is now **future-proof, extensible, and maintainable**. New widgets can be added without modifying core registration code, and page-specific widget availability is easily configured through the manifest.
