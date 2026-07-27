#!/usr/bin/env bash
# cross_review.sh — kick an Omnigent polly cross-review of a PR and wait for the verdict.
#
# Usage: cross_review.sh <PR-number> "<contract: 2-4 sentences on what the PR must/must-not do>"
#
# Pre-ready advisory tier of the review pipeline (see
# docs/knowledge/omnigent-cross-review.md): run it on the DRAFT PR before
# marking ready. polly reviews with an independent different-vendor sub-agent;
# findings are ADVISORY — they never become PR threads, and polly never
# pushes/resolves/merges.
#
# Exit codes: 0 = report retrieved (printed to stdout), 1 = usage,
# 2 = kick/poll failure or silent-death alarm (session ended with no report).
set -euo pipefail

SERVER="${OMNIGENT_SERVER:-http://127.0.0.1:6767}"
POLLY_ID="${OMNIGENT_POLLY_ID:-ag_057995d1517418e6839f51d340785dd6}"
CONTAINER="${OMNIGENT_CONTAINER:-zoe-omnigent}"
TIMEOUT_S="${CROSS_REVIEW_TIMEOUT_S:-2400}"

[ $# -ge 2 ] || { echo "usage: $0 <PR-number> \"<contract>\"" >&2; exit 1; }

# ONE polly worker repo-wide (RAM discipline) — serialize concurrent invocations.
exec 9>/tmp/zoe-cross-review.lock
flock 9
PR="$1"; CONTRACT="$2"
case "$PR" in (*[!0-9]*|'') echo "ALARM: PR must be numeric, got: $PR" >&2; exit 1;; esac

SID=$(curl -sf --connect-timeout 5 --max-time 60 -X POST "$SERVER/v1/sessions" -H 'Content-Type: application/json' \
  -d "{\"agent_id\":\"$POLLY_ID\",\"title\":\"cross-review PR #$PR\"}" \
  | python3 -c "import json,sys;print(json.load(sys.stdin)['id'])") \
  || { echo "ALARM: session create failed against $SERVER" >&2; exit 2; }
echo "session: $SID" >&2

# Brief goes INLINE via -p. Do not stage it as a session comment: comment
# staging fails silently and polly wakes to an empty session (2026-07-27).
BRIEF="Use your cross-review skill on PR #$PR of jason-easyazz/zoe-ai-assistant. Fetch the diff with: gh pr diff $PR. Contract: $CONTRACT Any new tests must be able to FAIL (non-vacuous). You are a REVIEWER, not a driver: do NOT push, resolve threads, comment on GitHub, or merge. End with a structured report: blocking findings / non-blocking / confirmed clean, or an explicit CLEAN verdict."

# REST cannot start claude-sdk sessions; the docker-exec kick is the only
# working recipe (reference_omnigent_handoff_mechanics).
docker exec -d "$CONTAINER" sh -c "cd /workspace && omnigent run --server $SERVER --harness claude-sdk -r $SID -p \"\$(cat <<'CROSS_REVIEW_BRIEF_7f3a9c'
$BRIEF
CROSS_REVIEW_BRIEF_7f3a9c
)\" --no-log" \
  || { echo "ALARM: docker-exec kick failed" >&2; exit 2; }

# Poll. `docker exec -d` returns before the run registers, so an early `idle`
# is a slow START, not completion — require having SEEN `running` once before
# treating a non-running status as terminal (polly finding #2 on #1578).
start=$(date +%s)
saw_running=0
status=""
while :; do
  sleep 30
  status=$(curl -sf --connect-timeout 5 --max-time 60 "$SERVER/v1/sessions/$SID" \
    | python3 -c "import json,sys;print(json.load(sys.stdin).get('status','?'))" || echo poll-fail)
  [ "$status" = "running" ] && saw_running=1
  if [ "$status" != "running" ] && [ "$status" != "poll-fail" ] && [ "$saw_running" = 1 ]; then
    break
  fi
  now=$(date +%s)
  if [ "$saw_running" = 0 ] && [ "$status" != "running" ] && [ "$status" != "poll-fail" ]; then
    # The run may have started AND finished between two polls — evidence of an
    # assistant reply means completion, not a dead kick (Codex P2, #1578).
    if curl -sf --connect-timeout 5 --max-time 60 "$SERVER/v1/sessions/$SID" \
        | python3 -c "import json,sys;d=json.load(sys.stdin);sys.exit(0 if any(i.get('type')=='message' and (i.get('data',{}).get('role') or i.get('role'))=='assistant' for i in d.get('items',[])) else 1)" 2>/dev/null; then
      saw_running=1; continue
    fi
  fi
  if [ "$saw_running" = 0 ] && [ $((now - start)) -gt 300 ]; then
    echo "ALARM: session never reached 'running' within 300s (status: $status) — kick died silently" >&2
    exit 2
  fi
  if [ $((now - start)) -gt "$TIMEOUT_S" ]; then
    echo "ALARM: review still '$status' after ${TIMEOUT_S}s — inspect session $SID" >&2
    exit 2
  fi
done

case "$status" in
  idle) : ;;  # normal completion — sessions end idle, never 'completed'
  *) echo "ALARM: session ended in error status '$status' — inspect session $SID" >&2; exit 2 ;;
esac

# Report extraction. The session JSON goes through a temp FILE — piping curl
# into `python3 - <<'PY'` makes the heredoc win the fight for stdin and the
# JSON is never read (SC2259; polly finding #1 on #1578, reproduced live).
# An idle session with NO messages is the silent-launch-failure signature —
# alarm, never report it as a clean review.
TMPJ=$(mktemp)
trap 'rm -f "$TMPJ"' EXIT
curl -sf --connect-timeout 5 --max-time 60 "$SERVER/v1/sessions/$SID" -o "$TMPJ" \
  || { echo "ALARM: could not fetch session $SID for the report" >&2; exit 2; }
python3 - "$SID" "$TMPJ" <<'PY'
import json, sys
d = json.load(open(sys.argv[2]))
texts = []
for it in d.get("items", []):
    if it.get("type") != "message":
        continue
    role = it.get("data", {}).get("role") or it.get("role") or ""
    if role != "assistant":
        continue  # the inline kick prompt is a message item too (Codex P2)
    c = it.get("data", {}).get("content") or it.get("content") or ""
    if isinstance(c, list):
        c = " ".join(str(x.get("text", "")) for x in c if isinstance(x, dict))
    texts.append(str(c))
if not texts:
    print(f"ALARM: session {sys.argv[1]} ended idle with zero ASSISTANT messages — "
          "the kick died silently (check container auth: claude OAuth expires 2026-08-22).",
          file=sys.stderr)
    sys.exit(2)
# Reports can span messages — print the tail of the conversation, not just
# the last message (polly non-blocking on #1578).
print("\n\n---\n\n".join(texts[-3:]))
PY
