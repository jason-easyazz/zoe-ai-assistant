# MCP Migration Plan - Adopt Official Implementations

## Current State ✅
- **Home Assistant:** 2025.10.4 (has official MCP support!)
- **N8N:** Running n8nio/n8n
- **Our Bridges:** homeassistant-mcp-bridge, n8n-mcp-bridge (both healthy)
- **Matrix:** Just added matrix-nio integration to zoe-mcp-server

## Discovery from Claude's Research
Official MCP integrations exist for all three services we're integrating:
1. Home Assistant has **built-in MCP server** (since 2025.2)
2. N8N has **native MCP nodes** (official)
3. Matrix has **community TypeScript server** (mjknowles/matrix-mcp-server)

## Migration Strategy

### Phase 1: Enable Home Assistant Official MCP Server ⚡ IMMEDIATE

**Current:** zoe-mcp-server → homeassistant-mcp-bridge → HA REST API  
**Target:** zoe-mcp-server → HA Built-in MCP Server

**Steps:**
1. Enable HA MCP Server in configuration.yaml:
```yaml
# Add to /config/configuration.yaml
mcp_server:
  enabled: true
  api_password: !secret mcp_password  # or use existing HA token
```

2. Restart Home Assistant
3. Test MCP endpoint: `http://homeassistant:8123/api/mcp`
4. Update zoe-mcp-server to connect as MCP client
5. Deprecate homeassistant-mcp-bridge (keep for fallback initially)

**Benefits:**
- Official support
- Better maintained
- More HA features exposed
- Reduced latency (one less hop)

**Risks:** Low (official HA feature)

### Phase 2: Deploy Matrix MCP Server 🔄 SHORT-TERM

**Current:** zoe-mcp-server has matrix-nio client (just added)  
**Target:** zoe-mcp-server → matrix-mcp-server → Matrix

**Steps:**
1. Add matrix-mcp-server to docker-compose.yml:
```yaml
matrix-mcp-server:
  image: ghcr.io/mjknowles/matrix-mcp-server:latest
  container_name: matrix-mcp-server
  ports:
    - "3001:3000"
  environment:
    - MATRIX_HOMESERVER_URL=${MATRIX_HOMESERVER}
    - MATRIX_ACCESS_TOKEN=${MATRIX_TOKEN}
  networks:
    - zoe-network
```

2. Configure OAuth 2.0 / access token
3. Test endpoint: `http://matrix-mcp-server:3000/mcp`
4. Update zoe-mcp-server Matrix tools to proxy to it
5. Keep matrix-nio as fallback

**Benefits:**
- HTTP-based (easier integration)
- OAuth 2.0 support
- 15 production-ready tools
- TypeScript (faster for I/O)

**Risks:** Medium (community project, external dependency)

### Phase 3: Enable N8N Native MCP 🔄 MEDIUM-TERM

**Current:** zoe-mcp-server → n8n-mcp-bridge → N8N REST API  
**Target:** zoe-mcp-server → N8N Native MCP nodes

**Steps:**
1. Update N8N environment:
```yaml
# docker-compose.yml
zoe-n8n:
  environment:
    - N8N_COMMUNITY_PACKAGES_ALLOW_TOOL_USAGE=true
```

2. Install n8n-nodes-mcp community package
3. Create workflows using MCP Client Tool node
4. Expose MCP Server Trigger endpoint
5. Connect zoe-mcp-server to n8n's MCP interface

**Benefits:**
- Native n8n integration
- 541+ workflow nodes
- Visual workflow builder
- Community support

**Risks:** Medium (requires n8n reconfiguration, workflow changes)

## Architecture Evolution

### Current (Bridge Pattern):
```
┌─────────────────┐
│  zoe-mcp-server │
│   (52 tools)    │
└────────┬────────┘
         │
    ┌────┴─────┬──────────┬─────────┐
    ▼          ▼          ▼         ▼
  HA-Bridge  N8N-Bridge  Matrix  Custom
  (FastAPI)  (FastAPI)   (nio)   (SQLite)
    │          │          │         │
    ▼          ▼          ▼         ▼
   HA         N8N      Matrix    Memory/
  REST       REST       API      People/
  API        API                 Lists
```

### Target (Hybrid Pattern):
```
┌─────────────────┐
│  zoe-mcp-server │
│  (MCP Client +  │
│  Custom Tools)  │
└────────┬────────┘
         │
    ┌────┴─────┬──────────┬──────────┐
    ▼          ▼          ▼          ▼
  HA MCP    N8N MCP   Matrix MCP  Custom
 (Built-in) (Native)  (TypeScript) (Python)
    │          │          │          │
    ▼          ▼          ▼          ▼
   HA         N8N      Matrix    Memory/
 (Core)    (Workflows)  (Rooms)  People/
                                  Lists/
                                  Planning
```

## What Stays in Zoe MCP Server

Even after migration, zoe-mcp-server remains essential for:

**Custom Zoe Tools:**
- ✅ Memory search/storage (mem-agent integration)
- ✅ People tracking (relationships, interactions)
- ✅ Lists management (shopping, tasks, todos)
- ✅ Calendar (Zoe-specific scheduling)
- ✅ Planning tools (task decomposition, progress tracking)
- ✅ Developer tools (roadmap, tasks)

**MCP Client Aggregation:**
- Connects to multiple MCP servers
- Provides unified tool interface
- Handles authentication/routing
- Custom security policies

## Implementation Timeline

### Week 1: Home Assistant
- [ ] Enable HA MCP server
- [ ] Test HA MCP endpoints
- [ ] Implement MCP client in zoe-mcp-server
- [ ] Parallel testing (bridge vs official)
- [ ] Cutover to official

### Week 2: Matrix
- [ ] Deploy matrix-mcp-server container
- [ ] Configure OAuth/tokens
- [ ] Test Matrix MCP endpoints
- [ ] Update zoe-mcp-server Matrix tools
- [ ] Validate message sending/room management

### Week 3: N8N
- [ ] Enable n8n MCP support
- [ ] Install community packages
- [ ] Create test workflows
- [ ] Connect zoe-mcp-server to n8n MCP
- [ ] Migrate existing n8n automations

### Week 4: Cleanup
- [ ] Remove old bridge services
- [ ] Update documentation
- [ ] Performance testing
- [ ] Security audit

## Success Criteria

✅ **Home Assistant:**
- Can control devices via HA MCP server
- Automation triggers work
- Scenes activate properly
- Latency < old bridge

✅ **Matrix:**
- Can send messages
- Can create/join rooms
- Can read messages
- OAuth authentication works

✅ **N8N:**
- Workflows accessible via MCP
- Can trigger workflows
- Execution monitoring works
- 541+ nodes available

✅ **Overall:**
- All 52 tools functional
- Quick tests pass (5/5)
- Full tests improve (>80%)
- System stability maintained

## Rollback Plan

Each phase has independent rollback:
- **HA:** Re-enable homeassistant-mcp-bridge
- **Matrix:** Revert to matrix-nio client
- **N8N:** Re-enable n8n-mcp-bridge

Keep old bridges in docker-compose (commented out) for 1 month after migration.

## Next Steps

1. ✅ **Documented**: Created migration plan
2. 🔄 **Enable HA MCP**: Add to HA configuration.yaml
3. ⏳ **Test**: Verify HA MCP server responds
4. ⏳ **Implement**: Add MCP client to zoe-mcp-server
5. ⏳ **Migrate**: One service at a time

**Starting with Home Assistant since it's official and lowest risk!**

