"""Initial schema — all tables ported to PostgreSQL DDL

Revision ID: 0001
Revises:
Create Date: 2026-05-16
"""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'member',
    pin_hash TEXT,
    created_at TEXT NOT NULL DEFAULT NOW()::TEXT,
    updated_at TEXT NOT NULL DEFAULT NOW()::TEXT
)
""")

    op.execute("""
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
    created_at TEXT NOT NULL DEFAULT NOW()::TEXT,
    updated_at TEXT NOT NULL DEFAULT NOW()::TEXT,
    FOREIGN KEY (user_id) REFERENCES users(id)
)
""")

    op.execute("""
CREATE TABLE IF NOT EXISTS lists (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    name TEXT NOT NULL,
    list_type TEXT NOT NULL DEFAULT 'shopping',
    description TEXT,
    visibility TEXT NOT NULL DEFAULT 'family',
    deleted INTEGER DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT NOW()::TEXT,
    updated_at TEXT NOT NULL DEFAULT NOW()::TEXT,
    FOREIGN KEY (user_id) REFERENCES users(id)
)
""")

    op.execute("""
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
    created_at TEXT NOT NULL DEFAULT NOW()::TEXT,
    updated_at TEXT NOT NULL DEFAULT NOW()::TEXT,
    FOREIGN KEY (list_id) REFERENCES lists(id) ON DELETE CASCADE,
    FOREIGN KEY (assigned_to) REFERENCES users(id)
)
""")

    op.execute("""
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
    created_at TEXT NOT NULL DEFAULT NOW()::TEXT,
    updated_at TEXT NOT NULL DEFAULT NOW()::TEXT,
    FOREIGN KEY (user_id) REFERENCES users(id)
)
""")

    op.execute("""
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
    created_at TEXT NOT NULL DEFAULT NOW()::TEXT,
    updated_at TEXT NOT NULL DEFAULT NOW()::TEXT
)
""")

    op.execute("""
CREATE TABLE IF NOT EXISTS people_field_values (
    id TEXT PRIMARY KEY,
    person_id TEXT NOT NULL,
    field_key TEXT NOT NULL,
    value_json TEXT,
    updated_at TEXT NOT NULL DEFAULT NOW()::TEXT,
    created_at TEXT NOT NULL DEFAULT NOW()::TEXT,
    UNIQUE(person_id, field_key),
    FOREIGN KEY (person_id) REFERENCES people(id) ON DELETE CASCADE
)
""")

    op.execute("""
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
    created_at TEXT NOT NULL DEFAULT NOW()::TEXT,
    updated_at TEXT NOT NULL DEFAULT NOW()::TEXT,
    FOREIGN KEY (user_id) REFERENCES users(id)
)
""")

    op.execute("""
CREATE TABLE IF NOT EXISTS notes (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    title TEXT,
    content TEXT NOT NULL,
    category TEXT DEFAULT 'general',
    tags TEXT,
    visibility TEXT NOT NULL DEFAULT 'personal',
    deleted INTEGER DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT NOW()::TEXT,
    updated_at TEXT NOT NULL DEFAULT NOW()::TEXT,
    FOREIGN KEY (user_id) REFERENCES users(id)
)
""")

    op.execute("""
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
    created_at TEXT NOT NULL DEFAULT NOW()::TEXT,
    updated_at TEXT NOT NULL DEFAULT NOW()::TEXT,
    FOREIGN KEY (user_id) REFERENCES users(id)
)
""")

    op.execute("""
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
    created_at TEXT NOT NULL DEFAULT NOW()::TEXT,
    updated_at TEXT NOT NULL DEFAULT NOW()::TEXT,
    FOREIGN KEY (user_id) REFERENCES users(id)
)
""")

    op.execute("""
CREATE TABLE IF NOT EXISTS weather_preferences (
    user_id TEXT PRIMARY KEY,
    latitude REAL,
    longitude REAL,
    city TEXT,
    country TEXT,
    temperature_unit TEXT DEFAULT 'celsius',
    use_current_location INTEGER DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT NOW()::TEXT,
    FOREIGN KEY (user_id) REFERENCES users(id)
)
""")

    op.execute("""
CREATE TABLE IF NOT EXISTS system_preferences (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_by TEXT,
    updated_at TEXT NOT NULL DEFAULT NOW()::TEXT
)
""")

    op.execute("""
CREATE TABLE IF NOT EXISTS display_preferences (
    device_id TEXT PRIMARY KEY,
    enabled INTEGER DEFAULT 1,
    day_brightness INTEGER DEFAULT 100,
    night_enabled INTEGER DEFAULT 1,
    night_start TEXT DEFAULT '22:00',
    night_end TEXT DEFAULT '06:30',
    night_brightness INTEGER DEFAULT 15,
    idle_enabled INTEGER DEFAULT 1,
    idle_seconds INTEGER DEFAULT 120,
    idle_brightness INTEGER DEFAULT 30,
    off_enabled INTEGER DEFAULT 1,
    off_seconds INTEGER DEFAULT 900,
    pi_host TEXT,
    updated_at TEXT NOT NULL DEFAULT NOW()::TEXT
)
""")

    op.execute("""
CREATE TABLE IF NOT EXISTS update_history (
    id SERIAL PRIMARY KEY,
    component TEXT NOT NULL,
    version_before TEXT,
    version_after TEXT,
    ok INTEGER NOT NULL DEFAULT 0,
    log_excerpt TEXT,
    initiated_by TEXT,
    created_at TEXT NOT NULL DEFAULT NOW()::TEXT
)
""")

    op.execute("""
CREATE TABLE IF NOT EXISTS trust_allowlist (
    id TEXT PRIMARY KEY,
    tool_pattern TEXT NOT NULL,
    description TEXT,
    added_by TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT NOW()::TEXT
)
""")

    op.execute("""
CREATE TABLE IF NOT EXISTS trust_audit (
    id SERIAL PRIMARY KEY,
    tool_name TEXT NOT NULL,
    action TEXT NOT NULL,
    user_id TEXT,
    allowed INTEGER NOT NULL,
    reason TEXT,
    created_at TEXT NOT NULL DEFAULT NOW()::TEXT
)
""")

    op.execute("""
CREATE TABLE IF NOT EXISTS notifications (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    type TEXT NOT NULL,
    title TEXT,
    message TEXT,
    data TEXT,
    delivered INTEGER DEFAULT 0,
    action_taken TEXT,
    created_at TEXT NOT NULL DEFAULT NOW()::TEXT,
    FOREIGN KEY (user_id) REFERENCES users(id)
)
""")

    op.execute("""
CREATE TABLE IF NOT EXISTS chat_sessions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT 'New Chat',
    created_at TEXT NOT NULL DEFAULT NOW()::TEXT,
    updated_at TEXT NOT NULL DEFAULT NOW()::TEXT,
    FOREIGN KEY (user_id) REFERENCES users(id)
)
""")

    op.execute("""
CREATE TABLE IF NOT EXISTS chat_messages (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    metadata TEXT,
    created_at TEXT NOT NULL DEFAULT NOW()::TEXT,
    FOREIGN KEY (session_id) REFERENCES chat_sessions(id) ON DELETE CASCADE
)
""")

    op.execute("""
CREATE TABLE IF NOT EXISTS dashboard_layouts (
    user_id TEXT PRIMARY KEY,
    layout JSONB NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
""")

    op.execute("""
CREATE TABLE IF NOT EXISTS user_preferences (
    user_id TEXT PRIMARY KEY,
    prefs TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL DEFAULT NOW()::TEXT,
    FOREIGN KEY (user_id) REFERENCES users(id)
)
""")

    op.execute("""
CREATE TABLE IF NOT EXISTS chat_ag_ui_runs (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    events TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT NOW()::TEXT,
    FOREIGN KEY (session_id) REFERENCES chat_sessions(id) ON DELETE CASCADE
)
""")

    op.execute("""
CREATE TABLE IF NOT EXISTS openclaw_run_state (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    mode TEXT NOT NULL DEFAULT 'chat',
    status TEXT NOT NULL DEFAULT 'running',
    request_text TEXT,
    response_text TEXT,
    metadata TEXT,
    started_at TEXT NOT NULL DEFAULT NOW()::TEXT,
    finished_at TEXT
)
""")

    op.execute("""
CREATE TABLE IF NOT EXISTS openclaw_approvals (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    request_text TEXT NOT NULL,
    normalized_action TEXT,
    risk_level TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    reason TEXT,
    created_at TEXT NOT NULL DEFAULT NOW()::TEXT,
    resolved_at TEXT
)
""")

    op.execute("""
CREATE TABLE IF NOT EXISTS user_portraits (
    user_id TEXT PRIMARY KEY,
    portrait_text TEXT NOT NULL,
    portrait_version INTEGER DEFAULT 1,
    generated_from_memory_count INTEGER DEFAULT 0,
    last_generated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id)
)
""")

    op.execute("""
CREATE TABLE IF NOT EXISTS background_tasks (
    id SERIAL PRIMARY KEY,
    user_id TEXT NOT NULL,
    session_id TEXT,
    panel_id TEXT,
    task TEXT NOT NULL,
    status TEXT DEFAULT 'pending',
    result TEXT,
    seen INTEGER DEFAULT 0,
    created_at TEXT,
    completed_at TEXT
)
""")

    op.execute("""
CREATE TABLE IF NOT EXISTS open_loops (
    id SERIAL PRIMARY KEY,
    user_id TEXT NOT NULL,
    loop_text TEXT NOT NULL,
    context TEXT,
    follow_up_hint TEXT,
    emotional_weight INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    follow_up_after TIMESTAMP,
    resolved BOOLEAN DEFAULT FALSE,
    resolved_at TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id)
)
""")

    op.execute("""
CREATE TABLE IF NOT EXISTS push_subscriptions (
    user_id TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    keys_p256dh TEXT NOT NULL,
    keys_auth TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, endpoint)
)
""")

    op.execute("""
CREATE TABLE IF NOT EXISTS chat_feedback (
    id TEXT PRIMARY KEY,
    interaction_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    feedback_type TEXT NOT NULL,
    corrected_response TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
""")

    op.execute("""
CREATE TABLE IF NOT EXISTS ui_panel_sessions (
    panel_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    chat_session_id TEXT,
    page TEXT,
    ui_context TEXT,
    is_foreground INTEGER DEFAULT 1,
    last_seen_at TEXT NOT NULL DEFAULT NOW()::TEXT,
    created_at TEXT NOT NULL DEFAULT NOW()::TEXT,
    updated_at TEXT NOT NULL DEFAULT NOW()::TEXT
)
""")

    op.execute("""
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
    created_at TEXT NOT NULL DEFAULT NOW()::TEXT,
    updated_at TEXT NOT NULL DEFAULT NOW()::TEXT,
    acked_at TEXT
)
""")

    op.execute("""
CREATE TABLE IF NOT EXISTS ui_action_ledger (
    id TEXT PRIMARY KEY,
    action_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    panel_id TEXT,
    event_type TEXT NOT NULL,
    event_data TEXT,
    created_at TEXT NOT NULL DEFAULT NOW()::TEXT,
    FOREIGN KEY (action_id) REFERENCES ui_actions(id) ON DELETE CASCADE
)
""")

    op.execute("""
CREATE TABLE IF NOT EXISTS panels (
    panel_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT,
    ip_address TEXT,
    panel_type TEXT DEFAULT 'kiosk',
    os TEXT,
    notes TEXT,
    is_active INTEGER DEFAULT 1,
    allow_guest INTEGER NOT NULL DEFAULT 1,
    last_seen_at TEXT,
    created_at TEXT NOT NULL DEFAULT NOW()::TEXT,
    updated_at TEXT NOT NULL DEFAULT NOW()::TEXT
)
""")

    op.execute("""
CREATE TABLE IF NOT EXISTS panel_user_bindings (
    panel_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    binding_type TEXT NOT NULL DEFAULT 'allowed',
    priority INTEGER DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT NOW()::TEXT,
    PRIMARY KEY (panel_id, user_id),
    FOREIGN KEY (panel_id) REFERENCES panels(panel_id) ON DELETE CASCADE
)
""")

    op.execute("""
CREATE TABLE IF NOT EXISTS device_tokens (
    id TEXT PRIMARY KEY,
    panel_id TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'voice-daemon',
    scopes TEXT DEFAULT '["voice"]',
    expires_at TEXT,
    revoked INTEGER DEFAULT 0,
    revoked_at TEXT,
    created_at TEXT NOT NULL DEFAULT NOW()::TEXT,
    FOREIGN KEY (panel_id) REFERENCES panels(panel_id) ON DELETE CASCADE
)
""")

    op.execute("""
CREATE TABLE IF NOT EXISTS panel_presence_events (
    id TEXT PRIMARY KEY,
    panel_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload TEXT,
    confidence REAL,
    created_at TEXT NOT NULL DEFAULT NOW()::TEXT,
    FOREIGN KEY (panel_id) REFERENCES panels(panel_id) ON DELETE CASCADE
)
""")

    op.execute("""
CREATE TABLE IF NOT EXISTS panel_auth_challenges (
    challenge_id TEXT PRIMARY KEY,
    panel_id TEXT NOT NULL,
    user_id TEXT,
    action_context TEXT,
    pin_hash TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    expires_at TEXT NOT NULL,
    resolved_at TEXT,
    created_at TEXT NOT NULL DEFAULT NOW()::TEXT
)
""")

    op.execute("""
CREATE TABLE IF NOT EXISTS role_capability_matrix (
    role TEXT PRIMARY KEY,
    matrix_json TEXT NOT NULL,
    updated_by TEXT,
    created_at TEXT NOT NULL DEFAULT NOW()::TEXT,
    updated_at TEXT NOT NULL DEFAULT NOW()::TEXT
)
""")

    op.execute("""
CREATE TABLE IF NOT EXISTS ambient_memory (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    panel_id TEXT,
    room TEXT,
    transcript TEXT NOT NULL,
    speaker_id TEXT,
    duration_seconds REAL,
    source TEXT DEFAULT 'ambient',
    embedding BYTEA
)
""")

    op.execute("""
CREATE TABLE IF NOT EXISTS speaker_profiles (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    display_name TEXT NOT NULL,
    embedding_blob BYTEA NOT NULL,
    enrolled_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    sample_count INTEGER DEFAULT 0,
    panel_id TEXT,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
)
""")

    op.execute("""
CREATE TABLE IF NOT EXISTS proactive_pending (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    message TEXT NOT NULL,
    trigger_type TEXT NOT NULL,
    item_id TEXT DEFAULT '',
    trigger_context TEXT DEFAULT '{}',
    expires_at TEXT NOT NULL,
    claimed INTEGER DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT NOW()::TEXT,
    FOREIGN KEY (user_id) REFERENCES users(id)
)
""")

    op.execute("""
CREATE TABLE IF NOT EXISTS proactive_scheduled (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    message TEXT NOT NULL,
    trigger_type TEXT DEFAULT 'scheduled',
    send_at TEXT NOT NULL,
    apscheduler_job_id TEXT,
    fired INTEGER DEFAULT 0,
    item_id TEXT DEFAULT '',
    created_at TEXT NOT NULL DEFAULT NOW()::TEXT,
    FOREIGN KEY (user_id) REFERENCES users(id)
)
""")

    op.execute("""
CREATE TABLE IF NOT EXISTS music_listening_events (
    id SERIAL PRIMARY KEY,
    user_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    track_title TEXT,
    artist TEXT,
    album TEXT,
    genre TEXT,
    source TEXT,
    query TEXT,
    volume_level INTEGER,
    session_id TEXT,
    ts REAL NOT NULL,
    percent_played REAL,
    duration_seconds REAL,
    created_at TEXT DEFAULT NOW()::TEXT
)
""")

    # Indexes
    op.execute("CREATE INDEX IF NOT EXISTS idx_chat_sessions_user ON chat_sessions(user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_chat_messages_session ON chat_messages(session_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_chat_ag_ui_runs_session ON chat_ag_ui_runs(session_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_openclaw_run_state_session ON openclaw_run_state(session_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_openclaw_approvals_user_status ON openclaw_approvals(user_id, status)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_events_date ON events(start_date)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_events_user ON events(user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_list_items_list ON list_items(list_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders(due_date)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_reminders_user ON reminders(user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_notes_user ON notes(user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_journal_user ON journal_entries(user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_journal_date ON journal_entries(created_at)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_transactions_date ON transactions(transaction_date)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_people_field_values_person ON people_field_values(person_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_feedback_interaction ON chat_feedback(interaction_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_ui_panel_user ON ui_panel_sessions(user_id)")
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_ui_actions_idempotency
        ON ui_actions(user_id, idempotency_key)
        WHERE idempotency_key IS NOT NULL
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_ui_actions_status ON ui_actions(status)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_ui_actions_user_panel ON ui_actions(user_id, panel_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_ui_ledger_action ON ui_action_ledger(action_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_panel_user_bindings_panel ON panel_user_bindings(panel_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_panel_user_bindings_type ON panel_user_bindings(panel_id, binding_type)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_device_tokens_panel ON device_tokens(panel_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_device_tokens_hash ON device_tokens(token_hash)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_presence_panel_time ON panel_presence_events(panel_id, created_at)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_challenges_panel ON panel_auth_challenges(panel_id, status)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_ambient_panel_time ON ambient_memory(panel_id, timestamp)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_ambient_speaker ON ambient_memory(speaker_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_speaker_profiles_user ON speaker_profiles(user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_proactive_pending_user ON proactive_pending(user_id, claimed)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_proactive_pending_expires ON proactive_pending(expires_at)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_proactive_scheduled_user ON proactive_scheduled(user_id, fired)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_mle_user_ts ON music_listening_events(user_id, ts)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_mle_user_event ON music_listening_events(user_id, event_type)")


def downgrade() -> None:
    tables = [
        "music_listening_events", "proactive_scheduled", "proactive_pending",
        "speaker_profiles", "ambient_memory", "role_capability_matrix",
        "panel_auth_challenges", "panel_presence_events", "device_tokens",
        "panel_user_bindings", "panels", "ui_action_ledger", "ui_actions",
        "ui_panel_sessions", "chat_feedback", "push_subscriptions",
        "open_loops", "background_tasks", "user_portraits", "openclaw_approvals",
        "openclaw_run_state", "chat_ag_ui_runs", "user_preferences",
        "dashboard_layouts", "chat_messages", "chat_sessions", "notifications",
        "trust_audit", "trust_allowlist", "update_history", "display_preferences",
        "system_preferences", "weather_preferences", "transactions",
        "journal_entries", "notes", "reminders", "people_field_values",
        "people_field_definitions", "people", "list_items", "lists",
        "events", "users",
    ]
    for t in tables:
        op.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
