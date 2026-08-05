# Self-Contained Modules: Complete Guide

**Date**: 2026-01-22  
**Status**: ✅ **PRODUCTION READY**  
**Architecture Version**: 2.0

---

## 🎯 What Are Self-Contained Modules?

**Self-contained modules** are complete, independent packages that include:
- ✅ **Backend** (FastAPI + MCP tools)
- ✅ **Frontend** (JavaScript widgets + CSS)
- ✅ **Intents** (Voice/text command handlers)
- ✅ **Documentation** (README, examples)
- ✅ **Configuration** (Docker, requirements)

**One module = One complete feature**

---

## 🏗️ Module Structure

```
modules/{module-name}/          # Module root
├── main.py                     # FastAPI app + MCP tools
├── services/                   # Backend logic
│   ├── __init__.py
│   ├── music/                  # Domain services
│   │   ├── youtube_music.py
│   │   ├── media_controller.py
│   │   └── ...
│   └── platform.py
├── intents/                    # Intent system
│   ├── music.yaml              # Intent definitions
│   └── handlers.py             # Intent handlers
├── static/                     # 🆕 Frontend assets
│   ├── manifest.json           # Widget metadata
│   ├── js/                     # JavaScript files
│   │   ├── music-state.js      # Shared state
│   │   ├── player.js           # Player widget
│   │   ├── search.js           # Search widget
│   │   ├── queue.js            # Queue widget
│   │   └── suggestions.js      # Suggestions widget
│   ├── css/                    # Stylesheets (optional)
│   │   ├── player.css
│   │   └── common.css
│   └── icons/                  # Icons/images (optional)
│       └── icon.svg
├── db/                         # Database schemas
│   └── schema.sql
├── docker-compose.module.yml   # Docker config
├── Dockerfile                  # Container definition
├── requirements.txt            # Python dependencies
├── README.md                   # Module documentation
└── .gitignore                  # Git ignore rules
```

---

## 📋 manifest.json: Widget Metadata

The manifest defines what widgets your module provides:

```json
{
  "module": "zoe-example-music",
  "version": "1.0.0",
  "name": "Music Module",
  "description": "Complete music system with multiple providers",
  "author": "Your Name",
  
  "widgets": [
    {
      "id": "music-player",
      "name": "Music Player",
      "description": "Full-featured music player",
      "script": "/static/js/player.js",
      "styles": "/static/css/player.css",
      "icon": "🎵",
      "defaultSize": { "w": 3, "h": 4 },
      "minSize": { "w": 2, "h": 3 },
      "maxSize": { "w": 6, "h": 8 },
      "category": "media",
      "dependencies": ["music-state"]
    }
  ],
  
  "dependencies": [
    {
      "name": "music-state",
      "script": "/static/js/music-state.js",
      "description": "Shared music state management"
    }
  ],
  
  "permissions": ["audio", "storage"],
  
  "mcp_tools": [
    "music_search",
    "music_play_song",
    "music_pause",
    "music_resume"
  ]
}
```

### Widget Fields

| Field | Required | Description |
|-------|----------|-------------|
| `id` | ✅ Yes | Unique widget identifier |
| `name` | ✅ Yes | Display name |
| `description` | ✅ Yes | Short description |
| `script` | ✅ Yes | Path to JS file (relative to module) |
| `styles` | ❌ No | Path to CSS file |
| `icon` | ❌ No | Emoji or icon URL |
| `defaultSize` | ❌ No | Default grid size `{w, h}` |
| `minSize` | ❌ No | Minimum grid size |
| `maxSize` | ❌ No | Maximum grid size |
| `category` | ❌ No | Widget category (media, productivity, etc.) |
| `dependencies` | ❌ No | List of required shared scripts |

---

## 🔧 Backend: Serving Static Files

Update your `main.py` to serve static files:

```python
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path
import logging

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Your Module",
    description="Module description",
    version="1.0.0"
)

# Mount static files for widget UI
static_dir = Path(__file__).parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    logger.info(f"📁 Serving static files from {static_dir}")
else:
    logger.warning(f"⚠️  Static directory not found: {static_dir}")

@app.get("/widget/manifest")
async def get_widget_manifest():
    """Get widget manifest for UI integration"""
    manifest_path = Path(__file__).parent / "static" / "manifest.json"
    if manifest_path.exists():
        return FileResponse(manifest_path, media_type="application/json")
    else:
        raise HTTPException(status_code=404, detail="Widget manifest not found")

@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "module": "your-module",
        "version": "1.0.0"
    }
```

---

## 💻 Frontend: Widget Implementation

### Widget Structure

```javascript
// static/js/your-widget.js

/**
 * Your Widget
 * ===========
 * 
 * Description of what your widget does.
 */

class YourWidget {
    constructor(options = {}) {
        this.options = options;
        this.mcp = null;
        this.container = null;
    }
    
    /**
     * Initialize widget
     */
    async init(container) {
        console.log('🎵 YourWidget: Initializing...');
        this.container = container;
        
        // Get MCP client
        this.mcp = new MCPClient();
        await this.mcp.init();
        
        // Render UI
        this.render();
        
        // Bind events
        this.bindEvents();
        
        // Load initial data
        await this.loadData();
        
        console.log('✅ YourWidget: Ready');
    }
    
    /**
     * Render UI
     */
    render() {
        this.container.innerHTML = `
            <div class="your-widget">
                <h2>Widget Title</h2>
                <button id="action-btn">Action</button>
                <div id="content"></div>
            </div>
        `;
    }
    
    /**
     * Bind event handlers
     */
    bindEvents() {
        const btn = this.container.querySelector('#action-btn');
        btn.addEventListener('click', () => this.handleAction());
    }
    
    /**
     * Load data from module via MCP
     */
    async loadData() {
        try {
            const result = await this.mcp.callTool('your_tool_name', {
                user_id: this.getSessionId()
            });
            
            this.renderData(result);
        } catch (error) {
            console.error('Failed to load data:', error);
        }
    }
    
    /**
     * Handle action
     */
    async handleAction() {
        const result = await this.mcp.callTool('your_action_tool', {
            param: 'value',
            user_id: this.getSessionId()
        });
        
        console.log('Action result:', result);
    }
    
    /**
     * Render data
     */
    renderData(data) {
        const content = this.container.querySelector('#content');
        content.innerHTML = `<pre>${JSON.stringify(data, null, 2)}</pre>`;
    }
    
    /**
     * Get session ID
     */
    getSessionId() {
        if (window.zoeAuth && window.zoeAuth.getSession) {
            return window.zoeAuth.getSession();
        }
        return 'default';
    }
    
    /**
     * Cleanup
     */
    destroy() {
        // Remove event listeners, clear timers, etc.
        console.log('🧹 YourWidget: Cleaned up');
    }
}

// Auto-register widget
if (window.WidgetRegistry) {
    window.WidgetRegistry.register(YourWidget, {
        id: 'your-widget',
        name: 'Your Widget',
        description: 'Widget description',
        module: 'your-module',
        icon: '🎵'
    });
}

// Export
if (typeof window !== 'undefined') {
    window.YourWidget = YourWidget;
}
```

---

## 🔄 How It Works: Discovery Flow

```
1. User Opens UI (music.html, dashboard.html)
   ↓
2. UI Initializes ModuleWidgetLoader
   ↓
3. ModuleWidgetLoader queries MCP: "What modules are enabled?"
   ↓
4. For each enabled module:
   - Fetch: http://localhost:{port}/widget/manifest
   - Parse manifest.json
   - Register widget metadata in WidgetRegistry
   ↓
5. User Adds Widget to Page
   ↓
6. WidgetRegistry loads widget script dynamically
   ↓
7. Widget script executes and auto-registers
   ↓
8. Widget.init(container) is called
   ↓
9. Widget uses MCP client to call module tools
   ↓
10. Module responds with data/actions
```

---

## 🚀 Creating a New Module with Widgets

### Step 1: Create the Module

There is no copyable scaffold: `modules/omnigent` is container-only (no `main.py`,
`requirements.txt`, `services/` or `intents/`), its compose file still declares the
`omnigent` service and the `zoe-omnigent` container, and the widget steps below never
correct those — so a copy fails `tools/validate_module.py` and can collide with the
live Omnigent deployment. Build the five required files from the templates in
[BUILDING_MODULES.md](BUILDING_MODULES.md), then continue here for the widget half.

```bash
mkdir -p modules/zoe-your-feature/{services,intents}
cd modules/zoe-your-feature
```

### Step 2: Create Static Directory

```bash
mkdir -p static/{js,css,icons}
```

### Step 3: Create Manifest

```bash
cat > static/manifest.json <<EOF
{
  "module": "zoe-your-feature",
  "version": "1.0.0",
  "name": "Your Feature",
  "description": "Description here",
  "author": "Your Name",
  "widgets": [],
  "dependencies": [],
  "permissions": [],
  "mcp_tools": []
}
EOF
```

### Step 4: Create Widget

```javascript
// static/js/your-widget.js
class YourWidget {
    async init(container) {
        this.mcp = new MCPClient();
        await this.mcp.init();
        container.innerHTML = '<h1>Your Widget</h1>';
    }
}

if (window.WidgetRegistry) {
    window.WidgetRegistry.register(YourWidget, {
        id: 'your-widget',
        name: 'Your Widget',
        module: 'your-feature'
    });
}
```

### Step 5: Add to Manifest

```json
{
  "widgets": [
    {
      "id": "your-widget",
      "name": "Your Widget",
      "script": "/static/js/your-widget.js",
      "icon": "✨"
    }
  ]
}
```

### Step 6: Update main.py

```python
# Add static file serving (see Backend section above)
# Add /widget/manifest endpoint
```

### Step 7: Test

```bash
# Build and start module
docker compose -f docker-compose.module.yml up -d

# Check manifest
curl http://localhost:{PORT}/widget/manifest

# Check static files
curl http://localhost:{PORT}/static/js/your-widget.js

# Open UI and check browser console for widget discovery
```

---

## 📊 Benefits of Self-Contained Modules

### For Users
- ✅ **One-Click Install**: Enable module → widgets appear
- ✅ **One-Click Uninstall**: Disable module → widgets disappear
- ✅ **No Conflicts**: Each module isolated
- ✅ **Easy Updates**: Update module → UI updates automatically

### For Developers
- ✅ **Complete Package**: Backend + Frontend + Intents in one place
- ✅ **Easy Testing**: Test entire module in isolation
- ✅ **Version Sync**: UI and backend always compatible
- ✅ **Distribution**: `git clone` gets everything
- ✅ **No Core Changes**: Never touch zoe-core or zoe-ui

### For System
- ✅ **True Modularity**: Remove module = remove ALL code
- ✅ **Hot Loading**: Enable/disable without restart
- ✅ **Dynamic Discovery**: UI adapts to available modules
- ✅ **Marketplace Ready**: Can build module marketplace

---

## 🎨 Advanced Widget Features

### State Management

```javascript
class StatefulWidget {
    constructor() {
        this.state = {
            data: null,
            loading: false,
            error: null
        };
        this.listeners = [];
    }
    
    setState(updates) {
        this.state = { ...this.state, ...updates };
        this.notifyListeners();
    }
    
    subscribe(callback) {
        this.listeners.push(callback);
    }
    
    notifyListeners() {
        this.listeners.forEach(cb => cb(this.state));
    }
}
```

### WebSocket Updates

```javascript
class RealtimeWidget {
    async init(container) {
        this.setupWebSocket();
    }
    
    setupWebSocket() {
        const ws = new WebSocket('ws://localhost:8000/api/ws/device');
        
        ws.onmessage = (event) => {
            const data = JSON.parse(event.data);
            if (data.type === 'your_event') {
                this.handleUpdate(data);
            }
        };
    }
}
```

### Widget Communication

```javascript
// Widget A
window.dispatchEvent(new CustomEvent('widget:action', {
    detail: { action: 'play', trackId: '123' }
}));

// Widget B
window.addEventListener('widget:action', (event) => {
    console.log('Received:', event.detail);
});
```

---

## 🧪 Testing Your Module

### Manual Testing

```bash
# 1. Build module
cd modules/zoe-your-module
docker build -t zoe-your-module .

# 2. Run module
docker run -p 8200:8200 zoe-your-module

# 3. Test manifest
curl http://localhost:8200/widget/manifest | jq

# 4. Test static files
curl http://localhost:8200/static/js/your-widget.js

# 5. Test MCP tools
curl -X POST http://localhost:8200/tools/your_tool \
  -H "Content-Type: application/json" \
  -d '{"user_id":"test"}'
```

### Automated Testing

```python
# tests/test_widgets.py
import pytest
from fastapi.testclient import TestClient
from main import app

def test_manifest():
    client = TestClient(app)
    response = client.get("/widget/manifest")
    assert response.status_code == 200
    data = response.json()
    assert "widgets" in data
    assert len(data["widgets"]) > 0

def test_static_files():
    client = TestClient(app)
    response = client.get("/static/js/your-widget.js")
    assert response.status_code == 200
```

---

## 📝 Best Practices

### DO ✅

- **Use MCP Client**: Always interact with module via MCP tools
- **Error Handling**: Gracefully handle missing modules/tools
- **Responsive UI**: Widgets work on mobile and desktop
- **Clean Code**: Follow JavaScript best practices
- **Documentation**: Comment your widget code
- **Version Control**: Semantic versioning for manifest

### DON'T ❌

- **Hardcode URLs**: Use MCP client, not direct HTTP calls
- **Global State**: Keep state in widget instances
- **Block UI**: Use async/await for all operations
- **Large Files**: Keep widgets lightweight (<100kb)
- **Tight Coupling**: Widgets should be independent

---

## 🔍 Debugging

### Check Widget Discovery

```javascript
// Open browser console
console.log('Available modules:', window.moduleWidgetLoader.getEnabledModules());
console.log('Discovered widgets:', window.moduleWidgetLoader.getAvailableWidgets());
console.log('Registered widgets:', window.WidgetRegistry.getAll());
```

### Check Widget Loading

```javascript
// Load widget manually
const widget = await window.moduleWidgetLoader.loadWidget('music-player');
console.log('Widget loaded:', widget);
```

### Check MCP Connection

```javascript
// Test MCP tools
const mcp = new MCPClient();
await mcp.init();
console.log('MCP tools:', Array.from(mcp.tools.keys()));

const result = await mcp.callTool('music_search', {
    query: 'test',
    user_id: 'test'
});
console.log('Tool result:', result);
```

---

## 🚀 Example: Complete Calendar Module

See `docs/modules/examples/CALENDAR_MODULE_EXAMPLE.md` for a complete working example.

---

## 📚 Additional Resources

- **MCP Client API**: `docs/modules/MCP_CLIENT_API.md`
- **Widget Registry API**: `docs/modules/WIDGET_REGISTRY_API.md`
- **Module Template**: `modules/module-template/`
- **Module Source**: `modules/omnigent/` (the only live module; `modules/zoe-music` was deleted 2026-08-05 — `git log --all -- modules/zoe-music`)

---

## 🎉 Success!

You now have everything you need to build self-contained modules with beautiful, functional widgets that integrate seamlessly with Zoe!

**Next Steps**:
1. Copy the music module as a template
2. Modify for your feature
3. Test the manifest and static files
4. Submit as a community module!

**Questions?** Check the examples or ask in the community.

---

**Built with ❤️ for the Zoe modular architecture**
