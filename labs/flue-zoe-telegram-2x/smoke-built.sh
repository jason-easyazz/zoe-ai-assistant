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
set -u
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
  kill "${SERVER_PID}" "${MOCK_PID}" 2>/dev/null
  wait "${SERVER_PID}" "${MOCK_PID}" 2>/dev/null
  rm -rf "${DATA_DIR}"
}
trap cleanup EXIT

for _ in $(seq 1 40); do
  curl -s -o /dev/null "http://127.0.0.1:${PORT}/health" && break
  sleep 0.25
done

echo "--- /health (200 + polling:true = the bot took the mock token over)"
curl -s -w ' [http %{http_code}]\n' "http://127.0.0.1:${PORT}/health"

echo "--- POST the 2.x body to the mounted agent route"
echo "    (202 = the BUILT server's 'use agent' scan registered the agent)"
curl -s -w ' [http %{http_code}]\n' -X POST \
  -H 'content-type: application/json' \
  -d '{"kind":"user","body":"hi"}' \
  "http://127.0.0.1:${PORT}/agents/zoe/telegram:chat:42"

echo "--- NEGATIVE CONTROL: the nested body the migration guide documents"
echo "    (must NOT be 202 — proves the 202 above is a real admission)"
curl -s -o /dev/null -w '    [http %{http_code}]\n' -X POST \
  -H 'content-type: application/json' \
  -d '{"message":{"kind":"user","body":"hi"}}' \
  "http://127.0.0.1:${PORT}/agents/zoe/telegram:chat:43"

echo "--- store (proves db.ts was discovered and used the THROWAWAY path)"
ls -la "${DATA_DIR}"
