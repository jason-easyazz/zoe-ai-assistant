#!/usr/bin/env bash
# Apply production database migrations before restarting Zoe services.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ZOE_DATA_DIR="${ROOT_DIR}/services/zoe-data"
AUTH_DDL="${ROOT_DIR}/scripts/setup/migrate_auth_to_postgres.sql"

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

mapfile -t PG_PARTS < <(python3 - <<'PY'
import os
from urllib.parse import unquote, urlparse

url = urlparse(os.environ["POSTGRES_URL"])
print(url.hostname or "localhost")
print(str(url.port or 5432))
print(unquote(url.username or ""))
print(unquote(url.password or ""))
print(unquote((url.path or "/").lstrip("/")))
PY
)

if [[ -z "${PG_PARTS[2]}" || -z "${PG_PARTS[4]}" ]]; then
  echo "POSTGRES_URL must include a username and database name" >&2
  exit 1
fi

escape_pgpass() {
  local value="$1"
  value="${value//\\/\\\\}"
  value="${value//:/\\:}"
  printf '%s' "$value"
}

run_auth_ddl_with_host_psql() {
  local pgpassfile
  pgpassfile="$(mktemp)"
  trap 'rm -f "${pgpassfile}"' RETURN
  printf '%s:%s:%s:%s:%s\n' \
    "$(escape_pgpass "${PG_PARTS[0]}")" \
    "$(escape_pgpass "${PG_PARTS[1]}")" \
    "$(escape_pgpass "${PG_PARTS[4]}")" \
    "$(escape_pgpass "${PG_PARTS[2]}")" \
    "$(escape_pgpass "${PG_PARTS[3]}")" > "${pgpassfile}"
  chmod 600 "${pgpassfile}"
  PGPASSFILE="${pgpassfile}" psql \
    -h "${PG_PARTS[0]}" \
    -p "${PG_PARTS[1]}" \
    -U "${PG_PARTS[2]}" \
    -d "${PG_PARTS[4]}" \
    -v ON_ERROR_STOP=1 \
    -f "${AUTH_DDL}"
}

run_auth_ddl_with_docker_psql() {
  if ! command -v docker >/dev/null 2>&1; then
    return 1
  fi

  case "${PG_PARTS[0]}" in
    localhost|127.0.0.1|::1|zoe-database) ;;
    *)
      echo "Host psql is unavailable and POSTGRES_URL points to ${PG_PARTS[0]}, not the local zoe-database container" >&2
      return 1
      ;;
  esac

  {
    printf '%s:%s:%s:%s:%s\n' \
      "$(escape_pgpass "127.0.0.1")" \
      "$(escape_pgpass "${PG_PARTS[1]}")" \
      "$(escape_pgpass "${PG_PARTS[4]}")" \
      "$(escape_pgpass "${PG_PARTS[2]}")" \
      "$(escape_pgpass "${PG_PARTS[3]}")"
    cat "${AUTH_DDL}"
  } | docker exec -i zoe-database sh -c '
    set -eu
    port="$1"
    database="$2"
    username="$3"
    pgpassfile="$(mktemp)"
    trap '\''rm -f "${pgpassfile}"'\'' EXIT
    chmod 600 "${pgpassfile}"
    IFS= read -r pgpass_entry
    printf "%s\n" "${pgpass_entry}" > "${pgpassfile}"
    PGPASSFILE="${pgpassfile}" psql \
      -h 127.0.0.1 \
      -p "${port}" \
      -U "${username}" \
      -d "${database}" \
      -v ON_ERROR_STOP=1
  ' sh "${PG_PARTS[1]}" "${PG_PARTS[4]}" "${PG_PARTS[2]}"
}

database_container_running() {
  command -v docker >/dev/null 2>&1 \
    && [[ "$(docker inspect -f '{{.State.Running}}' zoe-database 2>/dev/null || true)" == "true" ]]
}

echo "Applying zoe-auth PostgreSQL DDL..."
if command -v psql >/dev/null 2>&1; then
  run_auth_ddl_with_host_psql
elif database_container_running; then
  echo "Host psql not found; using psql inside zoe-database container..."
  run_auth_ddl_with_docker_psql
else
  echo "psql is required on the host, or the zoe-database container must be running" >&2
  exit 1
fi

echo "Database migrations complete."
