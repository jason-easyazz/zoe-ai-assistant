# Widget System Documentation

## Overview

The Zoe Widget System is a modern, expandable architecture inspired by MagicMirror²'s module system. It provides a robust framework for creating, managing, and updating widgets in the touch dashboard.

## Architecture

### Core Components

1. **WidgetRegistry** - Central registry for all widgets with version management
2. **WidgetModule** - Base class for all widgets
3. **WidgetManager** - Handles widget lifecycle and management
4. **WidgetUpdater** - Manages widget updates and notifications

### Widget Module Structure

```javascript
class TimeWidget extends WidgetModule {
    constructor() {
        super('time', {
            version: '2.0.0',
            defaultSize: 'size-large',
            updateInterval: 1000
        });
    }
    
    getTemplate() {
        return `<div class="widget-content">...</div>`;
    }
    
    update() {
        // Real-time updates
    }
    
    init(element) {
        // Setup and styling
    }
}
```

## Widget Development

### Creating a New Widget

1. **Extend WidgetModule**
   ```javascript
   class MyWidget extends WidgetModule {
       constructor() {
           super('my-widget', {
               version: '1.0.0',
               defaultSize: 'size-medium',
               updateInterval: 5000
           });
       }
   }
   ```

2. **Implement Required Methods**
   - `getTemplate()` - Returns HTML template
   - `update()` - Handles real-time updates
   - `init(element)` - Initialization logic

3. **Register Widget**
   ```javascript
   WidgetRegistry.register('my-widget', new MyWidget());
   ```

### Widget Template Structure

```html
<div class="widget-controls">
    <button class="widget-control-btn" onclick="event.stopPropagation(); cycleWidgetSize(this.closest('.widget'))" title="Change Size">📏</button>
    <button class="widget-control-btn delete" onclick="event.stopPropagation(); removeWidget(this.closest('.widget'))" title="Remove Widget">🗑️</button>
</div>
<div class="size-indicator">Medium</div>
<div class="widget-content">
    <!-- Your widget content here -->
</div>
```

### Widget Sizes

- `size-small` - Compact widget
- `size-medium` - Standard widget
- `size-large` - Large widget
- `size-xlarge` - Extra large widget

## API Endpoints

### Widget Management

- `GET /api/widgets/registry` - Get all registered widgets
- `POST /api/widgets/create` - Create new widget
- `POST /api/widgets/update/{name}` - Update specific widget
- `POST /api/widgets/test/{name}` - Test widget
- `DELETE /api/widgets/{name}` - Delete widget

### Widget Updates

- `GET /api/widgets/updates` - Check for updates
- `POST /api/widgets/update-all` - Update all widgets

## Developer Tools

### Widget Development Page

Access the widget development tools at `/developer/widgets.html`:

- **Widget Registry** - View all registered widgets
- **Widget Creator** - Create new widgets
- **Widget Testing** - Test individual widgets
- **Update Management** - Manage widget updates

### Widget Templates

Common widget templates are available in `/developer/templates.html`:

- Time Widget Template
- Weather Widget Template
- Task Widget Template
- Chart Widget Template

## Best Practices

### Widget Design

1. **Responsive Design** - Ensure widgets work on all screen sizes
2. **Performance** - Use efficient update intervals
3. **Accessibility** - Include proper ARIA labels
4. **Styling** - Use consistent design patterns

### Update Intervals

- **Real-time data** (time, weather): 1000ms
- **Frequent updates** (notifications): 5000ms
- **Occasional updates** (events, tasks): 30000ms
- **Static content**: No interval

### Error Handling

```javascript
update() {
    try {
        // Widget update logic
    } catch (error) {
        console.error(`Widget ${this.name} update failed:`, error);
        // Handle error gracefully
    }
}
```

## Widget System Features

### Automatic Updates

- **Update Checking** - Automatic checks on startup
- **Update Notifications** - Visual notifications for available updates
- **Hot Swapping** - Seamless widget updates without data loss

### Widget Persistence

- **Layout Saving** - Widget positions and sizes saved
- **State Management** - Widget state preserved across sessions
- **Configuration** - Widget settings persisted

### Testing Framework

- **Unit Testing** - Individual widget testing
- **Integration Testing** - Widget interaction testing
- **Performance Testing** - Widget performance monitoring

## Migration Guide

### From Old System

1. **Convert Templates** - Move hardcoded templates to widget classes
2. **Update References** - Replace direct DOM manipulation with widget methods
3. **Register Widgets** - Add widgets to the registry
4. **Test Thoroughly** - Ensure all functionality works

### Breaking Changes

- Widget templates now use classes instead of inline HTML
- Update functions are now methods of widget classes
- Widget creation uses WidgetManager instead of direct DOM manipulation

## Troubleshooting

### Common Issues

1. **Widget Not Updating**
   - Check update interval configuration
   - Verify update method implementation
   - Check console for errors

2. **Widget Not Displaying**
   - Verify template HTML structure
   - Check CSS classes and styling
   - Ensure proper initialization

3. **Update Failures**
   - Check API endpoints
   - Verify widget registration
   - Check network connectivity

### Debug Tools

- **Console Logging** - Widget operations logged to console
- **Developer Tools** - Use browser dev tools for debugging
- **Widget Inspector** - Built-in widget debugging tools

## Future Enhancements

### Planned Features

1. **Widget Marketplace** - Community widget sharing
2. **Widget Analytics** - Usage and performance metrics
3. **Advanced Testing** - Automated widget testing
4. **Widget Dependencies** - Complex widget relationships

### API Improvements

1. **GraphQL Support** - More efficient data fetching
2. **WebSocket Updates** - Real-time widget communication
3. **Plugin System** - Third-party widget support

## Support

For widget development support:

- **Documentation** - This file and inline comments
- **Developer Chat** - Use `/developer/chat.html`
- **Issue Tracking** - Report bugs and feature requests
- **Community** - Share widgets and get help

---

*Last Updated: $(date)*
*Version: 2.0.0*
