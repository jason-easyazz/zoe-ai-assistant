# Official MCP Integrations - Updated Analysis

## What Actually Exists (Claude's Research)

### ✅ N8N - Official Native Support
**Status:** Production-ready MCP integration

**Official:**
- Native MCP Client Tool node
- MCP Server Trigger node  
- Built into n8n platform

**Community:**
- `n8n-nodes-mcp` by nerding-io (HTTP/SSE/stdio)
- `n8n-mcp` project (541+ workflow nodes)
- Requires: `N8N_COMMUNITY_PACKAGES_ALLOW_TOOL_USAGE=true`

**Resources:**
- https://n8n.io/integrations/categories/ai/model-context-protocol/
- https://github.com/nerding-io/n8n-nodes-mcp

### ✅ Home Assistant - Official Built-in (2025.2+)
**Status:** Official integration in HA core

**Features:**
- Native MCP Server integration (Streamable HTTP)
- MCP Client integration (SSE transport)
- Assist API integration
- Built-in since Home Assistant 2025.2

**Community Implementations:**
1. **hass-mcp** (voska) - Docker-based, AI assistant control
2. **homeassistant-mcp** (cronus42) - 60 tools, 9 categories
3. **home-assistant-mcp** (hpohlmann) - OAuth 2.0 authenticated

**Resources:**
- https://www.home-assistant.io/integrations/mcp_server/
- https://www.home-assistant.io/integrations/mcp_client/

### ✅ Matrix - Community TypeScript Server
**Status:** Community-maintained, production-quality

**Implementation:** matrix-mcp-server by mjknowles
- TypeScript-based
- OAuth 2.0 with token exchange
- 15 Matrix tools
- HTTP transport
- Multi-homeserver support

**Configuration:**
```json
{
  "servers": {
    "matrix-mcp": {
      "url": "http://localhost:3000/mcp",
      "type": "http",
      "headers": {
        "matrix_access_token": "${input:matrix-access-token}",
        "matrix_user_id": "@your-username:homeserver",
        "matrix_homeserver_url": "https://matrix.example.com"
      }
    }
  }
}
```

**Resources:**
- https://github.com/mjknowles/matrix-mcp-server

## Our Current Implementation vs Official

### What We Have Now:
```
LLM → zoe-mcp-server → Bridge Services → Native APIs
      (Python)          (Python FastAPI)
```

### What Officials Use:
```
LLM → Official MCP Server → Native APIs
      (Various languages)
```

## Comparison Table

| Service | Our Approach | Official Approach | Recommendation |
|---------|-------------|-------------------|----------------|
| **N8N** | Python bridge | Native n8n nodes | **Adopt official** - Better integration |
| **Home Assistant** | Python bridge | Built-in MCP server | **Use official** - Already in HA 2025.2+ |
| **Matrix** | Python matrix-nio | TypeScript server | **Evaluate** - Both valid |

## Architectural Options

### Option 1: Hybrid Approach (RECOMMENDED)
Keep `zoe-mcp-server` as aggregator, connect to official MCP servers:

```yaml
services:
  zoe-mcp-server:
    # Aggregates all tools, provides unified interface
    
  homeassistant:
    # Use built-in MCP server (2025.2+)
    # zoe-mcp-server connects as MCP client
    
  n8n:
    # Use native MCP nodes
    # zoe-mcp-server connects as MCP client
    
  matrix-mcp:
    # Deploy mjknowles/matrix-mcp-server
    # zoe-mcp-server connects as MCP client
```

**Benefits:**
- Leverage official implementations
- Unified Zoe interface
- Better maintenance (official updates)
- More features (official tools)

### Option 2: Direct Integration
Replace our bridges entirely with official servers:

**Pros:** 
- Simpler architecture
- Official support
- Better documentation

**Cons:**
- Lose unified interface
- Multiple MCP connections
- Less control over tool definitions

### Option 3: Keep Current (NOT RECOMMENDED)
Continue with our Python bridges:

**Pros:**
- Already built
- Full control

**Cons:**
- Maintenance burden
- Missing official features
- Duplicate effort

## Migration Strategy

### Phase 1: Home Assistant (EASY)
```bash
# In docker-compose.yml, configure HA's built-in MCP server
# Update zoe-mcp-server to connect as MCP client
# Remove homeassistant-mcp-bridge service
```

**Effort:** 1-2 hours  
**Risk:** Low (official integration)

### Phase 2: Matrix (MEDIUM)
```bash
# Add matrix-mcp-server container
docker run -p 3000:3000 mjknowles/matrix-mcp-server
# Update zoe-mcp-server Matrix tools to proxy to it
# Remove our matrix-nio implementation
```

**Effort:** 2-4 hours  
**Risk:** Medium (community project)

### Phase 3: N8N (COMPLEX)
```bash
# Enable n8n MCP support
export N8N_COMMUNITY_PACKAGES_ALLOW_TOOL_USAGE=true
# Install n8n-nodes-mcp
# Configure zoe-mcp-server to connect to n8n's MCP interface
# Remove n8n-mcp-bridge service
```

**Effort:** 4-8 hours  
**Risk:** Medium (requires n8n reconfiguration)

## Updated Recommendations

### Immediate Actions:
1. ✅ **Keep current bridges working** (don't break production)
2. 🔄 **Verify HA version** - Check if we're on 2025.2+ with MCP support
3. 📋 **Document migration path** for each service
4. 🧪 **Test official servers** in dev environment first

### Long-term Strategy:
1. **Home Assistant**: Migrate to official MCP server (when HA upgraded)
2. **Matrix**: Deploy mjknowles/matrix-mcp-server alongside current
3. **N8N**: Enable native MCP support, keep bridge as fallback
4. **Zoe MCP Server**: Evolve into MCP client aggregator + custom tools

### Tool Categories to Keep in Zoe:
Even with official servers, keep these in zoe-mcp-server:
- **Memory tools** (unique to Zoe)
- **People tracking** (custom Zoe feature)
- **Lists** (Zoe-specific implementation)
- **Calendar** (custom integration)
- **Planning tools** (Zoe's task decomposition)

Delegate to official servers:
- **HomeAssistant** → Official HA MCP
- **N8N** → Native n8n MCP
- **Matrix** → matrix-mcp-server

## Conclusion

**Current Status:** ✅ Working but not aligned with official implementations

**Recommended Path:** Hybrid approach with gradual migration

**Priority:**
1. Test official HA MCP server (if version 2025.2+)
2. Deploy matrix-mcp-server in parallel
3. Enable n8n native MCP support
4. Keep Zoe-specific tools in zoe-mcp-server

**Timeline:** 
- Testing: 1-2 days
- Migration: 1-2 weeks (gradual)
- Full alignment: 1 month

This approach maintains stability while moving toward industry standards.

