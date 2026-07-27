#!/usr/bin/env bash
# cross_review.sh — kick an Omnigent polly cross-review of a PR and wait for the verdict.
#
# Usage: cross_review.sh <PR-number> "<contract: 2-4 sentences on what the PR must/must-not do>"
#
# Pre-push advisory tier of the review pipeline (see
# docs/knowledge/omnigent-cross-review.md). polly reviews with an independent
# different-vendor sub-agent; findings are ADVISORY — they never become PR
# threads, and polly never pushes/resolves/merges.
#
# Exit codes: 0 = report retrieved (printed to stdout), 1 = usage,
# 2 = kick failed / silent-death alarm (session went idle with no messages).
set -euo pipefail

SERVER="${OMNIGENT_SERVER:-http://127.0.0.1:6767}"
POLLY_ID="${OMNIGENT_POLLY_ID:-ag_057995d1517418e6839f51d340785dd6}"
CONTAINER="${OMNIGENT_CONTAINER:-zoe-omnigent}"
TIMEOUT_S="${CROSS_REVIEW_TIMEOUT_S:-2400}"

[ $# -ge 2 ] || { echo "usage: $0 <PR-number> \"<contract>\"" >&2; exit 1; }
PR="$1"; CONTRACT="$2"

SID=$(curl -sf -X POST "$SERVER/v1/sessions" -H 'Content-Type: application/json' \
  -d "{\"agent_id\":\"$POLLY_ID\",\"title\":\"cross-review PR #$PR\"}" \
  | python3 -c "import json,sys;print(json.load(sys.stdin)['id'])")
echo "session: $SID" >&2

# Brief goes INLINE via -p. Do not stage it as a session comment: comment
# staging fails silently and polly wakes to an empty session (2026-07-27).
BRIEF="Use your cross-review skill on PR #$PR of jason-easyazz/zoe-ai-assistant. Fetch the diff with: gh pr diff $PR. Contract: $CONTRACT Any new tests must be able to FAIL (non-vacuous). You are a REVIEWER, not a driver: do NOT push, resolve threads, comment on GitHub, or merge. End with a structured report: blocking findings / non-blocking / confirmed clean, or an explicit CLEAN verdict."

# REST cannot start claude-sdk sessions; the docker-exec kick is the only
# working recipe (reference_omnigent_handoff_mechanics).
docker exec -d "$CONTAINER" sh -c "cd /workspace && omnigent run --server $SERVER --harness claude-sdk -r $SID -p \"\$(cat <<'CROSS_REVIEW_EOF'
$BRIEF
CROSS_REVIEW_EOF
)\" --no-log"

start=$(date +%s)
while :; do
  sleep 30
  st=$(curl -sf "$SERVER/v1/sessions/$SID" \
    | python3 -c "import json,sys;print(json.load(sys.stdin).get('status','?'))" || echo poll-fail)
  now=$(date +%s)
  if [ "$st" != "running" ] && [ "$st" != "poll-fail" ]; then break; fi
  if [ $((now - start)) -gt "$TIMEOUT_S" ]; then
    echo "ALARM: review still '$st' after ${TIMEOUT_S}s — inspect session $SID" >&2; exit 2
  fi
done

# Sessions end 'idle', never 'completed'. An idle session with NO messages is
# the silent-launch-failure signature (e.g. expired container OAuth) — alarm,
# never report it as a clean review.
curl -sf "$SERVER/v1/sessions/$SID" | python3 - "$SID" <<'PY'
import json, sys
d = json.load(sys.stdin)
texts = []
for it in d.get("items", []):
    if it.get("type") == "message":
        c = it.get("data", {}).get("content") or it.get("content") or ""
        if isinstance(c, list):
            c = " ".join(str(x.get("text", "")) for x in c if isinstance(x, dict))
        texts.append(str(c))
if not texts:
    print(f"ALARM: session {sys.argv[1]} ended '{d.get('status')}' with zero messages — "
          "the kick died silently (check container auth: claude OAuth expires 2026-08-22).",
          file=sys.stderr)
    sys.exit(2)
print(texts[-1])
PY
