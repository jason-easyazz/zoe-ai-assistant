-- ZOE EVOLUTION v3.0 - UNIFIED DATABASE SCHEMA
-- Consolidates all 12 databases into a single, well-structured zoe.db
-- Created: October 4, 2025

-- ============================================================================
-- CORE USER MANAGEMENT
-- ============================================================================

-- Users table (consolidates from zoe.db, auth.db, developer_tasks.db)
CREATE TABLE users (
    user_id TEXT PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    is_active BOOLEAN DEFAULT 1,
    is_admin BOOLEAN DEFAULT 0,
    role TEXT DEFAULT 'user', -- user, admin, developer
    permissions JSON DEFAULT '[]',
    settings_json JSON DEFAULT '{}',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    failed_login_attempts INTEGER DEFAULT 0,
    locked_until TIMESTAMP,
    last_login TIMESTAMP,
    is_verified BOOLEAN DEFAULT 1
);

-- User sessions (consolidates from zoe.db, developer_tasks.db)
CREATE TABLE user_sessions (
    session_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(user_id),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP NOT NULL,
    last_activity TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    metadata JSON DEFAULT '{}',
    is_active BOOLEAN DEFAULT 1
);

-- User API keys
CREATE TABLE user_api_keys (
    key_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(user_id),
    key_name TEXT NOT NULL,
    key_hash TEXT NOT NULL,
    permissions JSON DEFAULT '[]',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP,
    last_used TIMESTAMP,
    is_active BOOLEAN DEFAULT 1
);

-- ============================================================================
-- MEMORY SYSTEM (consolidates from zoe.db, memory.db)
-- ============================================================================

-- People (consolidates from zoe.db, memory.db)
CREATE TABLE people (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(user_id),
    name TEXT NOT NULL,
    folder_path TEXT,
    profile JSON DEFAULT '{}',
    facts JSON DEFAULT '{}',
    important_dates JSON DEFAULT '{}',
    preferences JSON DEFAULT '{}',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, name)
);

-- Projects (consolidates from zoe.db, memory.db)
CREATE TABLE projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(user_id),
    name TEXT NOT NULL,
    description TEXT,
    status TEXT DEFAULT 'active',
    metadata JSON DEFAULT '{}',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, name)
);

-- Notes
CREATE TABLE notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(user_id),
    title TEXT NOT NULL,
    content TEXT,
    category TEXT DEFAULT 'general',
    tags JSON DEFAULT '[]',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Collections
CREATE TABLE collections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(user_id),
    name TEXT NOT NULL,
    description TEXT,
    layout_config JSON DEFAULT '{}',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Tiles (visual elements for collections)
CREATE TABLE tiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    collection_id INTEGER NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    content TEXT,
    position_x INTEGER DEFAULT 0,
    position_y INTEGER DEFAULT 0,
    width INTEGER DEFAULT 200,
    height INTEGER DEFAULT 150,
    tile_type TEXT DEFAULT 'text',
    metadata JSON DEFAULT '{}',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Relationships between people
CREATE TABLE relationships (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(user_id),
    person1_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    person2_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    relationship_type TEXT NOT NULL,
    strength INTEGER DEFAULT 5, -- 1-10 scale
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(person1_id, person2_id)
);

-- Memory facts
CREATE TABLE memory_facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(user_id),
    person_id INTEGER REFERENCES people(id) ON DELETE CASCADE,
    project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE,
    fact_text TEXT NOT NULL,
    fact_type TEXT DEFAULT 'general',
    confidence_score REAL DEFAULT 1.0,
    source TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================================
-- CALENDAR SYSTEM (from zoe.db)
-- ============================================================================

-- Calendar events
CREATE TABLE events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(user_id),
    title TEXT NOT NULL,
    description TEXT,
    start_date DATE NOT NULL,
    start_time TIME,
    end_date DATE,
    end_time TIME,
    all_day BOOLEAN DEFAULT FALSE,
    location TEXT,
    category TEXT DEFAULT 'personal',
    recurring TEXT, -- JSON for recurring rules
    metadata JSON DEFAULT '{}',
    cluster_id TEXT,
    exdates TEXT, -- JSON for exception dates
    overrides TEXT, -- JSON for overrides
    duration INTEGER DEFAULT 30,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================================
-- TASK MANAGEMENT (consolidates from zoe.db)
-- ============================================================================

-- Developer tasks (from developer_tasks.db)
CREATE TABLE developer_tasks (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(user_id),
    title TEXT NOT NULL,
    objective TEXT NOT NULL,
    requirements TEXT NOT NULL, -- JSON array
    constraints TEXT, -- JSON array
    acceptance_criteria TEXT, -- JSON array
    priority TEXT DEFAULT 'medium',
    assigned_to TEXT DEFAULT 'zack',
    status TEXT DEFAULT 'pending',
    context_snapshot TEXT, -- JSON
    last_analysis TEXT, -- JSON
    execution_count INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_executed_at TIMESTAMP,
    completed_at TIMESTAMP
);

-- User tasks (from zoe.db tasks table)
CREATE TABLE tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(user_id),
    title TEXT NOT NULL,
    description TEXT,
    status TEXT DEFAULT 'pending',
    priority TEXT DEFAULT 'medium',
    assigned_to TEXT,
    due_date DATE,
    completed_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Lists (shopping, personal, work todos)
CREATE TABLE lists (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(user_id),
    name TEXT NOT NULL,
    category TEXT DEFAULT 'personal', -- shopping, personal, work
    description TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- List items
CREATE TABLE list_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    list_id INTEGER NOT NULL REFERENCES lists(id) ON DELETE CASCADE,
    task_text TEXT NOT NULL,
    priority TEXT DEFAULT 'medium',
    completed BOOLEAN DEFAULT FALSE,
    completed_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================================
-- CONVERSATION SYSTEM (from zoe.db)
-- ============================================================================

-- Conversations
CREATE TABLE conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(user_id),
    user_message TEXT NOT NULL,
    assistant_response TEXT NOT NULL,
    context JSON DEFAULT '{}',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================================
-- SYSTEM MANAGEMENT
-- ============================================================================

-- System metrics (from zoe.db)
CREATE TABLE system_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    metric_name TEXT NOT NULL,
    metric_value REAL NOT NULL,
    metric_unit TEXT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    metadata JSON DEFAULT '{}'
);

-- Notifications
CREATE TABLE notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(user_id),
    title TEXT NOT NULL,
    message TEXT NOT NULL,
    notification_type TEXT DEFAULT 'info',
    is_read BOOLEAN DEFAULT FALSE,
    metadata JSON DEFAULT '{}',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    read_at TIMESTAMP
);

-- Reminders
CREATE TABLE reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(user_id),
    title TEXT NOT NULL,
    description TEXT,
    reminder_time TIMESTAMP NOT NULL,
    is_recurring BOOLEAN DEFAULT FALSE,
    recurring_pattern TEXT, -- JSON
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    triggered_at TIMESTAMP
);

-- ============================================================================
-- PERFORMANCE & MONITORING (from performance.db)
-- ============================================================================

-- Performance metrics
CREATE TABLE performance_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    service_name TEXT NOT NULL,
    metric_name TEXT NOT NULL,
    metric_value REAL NOT NULL,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    metadata JSON DEFAULT '{}'
);

-- ============================================================================
-- INDEXES FOR PERFORMANCE
-- ============================================================================

-- User indexes
CREATE INDEX idx_users_username ON users(username);
CREATE INDEX idx_users_email ON users(email);
CREATE INDEX idx_users_active ON users(is_active);

-- Session indexes
CREATE INDEX idx_user_sessions_user_id ON user_sessions(user_id);
CREATE INDEX idx_user_sessions_expires ON user_sessions(expires_at);
CREATE INDEX idx_user_sessions_active ON user_sessions(is_active);

-- Memory system indexes
CREATE INDEX idx_people_user_id ON people(user_id);
CREATE INDEX idx_people_name ON people(name);
CREATE INDEX idx_projects_user_id ON projects(user_id);
CREATE INDEX idx_notes_user_id ON notes(user_id);
CREATE INDEX idx_collections_user_id ON collections(user_id);
CREATE INDEX idx_tiles_collection_id ON tiles(collection_id);
CREATE INDEX idx_relationships_user_id ON relationships(user_id);
CREATE INDEX idx_memory_facts_user_id ON memory_facts(user_id);

-- Calendar indexes
CREATE INDEX idx_events_user_id ON events(user_id);
CREATE INDEX idx_events_start_date ON events(start_date);
CREATE INDEX idx_events_category ON events(category);

-- Task indexes
CREATE INDEX idx_developer_tasks_user_id ON developer_tasks(user_id);
CREATE INDEX idx_developer_tasks_status ON developer_tasks(status);
CREATE INDEX idx_developer_tasks_priority ON developer_tasks(priority);
CREATE INDEX idx_tasks_user_id ON tasks(user_id);
CREATE INDEX idx_tasks_status ON tasks(status);
CREATE INDEX idx_lists_user_id ON lists(user_id);
CREATE INDEX idx_list_items_list_id ON list_items(list_id);

-- Conversation indexes
CREATE INDEX idx_conversations_user_id ON conversations(user_id);
CREATE INDEX idx_conversations_timestamp ON conversations(created_at);

-- System indexes
CREATE INDEX idx_notifications_user_id ON notifications(user_id);
CREATE INDEX idx_notifications_read ON notifications(is_read);
CREATE INDEX idx_reminders_user_id ON reminders(user_id);
CREATE INDEX idx_reminders_time ON reminders(reminder_time);
CREATE INDEX idx_performance_metrics_service ON performance_metrics(service_name);
CREATE INDEX idx_performance_metrics_timestamp ON performance_metrics(timestamp);

-- ============================================================================
-- DATA INTEGRITY CONSTRAINTS
-- ============================================================================

-- Ensure user_id consistency across all tables
-- This will be enforced by foreign key constraints

-- Ensure proper data types and ranges
-- Priority values should be valid
CREATE TRIGGER validate_task_priority 
    BEFORE INSERT ON tasks
    WHEN NEW.priority NOT IN ('low', 'medium', 'high', 'critical')
    BEGIN
        SELECT RAISE(ABORT, 'Invalid priority value');
    END;

-- Ensure reminder_time is in the future for active reminders
CREATE TRIGGER validate_reminder_time
    BEFORE INSERT ON reminders
    WHEN NEW.is_active = 1 AND NEW.reminder_time <= datetime('now')
    BEGIN
        SELECT RAISE(ABORT, 'Reminder time must be in the future');
    END;

-- ============================================================================
-- MIGRATION NOTES
-- ============================================================================

/*
MIGRATION STRATEGY:

1. Create new unified schema
2. Migrate data in this order:
   - Users (consolidate from zoe.db, auth.db, developer_tasks.db)
   - User sessions
   - People (consolidate from zoe.db, memory.db)
   - Projects (consolidate from zoe.db, memory.db)
   - Events (from zoe.db)
   - Tasks (consolidate developer_tasks and user tasks)
   - Lists and list_items (from zoe.db)
   - Conversations (from zoe.db)
   - System data (metrics, notifications, etc.)

3. Preserve all existing data
4. Update all application code to use new schema
5. Test thoroughly
6. Remove old databases

TABLES TO CONSOLIDATE:
- users: zoe.db, auth.db, developer_tasks.db
- people: zoe.db, memory.db
- projects: zoe.db, memory.db
- relationships: zoe.db, memory.db
- developer_tasks: developer_tasks.db
- performance_metrics: performance.db
- conversations: zoe.db

TABLES TO PRESERVE AS-IS:
- events: zoe.db (calendar system)
- lists/list_items: zoe.db (todo system)
- notifications: zoe.db
- reminders: zoe.db
*/

