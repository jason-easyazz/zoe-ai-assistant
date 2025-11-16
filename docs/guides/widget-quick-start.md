# Widget System Quick Start Guide

## For Users

### Using the Widget Dashboard

1. **Access Dashboard**
   - Navigate to `http://your-zoe-server/dashboard.html`
   - Widgets load automatically

2. **Edit Mode**
   - Click "Edit Mode" button in top-right
   - Widget controls (resize, remove) appear
   - Drag widgets to rearrange
   - Click "Exit Edit" when done

3. **Add Widgets**
   - Enter Edit Mode
   - Click the floating + button (bottom-right)
   - Choose from:
     - **Core Widgets** - Built-in widgets
     - **Marketplace** - Community widgets
     - **AI Generate** - Create custom widgets with AI

4. **Resize Widgets**
   - Enter Edit Mode
   - Click 📏 button on widget
   - Cycles through: Small → Medium → Large → XLarge

5. **Remove Widgets**
   - Enter Edit Mode
   - Click 🗑️ button on widget
   - Confirm removal

6. **Customize Layout**
   - Your layout automatically saves
   - Synced across devices (same user, different device IDs)
   - Reset to default via Edit Mode → Reset

### Creating Widgets with AI

**Example 1: Simple Stat Widget**
```
"Create a widget showing my daily water intake in liters"
```

**Example 2: List Widget**
```
"Show my upcoming bills as a list with due dates"
```

**Example 3: Gauge Widget**
```
"Display my daily step count as a circular progress gauge with a goal of 10,000 steps"
```

**How it works:**
1. Open widget library (+ button)
2. Click "✨ AI Generate" tab
3. Describe your widget
4. Click "Generate Widget"
5. AI creates the widget
6. Reload page to use it

### Available Core Widgets

| Widget | Icon | Description | Default Size |
|--------|------|-------------|--------------|
| Events | 📅 | Today's calendar events | Medium |
| Tasks | ✅ | Pending tasks and todos | Small |
| Time | 🕐 | Current time and date | Large |
| Weather | 🌤️ | Weather forecast | Medium |
| Home | 🏠 | Smart home controls | Small |
| System | 💻 | System resources | Small |
| Notes | 📝 | Quick notes | Small |
| Zoe AI | 🤖 | AI assistant with voice | Large |

### Touch Dashboard

The touch version (`/touch/dashboard.html`) uses the same widgets but optimized for:
- Larger touch targets (56px minimum)
- Gesture-based interactions
- Full-screen mode support
- Voice interaction with Zoe orb

## For Developers

### Quick Widget Creation

1. **Create Widget File**
   ```bash
   # Create file in user widgets directory
   touch /home/zoe/assistant/services/zoe-ui/dist/js/widgets/user/my-widget.js
   ```

2. **Write Widget Code**
   ```javascript
   class MyWidget extends WidgetModule {
       constructor() {
           super('my-widget', {
               version: '1.0.0',
               defaultSize: 'size-small',
               updateInterval: 60000
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
               </div>
               <div class="widget-content">
                   <div>Hello World!</div>
               </div>
           `;
       }
   }
   
   if (typeof WidgetRegistry !== 'undefined') {
       WidgetRegistry.register('my-widget', new MyWidget());
   }
   ```

3. **Add to Dashboard**
   ```html
   <!-- In dashboard.html, after other widget imports -->
   <script src="js/widgets/user/my-widget.js"></script>
   ```

4. **Test**
   - Reload dashboard
   - Enter Edit Mode
   - Click + button
   - Add your widget

### Publishing to Marketplace

1. **Test Locally** - Ensure widget works on desktop and touch
2. **Add Documentation** - Include comments explaining widget
3. **Prepare Metadata**:
   - Name (unique identifier)
   - Display name (user-friendly)
   - Description
   - Icon (emoji)
   - Version

4. **Publish via API**:
   ```bash
   curl -X POST http://localhost:8000/api/widgets/marketplace \
     -H "Content-Type: application/json" \
     -d '{
       "name": "my-awesome-widget",
       "display_name": "My Awesome Widget",
       "description": "Does amazing things",
       "version": "1.0.0",
       "widget_code": "...",
       "icon": "🎯",
       "default_size": "size-small"
     }'
   ```

5. **Share Widget ID** - Users can install via marketplace

## Troubleshooting

### Widget Not Appearing
- Check browser console for errors
- Verify widget is registered: `WidgetRegistry.has('widget-name')`
- Check script tag in HTML
- Reload page

### Layout Not Saving
- Check browser console for API errors
- Verify database is writable
- Check `/app/data/zoe.db` permissions

### AI Generation Fails
- Check if AI service is running
- Verify API endpoint `/api/widgets/generate` is accessible
- Check server logs for errors

### Widget Update Issues
- Clear browser cache
- Check widget version in marketplace
- Manually reload widget via library

## Best Practices

### For Users
- ✅ Keep essential widgets visible
- ✅ Use Edit Mode sparingly (better performance)
- ✅ Try AI generation for custom needs
- ✅ Rate widgets to help community

### For Developers
- ✅ Follow security guidelines (no eval, external APIs)
- ✅ Test all 4 sizes
- ✅ Handle loading/error states
- ✅ Clean up resources in destroy()
- ✅ Document widget configuration
- ✅ Use semantic versioning

## Support

- **Full Documentation**: `/docs/guides/widget-development.md`
- **API Reference**: `http://localhost:8000/docs` (FastAPI auto-docs)
- **Examples**: `/services/zoe-ui/dist/js/widgets/core/`
- **GitHub Issues**: Report bugs and request features

## What's Next?

- Create your first custom widget
- Share widgets with the community
- Try AI generation for unique visualizations
- Build integrations with external services
- Contribute to core widget enhancements




