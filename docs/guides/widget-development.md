# Zoe Widget Development Guide

## Overview

Zoe's widget system allows you to create custom dashboard widgets that display information, provide controls, and integrate with Zoe's AI capabilities. This guide covers everything you need to know to develop, test, and publish widgets.

## Architecture

The Zoe widget system is based on proven patterns from:
- **MagicMirror²** - Module registry and lifecycle management
- **Grafana** - Plugin architecture and marketplace
- **Home Assistant** - Configuration-driven cards and visual editor

### Core Components

1. **WidgetRegistry** - Central registry for all widgets
2. **WidgetModule** - Base class all widgets extend
3. **WidgetManager** - Manages widget lifecycle in DOM
4. **WidgetUpdater** - Handles widget version updates

## Creating Your First Widget

### 1. Basic Widget Structure

Every widget is a JavaScript class that extends `WidgetModule`:

```javascript
/**
 * My Custom Widget
 * Description of what it does
 * Version: 1.0.0
 */

class MyCustomWidget extends WidgetModule {
    constructor() {
        super('my-widget', {
            version: '1.0.0',
            defaultSize: 'size-small',
            updateInterval: 60000 // Update every minute (optional)
        });
    }
    
    getTemplate() {
        return `
            <div class="widget-controls">
                <button class="widget-control-btn" onclick="event.stopPropagation(); cycleWidgetSize(this.closest('.widget'))">📏</button>
                <button class="widget-control-btn delete" onclick="event.stopPropagation(); removeWidget(this.closest('.widget'))">🗑️</button>
            </div>
            <div class="widget-header">
                <div class="widget-title">🎯 My Widget</div>
                <div class="widget-badge">0</div>
            </div>
            <div class="widget-content">
                <!-- Your widget content here -->
            </div>
        `;
    }
    
    init(element) {
        super.init(element);
        // Initialize your widget
        this.loadData();
    }
    
    update() {
        // Called automatically based on updateInterval
        this.loadData();
    }
    
    async loadData() {
        // Fetch and display data
        try {
            const response = await fetch('/api/your-endpoint');
            const data = await response.json();
            this.updateDisplay(data);
        } catch (error) {
            console.error('Failed to load data:', error);
        }
    }
    
    updateDisplay(data) {
        // Update DOM with data
    }
}

// Register widget
if (typeof WidgetRegistry !== 'undefined') {
    WidgetRegistry.register('my-widget', new MyCustomWidget());
}
```

### 2. Widget Lifecycle Methods

#### `constructor()`
Initialize widget configuration:
- `name` - Unique widget identifier
- `version` - Semantic version (major.minor.patch)
- `defaultSize` - Initial size (size-small, size-medium, size-large, size-xlarge)
- `updateInterval` - Auto-update interval in milliseconds (null for no auto-update)
- `dependencies` - Array of widget dependencies (optional)

#### `getTemplate()`
**Required method** - Returns HTML template as string.

Must include:
- Widget controls (resize, settings, delete buttons)
- Widget header with title
- Widget content area

#### `init(element)`
Called when widget is added to DOM:
- `element` - The DOM element for this widget
- Setup event listeners
- Load initial data
- Initialize UI components

#### `update()`
Called automatically based on `updateInterval`:
- Refresh widget data
- Update display
- Re-fetch from APIs

#### `destroy()`
Called when widget is removed:
- Clean up timers
- Remove event listeners
- Free resources

#### `resize(newSize)`
Called when widget size changes:
- Adjust layout for new size
- Re-render if needed

## Widget Sizes

Four standard sizes available:

- `size-small` - 1 column × 1 row (320px × 240px min)
- `size-medium` - 1 column × 2 rows (320px × 480px min)
- `size-large` - 2 columns × 1 row (640px × 240px min)
- `size-xlarge` - 2 columns × 2 rows (640px × 480px min)

Responsive grid automatically adapts to screen size.

## Data Binding

### Fetching Data from Zoe APIs

Available API endpoints:

```javascript
// Calendar events
await fetch('/api/calendar/events');

// Task lists
await fetch('/api/lists/tasks');

// Weather data
await fetch('/api/weather/current');

// Smart home states
await fetch('/api/homeassistant/states');

// System stats
await fetch('/api/system/stats');

// User memories
await fetch('/api/memories/search?query=...');

// Notifications
await fetch('/api/reminders/notifications/pending');
```

### Example: Loading and Displaying Data

```javascript
async loadData() {
    try {
        this.setLoading(true);
        
        const response = await fetch('/api/calendar/events');
        if (!response.ok) {
            throw new Error('Failed to fetch events');
        }
        
        const data = await response.json();
        this.updateDisplay(data.events || []);
        
        this.clearError();
    } catch (error) {
        this.setError('Failed to load events');
        console.error('Error:', error);
    } finally {
        this.setLoading(false);
    }
}

updateDisplay(events) {
    const content = this.element.querySelector('.widget-content');
    if (!content) return;
    
    if (events.length === 0) {
        content.innerHTML = '<div>No events</div>';
        return;
    }
    
    content.innerHTML = events.map(event => `
        <div class="event-item">
            <div>${event.title}</div>
            <div>${event.time}</div>
        </div>
    `).join('');
}
```

## Widget Communication

### Emitting Events

Widgets can communicate with each other using the event system:

```javascript
// Emit event from widget
this.emit('data-updated', { newCount: 5 });

// Listen to events
this.on('data-updated', (data) => {
    console.log('Received:', data);
});
```

### Global Events

```javascript
// Listen to global widget events
document.addEventListener('widget:refresh-all', () => {
    this.update();
});

// Emit global event
const event = new CustomEvent('widget:refresh-all');
document.dispatchEvent(event);
```

## Styling Guidelines

### Use CSS Variables

Widgets should use Zoe's design system variables:

```css
/* In your template */
<div style="color: var(--text-primary); background: var(--surface);">
    Content
</div>
```

Available variables:
- `--primary-gradient` - Main gradient (purple to cyan)
- `--surface` - Card background
- `--text-primary` - Primary text color
- `--text-secondary` - Secondary text color
- `--widget-radius` - Border radius for widgets

### Category Colors

For categorized items (events, tasks):

```javascript
// Work
background: rgba(59, 130, 246, 0.15);
color: #1e40af;

// Personal
background: rgba(147, 51, 234, 0.15);
color: #7e22ce;

// Health
background: rgba(236, 72, 153, 0.15);
color: #9d174d;

// Social
background: rgba(34, 197, 94, 0.15);
color: #15803d;
```

## Advanced Features

### Using WidgetModule Helper Methods

The base class provides useful helpers:

```javascript
// Set loading state
this.setLoading(true);

// Set error state
this.setError('Failed to load data');

// Clear error
this.clearError();

// Emit custom event
this.emit('my-event', { data: 'value' });

// Listen to events
this.on('other-event', (data) => {
    console.log(data);
});

// Get/Update configuration
const config = this.getConfig();
this.updateConfig({ newSetting: true });
```

### Widget Settings

Allow users to configure your widget:

```javascript
getTemplate() {
    return `
        <!-- ... existing template ... -->
        <button onclick="openWidgetSettings(this.closest('.widget'))">⚙️</button>
    `;
}

// Handle settings updates
updateConfig(newConfig) {
    super.updateConfig(newConfig);
    // Re-render or update based on new config
    this.update();
}
```

## AI-Generated Widgets

Users can ask Zoe to create widgets using natural language:

**User:** "Hey Zoe, create a widget showing my daily step count"

**AI generates:**
1. Determines widget type (StatWidget)
2. Identifies data source (Health API)
3. Sets update interval (1 hour)
4. Generates widget code
5. Registers widget automatically
6. Adds to dashboard

### Widget Templates for AI

Available templates:

1. **StatWidget** - Single stat with icon
2. **ChartWidget** - Time series visualizations
3. **ListWidget** - Scrollable list of items
4. **GaugeWidget** - Progress/gauge displays
5. **MediaWidget** - Images, videos, cameras
6. **IframeWidget** - Embed external content

## Security & Sandboxing

### Widget Security Rules

For marketplace acceptance, widgets must:

1. ✅ **DO:**
   - Use approved API endpoints only
   - Handle errors gracefully
   - Respect user privacy
   - Clean up resources on destroy
   - Use semantic versioning

2. ❌ **DON'T:**
   - Access `window` or `document` directly (use `this.element`)
   - Use `eval()` or `Function()` constructor
   - Make external API calls to unknown domains
   - Store sensitive data in localStorage
   - Access other widgets' DOM elements

### Safe Data Access

```javascript
// ✅ GOOD - Use widget element
const content = this.element.querySelector('.widget-content');

// ❌ BAD - Direct document access
const content = document.querySelector('.widget-content');

// ✅ GOOD - Approved API
await fetch('/api/calendar/events');

// ❌ BAD - External API
await fetch('https://random-external-api.com/data');
```

## Testing Your Widget

### Local Development

1. Create widget file in `/js/widgets/user/my-widget.js`
2. Add to dashboard HTML:
```html
<script src="js/widgets/user/my-widget.js"></script>
```
3. Refresh dashboard
4. Add widget from library

### Testing Checklist

- [ ] Widget loads without errors
- [ ] Data displays correctly
- [ ] Resize works (all 4 sizes)
- [ ] Auto-update works (if configured)
- [ ] Drag and drop works
- [ ] Settings work (if applicable)
- [ ] Errors handled gracefully
- [ ] Widget cleans up on removal
- [ ] Works on both desktop and touch

## Publishing to Marketplace

### 1. Prepare Widget

Ensure your widget:
- Has proper documentation (comments)
- Includes version number
- Has icon emoji
- Includes description
- Lists required permissions
- Specifies data sources

### 2. Submit via API

```javascript
const response = await fetch('/api/widgets/marketplace', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
        name: 'my-awesome-widget',
        display_name: 'My Awesome Widget',
        description: 'Does amazing things',
        version: '1.0.0',
        widget_code: widgetCodeString,
        widget_type: 'custom',
        icon: '🎯',
        default_size: 'size-small',
        update_interval: 60000,
        data_sources: ['/api/calendar/events'],
        permissions: ['calendar']
    })
});
```

### 3. Widget Review

Submitted widgets are reviewed for:
- Security compliance
- Code quality
- Performance
- User experience
- Documentation

## Widget Marketplace Integration

### Browse Marketplace

Users can:
- Search widgets by name/description
- Filter by type (core, custom, AI-generated)
- Sort by downloads, rating, date
- View ratings and reviews
- One-click install

### Rating System

Users can rate widgets 1-5 stars and leave reviews:

```javascript
await fetch('/api/widgets/rate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
        widget_id: 'uuid-here',
        rating: 5,
        review: 'Excellent widget!'
    })
});
```

## Best Practices

### Performance

1. **Lazy Load Data** - Don't load data until needed
2. **Debounce Updates** - Avoid excessive API calls
3. **Cache Results** - Store data when appropriate
4. **Clean Up** - Always clear timers and listeners

### User Experience

1. **Loading States** - Show spinners during data fetch
2. **Error States** - Display friendly error messages
3. **Empty States** - Handle no-data gracefully
4. **Responsive** - Work well at all sizes

### Code Quality

1. **Use Comments** - Document complex logic
2. **Handle Errors** - Try/catch on all async operations
3. **Validate Data** - Check API responses
4. **Test Thoroughly** - All sizes and edge cases

## Example Widgets

### Simple Stat Widget

```javascript
class StepCountWidget extends WidgetModule {
    constructor() {
        super('step-count', {
            version: '1.0.0',
            defaultSize: 'size-small',
            updateInterval: 3600000 // Update hourly
        });
    }
    
    getTemplate() {
        return `
            <div class="widget-controls">
                <button class="widget-control-btn" onclick="event.stopPropagation(); cycleWidgetSize(this.closest('.widget'))">📏</button>
                <button class="widget-control-btn delete" onclick="event.stopPropagation(); removeWidget(this.closest('.widget'))">🗑️</button>
            </div>
            <div class="widget-header">
                <div class="widget-title">🚶 Steps Today</div>
            </div>
            <div class="widget-content" style="text-align: center; padding: 20px;">
                <div style="font-size: 48px; font-weight: bold;" id="stepCount">--</div>
                <div style="font-size: 14px; color: #666;">of 10,000 goal</div>
            </div>
        `;
    }
    
    async loadData() {
        const response = await fetch('/api/health/steps/today');
        const data = await response.json();
        
        const stepCount = this.element.querySelector('#stepCount');
        if (stepCount) {
            stepCount.textContent = data.steps.toLocaleString();
        }
    }
}

WidgetRegistry.register('step-count', new StepCountWidget());
```

### List Widget

```javascript
class TodoListWidget extends WidgetModule {
    constructor() {
        super('todo-list', {
            version: '1.0.0',
            defaultSize: 'size-medium'
        });
    }
    
    getTemplate() {
        return `
            <div class="widget-controls">
                <button class="widget-control-btn" onclick="event.stopPropagation(); cycleWidgetSize(this.closest('.widget'))">📏</button>
                <button class="widget-control-btn delete" onclick="event.stopPropagation(); removeWidget(this.closest('.widget'))">🗑️</button>
            </div>
            <div class="widget-header">
                <div class="widget-title">✅ Todos</div>
                <div class="widget-badge" id="todoCount">0</div>
            </div>
            <div class="widget-content" id="todoList"></div>
        `;
    }
    
    async loadData() {
        const response = await fetch('/api/lists/tasks');
        const data = await response.json();
        
        const list = this.element.querySelector('#todoList');
        const badge = this.element.querySelector('#todoCount');
        
        if (badge) badge.textContent = data.tasks.length;
        
        if (list) {
            list.innerHTML = data.tasks.map(task => `
                <div class="task-item">
                    <input type="checkbox" ${task.completed ? 'checked' : ''}>
                    <span>${task.text}</span>
                </div>
            `).join('');
        }
    }
}

WidgetRegistry.register('todo-list', new TodoListWidget());
```

## API Reference

### WidgetModule Methods

| Method | Description | Parameters |
|--------|-------------|------------|
| `init(element)` | Initialize widget | `element` - DOM element |
| `update()` | Refresh widget data | None |
| `destroy()` | Clean up widget | None |
| `resize(size)` | Change widget size | `size` - Size class name |
| `getTemplate()` | Return HTML template | None |
| `setLoading(bool)` | Set loading state | `bool` - Loading status |
| `setError(msg)` | Set error state | `msg` - Error message |
| `clearError()` | Clear error state | None |
| `emit(event, data)` | Emit custom event | `event` - Event name, `data` - Event data |
| `on(event, handler)` | Listen to events | `event` - Event name, `handler` - Callback function |

### WidgetRegistry Methods

| Method | Description |
|--------|-------------|
| `register(name, widget)` | Register a widget |
| `get(name)` | Get widget instance |
| `getAll()` | Get all widget names |
| `has(name)` | Check if widget exists |
| `update(name, newWidget)` | Update widget version |

### WidgetManager Methods

| Method | Description |
|--------|-------------|
| `createWidget(type, grid)` | Create widget in grid |
| `removeWidget(element)` | Remove widget |
| `resizeWidget(element, size)` | Resize widget |
| `getAllActive()` | Get all active widgets |
| `updateAll()` | Update all widgets |

## Troubleshooting

### Widget Doesn't Load

1. Check console for errors
2. Verify widget is registered: `WidgetRegistry.has('my-widget')`
3. Check script is loaded in HTML
4. Verify `getTemplate()` returns valid HTML

### Data Not Displaying

1. Check API endpoint is correct
2. Verify response format
3. Check for CORS errors
4. Test API endpoint directly
5. Add error handling with try/catch

### Widget Crashes Dashboard

1. Check for infinite loops
2. Verify all async operations have error handling
3. Check for memory leaks (timers not cleared)
4. Test destroy() method cleans up properly

## Support

- **Documentation:** `/docs/guides/`
- **API Reference:** `/docs/api/`
- **Examples:** `/js/widgets/core/`
- **Issues:** GitHub Issues

## Changelog

### Version 1.0.0 (2025-10-12)
- Initial widget system release
- Core widgets: events, tasks, time, weather, home, system, notes, zoe-orb
- Widget marketplace
- AI widget generation
- Layout persistence per user per device




