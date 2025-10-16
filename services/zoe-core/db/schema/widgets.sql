-- Widget Marketplace Schema
-- Supports user-created widgets, marketplace, and widget installations
-- SQLite Version

-- Widget Marketplace Table
-- Stores all available widgets (both official and user-created)
CREATE TABLE IF NOT EXISTS widget_marketplace (
    id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    name TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    description TEXT,
    author_id TEXT,
    version TEXT NOT NULL,
    widget_code TEXT NOT NULL, -- JavaScript code or JSON configuration
    widget_type TEXT DEFAULT 'custom', -- 'core', 'custom', 'ai-generated'
    icon TEXT, -- Emoji icon
    default_size TEXT DEFAULT 'size-small',
    update_interval INTEGER, -- Update interval in milliseconds
    data_sources TEXT, -- JSON array of API endpoints widget uses
    permissions TEXT, -- JSON array of required permissions
    downloads INTEGER DEFAULT 0,
    rating REAL DEFAULT 0.00,
    rating_count INTEGER DEFAULT 0,
    is_official INTEGER DEFAULT 0,
    is_active INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    published_at TEXT
);

-- User Installed Widgets
-- Tracks which widgets each user has installed
CREATE TABLE IF NOT EXISTS user_installed_widgets (
    user_id TEXT,
    widget_id TEXT,
    installed_at TEXT DEFAULT (datetime('now')),
    enabled INTEGER DEFAULT 1,
    custom_config TEXT, -- JSON user-specific widget configuration
    PRIMARY KEY (user_id, widget_id),
    FOREIGN KEY (widget_id) REFERENCES widget_marketplace(id)
);

-- Widget Ratings
-- Allows users to rate widgets
CREATE TABLE IF NOT EXISTS widget_ratings (
    id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    widget_id TEXT,
    user_id TEXT,
    rating INTEGER CHECK (rating >= 1 AND rating <= 5),
    review TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (widget_id) REFERENCES widget_marketplace(id),
    UNIQUE(widget_id, user_id)
);

-- Widget Update History
-- Tracks widget version updates
CREATE TABLE IF NOT EXISTS widget_update_history (
    id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    widget_id TEXT,
    old_version TEXT,
    new_version TEXT NOT NULL,
    changelog TEXT,
    updated_by TEXT,
    updated_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (widget_id) REFERENCES widget_marketplace(id)
);

-- User Widget Layouts
-- Stores widget layout configurations per user per device
CREATE TABLE IF NOT EXISTS user_widget_layouts (
    id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    user_id TEXT,
    device_id TEXT NOT NULL,
    layout_type TEXT NOT NULL, -- 'desktop_dashboard', 'touch_dashboard', etc.
    layout TEXT NOT NULL, -- JSON array of widget configurations
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    UNIQUE(user_id, device_id, layout_type)
);

-- Widget Usage Analytics
-- Tracks widget usage for analytics and recommendations
CREATE TABLE IF NOT EXISTS widget_usage_analytics (
    id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    user_id TEXT,
    widget_id TEXT,
    action TEXT NOT NULL, -- 'view', 'interact', 'resize', 'remove'
    metadata TEXT, -- JSON
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (widget_id) REFERENCES widget_marketplace(id)
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_widget_marketplace_author ON widget_marketplace(author_id);
CREATE INDEX IF NOT EXISTS idx_widget_marketplace_type ON widget_marketplace(widget_type);
CREATE INDEX IF NOT EXISTS idx_widget_marketplace_rating ON widget_marketplace(rating DESC);
CREATE INDEX IF NOT EXISTS idx_widget_marketplace_downloads ON widget_marketplace(downloads DESC);
CREATE INDEX IF NOT EXISTS idx_user_installed_widgets_user ON user_installed_widgets(user_id);
CREATE INDEX IF NOT EXISTS idx_user_widget_layouts_user_device ON user_widget_layouts(user_id, device_id);
CREATE INDEX IF NOT EXISTS idx_widget_ratings_widget ON widget_ratings(widget_id);
CREATE INDEX IF NOT EXISTS idx_widget_usage_analytics_user ON widget_usage_analytics(user_id);
CREATE INDEX IF NOT EXISTS idx_widget_usage_analytics_widget ON widget_usage_analytics(widget_id);

-- Triggers to update updated_at timestamp (SQLite version)
CREATE TRIGGER IF NOT EXISTS widget_marketplace_updated_at
AFTER UPDATE ON widget_marketplace
FOR EACH ROW
BEGIN
    UPDATE widget_marketplace SET updated_at = datetime('now') WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS widget_ratings_updated_at
AFTER UPDATE ON widget_ratings
FOR EACH ROW
BEGIN
    UPDATE widget_ratings SET updated_at = datetime('now') WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS user_widget_layouts_updated_at
AFTER UPDATE ON user_widget_layouts
FOR EACH ROW
BEGIN
    UPDATE user_widget_layouts SET updated_at = datetime('now') WHERE id = NEW.id;
END;

-- Insert core widgets into marketplace
INSERT OR IGNORE INTO widget_marketplace (name, display_name, description, version, widget_code, widget_type, icon, default_size, update_interval, is_official, is_active, published_at)
VALUES 
    ('events', 'Events', 'Display today''s calendar events', '1.0.0', '{}', 'core', '📅', 'size-medium', 30000, 1, 1, datetime('now')),
    ('tasks', 'Tasks', 'Display today''s tasks and todos', '1.0.0', '{}', 'core', '✅', 'size-small', 60000, 1, 1, datetime('now')),
    ('time', 'Clock', 'Display current time and date', '1.0.0', '{}', 'core', '🕐', 'size-large', 1000, 1, 1, datetime('now')),
    ('weather', 'Weather', 'Display current weather and forecast', '1.0.0', '{}', 'core', '🌤️', 'size-medium', 300000, 1, 1, datetime('now')),
    ('home', 'Smart Home', 'Control smart home devices', '1.0.0', '{}', 'core', '🏠', 'size-small', 60000, 1, 1, datetime('now')),
    ('system', 'System Status', 'Display system resources and status', '1.0.0', '{}', 'core', '💻', 'size-small', 30000, 1, 1, datetime('now')),
    ('notes', 'Notes', 'Quick notes and reminders', '1.0.0', '{}', 'core', '📝', 'size-small', NULL, 1, 1, datetime('now')),
    ('zoe-orb', 'Zoe AI', 'Interactive AI assistant with voice and chat', '1.0.0', '{}', 'core', '🤖', 'size-large', NULL, 1, 1, datetime('now'));

