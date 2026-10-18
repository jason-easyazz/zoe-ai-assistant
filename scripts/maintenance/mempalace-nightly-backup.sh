#!/usr/bin/env bash
#
# Nightly MemPalace tarball backup.
#
# Was missing prior to Phase-0 of the memory-and-self-learning overhaul: the
# only coverage for MemPalace came from update_safety._safe_install snapshots,
# which never trigger unless an install runs. A working MemPalace is now the
# single source of truth for memory; this script guarantees a rotating 7-day
# on-disk copy regardless of any other activity.
#
# Output:   ~/.zoe-backups/mempalace/mempalace-<ts>.tar.gz
# Rotation: keep the 7 most recent archives, oldest are pruned.
# Integrity: verified by extracting `chroma.sqlite3` to a tmpfile and running
#            `PRAGMA integrity_check`. Exits non-zero if the live DB is corrupt.

set -euo pipefail

MEMPALACE_DIR="${MEMPALACE_DATA_DIR:-$HOME/.mempalace}"
BACKUP_ROOT="${ZOE_BACKUP_DIR:-$HOME/.zoe-backups}/mempalace"
KEEP="${ZOE_MEMPALACE_BACKUP_KEEP:-7}"

mkdir -p "$BACKUP_ROOT"

if [[ ! -d "$MEMPALACE_DIR" ]]; then
  echo "mempalace-nightly-backup: no MemPalace dir at $MEMPALACE_DIR — nothing to do" >&2
  exit 0
fi

# Integrity check on the live Chroma sqlite before we snapshot it.
# Uses python3 because the sqlite3 CLI isn't installed on Jetson by default.
CHROMA_DB="$MEMPALACE_DIR/chroma.sqlite3"
if [[ -f "$CHROMA_DB" ]]; then
  integrity=$(python3 - "$CHROMA_DB" <<'PY' || true
import sqlite3, sys
con = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro&immutable=1", uri=True)
row = con.execute("PRAGMA integrity_check").fetchone()
print(row[0] if row else "no_result")
PY
)
  if [[ "$integrity" != "ok" ]]; then
    echo "mempalace-nightly-backup: sqlite integrity_check returned '$integrity' — refusing to back up corrupt DB" >&2
    exit 2
  fi
fi

ts="$(date +%Y%m%d-%H%M%S)"
out="$BACKUP_ROOT/mempalace-${ts}.tar.gz"
# Written to a .partial name and renamed only on success (2026-10-18, review of the tracked copy): a tar that fails
# mid-write must never leave a truncated mempalace-*.tar.gz that rotation, the verify and the off-box job read as
# the newest good archive.
part="$out.partial"

# 2026-09-25: tar used to read the LIVE chroma.sqlite3 and failed with
# "file changed as we read it" whenever zoe-data wrote mid-backup (exit 1 ->
# the unit's Postgres step was then skipped). Snapshot the SQLite with the
# online backup API first, then tar the palace with the snapshot swapped in. The
# SQLite sidecars (-journal/-wal/-shm) are transient and are excluded too: on
# 2026-09-26 tar saw a rollback journal vanish mid-read ("File removed before we
# read it", exit 1) — the snapshot is self-consistent without them.
snap="$(mktemp -d)"
trap 'rm -rf "$snap" "$part"' EXIT
base="$(basename "$MEMPALACE_DIR")"
if [[ -f "$CHROMA_DB" ]]; then
  python3 - "$CHROMA_DB" "$snap/chroma.sqlite3" <<'PY'
import sqlite3, sys
src = sqlite3.connect(sys.argv[1]); dst = sqlite3.connect(sys.argv[2])
with dst: src.backup(dst)
dst.close(); src.close()
PY
  tar -czf "$part" --warning=no-file-changed \
    -C "$(dirname "$MEMPALACE_DIR")" \
    --exclude="$base/chroma.sqlite3" --exclude="$base/chroma.sqlite3-journal" \
    --exclude="$base/chroma.sqlite3-wal" --exclude="$base/chroma.sqlite3-shm" \
    --warning=no-file-removed "$base" \
    -C "$snap" --transform="s#^chroma.sqlite3#$base/chroma.sqlite3#" chroma.sqlite3
else
  tar -czf "$part" --warning=no-file-changed -C "$(dirname "$MEMPALACE_DIR")" "$base"
fi
mv "$part" "$out"
size="$(du -h "$out" | awk '{print $1}')"
echo "mempalace-nightly-backup: wrote $out ($size)"

# Rotate: keep most recent N archives.
ls -1t "$BACKUP_ROOT"/mempalace-*.tar.gz 2>/dev/null \
  | tail -n +"$((KEEP + 1))" \
  | xargs -r rm -f

echo "mempalace-nightly-backup: rotation complete, keeping ${KEEP} most recent"
