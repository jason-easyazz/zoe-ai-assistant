# Zoe Music Module

**Music playback and control module for Zoe AI Assistant**

Provides Zoe AI with full control over music across multiple services and devices.

---

## Features

- **Multi-service support**: YouTube Music, Spotify, Apple Music
- **Multi-room audio**: Zone-based playback control
- **Smart recommendations**: ML-powered suggestions (Jetson) or metadata-based (Pi)
- **Behavioral learning**: Tracks listening patterns and preferences
- **Device routing**: Direct, Chromecast, AirPlay, Home Assistant speakers

---

## MCP Tools Provided

| Tool | Description | Parameters |
|------|-------------|------------|
| `music.search` | Search for music | query, filter_type, limit |
| `music.play_song` | Play a track/playlist | query or track_id, source, zone |
| `music.pause` | Pause playback | zone (optional) |
| `music.resume` | Resume playback | zone (optional) |
| `music.skip` | Skip to next track | zone (optional) |
| `music.set_volume` | Set volume (0-100) | volume, zone |
| `music.get_queue` | Get playback queue | zone, user_id |
| `music.add_to_queue` | Add track to queue | track_id, title, artist |
| `music.get_recommendations` | Get personalized suggestions | user_id, context, limit |
| `music.list_zones` | List multi-room zones | - |
| `music.get_context` | Get context for chat | user_id |

---

## Installation

### 1. Build the module

```bash
docker compose -f modules/zoe-music/docker-compose.module.yml build
```

### 2. Configure environment

Add to `.env`:

```bash
# Platform (auto-detected if not set)
PLATFORM=jetson  # or pi5, pi

# Optional: Music service credentials
SPOTIFY_CLIENT_ID=your_spotify_client_id
SPOTIFY_CLIENT_SECRET=your_spotify_secret
YOUTUBE_API_KEY=your_youtube_key
APPLE_MUSIC_KEY=your_apple_music_key
YTMUSIC_CLIENT_ID=your_ytmusic_client_id
YTMUSIC_CLIENT_SECRET=your_ytmusic_secret
```

### 3. Start the module

```bash
docker compose -f docker-compose.yml \
               -f docker-compose.jetson.yml \
               -f modules/zoe-music/docker-compose.module.yml \
               up -d
```

---

## Testing

### Health check

```bash
curl http://localhost:8100/health
```

### Test search

```bash
curl -X POST http://localhost:8100/tools/search \
  -H "Content-Type: application/json" \
  -d '{
    "query": "Beatles",
    "filter_type": "songs",
    "limit": 5
  }'
```

### Test play

```bash
curl -X POST http://localhost:8100/tools/play_song \
  -H "Content-Type: application/json" \
  -d '{
    "query": "Here Comes The Sun",
    "source": "youtube"
  }'
```

---

## Platform-Specific Features

### Jetson Orin NX

- **ML-powered recommendations** using audio embeddings
- **Audio analysis** for mood and genre detection
- **GPU-accelerated** similarity search
- **Higher quality** audio (256kbps)

### Raspberry Pi 5

- **Metadata-based recommendations** (efficient, CPU-only)
- **Standard quality** audio (128kbps)
- **Lower resource usage** for edge deployment

---

## Database

Module uses shared `zoe.db` database:

**Tables used**:
- `music_zones` - Multi-room zone configuration
- `music_queue` - Playback queue
- `music_history` - Listening history
- `music_affinity` - User preferences
- `music_auth` - Service authentication tokens
- `devices` - Playback devices (Chromecast, AirPlay, etc.)

**Schema files**: `db/schema/music.sql`, `db/schema/music_zones.sql`

---

## Architecture

```
zoe-music/
├── main.py                    # FastAPI server with MCP tools
├── services/
│   ├── platform.py            # Hardware detection
│   └── music/                 # Music services
│       ├── youtube_music.py   # YouTube Music integration
│       ├── media_controller.py # Playback control
│       ├── providers/         # Spotify, Apple Music
│       ├── outputs/           # Chromecast, AirPlay
│       └── recommendation_engine.py
└── tools/                     # MCP tool handlers (in main.py)
```

---

## Development

### Run standalone

```bash
cd modules/zoe-music
docker compose -f docker-compose.module.yml up
```

### View logs

```bash
docker logs -f zoe-music
```

### Rebuild after changes

```bash
docker compose -f modules/zoe-music/docker-compose.module.yml up --build
```

---

## Troubleshooting

### "Music services unavailable"

- Check `DATABASE_PATH` is correct
- Verify `zoe.db` exists and is accessible
- Check logs: `docker logs zoe-music`

### "No results found"

- Verify internet connectivity
- Check YouTube/Spotify API credentials
- Try different search query

### "Failed to play"

- Check output device is available
- Verify zone configuration
- Check media controller logs

---

## License

MIT License - Part of Zoe AI Assistant project

---

## Related Documentation

- [Music Module Execution Plan](../../docs/modules/MUSIC_MODULE_EXECUTION_PLAN.md)
- [Music Dependency Audit](../../docs/modules/MUSIC_DEPENDENCY_AUDIT.md)
- [Building MCP Modules Guide](../../docs/modules/BUILDING_MCP_MODULES.md)
