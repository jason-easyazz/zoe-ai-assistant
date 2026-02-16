-- Trust Gate Schema
-- Phase 0: Security foundation for the OpenClaw adoption plan.
-- Separates READ (anyone) from ACT (allowlisted only) to prevent
-- prompt injection attacks.

-- Per-user allowlist of trusted contacts/sources
CREATE TABLE IF NOT EXISTS trust_allowlist (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    contact_type TEXT NOT NULL,        -- phone, email, telegram_id, discord_id, whatsapp, service
    contact_value TEXT NOT NULL,        -- "+44123456789", "wife@gmail.com", "n8n-webhook"
    label TEXT NOT NULL DEFAULT '',     -- Human-friendly name: "Wife", "Work email"
    permissions TEXT NOT NULL DEFAULT '["all"]',  -- JSON array: ["smart_home", "memory", "workflows", "research", "all"]
    active INTEGER NOT NULL DEFAULT 1, -- 1=active, 0=disabled (soft-delete)
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(user_id, contact_type, contact_value)
);

CREATE INDEX IF NOT EXISTS idx_trust_allowlist_user
    ON trust_allowlist(user_id, active);

CREATE INDEX IF NOT EXISTS idx_trust_allowlist_lookup
    ON trust_allowlist(contact_type, contact_value, active);

-- Audit log of Trust Gate decisions
CREATE TABLE IF NOT EXISTS trust_gate_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    timestamp TEXT NOT NULL DEFAULT (datetime('now')),
    source_type TEXT NOT NULL,          -- phone, email, telegram_id, web, internal
    source_value TEXT NOT NULL,         -- The sender identity
    channel TEXT NOT NULL DEFAULT 'web', -- web, whatsapp, telegram, email, webhook
    content_summary TEXT,               -- Truncated first 200 chars of content
    decision TEXT NOT NULL,             -- ALLOWED, BLOCKED, READ_ONLY
    reason TEXT,                        -- "Allowlisted with 'all' permissions", "Sender not on allowlist"
    action_requested TEXT,              -- What action was attempted: "smart_home.turn_on", "workflows.execute"
    permissions_matched TEXT            -- JSON: which permissions matched (if allowed)
);

CREATE INDEX IF NOT EXISTS idx_trust_gate_audit_user
    ON trust_gate_audit(user_id, timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_trust_gate_audit_decision
    ON trust_gate_audit(decision, timestamp DESC);
