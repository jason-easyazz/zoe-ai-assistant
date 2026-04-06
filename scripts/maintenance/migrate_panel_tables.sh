#!/usr/bin/env bash
# ============================================================
# Zoe database migration: panel presence platform tables
# ============================================================
# Safe to run multiple times (uses CREATE TABLE IF NOT EXISTS).
# Always creates a timestamped backup first.
#
# Usage:  bash scripts/maintenance/migrate_panel_tables.sh [--dry-run]
# ============================================================

set -euo pipefail

DRY_RUN=0
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=1

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# --- Locate the database -------------------------------------------------------
DB_PATH="${ZOE_DATA_DB:-$REPO_ROOT/services/zoe-data/zoe.db}"

if [ ! -f "$DB_PATH" ]; then
    echo "ERROR: Database not found at $DB_PATH"
    echo "Set ZOE_DATA_DB if your db is elsewhere."
    exit 1
fi

echo "==> Database: $DB_PATH"

if [ "$DRY_RUN" -eq 1 ]; then
    echo "==> DRY RUN — no changes will be made"
else
    # Backup before migration
    BACKUP="${DB_PATH}.pre-panel-migration.$(date +%Y%m%d_%H%M%S)"
    echo "==> Backing up to $BACKUP"
    cp "$DB_PATH" "$BACKUP"
    echo "    Backup created: $(du -sh "$BACKUP" | cut -f1)"
fi

# --- Migration SQL -------------------------------------------------------------
SQL_FILE="$(mktemp /tmp/zoe-migration-XXXXXX.sql)"
trap 'rm -f "$SQL_FILE"' EXIT

cat > "$SQL_FILE" << 'SQL'
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- Touch Presence Platform: registered panels (kiosks etc.)
CREATE TABLE IF NOT EXISTS panels (
    panel_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT,
    ip_address TEXT,
    panel_type TEXT DEFAULT 'kiosk',
    os TEXT,
    notes TEXT,
    is_active INTEGER DEFAULT 1,
    last_seen_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Device tokens for Pi voice / presence daemons (hashed)
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
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (panel_id) REFERENCES panels(panel_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_device_tokens_panel ON device_tokens(panel_id);
CREATE INDEX IF NOT EXISTS idx_device_tokens_hash  ON device_tokens(token_hash);

-- Panel presence events (motion, face recognition, occupancy)
CREATE TABLE IF NOT EXISTS panel_presence_events (
    id TEXT PRIMARY KEY,
    panel_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload TEXT,
    confidence REAL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (panel_id) REFERENCES panels(panel_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_presence_panel_time
    ON panel_presence_events(panel_id, created_at);

-- PIN auth challenges for high-privilege panel actions
CREATE TABLE IF NOT EXISTS panel_auth_challenges (
    challenge_id TEXT PRIMARY KEY,
    panel_id TEXT NOT NULL,
    user_id TEXT,
    action_context TEXT,
    pin_hash TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    expires_at TEXT NOT NULL,
    resolved_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_challenges_panel
    ON panel_auth_challenges(panel_id, status);
SQL

echo "==> Migration SQL prepared"

if [ "$DRY_RUN" -eq 1 ]; then
    echo "==> SQL to be executed:"
    cat "$SQL_FILE"
    echo ""
    echo "==> DRY RUN complete — run without --dry-run to apply."
    exit 0
fi

echo "==> Applying migration..."
sqlite3 "$DB_PATH" < "$SQL_FILE"

echo "==> Verifying tables..."
for tbl in panels device_tokens panel_presence_events panel_auth_challenges; do
    ROWS=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM $tbl;" 2>/dev/null || echo "ERROR")
    echo "    $tbl: $ROWS row(s)"
done

echo ""
echo "==> Migration complete."
echo "    Backup available at: $BACKUP"
echo "    To rollback: cp '$BACKUP' '$DB_PATH'"
