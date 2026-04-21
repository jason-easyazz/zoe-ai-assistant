-- Action Logs Table for Proactive Suggestion System
-- Tracks all tool executions with full context for learning patterns

CREATE TABLE IF NOT EXISTS action_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    tool_params JSON,
    success BOOLEAN,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    context JSON,
    session_id TEXT
);

CREATE INDEX IF NOT EXISTS idx_action_logs_user_tool 
    ON action_logs(user_id, tool_name, timestamp);

CREATE INDEX IF NOT EXISTS idx_action_logs_timestamp 
    ON action_logs(timestamp);

CREATE INDEX IF NOT EXISTS idx_action_logs_user_recent
    ON action_logs(user_id, timestamp DESC);

