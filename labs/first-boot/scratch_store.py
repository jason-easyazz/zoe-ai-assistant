"""The scratch store's schema: only the tables and columns ``first_boot_probe`` reads, in SQLite (lab only).

Column names follow the real migrations (0001 auth_users / panels / device_tokens / reminders / lists / push_subscriptions /
speaker_profiles, 0024 face_profiles, 0026 rooms, 0035 member_modes, 0046 enrollment_offers, 0060 phone_devices, 0078
connected_accounts; zoe-auth passcodes); unused columns are left out. Column ORDER matters to sim.py's positional INSERTs.
"""

SCHEMA = (
    "CREATE TABLE auth_users (user_id TEXT PRIMARY KEY, username TEXT, role TEXT NOT NULL, password_hash TEXT, created_at TEXT, "
    "is_active INTEGER DEFAULT 1)",
    "CREATE TABLE passcodes (user_id TEXT, is_active INTEGER DEFAULT 1)",
    "CREATE TABLE panels (panel_id TEXT PRIMARY KEY, name TEXT NOT NULL, room_id TEXT, is_active INTEGER DEFAULT 1)",
    "CREATE TABLE device_tokens (panel_id TEXT, revoked INTEGER DEFAULT 0)",
    "CREATE TABLE rooms (id TEXT PRIMARY KEY, name TEXT, slug TEXT)",
    "CREATE TABLE speaker_profiles (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, sample_count INTEGER DEFAULT 0, enrolled_at TEXT, "
    "consent_at TEXT, model_id TEXT)",
    "CREATE TABLE face_profiles (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, active INTEGER DEFAULT 1)",
    "CREATE TABLE enrollment_offers (user_id TEXT NOT NULL, modality TEXT NOT NULL, last_offered_at TEXT, offer_count INTEGER "
    "NOT NULL DEFAULT 0, pending_since TEXT, ignored_count INTEGER NOT NULL DEFAULT 0, accepted_at TEXT, declined_at TEXT, "
    "declined_until TEXT, updated_at TEXT NOT NULL, PRIMARY KEY (user_id, modality))",
    "CREATE TABLE push_subscriptions (user_id TEXT, endpoint TEXT)",
    "CREATE TABLE user_preferences (user_id TEXT PRIMARY KEY, prefs TEXT)",
    "CREATE TABLE phone_devices (id TEXT PRIMARY KEY, user_id TEXT, revoked INTEGER DEFAULT 0)",
    "CREATE TABLE reminders (id TEXT PRIMARY KEY, user_id TEXT, is_active INTEGER DEFAULT 1)",
    "CREATE TABLE lists (id TEXT PRIMARY KEY, user_id TEXT, deleted INTEGER DEFAULT 0)",
    "CREATE TABLE member_modes (user_id TEXT PRIMARY KEY, mode TEXT, minor INTEGER DEFAULT 0)",
    "CREATE TABLE connected_accounts (id TEXT PRIMARY KEY, user_id TEXT, kind TEXT, status TEXT)",
)
