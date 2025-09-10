#!/bin/bash
# Migrate existing single-user data to multi-user structure

set -e

echo "🔄 Migrating to Multi-User Structure"

if ! command -v docker >/dev/null 2>&1; then
  echo "❌ Docker not found. This script expects a running container named 'zoe-core'."
  exit 1
fi

TARGET_CONTAINER=${TARGET_CONTAINER:-zoe-core}

echo "➡️ Using container: $TARGET_CONTAINER"

docker exec "$TARGET_CONTAINER" sh -lc "sqlite3 /app/data/zoe.db \<< 'SQL'
CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    email TEXT UNIQUE NOT NULL,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    is_active BOOLEAN DEFAULT 1,
    is_admin BOOLEAN DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    settings_json TEXT DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS user_sessions (
    session_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    token TEXT NOT NULL,
    expires_at TIMESTAMP NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);
CREATE TABLE IF NOT EXISTS user_api_keys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    service TEXT NOT NULL,
    encrypted_key TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(user_id),
    UNIQUE(user_id, service)
);
ALTER TABLE events ADD COLUMN user_id TEXT DEFAULT 'default';
ALTER TABLE lists ADD COLUMN user_id TEXT DEFAULT 'default';
ALTER TABLE memories ADD COLUMN user_id TEXT DEFAULT 'default';
ALTER TABLE tasks ADD COLUMN user_id TEXT DEFAULT 'default';
CREATE INDEX IF NOT EXISTS idx_events_user ON events(user_id);
CREATE INDEX IF NOT EXISTS idx_lists_user ON lists(user_id);
CREATE INDEX IF NOT EXISTS idx_memories_user ON memories(user_id);
CREATE INDEX IF NOT EXISTS idx_tasks_user ON tasks(user_id);
INSERT OR IGNORE INTO users (user_id, email, username, password_hash, is_admin) VALUES ('default', 'admin@local', 'admin', 'CHANGE_ME', 1);
UPDATE events SET user_id = 'default' WHERE user_id IS NULL;
UPDATE lists SET user_id = 'default' WHERE user_id IS NULL;
UPDATE memories SET user_id = 'default' WHERE user_id IS NULL;
UPDATE tasks SET user_id = 'default' WHERE user_id IS NULL;
SQL"

docker exec "$TARGET_CONTAINER" sh -lc "mkdir -p /app/data/users/default/backups /app/data/users/default/exports /app/data/users/default/uploads"

echo "✅ Migration complete"






