# Music Module Dependency Audit

**Date**: 2026-01-22  
**Purpose**: Understand dependencies before extracting music as MCP module  
**Status**: ✅ Complete

---

## Music Module Overview

**Location**: `services/zoe-core/services/music/`  
**Files**: 26 Python files  
**Router**: `services/zoe-core/routers/music.py` (2,050 lines)  
**Database**: Uses `zoe.db` with `music_*` and `devices` tables

---

## File Inventory

### Core Services (26 files)

```
services/music/
├── __init__.py                    # Module exports, platform detection
├── youtube_music.py               # YouTube Music API integration
├── auth_manager.py                # Music service auth management
├── media_controller.py            # Device routing & playback
├── event_tracker.py               # Behavioral event logging
├── affinity_engine.py             # User preference scoring
├── recommendation_engine.py       # Recommendation algorithms
├── context.py                     # Music context for chat
├── zone_manager.py                # Multi-room audio zones
├── cast_service.py                # Chromecast support
├── airplay_service.py             # AirPlay support
├── audio_analyzer.py              # Audio feature extraction (Jetson)
├── embedding_service.py           # Audio embeddings (Jetson)
├── vector_index.py                # Similarity search (Jetson)
│
├── providers/                     # Music service providers
│   ├── base.py                    # Abstract provider interface
│   ├── spotify.py                 # Spotify integration
│   ├── apple.py                   # Apple Music integration
│   ├── youtube.py                 # YouTube integration
│   └── registry.py                # Provider registry
│
└── outputs/                       # Playback outputs
    ├── base.py                    # Abstract output interface
    ├── manager.py                 # Output manager
    ├── chromecast.py              # Chromecast output
    ├── airplay.py                 # AirPlay output
    └── homeassistant.py           # Home Assistant speaker output
```

---

## External Dependencies

### zoe-core Imports

**1. Platform Detection** (`model_config.py`)
```python
from model_config import detect_hardware  # Jetson vs Pi detection
```
**Used in**: `__init__.py`, `youtube_music.py`  
**Purpose**: Platform-aware feature loading (ML on Jetson, CPU on Pi)

**Solution**: Copy or expose via environment variable

---

### Database Access

**All music files use direct SQLite3 connections:**

```python
DB_PATH = os.getenv("DATABASE_PATH", "/app/data/zoe.db")
conn = sqlite3.connect(DB_PATH)
```

**Files with DB access**: 14 files
- `recommendation_engine.py` (4 queries)
- `providers/spotify.py` (6 queries)
- `providers/apple.py` (6 queries)  
- `youtube_music.py` (1 query)
- `context.py` (1 query)
- `media_controller.py` (9 queries)
- `zone_manager.py`, `cast_service.py`, `airplay_service.py`
- `audio_analyzer.py`, `vector_index.py`

**Tables used**:
- `music_*` tables (zones, queue, affinity, history, etc.)
- `devices` table
- `music_auth` table

**Solution**: Module continues to use shared `zoe.db` - this is fine! Modules share database, just not code.

---

### Standard Library Only

**No other zoe-core imports detected!**

Music module is surprisingly self-contained:
- ✅ No imports from `auth_integration`
- ✅ No imports from `session_manager`
- ✅ No imports from `ai_client`
- ✅ No imports from `mem_agent_client`

Router handles auth via `validate_session`, but services are standalone.

---

## Reverse Dependencies (Who Uses Music?)

### 1. `routers/music.py`
**Status**: Will move with module  
**Action**: Becomes MCP endpoints in module

### 2. `routers/chat.py`
```python
from services.music.context import get_music_context, format_music_for_prompt
```
**Usage**: Adds music context to chat prompts  
**Solution**: Replace with MCP tool call `music.get_context()`

### 3. `routers/tool_registry.py`
```python
from services.music.youtube_provider import YouTubeMusicProvider
from services.music.media_controller import MediaController
from services.music.recommendation_engine import MetadataRecommendationEngine
```
**Usage**: Registers music tools for LLM  
**Solution**: Module registers its own MCP tools

---

## Database Schema

### Tables (from `db/schema/`)

**music.sql** - Core music tables
**music_zones.sql** - Multi-room zone configuration

**Migration**: No changes needed. Module uses existing tables in shared `zoe.db`.

---

## MCP Tool Mapping

### Existing Functions → MCP Tools

| Current Function | MCP Tool | Description |
|-----------------|----------|-------------|
| `search()` | `music.search` | Search YouTube/Spotify/Apple Music |
| `play()` | `music.play_song` | Play a track/album/playlist |
| `pause()` | `music.pause` | Pause playback |
| `resume()` | `music.resume` | Resume playback |
| `skip()` | `music.skip` | Skip to next track |
| `set_volume()` | `music.set_volume` | Set volume for zone |
| `get_queue()` | `music.get_queue` | Get current queue |
| `add_to_queue()` | `music.add_to_queue` | Add track to queue |
| `create_playlist()` | `music.create_playlist` | Create new playlist |
| `get_recommendations()` | `music.get_recommendations` | Get personalized recommendations |
| `get_zones()` | `music.list_zones` | List multi-room zones |
| `create_zone()` | `music.create_zone` | Create new zone |
| `get_context()` | `music.get_context` | Get context for chat (replaces chat.py import) |

---

## Migration Complexity Assessment

### ✅ Easy (Low Risk)

**Self-contained code**:
- Music services are already well-isolated
- No complex zoe-core dependencies
- Clean provider/output abstractions

### 🟡 Medium (Manageable)

**Platform detection**: 
- Need to handle `model_config.detect_hardware()`
- Solution: Environment variable or copy function

**Database access**:
- 14 files make DB calls
- Solution: Keep using shared `zoe.db` (modules can share DB)

### 🟢 No Blockers

**Authentication**: Router handles it, services don't care  
**Session management**: Router handles it, services don't care  
**AI integration**: Will be MCP tools instead

---

## Extraction Strategy

### Phase 1: Create Module Structure

```
modules/zoe-music-mcp/
├── main.py                        # FastAPI app (copy homeassistant-mcp pattern)
├── Dockerfile
├── requirements.txt
├── docker-compose.module.yml
│
├── tools/                         # MCP tool handlers
│   ├── search.py                  # music.search
│   ├── play_song.py               # music.play_song
│   ├── pause.py                   # music.pause
│   ├── set_volume.py              # music.set_volume
│   └── ...
│
├── services/                      # Move from zoe-core
│   ├── music/                     # Copy entire music/ directory
│   │   ├── __init__.py
│   │   ├── youtube_music.py
│   │   ├── providers/
│   │   ├── outputs/
│   │   └── ...
│   └── platform.py                # Platform detection helper
│
└── db/
    └── schema/                    # Copy schema files
        ├── music.sql
        └── music_zones.sql
```

### Phase 2: Handle Dependencies

**1. Platform Detection**
```python
# modules/zoe-music-mcp/services/platform.py
def detect_hardware():
    """Detect hardware platform from environment or system info."""
    platform = os.getenv("PLATFORM", "")
    if platform:
        return platform
    
    # Auto-detect
    # (copy logic from model_config.py)
```

**2. Database Path**
```python
# Already handled via DATABASE_PATH environment variable
DB_PATH = os.getenv("DATABASE_PATH", "/app/data/zoe.db")
```

**3. Authentication**
```python
# MCP tools receive user_id in requests
# Services don't need to know about auth
```

### Phase 3: Replace Imports in zoe-core

**chat.py**: Replace import with MCP tool call
```python
# OLD
from services.music.context import get_music_context

# NEW
music_context = await mcp_client.call("music.get_context", {"user_id": user_id})
```

**tool_registry.py**: Remove music tool registration (module handles it)

---

## Risks & Mitigation

### Risk 1: Breaking Chat Context

**What**: `chat.py` imports `music.context` for prompt enhancement  
**Impact**: Medium - affects chat quality  
**Mitigation**: 
- Keep old import working during transition (backward compat)
- Add `music.get_context` MCP tool
- Test both paths work identically
- Switch chat.py to MCP call after validation

### Risk 2: Platform Detection

**What**: Music uses `model_config.detect_hardware()`  
**Impact**: Low - only affects ML feature availability  
**Mitigation**:
- Copy platform detection logic to module
- Or use `PLATFORM` environment variable
- Test on both Jetson and Pi

### Risk 3: Database Schema Changes

**What**: Future schema changes affect both core and module  
**Impact**: Low - schema is stable  
**Mitigation**:
- Document shared tables clearly
- Module validates required tables exist on startup
- Migration scripts update both locations

---

## Success Criteria

✅ Music module runs standalone container  
✅ AI can control music via MCP tools  
✅ Chat context still includes music information  
✅ All 26 files moved successfully  
✅ No regression in music functionality  
✅ Module can be disabled without breaking core  
✅ Tests pass for both enabled and disabled states

---

## Estimated Effort

**2-4 hours** for complete extraction:
- 30min: Create module structure (copy homeassistant-mcp template)
- 60min: Move music files and handle imports
- 30min: Define MCP tools and handlers
- 30min: Update chat.py to use MCP call
- 30min: Create docker-compose.module.yml
- 30min: Test end-to-end

**Low complexity** - Music is surprisingly well-isolated already!

---

## Next Steps

1. ✅ Dependency audit complete
2. ⏳ Create `modules/zoe-music-mcp/` structure
3. ⏳ Copy files and adapt imports
4. ⏳ Define MCP tool interface
5. ⏳ Test isolation
6. ⏳ Update chat.py
7. ⏳ Documentation

---

**Recommendation**: ✅ Proceed with extraction. Dependencies are manageable and well-understood.
