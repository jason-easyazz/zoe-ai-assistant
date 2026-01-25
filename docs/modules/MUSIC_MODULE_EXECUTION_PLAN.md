# Music Module Extraction - Complete Execution Plan

**Status**: 🚀 IN PROGRESS  
**Goal**: Extract music as standalone MCP module with full testing  
**Estimated Time**: 2-4 hours  
**Started**: 2026-01-22

---

## Phase 1: Foundation (30 minutes)

### Step 1.1: Create Module Directory Structure ✅ NEXT
```bash
modules/zoe-music-mcp/
├── main.py                        # FastAPI MCP server
├── Dockerfile                     # Container definition
├── requirements.txt               # Python dependencies
├── docker-compose.module.yml      # Service configuration
├── tools/                         # MCP tool handlers (empty for now)
├── services/                      # Business logic
│   └── music/                     # Copied from zoe-core
└── db/
    └── schema/                    # Database schemas
```

**Tasks**:
- [x] Create `modules/zoe-music-mcp/` directory
- [ ] Create subdirectories (tools/, services/, db/schema/)
- [ ] Copy homeassistant-mcp-bridge/main.py as template
- [ ] Create basic Dockerfile
- [ ] Create requirements.txt with music dependencies

**Validation**: Directory structure exists

---

### Step 1.2: Copy Music Services (15 minutes)
```bash
FROM: services/zoe-core/services/music/
TO:   modules/zoe-music-mcp/services/music/
```

**Tasks**:
- [ ] Copy all 26 music Python files
- [ ] Copy music database schemas
- [ ] Create platform.py helper for hardware detection
- [ ] Verify all files copied successfully

**Validation**: `ls modules/zoe-music-mcp/services/music/ | wc -l` = 26

---

### Step 1.3: Create Platform Helper (10 minutes)

**Create**: `modules/zoe-music-mcp/services/platform.py`

```python
"""Platform detection for music module."""
import os
import platform

def detect_hardware():
    """Detect hardware platform."""
    # Check environment variable first
    env_platform = os.getenv("PLATFORM", "").lower()
    if env_platform in ["jetson", "pi5", "pi"]:
        return env_platform
    
    # Auto-detect from system
    machine = platform.machine().lower()
    if "aarch64" in machine or "arm" in machine:
        # Check for Jetson-specific files
        if os.path.exists("/etc/nv_tegra_release"):
            return "jetson"
        return "pi5"
    
    return "unknown"
```

**Tasks**:
- [ ] Create platform.py
- [ ] Update music/__init__.py to use it
- [ ] Test detection works

**Validation**: Platform detection returns correct value

---

## Phase 2: MCP Tool Definition (45 minutes)

### Step 2.1: Define Core MCP Tools (20 minutes)

**Create**: `modules/zoe-music-mcp/main.py`

Based on homeassistant-mcp-bridge pattern, define tools:

```python
TOOLS = [
    {
        "name": "music.search",
        "description": "Search for music (songs, albums, artists, playlists)",
        "parameters": {
            "query": {"type": "string", "required": True},
            "filter_type": {"type": "string", "enum": ["songs", "albums", "artists", "playlists"]},
            "limit": {"type": "integer", "min": 1, "max": 50}
        }
    },
    {
        "name": "music.play_song",
        "description": "Play a song, album, or playlist",
        "parameters": {
            "query": {"type": "string", "required": True},
            "source": {"type": "string", "enum": ["spotify", "youtube", "apple"]},
            "zone": {"type": "string"}
        }
    },
    # ... more tools
]
```

**Tasks**:
- [ ] Create main.py with FastAPI setup
- [ ] Define 10-12 core MCP tools
- [ ] Add health check endpoint
- [ ] Add MCP tool registration on startup

**Validation**: `curl http://localhost:8100/` returns service info

---

### Step 2.2: Implement Tool Handlers (25 minutes)

**Create tool handlers in `tools/`**:

```
tools/
├── search.py           # music.search
├── play_song.py        # music.play_song  
├── pause.py            # music.pause
├── resume.py           # music.resume
├── skip.py             # music.skip
├── set_volume.py       # music.set_volume
├── get_queue.py        # music.get_queue
├── add_to_queue.py     # music.add_to_queue
├── create_playlist.py  # music.create_playlist
└── get_context.py      # music.get_context (for chat)
```

**Pattern for each handler**:
```python
# tools/play_song.py
from fastapi import HTTPException
from ..services.music import get_youtube_music, get_media_controller

async def handle_play_song(query: str, source: str = "youtube", zone: str = None, user_id: str = None):
    """Play a song via MCP tool."""
    youtube = get_youtube_music()
    controller = get_media_controller()
    
    # Search for track
    results = await youtube.search(query, user_id, "songs", limit=1)
    if not results:
        raise HTTPException(404, "No results found")
    
    # Play first result
    track = results[0]
    await controller.play(track['id'], zone)
    
    return {
        "status": "playing",
        "track": track['title'],
        "artist": track.get('artist'),
        "zone": zone
    }
```

**Tasks**:
- [ ] Implement 10 tool handlers
- [ ] Map router endpoints to tool functions
- [ ] Add error handling
- [ ] Add logging

**Validation**: Each tool handler can be called successfully

---

## Phase 3: Docker Configuration (30 minutes)

### Step 3.1: Create Dockerfile (15 minutes)

**Create**: `modules/zoe-music-mcp/Dockerfile`

```dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY . .

# Expose port
EXPOSE 8100

# Run service
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8100"]
```

**Tasks**:
- [ ] Create Dockerfile
- [ ] List all Python dependencies in requirements.txt
- [ ] Test build: `docker build -t zoe-music-mcp .`

**Validation**: Docker image builds successfully

---

### Step 3.2: Create Docker Compose Module File (15 minutes)

**Create**: `modules/zoe-music-mcp/docker-compose.module.yml`

```yaml
services:
  zoe-music-mcp:
    build: ./modules/zoe-music-mcp
    container_name: zoe-music-mcp
    restart: unless-stopped
    ports:
      - "8100:8100"
    volumes:
      - ./modules/zoe-music-mcp:/app
      - ./data:/app/data
    environment:
      - PYTHONUNBUFFERED=1
      - DATABASE_PATH=/app/data/zoe.db
      - MCP_SERVER_URL=http://zoe-mcp-server:8003
      - PLATFORM=${PLATFORM:-unknown}
      - SPOTIFY_CLIENT_ID=${SPOTIFY_CLIENT_ID}
      - SPOTIFY_CLIENT_SECRET=${SPOTIFY_CLIENT_SECRET}
      - YOUTUBE_API_KEY=${YOUTUBE_API_KEY}
      - APPLE_MUSIC_KEY=${APPLE_MUSIC_KEY}
    networks:
      - zoe-network
    depends_on:
      - zoe-mcp-server
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8100/health"]
      interval: 30s
      timeout: 10s
      retries: 3
```

**Tasks**:
- [ ] Create docker-compose.module.yml
- [ ] Configure all required environment variables
- [ ] Set up health check
- [ ] Configure volume mounts

**Validation**: Compose file is valid YAML

---

## Phase 4: Integration & Testing (60 minutes)

### Step 4.1: Test Module Standalone (20 minutes)

**Start module in isolation**:

```bash
# Build and start
cd modules/zoe-music-mcp
docker-compose -f docker-compose.module.yml up --build

# Test health
curl http://localhost:8100/health

# Test tool endpoint
curl -X POST http://localhost:8100/tools/search \
  -H "Content-Type: application/json" \
  -d '{"query": "Beatles", "filter_type": "songs", "limit": 5}'
```

**Tasks**:
- [ ] Start module container
- [ ] Verify health check passes
- [ ] Test each MCP tool endpoint
- [ ] Check logs for errors
- [ ] Verify database connectivity

**Validation**: All tools respond successfully

---

### Step 4.2: Register with zoe-mcp-server (15 minutes)

**Update**: `services/zoe-mcp-server/main.py` or config

Add music module to MCP server's tool registry:

```python
# MCP server discovers music tools
MUSIC_BRIDGE_URL = os.getenv("MUSIC_BRIDGE_URL", "http://zoe-music-mcp:8100")

# Register on startup
await register_bridge_tools(
    service="zoe-music-mcp",
    url=MUSIC_BRIDGE_URL,
    tools_endpoint="/tools"
)
```

**Tasks**:
- [ ] Add music module URL to MCP server config
- [ ] Implement tool discovery from module
- [ ] Register music tools with MCP server
- [ ] Verify tools appear in MCP tool list

**Validation**: `curl http://zoe-mcp-server:8003/tools | grep music`

---

### Step 4.3: Test AI Control (15 minutes)

**Test Zoe AI can call music tools**:

```bash
# Start full system with module
docker-compose -f docker-compose.yml \
               -f docker-compose.jetson.yml \
               -f modules/zoe-music-mcp/docker-compose.module.yml \
               up -d

# Test via chat
curl -X POST http://localhost:8000/api/chat \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"message": "Play some Beatles music"}'
```

**Tasks**:
- [ ] Start full Zoe system with module
- [ ] Chat with Zoe about music
- [ ] Verify AI calls music.play_song tool
- [ ] Check music actually plays
- [ ] Test multiple music commands

**Validation**: AI successfully controls music via MCP

---

### Step 4.4: Update chat.py Context (10 minutes)

**Replace music import with MCP call**:

```python
# services/zoe-core/routers/chat.py

# OLD (remove after testing)
# from services.music.context import get_music_context, format_music_for_prompt

# NEW
async def get_music_context_via_mcp(user_id: str):
    """Get music context via MCP call."""
    try:
        response = await mcp_client.call_tool(
            tool="music.get_context",
            parameters={"user_id": user_id}
        )
        return response
    except Exception as e:
        logger.warning(f"Failed to get music context: {e}")
        return None

# Use in chat endpoint
music_context = await get_music_context_via_mcp(user_id)
```

**Tasks**:
- [ ] Add MCP client call in chat.py
- [ ] Keep old import as fallback initially
- [ ] Test chat quality unchanged
- [ ] Remove old import after validation

**Validation**: Chat includes music context correctly

---

## Phase 5: Module Management (45 minutes)

### Step 5.1: Create Module CLI (30 minutes)

**Create**: `tools/zoe_module.py`

```python
#!/usr/bin/env python3
"""Zoe Module Manager CLI."""
import click
import yaml
from pathlib import Path

@click.group()
def cli():
    """Zoe module management CLI."""
    pass

@cli.command()
def list():
    """List all available modules."""
    modules_dir = Path("modules")
    for module_path in modules_dir.iterdir():
        if module_path.is_dir() and (module_path / "module.yaml").exists():
            manifest = yaml.safe_load((module_path / "module.yaml").read_text())
            status = "✓ enabled" if is_enabled(module_path.name) else "○ disabled"
            click.echo(f"{status} {module_path.name} - {manifest.get('description', '')}")

@cli.command()
@click.argument('module_name')
def enable(module_name):
    """Enable a module."""
    # Add to config/modules.yaml
    # Regenerate docker-compose.modules.yml
    click.echo(f"✓ Enabled {module_name}")

@cli.command()
@click.argument('module_name')
def disable(module_name):
    """Disable a module."""
    # Remove from config/modules.yaml
    # Regenerate docker-compose.modules.yml
    click.echo(f"✓ Disabled {module_name}")

@cli.command()
@click.argument('module_name')
def info(module_name):
    """Show module information."""
    # Display manifest, tools, status
    pass

if __name__ == '__main__':
    cli()
```

**Tasks**:
- [ ] Create CLI tool
- [ ] Implement list command
- [ ] Implement enable/disable commands
- [ ] Implement info command
- [ ] Add to PATH or create alias

**Validation**: `python tools/zoe_module.py list` shows music module

---

### Step 5.2: Create Compose Generator (15 minutes)

**Create**: `tools/generate_module_compose.py`

```python
#!/usr/bin/env python3
"""Generate docker-compose.modules.yml from enabled modules."""
import yaml
from pathlib import Path

def generate_modules_compose():
    """Generate compose file from enabled modules."""
    # Read config/modules.yaml
    config = yaml.safe_load(Path("config/modules.yaml").read_text())
    enabled = config.get("enabled_modules", [])
    
    # Start with base structure
    compose = {
        "services": {},
        "networks": {
            "zoe-network": {"external": True}
        }
    }
    
    # Merge each enabled module
    for module_name in enabled:
        module_compose_file = Path(f"modules/{module_name}/docker-compose.module.yml")
        if module_compose_file.exists():
            module_compose = yaml.safe_load(module_compose_file.read_text())
            compose["services"].update(module_compose.get("services", {}))
    
    # Write result
    output_file = Path("docker-compose.modules.yml")
    output_file.write_text(yaml.dump(compose, default_flow_style=False))
    print(f"✓ Generated {output_file}")

if __name__ == "__main__":
    generate_modules_compose()
```

**Tasks**:
- [ ] Create generator script
- [ ] Test with music module enabled
- [ ] Test with music module disabled
- [ ] Validate generated compose is correct

**Validation**: Generated `docker-compose.modules.yml` is valid

---

## Phase 6: Documentation (30 minutes)

### Step 6.1: Module README (15 minutes)

**Create**: `modules/zoe-music-mcp/README.md`

Document:
- What the module does
- MCP tools provided
- Configuration required
- Dependencies
- Testing instructions

**Tasks**:
- [ ] Write comprehensive README
- [ ] Include example tool calls
- [ ] Document environment variables
- [ ] Add troubleshooting section

---

### Step 6.2: Developer Guide (15 minutes)

**Create**: `docs/modules/BUILDING_MCP_MODULES.md`

Document how to build new modules:
- Module structure
- MCP tool definition
- Docker configuration
- Testing procedures
- Submission guidelines

**Tasks**:
- [ ] Write complete developer guide
- [ ] Include code examples
- [ ] Reference music module as example
- [ ] Add best practices section

---

## Phase 7: Cleanup & Finalization (30 minutes)

### Step 7.1: Deprecate Old Code (10 minutes)

**Mark old music code as deprecated**:

```python
# services/zoe-core/services/music/__init__.py
import warnings
warnings.warn(
    "Music service has moved to zoe-music-mcp module. "
    "This import will be removed in a future version.",
    DeprecationWarning
)
```

**Tasks**:
- [ ] Add deprecation warnings
- [ ] Update import statements to show new path
- [ ] Don't delete files yet (backward compat)
- [ ] Document deprecation timeline

**Validation**: Deprecation warnings appear in logs

---

### Step 7.2: Create Migration Guide (10 minutes)

**Create**: `docs/modules/MIGRATION_MUSIC.md`

Document the migration:
- What changed
- How to use new module
- Backward compatibility
- Testing checklist
- Rollback procedure

**Tasks**:
- [ ] Write migration guide
- [ ] Include before/after examples
- [ ] Document any breaking changes
- [ ] Add FAQ section

---

### Step 7.3: Final Testing (10 minutes)

**Complete test suite**:

```bash
# Test 1: Module disabled (core still works)
docker-compose down
# Comment out music in config/modules.yaml
python tools/generate_module_compose.py
docker-compose up -d
# Verify: Zoe works, music commands fail gracefully

# Test 2: Module enabled (music works)
# Uncomment music in config/modules.yaml
python tools/generate_module_compose.py
docker-compose up -d
# Verify: Music commands work via AI

# Test 3: Isolation (module works alone)
docker-compose -f modules/zoe-music-mcp/docker-compose.module.yml up
# Verify: Tools respond correctly
```

**Tasks**:
- [ ] Test module disabled
- [ ] Test module enabled
- [ ] Test standalone operation
- [ ] Test AI integration
- [ ] Test all MCP tools
- [ ] Check logs for errors

**Validation**: All tests pass

---

## Success Checklist

### Module Functionality
- [ ] Music module runs in separate container
- [ ] All 10+ MCP tools defined
- [ ] Tools callable via HTTP
- [ ] Tools registered with zoe-mcp-server
- [ ] AI can control music via MCP
- [ ] Database access works
- [ ] Platform detection works
- [ ] All providers work (YouTube, Spotify, Apple)

### Integration
- [ ] Module integrates with zoe-mcp-server
- [ ] chat.py gets context via MCP
- [ ] No regression in music functionality
- [ ] No regression in chat quality
- [ ] All original features preserved

### Module Management
- [ ] CLI tool works (list/enable/disable)
- [ ] Compose generator works
- [ ] Module can be enabled/disabled
- [ ] Zoe works with module disabled
- [ ] config/modules.yaml exists

### Documentation
- [ ] Module README complete
- [ ] Developer guide written
- [ ] Migration guide created
- [ ] Dependency audit documented
- [ ] Execution plan documented

### Testing
- [ ] Standalone module test passes
- [ ] Integration test passes
- [ ] Disabled module test passes
- [ ] AI control test passes
- [ ] Performance acceptable

### Cleanup
- [ ] Old code marked deprecated
- [ ] No deletions made yet
- [ ] Backward compatibility maintained
- [ ] Rollback procedure documented

---

## Rollback Plan

**If anything goes wrong**:

1. **Stop module container**:
   ```bash
   docker stop zoe-music-mcp
   ```

2. **Remove from enabled modules**:
   ```yaml
   # config/modules.yaml
   enabled_modules: []  # Remove zoe-music-mcp
   ```

3. **Restart without module**:
   ```bash
   docker-compose -f docker-compose.yml -f docker-compose.jetson.yml up -d
   ```

4. **Old code still works** - No files deleted, imports still functional

---

## Timeline

**Total Estimated Time**: 4-5 hours

- Phase 1: Foundation (30min)
- Phase 2: MCP Tools (45min)
- Phase 3: Docker (30min)
- Phase 4: Integration (60min)
- Phase 5: Management (45min)
- Phase 6: Documentation (30min)
- Phase 7: Cleanup (30min)
- Buffer: 30min

**Can be split across multiple sessions** - each phase is a checkpoint.

---

## Current Status

**Phase**: 1 - Foundation  
**Step**: 1.1 - Create Module Directory Structure  
**Next Action**: Create `modules/zoe-music-mcp/` directory and subdirectories

---

**Let's begin! 🚀**
