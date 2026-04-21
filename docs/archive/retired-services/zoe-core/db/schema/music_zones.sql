-- Music Zones Database Schema
-- Multi-zone playback with device routing
-- Created: December 2024

-- Music zones (named groups of devices)
CREATE TABLE IF NOT EXISTS music_zones (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    room_id TEXT,                           -- Optional link to room
    user_id TEXT NOT NULL,                  -- Owner
    
    -- Settings
    is_default BOOLEAN DEFAULT FALSE,
    icon TEXT DEFAULT '🎵',
    color TEXT,                             -- Hex color for UI
    
    -- Timestamps
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_zones_user ON music_zones(user_id);

-- Zone playback state
CREATE TABLE IF NOT EXISTS zone_playback_state (
    zone_id TEXT PRIMARY KEY REFERENCES music_zones(id) ON DELETE CASCADE,
    
    -- Current track
    current_track_id TEXT,
    track_info TEXT,                        -- JSON with title, artist, album, thumbnail
    
    -- Playback state
    position_ms INTEGER DEFAULT 0,
    is_playing BOOLEAN DEFAULT FALSE,
    volume INTEGER DEFAULT 80,
    shuffle BOOLEAN DEFAULT FALSE,
    repeat_mode TEXT DEFAULT 'off',         -- off, one, all
    
    -- Queue (stored as JSON array)
    queue TEXT DEFAULT '[]',
    queue_index INTEGER DEFAULT 0,
    
    -- Active controllers
    active_controller_count INTEGER DEFAULT 0,
    
    -- Timestamps
    started_at TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Devices assigned to zones
CREATE TABLE IF NOT EXISTS zone_devices (
    zone_id TEXT NOT NULL REFERENCES music_zones(id) ON DELETE CASCADE,
    device_id TEXT NOT NULL,
    
    -- Device info
    device_type TEXT NOT NULL,              -- 'browser', 'chromecast', 'airplay', 'sonos'
    device_name TEXT,
    
    -- Role
    role TEXT DEFAULT 'player',             -- 'player', 'controller', 'both'
    
    -- State
    is_active BOOLEAN DEFAULT TRUE,
    is_connected BOOLEAN DEFAULT FALSE,
    last_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    -- Playback capabilities
    supports_video BOOLEAN DEFAULT FALSE,
    supports_seek BOOLEAN DEFAULT TRUE,
    max_volume INTEGER DEFAULT 100,
    
    PRIMARY KEY (zone_id, device_id)
);

CREATE INDEX IF NOT EXISTS idx_zone_devices_device ON zone_devices(device_id);

-- Chromecast devices discovered
CREATE TABLE IF NOT EXISTS cast_devices (
    id TEXT PRIMARY KEY,                    -- UUID
    friendly_name TEXT NOT NULL,
    model_name TEXT,
    ip_address TEXT,
    port INTEGER DEFAULT 8009,
    
    -- Capabilities
    cast_type TEXT,                         -- 'audio', 'video', 'group'
    supports_video BOOLEAN DEFAULT TRUE,
    
    -- State
    is_available BOOLEAN DEFAULT TRUE,
    current_app TEXT,
    last_discovered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    -- Associated zone
    zone_id TEXT REFERENCES music_zones(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_cast_devices_zone ON cast_devices(zone_id);

-- AirPlay devices discovered
CREATE TABLE IF NOT EXISTS airplay_devices (
    id TEXT PRIMARY KEY,                    -- Unique identifier
    name TEXT NOT NULL,
    model TEXT,
    ip_address TEXT,
    port INTEGER,
    
    -- Device type
    device_type TEXT,                       -- 'appletv', 'homepod', 'speaker', 'other'
    
    -- Capabilities
    supports_video BOOLEAN DEFAULT FALSE,
    supports_screen_mirroring BOOLEAN DEFAULT FALSE,
    airplay_version INTEGER DEFAULT 1,
    
    -- Authentication
    requires_pairing BOOLEAN DEFAULT FALSE,
    is_paired BOOLEAN DEFAULT FALSE,
    credentials TEXT,                       -- Encrypted pairing credentials
    
    -- State
    is_available BOOLEAN DEFAULT TRUE,
    is_playing BOOLEAN DEFAULT FALSE,
    current_media TEXT,
    last_discovered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    -- Associated zone
    zone_id TEXT REFERENCES music_zones(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_airplay_devices_zone ON airplay_devices(zone_id);

-- Zone activity log
CREATE TABLE IF NOT EXISTS zone_activity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    zone_id TEXT NOT NULL REFERENCES music_zones(id) ON DELETE CASCADE,
    
    -- Event
    event_type TEXT NOT NULL,               -- play, pause, skip, device_joined, device_left
    event_data TEXT,                        -- JSON with details
    
    -- Actor
    device_id TEXT,
    user_id TEXT,
    
    -- Timestamp
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_zone_activity_zone ON zone_activity(zone_id, timestamp DESC);

-- User layout preferences (per user + device)
CREATE TABLE IF NOT EXISTS user_layouts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    device_id TEXT,                         -- NULL for default layout
    page TEXT NOT NULL,                     -- 'music', 'dashboard', etc.
    
    -- Layout data (JSON)
    layout_data TEXT NOT NULL,              -- Gridstack layout array
    layout_name TEXT DEFAULT 'default',
    
    -- Timestamps
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    UNIQUE(user_id, device_id, page, layout_name)
);

CREATE INDEX IF NOT EXISTS idx_user_layouts_user ON user_layouts(user_id);

-- Triggers
CREATE TRIGGER IF NOT EXISTS music_zones_updated
AFTER UPDATE ON music_zones
FOR EACH ROW
BEGIN
    UPDATE music_zones SET updated_at = datetime('now') WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS zone_playback_state_updated
AFTER UPDATE ON zone_playback_state
FOR EACH ROW
BEGIN
    UPDATE zone_playback_state SET updated_at = datetime('now') WHERE zone_id = NEW.zone_id;
END;

