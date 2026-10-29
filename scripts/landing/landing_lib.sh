#!/bin/bash
# landing_lib.sh - shared config + helpers for the operator landing tooling (docs/knowledge/landing-tooling.md).
# SOURCED by the sibling scripts; never executed. Every path is overridable so tests (and a second box) can redirect it.
#   ZOE_LANDING_HOME      state dir: voice-pending.txt, hold lists, logs/, voice-day-ok ...   (default ~/.zoe/agent-tools)
#   ZOE_LANDING_REPO      the live checkout gh/probe run from                                  (default /home/zoe/assistant)
#   ZOE_LANDING_GH_REPO   owner/name for GraphQL                                               (default jason-easyazz/zoe-ai-assistant)
#   ZOE_LANDING_NOW_EPOCH TEST ONLY: pretend "now" is this epoch second (night_busy.sh, land_voice_pr.sh deadline maths)
LANDING_DIR=${LANDING_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}
LANDING_HOME=${ZOE_LANDING_HOME:-$HOME/.zoe/agent-tools}
LANDING_REPO=${ZOE_LANDING_REPO:-/home/zoe/assistant}
LANDING_GH_REPO=${ZOE_LANDING_GH_REPO:-jason-easyazz/zoe-ai-assistant}
LANDING_HOLD_FILES=${ZOE_LANDING_HOLD_FILES:-"$LANDING_HOME/coord/hold.txt $LANDING_HOME/chain-hold.txt"}
LANDING_VOICE_ARTIFACT=${ZOE_VOICE_ARTIFACT:-$HOME/.cache/zoe/voice_regression_last.json}   # the probe's single shared slot
LANDING_ARTIFACT_DIR=${ZOE_LANDING_ARTIFACT_DIR:-$LANDING_HOME/probe-artifacts}               # per-PR snapshots taken by the lander
LANDING_WORKTREES=${ZOE_LANDING_WORKTREES:-$HOME/.worktrees}
LANDING_PANEL_HOST=${ZOE_LANDING_PANEL_HOST-zoe-pi}                                         # empty = skip the panel-quiet wait
LANDING_PANEL_LOG=${ZOE_LANDING_PANEL_LOG:-/home/pi/.zoe-voice/voice.log}
LANDING_PYTHON=${ZOE_LANDING_PYTHON:-/usr/bin/python3}
LANDING_RESOLVE_SCRIPT=${ZOE_LANDING_RESOLVE_SCRIPT:-$LANDING_HOME/resolve_inventory_conflict.sh}   # not in the repo (yet)
LANDING_LOG=${ZOE_LANDING_LOG:-$LANDING_HOME/logs/land_queue.log}

landing_now(){ echo "${ZOE_LANDING_NOW_EPOCH:-$(date +%s)}"; }

# landing_is_held <pr>: 0 when <pr> is listed (first token of a line; blank lines and #comments ignored) in ANY hold file.
landing_is_held(){
  local f
  for f in $LANDING_HOLD_FILES; do
    [ -r "$f" ] || continue
    grep -vE '^[[:space:]]*(#|$)' "$f" 2>/dev/null | awk -v p="$1" '$1==p{found=1} END{exit !found}' && return 0
  done
  return 1
}

# landing_unresolved_count <pr>: prints the number of UNRESOLVED review threads, paging through ALL of them.
# Non-zero exit (and no output) if the API could not be read - callers must treat that as "unknown = not safe", never as 0.
landing_unresolved_count(){
  local out
  out=$(bash "$LANDING_DIR/threads.sh" --count "$1" 2>/dev/null) || return 1
  case "$out" in ''|*[!0-9]*) return 1;; esac
  echo "$out"
}

# landing_probe_speed_only <artifact> [head]: 0 only when the artifact is status=fail purely on SPEED: no said-vs-did regressions,
# VAD not failed/errored, speed explicitly "compared" (a missing field is NOT retryable) and - when <head> is given - the artifact's
# recorded revision is that head. Missing/unreadable/anything else = 1 (not retryable).
landing_probe_speed_only(){
  python3 - "$1" "${2:-}" <<'PY'
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(1)
vad = (d.get("vad") or {}).get("status")
rev = ((d.get("revision") or {}).get("commit") or "")
ok = (d.get("status") == "fail" and not d.get("said_vs_did_regressions")
      and vad not in ("fail", "error") and d.get("speed_comparison") == "compared"
      and (not sys.argv[2] or (rev != "" and rev[:8] == sys.argv[2][:8])))
sys.exit(0 if ok else 1)
PY
}

# landing_run_watching_head <pr> <expected-head> <cmd...>: run <cmd> (own process group) and poll the PR head every
# ZOE_LANDING_HEAD_POLL_S (default 45) seconds. A push mid-run makes the result worthless (the artifact binds to the old
# head), so the command is TERMed (KILLed after 20 s) and 75 is returned instead of burning the rest of the probe.
landing_run_watching_head(){
  local pr=$1 head=$2 poll=${ZOE_LANDING_HEAD_POLL_S:-45} pid cur moved=0 i rc was_m=0; shift 2
  case $- in *m*) was_m=1;; esac
  set -m; "$@" & pid=$!
  while kill -0 "$pid" 2>/dev/null; do
    for ((i=0; i<poll; i++)); do kill -0 "$pid" 2>/dev/null || break; sleep 1; done
    kill -0 "$pid" 2>/dev/null || break
    cur=$(gh pr view "$pr" --json headRefOid --jq .headRefOid 2>/dev/null)
    if [ -n "$cur" ] && [ "$cur" != "$head" ]; then
      moved=1; kill -TERM -- "-$pid" 2>/dev/null
      for ((i=0; i<20; i++)); do kill -0 "$pid" 2>/dev/null || break; sleep 1; done
      kill -KILL -- "-$pid" 2>/dev/null; break
    fi
  done
  wait "$pid" 2>/dev/null; rc=$?; [ "$was_m" = 1 ] || set +m
  [ "$moved" = 1 ] && return 75
  return "$rc"
}

# landing_requeue_decision <pr> <rc>: what the voice loop does with a PR whose lander exited <rc> and is still open.
# Prints one word: requeue | retry-once | park-probe-failed.
#   rc 4 (probe NOT PASS): a SPEED-only fail is re-queued ONCE per (pr, head) - load spikes fail the speed bar without any
#   code fault; anything else (said-vs-did regression, a second speed fail on the same head) is parked for a human.
#   Every other rc keeps today's behaviour: back in the queue.
landing_requeue_decision(){
  local pr=$1 rc=${2:-} head key marks="$LANDING_HOME/voice-probe-retried.txt"
  if [ "$rc" != 4 ]; then echo requeue; return; fi
  head=$(gh pr view "$pr" --json headRefOid --jq .headRefOid 2>/dev/null); key="$pr:${head:0:12}"
  # the per-PR snapshot the lander took right after ITS probe (the shared artifact slot belongs to whichever probe ran last)
  if landing_probe_speed_only "$LANDING_ARTIFACT_DIR/$pr.json" "$head" && ! grep -qxF "$key" "$marks" 2>/dev/null; then
    echo "$key" >> "$marks"; echo retry-once
  else
    echo "$key" >> "$LANDING_HOME/probe-failed-heads.txt"; echo park-probe-failed
  fi
}

# landing_head_parked <pr>: 0 when the PR's CURRENT head was parked after a non-retryable probe fail (autoland must not refill it).
landing_head_parked(){
  local head; head=$(gh pr view "$1" --json headRefOid --jq .headRefOid 2>/dev/null)
  [ -n "$head" ] && grep -qxF "$1:${head:0:12}" "$LANDING_HOME/probe-failed-heads.txt" 2>/dev/null
}

# landing_artifact_fresh <artifact> <t0-epoch>: 0 only if the artifact file was (re)written at or after <t0> - i.e. by THIS probe attempt.
landing_artifact_fresh(){ [ "$(stat -c %Y "$1" 2>/dev/null || echo 0)" -ge "$2" ]; }

# landing_others_busy: 0 while another chain/lander is running anywhere on the box (the voice loop waits for quiet).
# ZOE_LANDING_NO_BUSY_WAIT=1 (tests) reports "never busy" - pgrep is system-wide and would see the live loops.
landing_others_busy(){
  [ -n "${ZOE_LANDING_NO_BUSY_WAIT:-}" ] && return 1
  pgrep -f 'docs_merge_chain\.sh|after_chain_land_1826\.sh|after_1826_land_1848\.sh|^bash .*/(land_queue|land_voice_pr)\.sh' >/dev/null
}

# landing_probe_window <lane-wait-s> <pr> <head> <cmd...>: the Kokoro-silent part of a landing, in the ONLY safe order:
#   1. take the test-lane lock (fd 8) - bounded by <lane-wait-s>; not won = return 9 with Kokoro NEVER touched
#   2. only then stop Kokoro, settle, run <cmd> under landing_run_watching_head (rc 75 = head moved)
#   3. always restart Kokoro and release the lane. <cmd> must close fd 8 for its children (8>&-).
# (2026-10-29: Kokoro used to be stopped first, then the probe waited an hour for the lane - Zoe mute all morning.)
# ZOE_TEST_LANE_LOCK / ZOE_LANDING_SETTLE_S are overridable for tests.
landing_probe_window(){
  local wait_s=$1 pr=$2 head=$3 rc i; shift 3
  exec 8>"${ZOE_TEST_LANE_LOCK:-/tmp/zoe-test-lane.lock}"
  flock -x -w "$wait_s" 8 || { exec 8>&-; return 9; }
  # the lane wait can eat the whole budget: re-validate (night gate + probe time left) BEFORE Kokoro is touched
  if declare -F landing_pre_stop >/dev/null && ! landing_pre_stop; then flock -u 8; exec 8>&-; return 9; fi
  systemctl --user stop kokoro-tts.service
  if [ "${ZOE_LANDING_SETTLE_S:-15}" != 0 ]; then
    for i in $(seq 1 30); do [ "$(awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo)" -ge 1500 ] && break; sleep 2; done
    sleep "${ZOE_LANDING_SETTLE_S:-15}"
  fi
  landing_run_watching_head "$pr" "$head" "$@"; rc=$?
  systemctl --user start kokoro-tts.service
  flock -u 8; exec 8>&-
  return "$rc"
}
