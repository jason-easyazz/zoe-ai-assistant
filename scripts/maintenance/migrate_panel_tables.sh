#!/usr/bin/env bash
# ============================================================
# Zoe database migration: panel presence platform tables
# ============================================================
# Safe to run multiple times (uses CREATE TABLE IF NOT EXISTS).
#
# DRY-RUN BY DEFAULT (scripts/AGENTS.md: destructive maintenance
# scripts preview first, mutate only with the explicit flag) — the
# default run prints the SQL and the drop candidate and changes
# nothing. --execute creates a timestamped backup, then applies.
#
# Usage:  bash scripts/maintenance/migrate_panel_tables.sh [--execute]
# ============================================================

set -euo pipefail

EXECUTE=0
case "${1:-}" in
    --execute) EXECUTE=1 ;;
    --dry-run|"") ;;  # dry-run is the default; the flag is an accepted no-op
    *)
        echo "Usage: $0 [--execute]   (default: dry-run preview, no changes)"
        exit 1
        ;;
esac

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

-- panel_presence_events is retired (alembic 0028 drops it on Postgres, but
-- that chain never runs against SQLite, so existing SQLite DBs keep it
-- forever otherwise). The pre-apply backup preserves its rows.
DROP TABLE IF EXISTS panel_presence_events;
SQL

echo "==> Migration SQL prepared"

# --- Candidate list (printed in BOTH modes, before anything mutates) -----------
# The only destructive statement is the DROP; show exactly what it would take.
CANDIDATE_ROWS=$(sqlite3 "$DB_PATH" \
    "SELECT COUNT(*) FROM panel_presence_events;" 2>/dev/null \
    || echo "absent")
if [ "$CANDIDATE_ROWS" = "absent" ]; then
    echo "==> Drop candidate: panel_presence_events — not present (DROP is a no-op)"
else
    echo "==> Drop candidate: panel_presence_events — $CANDIDATE_ROWS row(s)"
fi

if [ "$EXECUTE" -ne 1 ]; then
    echo "==> DRY RUN (default) — no changes made. SQL that --execute would apply:"
    cat "$SQL_FILE"
    echo ""
    echo "==> Re-run with --execute to back up and apply."
    exit 0
fi

# --- Backup (WAL-safe), then apply ---------------------------------------------
# The DB runs journal_mode=WAL (set above, and by the service), so committed
# rows can live only in the -wal sidecar — a bare `cp` of the main file
# produces a backup missing those rows. `.backup` uses SQLite's online backup
# API, which reads through the WAL into one consistent standalone file.
BACKUP="${DB_PATH}.pre-panel-migration.$(date +%Y%m%d_%H%M%S)"
echo "==> Backing up to $BACKUP (sqlite3 .backup — WAL-safe)"
sqlite3 "$DB_PATH" ".backup '$BACKUP'"
echo "    Backup created: $(du -sh "$BACKUP" | cut -f1)"

echo "==> Applying migration..."
sqlite3 "$DB_PATH" < "$SQL_FILE"

echo "==> Verifying tables..."
for tbl in panels device_tokens panel_auth_challenges; do
    ROWS=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM $tbl;" 2>/dev/null || echo "ERROR")
    echo "    $tbl: $ROWS row(s)"
done

echo "==> Dropped retired table panel_presence_events (if it existed;"
echo "    rows preserved in the pre-migration backup)."

echo ""
echo "==> Migration complete."
echo "    Backup available at: $BACKUP"
echo "    To rollback: cp '$BACKUP' '$DB_PATH'"
