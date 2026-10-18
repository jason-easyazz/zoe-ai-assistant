#!/usr/bin/env bash
#
# Weekly restore verification (zoe-backup-verify.timer; tracked 2026-10-18 - was ~/scripts/maintenance/).
#
# 1. ON-BOX, weekly: picks the most recent MemPalace tarball + Postgres dumps,
#    restores them into scratch locations, and checks that each restored database
#    has expected rows.
# 2. OFF-BOX, monthly (architecture deep dive A5): `zoe_offbox.py drill`
#    restores the newest snapshot of every tier from the411 into a scratch dir,
#    compares every file's sha256 with what this box sent, reads a twelfth of the
#    repo's data, and checks the household tier ages out within 35 days. It gates
#    itself (once per 28 days) and alerts on its own failure. Skipped, with a
#    line, while the off-box backup is not configured.
#
# Any failure exits non-zero AND sends an alert (Telegram, via the crash-loop
# watcher's sender) - a weekly check that only writes to a journal nobody reads
# is not a check.
#
# Intentionally does NOT touch the live MemPalace or live DB — everything goes
# through /tmp. A failure here means the backups are not restorable, which is
# worse than no backups at all.

set -euo pipefail

BACKUP_ROOT="${ZOE_BACKUP_DIR:-$HOME/.zoe-backups}"
MP_BACKUPS="$BACKUP_ROOT/mempalace"
PG_BACKUPS="$BACKUP_ROOT/postgres"
PG_CONTAINER="${ZOE_POSTGRES_CONTAINER:-zoe-database}"
OFFBOX="$(dirname "$(readlink -f "$0")")/zoe_offbox.py"

scratch="$(mktemp -d /tmp/zoe-backup-verify.XXXXXX)"
created_dbs=()
cleanup() {
  for db in "${created_dbs[@]:-}"; do
    docker exec "$PG_CONTAINER" dropdb -U zoe --if-exists "$db" >/dev/null 2>&1 || true
  done
  rm -rf "$scratch"
}
trap cleanup EXIT

errors=0

latest_mp="$(ls -1t "$MP_BACKUPS"/mempalace-*.tar.gz 2>/dev/null | head -1 || true)"
if [[ -z "$latest_mp" ]]; then
  echo "zoe-backup-verify: no MemPalace tarball found in $MP_BACKUPS" >&2
  errors=$((errors + 1))
else
  echo "zoe-backup-verify: restoring $latest_mp"
  tar -xzf "$latest_mp" -C "$scratch"
  mp_dir="$(find "$scratch" -maxdepth 2 -name chroma.sqlite3 -printf '%h\n' | head -1)"
  if [[ -z "$mp_dir" ]]; then
    echo "zoe-backup-verify: mempalace tarball missing chroma.sqlite3" >&2
    errors=$((errors + 1))
  else
    mp_rows=$(python3 - "$mp_dir/chroma.sqlite3" <<'PY' 2>/dev/null || true
import sqlite3, sys
try:
    con = sqlite3.connect(sys.argv[1])
    row = con.execute("SELECT COUNT(*) FROM embeddings").fetchone()
    print(row[0] if row else "0")
except Exception:
    print("err")
PY
)
    echo "zoe-backup-verify: restored mempalace → $mp_rows embeddings"
    if [[ "$mp_rows" == "err" || -z "$mp_rows" ]]; then
      echo "zoe-backup-verify: mempalace restored DB unreadable" >&2
      errors=$((errors + 1))
    fi
  fi
fi

if ! docker inspect "$PG_CONTAINER" >/dev/null 2>&1; then
  echo "zoe-backup-verify: Postgres container $PG_CONTAINER not found" >&2
  errors=$((errors + 1))
else
  for db in zoe multica; do
    latest_pg="$(ls -1t "$PG_BACKUPS"/"${db}"-*.dump.gz 2>/dev/null | grep -v -- "-weekly-" | head -1 || true)"
    if [[ -z "$latest_pg" ]]; then
      echo "zoe-backup-verify: no Postgres dump found for $db in $PG_BACKUPS" >&2
      errors=$((errors + 1))
      continue
    fi

    verify_db="zoe_verify_${db}_$$"
    created_dbs+=("$verify_db")
    echo "zoe-backup-verify: restoring $latest_pg into $verify_db"

    if ! docker exec "$PG_CONTAINER" createdb -U zoe "$verify_db" >/dev/null 2>&1; then
      echo "zoe-backup-verify: could not create scratch DB $verify_db" >&2
      errors=$((errors + 1))
      continue
    fi

    if ! gunzip -c "$latest_pg" | docker exec -i "$PG_CONTAINER" pg_restore -U zoe -d "$verify_db" >/dev/null 2>&1; then
      echo "zoe-backup-verify: pg_restore failed for $db" >&2
      errors=$((errors + 1))
      continue
    fi

    if [[ "$db" == "zoe" ]]; then
      summary=$(docker exec "$PG_CONTAINER" psql -U zoe -d "$verify_db" -Atc \
        "SELECT (SELECT COUNT(*) FROM auth_users), (SELECT COUNT(*) FROM chat_messages);" 2>/dev/null || true)
      if [[ -z "$summary" ]]; then
        echo "zoe-backup-verify: restored zoe DB missing expected tables" >&2
        errors=$((errors + 1))
      else
        echo "zoe-backup-verify: restored zoe auth_users/chat_messages=$summary"
      fi
    else
      summary=$(docker exec "$PG_CONTAINER" psql -U zoe -d "$verify_db" -Atc \
        "SELECT COUNT(*) FROM issue;" 2>/dev/null || true)
      if [[ -z "$summary" ]]; then
        echo "zoe-backup-verify: restored multica DB missing issue table" >&2
        errors=$((errors + 1))
      else
        echo "zoe-backup-verify: restored multica issues=$summary"
      fi
    fi
  done
fi

onbox_errors=$errors
if [[ -f "$HOME/.config/zoe-backup/offbox.env" ]]; then
  # the drill sends its own alert; count it here so the unit fails too
  if ! python3 "$OFFBOX" drill; then
    echo "zoe-backup-verify: off-box restore drill failed (details above)" >&2
    errors=$((errors + 1))
  fi
else
  echo "zoe-backup-verify: off-box backup not configured - monthly drill skipped (docs/knowledge/offbox-backup.md)"
fi

if [[ $errors -gt 0 ]]; then
  echo "zoe-backup-verify: $errors problem(s) detected" >&2
  # on-box problems alert here; an off-box drill failure has already alerted itself
  [[ $onbox_errors -gt 0 ]] && { python3 "$OFFBOX" notify \
    "weekly on-box backup verify found $onbox_errors problem(s); see ~/.zoe-logs/backup-verify.log" || true; }
  exit 1
fi

echo "zoe-backup-verify: all checks passed"
