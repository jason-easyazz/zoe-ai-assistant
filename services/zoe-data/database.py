import aiosqlite
import os
import uuid
from contextlib import asynccontextmanager

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("ZOE_DATA_DB", os.path.join(_BASE_DIR, "zoe.db"))

async def get_db():
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA foreign_keys=ON")
    try:
        yield db
    finally:
        await db.close()

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA foreign_keys=ON")
        await db.executescript(SCHEMA)
        await db.execute(
            "INSERT OR IGNORE INTO users (id, name, role) VALUES (?, ?, ?)",
            ("family-admin", "Admin", "admin"),
        )
        default_fields = [
            ("nickname", "Nickname", "text", 0, None, "person", 10, "family"),
            ("pronouns", "Pronouns", "text", 0, None, "person", 20, "family"),
            ("address", "Address", "text", 0, None, "person", 30, "family"),
            ("company", "Company", "text", 0, None, "person", 40, "family"),
            ("job_title", "Job Title", "text", 0, None, "person", 50, "family"),
            ("social_handle", "Social Handle", "text", 0, None, "person", 60, "family"),
            ("important_dates", "Important Dates", "json", 0, None, "person", 70, "personal"),
            ("gift_preferences", "Gift Preferences", "json", 0, None, "person", 80, "personal"),
            ("communication_style", "Communication Style", "text", 0, None, "person", 90, "personal"),
            ("tags", "Tags", "array", 0, None, "person", 100, "family"),
        ]
        for item in default_fields:
            await db.execute(
                """INSERT OR IGNORE INTO people_field_definitions
                   (id, field_key, label, field_type, required, options_json, scope, sort_order, visibility)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (str(uuid.uuid4()), *item),
            )
        await db.commit()

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'member',
    pin_hash TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    title TEXT NOT NULL,
    start_date TEXT NOT NULL,
    start_time TEXT,
    end_date TEXT,
    end_time TEXT,
    duration INTEGER,
    category TEXT DEFAULT 'general',
    location TEXT,
    all_day INTEGER DEFAULT 0,
    recurring TEXT,
    metadata TEXT,
    visibility TEXT NOT NULL DEFAULT 'family',
    deleted INTEGER DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS lists (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    name TEXT NOT NULL,
    list_type TEXT NOT NULL DEFAULT 'shopping',
    description TEXT,
    visibility TEXT NOT NULL DEFAULT 'family',
    deleted INTEGER DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS list_items (
    id TEXT PRIMARY KEY,
    list_id TEXT NOT NULL,
    text TEXT NOT NULL,
    completed INTEGER DEFAULT 0,
    priority TEXT DEFAULT 'normal',
    category TEXT,
    quantity TEXT,
    sort_order INTEGER DEFAULT 0,
    parent_id TEXT,
    assigned_to TEXT,
    deleted INTEGER DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (list_id) REFERENCES lists(id) ON DELETE CASCADE,
    FOREIGN KEY (assigned_to) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS people (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    name TEXT NOT NULL,
    relationship TEXT,
    email TEXT,
    phone TEXT,
    birthday TEXT,
    notes TEXT,
    preferences TEXT,
    visibility TEXT NOT NULL DEFAULT 'family',
    deleted INTEGER DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS people_field_definitions (
    id TEXT PRIMARY KEY,
    field_key TEXT NOT NULL UNIQUE,
    label TEXT NOT NULL,
    field_type TEXT NOT NULL DEFAULT 'text',
    required INTEGER NOT NULL DEFAULT 0,
    options_json TEXT,
    scope TEXT NOT NULL DEFAULT 'person',
    sort_order INTEGER NOT NULL DEFAULT 100,
    visibility TEXT NOT NULL DEFAULT 'family',
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS people_field_values (
    id TEXT PRIMARY KEY,
    person_id TEXT NOT NULL,
    field_key TEXT NOT NULL,
    value_json TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(person_id, field_key),
    FOREIGN KEY (person_id) REFERENCES people(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS reminders (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    reminder_type TEXT DEFAULT 'one-time',
    category TEXT DEFAULT 'general',
    priority TEXT DEFAULT 'normal',
    due_date TEXT,
    due_time TEXT,
    recurring_pattern TEXT,
    is_active INTEGER DEFAULT 1,
    acknowledged INTEGER DEFAULT 0,
    snoozed_until TEXT,
    visibility TEXT NOT NULL DEFAULT 'personal',
    deleted INTEGER DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS notes (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    title TEXT,
    content TEXT NOT NULL,
    category TEXT DEFAULT 'general',
    tags TEXT,
    visibility TEXT NOT NULL DEFAULT 'personal',
    deleted INTEGER DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS journal_entries (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    title TEXT,
    content TEXT NOT NULL,
    mood TEXT,
    mood_score INTEGER,
    tags TEXT,
    weather TEXT,
    location TEXT,
    photos TEXT,
    privacy_level TEXT DEFAULT 'personal',
    visibility TEXT NOT NULL DEFAULT 'personal',
    deleted INTEGER DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS transactions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    description TEXT NOT NULL,
    amount REAL NOT NULL,
    type TEXT NOT NULL DEFAULT 'expense',
    transaction_date TEXT NOT NULL,
    payment_method TEXT,
    status TEXT DEFAULT 'completed',
    person_id TEXT,
    calendar_event_id TEXT,
    metadata TEXT,
    visibility TEXT NOT NULL DEFAULT 'family',
    deleted INTEGER DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS weather_preferences (
    user_id TEXT PRIMARY KEY,
    latitude REAL,
    longitude REAL,
    city TEXT,
    country TEXT,
    temperature_unit TEXT DEFAULT 'celsius',
    use_current_location INTEGER DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS trust_allowlist (
    id TEXT PRIMARY KEY,
    tool_pattern TEXT NOT NULL,
    description TEXT,
    added_by TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS trust_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tool_name TEXT NOT NULL,
    action TEXT NOT NULL,
    user_id TEXT,
    allowed INTEGER NOT NULL,
    reason TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS notifications (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    type TEXT NOT NULL,
    title TEXT,
    message TEXT,
    data TEXT,
    delivered INTEGER DEFAULT 0,
    action_taken TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS chat_sessions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT 'New Chat',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS chat_messages (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    metadata TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (session_id) REFERENCES chat_sessions(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS dashboard_layouts (
    user_id TEXT PRIMARY KEY,
    layout JSON NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS user_preferences (
    user_id TEXT PRIMARY KEY,
    prefs TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS chat_ag_ui_runs (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    events TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (session_id) REFERENCES chat_sessions(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS openclaw_run_state (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    mode TEXT NOT NULL DEFAULT 'chat',
    status TEXT NOT NULL DEFAULT 'running',
    request_text TEXT,
    response_text TEXT,
    metadata TEXT,
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS openclaw_approvals (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    request_text TEXT NOT NULL,
    normalized_action TEXT,
    risk_level TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    reason TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    resolved_at TEXT
);

CREATE TABLE IF NOT EXISTS memory_items (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    memory_type TEXT NOT NULL DEFAULT 'fact',
    title TEXT,
    content TEXT NOT NULL,
    entity_type TEXT,
    entity_id TEXT,
    confidence REAL NOT NULL DEFAULT 0.5,
    source_type TEXT NOT NULL DEFAULT 'manual',
    source_id TEXT,
    source_excerpt TEXT,
    provenance_json TEXT,
    visibility TEXT NOT NULL DEFAULT 'personal',
    status TEXT NOT NULL DEFAULT 'pending_review',
    observed_at TEXT NOT NULL DEFAULT (datetime('now')),
    last_verified_at TEXT,
    reviewed_by TEXT,
    reviewed_at TEXT,
    review_note TEXT,
    deleted INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS memory_links (
    id TEXT PRIMARY KEY,
    memory_id TEXT NOT NULL,
    linked_type TEXT NOT NULL,
    linked_id TEXT NOT NULL,
    link_reason TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (memory_id) REFERENCES memory_items(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS memory_audit (
    id TEXT PRIMARY KEY,
    memory_id TEXT,
    user_id TEXT NOT NULL,
    action TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT,
    reason TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_chat_sessions_user ON chat_sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_chat_messages_session ON chat_messages(session_id);
CREATE INDEX IF NOT EXISTS idx_chat_ag_ui_runs_session ON chat_ag_ui_runs(session_id);
CREATE INDEX IF NOT EXISTS idx_openclaw_run_state_session ON openclaw_run_state(session_id);
CREATE INDEX IF NOT EXISTS idx_openclaw_approvals_user_status ON openclaw_approvals(user_id, status);

CREATE INDEX IF NOT EXISTS idx_events_date ON events(start_date);
CREATE INDEX IF NOT EXISTS idx_events_user ON events(user_id);
CREATE INDEX IF NOT EXISTS idx_list_items_list ON list_items(list_id);
CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders(due_date);
CREATE INDEX IF NOT EXISTS idx_reminders_user ON reminders(user_id);
CREATE INDEX IF NOT EXISTS idx_notes_user ON notes(user_id);
CREATE INDEX IF NOT EXISTS idx_journal_user ON journal_entries(user_id);
CREATE INDEX IF NOT EXISTS idx_journal_date ON journal_entries(created_at);
CREATE INDEX IF NOT EXISTS idx_transactions_date ON transactions(transaction_date);
CREATE INDEX IF NOT EXISTS idx_people_field_values_person ON people_field_values(person_id);
CREATE INDEX IF NOT EXISTS idx_memory_items_user_status ON memory_items(user_id, status);
CREATE INDEX IF NOT EXISTS idx_memory_items_entity ON memory_items(entity_type, entity_id);

CREATE TABLE IF NOT EXISTS push_subscriptions (
    user_id TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    keys_p256dh TEXT NOT NULL,
    keys_auth TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, endpoint)
);

CREATE TABLE IF NOT EXISTS chat_feedback (
    id TEXT PRIMARY KEY,
    interaction_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    feedback_type TEXT NOT NULL,
    corrected_response TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_feedback_interaction ON chat_feedback(interaction_id);

CREATE TABLE IF NOT EXISTS ui_panel_sessions (
    panel_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    chat_session_id TEXT,
    page TEXT,
    ui_context TEXT,
    is_foreground INTEGER DEFAULT 1,
    last_seen_at TEXT NOT NULL DEFAULT (datetime('now')),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_ui_panel_user ON ui_panel_sessions(user_id);

CREATE TABLE IF NOT EXISTS ui_actions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    panel_id TEXT,
    chat_session_id TEXT,
    idempotency_key TEXT,
    action_type TEXT NOT NULL,
    payload TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    requires_confirmation INTEGER DEFAULT 0,
    confirmation_token TEXT,
    retry_count INTEGER DEFAULT 0,
    max_retries INTEGER DEFAULT 2,
    error_code TEXT,
    error_message TEXT,
    requested_by TEXT NOT NULL DEFAULT 'system',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    acked_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_ui_actions_idempotency
    ON ui_actions(user_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_ui_actions_status ON ui_actions(status);
CREATE INDEX IF NOT EXISTS idx_ui_actions_user_panel ON ui_actions(user_id, panel_id);

CREATE TABLE IF NOT EXISTS ui_action_ledger (
    id TEXT PRIMARY KEY,
    action_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    panel_id TEXT,
    event_type TEXT NOT NULL,
    event_data TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (action_id) REFERENCES ui_actions(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_ui_ledger_action ON ui_action_ledger(action_id);
"""
