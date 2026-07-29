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
# polly's id in the BARE hex form 0.7.0 returns (`GET /v1/agents`); <=0.4.0
# emitted `ag_<hex>`. The prefixed form still resolves, but only via omnigent's
# `_LEGACY_ID_PREFIXES` back-compat strip, which is type-blind (`host_<agent-hex>`
# binds too) — it validates nothing. See omnigent_issue_executor._omnigent_agent_id.
POLLY_ID="${OMNIGENT_POLLY_ID:-057995d1517418e6839f51d340785dd6}"
CONTAINER="${OMNIGENT_CONTAINER:-zoe-omnigent}"
TIMEOUT_S="${CROSS_REVIEW_TIMEOUT_S:-2400}"
case "$TIMEOUT_S" in (*[!0-9]*|'') echo "ALARM: CROSS_REVIEW_TIMEOUT_S must be a positive integer (seconds), got: $TIMEOUT_S" >&2; exit 1;; esac
[ "$TIMEOUT_S" -gt 0 ] || { echo "ALARM: CROSS_REVIEW_TIMEOUT_S must be > 0" >&2; exit 1; }

[ $# -ge 2 ] || { echo "usage: $0 <PR-number> \"<contract>\"" >&2; exit 1; }

# Serialize concurrent CROSS-REVIEW invocations (RAM discipline). Note: other
# polly launch paths (omnigent_issue_executor, the Flue heavy lane) do not take
# this lock — a shared lease across all launchers is future cross-subsystem
# work (Codex, #1578); this bounds what THIS wrapper can add to the load.
exec 9>/tmp/zoe-cross-review.lock
flock 9
PR="$1"; shift
# Join ALL remaining words — an unquoted multiword contract must not silently
# truncate to its first word (Codex, #1578).
CONTRACT="$*"
case "$PR" in (*[!0-9]*|'') echo "ALARM: PR must be numeric, got: $PR" >&2; exit 1;; esac

SID=$(curl -sf --connect-timeout 5 --max-time 60 -X POST "$SERVER/v1/sessions" -H 'Content-Type: application/json' \
  -d "{\"agent_id\":\"$POLLY_ID\",\"title\":\"cross-review PR #$PR\"}" \
  | python3 -c "import json,sys;print(json.load(sys.stdin)['id'])") \
  || { echo "ALARM: session create failed against $SERVER" >&2; exit 2; }
# The ID is interpolated into an sh -c below — reject anything that isn't a
# plain alnum token, optionally conv_-prefixed (same rule as
# omnigent_issue_executor; Codex, #1578).
#
# The PREFIX is not the security property and must not be required: omnigent
# <=0.4.0 returned `conv_<hex>`, 0.7.0 returns the BARE `<hex>` (it dropped the
# type prefix from conv_/ag_/host_ ids alike). Demanding `conv_*` made every
# cross-review abort with "malformed session id" on 0.7.0. The strict charset
# is what keeps the interpolation injection-free, and it is unchanged.
[[ "$SID" =~ ^(conv_)?[A-Za-z0-9]+$ ]] || { echo "ALARM: malformed session id: $SID" >&2; exit 2; }
echo "session: $SID" >&2

# Brief goes INLINE via -p. Do not stage it as a session comment: comment
# staging fails silently and polly wakes to an empty session (2026-07-27).
BRIEF="Use your cross-review skill on PR #$PR of jason-easyazz/zoe-ai-assistant. Fetch the diff with: gh pr diff $PR. Contract: $CONTRACT Any new tests must be able to FAIL (non-vacuous). You are a REVIEWER, not a driver: do NOT push, resolve threads, comment on GitHub, or merge. End with a structured report: blocking findings / non-blocking / confirmed clean, or an explicit CLEAN verdict."

# REST cannot start claude-sdk sessions; the docker-exec kick is the only
# working recipe (reference_omnigent_handoff_mechanics).
# The run's output persists to a session-specific log INSIDE the container so
# a pre-reply death (OAuth expiry, credits, rate limit) stays diagnosable —
# the generic silent-kick ALARM points there (Codex, #1578).
# The brief crosses into the container BASE64-ENCODED: no heredoc, so no
# delimiter a hostile contract could escape into the authenticated shell
# (Codex, #1578 — reproduced with a crafted delimiter line).
KICK_LOG="/tmp/zoe-cross-review-$SID.log"

# Worker cleanup. The container image has NO pkill/procps (verified live;
# Codex, #1578) — kill by /proc cmdline scan instead, and report honestly
# whether anything was actually signalled. Armed as an EXIT/signal trap right
# after the kick so SIGINT/SIGTERM/disconnect cannot orphan a worker while
# the flock releases (Codex, #1578); disarmed once the report is out.
stop_worker() {
  # Two self-match traps here (both hit during control runs): the scanning
  # sh's cmdline contains the SID (grep arg) — exclude $$ or it kills itself
  # (exit 143); and the grep CHILD matches too but is dead by kill-time, so a
  # single `kill $pids` reports failure even when the real worker died —
  # signal per-pid and succeed if ANY landed.
  # Signal delivery is not death: wait briefly for the pids to vanish, then
  # escalate to KILL — otherwise the flock releases while a TERM-trapping
  # worker is still shutting down and the next invocation overlaps it
  # (Codex, #1578). KILL is the terminal rung; nothing to escalate past it.
  docker exec "$CONTAINER" sh -c \
    'pids=$(grep -la "'"$SID"'" /proc/[0-9]*/cmdline 2>/dev/null | cut -d/ -f3 | grep -vx "$$"); n=0
     for p in $pids; do kill "$p" 2>/dev/null && n=$((n+1)); done
     [ "$n" -gt 0 ] || { echo none; exit 0; }
     i=0; while [ $i -lt 5 ]; do
       alive=0; for p in $pids; do [ -d "/proc/$p" ] && alive=1; done
       [ "$alive" -eq 0 ] && { echo killed; exit 0; }
       sleep 1; i=$((i+1))
     done
     for p in $pids; do [ -d "/proc/$p" ] && kill -9 "$p" 2>/dev/null; done
     sleep 1
     alive=0; for p in $pids; do [ -d "/proc/$p" ] && alive=1; done
     [ "$alive" -eq 0 ] && echo killed-escalated || echo "STILL-ALIVE after KILL"' \
    2>/dev/null || echo unreachable
}
REVIEW_DONE=0
CLEANED=0
cleanup() {
  [ "$CLEANED" -eq 1 ] && return
  CLEANED=1
  rm -f "${TMPJ:-}"
  if [ "$REVIEW_DONE" -ne 1 ]; then
    r=$(stop_worker)
    echo "cleanup: worker signal result: $r (session $SID)" >&2
  fi
}
trap cleanup EXIT
# A signal trap that only cleans up RETURNS, and the poll loop resumes with
# the lock held (Codex, #1578) — clean up, then actually exit.
trap 'cleanup; exit 130' INT TERM

BRIEF_B64=$(printf %s "$BRIEF" | base64 -w0)
docker exec -d "$CONTAINER" sh -c "cd /workspace && omnigent run --server $SERVER --harness claude-sdk -r $SID -p \"\$(echo $BRIEF_B64 | base64 -d)\" --no-log > $KICK_LOG 2>&1" \
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
  # `waiting` is a NONTERMINAL Omnigent state (awaiting external work) — it
  # both proves the run started and must keep the loop polling (Greptile P1).
  case "$status" in (running|waiting) saw_running=1;; esac
  if [ "$status" != "running" ] && [ "$status" != "waiting" ] && [ "$status" != "poll-fail" ] && [ "$saw_running" = 1 ]; then
    break
  fi
  now=$(date +%s)
  if [ "$saw_running" = 0 ] && [ "$status" != "running" ] && [ "$status" != "waiting" ] && [ "$status" != "poll-fail" ]; then
    # The run may have started AND finished between two polls — evidence of an
    # assistant reply means completion, not a dead kick (Codex P2, #1578).
    if curl -sf --connect-timeout 5 --max-time 60 "$SERVER/v1/sessions/$SID" \
        | python3 -c "import json,sys;d=json.load(sys.stdin);sys.exit(0 if any(i.get('type')=='message' and (i.get('data',{}).get('role') or i.get('role'))=='assistant' for i in d.get('items',[])) else 1)" 2>/dev/null; then
      saw_running=1; continue
    fi
  fi
  if [ "$saw_running" = 0 ] && [ $((now - start)) -gt 300 ]; then
    echo "ALARM: session never reached 'running' within 300s (status: $status) — kick died silently; diagnose: docker exec $CONTAINER tail -40 $KICK_LOG" >&2
    exit 2
  fi
  if [ $((now - start)) -gt "$TIMEOUT_S" ]; then
    # Stop the detached worker before releasing the flock, or the next
    # invocation would run a SECOND polly beside the stuck one (Codex, #1578).
    r=$(stop_worker)
    echo "ALARM: review still '$status' after ${TIMEOUT_S}s — worker signal result: $r; inspect session $SID and docker exec $CONTAINER tail -40 $KICK_LOG" >&2
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
REVIEW_DONE=1  # normal completion — the cleanup trap must not kill a finished session's remnants
TMPJ=$(mktemp)
curl -sf --connect-timeout 5 --max-time 60 "$SERVER/v1/sessions/$SID" -o "$TMPJ" \
  || { echo "ALARM: could not fetch session $SID for the report" >&2; exit 2; }
rc=0
python3 - "$SID" "$TMPJ" <<'PY' || rc=$?
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
    c = str(c).strip()
    if c:  # tool_use-only assistant items reduce to "" — a blank is not a report (Codex, #1578)
        texts.append(c)
if not texts:
    print(f"ALARM: session {sys.argv[1]} ended idle with zero ASSISTANT messages — "
          "the kick died silently (check container auth: claude OAuth expires 2026-08-22).",
          file=sys.stderr)
    sys.exit(2)
# Reports can span messages — print the tail of the conversation, not just
# the last message (polly non-blocking on #1578).
print("\n\n---\n\n".join(texts[-3:]))
PY
if [ "$rc" -ne 0 ]; then
  # rc=2 is the zero-assistant-messages ALARM (already printed its own message).
  # Anything else is a parse/schema crash — still a report-stage incident, not
  # a usage error: exit 2 like every other alarm (Codex, #1578), never set -e's
  # raw 1.
  [ "$rc" -ne 2 ] && echo "ALARM: report extraction failed (exit $rc) for session $SID — inspect the payload shape" >&2
  exit 2
fi
