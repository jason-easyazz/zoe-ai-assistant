# MCP-Only Architecture for Zoe Modules

## ✅ Current State (January 22, 2026)

The Zoe module system now follows a **pure MCP architecture** with no REST fallbacks.

## Architecture Overview

```
┌─────────────┐
│   Zoe AI    │  ← Natural language → MCP tools
└─────────────┘
       ↓
┌─────────────┐
│ UI Widgets  │  ← User interactions → MCP tools
└─────────────┘
       ↓
┌─────────────────┐
│ zoe-mcp-server  │  ← Central orchestration
└─────────────────┘
       ↓
┌─────────────┐
│ zoe-music   │  ← Module exposes MCP tools
└─────────────┘
```

## Key Principles

### 1. Single Interface
**Everything uses MCP** - no dual REST/MCP interfaces.

- **AI commands**: `"play some Beatles"` → MCP tool `music_play_song`
- **UI widgets**: Click play button → MCPClient → `music_play_song`
- **Same tools, same interface**

### 2. Module Self-Containment
Each module contains:
- **MCP Tools** (`main.py`) - Backend logic exposed as tools
- **UI Widgets** (`static/js/*.js`) - Frontend components
- **Widget Manifest** (`static/manifest.json`) - Widget metadata
- **Shared State** (`static/js/music-state.js`) - Module-level state manager

### 3. No REST Fallbacks
Widgets **require MCP** to function. If MCP isn't available, the module doesn't work.

This ensures:
- ✅ Single source of truth
- ✅ Consistent behavior (AI and UI use same code paths)
- ✅ Easier testing and debugging
- ✅ Clear error messages when misconfigured

## Implementation Details

### Widget → MCP Flow

```javascript
// In music widget (e.g., search.js)
class MusicSearchWidget {
    async handlePlayClick(trackId) {
        // Use shared MCP-based state manager
        await MusicState.play(trackId);
    }
}

// In music-state.js
class MCPMusicStateManager {
    async init() {
        // Initialize MCP client
        this.mcp = new MCPClient({
            mcpServerUrl: '/api/mcp'  // Routes through Nginx
        });
        await this.mcp.init();
        
        // Fail hard if MCP not available
        if (!this.mcp.isModuleEnabled('music')) {
            throw new Error('MCP required for music module');
        }
    }
    
    async play(trackId) {
        // Pure MCP - no REST fallback
        const result = await this.mcp.callTool('music_play_song', {
            track_id: trackId,
            user_id: this.getSessionId()
        });
        
        // Handle result...
    }
}
```

### Network Routing

All MCP requests route through Nginx:

```nginx
# In nginx.conf
location /api/mcp/ {
    proxy_pass http://zoe-mcp-server:8003/;
    # ... proxy headers ...
}
```

This ensures:
- No CORS issues
- Same-origin policy satisfied
- HTTPS works correctly
- Consistent auth handling

## Testing MCP Connectivity

```bash
# 1. Check MCP server is running
docker ps --filter "name=zoe-mcp-server"

# 2. Test MCP tools endpoint
curl -X POST http://localhost/api/mcp/tools/list \
  -H "Content-Type: application/json" \
  -d '{}'

# 3. Test music module manifest
curl http://localhost/modules/music/widget/manifest

# 4. Test a music tool
curl -X POST http://localhost/api/mcp/tools/call \
  -H "Content-Type: application/json" \
  -d '{
    "name": "music_search",
    "arguments": {
      "query": "Beatles",
      "limit": 5
    }
  }'
```

## Browser Cache Issues

When updating module code, browsers may cache old JavaScript files. Solutions:

### Automatic: Cache-Busting
Module loader adds timestamps to URLs:
```javascript
const scriptUrl = `/modules/music/static/js/player.js?v=${Date.now()}`;
```

### Manual: Clear Cache Page
Navigate to: `https://zoe.the411.life/clear-cache.html`

This page will:
- Unregister service workers
- Clear all caches (HTTP, Cache API, localStorage, IndexedDB)
- Force reload

### Nuclear: DevTools
1. F12 → Application tab
2. Clear storage → Check all boxes
3. Clear site data
4. Ctrl+Shift+R (hard refresh)

## Benefits of MCP-Only

### For Developers
- **Single code path** - Widget bugs = AI bugs, fix once
- **Clear contracts** - MCP tool schemas define behavior
- **Easy debugging** - All traffic through MCP server (single logging point)
- **Type safety** - MCP tools have defined input/output schemas

### For Users
- **Consistent behavior** - AI and UI do the same thing
- **Better errors** - Clear "MCP not available" vs. silent fallback failures
- **Predictable** - What works in chat works in widgets

### For Architecture
- **Modularity** - Modules are truly independent
- **Discoverability** - New modules auto-register, UI auto-discovers
- **Extensibility** - Community modules follow same pattern
- **Future-proof** - MCP is an open standard, not Zoe-specific

## Trade-offs

### Pros ✅
- Clean architecture
- Single source of truth
- Easier to reason about
- Better for long-term maintenance

### Cons ⚠️
- Requires MCP server to be running
- Extra network hop (UI → MCP Server → Module)
- Slight latency increase (~5-10ms)
- No graceful degradation

**Decision**: We chose purity over pragmatism. The architectural benefits outweigh the cons.

## Future Modules

When building new modules, follow this pattern:

1. **Expose functionality as MCP tools** (not REST endpoints)
2. **Widget state managers use MCPClient** (not fetch)
3. **Fail hard if MCP unavailable** (no fallbacks)
4. **Test through MCP** (not direct REST)

See: `docs/modules/BUILDING_MODULES.md` for full guide.

---

**Last Updated**: January 22, 2026  
**Status**: ✅ Implemented and enforced  
**Related**: `docs/modules/MUSIC_MODULE_EXECUTION_PLAN.md`, `docs/modules/BUILDING_MODULES.md`
