# Music System Developer Guide

A comprehensive guide for developers working with Zoe's music system.

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                        Frontend                              │
│  ┌─────────┐  ┌──────────┐  ┌────────┐  ┌──────────────┐   │
│  │ Player  │  │  Queue   │  │ Search │  │ Suggestions  │   │
│  │ Widget  │  │  Widget  │  │ Widget │  │    Widget    │   │
│  └────┬────┘  └────┬─────┘  └───┬────┘  └──────┬───────┘   │
│       └────────────┼────────────┼──────────────┘           │
│                    ▼                                        │
│              ┌───────────────┐                              │
│              │ MusicState    │   ◄── Centralized state      │
│              │ Manager       │                              │
│              └───────┬───────┘                              │
└──────────────────────┼──────────────────────────────────────┘
                       │ WebSocket/REST
                       ▼
┌──────────────────────────────────────────────────────────────┐
│                        Backend                                │
│  ┌───────────────┐    ┌─────────────────┐                    │
│  │ music.py      │    │ household.py    │                    │
│  │ (Router)      │    │ (Router)        │                    │
│  └───────┬───────┘    └────────┬────────┘                    │
│          │                     │                              │
│          ▼                     ▼                              │
│  ┌───────────────────────────────────────┐                   │
│  │           Media Controller            │                   │
│  │  - Playback state                     │                   │
│  │  - Queue management                   │                   │
│  │  - Stream URL caching                 │                   │
│  └───────────────┬───────────────────────┘                   │
│                  │                                            │
│    ┌─────────────┼──────────────┐                            │
│    ▼             ▼              ▼                            │
│ ┌─────────┐ ┌─────────┐ ┌────────────┐                       │
│ │YouTube  │ │Spotify  │ │Apple Music │   Music Providers     │
│ │Music    │ │Provider │ │Provider    │                       │
│ └────┬────┘ └────┬────┘ └─────┬──────┘                       │
│      └───────────┼────────────┘                              │
│                  ▼                                            │
│    ┌─────────────────────────────┐                           │
│    │      Output Manager         │                           │
│    └──────────────┬──────────────┘                           │
│    ┌──────────────┼──────────────┐                           │
│    ▼              ▼              ▼                           │
│ ┌─────────┐ ┌─────────┐ ┌────────────┐                       │
│ │Chromecast│ │AirPlay │ │Home        │   Output Targets      │
│ │Output   │ │Output   │ │Assistant   │                       │
│ └─────────┘ └─────────┘ └────────────┘                       │
└──────────────────────────────────────────────────────────────┘
```

## Directory Structure

```
services/zoe-core/
├── routers/
│   ├── music.py           # Music API endpoints
│   ├── household.py       # Household API endpoints
│   └── voice.py           # Voice control endpoints
├── services/
│   ├── music/
│   │   ├── media_controller.py   # Core playback controller
│   │   ├── youtube_music.py      # YouTube Music provider
│   │   ├── recommendation_engine.py
│   │   ├── context.py            # LLM context integration
│   │   ├── providers/
│   │   │   ├── base.py           # Provider interface
│   │   │   ├── registry.py       # Provider registry
│   │   │   ├── youtube.py        # YouTube adapter
│   │   │   ├── spotify.py        # Spotify provider
│   │   │   └── apple.py          # Apple Music provider
│   │   └── outputs/
│   │       ├── base.py           # Output target interface
│   │       ├── manager.py        # Output device manager
│   │       ├── chromecast.py     # Chromecast output
│   │       ├── airplay.py        # AirPlay output
│   │       └── homeassistant.py  # Home Assistant output
│   ├── household/
│   │   ├── household_manager.py  # Household management
│   │   ├── device_binding.py     # Device-user bindings
│   │   └── family_mix.py         # Family mix generation
│   └── voice/
│       ├── wake_word.py          # Wake word detection
│       ├── audio_ducking.py      # Audio ducking
│       └── barge_in.py           # Interrupt handling
├── db/schema/
│   ├── music.sql                 # Music schema
│   └── household.sql             # Household schema
└── utils/
    ├── retry.py                  # Retry utilities
    ├── errors.py                 # Custom exceptions
    └── error_handler.py          # FastAPI error handlers
```

## Adding a New Music Provider

### 1. Create Provider Class

```python
# services/music/providers/new_provider.py

from .base import MusicProvider
from dataclasses import dataclass

@dataclass
class NewProviderTrack:
    """Track data from new provider."""
    id: str
    title: str
    artist: str
    # ... other fields

class NewProvider(MusicProvider):
    """New music provider implementation."""
    
    @property
    def name(self) -> str:
        return "new_provider"
    
    @property
    def display_name(self) -> str:
        return "New Music Service"
    
    async def search(self, query: str, limit: int = 20) -> List[dict]:
        """Search for tracks."""
        # Implement API call
        response = await self._api_call(f"/search?q={query}")
        return self._parse_tracks(response)
    
    async def get_stream_url(
        self,
        track_id: str,
        quality: str = "auto"
    ) -> str:
        """Get streaming URL for track."""
        # Implement stream URL fetching
        pass
    
    async def authenticate(self, credentials: dict) -> bool:
        """Authenticate with the service."""
        pass
    
    # Implement remaining abstract methods...
```

### 2. Register Provider

```python
# services/music/providers/registry.py

from .new_provider import NewProvider

# In initialization code:
registry = MusicProviderRegistry()
registry.register_provider(NewProvider())
```

### 3. Add Authentication Endpoint

```python
# routers/music.py

@router.post("/auth/new_provider")
async def auth_new_provider(
    credentials: NewProviderCredentials,
    user_id: str = Depends(get_current_user)
):
    """Authenticate with New Provider."""
    provider = registry.get_provider("new_provider")
    success = await provider.authenticate(credentials.dict())
    
    if success:
        await save_provider_auth(user_id, "new_provider", credentials)
    
    return {"success": success}
```

## Adding a New Output Target

### 1. Create Output Class

```python
# services/music/outputs/new_output.py

from .base import OutputTarget
from dataclasses import dataclass

@dataclass
class NewDevice:
    """Device info for new output type."""
    id: str
    name: str
    address: str

class NewOutput(OutputTarget):
    """New output target implementation."""
    
    @property
    def output_type(self) -> str:
        return "new_output"
    
    async def discover_devices(self) -> List[NewDevice]:
        """Discover available devices."""
        # Implement device discovery
        pass
    
    async def play(
        self,
        device_id: str,
        stream_url: str,
        track_info: dict
    ) -> bool:
        """Play on device."""
        device = self._get_device(device_id)
        # Implement playback
        pass
    
    # Implement remaining methods...
```

### 2. Register Output

```python
# services/music/outputs/manager.py

from .new_output import NewOutput

# In initialization:
manager = OutputManager()
manager.register_output(NewOutput())
```

## Working with the Queue

### Queue Data Structure

```python
@dataclass
class QueueItem:
    track_id: str
    provider: str
    title: str
    artist: str
    album_art_url: str
    duration_ms: int
    position: int
    added_at: datetime
    added_by: str  # 'user', 'autoplay', 'radio'
```

### Queue Operations

```python
from services.music.media_controller import get_media_controller

controller = await get_media_controller()

# Add to queue
await controller.add_to_queue(track, user_id)

# Add at specific position
await controller.add_to_queue(track, user_id, position=0)

# Remove specific instance
await controller.remove_from_queue(track_id, user_id, position=3)

# Clear queue
await controller.clear_queue(user_id)

# Shuffle
await controller.shuffle_queue(user_id)

# Get next track
next_track = await controller.get_next_track(user_id)
```

## Error Handling

### Using Custom Exceptions

```python
from utils.errors import (
    MusicProviderError,
    MusicStreamError,
    TrackNotFoundError
)

async def get_stream(track_id: str):
    try:
        url = await provider.get_stream_url(track_id)
    except ProviderAPIError as e:
        raise MusicProviderError(
            f"Failed to get stream",
            provider=provider.name,
            original_error=str(e),
            cause=e
        )
```

### Using Retry Decorator

```python
from utils.retry import retry, NETWORK_CONFIG

@retry(config=NETWORK_CONFIG)
async def fetch_with_retry():
    """Automatically retries on network errors."""
    return await external_api.call()

@retry(max_retries=5, base_delay=0.5)
async def aggressive_retry():
    """Custom retry configuration."""
    pass
```

## Database Schema

### Key Tables

- `music_playback_state`: Current playback per user/device
- `music_queue`: Queue items with position
- `music_playlists`: User and synced playlists
- `music_history`: Listening history
- `music_likes`: Liked tracks
- `music_auth`: Encrypted provider credentials
- `music_events`: Behavioral events for recommendations

### Adding New Tables

1. Create SQL in `db/schema/music.sql` or new file
2. Add migration in `db/migrations/`
3. Update model classes as needed

## Testing

### Running Tests

```bash
# All tests
pytest tests/

# Unit tests only
pytest tests/unit/

# Specific module
pytest tests/unit/music/test_retry.py

# With coverage
pytest --cov=services tests/
```

### Writing Tests

```python
# tests/unit/music/test_new_feature.py

import pytest
from unittest.mock import AsyncMock, patch

class TestNewFeature:
    
    @pytest.mark.asyncio
    async def test_basic_functionality(self):
        """Test basic feature works."""
        result = await new_feature()
        assert result.success
    
    @pytest.fixture
    async def mock_provider(self):
        """Create mock provider."""
        provider = AsyncMock()
        provider.search.return_value = [{"id": "1"}]
        return provider
```

## Integration with LLM

### Adding Music Context

The music system integrates with Zoe's LLM through:

1. **Context Injection** (`chat.py`):
```python
context["music"] = await get_music_context(user_id)
```

2. **Tool Registration** (`tool_registry.py`):
```python
register_tool(
    name="music_play",
    description="Play a song or playlist",
    handler=handle_music_play
)
```

3. **Preference Learning** (`preference_learner.py`):
```python
await learner.save_music_preferences(user_id, preferences)
```

## Best Practices

### Code Style
- Use async/await for all I/O operations
- Type hints for all function signatures
- Docstrings for public methods
- Follow existing patterns in codebase

### Error Handling
- Use custom exceptions from `utils.errors`
- Add retry logic for external APIs
- Log errors with context
- Return user-friendly error messages

### Performance
- Cache stream URLs (they expire!)
- Pre-fetch next track in queue
- Use connection pooling for HTTP clients
- Batch database operations

### Security
- Encrypt stored credentials (Fernet)
- Validate user permissions
- Sanitize search queries
- Use parameterized queries

## Common Tasks

### Add a new widget
1. Create JS class in `js/widgets/music/`
2. Register in `widget-manifest.json`
3. Add to `widget-system.js` mappings
4. Include in page HTML

### Modify queue behavior
1. Update `media_controller.py`
2. Update API in `routers/music.py`
3. Update frontend in `queue.js`

### Add recommendation source
1. Extend `recommendation_engine.py`
2. Add API endpoint if needed
3. Update suggestions widget

