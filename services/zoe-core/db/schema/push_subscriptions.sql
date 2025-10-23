-- Push Subscriptions Schema for Web Push Notifications
-- Created: October 2025
-- Purpose: Store browser push notification subscriptions for users

CREATE TABLE IF NOT EXISTS push_subscriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    endpoint TEXT NOT NULL UNIQUE,
    keys_p256dh TEXT NOT NULL,
    keys_auth TEXT NOT NULL,
    user_agent TEXT,
    device_type TEXT,  -- 'mobile', 'desktop', 'tablet'
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_used TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    active BOOLEAN DEFAULT 1,
    
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
);

-- Index for fast user lookups
CREATE INDEX IF NOT EXISTS idx_push_user_id ON push_subscriptions(user_id);

-- Index for active subscriptions
CREATE INDEX IF NOT EXISTS idx_push_active ON push_subscriptions(active);

-- Index for endpoint lookups (unique constraint already creates this)
-- CREATE INDEX IF NOT EXISTS idx_push_endpoint ON push_subscriptions(endpoint);

-- Notification Preferences Table
CREATE TABLE IF NOT EXISTS notification_preferences (
    user_id TEXT PRIMARY KEY,
    calendar_reminders BOOLEAN DEFAULT 1,
    calendar_reminder_minutes INTEGER DEFAULT 15,
    task_due_alerts BOOLEAN DEFAULT 1,
    task_due_hours INTEGER DEFAULT 24,
    shopping_updates BOOLEAN DEFAULT 1,
    chat_messages BOOLEAN DEFAULT 1,
    home_assistant_alerts BOOLEAN DEFAULT 0,
    journal_prompts BOOLEAN DEFAULT 0,
    journal_prompt_time TEXT DEFAULT '20:00',  -- 8 PM
    birthday_reminders BOOLEAN DEFAULT 1,
    quiet_hours_enabled BOOLEAN DEFAULT 0,
    quiet_hours_start TEXT DEFAULT '22:00',
    quiet_hours_end TEXT DEFAULT '08:00',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
);

-- Notification Log (for debugging and analytics)
CREATE TABLE IF NOT EXISTS notification_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    subscription_id INTEGER,
    notification_type TEXT NOT NULL,  -- 'calendar', 'task', 'chat', etc.
    title TEXT,
    body TEXT,
    data TEXT,  -- JSON payload
    sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    success BOOLEAN DEFAULT 1,
    error_message TEXT,
    clicked BOOLEAN DEFAULT 0,
    clicked_at TIMESTAMP,
    
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
    FOREIGN KEY (subscription_id) REFERENCES push_subscriptions(id) ON DELETE SET NULL
);

-- Index for notification analytics
CREATE INDEX IF NOT EXISTS idx_notification_user ON notification_log(user_id);
CREATE INDEX IF NOT EXISTS idx_notification_type ON notification_log(notification_type);
CREATE INDEX IF NOT EXISTS idx_notification_sent ON notification_log(sent_at);

