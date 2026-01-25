-- Household Multi-User Music Schema
-- Extends the music system to support multiple users and shared features
-- Created: December 2024

-- ============================================================
-- HOUSEHOLD & FAMILY STRUCTURE
-- ============================================================

-- Households group multiple users
CREATE TABLE IF NOT EXISTS households (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL DEFAULT 'My Home',
    
    -- Owner/admin of household
    owner_id TEXT NOT NULL,
    
    -- Settings (JSON)
    settings TEXT DEFAULT '{}',
    
    -- Timestamps
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Link users to households with roles
CREATE TABLE IF NOT EXISTS household_members (
    household_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    
    -- Role determines permissions
    role TEXT NOT NULL DEFAULT 'member',  -- owner, admin, member, child
    
    -- Profile info specific to this household
    display_name TEXT,
    avatar_url TEXT,
    
    -- Parental controls
    content_filter TEXT DEFAULT 'off',    -- off, moderate, strict
    time_limits TEXT,                      -- JSON: {"daily_minutes": 120}
    
    -- Timestamps
    joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    PRIMARY KEY (household_id, user_id),
    FOREIGN KEY (household_id) REFERENCES households(id) ON DELETE CASCADE
);

-- ============================================================
-- DEVICE BINDING
-- ============================================================

-- Devices (speakers, displays, etc.)
CREATE TABLE IF NOT EXISTS devices (
    id TEXT PRIMARY KEY,
    household_id TEXT,
    
    -- Device info
    name TEXT NOT NULL,
    type TEXT NOT NULL,                   -- speaker, display, computer, phone
    manufacturer TEXT,
    model TEXT,
    
    -- Location/zone
    room TEXT,                             -- Living Room, Bedroom, etc.
    
    -- Capabilities (JSON array)
    capabilities TEXT DEFAULT '["audio"]', -- ["audio", "video", "voice"]
    
    -- Connection info
    ip_address TEXT,
    mac_address TEXT,
    
    -- Status
    is_online BOOLEAN DEFAULT FALSE,
    last_seen TIMESTAMP,
    
    -- Timestamps
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    FOREIGN KEY (household_id) REFERENCES households(id) ON DELETE SET NULL
);

-- Device-to-user binding (which user "owns" which device)
CREATE TABLE IF NOT EXISTS device_user_bindings (
    device_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    
    -- Binding type
    binding_type TEXT NOT NULL DEFAULT 'primary',  -- primary, shared, temporary
    
    -- Priority when multiple users are present
    priority INTEGER DEFAULT 1,
    
    -- Timestamps
    bound_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP,                  -- For temporary bindings
    
    PRIMARY KEY (device_id, user_id),
    FOREIGN KEY (device_id) REFERENCES devices(id) ON DELETE CASCADE
);

-- ============================================================
-- PER-USER MUSIC STATE
-- ============================================================

-- User music preferences (extends existing music system)
CREATE TABLE IF NOT EXISTS user_music_preferences (
    user_id TEXT PRIMARY KEY,
    
    -- Default provider
    default_provider TEXT DEFAULT 'youtube_music',
    
    -- Playback preferences
    default_volume INTEGER DEFAULT 75,
    crossfade_enabled BOOLEAN DEFAULT FALSE,
    crossfade_seconds INTEGER DEFAULT 5,
    audio_quality TEXT DEFAULT 'auto',    -- auto, low, medium, high, lossless
    
    -- Autoplay behavior
    autoplay_enabled BOOLEAN DEFAULT TRUE,
    autoplay_source TEXT DEFAULT 'radio', -- radio, similar, discover
    
    -- Privacy
    share_listening_activity BOOLEAN DEFAULT TRUE,
    
    -- Explicit content
    explicit_content_allowed BOOLEAN DEFAULT TRUE,
    
    -- Timestamps
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Per-user listening sessions
CREATE TABLE IF NOT EXISTS music_sessions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    device_id TEXT,
    household_id TEXT,
    
    -- Session state
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    ended_at TIMESTAMP,
    
    -- What's playing
    current_track_id TEXT,
    queue_snapshot TEXT,                   -- JSON snapshot of queue at session start
    
    -- Stats
    tracks_played INTEGER DEFAULT 0,
    tracks_skipped INTEGER DEFAULT 0,
    total_listen_time_ms INTEGER DEFAULT 0,
    
    FOREIGN KEY (device_id) REFERENCES devices(id) ON DELETE SET NULL,
    FOREIGN KEY (household_id) REFERENCES households(id) ON DELETE SET NULL
);

-- ============================================================
-- SHARED PLAYLISTS
-- ============================================================

-- Shared playlists that multiple users can edit
CREATE TABLE IF NOT EXISTS shared_playlists (
    id TEXT PRIMARY KEY,
    household_id TEXT NOT NULL,
    
    -- Playlist info
    name TEXT NOT NULL,
    description TEXT,
    thumbnail_url TEXT,
    
    -- Type of shared playlist
    playlist_type TEXT NOT NULL DEFAULT 'collaborative',  -- collaborative, family_mix, party
    
    -- Settings
    allow_all_members BOOLEAN DEFAULT TRUE,
    allowed_users TEXT,                    -- JSON array of user_ids if not all
    
    -- Timestamps
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_by TEXT NOT NULL,
    
    FOREIGN KEY (household_id) REFERENCES households(id) ON DELETE CASCADE
);

-- Shared playlist tracks
CREATE TABLE IF NOT EXISTS shared_playlist_tracks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    playlist_id TEXT NOT NULL,
    
    -- Track info
    track_id TEXT NOT NULL,
    provider TEXT NOT NULL DEFAULT 'youtube_music',
    track_title TEXT,
    artist TEXT,
    album_art_url TEXT,
    duration_ms INTEGER,
    
    -- Position and metadata
    position INTEGER NOT NULL,
    added_by TEXT NOT NULL,                -- user_id
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    -- Voting/engagement (for family mix)
    votes INTEGER DEFAULT 0,
    last_played TIMESTAMP,
    
    FOREIGN KEY (playlist_id) REFERENCES shared_playlists(id) ON DELETE CASCADE
);

-- ============================================================
-- FAMILY MIX GENERATION
-- ============================================================

-- Family mix combines tastes from all household members
CREATE TABLE IF NOT EXISTS family_mix_state (
    household_id TEXT PRIMARY KEY,
    
    -- Generated mix (JSON array of track objects)
    current_mix TEXT,
    
    -- Algorithm weights per user (JSON: {user_id: weight})
    user_weights TEXT DEFAULT '{}',
    
    -- Constraints
    exclude_explicit BOOLEAN DEFAULT FALSE,
    max_track_age_days INTEGER,            -- Only include recent tracks
    
    -- Generation info
    generated_at TIMESTAMP,
    valid_until TIMESTAMP,
    generation_seed TEXT,                   -- For reproducibility
    
    FOREIGN KEY (household_id) REFERENCES households(id) ON DELETE CASCADE
);

-- ============================================================
-- INDEXES
-- ============================================================

CREATE INDEX IF NOT EXISTS idx_household_members_user ON household_members(user_id);
CREATE INDEX IF NOT EXISTS idx_devices_household ON devices(household_id);
CREATE INDEX IF NOT EXISTS idx_device_bindings_user ON device_user_bindings(user_id);
CREATE INDEX IF NOT EXISTS idx_music_sessions_user ON music_sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_music_sessions_device ON music_sessions(device_id);
CREATE INDEX IF NOT EXISTS idx_shared_playlists_household ON shared_playlists(household_id);
CREATE INDEX IF NOT EXISTS idx_shared_playlist_tracks_playlist ON shared_playlist_tracks(playlist_id);

-- ============================================================
-- TRIGGERS
-- ============================================================

CREATE TRIGGER IF NOT EXISTS households_updated
AFTER UPDATE ON households
FOR EACH ROW
BEGIN
    UPDATE households SET updated_at = datetime('now') WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS user_music_preferences_updated
AFTER UPDATE ON user_music_preferences
FOR EACH ROW
BEGIN
    UPDATE user_music_preferences SET updated_at = datetime('now') WHERE user_id = NEW.user_id;
END;

CREATE TRIGGER IF NOT EXISTS shared_playlists_updated
AFTER UPDATE ON shared_playlists
FOR EACH ROW
BEGIN
    UPDATE shared_playlists SET updated_at = datetime('now') WHERE id = NEW.id;
END;

