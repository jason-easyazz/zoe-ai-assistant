-- Fresh User Setup for Zoe AI Assistant
-- Created: 2025-10-26
-- Purpose: Recreate proper user accounts after data loss incident

-- Clear existing users (except system)
DELETE FROM users WHERE user_id != 'system';

-- Create Admin Users
INSERT INTO users (user_id, username, email, password_hash, is_active, is_admin, role, created_at) VALUES
('jason', 'jason', 'jason@easyazz.com', '$2b$12$LKEKRjvUP8VGQ3Z3Z3Z3ZON0YC7J52ZLHsapZO2dwHS6oVwr2bScq', 1, 1, 'admin', CURRENT_TIMESTAMP),
('andrew', 'andrew', 'andrew@easyazz.com', '$2b$12$LKEKRjvUP8VGQ3Z3Z3Z3ZON0YC7J52ZLHsapZO2dwHS6oVwr2bScq', 1, 1, 'admin', CURRENT_TIMESTAMP);

-- Create Regular Users
INSERT INTO users (user_id, username, email, password_hash, is_active, is_admin, role, created_at) VALUES
('teneeka', 'teneeka', 'teneeka@easyazz.com', '$2b$12$LKEKRjvUP8VGQ3Z3Z3Z3ZON0YC7J52ZLHsapZO2dwHS6oVwr2bScq', 1, 0, 'user', CURRENT_TIMESTAMP),
('asya', 'asya', 'asya@easyazz.com', '$2b$12$LKEKRjvUP8VGQ3Z3Z3Z3ZON0YC7J52ZLHsapZO2dwHS6oVwr2bScq', 1, 0, 'user', CURRENT_TIMESTAMP),
('user', 'user', 'user@easyazz.com', '$2b$12$LKEKRjvUP8VGQ3Z3Z3Z3ZON0YC7J52ZLHsapZO2dwHS6oVwr2bScq', 1, 0, 'user', CURRENT_TIMESTAMP);

-- Verify users created
SELECT 'Users created:' as status;
SELECT user_id, username, email, role, is_admin FROM users WHERE user_id != 'system' ORDER BY is_admin DESC, username;

