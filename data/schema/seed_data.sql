-- Seed Data for Zoe AI Assistant
-- Optional demo data for testing and development
-- Created: October 18, 2025

-- ============================================================================
-- DEMO USER (optional - only for development/testing)
-- ============================================================================
-- SECURITY NOTE: This creates a demo user with a bcrypt-hashed password
-- For production use: Create users via the onboarding UI with strong passwords
-- Demo credentials are documented in docs/guides/MIGRATION_TO_V2.4.md

INSERT OR IGNORE INTO users (user_id, username, email, password_hash, is_active, is_admin, role, created_at)
VALUES (
    'demo-user-001',
    'demo',
    'demo@zoe.local',
    '$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6TtxMQJqhN8/LewY5GyYIgHqGvLzq',
    1,
    0,
    'user',
    CURRENT_TIMESTAMP
);

-- ============================================================================
-- DEFAULT SETTINGS
-- ============================================================================

-- Default lists for demo user
INSERT OR IGNORE INTO lists (user_id, name, list_type, created_at)
VALUES 
    ('demo-user-001', 'Shopping List', 'shopping', CURRENT_TIMESTAMP),
    ('demo-user-001', 'To-Do', 'todo', CURRENT_TIMESTAMP),
    ('demo-user-001', 'Ideas', 'notes', CURRENT_TIMESTAMP);

-- ============================================================================
-- SAMPLE DATA (optional examples)
-- ============================================================================

-- Sample calendar event
INSERT OR IGNORE INTO calendar_events (user_id, title, description, start_time, end_time, created_at)
VALUES (
    'demo-user-001',
    'Welcome to Zoe!',
    'This is a sample calendar event. You can create, edit, and delete events via voice or chat.',
    datetime('now', '+1 day', '10:00'),
    datetime('now', '+1 day', '11:00'),
    CURRENT_TIMESTAMP
);

-- Sample journal entry
INSERT OR IGNORE INTO journal_entries (user_id, content, created_at)
VALUES (
    'demo-user-001',
    'Today I set up Zoe for the first time. Excited to explore all the features!',
    CURRENT_TIMESTAMP
);

-- ============================================================================
-- END OF SEED DATA
-- ============================================================================

