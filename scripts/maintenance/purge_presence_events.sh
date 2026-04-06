#!/usr/bin/env bash
# purge_presence_events.sh
# Delete panel_presence_events rows older than 30 days.
# Safe to run as a systemd timer or cron job.
set -euo pipefail

DB="${ZOE_DB_PATH:-/home/zoe/zoe-data/data/zoe.db}"
RETAIN_DAYS="${ZOE_PRESENCE_RETAIN_DAYS:-30}"

if [ ! -f "$DB" ]; then
    echo "[purge_presence_events] DB not found: $DB" >&2
    exit 1
fi

CUTOFF=$(date -d "-${RETAIN_DAYS} days" '+%Y-%m-%dT%H:%M:%S' 2>/dev/null \
      || date -v "-${RETAIN_DAYS}d" '+%Y-%m-%dT%H:%M:%S')

DELETED=$(python3 - <<EOF
import sqlite3, sys
db = sqlite3.connect("$DB")
cur = db.execute(
    "DELETE FROM panel_presence_events WHERE seen_at < ?",
    ("$CUTOFF",)
)
db.commit()
print(cur.rowcount)
db.close()
EOF
)

echo "[purge_presence_events] Deleted ${DELETED} rows older than ${RETAIN_DAYS} days (cutoff: ${CUTOFF})"
