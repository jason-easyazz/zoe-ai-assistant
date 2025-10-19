# Zoe Widget Directory

This directory contains all widget modules for the Zoe dashboard system.

## Structure

```
widgets/
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

## User Widgets

Place custom widgets in the `user/` directory:

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

WidgetRegistry.register('my-custom', new MyCustomWidget());
```

Then import in dashboard HTML:
```html
<script src="js/widgets/user/my-custom-widget.js"></script>
```

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
- `widget-system.js` - WidgetRegistry, WidgetManager, WidgetUpdater

Load order in HTML:
1. widget-base.js (defines WidgetModule)
2. widget-system.js (defines registry/manager)
3. Individual widget files
4. Dashboard initialization

## Versioning

Widgets use semantic versioning (major.minor.patch):
- **Major** - Breaking changes to widget API
- **Minor** - New features, backward compatible
- **Patch** - Bug fixes

Widget updates are managed through WidgetUpdater system.




