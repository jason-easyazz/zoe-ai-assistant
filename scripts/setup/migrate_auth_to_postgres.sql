-- Zoe Auth PostgreSQL DDL Migration
-- Run as: docker exec zoe-database psql -U zoe -d zoe -f /path/to/migrate_auth_to_postgres.sql

CREATE TABLE IF NOT EXISTS auth_users (
    user_id TEXT PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    email TEXT,
    password_hash TEXT,
    role TEXT DEFAULT 'user',
    is_active SMALLINT DEFAULT 1,
    is_verified SMALLINT DEFAULT 1,
    failed_login_attempts INTEGER DEFAULT 0,
    locked_until TEXT,
    settings TEXT,
    last_login TEXT,
    created_at TEXT,
    updated_at TEXT
);

ALTER TABLE auth_users ADD COLUMN IF NOT EXISTS is_active SMALLINT DEFAULT 1;
ALTER TABLE auth_users ADD COLUMN IF NOT EXISTS is_verified SMALLINT DEFAULT 1;
ALTER TABLE auth_users ADD COLUMN IF NOT EXISTS failed_login_attempts INTEGER DEFAULT 0;
ALTER TABLE auth_users ADD COLUMN IF NOT EXISTS locked_until TEXT;
ALTER TABLE auth_users ADD COLUMN IF NOT EXISTS settings TEXT;

CREATE TABLE IF NOT EXISTS auth_sessions (
    session_id TEXT PRIMARY KEY,
    user_id TEXT,
    session_type TEXT,
    auth_method TEXT,
    device_info TEXT,
    created_at TEXT,
    last_activity TEXT,
    expires_at TEXT,
    is_active SMALLINT DEFAULT 1,
    permissions_cache TEXT,
    role_cache TEXT,
    metadata TEXT
);

CREATE TABLE IF NOT EXISTS passcodes (
    user_id TEXT PRIMARY KEY,
    passcode_hash TEXT NOT NULL,
    algorithm TEXT DEFAULT 'argon2',
    salt TEXT,
    created_at TEXT,
    expires_at TEXT,
    failed_attempts INTEGER DEFAULT 0,
    max_attempts INTEGER DEFAULT 5,
    is_active SMALLINT DEFAULT 1,
    last_used TEXT
);

CREATE TABLE IF NOT EXISTS passcode_history (
    id SERIAL PRIMARY KEY,
    user_id TEXT NOT NULL,
    passcode_hash TEXT NOT NULL,
    salt TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS password_history (
    id SERIAL PRIMARY KEY,
    user_id TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS roles (
    role_id TEXT PRIMARY KEY,
    name TEXT,
    description TEXT,
    permissions TEXT,
    inherits_from TEXT,
    is_system SMALLINT DEFAULT 0,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS permissions (
    permission_id TEXT PRIMARY KEY,
    name TEXT,
    description TEXT,
    resource TEXT,
    action TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS audit_logs (
    log_id TEXT PRIMARY KEY,
    user_id TEXT,
    action TEXT,
    resource TEXT,
    result TEXT,
    ip_address TEXT,
    user_agent TEXT,
    details TEXT,
    timestamp TEXT
);

CREATE TABLE IF NOT EXISTS panels (
    panel_id TEXT PRIMARY KEY,
    name TEXT,
    allow_guest SMALLINT DEFAULT 1,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS panel_user_bindings (
    id SERIAL PRIMARY KEY,
    panel_id TEXT,
    user_id TEXT,
    binding_type TEXT
);

CREATE TABLE IF NOT EXISTS rate_limits (
    id SERIAL PRIMARY KEY,
    key TEXT,
    attempts INTEGER,
    window_start TEXT
);

CREATE TABLE IF NOT EXISTS guest_codes (
    id SERIAL PRIMARY KEY,
    code TEXT,
    created_by TEXT,
    expires_at TEXT,
    used SMALLINT DEFAULT 0
);

CREATE TABLE IF NOT EXISTS oauth_states (
    state TEXT PRIMARY KEY,
    data TEXT,
    created_at TEXT,
    expires_at TEXT
);

CREATE TABLE IF NOT EXISTS oauth_device_codes (
    device_code TEXT PRIMARY KEY,
    user_code TEXT,
    data TEXT,
    created_at TEXT,
    expires_at TEXT
);

CREATE TABLE IF NOT EXISTS service_accounts (
    id TEXT PRIMARY KEY,
    name TEXT,
    key_hash TEXT,
    permissions TEXT,
    created_at TEXT,
    is_active SMALLINT DEFAULT 1
);

CREATE TABLE IF NOT EXISTS api_keys (
    id SERIAL PRIMARY KEY,
    user_id TEXT,
    key_hash TEXT,
    permissions TEXT,
    is_active SMALLINT DEFAULT 1,
    created_at TEXT,
    expires_at TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    user_id TEXT,
    created_at TEXT,
    expires_at TEXT,
    data TEXT
);

CREATE TABLE IF NOT EXISTS oidc_clients (
    client_id         TEXT PRIMARY KEY,
    client_secret_hash TEXT NOT NULL,
    client_name       TEXT NOT NULL,
    redirect_uris     TEXT NOT NULL,
    scopes            TEXT NOT NULL DEFAULT 'openid profile email',
    is_active         BOOLEAN NOT NULL DEFAULT TRUE,
    created_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS oidc_signing_keys (
    kid             TEXT PRIMARY KEY,
    algorithm       TEXT NOT NULL DEFAULT 'RS256',
    private_key_pem TEXT NOT NULL,
    public_key_pem  TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE
);
