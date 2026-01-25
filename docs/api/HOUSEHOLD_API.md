# Household API Reference

API documentation for Zoe's multi-user household system.

## Overview

The Household API enables:
- Household management (create, join, manage)
- Member roles and permissions
- Device registration and binding
- Shared playlists and family mixes
- User music preferences

**Base URL**: `/api/household`

## Authentication

All endpoints require authentication via session cookie or `X-Session-ID` header.

## Household Endpoints

### Create Household

**POST** `/api/household/create`

Create a new household with current user as owner.

**Request Body:**
```json
{
  "name": "Smith Family",
  "settings": {}
}
```

**Response:**
```json
{
  "id": "household123",
  "name": "Smith Family",
  "owner_id": "user123",
  "created_at": "2024-12-27T12:00:00Z"
}
```

### Get My Households

**GET** `/api/household/mine`

Get all households the current user belongs to.

**Response:**
```json
{
  "households": [
    {
      "id": "household123",
      "name": "Smith Family",
      "owner_id": "user123",
      "member_count": 4,
      "my_role": "owner"
    }
  ]
}
```

### Get Household Details

**GET** `/api/household/{household_id}`

Get detailed household information.

**Response:**
```json
{
  "id": "household123",
  "name": "Smith Family",
  "owner_id": "user123",
  "settings": {},
  "members": [
    {
      "user_id": "user123",
      "role": "owner",
      "display_name": "John",
      "content_filter": "off"
    },
    {
      "user_id": "user456",
      "role": "member",
      "display_name": "Jane",
      "content_filter": "off"
    }
  ]
}
```

### Update Household

**PUT** `/api/household/{household_id}`

Update household details. Requires `owner` or `admin` role.

**Request Body:**
```json
{
  "name": "New Name",
  "settings": {"key": "value"}
}
```

### Delete Household

**DELETE** `/api/household/{household_id}`

Delete a household. Requires `owner` role.

## Member Endpoints

### Add Member

**POST** `/api/household/{household_id}/members`

Add a member to household. Requires `owner` or `admin` role.

**Request Body:**
```json
{
  "user_id": "user789",
  "role": "member",
  "display_name": "Alex",
  "content_filter": "off"
}
```

### Update Member

**PUT** `/api/household/{household_id}/members/{user_id}`

Update member settings.

**Request Body:**
```json
{
  "role": "admin",
  "display_name": "New Name",
  "content_filter": "moderate",
  "time_limits": {
    "daily_minutes": 120
  }
}
```

### Remove Member

**DELETE** `/api/household/{household_id}/members/{user_id}`

Remove member from household. Cannot remove owner.

## Member Roles

| Role | Permissions |
|------|-------------|
| `owner` | Full control, can delete household |
| `admin` | Add/remove members, manage settings |
| `member` | Use shared features, manage own preferences |
| `child` | Limited access, subject to parental controls |

## Content Filters

| Filter | Description |
|--------|-------------|
| `off` | No filtering |
| `moderate` | Filter explicit content |
| `strict` | Filter explicit + suggestive content |

## Device Endpoints

### Get Household Devices

**GET** `/api/household/{household_id}/devices`

Get all devices in a household.

**Response:**
```json
{
  "devices": [
    {
      "id": "device123",
      "name": "Living Room Speaker",
      "type": "speaker",
      "room": "Living Room",
      "is_online": true,
      "capabilities": ["audio"]
    }
  ]
}
```

### Register Device

**POST** `/api/household/{household_id}/devices`

Register a new device.

**Request Body:**
```json
{
  "name": "Kitchen Display",
  "type": "display",
  "room": "Kitchen",
  "manufacturer": "Google",
  "model": "Nest Hub",
  "capabilities": ["audio", "video"],
  "ip_address": "192.168.1.100"
}
```

### Bind Device to User

**POST** `/api/household/devices/{device_id}/bind`

Bind a device to a user.

**Request Body:**
```json
{
  "user_id": "user123",
  "binding_type": "primary",
  "priority": 1,
  "duration_minutes": null
}
```

### Binding Types

| Type | Description |
|------|-------------|
| `primary` | User's personal device |
| `shared` | Shared household device |
| `temporary` | Voice-activated, time-limited |

### Get Active User for Device

**GET** `/api/household/devices/{device_id}/active-user`

Get currently active user for a device.

**Response:**
```json
{
  "active_user_id": "user123"
}
```

## Music Preferences

### Get Music Preferences

**GET** `/api/household/preferences/music`

Get user's music preferences.

**Response:**
```json
{
  "default_provider": "youtube_music",
  "default_volume": 75,
  "crossfade_enabled": false,
  "crossfade_seconds": 5,
  "audio_quality": "auto",
  "autoplay_enabled": true,
  "autoplay_source": "radio",
  "share_listening_activity": true,
  "explicit_content_allowed": true
}
```

### Update Music Preferences

**PUT** `/api/household/preferences/music`

Update music preferences.

**Request Body:**
```json
{
  "default_volume": 80,
  "audio_quality": "high",
  "autoplay_enabled": false
}
```

## Family Mix Endpoints

### Get Family Mix

**GET** `/api/household/{household_id}/family-mix`

Get the family mix for a household.

**Query Parameters:**
- `force_regenerate`: Force new generation (default: false)

**Response:**
```json
{
  "household_id": "household123",
  "tracks": [
    {
      "track_id": "abc123",
      "provider": "youtube_music",
      "title": "Shared Song",
      "artist": "Artist Name",
      "album_art_url": "https://...",
      "contributed_by": "user123"
    }
  ],
  "generated_at": "2024-12-27T12:00:00Z",
  "valid_until": "2024-12-28T12:00:00Z",
  "track_count": 50
}
```

### Regenerate Family Mix

**POST** `/api/household/{household_id}/family-mix/regenerate`

Force regenerate the family mix.

**Response:**
```json
{
  "success": true,
  "track_count": 50
}
```

## Shared Playlist Endpoints

### Get Shared Playlists

**GET** `/api/household/{household_id}/playlists`

Get all shared playlists for a household.

**Response:**
```json
{
  "playlists": [
    {
      "id": "playlist123",
      "name": "Party Mix",
      "description": "Family party playlist",
      "playlist_type": "collaborative",
      "track_count": 25,
      "created_by": "user123"
    }
  ]
}
```

### Create Shared Playlist

**POST** `/api/household/{household_id}/playlists`

Create a new shared playlist.

**Request Body:**
```json
{
  "name": "Road Trip",
  "description": "Music for the drive",
  "playlist_type": "collaborative"
}
```

### Playlist Types

| Type | Description |
|------|-------------|
| `collaborative` | All members can edit |
| `family_mix` | Auto-generated mix |
| `party` | Voting-based ordering |

### Get Shared Playlist Tracks

**GET** `/api/household/playlists/{playlist_id}/tracks`

Get tracks in a shared playlist.

**Response:**
```json
{
  "tracks": [
    {
      "track_id": "abc123",
      "track_title": "Song",
      "artist": "Artist",
      "position": 0,
      "added_by": "user123",
      "added_at": "2024-12-27T12:00:00Z"
    }
  ]
}
```

### Add to Shared Playlist

**POST** `/api/household/playlists/{playlist_id}/tracks`

Add a track to a shared playlist.

**Request Body:**
```json
{
  "track_id": "xyz789",
  "provider": "youtube_music",
  "title": "New Song",
  "artist": "Artist Name",
  "duration_ms": 180000
}
```

## Error Responses

```json
{
  "error": "HOUSEHOLD_NOT_FOUND",
  "message": "Household not found",
  "details": {},
  "retryable": false
}
```

### Error Codes

| Code | Status | Description |
|------|--------|-------------|
| `HOUSEHOLD_NOT_FOUND` | 404 | Household doesn't exist |
| `NOT_HOUSEHOLD_MEMBER` | 403 | User is not a member |
| `INSUFFICIENT_PERMISSIONS` | 403 | Action requires higher role |
| `DEVICE_BINDING_ERROR` | 400 | Device binding failed |

