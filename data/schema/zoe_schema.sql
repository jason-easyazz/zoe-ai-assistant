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
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE user_sessions (
    session_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(user_id),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP NOT NULL,
    last_activity TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    metadata JSON DEFAULT '{}',
    is_active BOOLEAN DEFAULT 1
);
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
CREATE TABLE sqlite_sequence(name,seq);
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
CREATE TABLE collections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(user_id),
    name TEXT NOT NULL,
    description TEXT,
    layout_config JSON DEFAULT '{}',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
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
CREATE TABLE list_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    list_id INTEGER NOT NULL REFERENCES lists(id) ON DELETE CASCADE,
    task_text TEXT NOT NULL,
    priority TEXT DEFAULT 'medium',
    completed BOOLEAN DEFAULT FALSE,
    completed_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
, metadata JSON, journey_id INTEGER);
CREATE TABLE conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(user_id),
    user_message TEXT NOT NULL,
    assistant_response TEXT NOT NULL,
    context JSON DEFAULT '{}',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE system_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    metric_name TEXT NOT NULL,
    metric_value REAL NOT NULL,
    metric_unit TEXT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    metadata JSON DEFAULT '{}'
);
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
, priority TEXT, action_url TEXT, dismissible INTEGER, read INTEGER);
CREATE TABLE performance_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    service_name TEXT NOT NULL,
    metric_name TEXT NOT NULL,
    metric_value REAL NOT NULL,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    metadata JSON DEFAULT '{}'
);
CREATE INDEX idx_users_username ON users(username);
CREATE INDEX idx_users_email ON users(email);
CREATE INDEX idx_users_active ON users(is_active);
CREATE INDEX idx_user_sessions_user_id ON user_sessions(user_id);
CREATE INDEX idx_user_sessions_expires ON user_sessions(expires_at);
CREATE INDEX idx_user_sessions_active ON user_sessions(is_active);
CREATE INDEX idx_people_user_id ON people(user_id);
CREATE INDEX idx_people_name ON people(name);
CREATE INDEX idx_projects_user_id ON projects(user_id);
CREATE INDEX idx_notes_user_id ON notes(user_id);
CREATE INDEX idx_collections_user_id ON collections(user_id);
CREATE INDEX idx_tiles_collection_id ON tiles(collection_id);
CREATE INDEX idx_relationships_user_id ON relationships(user_id);
CREATE INDEX idx_memory_facts_user_id ON memory_facts(user_id);
CREATE INDEX idx_events_user_id ON events(user_id);
CREATE INDEX idx_events_start_date ON events(start_date);
CREATE INDEX idx_events_category ON events(category);
CREATE INDEX idx_developer_tasks_user_id ON developer_tasks(user_id);
CREATE INDEX idx_developer_tasks_status ON developer_tasks(status);
CREATE INDEX idx_developer_tasks_priority ON developer_tasks(priority);
CREATE INDEX idx_tasks_user_id ON tasks(user_id);
CREATE INDEX idx_tasks_status ON tasks(status);
CREATE INDEX idx_list_items_list_id ON list_items(list_id);
CREATE INDEX idx_conversations_user_id ON conversations(user_id);
CREATE INDEX idx_conversations_timestamp ON conversations(created_at);
CREATE INDEX idx_notifications_user_id ON notifications(user_id);
CREATE INDEX idx_notifications_read ON notifications(is_read);
CREATE INDEX idx_performance_metrics_service ON performance_metrics(service_name);
CREATE INDEX idx_performance_metrics_timestamp ON performance_metrics(timestamp);
CREATE TRIGGER validate_task_priority 
    BEFORE INSERT ON tasks
    WHEN NEW.priority NOT IN ('low', 'medium', 'high', 'critical')
    BEGIN
        SELECT RAISE(ABORT, 'Invalid priority value');
    END;
CREATE TABLE mcp_audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                username TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                action TEXT NOT NULL,
                details TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                session_id TEXT
            );
CREATE TABLE timeline_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL REFERENCES users(user_id),
                person_id INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                event_title TEXT NOT NULL,
                event_description TEXT,
                event_date DATE,
                importance INTEGER DEFAULT 5,
                metadata JSON DEFAULT '{}',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (person_id) REFERENCES people(id)
            );
CREATE TABLE collection_layouts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                collection_id INTEGER NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
                user_id TEXT NOT NULL REFERENCES users(user_id),
                layout_name TEXT NOT NULL,
                layout_config JSON NOT NULL,
                is_default BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(collection_id, layout_name)
            );
CREATE TABLE curation_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                collection_id INTEGER NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
                user_id TEXT NOT NULL REFERENCES users(user_id),
                rule_name TEXT NOT NULL,
                rule_type TEXT NOT NULL, -- 'auto_tag', 'auto_position', 'content_filter'
                rule_config JSON NOT NULL,
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
CREATE INDEX idx_events_date 
        ON events(start_date, user_id)
    ;
CREATE TABLE person_timeline (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT DEFAULT 'default',
            person_id INTEGER,
            event_type TEXT,
            event_text TEXT,
            event_date TEXT,
            location TEXT,
            metadata JSON,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (person_id) REFERENCES people(id) ON DELETE CASCADE
        );
CREATE TABLE person_activities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT DEFAULT 'default',
            person_id INTEGER,
            activity TEXT,
            frequency TEXT,
            last_done TEXT,
            metadata JSON,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, last_prompted_journal TIMESTAMP, calendar_event_id INTEGER,
            FOREIGN KEY (person_id) REFERENCES people(id) ON DELETE CASCADE
        );
CREATE TABLE person_conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT DEFAULT 'default',
            person_id INTEGER,
            topic TEXT,
            notes TEXT,
            conversation_date TEXT,
            metadata JSON,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (person_id) REFERENCES people(id) ON DELETE CASCADE
        );
CREATE TABLE person_gifts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT DEFAULT 'default',
            person_id INTEGER,
            item TEXT,
            occasion TEXT,
            status TEXT DEFAULT 'idea',
            metadata JSON,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (person_id) REFERENCES people(id) ON DELETE CASCADE
        );
CREATE TABLE person_important_dates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT DEFAULT 'default',
            person_id INTEGER,
            name TEXT,
            date TEXT,
            metadata JSON,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (person_id) REFERENCES people(id) ON DELETE CASCADE
        );
CREATE INDEX idx_people_user ON people(user_id);
CREATE INDEX idx_projects_user ON projects(user_id);
CREATE INDEX idx_notes_user ON notes(user_id);
CREATE INDEX idx_collections_user ON collections(user_id);
CREATE INDEX idx_tiles_collection ON tiles(collection_id);
CREATE INDEX idx_relationships_user ON relationships(user_id);
CREATE INDEX idx_timeline_person ON person_timeline(person_id);
CREATE INDEX idx_activities_person ON person_activities(person_id);
CREATE INDEX idx_conversations_person ON person_conversations(person_id);
CREATE INDEX idx_gifts_person ON person_gifts(person_id);
CREATE INDEX idx_dates_person ON person_important_dates(person_id);
CREATE TABLE reminder_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT DEFAULT 'default',
            reminder_id INTEGER,
            action TEXT NOT NULL, -- completed, snoozed, dismissed, missed
            action_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            notes TEXT,
            FOREIGN KEY (reminder_id) REFERENCES reminders(id)
        );
CREATE INDEX idx_notifications_user ON notifications(user_id);
CREATE TABLE journal_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT DEFAULT 'default',
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            mood TEXT,
            mood_score INTEGER,
            tags TEXT,
            weather TEXT,
            location TEXT,
            photos JSON,
            health_data JSON,
            metadata JSON,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        , privacy_level TEXT DEFAULT 'private', place_tags JSON, journey_id INTEGER, journey_stop_id INTEGER, word_count INTEGER, read_time_minutes INTEGER, is_journey_checkin BOOLEAN DEFAULT 0);
CREATE INDEX idx_journal_date 
        ON journal_entries(created_at, user_id)
    ;
CREATE INDEX idx_journal_mood 
        ON journal_entries(mood, user_id)
    ;
CREATE TABLE families (
                    family_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT,
                    family_type TEXT DEFAULT 'family',
                    created_by TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
CREATE TABLE family_members (
                    family_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    role TEXT DEFAULT 'member',
                    joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    invited_by TEXT,
                    status TEXT DEFAULT 'active', -- 'active', 'pending', 'left'
                    PRIMARY KEY (family_id, user_id),
                    FOREIGN KEY (family_id) REFERENCES families (family_id)
                );
CREATE TABLE family_invitations (
                    invitation_id TEXT PRIMARY KEY,
                    family_id TEXT NOT NULL,
                    email TEXT NOT NULL,
                    role TEXT DEFAULT 'member',
                    message TEXT,
                    invited_by TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    expires_at TIMESTAMP,
                    status TEXT DEFAULT 'pending', -- 'pending', 'accepted', 'declined', 'expired'
                    FOREIGN KEY (family_id) REFERENCES families (family_id)
                );
CREATE TABLE shared_events (
                    event_id TEXT PRIMARY KEY,
                    family_id TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    assigned_to TEXT,
                    title TEXT NOT NULL,
                    description TEXT,
                    event_type TEXT DEFAULT 'family',
                    visibility TEXT DEFAULT 'family',
                    start_time TIMESTAMP NOT NULL,
                    end_time TIMESTAMP,
                    recurring TEXT, -- JSON for recurring rules
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (family_id) REFERENCES families (family_id)
                );
CREATE TABLE event_participants (
                    event_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    role TEXT DEFAULT 'participant', -- 'organizer', 'participant', 'observer'
                    status TEXT DEFAULT 'pending', -- 'pending', 'accepted', 'declined'
                    PRIMARY KEY (event_id, user_id),
                    FOREIGN KEY (event_id) REFERENCES shared_events (event_id)
                );
CREATE TABLE event_permissions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    permission TEXT NOT NULL,
                    granted_by TEXT NOT NULL,
                    granted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(event_id, user_id, permission)
                );
CREATE TABLE event_visibility (
                    event_id TEXT PRIMARY KEY,
                    visibility TEXT NOT NULL,
                    family_id TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
CREATE TABLE event_shares (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL,
                    shared_with TEXT NOT NULL,
                    permission_level TEXT NOT NULL,
                    shared_by TEXT NOT NULL,
                    shared_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(event_id, shared_with)
                );
CREATE TABLE reminders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL,
        title TEXT NOT NULL,
        description TEXT,
        reminder_time TIMESTAMP NOT NULL,
        is_recurring BOOLEAN DEFAULT FALSE,
        recurring_pattern TEXT,
        is_active BOOLEAN DEFAULT TRUE,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        triggered_at TIMESTAMP,
        reminder_type TEXT DEFAULT 'once',
        category TEXT DEFAULT 'personal',
        priority TEXT DEFAULT 'medium',
        due_date DATE,
        due_time TIME,
        linked_list_id INTEGER,
        linked_list_item_id INTEGER,
        family_member TEXT,
        snooze_minutes INTEGER DEFAULT 5,
        requires_acknowledgment BOOLEAN DEFAULT FALSE
    , updated_at TIMESTAMP);
CREATE INDEX idx_reminders_user_active ON reminders(user_id, is_active);
CREATE INDEX idx_reminders_time ON reminders(reminder_time);
CREATE TABLE user_onboarding (
            user_id TEXT PRIMARY KEY,
            current_phase TEXT DEFAULT 'intro',
            phase_progress TEXT DEFAULT '{}',
            responses TEXT DEFAULT '{}',
            profile_data TEXT DEFAULT '{}',
            started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP,
            last_interaction TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_complete BOOLEAN DEFAULT 0
        );
CREATE TABLE user_compatibility_profiles (
            user_id TEXT PRIMARY KEY,
            profile_data TEXT NOT NULL,
            profile_completeness REAL DEFAULT 0.0,
            confidence_score REAL DEFAULT 0.0,
            interaction_count INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
CREATE TABLE chat_sessions (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            title TEXT DEFAULT 'New Chat',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            message_count INTEGER DEFAULT 0,
            metadata JSON
        );
CREATE TABLE chat_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            metadata JSON,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (session_id) REFERENCES chat_sessions(id) ON DELETE CASCADE
        );
CREATE INDEX idx_sessions_user ON chat_sessions(user_id, updated_at DESC);
CREATE INDEX idx_messages_session ON chat_messages(session_id, created_at ASC);
CREATE TABLE model_quality (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    model_name TEXT NOT NULL,
                    response_time REAL,
                    success BOOLEAN,
                    quality_score REAL,
                    warmth_score REAL,
                    intelligence_score REAL,
                    tool_calling_score REAL,
                    query_type TEXT,
                    user_id TEXT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                );
CREATE TABLE learning_patterns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pattern_type TEXT NOT NULL,
                pattern_data TEXT NOT NULL,  -- JSON
                success_count INTEGER DEFAULT 0,
                failure_count INTEGER DEFAULT 0,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
CREATE TABLE task_execution_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                task_title TEXT,
                execution_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                success BOOLEAN NOT NULL,
                execution_duration REAL,
                error_message TEXT,
                system_context TEXT,  -- JSON
                improvements_applied TEXT,  -- JSON
                learning_insights TEXT  -- JSON
            );
CREATE TABLE knowledge_base (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT NOT NULL,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                confidence_score REAL DEFAULT 0.0,
                usage_count INTEGER DEFAULT 0,
                last_used TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
CREATE TABLE system_improvements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                improvement_type TEXT NOT NULL,
                description TEXT NOT NULL,
                implementation TEXT,  -- JSON
                effectiveness_score REAL DEFAULT 0.0,
                applied_count INTEGER DEFAULT 0,
                last_applied TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
CREATE TABLE dynamic_tasks (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            objective TEXT NOT NULL,
            requirements TEXT NOT NULL,  -- JSON array
            constraints TEXT,  -- JSON array
            acceptance_criteria TEXT,  -- JSON array
            priority TEXT DEFAULT 'medium',
            assigned_to TEXT DEFAULT 'zack',
            status TEXT DEFAULT 'pending',
            context_snapshot TEXT,  -- System state when created (for reference)
            last_analysis TEXT,  -- Last execution analysis
            execution_count INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_executed_at TIMESTAMP,
            completed_at TIMESTAMP
        , user_id TEXT DEFAULT 'system');
CREATE TABLE task_executions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT NOT NULL,
            execution_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            system_state_before TEXT,  -- JSON
            plan_generated TEXT,  -- JSON
            execution_result TEXT,
            success BOOLEAN,
            changes_made TEXT,  -- JSON list of changes
            FOREIGN KEY (task_id) REFERENCES dynamic_tasks(id)
        );
CREATE TABLE agent_goals (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            objective TEXT NOT NULL,
            constraints TEXT,  -- JSON array
            success_criteria TEXT,  -- JSON array
            priority TEXT DEFAULT 'medium',
            deadline TEXT,
            estimated_duration_minutes INTEGER,
            dependencies TEXT,  -- JSON array
            context TEXT,  -- JSON object
            assigned_agent TEXT,
            status TEXT DEFAULT 'pending',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            completed_at TEXT
        );
CREATE TABLE task_plans (
            plan_id TEXT PRIMARY KEY,
            goal_id TEXT NOT NULL,
            goal_data TEXT,  -- JSON object
            steps TEXT,  -- JSON array
            estimated_total_duration INTEGER,
            critical_path TEXT,  -- JSON array
            parallel_steps TEXT,  -- JSON array
            risk_assessment TEXT,  -- JSON object
            rollback_strategy TEXT,  -- JSON object
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            status TEXT DEFAULT 'planned',
            FOREIGN KEY (goal_id) REFERENCES agent_goals(id)
        );
CREATE TABLE agents (
            agent_id TEXT PRIMARY KEY,
            agent_type TEXT NOT NULL,
            name TEXT NOT NULL,
            capabilities TEXT,  -- JSON array
            current_load INTEGER DEFAULT 0,
            max_concurrent_tasks INTEGER DEFAULT 3,
            specializations TEXT,  -- JSON array
            status TEXT DEFAULT 'available',
            last_activity TEXT DEFAULT CURRENT_TIMESTAMP
        );
CREATE TABLE agent_messages (
            message_id TEXT PRIMARY KEY,
            from_agent TEXT NOT NULL,
            to_agent TEXT NOT NULL,
            message_type TEXT NOT NULL,
            content TEXT,  -- JSON object
            timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
            priority TEXT DEFAULT 'medium',
            requires_response BOOLEAN DEFAULT FALSE,
            response_deadline TEXT,
            responded_at TEXT
        );
CREATE TABLE tools (
            tool_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT NOT NULL,
            category TEXT NOT NULL,
            permissions TEXT,  -- JSON array
            parameters TEXT,   -- JSON array
            status TEXT DEFAULT 'available',
            requires_confirmation BOOLEAN DEFAULT FALSE,
            timeout_seconds INTEGER DEFAULT 30,
            retry_count INTEGER DEFAULT 3,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            last_used TEXT,
            usage_count INTEGER DEFAULT 0,
            success_rate REAL DEFAULT 1.0,
            metadata TEXT   -- JSON object
        );
CREATE TABLE tool_executions (
            execution_id TEXT PRIMARY KEY,
            tool_id TEXT NOT NULL,
            parameters TEXT,  -- JSON object
            status TEXT DEFAULT 'pending',
            result TEXT,      -- JSON object
            error_message TEXT,
            started_at TEXT,
            completed_at TEXT,
            duration_ms INTEGER,
            user_id TEXT DEFAULT 'default',
            session_id TEXT,
            requires_confirmation BOOLEAN DEFAULT FALSE,
            confirmed BOOLEAN DEFAULT FALSE,
            FOREIGN KEY (tool_id) REFERENCES tools(tool_id)
        );
CREATE TABLE ai_invocations (
            invocation_id TEXT PRIMARY KEY,
            user_request TEXT NOT NULL,
            context TEXT,     -- JSON object
            selected_tools TEXT,  -- JSON array
            execution_ids TEXT,   -- JSON array
            status TEXT DEFAULT 'pending',
            user_id TEXT DEFAULT 'default',
            session_id TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            completed_at TEXT
        );
CREATE TABLE identity (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                data JSON NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id)
            );
CREATE TABLE self_reflections (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                timestamp TIMESTAMP NOT NULL,
                reflection_type TEXT NOT NULL,
                content TEXT NOT NULL,
                insights JSON,
                action_items JSON,
                emotional_state TEXT DEFAULT 'neutral',
                confidence_level REAL DEFAULT 0.5
            );
CREATE TABLE consciousness_states (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                timestamp TIMESTAMP NOT NULL,
                attention_focus TEXT,
                current_goals JSON,
                emotional_state TEXT,
                energy_level REAL,
                confidence REAL,
                active_memories JSON,
                current_context JSON
            );
CREATE TABLE self_memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                memory_type TEXT NOT NULL,
                content TEXT NOT NULL,
                importance REAL DEFAULT 5.0,
                tags JSON,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
CREATE TABLE goal_progress (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                goal TEXT NOT NULL,
                status TEXT DEFAULT 'active',
                progress REAL DEFAULT 0.0,
                milestones JSON,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
CREATE TABLE IF NOT EXISTS "lists" (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT DEFAULT 'default',
                list_type TEXT NOT NULL,
                list_category TEXT DEFAULT 'personal',
                name TEXT NOT NULL,
                description TEXT,
                metadata JSON,
                shared_with JSON,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
CREATE INDEX idx_lists_category 
            ON lists(list_category, user_id)
        ;
CREATE INDEX idx_events_user_date ON events(user_id, start_date);
CREATE INDEX idx_memories_user_importance ON memory_facts(user_id, confidence_score);
CREATE TABLE sessions (
            session_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP NOT NULL,
            session_type TEXT DEFAULT 'password',
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        );
CREATE TABLE person_shared_goals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT DEFAULT 'default',
            person_id INTEGER NOT NULL,
            goal_text TEXT NOT NULL,
            goal_type TEXT DEFAULT 'general',
            status TEXT DEFAULT 'active',
            target_date DATE,
            journey_id INTEGER,
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP,
            FOREIGN KEY (person_id) REFERENCES people(id) ON DELETE CASCADE,
            FOREIGN KEY (journey_id) REFERENCES journeys(id)
        );
CREATE INDEX idx_shared_goals_person ON person_shared_goals(person_id);
CREATE INDEX idx_shared_goals_status ON person_shared_goals(status, user_id);
CREATE TABLE journal_entry_people (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entry_id INTEGER NOT NULL,
            person_id INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (entry_id) REFERENCES journal_entries(id) ON DELETE CASCADE,
            FOREIGN KEY (person_id) REFERENCES people(id) ON DELETE CASCADE,
            UNIQUE(entry_id, person_id)
        );
CREATE INDEX idx_journal_journey ON journal_entries(journey_id);
CREATE INDEX idx_journal_privacy ON journal_entries(privacy_level, user_id);
CREATE INDEX idx_entry_people_entry ON journal_entry_people(entry_id);
CREATE INDEX idx_entry_people_person ON journal_entry_people(person_id);
CREATE TABLE uploaded_photos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            photo_id TEXT UNIQUE NOT NULL,
            user_id TEXT DEFAULT 'default',
            filename TEXT NOT NULL,
            original_filename TEXT NOT NULL,
            file_path TEXT NOT NULL,
            thumbnail_path TEXT NOT NULL,
            url TEXT NOT NULL,
            thumbnail_url TEXT NOT NULL,
            size_bytes INTEGER,
            width INTEGER,
            height INTEGER,
            format TEXT,
            exif_data JSON,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
CREATE INDEX idx_photos_user ON uploaded_photos(user_id);
CREATE INDEX idx_photos_id ON uploaded_photos(photo_id);
CREATE TABLE journeys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT DEFAULT 'default',
            bucket_list_id INTEGER,
            title TEXT NOT NULL,
            description TEXT,
            start_date DATE,
            end_date DATE,
            status TEXT DEFAULT 'planning',
            cover_photo TEXT,
            metadata JSON,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (bucket_list_id) REFERENCES lists(id)
        );
CREATE TABLE journey_stops (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            journey_id INTEGER NOT NULL,
            stop_order INTEGER,
            title TEXT NOT NULL,
            location TEXT,
            location_coords JSON,
            planned_date DATE,
            actual_date DATE,
            status TEXT DEFAULT 'upcoming',
            checkin_entry_id INTEGER,
            emoji TEXT DEFAULT '📍',
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (journey_id) REFERENCES journeys(id) ON DELETE CASCADE,
            FOREIGN KEY (checkin_entry_id) REFERENCES journal_entries(id)
        );
CREATE INDEX idx_journeys_user ON journeys(user_id);
CREATE INDEX idx_journeys_status ON journeys(status, user_id);
CREATE INDEX idx_journey_stops_journey ON journey_stops(journey_id);
CREATE INDEX idx_journey_stops_status ON journey_stops(status);
CREATE TABLE memory_summaries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                summary_type TEXT NOT NULL,
                summary_date DATE NOT NULL,
                summary_text TEXT NOT NULL,
                insights TEXT,
                entities_mentioned TEXT,
                importance INTEGER DEFAULT 7,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, summary_type, summary_date)
            );
CREATE TABLE pattern_insights (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                pattern_type TEXT,
                pattern_description TEXT,
                frequency INTEGER DEFAULT 1,
                last_seen DATE,
                examples TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
CREATE TABLE knowledge_nodes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                node_type TEXT NOT NULL,
                node_id TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                data_json TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
CREATE TABLE knowledge_edges (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_node TEXT NOT NULL,
                target_node TEXT NOT NULL,
                relationship_type TEXT NOT NULL,
                weight REAL DEFAULT 1.0,
                metadata_json TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (source_node) REFERENCES knowledge_nodes(node_id),
                FOREIGN KEY (target_node) REFERENCES knowledge_nodes(node_id)
            );
CREATE INDEX idx_nodes_type ON knowledge_nodes(node_type);
CREATE INDEX idx_nodes_id ON knowledge_nodes(node_id);
CREATE INDEX idx_edges_source ON knowledge_edges(source_node);
CREATE INDEX idx_edges_target ON knowledge_edges(target_node);
CREATE TABLE user_preferences (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT UNIQUE NOT NULL,
                response_length TEXT DEFAULT 'balanced',
                tone_preference TEXT DEFAULT 'friendly',
                emoji_usage TEXT DEFAULT 'moderate',
                proactiveness_level TEXT DEFAULT 'moderate',
                detail_level TEXT DEFAULT 'balanced',
                technical_level TEXT DEFAULT 'moderate',
                preferences_json TEXT,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
CREATE TABLE preference_signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                signal_type TEXT,
                signal_value TEXT,
                confidence REAL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
CREATE TABLE event_attendees (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id INTEGER NOT NULL,
            person_id INTEGER NOT NULL,
            role TEXT DEFAULT 'participant',
            notes TEXT,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (event_id) REFERENCES events(id) ON DELETE CASCADE,
            UNIQUE(event_id, person_id)
        );
CREATE INDEX idx_event_attendees 
        ON event_attendees(event_id)
    ;
CREATE TABLE user_feedback (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                interaction_id TEXT NOT NULL,
                feedback_type TEXT NOT NULL,
                satisfaction_level INTEGER,
                explicit_rating INTEGER,
                implicit_signals TEXT,  -- JSON
                feedback_text TEXT,
                context TEXT,  -- JSON
                processed BOOLEAN DEFAULT FALSE,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
CREATE TABLE satisfaction_metrics (
                user_id TEXT PRIMARY KEY,
                total_interactions INTEGER DEFAULT 0,
                explicit_feedback_count INTEGER DEFAULT 0,
                implicit_feedback_count INTEGER DEFAULT 0,
                average_satisfaction REAL DEFAULT 0.0,
                satisfaction_trend TEXT,  -- JSON array
                top_positive_factors TEXT,  -- JSON array
                top_negative_factors TEXT,  -- JSON array
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
CREATE TABLE interaction_tracking (
                interaction_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                request_text TEXT,
                response_text TEXT,
                response_time REAL,
                task_completed BOOLEAN DEFAULT FALSE,
                follow_up_questions INTEGER DEFAULT 0,
                engagement_duration REAL,
                context TEXT,  -- JSON
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
CREATE INDEX idx_feedback_user ON user_feedback(user_id);
CREATE INDEX idx_feedback_timestamp ON user_feedback(timestamp);
CREATE INDEX idx_interaction_user ON interaction_tracking(user_id);
CREATE INDEX idx_interaction_timestamp ON interaction_tracking(timestamp);
CREATE TABLE alerts (
            id TEXT PRIMARY KEY,
            service TEXT NOT NULL,
            level TEXT NOT NULL,
            message TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            resolved BOOLEAN DEFAULT FALSE,
            resolved_at TEXT
        );
CREATE TABLE alert_rules (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            service TEXT NOT NULL,
            metric TEXT NOT NULL,
            threshold REAL NOT NULL,
            operator TEXT NOT NULL,
            level TEXT NOT NULL,
            enabled BOOLEAN DEFAULT TRUE
        );
CREATE TABLE recovery_attempts (
            id TEXT PRIMARY KEY,
            service TEXT NOT NULL,
            attempt_number INTEGER NOT NULL,
            timestamp TEXT NOT NULL,
            success BOOLEAN NOT NULL,
            error_message TEXT,
            backoff_delay INTEGER NOT NULL
        );
CREATE TABLE recovery_config (
            id INTEGER PRIMARY KEY,
            enabled BOOLEAN DEFAULT TRUE,
            max_attempts INTEGER DEFAULT 5,
            base_delay INTEGER DEFAULT 30,
            max_delay INTEGER DEFAULT 300,
            backoff_multiplier REAL DEFAULT 2.0,
            services TEXT DEFAULT '["core", "ui", "ollama", "redis", "whisper", "tts", "n8n"]'
        );
CREATE TABLE workflows (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT DEFAULT 'default',
            name TEXT NOT NULL,
            description TEXT,
            trigger_type TEXT NOT NULL,
            trigger_config JSON,
            actions JSON NOT NULL,
            conditions JSON,
            active BOOLEAN DEFAULT TRUE,
            last_run TIMESTAMP,
            next_run TIMESTAMP,
            run_count INTEGER DEFAULT 0,
            success_count INTEGER DEFAULT 0,
            error_count INTEGER DEFAULT 0,
            metadata JSON,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
CREATE TABLE workflow_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workflow_id INTEGER NOT NULL,
            status TEXT NOT NULL,
            started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP,
            error_message TEXT,
            result_data JSON,
            FOREIGN KEY (workflow_id) REFERENCES workflows (id)
        );
CREATE INDEX idx_workflows_active 
        ON workflows(active, user_id)
    ;
CREATE INDEX idx_workflow_runs_workflow 
        ON workflow_runs(workflow_id, started_at)
    ;
CREATE INDEX idx_metric_name_timestamp 
                ON performance_metrics(metric_name, timestamp)
            ;
CREATE INDEX idx_timestamp 
                ON performance_metrics(timestamp)
            ;
CREATE TABLE templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            category TEXT NOT NULL,
            content TEXT NOT NULL,
            description TEXT,
            placeholders JSON,
            usage_count INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
CREATE TABLE generated_pages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            page_name TEXT NOT NULL,
            template_used TEXT,
            generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            replacements JSON
        );
CREATE TABLE aider_sessions (
            session_id TEXT PRIMARY KEY,
            task_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_active TIMESTAMP,
            status TEXT DEFAULT 'active',
            files_context TEXT,
            conversation_history TEXT
        );
CREATE TABLE aider_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            role TEXT,
            content TEXT,
            code_changes TEXT,
            files_modified TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (session_id) REFERENCES aider_sessions(session_id)
        );
CREATE TABLE user_patterns (
            user_id TEXT PRIMARY KEY,
            morning_energy REAL DEFAULT 0.8,
            afternoon_energy REAL DEFAULT 0.6,
            evening_energy REAL DEFAULT 0.4,
            focus_time_preference TEXT DEFAULT 'morning',
            break_frequency_minutes INTEGER DEFAULT 25,
            work_session_length_minutes INTEGER DEFAULT 50,
            preferred_work_days TEXT DEFAULT '["monday", "tuesday", "wednesday", "thursday", "friday"]',
            preferred_work_hours TEXT DEFAULT '{"start": "09:00", "end": "17:00"}',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
CREATE TABLE scheduled_tasks (
            id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            scheduled_start TEXT NOT NULL,
            scheduled_end TEXT NOT NULL,
            actual_start TEXT,
            actual_end TEXT,
            status TEXT DEFAULT 'scheduled',  -- scheduled, in_progress, completed, cancelled
            energy_level TEXT DEFAULT 'medium',
            task_type TEXT DEFAULT 'focus',
            priority INTEGER DEFAULT 3,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
CREATE TABLE time_slots (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT NOT NULL,
            duration_minutes INTEGER NOT NULL,
            energy_level TEXT NOT NULL,
            task_type TEXT NOT NULL,
            priority INTEGER NOT NULL,
            available BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
CREATE TABLE matrix_users (
            user_id TEXT PRIMARY KEY,
            display_name TEXT NOT NULL,
            avatar_url TEXT,
            is_household BOOLEAN DEFAULT TRUE,
            is_zoe_bot BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_seen TIMESTAMP
        );
CREATE TABLE matrix_rooms (
            room_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            room_type TEXT NOT NULL,
            members TEXT NOT NULL,  -- JSON array
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_activity TIMESTAMP
        );
CREATE TABLE matrix_messages (
            message_id TEXT PRIMARY KEY,
            room_id TEXT NOT NULL,
            sender TEXT NOT NULL,
            content TEXT NOT NULL,
            message_type TEXT DEFAULT 'text',
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            encrypted BOOLEAN DEFAULT TRUE,
            reply_to TEXT,
            FOREIGN KEY (room_id) REFERENCES matrix_rooms(room_id)
        );
CREATE TABLE snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    snapshot_id TEXT UNIQUE NOT NULL,
                    created_at TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    encrypted BOOLEAN NOT NULL,
                    compression_ratio REAL,
                    checksum TEXT NOT NULL,
                    status TEXT NOT NULL,
                    backup_path TEXT NOT NULL,
                    metadata TEXT
                );
CREATE TABLE snapshot_config (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
CREATE INDEX idx_sessions_user_id 
                    ON sessions(user_id)
                ;
CREATE INDEX idx_sessions_expires_at 
                    ON sessions(expires_at)
                ;
