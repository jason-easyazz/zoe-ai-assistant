#!/usr/bin/env bash
# Apply production database migrations before restarting Zoe services.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ZOE_DATA_DIR="${ROOT_DIR}/services/zoe-data"

load_env_file() {
  local file="$1"
  if [[ -f "$file" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$file"
    set +a
  fi
}

load_env_file "${ROOT_DIR}/.env"
load_env_file "${ZOE_DATA_DIR}/.env"

if [[ -z "${POSTGRES_URL:-}" ]]; then
  echo "POSTGRES_URL is required for database migrations" >&2
  exit 1
fi

echo "Applying zoe-data Alembic migrations..."
(
  cd "${ZOE_DATA_DIR}"
  POSTGRES_URL="${POSTGRES_URL}" python3 -m alembic upgrade head
)

if command -v psql >/dev/null 2>&1; then
  echo "Applying zoe-auth PostgreSQL DDL..."
  psql "${POSTGRES_URL}" -v ON_ERROR_STOP=1 -f "${ROOT_DIR}/scripts/setup/migrate_auth_to_postgres.sql"
else
  echo "psql is required to apply zoe-auth DDL" >&2
  exit 1
fi

echo "Database migrations complete."
