-- Music System Database Schema
-- Supports playback state, queues, playlists, and encrypted auth
-- Created: December 2024

-- Playback state per user/device
CREATE TABLE IF NOT EXISTS music_playback_state (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    device_id TEXT,                           -- Target playback device
    provider TEXT NOT NULL DEFAULT 'youtube_music',
    
    -- Current track info
    track_id TEXT,
    track_title TEXT,
    artist TEXT,
    album TEXT,
    album_art_url TEXT,
    
    -- Playback state
    position_ms INTEGER DEFAULT 0,
    duration_ms INTEGER,
    is_playing BOOLEAN DEFAULT FALSE,
    volume INTEGER DEFAULT 100,
    shuffle BOOLEAN DEFAULT FALSE,
    repeat_mode TEXT DEFAULT 'off',           -- off, one, all
    
    -- Timestamps
    started_at TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    FOREIGN KEY (device_id) REFERENCES devices(id) ON DELETE SET NULL
);

-- Music queue per user/device
CREATE TABLE IF NOT EXISTS music_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    device_id TEXT,
    
    -- Track info
    track_id TEXT NOT NULL,
    provider TEXT NOT NULL DEFAULT 'youtube_music',
    track_title TEXT,
    artist TEXT,
    album_art_url TEXT,
    duration_ms INTEGER,
    
    -- Queue position
    position INTEGER NOT NULL,
    
    -- Metadata
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    added_by TEXT,                            -- 'user', 'autoplay', 'radio'
    
    FOREIGN KEY (device_id) REFERENCES devices(id) ON DELETE CASCADE
);

-- User playlists (synced from providers + custom)
CREATE TABLE IF NOT EXISTS music_playlists (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    
    -- Provider info
    provider TEXT,                            -- youtube_music, spotify, local, null for custom
    provider_id TEXT,                         -- External playlist ID
    
    -- Metadata
    track_count INTEGER DEFAULT 0,
    thumbnail_url TEXT,
    is_public BOOLEAN DEFAULT FALSE,
    is_synced BOOLEAN DEFAULT FALSE,
    
    -- Timestamps
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_synced_at TIMESTAMP
);

-- Playlist tracks
CREATE TABLE IF NOT EXISTS music_playlist_tracks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    playlist_id TEXT NOT NULL,
    track_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    
    -- Track info (cached for offline display)
    track_title TEXT,
    artist TEXT,
    album TEXT,
    album_art_url TEXT,
    duration_ms INTEGER,
    
    -- Position in playlist
    position INTEGER NOT NULL,
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    FOREIGN KEY (playlist_id) REFERENCES music_playlists(id) ON DELETE CASCADE
);

-- Encrypted auth credentials for music providers
CREATE TABLE IF NOT EXISTS music_auth (
    user_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    auth_data TEXT NOT NULL,                  -- Encrypted with Fernet
    auth_type TEXT DEFAULT 'oauth',           -- oauth, cookie, api_key
    
    -- Token refresh info
    expires_at TIMESTAMP,
    refresh_token TEXT,                       -- Encrypted
    
    -- Timestamps
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    PRIMARY KEY (user_id, provider)
);

-- Music listening history
CREATE TABLE IF NOT EXISTS music_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    device_id TEXT,
    
    -- Track info
    track_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    track_title TEXT,
    artist TEXT,
    album TEXT,
    
    -- Play info
    played_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    duration_played_ms INTEGER,               -- How long user listened
    completed BOOLEAN DEFAULT FALSE,          -- Did they finish the track?
    
    -- Context
    source TEXT,                              -- search, playlist, queue, radio
    playlist_id TEXT
);

-- Liked tracks
CREATE TABLE IF NOT EXISTS music_likes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    track_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    
    -- Track info (cached)
    track_title TEXT,
    artist TEXT,
    album TEXT,
    album_art_url TEXT,
    
    liked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    UNIQUE(user_id, track_id, provider)
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_music_state_user ON music_playback_state(user_id);
CREATE INDEX IF NOT EXISTS idx_music_state_device ON music_playback_state(device_id);
CREATE INDEX IF NOT EXISTS idx_music_queue_user_device ON music_queue(user_id, device_id);
CREATE INDEX IF NOT EXISTS idx_music_queue_position ON music_queue(user_id, device_id, position);
CREATE INDEX IF NOT EXISTS idx_music_playlists_user ON music_playlists(user_id);
CREATE INDEX IF NOT EXISTS idx_music_playlist_tracks_playlist ON music_playlist_tracks(playlist_id);
CREATE INDEX IF NOT EXISTS idx_music_history_user ON music_history(user_id);
CREATE INDEX IF NOT EXISTS idx_music_history_played ON music_history(user_id, played_at DESC);
CREATE INDEX IF NOT EXISTS idx_music_likes_user ON music_likes(user_id);

-- Trigger to update timestamps
CREATE TRIGGER IF NOT EXISTS music_playback_state_updated
AFTER UPDATE ON music_playback_state
FOR EACH ROW
BEGIN
    UPDATE music_playback_state SET updated_at = datetime('now') WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS music_playlists_updated
AFTER UPDATE ON music_playlists
FOR EACH ROW
BEGIN
    UPDATE music_playlists SET updated_at = datetime('now') WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS music_auth_updated
AFTER UPDATE ON music_auth
FOR EACH ROW
BEGIN
    UPDATE music_auth SET updated_at = datetime('now') WHERE user_id = NEW.user_id AND provider = NEW.provider;
END;

-- ============================================================
-- MUSIC INTELLIGENCE TABLES
-- Behavioral learning + ML embeddings for recommendations
-- ============================================================

-- Listening behavior events for affinity scoring
CREATE TABLE IF NOT EXISTS music_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    track_id TEXT NOT NULL,
    event_type TEXT NOT NULL,              -- play_start, play_end, skip, repeat, like, queue_add
    
    -- Context
    device_id TEXT,
    session_id TEXT,
    source TEXT,                           -- search, playlist, radio, similar, queue, discover
    
    -- Timing
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    position_ms INTEGER,                   -- Where in track event occurred
    duration_ms INTEGER,                   -- Track total duration
    
    -- Calculated
    completion_pct REAL,                   -- position_ms / duration_ms
    
    FOREIGN KEY (device_id) REFERENCES devices(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_events_user_time ON music_events(user_id, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_events_track ON music_events(track_id);
CREATE INDEX IF NOT EXISTS idx_events_type ON music_events(user_id, event_type);

-- Audio features extracted by Essentia (Jetson only)
CREATE TABLE IF NOT EXISTS music_audio_features (
    track_id TEXT PRIMARY KEY,
    provider TEXT NOT NULL DEFAULT 'youtube_music',
    
    -- Rhythm features
    bpm REAL,
    danceability REAL,                     -- 0.0 - 1.0
    beat_strength REAL,
    
    -- Tonal features
    key TEXT,                              -- C, C#, D, etc.
    scale TEXT,                            -- major, minor
    key_strength REAL,
    
    -- Energy/dynamics
    energy REAL,                           -- 0.0 - 1.0
    loudness REAL,                         -- dB
    dynamic_range REAL,
    
    -- Mood estimation
    valence REAL,                          -- 0.0 (sad) - 1.0 (happy)
    arousal REAL,                          -- 0.0 (calm) - 1.0 (energetic)
    
    -- Timbral (stored as JSON array)
    mfccs TEXT,                            -- JSON array of 13 MFCC coefficients
    spectral_centroid REAL,
    spectral_rolloff REAL,
    
    -- Metadata
    analyzed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    analysis_version TEXT DEFAULT '1.0'
);

-- Track embeddings for similarity search (Jetson only)
CREATE TABLE IF NOT EXISTS music_embeddings (
    track_id TEXT PRIMARY KEY,
    provider TEXT NOT NULL DEFAULT 'youtube_music',
    
    -- Embedding vectors (stored as base64-encoded binary)
    audio_embedding TEXT,                  -- 512-dim CLAP audio embedding
    metadata_embedding TEXT,               -- 384-dim text embedding
    fused_embedding TEXT,                  -- 256-dim combined embedding
    
    -- Track metadata (for quick access)
    track_title TEXT,
    artist TEXT,
    album TEXT,
    
    -- Timestamps
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_embeddings_artist ON music_embeddings(artist);

-- User taste profile (aggregated preferences)
CREATE TABLE IF NOT EXISTS music_taste_profile (
    user_id TEXT PRIMARY KEY,
    
    -- Aggregated taste vector (256-dim, base64-encoded)
    taste_embedding TEXT,
    
    -- Top preferences (JSON arrays)
    top_artists TEXT,                      -- JSON: [{"artist": "...", "score": 1.5}, ...]
    top_genres TEXT,                       -- JSON: [{"genre": "...", "score": 1.2}, ...]
    
    -- Listening patterns
    avg_bpm REAL,
    avg_energy REAL,
    avg_valence REAL,
    preferred_decades TEXT,                -- JSON: ["2010s", "2020s"]
    
    -- Stats
    total_plays INTEGER DEFAULT 0,
    total_listening_ms INTEGER DEFAULT 0,
    
    -- Timestamps
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

