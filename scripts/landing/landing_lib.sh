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

# landing_probe_speed_only <artifact> [head]: 0 ONLY with positive evidence of a pure speed failure: status=fail, `reason` made solely
# of "SPEED ..." clauses (split on "; " - a COLD, VAD, cleanup, clip-expectation or error clause disqualifies), no said-vs-did
# regressions, VAD pass, cold_first_turn pass/absent/disabled, speed explicitly "compared", and - when <head> is given - the artifact's
# revision is that head. Missing/unreadable/anything else = 1 (park, not retry).
landing_probe_speed_only(){
  python3 - "$1" "${2:-}" <<'PY'
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(1)
parts = [p for p in str(d.get("reason") or "").split("; ") if p.strip()]
cold = (d.get("cold_first_turn") or {}).get("status")
rev = ((d.get("revision") or {}).get("commit") or "")
ok = (d.get("status") == "fail" and parts and all(p.startswith("SPEED ") for p in parts)
      and not d.get("said_vs_did_regressions")
      and (d.get("vad") or {}).get("status") == "pass"
      and cold in (None, "pass", "disabled")
      and d.get("speed_comparison") == "compared"
      and (not sys.argv[2] or (rev != "" and rev[:8] == sys.argv[2][:8])))
sys.exit(0 if ok else 1)
PY
}

# landing_requeue_decision <pr> <rc>: what the voice loop does with a PR whose lander exited <rc> and is still open.
# Prints one word: requeue | requeue-front | retry-once | park-probe-failed | drop.
#   rc 4 (probe NOT PASS): a SPEED-only fail is re-queued ONCE per (pr, head) - load spikes fail the speed bar without any
#   code fault; anything else (said-vs-did regression, a second speed fail on the same head) is parked for a human.
#   rc 12 (probe PASSED, then BEHIND/DIRTY or the CI wait ended unmerged): FRONT of the queue - it was ready and lost its turn to
#   something else, so it must not wait behind every newer PR (it is still re-probed: the evidence binds to the exact head/tree).
#   Every other rc keeps today's behaviour: back in the queue.
landing_requeue_decision(){
  local pr=$1 rc=${2:-} head key marks="$LANDING_HOME/voice-probe-retried.txt"
  if [ "$rc" = 11 ]; then echo drop; return; fi   # parked head already: do not run it again
  if [ "$rc" = 12 ]; then echo requeue-front; return; fi
  if [ "$rc" != 4 ]; then echo requeue; return; fi
  head=$(gh pr view "$pr" --json headRefOid --jq .headRefOid 2>/dev/null); key="$pr:${head:0:12}"
  # an artifact for an OLDER head means a push landed after the failed probe: the new head is untested, not failed - requeue it
  local arev; arev=$(python3 -c 'import json,sys;print((json.load(open(sys.argv[1])).get("revision") or {}).get("commit","")[:8])' "$LANDING_ARTIFACT_DIR/$pr.json" 2>/dev/null)
  if [ -n "$arev" ] && [ "$arev" != "${head:0:8}" ]; then echo requeue; return; fi
  # the per-PR snapshot the lander took right after ITS probe (the shared artifact slot belongs to whichever probe ran last)
  if landing_probe_speed_only "$LANDING_ARTIFACT_DIR/$pr.json" "$head" && ! grep -qxF "$key" "$marks" 2>/dev/null; then
    echo "$key" >> "$marks"; echo retry-once
  else
    echo "$key" >> "$LANDING_HOME/probe-failed-heads.txt"; echo park-probe-failed
  fi
}

# landing_probe_timeout <seconds-left>: wall-clock cap for the probe command = time left minus a 120 s cleanup margin (floor 60).
landing_probe_timeout(){ if [ "$1" -gt 180 ]; then echo $(( $1 - 120 )); else echo 60; fi; }

# landing_head_parked <pr>: 0 when the PR's CURRENT head was parked after a non-retryable probe fail (autoland must not refill it).
landing_head_parked(){
  local head; head=$(gh pr view "$1" --json headRefOid --jq .headRefOid 2>/dev/null)
  [ -n "$head" ] && grep -qxF "$1:${head:0:12}" "$LANDING_HOME/probe-failed-heads.txt" 2>/dev/null
}

# landing_evidence_reusable <probed-sha> <new-head>: 0 only when THIS lander's probe evidence (the shared artifact, which must still
# record <probed-sha>) may be REUSED at <new-head> - the probed commit is an ancestor of <new-head> and the tree diff between them touches
# nothing voice-relevant (voice_gate_check.py --allow-reuse; offline, over $LANDING_REPO's objects). Every other check the gate makes
# (pass, fresh, clean, baseline, VAD, per-clip) still applies. Prints the checker's decision line(s). Anything unreadable = 1 (re-probe).
# The checker is the LIVE checkout's copy (trusted main), never the PR's. ZOE_LANDING_GATE_CHECK overrides (tests).
LANDING_GATE_CHECK=${ZOE_LANDING_GATE_CHECK:-$LANDING_REPO/scripts/maintenance/voice_gate_check.py}
landing_evidence_reusable(){
  local probed=$1 new=$2 rec
  case "$probed$new" in *[!0-9a-f]*|'') echo "not two full shas ('$probed' '$new')"; return 1;; esac
  [ "${#probed}" = 40 ] && [ "${#new}" = 40 ] || { echo "not two full shas"; return 1; }
  rec=$(python3 "$LANDING_GATE_CHECK" --artifact "$LANDING_VOICE_ARTIFACT" --print-artifact-commit 2>/dev/null) || rec=""
  [ "$rec" = "$probed" ] || { echo "the shared artifact no longer records the probed ${probed:0:8} (now '${rec:0:8}') - not this probe's evidence"; return 1; }
  python3 "$LANDING_GATE_CHECK" --repo "$LANDING_REPO" --artifact "$LANDING_VOICE_ARTIFACT" --require \
    --expect-revision "$new" --allow-reuse 2>&1 | grep -E '^voice-gate: (evidence reuse|OK|BLOCKED)' | cut -c1-600
  return "${PIPESTATUS[0]}"
}

# landing_gate_state <run-id> <prev-attempt>: "<status>/<conclusion>" of the run's CURRENT attempt, or "pending/" until a rerun has registered.
# A rerun request returns before the new attempt exists, so an immediate run view still shows the PREVIOUS attempt as completed - 2026-11-11, #2408:
# the lander logged "gate: completed/failure" two seconds after a rerun that went on to succeed (the check on the head was success). The attempt
# number only increases once the rerun is real, so a state is believed only when attempt > <prev-attempt>. Unreadable = "unreadable/".
landing_gate_state(){
  local out att
  out=$(gh run view "$1" --json attempt,status,conclusion --jq '"\(.attempt) \(.status)/\(.conclusion)"' 2>/dev/null) || { echo "unreadable/"; return 0; }
  att=${out%% *}
  case "$att" in ''|*[!0-9]*) echo "unreadable/";; *) if [ "$att" -gt "${2:-0}" ]; then echo "${out#* }"; else echo "pending/"; fi;; esac
}

# landing_artifact_fresh <artifact> <t0-epoch>: 0 only if the artifact file was (re)written at or after <t0> - i.e. by THIS probe attempt.
landing_artifact_fresh(){ [ "$(stat -c %Y "$1" 2>/dev/null || echo 0)" -ge "$2" ]; }

# landing_running <script-basename>...: 0 if a REAL process is running one of those scripts, else 1. Decided from /proc/<pid>/cmdline
# argv, NOT `pgrep -f` (which matches any command line that merely MENTIONS the name: 2026-11-05, 37 agent waiter shells
# `bash -c '... until ! pgrep -f land_voice_pr.sh ...'` self-matched and autoland believed a landing was always running).
# A match = argv[0] is the script itself, or argv[0] is a shell and the first non-option argument is the script; `bash -c <text>`
# is never a match. The calling shell and its parent are skipped (a lander must not see itself as "another" one).
landing_running(){
  local p c a0 i skip=" $$ $PPID " n b root=${LANDING_PROC_ROOT:-/proc} errf err
  errf=$(mktemp 2>/dev/null) || errf=/dev/null
  for p in "$root"/[0-9]*; do
    p=${p#"$root"/}; case "$skip" in *" $p "*) continue;; esac
    # stderr goes to a file through a brace group (a redirection that fails to OPEN is reported by the shell before a later 2>/dev/null applies); ONLY the
    # vanished-pid ENOENT is dropped - any other read error (EACCES, EISDIR, ...) is re-emitted, so it is not hidden along with the race
    c=()
    if ! { mapfile -d '' -t c < "$root/$p/cmdline"; } 2>"$errf"; then
      [ "$errf" = /dev/null ] || { err=$(grep -v 'No such file or directory' "$errf"); [ -z "$err" ] || printf '%s\n' "$err" >&2; }
      continue
    fi
    [ "${#c[@]}" -gt 0 ] || continue
    a0=${c[0]##*/}; b=
    case "$a0" in
      bash|sh|dash)
        for ((i=1; i<${#c[@]}; i++)); do
          if [[ "${c[i]}" =~ ^-[a-zA-Z]*c ]]; then break; fi
          case "${c[i]}" in -*) continue;; *) b=${c[i]##*/}; break;; esac
        done;;
      *) b=$a0;;
    esac
    for n in "$@"; do [ "$b" = "$n" ] && { [ "$errf" = /dev/null ] || rm -f "$errf"; return 0; }; done
  done
  [ "$errf" = /dev/null ] || rm -f "$errf"
  return 1
}

# landing_others_busy: 0 while another chain/lander is running anywhere on the box (the voice loop waits for quiet).
# ZOE_LANDING_NO_BUSY_WAIT=1 (tests) reports "never busy" - the scan is system-wide and would see the live loops.
landing_others_busy(){
  [ -n "${ZOE_LANDING_NO_BUSY_WAIT:-}" ] && return 1
  landing_running docs_merge_chain.sh after_chain_land_1826.sh after_1826_land_1848.sh land_queue.sh land_voice_pr.sh
}

# landing_probe_window <lane-wait-s> <pr> <head> <cmd...>: the Kokoro-silent part of a landing, in the ONLY safe order:
#   1. take the test-lane lock (fd 8) - bounded by <lane-wait-s>; not won = return 9 with Kokoro NEVER touched
#   2. only then stop Kokoro, settle, run <cmd> TO COMPLETION (the caller bounds it with `timeout`; a push mid-probe is caught by the
#      artifact-revision == current-head check before anything is armed)
#   3. always restart Kokoro and release the lane. <cmd> must close fd 8 for its children (8>&-).
#   (2026-11-10: between the quiet-household check and the Kokoro stop it also takes the chat gate + drain, landing_quiet_chat_take below,
#   held until <cmd> ends; fds 6/7 are closed for <cmd>'s children.)
# (2026-10-29: Kokoro used to be stopped first, then the probe waited an hour for the lane - Zoe mute all morning.)
# ZOE_TEST_LANE_LOCK / ZOE_LANDING_SETTLE_S are overridable for tests.
# 2026-11-02: a probe silences Zoe for ~15-30 min, so it also waits for a QUIET HOUSEHOLD (landing_household_quiet) - the
# owner-allowed daytime landings muted speech 214 min on 10-29 while people were talking to her.

# landing_household_quiet: 0 when nobody has spoken to Zoe for ZOE_LANDING_QUIET_MIN minutes (default 20), read from zoe-data
# /health `last_voice_turn_age_s` (null = no turn since zoe-data started = quiet). FAIL CLOSED: an unreadable or malformed
# signal is "not quiet" - the landing re-queues with Kokoro untouched, it never guesses. ZOE_LANDING_HEALTH_URL overrides (tests).
landing_household_quiet(){
  local min=${ZOE_LANDING_QUIET_MIN:-20} body age
  case "$min" in ''|*[!0-9]*) min=20;; esac
  body=$(curl -fsS -m 5 "${ZOE_LANDING_HEALTH_URL:-http://127.0.0.1:${ZOE_PORT:-8000}/health}" 2>/dev/null) || { echo "household quiet: /health unreadable - not quiet" >&2; return 1; }
  age=$(printf '%s' "$body" | jq -r 'if has("last_voice_turn_age_s") then (.last_voice_turn_age_s // "none") else "missing" end' 2>/dev/null) || age=missing
  case "$age" in
    none) return 0;;
    missing|'') echo "household quiet: no last_voice_turn_age_s on /health - not quiet" >&2; return 1;;
  esac
  age=${age%%.*}
  case "$age" in ''|*[!0-9]*) echo "household quiet: unreadable age - not quiet" >&2; return 1;; esac
  [ "$age" -ge $(( min * 60 )) ] && return 0
  echo "household quiet: last voice turn ${age}s ago (< ${min} min) - not silencing Zoe" >&2; return 1
}

landing_probe_window(){
  local wait_s=$1 pr=$2 head=$3 rc i; shift 3
  exec 8>"${ZOE_TEST_LANE_LOCK:-/tmp/zoe-test-lane.lock}"
  flock -x -w "$wait_s" 8 || { exec 8>&-; return 9; }
  # the lane wait can eat the whole budget: re-validate (night gate + probe time left) BEFORE Kokoro is touched
  [ "$(gh pr view "$pr" --json headRefOid --jq .headRefOid 2>/dev/null)" = "$head" ] || { flock -u 8; exec 8>&-; return 9; }   # a push during the lock waits: do not silence Zoe for a stale head
  if declare -F landing_pre_stop >/dev/null && ! landing_pre_stop; then flock -u 8; exec 8>&-; return 9; fi
  landing_household_quiet || { flock -u 8; exec 8>&-; return 9; }   # someone is talking to her: re-queue, Kokoro untouched
  # 2026-11-10: stop NEW chat turns (QA drivers) and wait for the ones in flight BEFORE Kokoro is touched; not won = re-queue, untouched
  landing_quiet_chat_take "${ZOE_PROBE_GATE_WAIT_S:-120}" || { flock -u 8; exec 8>&-; return 9; }
  systemctl --user stop kokoro-tts.service
  if [ "${ZOE_LANDING_SETTLE_S:-15}" != 0 ]; then
    for i in $(seq 1 30); do [ "$(awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo)" -ge 1500 ] && break; sleep 2; done
    sleep "${ZOE_LANDING_SETTLE_S:-15}"
  fi
  "$@" 6>&- 7>&-; rc=$?
  systemctl --user start kokoro-tts.service
  landing_quiet_chat_release
  flock -u 8; exec 8>&-
  return "$rc"
}

# landing_disarm_unless_merged <pr>: disable auto-merge unless the PR is already MERGED (EXIT-trap safe).
landing_disarm_unless_merged(){
  [ "$(gh pr view "$1" --json state --jq .state 2>/dev/null)" = MERGED ] || gh pr merge "$1" --disable-auto >/dev/null 2>&1
}

# landing_required_state <pr>: fail | pending | ok | unknown for the REQUIRED checks of the current head. A LANDING_REQUIRED_CHECKS context
# that has not REPORTED yet is pending; an empty/unreadable answer is unknown - never "ok".
LANDING_REQUIRED_CHECKS=${ZOE_LANDING_REQUIRED_CHECKS:-"validate secret-scan"}
landing_required_state(){
  local out
  out=$(gh pr checks "$1" --required 2>/dev/null)
  [ -n "$out" ] || { echo unknown; return 0; }
  printf '%s\n' "$out" | awk -F'\t' -v req="$LANDING_REQUIRED_CHECKS" '
    {s=tolower($2); seen[$1]=1; if (s=="fail"||s=="cancel") f=1; else if (s=="pending") p=1}
    END{n=split(req, r, " "); for (k=1; k<=n; k++) if (r[k]!="" && !(r[k] in seen)) p=1; print f?"fail":(p?"pending":"ok")}'
}

# landing_wait_merged <pr> <armed-head> [tries] [cap-tries]: poll state AND head after arming. 0 = MERGED; 5 = the head moved off
# <armed-head> (auto-merge disabled immediately); 1 = not merged (auto-merge disabled): a required check FAILED/cancelled, mergeState
# DIRTY/BEHIND, the night deadline came, or the wait ran out. The wait is <tries> steps (default 180 x 10 s = 30 min) but is EXTENDED, up
# to <cap-tries> steps (default 450 = 75 min), while the head is unchanged and a required check is still pending OR its state could not be
# read (landing_required_state): the box's own runner can take >30 min on `validate` under load, and giving up there threw away a whole
# probe (2026-11-03/04: #2253 #2270 #2269). If the caller defines `left` (seconds to the night deadline) the wait stops 120 s short of it,
# extension or not. The state is read ONCE MORE before any disarm, and the reason is printed as one `wait ended:` line.
landing_wait_merged(){
  local pr=$1 head=$2 tries=${3:-180} cap=${4:-${ZOE_LANDING_WAIT_CAP_TRIES:-450}} i=0 v ms chk why
  while :; do
    v=$(gh pr view "$pr" --json state,headRefOid,mergeStateStatus --jq '"\(.state) \(.headRefOid) \(.mergeStateStatus)"' 2>/dev/null); set -- $v
    [ "${1:-}" = MERGED ] && return 0
    if [ -n "${2:-}" ] && [ "$2" != "$head" ]; then gh pr merge "$pr" --disable-auto >/dev/null 2>&1; return 5; fi
    ms=${3:-}
    case "$ms" in DIRTY|BEHIND) why="mergeState=$ms"; break;; esac
    if declare -F left >/dev/null && [ "$(left)" -le 120 ]; then why="night deadline"; break; fi
    chk=$(landing_required_state "$pr")
    [ "$chk" = fail ] && { why="a required check failed"; break; }
    i=$((i+1))
    [ "$i" -ge "$cap" ] && { why="hard cap ($cap polls), required checks $chk"; break; }   # the hard cap bounds the base wait too
    if [ "$i" -ge "$tries" ]; then
      # base wait spent: extension is re-earned on EVERY poll - a required check pending/unreadable AND the head positively read as unchanged
      if ! { [ "$chk" = pending ] || [ "$chk" = unknown ]; } || [ "${2:-}" != "$head" ]; then
        why="base wait spent: required checks $chk, mergeState=${ms:-unreadable}"; break
      fi
    fi
    sleep "${ZOE_LANDING_WAIT_STEP_S:-10}"
  done
  [ "$(gh pr view "$pr" --json state --jq .state 2>/dev/null)" = MERGED ] && return 0   # one more look before any disarm
  echo "wait ended: $why"
  gh pr merge "$pr" --disable-auto >/dev/null 2>&1; return 1
}

# ---- the voice hold (2026-11-10): nothing overtakes a probed voice PR - docs/knowledge/landing-tooling.md "The voice hold" -------------
# coord/voice-hold = "<pr> <pid> <pid-starttime> <epoch>", taken by land_voice_pr.sh before update-branch, released by its EXIT trap; the
# docs chain and autoland honour it. coord/voice-hold-disarmed.txt = "<pr> <head>" of other PRs whose auto-merge it dropped (re-armed on
# release, bound to that head). STALE (cleared by whoever reads it) once the owner pid+start time is gone or after ZOE_LANDING_VOICE_HOLD_MAX_S.
LANDING_VOICE_HOLD=${ZOE_LANDING_VOICE_HOLD:-$LANDING_HOME/coord/voice-hold}
LANDING_VOICE_HOLD_DISARMED=${ZOE_LANDING_VOICE_HOLD_DISARMED:-$LANDING_HOME/coord/voice-hold-disarmed.txt}
landing_pid_start(){ sed -E 's/^.*\) //' "/proc/$1/stat" 2>/dev/null | awk '{print $20}'; }   # stat field 22; comm may contain spaces
# Every hold operation runs under ONE flock (<hold>.lock), so take / sweep / release / stale-clear / re-arm never interleave. 2 = lock not won.
# Every gh call made while it is held is bounded (timeout 30 s): a hung gh must not keep the lock - and so the chain - held for ever.
_hold_locked(){ mkdir -p "$(dirname "$LANDING_VOICE_HOLD")"; ( flock -w 60 5 || exit 2; "$@" ) 5>"$LANDING_VOICE_HOLD.lock"; }

# landing_voice_hold_take <pr>: take the hold for THIS shell ($$), then drop every other armed PR's auto-merge (recorded for re-arming).
landing_voice_hold_take(){ _hold_locked _hold_take "$1"; }
_hold_take(){
  local cur; cur=$(_hold_active 2>/dev/null) && [ "$cur" != "$1" ] && return 1   # another LIVE lander's hold is never overwritten
  echo "$1 $$ $(landing_pid_start $$) $(landing_now)" > "$LANDING_VOICE_HOLD.$$" && mv -f "$LANDING_VOICE_HOLD.$$" "$LANDING_VOICE_HOLD" || return 1
  _hold_sweep "$1"
}
landing_voice_hold_sweep(){ _hold_locked _hold_sweep "$1"; }
_hold_sweep(){
  local pr=$1 n h rc=0
  [ "${ZOE_LANDING_VOICE_HOLD_SWEEP:-1}" = 0 ] && return 0
  # an unreadable list = 1 (the lander does not go on as if nothing else were armed)
  timeout -k 5 30 gh pr list --state open --limit 300 --json number,headRefOid,autoMergeRequest \
       --jq '.[] | select(.autoMergeRequest != null) | "\(.number) \(.headRefOid)"' 2>/dev/null > "$LANDING_VOICE_HOLD.armed.$$" || rc=1
  while read -r n h; do
    { [ -z "$n" ] || [ "$n" = "$pr" ]; } && continue
    # record BEFORE disarming: a crash in between leaves a record (re-arming an armed PR is a no-op), never a silently lost arm
    grep -qE "^$n " "$LANDING_VOICE_HOLD_DISARMED" 2>/dev/null || echo "$n $h" >> "$LANDING_VOICE_HOLD_DISARMED"
    if timeout -k 5 30 gh pr merge "$n" --disable-auto >/dev/null 2>&1; then
      echo "[$(date '+%F %T')] voice-hold #$pr: auto-merge of #$n dropped while it lands (re-armed on release, bound to ${h:0:8})" >> "$LANDING_LOG"
    else rc=1; fi   # a PR we could not disarm may still merge first: the caller must not go on as if it were held
  done < "$LANDING_VOICE_HOLD.armed.$$"
  rm -f "$LANDING_VOICE_HOLD.armed.$$"
  return "$rc"
}

# _hold_rearm (under the lock): re-arm an OPEN PR whose head is still the recorded one, bound to that head. A record whose re-arm FAILED is
# kept for the next release/stale-clear; a closed/merged/moved PR is dropped (logged).
_hold_rearm(){
  local n h cur f=$LANDING_VOICE_HOLD_DISARMED
  [ -e "$f" ] || return 0
  : > "$f.keep"
  while read -r n h; do
    [ -n "$n" ] || continue
    cur=$(timeout -k 5 30 gh pr view "$n" --json state,headRefOid --jq '"\(.state) \(.headRefOid)"' 2>/dev/null)
    if [ "$cur" = "OPEN $h" ]; then
      if timeout -k 5 30 gh pr merge "$n" --squash --auto --match-head-commit "$h" >/dev/null 2>&1; then
        echo "[$(date '+%F %T')] voice-hold released: #$n auto-merge re-armed (bound to ${h:0:8})" >> "$LANDING_LOG"
      else echo "$n $h" >> "$f.keep"; fi
    elif [ -z "$cur" ]; then echo "$n $h" >> "$f.keep"           # unreadable: try again next time
    else echo "[$(date '+%F %T')] voice-hold released: #$n NOT re-armed (now '$cur', was OPEN ${h:0:8})" >> "$LANDING_LOG"; fi
  done < "$f"
  if [ -s "$f.keep" ]; then mv -f "$f.keep" "$f"; else rm -f "$f" "$f.keep"; fi
}

# landing_voice_hold_release: drop the hold if THIS shell owns it, then re-arm what it disarmed. Safe in an EXIT trap, safe to repeat.
landing_voice_hold_release(){ _hold_locked _hold_release; }
_hold_release(){
  local pr pid _rest
  read -r pr pid _rest < "$LANDING_VOICE_HOLD" 2>/dev/null || return 0
  [ "$pid" = "$$" ] || return 0
  rm -f "$LANDING_VOICE_HOLD"
  _hold_rearm
}

# landing_voice_hold_active: 0 + prints the holding PR while a live, young hold exists (or "?" if the hold lock could not be had - fail
# closed); a stale one is cleared loudly, its list re-armed, and is 1.
landing_voice_hold_active(){
  local out rc
  out=$(_hold_locked _hold_active); rc=$?
  [ "$rc" = 2 ] && { echo "?"; return 0; }
  [ "$rc" = 0 ] && echo "$out"
  return "$rc"
}
_hold_active(){
  local pr pid st t age max=${ZOE_LANDING_VOICE_HOLD_MAX_S:-10800} why=""
  # no hold, but a list a failed re-arm kept: retry it now (every chain pass reads this), so a transient gh failure is not permanent
  read -r pr pid st t < "$LANDING_VOICE_HOLD" 2>/dev/null || { _hold_rearm; return 1; }
  case "$max" in ''|*[!0-9]*) max=10800;; esac
  case "$t" in ''|*[!0-9]*) t=0;; esac
  age=$(( $(landing_now) - t ))
  if [ -z "$pid" ] || [ -z "$st" ] || [ "$(landing_pid_start "$pid")" != "$st" ]; then why="its owner (pid ${pid:-?}) is gone"
  elif [ "$age" -ge "$max" ]; then why="older than ${max}s (${age}s)"; fi
  if [ -n "$why" ]; then
    rm -f "$LANDING_VOICE_HOLD"
    echo "[$(date '+%F %T')] VOICE HOLD STALE: #${pr:-?} hold cleared - $why; the chain resumes" >> "$LANDING_LOG"
    _hold_rearm
    return 1
  fi
  echo "$pr"
}

# ---- chat drivers yield to the probe (2026-11-10; drivers' side: quiet_for_probe.py) ---------------------------------------------------
# landing_quiet_chat_take [gate-wait-s] [drain-wait-s]: ZOE_PROBE_GATE_LOCK exclusive (no NEW chat turn starts; drivers hold it shared only
# for an instant, so they cannot starve it), then ZOE_CHAT_TURN_LOCK exclusive (waits for in-flight turns). 0 = both held on fds 6/7 until
# landing_quiet_chat_release; 1 = not won in time, nothing held.
landing_quiet_chat_take(){
  local gw=${1:-120} dw=${2:-${ZOE_PROBE_DRAIN_WAIT_S:-300}}
  exec 6>"${ZOE_PROBE_GATE_LOCK:-/tmp/zoe-probe-gate.lock}"
  flock -x -w "$gw" 6 || { exec 6>&-; echo "quiet chat: the probe gate was not won in ${gw}s" >&2; return 1; }
  exec 7>"${ZOE_CHAT_TURN_LOCK:-/tmp/zoe-chat-turn.lock}"
  flock -x -w "$dw" 7 || { exec 7>&-; flock -u 6; exec 6>&-; echo "quiet chat: chat turns in flight did not finish in ${dw}s" >&2; return 1; }
}
landing_quiet_chat_release(){ { flock -u 7; exec 7>&-; flock -u 6; exec 6>&-; } 2>/dev/null; return 0; }

# landing_window_open: is the 12B night window genuinely open? A WINDOW_OPEN marker left behind by a FAILED restore (2026-11-06: the router would not start,
# the marker stayed 2 h and paused every landing) must not pause landings forever. The marker is rewritten (mtime refreshed) after every unit the window
# wakes, so one is STALE only when it is older than ZOE_NIGHT_MARKER_MAX_S (default 5400 s: the 65-min cap plus the restore) AND the pid that wrote it is
# gone. A live owner, or a young marker, is always "open". Prints "STALE <age>s" on stdout (and returns 1) so the caller can log it loudly.
landing_window_open(){
  local m="${ZOE_NIGHT_MARKER:-$HOME/.zoe/night-window/WINDOW_OPEN}" max="${ZOE_NIGHT_MARKER_MAX_S:-5400}" mt age pid
  [ -e "$m" ] || return 1
  mt=$(stat -c %Y "$m" 2>/dev/null || echo 0); age=$(( $(landing_now) - mt ))
  [ "$age" -lt "$max" ] && return 0
  pid=$(sed -n 's/.*"pid": *\([0-9][0-9]*\).*/\1/p' "$m" 2>/dev/null | head -1)
  if [ -n "$pid" ] && [ -e "/proc/$pid" ] && grep -q night_window "/proc/$pid/cmdline" 2>/dev/null; then return 0; fi
  echo "STALE ${age}s"; return 1
}
