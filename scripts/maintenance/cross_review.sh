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
# Exit codes: 0 = report retrieved (printed to stdout), 1 = usage or a budget
# that does not fit inside the caller's subprocess timeout (see BUDGET below),
# 2 = kick/poll failure or silent-death alarm (session ended with no report).
#
# Every HTTP body is parsed by scripts/maintenance/cross_review_poll.py, never
# by an inline `json.load`. That module checks HTTP status / emptiness /
# content-type before parsing, retries transient faults with bounded backoff,
# and on exhaustion prints ONE terminal line naming the session and
# "re-dispatch required". Its distinct exit codes (3 never-registered,
# 4 poll-lost, 5 timeout, 6 never-running, 7 dispatch-failed) are diagnostic;
# this wrapper collapses them all to its long-standing public `exit 2`.
set -euo pipefail

POLLER="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/cross_review_poll.py"
[ -f "$POLLER" ] || { echo "ALARM: poller module missing: $POLLER" >&2; exit 2; }

SERVER="${OMNIGENT_SERVER:-http://127.0.0.1:6767}"
# polly's id in the BARE hex form 0.7.0 returns (`GET /v1/agents`); <=0.4.0
# emitted `ag_<hex>`. The prefixed form still resolves, but only via omnigent's
# `_LEGACY_ID_PREFIXES` back-compat strip, which is type-blind (`host_<agent-hex>`
# binds too) — it validates nothing. See omnigent_issue_executor._omnigent_agent_id.
POLLY_ID="${OMNIGENT_POLLY_ID:-057995d1517418e6839f51d340785dd6}"
CONTAINER="${OMNIGENT_CONTAINER:-zoe-omnigent}"
# 1800, not 2400: the poll budget is the only large term in the BUDGET
# arithmetic below, and 2400 put the wrapper's worst-case wall ABOVE the
# caller's 2500s subprocess timeout (Codex, #1618). It came down again once the
# bounded flock wait joined the sum (Codex P1, #1624). 30 minutes remains far
# more than a polly cross-review has ever needed.
TIMEOUT_S="${CROSS_REVIEW_TIMEOUT_S:-1800}"
case "$TIMEOUT_S" in (*[!0-9]*|'') echo "ALARM: CROSS_REVIEW_TIMEOUT_S must be a positive integer (seconds), got: $TIMEOUT_S" >&2; exit 1;; esac
[ "$TIMEOUT_S" -gt 0 ] || { echo "ALARM: CROSS_REVIEW_TIMEOUT_S must be > 0" >&2; exit 1; }
# Validated HERE, not left to argparse: an unvalidated value reaches the poller
# as a usage error and prints a traceback instead of this script's single
# session-scoped alarm line (codex cross-review, #1618).
REGISTER_TIMEOUT_S="${CROSS_REVIEW_REGISTER_TIMEOUT_S:-60}"
case "$REGISTER_TIMEOUT_S" in (*[!0-9]*|'') echo "ALARM: CROSS_REVIEW_REGISTER_TIMEOUT_S must be a positive integer (seconds), got: $REGISTER_TIMEOUT_S" >&2; exit 1;; esac
[ "$REGISTER_TIMEOUT_S" -gt 0 ] || { echo "ALARM: CROSS_REVIEW_REGISTER_TIMEOUT_S must be > 0" >&2; exit 1; }

# ------------------------------------------------------------------- BUDGET ---
# The wrapper's WORST-CASE WALL MUST STAY BELOW THE CALLER'S TIMEOUT.
# services/zoe-data/pipeline_cross_review.py runs this script under
# `subprocess.run(..., timeout=2500)`. That timeout kills only THIS shell: the
# EXIT trap never fires, so the detached polly worker survives while the flock
# releases — the next invocation then runs a SECOND polly beside the orphan,
# which is precisely the failure the cleanup trap exists to prevent. The old
# defaults summed to 2580s and were therefore already inverted (Codex, #1618).
#
# Every phase is explicitly bounded, and the sum is checked HERE rather than
# left as a comment, so raising CROSS_REVIEW_TIMEOUT_S cannot silently
# reintroduce the inversion — it fails loudly at startup instead.
#
# THE `flock` WAIT COUNTS TOO, and an earlier revision of this block wrongly
# excluded it on the grounds that nothing exists to orphan while merely waiting
# (Codex P1, #1624). That misses which clock is running: the caller's timer
# starts at `subprocess.run`, BEFORE the lock is acquired. So a lock wait longer
# than the margin leaves less than the post-lock worst case, and the script then
# proceeds to create a session and kick a worker with a deadline it can no
# longer meet — reproducing exactly the orphan this block exists to prevent.
# The wait is therefore BOUNDED, and failing loudly at the lock is strictly
# better than being killed mid-review: a queued invocation would in any case
# wait most of a full review, so `LOCK_WAIT_MAX_S` is a cap on futility, not on
# legitimate serialization.
CALLER_TIMEOUT_S=2500       # mirrors _CROSS_REVIEW_TIMEOUT_S; pinned by tests/unit/test_cross_review_poll.py
LOCK_WAIT_MAX_S=120         # flock -w: the caller's clock runs during this too
CREATE_MAX_S=60             # curl --max-time on POST /v1/sessions
REGISTER_HTTP_TIMEOUT_S=30  # await-registration overshoots its budget by at most one request
KICK_MAX_S=30               # timeout(1) around the detached docker-exec kick
POLL_HTTP_TIMEOUT_S=60      # poll() returns within --timeout-s + one request
FETCH_MAX_S=60              # curl --max-time on the report GET
CLEANUP_MAX_S=20            # timeout(1) around stop_worker's docker exec
# `timeout N cmd` is NOT a hard bound: it sends TERM and then waits forever if
# the command ignores it, so a wedged docker CLI would run past the advertised
# budget and be killed by the caller instead — trap skipped, worker orphaned
# (Codex P1 + Greptile P1, #1624). `-k` is what escalates to KILL. It applies to
# BOTH timeout-wrapped phases, so it is counted twice.
TIMEOUT_KILL_AFTER_S=5      # timeout -k: the KILL escalation after TERM is ignored
OVERHEAD_S=$(( LOCK_WAIT_MAX_S + CREATE_MAX_S + REGISTER_TIMEOUT_S + REGISTER_HTTP_TIMEOUT_S + KICK_MAX_S ))
OVERHEAD_S=$(( OVERHEAD_S + POLL_HTTP_TIMEOUT_S + FETCH_MAX_S + CLEANUP_MAX_S ))
OVERHEAD_S=$(( OVERHEAD_S + 2 * TIMEOUT_KILL_AFTER_S ))
WORST_CASE_S=$(( TIMEOUT_S + OVERHEAD_S ))
[ "$WORST_CASE_S" -lt "$CALLER_TIMEOUT_S" ] || {
  echo "ALARM: budget inversion — worst-case wall ${WORST_CASE_S}s (poll ${TIMEOUT_S}s + ${OVERHEAD_S}s of bounded phases) is not below the caller's ${CALLER_TIMEOUT_S}s subprocess timeout; a kill there orphans the detached worker. Lower CROSS_REVIEW_TIMEOUT_S (max $(( CALLER_TIMEOUT_S - OVERHEAD_S - 1 ))) or CROSS_REVIEW_REGISTER_TIMEOUT_S." >&2
  exit 1
}

[ $# -ge 2 ] || { echo "usage: $0 <PR-number> \"<contract>\"" >&2; exit 1; }

# Serialize concurrent CROSS-REVIEW invocations (RAM discipline). Note: other
# polly launch paths (omnigent_issue_executor, the Flue heavy lane) do not take
# this lock — a shared lease across all launchers is future cross-subsystem
# work (Codex, #1578); this bounds what THIS wrapper can add to the load.
# BOUNDED (`-w`), because the caller's subprocess timer is already running while
# we block here — see BUDGET above. An unbounded wait silently eats the margin
# and the run gets killed AFTER it has spawned a worker (Codex P1, #1624).
exec 9>/tmp/zoe-cross-review.lock
flock -w "$LOCK_WAIT_MAX_S" 9 || {
  echo "ALARM: another cross-review still holds /tmp/zoe-cross-review.lock after ${LOCK_WAIT_MAX_S}s — refusing to start with too little of the caller's ${CALLER_TIMEOUT_S}s budget left to finish; retry once it releases" >&2
  exit 2
}
PR="$1"; shift
# Join ALL remaining words — an unquoted multiword contract must not silently
# truncate to its first word (Codex, #1578).
CONTRACT="$*"
case "$PR" in (*[!0-9]*|'') echo "ALARM: PR must be numeric, got: $PR" >&2; exit 1;; esac

# The create response goes through a FILE, then a validating parser. Piping it
# into `json.load(sys.stdin)['id']` conflated "the server refused" with "the
# server accepted and answered with an empty body" — and the second case leaves
# an ORPHANED session whose id nobody holds, which is exactly how the 2026-08-03
# review on #1614 came back `not_found` with no poller attached.
TMPC=$(mktemp)
curl -sf --connect-timeout 5 --max-time "$CREATE_MAX_S" -X POST "$SERVER/v1/sessions" -H 'Content-Type: application/json' \
  -d "{\"agent_id\":\"$POLLY_ID\",\"title\":\"cross-review PR #$PR\"}" -o "$TMPC" \
  || { rm -f "$TMPC"; echo "ALARM: session create failed against $SERVER" >&2; exit 2; }
SID=$(python3 "$POLLER" session-id --payload "$TMPC") || { rm -f "$TMPC"; exit 2; }
rm -f "$TMPC"
# The ID is interpolated into an sh -c below — reject anything that isn't a
# plain alnum token, optionally conv_-prefixed (same rule as
# omnigent_issue_executor; Codex, #1578).
#
# The PREFIX is not the security property and must not be required: omnigent
# <=0.4.0 returned `conv_<hex>`, 0.7.0 returns the BARE `<hex>` (it dropped the
# type prefix from conv_/ag_/host_ ids alike). Demanding `conv_*` made every
# cross-review abort with "malformed session id" on 0.7.0. The strict charset
# is what keeps the interpolation injection-free, and it is unchanged.
#
# The {16,} LENGTH FLOOR is a second, independent property (Codex P1 on #1589),
# and it is NOT cosmetic: stop_worker() below signals every container process
# whose /proc/<pid>/cmdline CONTAINS this id — a SUBSTRING scan. A degenerate
# short id would therefore match almost everything. Measured in the live
# container: a one-character id "e" matches 5 processes (the omnigent SERVER
# among them) against 2 for a real 32-hex id, so a malformed short id turns
# cleanup into a container-wide kill. The old conv_ requirement blocked that
# only by accident. Real ids are 32 hex; 16 is a deliberately loose floor that
# still kills the degenerate case without re-pinning an upstream id length we
# do not control.
[[ "$SID" =~ ^(conv_)?[A-Za-z0-9]{16,}$ ]] || { echo "ALARM: malformed session id: $SID" >&2; exit 2; }
echo "session: $SID" >&2

# Dispatch-race guard. A POST that returned an id is NOT proof that a
# subsequent GET resolves it: the crashed 2026-08-03 run's session was
# `not_found` afterwards. Confirm the session is readable BEFORE spending a
# worker on it — bailing here costs nothing, whereas an unreadable session
# after the kick means a detached polly with no poller. Bounded and short.
python3 "$POLLER" await-registration --server "$SERVER" --session-id "$SID" \
  --budget-s "$REGISTER_TIMEOUT_S" --http-timeout-s "$REGISTER_HTTP_TIMEOUT_S" || exit 2

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
  # Bounded by timeout(1): an unresponsive docker daemon must not stretch the
  # cleanup past the budget the BUDGET block promised (Codex, #1618).
  #
  # SC2016 is deliberate and load-bearing: everything inside the single quotes
  # runs INSIDE the container, so `$pids`, `$$`, `$p` and `$n` must reach it
  # unexpanded. Only `$SID` is host-side, and it is spliced in via the
  # '"$SID"' quote dance below. (The directive is needed only because the
  # timeout(1) prefix stops shellcheck recognising the `sh -c` payload as code.)
  # shellcheck disable=SC2016
  timeout -k "$TIMEOUT_KILL_AFTER_S" "$CLEANUP_MAX_S" docker exec "$CONTAINER" sh -c \
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
    2>/dev/null || echo "unreachable (docker exec failed or exceeded ${CLEANUP_MAX_S}s)"
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
# `docker exec -d` returns as soon as the container accepts the exec, but that
# is still an unbounded RPC to the daemon — bound it so the BUDGET arithmetic
# above holds end to end (Codex, #1618).
timeout -k "$TIMEOUT_KILL_AFTER_S" "$KICK_MAX_S" docker exec -d "$CONTAINER" sh -c "cd /workspace && omnigent run --server $SERVER --harness claude-sdk -r $SID -p \"\$(echo $BRIEF_B64 | base64 -d)\" --no-log > $KICK_LOG 2>&1" \
  || { echo "ALARM: docker-exec kick failed" >&2; exit 2; }

# Poll. The loop lives in cross_review_poll.py so that every response body is
# status/emptiness/content-type checked before parsing. On success it prints the
# terminal status; on any failure it prints its own single terminal ALARM line
# and exits non-zero — the EXIT trap then stops the detached worker before the
# flock releases, so the next invocation cannot run a SECOND polly beside a
# stuck one (Codex, #1578).
#
# The old inline loop's real defect was that a bad body degraded to the sentinel
# `poll-fail`, which was neither terminal nor alarming — a vanished session
# (404, which `curl -sf` renders as an empty body) spun in silence for the full
# ${TIMEOUT_S}s and then blamed the timeout. A fast-failing endpoint is now
# declared poll-lost after 121s of sleeps at these defaults (a vanished session
# ~33s); a STALLING one adds up to --http-timeout-s per attempt on top, i.e.
# 121 + 8*60 = 601s, and the whole call still returns within
# --timeout-s + --http-timeout-s. A ~60s server restart is ridden out. All of
# these numbers are pinned by tests/unit/test_cross_review_poll.py.
set +e
status=$(python3 "$POLLER" poll --server "$SERVER" --session-id "$SID" --timeout-s "$TIMEOUT_S" \
  --http-timeout-s "$POLL_HTTP_TIMEOUT_S")
poll_rc=$?
set -e
if [ "$poll_rc" -ne 0 ]; then
  echo "diagnose: docker exec $CONTAINER tail -40 $KICK_LOG" >&2
  exit 2
fi

case "$status" in
  idle) : ;;  # normal completion — sessions end idle, never 'completed'
  *) echo "ALARM: session ended in error status '$status' — inspect session $SID" >&2; exit 2 ;;
esac

# Report extraction. The session JSON goes through a temp FILE, never a pipe
# into stdin (SC2259; polly finding #1 on #1578, reproduced live). An idle
# session with NO messages is the silent-launch-failure signature — alarm,
# never report it as a clean review.
REVIEW_DONE=1  # normal completion — the cleanup trap must not kill a finished session's remnants
TMPJ=$(mktemp)
curl -sf --connect-timeout 5 --max-time "$FETCH_MAX_S" "$SERVER/v1/sessions/$SID" -o "$TMPJ" \
  || { echo "ALARM: could not fetch session $SID for the report" >&2; exit 2; }
# The extractor prints its own ALARM for every unusable payload shape (missing,
# empty, non-JSON, non-object, zero assistant messages) — this wrapper only maps
# the outcome onto its public `exit 2`, never set -e's raw 1 (Codex, #1578).
python3 "$POLLER" report --session-id "$SID" --payload "$TMPJ" || exit 2
