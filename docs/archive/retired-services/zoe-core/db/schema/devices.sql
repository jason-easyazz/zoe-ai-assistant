-- Device Registry Schema
-- Unified device management for Zoe AI Assistant
-- Enables per-device settings, notifications, and presence tracking

-- Devices Table - Central registry of all user devices
CREATE TABLE IF NOT EXISTS devices (
    id TEXT PRIMARY KEY,                              -- UUID or client-generated identifier
    user_id TEXT NOT NULL,                            -- Device owner
    name TEXT NOT NULL,                               -- Friendly name: "Kitchen Panel", "Jason's Phone"
    device_type TEXT NOT NULL,                        -- Type: touch_panel, browser, mobile, speaker, tablet
    room TEXT,                                        -- Location: kitchen, bedroom, office, living_room
    
    -- Device Capabilities
    has_display BOOLEAN DEFAULT TRUE,                 -- Can show visual notifications
    has_audio BOOLEAN DEFAULT FALSE,                  -- Can play audio/TTS
    has_microphone BOOLEAN DEFAULT FALSE,             -- Can accept voice input
    has_camera BOOLEAN DEFAULT FALSE,                 -- Has camera for video calls
    screen_size TEXT,                                 -- small, medium, large, xlarge
    
    -- Connection Status
    is_online BOOLEAN DEFAULT FALSE,                  -- Currently connected
    last_seen_at TIMESTAMP,                           -- Last activity timestamp
    ip_address TEXT,                                  -- Last known IP
    user_agent TEXT,                                  -- Browser/app user agent
    
    -- Push Notifications
    push_token TEXT,                                  -- FCM/APNS token for mobile push
    push_provider TEXT,                               -- fcm, apns, web-push
    
    -- Notification Preferences
    is_primary_alert_device BOOLEAN DEFAULT FALSE,    -- Primary device for important alerts
    notification_volume INTEGER DEFAULT 100,          -- 0-100 volume level
    alert_sound TEXT DEFAULT 'default',               -- Alert sound preference
    
    -- Do Not Disturb
    do_not_disturb BOOLEAN DEFAULT FALSE,             -- DND currently active
    dnd_schedule TEXT,                                -- JSON: {"enabled": true, "start": "22:00", "end": "07:00"}
    
    -- Metadata
    os_type TEXT,                                     -- ios, android, linux, macos, windows
    os_version TEXT,
    app_version TEXT,                                 -- Zoe app/UI version
    timezone TEXT,                                    -- Device timezone
    locale TEXT DEFAULT 'en-US',                      -- Language/locale preference
    
    -- Timestamps
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- Device Sessions - Track active connections (WebSocket, etc.)
CREATE TABLE IF NOT EXISTS device_sessions (
    id TEXT PRIMARY KEY,
    device_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    session_type TEXT NOT NULL,                       -- websocket, api, voice
    connected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_activity_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_active BOOLEAN DEFAULT TRUE,
    metadata TEXT,                                    -- JSON: additional session data
    
    FOREIGN KEY (device_id) REFERENCES devices(id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- Device Notification Queue - Pending notifications per device
CREATE TABLE IF NOT EXISTS device_notification_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    notification_type TEXT NOT NULL,                  -- timer, reminder, alert, message
    title TEXT,
    message TEXT NOT NULL,
    priority TEXT DEFAULT 'normal',                   -- low, normal, high, critical
    payload TEXT,                                     -- JSON: additional data
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP,                             -- When notification becomes stale
    delivered_at TIMESTAMP,                           -- When actually delivered
    read_at TIMESTAMP,                                -- When user acknowledged
    
    FOREIGN KEY (device_id) REFERENCES devices(id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_devices_user ON devices(user_id);
CREATE INDEX IF NOT EXISTS idx_devices_room ON devices(room);
CREATE INDEX IF NOT EXISTS idx_devices_online ON devices(is_online);
CREATE INDEX IF NOT EXISTS idx_devices_primary ON devices(user_id, is_primary_alert_device);
CREATE INDEX IF NOT EXISTS idx_device_sessions_device ON device_sessions(device_id);
CREATE INDEX IF NOT EXISTS idx_device_sessions_active ON device_sessions(is_active, device_id);
CREATE INDEX IF NOT EXISTS idx_device_notifications_device ON device_notification_queue(device_id, delivered_at);
CREATE INDEX IF NOT EXISTS idx_device_notifications_pending ON device_notification_queue(device_id, delivered_at) 
    WHERE delivered_at IS NULL;

-- Trigger to update updated_at timestamp
CREATE TRIGGER IF NOT EXISTS devices_updated_at
AFTER UPDATE ON devices
FOR EACH ROW
BEGIN
    UPDATE devices SET updated_at = datetime('now') WHERE id = NEW.id;
END;

-- Add source_device_id to timers table (migration)
-- This will be run separately to avoid issues if timers table doesn't exist

