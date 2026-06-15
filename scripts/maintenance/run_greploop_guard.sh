#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
USER_SITE="$(python3 -c 'import site; print(site.getusersitepackages())')"
export PYTHONPATH="${USER_SITE}:${ROOT}/services/zoe-data${PYTHONPATH:+:${PYTHONPATH}}"

for env_file in "${ROOT}/services/zoe-data/.env" "${ROOT}/.env" "${HOME}/.hermes/.env"; do
    if [[ -f "${env_file}" ]]; then
        set -a
        # shellcheck disable=SC1090
        source "${env_file}"
        set +a
    fi
done

exec python3 "${ROOT}/scripts/maintenance/greploop_guard.py" "$@"
