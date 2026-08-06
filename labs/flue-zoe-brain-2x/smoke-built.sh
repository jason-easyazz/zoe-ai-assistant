#!/usr/bin/env bash
# Throwaway smoke of the BUILT server: proves the `'use agent'` build scan
# registered the agent (the test suite's `start()` path bypasses that scan).
# Throwaway port, throwaway data dir, killed before exit.
set -u
cd "$(dirname "$0")"

PORT=38578
DATA_DIR="$(mktemp -d)"
export PORT
export ZOE_BRAIN_DB="${DATA_DIR}/throwaway.db"
export ZOE_BRAIN_TOKEN='smoke-token'
export ZOE_BRAIN_BASE_URL='http://127.0.0.1:9/v1'   # dead port: never reach a real model
export ZOE_DATA_URL='http://127.0.0.1:9'
export ZOE_BRAIN_ALLOW_WRITES=false

node dist/server.mjs &
SERVER_PID=$!
cleanup() {
  kill "${SERVER_PID}" 2>/dev/null
  wait "${SERVER_PID}" 2>/dev/null
  rm -rf "${DATA_DIR}"
}
trap cleanup EXIT

for _ in $(seq 1 40); do
  curl -sf "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1 && break
  sleep 0.25
done

echo "--- /health"
curl -s "http://127.0.0.1:${PORT}/health"; echo
echo "--- POST without a token (must be 401)"
curl -s -o /dev/null -w '%{http_code}\n' -X POST \
  -H 'content-type: application/json' \
  -d '{"kind":"user","body":"hi"}' \
  "http://127.0.0.1:${PORT}/agents/zoe/smoke-1"
echo "--- POST with the token (202 = the agent is REGISTERED and admitted the turn)"
curl -s -X POST \
  -H 'content-type: application/json' \
  -H "authorization: Bearer ${ZOE_BRAIN_TOKEN}" \
  -d '{"kind":"user","body":"hi"}' \
  "http://127.0.0.1:${PORT}/agents/zoe/smoke-1"; echo
echo "--- data dir contents (proves it used the throwaway path, not the live one)"
ls "${DATA_DIR}"
