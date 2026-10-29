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
LANDING_VOICE_ARTIFACT=${ZOE_VOICE_ARTIFACT:-$HOME/.cache/zoe/voice_regression_last.json}
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

# landing_probe_speed_only [artifact]: 0 only when the last probe artifact is status=fail purely on SPEED
# (no said-vs-did regressions, VAD not failed/errored). Missing/unreadable/other = 1 (not retryable).
landing_probe_speed_only(){
  python3 - "${1:-$LANDING_VOICE_ARTIFACT}" <<'PY'
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(1)
vad = (d.get("vad") or {}).get("status")
ok = (d.get("status") == "fail" and not d.get("said_vs_did_regressions")
      and vad not in ("fail", "error") and d.get("speed_comparison", "compared") == "compared")
sys.exit(0 if ok else 1)
PY
}

# landing_run_watching_head <pr> <expected-head> <cmd...>: run <cmd> (own process group) and poll the PR head every
# ZOE_LANDING_HEAD_POLL_S (default 45) seconds. A push mid-run makes the result worthless (the artifact binds to the old
# head), so the command is TERMed (KILLed after 20 s) and 75 is returned instead of burning the rest of the probe.
landing_run_watching_head(){
  local pr=$1 head=$2 poll=${ZOE_LANDING_HEAD_POLL_S:-45} pid cur moved=0 i rc; shift 2
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
  wait "$pid" 2>/dev/null; rc=$?; set +m
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
  if landing_probe_speed_only && ! grep -qxF "$key" "$marks" 2>/dev/null; then
    echo "$key" >> "$marks"; echo retry-once
  else
    echo park-probe-failed
  fi
}

# landing_others_busy: 0 while another chain/lander is running anywhere on the box (the voice loop waits for quiet).
# ZOE_LANDING_NO_BUSY_WAIT=1 (tests) reports "never busy" - pgrep is system-wide and would see the live loops.
landing_others_busy(){
  [ -n "${ZOE_LANDING_NO_BUSY_WAIT:-}" ] && return 1
  pgrep -f 'docs_merge_chain\.sh|after_chain_land_1826\.sh|after_1826_land_1848\.sh|^bash .*/(land_queue|land_voice_pr)\.sh' >/dev/null
}
