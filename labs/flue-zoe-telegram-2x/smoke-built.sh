#!/usr/bin/env bash
# Throwaway smoke of the BUILT server (`dist/server.mjs`) — the artifact the
# systemd unit actually runs.
#
# WHY IT IS NOT REDUNDANT WITH `npm test`. The suite registers the agent through
# `start({ agents: [...] })`, which BYPASSES the build-time `'use agent'` scan.
# Only the built server proves that scan ran, that `src/db.ts` was discovered and
# opened, and that `PORT` is honoured. That is the whole class of 2.x failure the
# tests structurally cannot see.
#
# Fully offline and non-destructive: throwaway port, throwaway store, a mock Bot
# API on loopback (so no real Telegram traffic and no fight over the live bot
# token), and a dead-port ZOE_DATA_URL so nothing can reach the live zoe-data.
# Everything is killed and deleted on exit.
#
# IT MUST BE ABLE TO FAIL. This is the ONLY proof of build-time agent
# registration that the README and labs/AGENTS.md cite, and it used to exit 0
# whatever happened: no `set -e`, a readiness loop that fell through on timeout,
# and curls that PRINTED status codes without asserting any of them — so a
# server that never started still ended in a green `ls` (cross-review, #1639).
# Every check below is now an assertion with a named failure, and the script
# exits non-zero on the first one that does not hold.
set -euo pipefail
cd "$(dirname "$0")"

PORT=33582
MOCK_TG_PORT=33583
DATA_DIR="$(mktemp -d)"
export PORT
export ZOE_TELEGRAM_DB="${DATA_DIR}/throwaway.db"
export SESSION_EPOCHS_PATH="${DATA_DIR}/epochs.json"
export TELEGRAM_BOT_TOKEN='123456:smoke-token-not-real'
export TELEGRAM_API_ROOT="http://127.0.0.1:${MOCK_TG_PORT}"
export ZOE_DATA_URL='http://127.0.0.1:9'   # dead port: never reach live zoe-data

node --experimental-strip-types test/helpers/mock-telegram.ts "${MOCK_TG_PORT}" &
MOCK_PID=$!
node dist/server.mjs &
SERVER_PID=$!
cleanup() {
  # `|| true` throughout: under `set -e` a already-dead child would abort the
  # trap and leak ${DATA_DIR}. Cleanup must run to completion on the FAILURE
  # path especially — that is the path that now actually happens.
  kill "${SERVER_PID}" "${MOCK_PID}" 2>/dev/null || true
  wait "${SERVER_PID}" "${MOCK_PID}" 2>/dev/null || true
  rm -rf "${DATA_DIR}"
}
trap cleanup EXIT

fail() { echo "SMOKE FAILED: $*" >&2; exit 1; }

# `-f` (fail on HTTP >= 400) plus a polling:true check, NOT a bare connection.
# The HTTP server starts listening BEFORE grammY finishes getMe and runs onStart,
# so /health returns 503 in that window. A plain `curl -s` succeeds on a 503 and
# breaks the loop immediately, throwing away the remaining warm-up and failing
# the assertion below on a server that was merely still starting (cross-review,
# #1639). Wait for the state we are actually going to assert.
ready=0
for _ in $(seq 1 40); do
  if curl -sf "http://127.0.0.1:${PORT}/health" 2>/dev/null | grep -q '"polling":true'; then
    ready=1; break
  fi
  sleep 0.25
done
[ "${ready}" -eq 1 ] || fail "dist/server.mjs never reported a healthy /health (200 + polling:true) on ${PORT} within 10s"

echo "--- /health (200 + polling:true = the bot took the mock token over)"
health_body="$(curl -s -w '\n%{http_code}' "http://127.0.0.1:${PORT}/health")"
health_code="${health_body##*$'\n'}"
health_json="${health_body%$'\n'*}"
echo "    ${health_json} [http ${health_code}]"
[ "${health_code}" = "200" ] || fail "/health returned ${health_code}, want 200"
case "${health_json}" in
  *'"polling":true'*) ;;
  *) fail "/health did not report polling:true — the bot never took the mock token over" ;;
esac

echo "--- POST the 2.x body to the mounted agent route"
echo "    (202 = the BUILT server's 'use agent' scan registered the agent)"
flat_code="$(curl -s -o /dev/null -w '%{http_code}' -X POST \
  -H 'content-type: application/json' \
  -d '{"kind":"user","body":"hi"}' \
  "http://127.0.0.1:${PORT}/agents/zoe/telegram:chat:42")"
echo "    [http ${flat_code}]"
[ "${flat_code}" = "202" ] || fail \
  "the flat 2.x body returned ${flat_code}, want 202 — the built server's 'use agent' scan did NOT register the agent"

echo "--- NEGATIVE CONTROL: the nested body the migration guide documents"
echo "    (must NOT be 202 — proves the 202 above is a real admission)"
nested_code="$(curl -s -o /dev/null -w '%{http_code}' -X POST \
  -H 'content-type: application/json' \
  -d '{"message":{"kind":"user","body":"hi"}}' \
  "http://127.0.0.1:${PORT}/agents/zoe/telegram:chat:43")"
echo "    [http ${nested_code}]"
[ "${nested_code}" != "202" ] || fail \
  "the nested body was ALSO accepted (202) — the 202 above proves nothing about the envelope shape"

echo "--- store (proves db.ts was discovered and used the THROWAWAY path)"
ls -la "${DATA_DIR}"
[ -e "${ZOE_TELEGRAM_DB}" ] || fail \
  "no store at ${ZOE_TELEGRAM_DB} — src/db.ts was not discovered, or it opened a DIFFERENT path"

echo "SMOKE OK: built server registered the agent, honoured PORT, and opened the throwaway store"
