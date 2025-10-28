# Zoe Widget Directory

This directory contains all widget modules for the Zoe dashboard system.

## Structure

```
widgets/
├── widget-manifest.json  # Widget metadata and configuration (NEW!)
├── core/           # Core widgets (built-in, always available)
│   ├── events.js   # Calendar events widget
│   ├── tasks.js    # Tasks and todos widget
│   ├── time.js     # Clock widget
│   ├── weather.js  # Weather forecast widget
│   ├── home.js     # Smart home controls
│   ├── system.js   # System status widget
│   ├── notes.js    # Quick notes widget
│   └── zoe-orb.js  # Zoe AI assistant widget
└── user/           # User-created widgets (custom, AI-generated, marketplace)
```

## Manifest-Based Widget System

**Version 2.0** - Widgets are now dynamically discovered from `widget-manifest.json`:

- **Automatic Registration**: No hardcoded widget lists
- **Page-Specific Widgets**: Dashboard vs Lists vs Touch mode
- **Centralized Configuration**: All size constraints in one place  
- **Easy Extension**: Add widgets by updating manifest
- **Dynamic Discovery**: API endpoint provides widget metadata

See `widget-manifest.json` for widget definitions and metadata.

## Core Widgets

### Events Widget (`events.js`)
- Displays today's calendar events
- Auto-updates every 30 seconds
- Color-coded by category
- Fetches from `/api/calendar/events`

### Tasks Widget (`tasks.js`)
- Shows pending tasks from all lists
- Auto-updates every minute
- Priority color coding
- Interactive checkboxes
- Fetches from `/api/lists/tasks`

### Time Widget (`time.js`)
- Current time and date
- Timezone display
- Updates every second
- Large format for visibility

### Weather Widget (`weather.js`)
- Current weather conditions
- 4-day forecast
- Updates every 5 minutes
- Fetches from `/api/weather/current`

### Home Widget (`home.js`)
- Smart home device controls
- Room toggles
- Energy stats (solar, battery)
- Updates every minute
- Connects to Home Assistant API

### System Widget (`system.js`)
- System resource monitoring
- CPU, memory, disk usage
- Uptime display
- Service status
- Updates every 30 seconds

### Notes Widget (`notes.js`)
- Quick notes and reminders
- No auto-update (manual refresh)
- Add/edit notes inline
- Fetches from `/api/notes`

### Zoe Orb Widget (`zoe-orb.js`)
- Interactive AI assistant
- Voice recognition (speech-to-text)
- Text-to-speech responses
- Inline chat interface
- Conversation history
- No auto-update (event-driven)

## Adding New Widgets

### Option 1: Manifest-Based (Recommended)

1. Create your widget class in `core/` or `user/`
2. Add entry to `widget-manifest.json`:
```json
{
  "id": "my-widget",
  "name": "MyWidget",
  "displayName": "My Widget",
  "description": "Does something cool",
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
3. Import script in HTML:
```html
<script src="js/widgets/core/my-widget.js"></script>
```

### Option 2: User Widgets

Place custom widgets in `user/` and update manifest:

```javascript
// user/my-custom-widget.js
class MyCustomWidget extends WidgetModule {
    constructor() {
        super('my-custom', {
            version: '1.0.0',
            defaultSize: 'size-small'
        });
    }
    
    getTemplate() {
        return `<div>My Widget Content</div>`;
    }
}
```

Then add to `widget-manifest.json` and import in HTML.

## Creating Widgets

See `/docs/guides/widget-development.md` for comprehensive guide.

Quick checklist:
- [ ] Extend WidgetModule base class
- [ ] Implement getTemplate() method
- [ ] Override init() if needed
- [ ] Add update() for auto-refresh
- [ ] Register with WidgetRegistry
- [ ] Test all 4 sizes
- [ ] Handle errors gracefully
- [ ] Clean up in destroy()

## AI-Generated Widgets

Users can create widgets by describing them to Zoe:

1. Open widget library (+ button in edit mode)
2. Click "✨ AI Generate" tab
3. Describe widget: "Show my daily step count as a progress gauge"
4. AI generates widget code
5. Widget appears in marketplace
6. Install and use immediately

## Widget Marketplace

Browse community widgets:
- Filter by type (core, custom, AI-generated)
- Sort by downloads, rating, or date
- One-click install
- Rate and review widgets
- Share your creations

Access via:
- Dashboard → Edit Mode → + Button → Marketplace tab
- API: `GET /api/widgets/marketplace`

## File Naming Convention

- Core widgets: `{widget-name}.js` (lowercase, hyphenated)
- User widgets: `{widget-name}-{uuid}.js` (AI-generated include UUID)
- Widget class names: `{WidgetName}Widget` (PascalCase + Widget suffix)

## Dependencies

All widgets depend on:
- `widget-base.js` - WidgetModule base class
- `widget-system.js` - WidgetManager with manifest-based registration
- `widget-manifest.json` - Widget metadata and configuration

Load order in HTML:
1. widget-base.js (defines WidgetModule)
2. widget-system.js (defines WidgetManager + manifest loader)
3. Individual widget files (loads all widget classes)
4. Dashboard initialization (auto-registers from manifest)

**New in v2.0**: Widgets are automatically discovered and registered based on manifest!

## Versioning

Widgets use semantic versioning (major.minor.patch):
- **Major** - Breaking changes to widget API
- **Minor** - New features, backward compatible
- **Patch** - Bug fixes

Widget updates are managed through WidgetUpdater system.




