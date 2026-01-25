# Music API Reference

Complete API documentation for Zoe's music system.

## Overview

The Music API provides endpoints for:
- Music playback control
- Queue management
- Playlist operations
- Search and discovery
- User preferences
- Output device control
- Library synchronization

**Base URL**: `/api/music`

## Authentication

All endpoints require authentication via the `X-Session-ID` header or session cookie.

## Playback Endpoints

### Play Track

**POST** `/api/music/play`

Start playing a track.

**Request Body:**
```json
{
  "track_id": "string",
  "provider": "youtube_music",
  "position": 0
}
```

**Response:**
```json
{
  "success": true,
  "track": {
    "track_id": "abc123",
    "title": "Song Title",
    "artist": "Artist Name",
    "duration_ms": 180000
  }
}
```

### Pause Playback

**POST** `/api/music/pause`

Pause current playback.

**Response:**
```json
{
  "success": true,
  "state": "paused"
}
```

### Resume Playback

**POST** `/api/music/resume`

Resume paused playback.

**Response:**
```json
{
  "success": true,
  "state": "playing"
}
```

### Skip to Next Track

**POST** `/api/music/skip`

Skip to the next track in queue.

**Response:**
```json
{
  "success": true,
  "track": {
    "track_id": "xyz789",
    "title": "Next Song",
    "artist": "Another Artist"
  }
}
```

### Previous Track

**POST** `/api/music/previous`

Go to previous track or restart current track.

**Response:**
```json
{
  "success": true,
  "track": {...}
}
```

### Seek

**POST** `/api/music/seek`

Seek to position in current track.

**Request Body:**
```json
{
  "position_ms": 30000
}
```

### Set Volume

**POST** `/api/music/volume`

Set playback volume.

**Request Body:**
```json
{
  "volume": 75
}
```

### Get Playback State

**GET** `/api/music/state`

Get current playback state.

**Response:**
```json
{
  "is_playing": true,
  "current_track": {
    "track_id": "abc123",
    "title": "Song Title",
    "artist": "Artist Name",
    "album": "Album Name",
    "album_art_url": "https://...",
    "duration_ms": 180000
  },
  "position_ms": 45000,
  "volume": 75,
  "shuffle": false,
  "repeat": "off"
}
```

## Queue Endpoints

### Get Queue

**GET** `/api/music/queue`

Get the current queue.

**Response:**
```json
{
  "queue": [
    {
      "track_id": "abc123",
      "title": "Song 1",
      "artist": "Artist 1",
      "position": 0
    },
    ...
  ],
  "total": 10
}
```

### Add to Queue

**POST** `/api/music/queue`

Add a track to the queue.

**Request Body:**
```json
{
  "track_id": "string",
  "provider": "youtube_music",
  "position": null
}
```

**Query Parameters:**
- `position` (optional): Insert at specific position

### Remove from Queue

**DELETE** `/api/music/queue/{track_id}`

Remove a track from the queue.

**Query Parameters:**
- `position` (optional): Remove specific instance at position

### Clear Queue

**DELETE** `/api/music/queue`

Clear entire queue.

### Reorder Queue

**PUT** `/api/music/queue/reorder`

Reorder tracks in the queue.

**Request Body:**
```json
{
  "from_position": 5,
  "to_position": 2
}
```

### Shuffle Queue

**POST** `/api/music/queue/shuffle`

Shuffle the queue.

### Get Next Track

**GET** `/api/music/queue/next`

Get the next track without playing it.

## Search Endpoints

### Search

**GET** `/api/music/search`

Search for music.

**Query Parameters:**
- `q`: Search query (required)
- `type`: Filter by type (`track`, `album`, `artist`, `playlist`)
- `provider`: Limit to specific provider
- `limit`: Max results (default: 20)

**Response:**
```json
{
  "results": {
    "tracks": [...],
    "albums": [...],
    "artists": [...],
    "playlists": [...]
  }
}
```

## Playlist Endpoints

### Get User Playlists

**GET** `/api/music/playlists`

Get user's playlists.

**Response:**
```json
{
  "playlists": [
    {
      "id": "playlist123",
      "name": "My Playlist",
      "track_count": 25,
      "thumbnail_url": "https://..."
    }
  ]
}
```

### Create Playlist

**POST** `/api/music/playlists`

Create a new playlist.

**Request Body:**
```json
{
  "name": "My New Playlist",
  "description": "Optional description"
}
```

### Get Playlist Tracks

**GET** `/api/music/playlists/{playlist_id}/tracks`

Get tracks in a playlist.

### Add to Playlist

**POST** `/api/music/playlists/{playlist_id}/tracks`

Add track to playlist.

### Remove from Playlist

**DELETE** `/api/music/playlists/{playlist_id}/tracks/{track_id}`

Remove track from playlist.

### Delete Playlist

**DELETE** `/api/music/playlists/{playlist_id}`

Delete a playlist.

## Recommendations Endpoints

### Get Similar Tracks

**GET** `/api/music/similar/{track_id}`

Get tracks similar to the given track.

**Response:**
```json
{
  "tracks": [...]
}
```

### Get Personal Radio

**GET** `/api/music/radio`

Get personalized radio mix.

### Get Discover

**GET** `/api/music/discover`

Get discovery recommendations.

## Preferences Endpoints

### Get Music Preferences

**GET** `/api/music/preferences`

Get user's music preferences.

**Response:**
```json
{
  "preferred_provider": "youtube_music",
  "preferred_audio_quality": "high",
  "autoplay_recommendations": true,
  "default_volume": 75
}
```

### Update Preferences

**PUT** `/api/music/preferences`

Update music preferences.

## Output Device Endpoints

### Get Available Devices

**GET** `/api/music/outputs/devices`

Get available output devices.

**Response:**
```json
{
  "devices": [
    {
      "id": "device123",
      "name": "Living Room Speaker",
      "type": "chromecast",
      "is_online": true
    },
    {
      "id": "device456",
      "name": "Kitchen HomePod",
      "type": "airplay",
      "is_online": true
    }
  ]
}
```

### Select Output Device

**POST** `/api/music/outputs/select`

Switch playback to a specific device.

**Request Body:**
```json
{
  "device_id": "device123"
}
```

### Get Device State

**GET** `/api/music/outputs/devices/{device_id}/state`

Get current state of a device.

## Library Sync Endpoints

### Sync Library

**POST** `/api/music/library/sync`

Trigger library synchronization.

**Query Parameters:**
- `provider`: Provider to sync from

### Get Liked Songs

**GET** `/api/music/library/liked`

Get liked songs.

## Error Responses

All errors follow this format:

```json
{
  "error": "ERROR_CODE",
  "message": "Human-readable message",
  "details": {},
  "retryable": true
}
```

### Common Error Codes

| Code | Status | Description |
|------|--------|-------------|
| `MUSIC_AUTH_ERROR` | 401 | Provider authentication failed |
| `TRACK_NOT_FOUND` | 404 | Track not found |
| `STREAM_ERROR` | 502 | Failed to get audio stream |
| `RATE_LIMITED` | 429 | Provider rate limit hit |
| `PROVIDER_ERROR` | 502 | Generic provider error |
| `QUEUE_ERROR` | 400 | Queue operation failed |
| `DEVICE_NOT_FOUND` | 404 | Output device not found |
| `DEVICE_OFFLINE` | 503 | Output device is offline |

## WebSocket API

### Connect

**WebSocket** `/api/music/ws`

Real-time music state updates.

### Messages from Server

```json
// Playback state change
{
  "type": "state_change",
  "state": {
    "is_playing": true,
    "current_track": {...},
    "position_ms": 45000
  }
}

// Queue update
{
  "type": "queue_update",
  "queue": [...]
}

// Track change
{
  "type": "track_change",
  "track": {...}
}
```

### Messages to Server

```json
// Request current state
{
  "type": "state_request"
}

// Ping
{
  "type": "ping"
}
```

