#!/usr/bin/env bash
#
# Nightly Postgres backup for Zoe's live relational databases.
#
# Output:   ~/.zoe-backups/postgres/<db>-<timestamp>.dump.gz
# Rotation: keep 7 daily dumps per database plus up to 4 weekly Sunday dumps.

set -euo pipefail

CONTAINER="${ZOE_POSTGRES_CONTAINER:-zoe-database}"
BACKUP_ROOT="${ZOE_BACKUP_DIR:-$HOME/.zoe-backups}/postgres"
KEEP_DAILY="${ZOE_POSTGRES_BACKUP_KEEP:-7}"
KEEP_WEEKLY="${ZOE_POSTGRES_WEEKLY_KEEP:-4}"
ENV_FILE="${ZOE_ENV_FILE:-$HOME/assistant/.env}"
DATABASES="${ZOE_POSTGRES_DATABASES:-zoe multica}"

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

ts="$(date +%Y%m%d-%H%M%S)"
tmpdir="$(mktemp -d /tmp/zoe-postgres-backup.XXXXXX)"
trap 'rm -rf "$tmpdir"' EXIT

for db in $DATABASES; do
  raw="$tmpdir/${db}.dump"
  if ! docker exec -e PGPASSWORD="$POSTGRES_PASSWORD" "$CONTAINER" \
      pg_dump -U zoe -Fc "$db" > "$raw"; then
    echo "postgres-nightly-backup: pg_dump failed for $db" >&2
    exit 2
  fi
  if [[ ! -s "$raw" ]]; then
    echo "postgres-nightly-backup: empty dump for $db" >&2
    exit 2
  fi

  out="$BACKUP_ROOT/${db}-${ts}.dump.gz"
  gzip -c "$raw" > "$out"
  size="$(du -h "$out" | awk '{print $1}')"
  echo "postgres-nightly-backup: wrote $out ($size)"

  ls -1t "$BACKUP_ROOT"/"${db}"-*.dump.gz 2>/dev/null \
    | grep -v -- "-weekly-" \
    | tail -n +"$((KEEP_DAILY + 1))" \
    | xargs -r rm -f
done

if [[ "$(date +%u)" == "7" ]]; then
  week_tag="$(date +%Y-%W)"
  for db in $DATABASES; do
    cp "$BACKUP_ROOT/${db}-${ts}.dump.gz" "$BACKUP_ROOT/${db}-weekly-${week_tag}.dump.gz"
    ls -1t "$BACKUP_ROOT"/"${db}"-weekly-*.dump.gz 2>/dev/null \
      | tail -n +"$((KEEP_WEEKLY + 1))" \
      | xargs -r rm -f
  done
fi

echo "postgres-nightly-backup: complete"
