-- Fresh User Setup for Zoe AI Assistant
-- Created: 2025-10-26
-- Updated: 2025-10-26 - Added correct emails and password setup flow
-- Updated: 2026-10-19 - the household's real names and emails replaced with PLACEHOLDERS: this repository
--          is public. Edit a private copy with the real accounts; never commit it.
-- Purpose: Recreate proper user accounts after data loss incident

-- Clear existing users (except system)
DELETE FROM users WHERE user_id != 'system';

-- Create Admin Users (password_hash is SETUP_REQUIRED - will be set on first login)
INSERT INTO users (user_id, username, email, password_hash, is_active, is_admin, role, created_at) VALUES
('owner', 'Owner', 'owner@example.com', 'SETUP_REQUIRED', 1, 1, 'admin', CURRENT_TIMESTAMP),
('admin-2', 'Admin Two', 'admin2@example.com', 'SETUP_REQUIRED', 1, 1, 'admin', CURRENT_TIMESTAMP);

-- Create Regular Users (password_hash is SETUP_REQUIRED - will be set on first login)
INSERT INTO users (user_id, username, email, password_hash, is_active, is_admin, role, created_at) VALUES
('member-1', 'Member One', 'member1@example.com', 'SETUP_REQUIRED', 1, 0, 'user', CURRENT_TIMESTAMP),
('member-2', 'Member Two', 'member2@example.com', 'SETUP_REQUIRED', 1, 0, 'user', CURRENT_TIMESTAMP);

-- Verify users created
SELECT 'Users created:' as status;
SELECT user_id, username, email, role, is_admin FROM users WHERE user_id != 'system' ORDER BY is_admin DESC, username;

