#!/usr/bin/env bash
#
# Retire Authentik after migrating SSO to zoe-auth native OIDC.
#
# Safe to re-run: skips steps that are already complete.
# Creates a final pg_dump of the authentik database (if it still exists),
# drops the database, removes stopped Authentik containers, and deletes
# leftover Docker volumes from the old compose profile.
#
# Requires: docker container zoe-database, POSTGRES_PASSWORD in ~/assistant/.env

set -euo pipefail

CONTAINER="${ZOE_POSTGRES_CONTAINER:-zoe-database}"
ENV_FILE="${ZOE_ENV_FILE:-$HOME/assistant/.env}"
BACKUP_ROOT="${ZOE_BACKUP_DIR:-$HOME/.zoe-backups}/postgres"
TS="$(date +%Y%m%d-%H%M%S)"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "retire-authentik: missing env file $ENV_FILE" >&2
  exit 1
fi

# shellcheck disable=SC1090
set -a
source "$ENV_FILE"
set +a

if [[ -z "${POSTGRES_PASSWORD:-}" ]]; then
  echo "retire-authentik: POSTGRES_PASSWORD not set" >&2
  exit 1
fi

mkdir -p "$BACKUP_ROOT"
chmod 700 "$BACKUP_ROOT" 2>/dev/null || true

db_exists="$(docker exec -e PGPASSWORD="$POSTGRES_PASSWORD" "$CONTAINER" \
  psql -U zoe -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='authentik'" \
  2>/dev/null | tr -d '[:space:]')"

if [[ "$db_exists" == "1" ]]; then
  backup_path="$BACKUP_ROOT/authentik-retired-$TS.dump.gz"
  echo "retire-authentik: backing up authentik database -> $backup_path"
  docker exec -e PGPASSWORD="$POSTGRES_PASSWORD" "$CONTAINER" \
    pg_dump -U zoe -d authentik | gzip > "$backup_path"
  echo "retire-authentik: dropping authentik database"
  docker exec -e PGPASSWORD="$POSTGRES_PASSWORD" "$CONTAINER" \
    psql -U zoe -d postgres -c "DROP DATABASE authentik;"
else
  echo "retire-authentik: authentik database already absent"
fi

for name in authentik-server authentik-worker authentik-redis; do
  if docker ps -a --format '{{.Names}}' | grep -qx "$name"; then
    echo "retire-authentik: removing container $name"
    docker rm "$name" >/dev/null
  fi
done

for vol in assistant_authentik-certs assistant_authentik-media \
           assistant_authentik-redis-data assistant_authentik-templates; do
  if docker volume ls --format '{{.Name}}' | grep -qx "$vol"; then
    echo "retire-authentik: removing volume $vol"
    docker volume rm "$vol" >/dev/null
  fi
done

echo "retire-authentik: complete"
echo "  OIDC issuer: zoe-auth (:8002) via /.well-known/openid-configuration"
echo "  Verify: curl -sf http://127.0.0.1:8002/.well-known/openid-configuration"
