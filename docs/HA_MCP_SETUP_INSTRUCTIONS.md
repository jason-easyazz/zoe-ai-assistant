# Home Assistant MCP Server Setup Instructions

## Discovery: MCP Server Requires UI Configuration

The Home Assistant MCP Server integration **does not support YAML configuration**. It must be set up through the Home Assistant UI.

### Error Encountered:
```
ERROR (MainThread) [homeassistant.helpers.config_validation] 
The mcp_server integration does not support YAML setup, 
please remove it from your configuration file
```

### How to Enable MCP Server in Home Assistant

#### Option 1: UI Setup (Recommended)
1. Open Home Assistant web interface: `http://homeassistant:8123`
2. Go to **Settings** → **Devices & Services**
3. Click **+ Add Integration**
4. Search for "Model Context Protocol Server"
5. Click to add and follow configuration wizard
6. Configure:
   - Port (default: 8124 or use existing port)
   - Authentication method
   - Allowed tools/domains

#### Option 2: Check if Already Available via API
Home Assistant 2025.10.4 might expose MCP via existing API endpoints.

Test endpoints:
```bash
# Check if MCP is available at standard HA API
curl http://homeassistant:8123/api/mcp

# Or check for MCP in API discovery
curl -H "Authorization: Bearer ${HA_TOKEN}" \
     http://homeassistant:8123/api/
```

### Alternative Approach: Use Community Implementations

Since HA's official MCP requires UI setup (manual intervention), we have alternatives:

#### 1. homeassistant-mcp (cronus42)
**Best alternative** - 60 tools across 9 categories
```yaml
services:
  homeassistant-mcp-official:
    image: ghcr.io/cronus42/homeassistant-mcp:latest
    container_name: homeassistant-mcp-official
    ports:
      - "8124:8124"
    environment:
      - HA_URL=http://homeassistant:8123
      - HA_TOKEN=${HA_ACCESS_TOKEN}
    networks:
      - zoe-network
```

Features:
- Entity state management
- Service calls
- Event handling
- Automation control
- Device registry management
- Area management
- Configuration validation
- Backup management
- Template rendering

#### 2. hass-mcp (voska)
Docker-based MCP server
```yaml
services:
  hass-mcp:
    image: ghcr.io/voska/hass-mcp:latest
    container_name: hass-mcp
    ports:
      - "8125:8125"
    environment:
      - HASS_URL=http://homeassistant:8123
      - HASS_TOKEN=${HA_ACCESS_TOKEN}
    networks:
      - zoe-network
```

Features:
- Entity control
- Automation health checks
- Entity naming consistency audits

### Recommended Path Forward

Since the built-in HA MCP requires manual UI setup, we have two options:

**Option A: Keep Current Bridge** ✅ EASIEST
- Our `homeassistant-mcp-bridge` already works
- Provides comprehensive coverage
- No additional setup needed
- Already tested and stable

**Option B: Deploy Community MCP Server** 🔄 BETTER FEATURES
- Use cronus42/homeassistant-mcp (60 tools)
- More features than our bridge
- Active community support
- Still requires configuration

**Option C: UI Setup + Wait** ⏳ OFFICIAL BUT MANUAL
- Enable through HA UI (requires manual access)
- Official support
- May have limitations vs community versions

### Decision: Hybrid Approach

For automated deployment (Zoe project), **Option A** makes most sense:

1. **Keep homeassistant-mcp-bridge** as primary
2. **Document how to enable official** (for users who want it)
3. **Add cronus42/homeassistant-mcp** as optional enhancement

Update docker-compose.yml to support all three:
```yaml
# Current (works, automated)
homeassistant-mcp-bridge:
  # ... existing config

# Optional: Community MCP (better features)
homeassistant-mcp-community:
  image: ghcr.io/cronus42/homeassistant-mcp:latest
  # ... config
  profiles: ["enhanced"]  # Only start if explicitly requested

# Official: Users can enable via UI
# No container needed - built into HA
```

### MCP Endpoint Testing

Once any MCP server is running:

```bash
# Test MCP server health
curl http://localhost:8124/health

# List available tools
curl http://localhost:8124/tools

# Test entity state retrieval
curl -X POST http://localhost:8124/mcp \
  -H "Content-Type: application/json" \
  -d '{
    "tool": "get_entity_state",
    "arguments": {"entity_id": "light.living_room"}
  }'
```

### Conclusion for Zoe Project

**Current Status:** ✅ homeassistant-mcp-bridge is appropriate
- Works without manual UI intervention
- Fully automated deployment
- Comprehensive API coverage

**Future Enhancement:** Can add cronus42/homeassistant-mcp for 60 tools

**Official HA MCP:** Available but requires manual UI setup (not ideal for automated deployment)

The bridge pattern remains valid for production automated deployments.

