#!/usr/bin/env bash
#
# Nightly Postgres backup for Zoe's live relational databases.
#
# Output:   ~/.zoe-backups/postgres/<db>-<timestamp>.dump.gz
#           ~/.zoe-backups/postgres/<db>-<timestamp>.manifest.json  (pg_dump_manifest.py: every table in the dump
#           with its row count, counted FROM the dump, plus the live table set / pgvector / Alembic versions; the
#           weekly restore drill, zoe_restore_drill.py, restores the dump into a throwaway container and compares)
# Rotation: keep 7 daily dumps per database plus up to 4 weekly Sunday dumps; a manifest goes with its dump.

set -euo pipefail

CONTAINER="${ZOE_POSTGRES_CONTAINER:-zoe-database}"
BACKUP_ROOT="${ZOE_BACKUP_DIR:-$HOME/.zoe-backups}/postgres"
KEEP_DAILY="${ZOE_POSTGRES_BACKUP_KEEP:-7}"
KEEP_WEEKLY="${ZOE_POSTGRES_WEEKLY_KEEP:-4}"
ENV_FILE="${ZOE_ENV_FILE:-$HOME/assistant/.env}"
DATABASES="${ZOE_POSTGRES_DATABASES:-zoe multica}"
HERE="$(dirname "$(readlink -f "$0")")"

mkdir -p "$BACKUP_ROOT"
chmod 700 "$BACKUP_ROOT" 2>/dev/null || true

if [[ ! -f "$ENV_FILE" ]]; then
  echo "postgres-nightly-backup: missing env file $ENV_FILE" >&2
  exit 1
fi

# shellcheck disable=SC1090
set -a
source "$ENV_FILE"
set +a

if [[ -z "${POSTGRES_PASSWORD:-}" ]]; then
  echo "postgres-nightly-backup: POSTGRES_PASSWORD not set in $ENV_FILE" >&2
  exit 1
fi

if ! docker inspect "$CONTAINER" >/dev/null 2>&1; then
  echo "postgres-nightly-backup: container $CONTAINER not found" >&2
  exit 1
fi

# Biometrics of third parties never reach a dump (docs/architecture/memories-space.md section 2.6 guardrail 1): nothing
# redacts these dumps on a forget, so the learned-voice tables are backed up as SCHEMA only. A restore re-learns them.
# (A pattern that matches no table - the multica db - is ignored by pg_dump.) Pinned by tests/test_omi_voices.py.
BIOMETRIC_TABLES="learned_voiceprints learned_voiceprint_segments omi_segment_embeddings omi_voice_clusters"
exclude_args=()
for t in $BIOMETRIC_TABLES; do
  exclude_args+=("--exclude-table-data=$t")
done

ts="$(date +%Y%m%d-%H%M%S)"
tmpdir="$(mktemp -d /tmp/zoe-postgres-backup.XXXXXX)"
trap 'rm -rf "$tmpdir"' EXIT

for db in $DATABASES; do
  raw="$tmpdir/${db}.dump"
  if ! docker exec -e PGPASSWORD="$POSTGRES_PASSWORD" "$CONTAINER" \
      pg_dump -U zoe -Fc "${exclude_args[@]}" "$db" > "$raw"; then
    echo "postgres-nightly-backup: pg_dump failed for $db" >&2
    exit 2
  fi
  if [[ ! -s "$raw" ]]; then
    echo "postgres-nightly-backup: empty dump for $db" >&2
    exit 2
  fi

  out="$BACKUP_ROOT/${db}-${ts}.dump.gz"
  gzip -c "$raw" > "$out"
  chmod 600 "$out"
  size="$(du -h "$out" | awk '{print $1}')"
  echo "postgres-nightly-backup: wrote $out ($size)"

  # The manifest never fails the dump (it is already written): the weekly restore drill reports a missing one.
  # PGPASSWORD is passed by NAME to `docker exec -e` inside the script, never on a command line.
  if ! PGPASSWORD="$POSTGRES_PASSWORD" python3 "$HERE/pg_dump_manifest.py" --container "$CONTAINER" --db "$db" \
      --dump "$out" --out "$BACKUP_ROOT/${db}-${ts}.manifest.json"; then
    echo "postgres-nightly-backup: WARNING no manifest for $db (the restore drill will report it)" >&2
  fi

  ls -1t "$BACKUP_ROOT"/"${db}"-*.dump.gz 2>/dev/null \
    | grep -v -- "-weekly-" \
    | tail -n +"$((KEEP_DAILY + 1))" \
    | xargs -r rm -f
done

if [[ "$(date +%u)" == "7" ]]; then
  week_tag="$(date +%Y-%W)"
  for db in $DATABASES; do
    cp "$BACKUP_ROOT/${db}-${ts}.dump.gz" "$BACKUP_ROOT/${db}-weekly-${week_tag}.dump.gz"
    if [[ -f "$BACKUP_ROOT/${db}-${ts}.manifest.json" ]]; then
      cp "$BACKUP_ROOT/${db}-${ts}.manifest.json" "$BACKUP_ROOT/${db}-weekly-${week_tag}.manifest.json"
    fi
    ls -1t "$BACKUP_ROOT"/"${db}"-weekly-*.dump.gz 2>/dev/null \
      | tail -n +"$((KEEP_WEEKLY + 1))" \
      | xargs -r rm -f
  done
fi

# A manifest whose dump has rotated away goes too.
for m in "$BACKUP_ROOT"/*.manifest.json; do
  [[ -e "$m" ]] || continue
  [[ -f "${m%.manifest.json}.dump.gz" ]] || rm -f "$m"
done

echo "postgres-nightly-backup: complete"
