#!/bin/bash
# land_voice_pr.sh <pr> <worktree> <branch> <title>: head-bound replay probe (Kokoro paused) -> gate rerun -> auto-merge -> wait MERGED.
# Contract: never arms a merge unless the probe artifact says pass FOR THIS HEAD - or for a probed ANCESTOR whose evidence
# voice_gate_check.py --allow-reuse accepts at this head (the tree diff between them is voice-free; 2026-11-11, `rebind` below).
# See docs/knowledge/landing-tooling.md.
# Exit codes: 0 landed/nothing to do, 4 probe not pass, 5 re-queue (head moved and the evidence is not reusable there / conflict /
# not clean), 6 not landable yet (draft, unresolved or unreadable threads), 7 validate failed on this head, 8 needs a hand merge,
# 9 night pause / no time (re-queue), 10 PR is on a hold list, 12 the probe PASSED but the PR was not merged (DIRTY, BEHIND with a
# voice-relevant update, or the CI wait ended) - the voice loop re-queues it at the FRONT for a fresh probe.
. "$(dirname "${BASH_SOURCE[0]}")/landing_lib.sh"
set -u; PR=$1; WT=$2; BR=$3; TITLE=$4; cd "$LANDING_REPO" || exit 1
# 2026-10-27 night contract (the 2026-10-26 window REFUSED "a voice-PR landing is running" and lost the night): this process must be GONE - lock
# released, no live land_voice_pr.sh - by DEADLINE (night_busy.sh deadline = the next 02:40, ten minutes before the window's 02:50 start).
# 1) the night gate is the FIRST thing, before any gh/ssh call (a paused invocation used to live ~5 s - long enough for the window to see it);
# 2) every wait below is bounded by DEADLINE, and the probe never STARTS unless PROBE_NEED seconds remain; 3) the brain lock is held only for the
# probe itself and dropped the moment it ends; 4) the post-probe CI wait is bounded by DEADLINE (an armed merge stays armed, bound to its head).
if bash "$LANDING_DIR/night_busy.sh" voice; then echo "[$(date +%T)] #$PR night pause (night_busy voice) - exit 9"; exit 9; fi
log(){ echo "[$(date +%T)] #$PR $*"; }; avail(){ awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo; }
# 2026-10-29: the operator hold lists (coord/hold.txt, chain-hold.txt) bind the voice lander too - a held PR is not touched at all
# (no disarm, no update-branch, no probe). It used to read neither, so a held voice PR was landed anyway.
if landing_is_held "$PR"; then log "on a hold list - not landing (exit 10)"; exit 10; fi
DEADLINE=$(bash "$LANDING_DIR/night_busy.sh" deadline); PROBE_NEED=1500
left(){ echo $(( DEADLINE - $(landing_now) )); }
# need_probe_time <what>: not enough time before the window for the probe -> leave NOTHING behind (callers have not taken the lock / stopped Kokoro yet)
need_probe_time(){ [ "$(left)" -ge "$PROBE_NEED" ] || { echo "[$(date +%T)] #$PR only $(left)s before the night window guard ($PROBE_NEED needed for the probe) while $1 - exit 9, re-queued"; exit 9; }; }
gh pr merge "$PR" --disable-auto >/dev/null 2>&1   # never let an earlier arm fire before this probe
ST0=$(gh pr view "$PR" --json state --jq .state); [ "$ST0" = MERGED ] && { log "already merged"; exit 0; }; [ "$ST0" = CLOSED ] && { log "PR is CLOSED - nothing to land"; exit 0; }
# 2026-10-28: a DRAFT is never landed (#2200 was merged as a draft before its cross-review finished). This lander also no longer
# runs `gh pr ready` - un-drafting is the owner's act, never the lander's.
DRAFT=$(gh pr view "$PR" --json isDraft --jq .isDraft 2>/dev/null || echo unknown); [ "$DRAFT" != false ] && { log "draft (or unreadable: $DRAFT) - not landing (exit 6)"; exit 6; }
# Threads are counted across ALL pages (threads.sh), and an unreadable API is "unknown", never "0": it used to read the first 100 only
# and treat a failed call as zero, leaving a PR silently BLOCKED after arming.
UNRES=$(landing_unresolved_count "$PR") || { log "review threads unreadable - not landing (exit 6)"; exit 6; }
[ "$UNRES" != 0 ] && { log "$UNRES unresolved review thread(s) - not landing yet (exit 6)"; exit 6; }
H0=$(gh pr view "$PR" --json headRefOid --jq .headRefOid); grep -qxF "$H0" "$LANDING_HOME/validate-failed-heads.txt" 2>/dev/null && { log "head $H0 already failed validate - not re-landing until it moves (exit 7)"; exit 7; }
# The lander owns its OWN detached worktree: it hard-resets WT, so it must never use an agent's worktree (2026-10-14 sig: reset a worker's branch).
WT="$LANDING_WORKTREES/land-$PR"; LREF="refs/zoe-landing/$BR"
# explicit refspec into the lander's OWN ref namespace: a plain `git fetch origin <branch>` rewrites the shared .git/FETCH_HEAD
# (2026-10-30: it made the CD runner deploy this unmerged branch)
git fetch -q --no-write-fetch-head origin "+refs/heads/$BR:$LREF" || exit 1
[ -d "$WT" ] || git worktree add -q --detach "$WT" "$LREF" || { log "could not create lander worktree - exit 5"; exit 5; }
panel_quiet(){ [ -z "$LANDING_PANEL_HOST" ] && return 0; last=$(timeout 8 ssh -o BatchMode=yes -o ConnectTimeout=6 "$LANDING_PANEL_HOST" "grep -a 'Wake word detected\|Follow-up speech detected' $LANDING_PANEL_LOG | tail -1 | cut -c1-19" 2>/dev/null); [ -z "$last" ] && return 0; age=$(( $(date +%s) - $(date -d "$last" +%s 2>/dev/null || echo 0) )); [ $age -ge 600 ]; }
for i in $(seq 1 180); do panel_quiet && break; need_probe_time "waiting for the panel"; log "panel in use - waiting"; sleep 60; done
# night quiet time (2026-10-13): the 12B night window (timer 02:50) REFUSES if the brain-window lock is held (no retry = a lost night),
# and a deploy restart collides with its stops; a landing holds the lock up to ~45 min. So: nothing starts 01:50-04:55; sleep without the lock.
# 2026-10-19: replaced the fixed 01:50-04:55 blackout with the measured window (night_busy.sh: no voice landing STARTS 01:55 until
# tonight's window has finished, nor 04:00-04:55 while the nightly regression owns the artifact slot).
if bash "$LANDING_DIR/night_busy.sh" voice; then log "night pause (night_busy voice) - not landing now (exit 9); the night window treats a RUNNING lander as busy and refuses after 20 min, so never sleep here"; exit 9; fi
while IFS= read -r line; do case "$line" in ''|'#'*) continue;; esac; export "$line"; done < "${ZOE_LANDING_ENV_FILE:-$HOME/.hermes/.env}"
export ZOE_VOICE_REPLAY_STT=remote ZOE_REPLAY_BASE_URL=http://127.0.0.1:8000; unset ZOE_BASE_URL
# P-2 (2026-10-19): this probe is THE landing gate - exempt from harness quiet hours (scripts/maintenance/harness_quiet.py), still yields to the panel
export ZOE_HARNESS_LANDING=1
[ "$(gh pr view "$PR" --json mergeable --jq .mergeable)" = CONFLICTING ] && { if "$LANDING_RESOLVE_SCRIPT" "$PR"; then log "conflict auto-resolved before the probe"; sleep 20; else log "CONFLICTING and not auto-resolvable - exit 5"; exit 5; fi; }
# wait out any running docs chain BEFORE updating the branch (2026-10-13: #2013 merged after #2004 updated -> BEHIND after its probe, a wasted cycle)
for i in $(seq 1 90); do landing_running docs_merge_chain.sh || break; need_probe_time "waiting for the docs chain"; log "docs chain running - waiting"; sleep 60; done
# 2026-11-10 voice hold (landing_lib.sh): nothing else lands from here to the merge; ONE EXIT trap releases it on every way out.
KOKORO_TRAP=0; ARMED=0
lander_exit(){ [ "$KOKORO_TRAP" = 1 ] && systemctl --user start kokoro-tts.service >/dev/null 2>&1; [ "$ARMED" = 1 ] && landing_disarm_unless_merged "$PR"; landing_voice_hold_release; }
trap lander_exit EXIT
landing_voice_hold_take "$PR" || { log "voice hold not taken (hold lock busy, unwritable, or the open-PR list unreadable) - nothing updated; re-queue (exit 9)"; exit 9; }
log "voice hold taken (the chain holds its merges until this lander exits)"
gh pr update-branch "$PR" >/dev/null 2>&1 && log "branch updated" || log "update-branch: $(gh pr view "$PR" --json mergeStateStatus --jq .mergeStateStatus)"
if [ "$(gh pr view "$PR" --json mergeStateStatus --jq .mergeStateStatus)" = DIRTY ]; then if "$LANDING_RESOLVE_SCRIPT" "$PR"; then log "DIRTY -> inventory/ledger conflict auto-resolved (sig 48)"; sleep 90; gh pr update-branch "$PR" >/dev/null 2>&1; else log "DIRTY -> needs a hand merge (sig 48) - NOT landing"; exit 8; fi; fi
sleep 8; git -C "$WT" fetch -q --no-write-fetch-head origin "+refs/heads/$BR:$LREF" && git -C "$WT" reset -q --hard "$LREF"; cp -n "$LANDING_REPO/services/zoe-data/.env" "$WT/services/zoe-data/.env" 2>/dev/null || true
HEAD_SHA=$(git -C "$WT" rev-parse HEAD); log "probing head ${HEAD_SHA:0:8}"
# probe in a FRESH detached worktree at the exact head (2026-10-13: #2006 and #2008 were refused as DIRTY - agent worktrees carry untracked
# files, e.g. a detached A/B run, that reset --hard never clears, so the artifact could not be attributed to a commit and the gate/deploy refused it)
PWT="$LANDING_WORKTREES/voice-probe-$PR"; git -C "$LANDING_REPO" worktree remove --force "$PWT" >/dev/null 2>&1; rm -rf "$PWT"
git -C "$LANDING_REPO" worktree add -q --detach "$PWT" "$HEAD_SHA" || { log "could not create the probe worktree - exit 5"; exit 5; }
cp -n "$LANDING_REPO/services/zoe-data/.env" "$PWT/services/zoe-data/.env" 2>/dev/null; chmod 600 "$PWT/services/zoe-data/.env" 2>/dev/null
[ -n "$(git -C "$PWT" status --porcelain)" ] && { log "probe worktree not clean - exit 5"; exit 5; }
wait_deploy(){ for i in $(seq 1 90); do git fetch -q --no-write-fetch-head origin "+refs/heads/main:refs/zoe-landing/main" 2>/dev/null; M=$(git rev-parse refs/zoe-landing/main); age=$(( $(date +%s) - $(git log -1 --format=%ct refs/zoe-landing/main) ))
    inprog=$(gh run list --workflow deploy.yml --limit 3 --json status --jq '[.[]|select(.status!="completed")]|length' 2>/dev/null)
    done_for_M=$(gh run list --workflow deploy.yml --limit 8 --json status,headSha --jq "[.[]|select(.headSha==\"$M\" and .status==\"completed\")]|length" 2>/dev/null)
    if [ "${inprog:-0}" = 0 ] && { [ "${done_for_M:-0}" -ge 1 ] || [ $age -ge 480 ]; }; then return; fi; need_probe_time "waiting for the main deploy"; log "waiting for main ${M:0:8} deploy"; sleep 15; done; }
# never probe within 3 min of a sidecar / zoe-data restart (cold prompt cache -> CANT_DO storm)
for svc in flue-zoe-brain-2x zoe-data; do ts=$(systemctl --user show -p ExecMainStartTimestamp --value $svc); age=$(( $(date +%s) - $(date -d "$ts" +%s 2>/dev/null || echo 0) )); [ $age -lt 180 ] && { log "$svc restarted ${age}s ago - waiting"; sleep $((180-age)); }; done
# serialise with the docs chain (2026-10-13: #1998 merged while #1999 was armed and waiting on validate -> BEHIND -> disarmed).
# (docs-chain wait moved BEFORE update-branch, 2026-10-13 - see above it)
log "probe (avail $(avail))"; wait_deploy
# CPU-quiet gate (2026-10-12: #1996 failed the probe on SPEED only - STT 1.85 s, e2e 3.9 s - while parallel agents ran full pytest sweeps, load 6-8).
# The probe's speed bar assumes a quiet box: wait until the 1-min load average is < 3.0 on two readings 60 s apart (max 60 min), else log and go on.
load1(){ awk '{print $1}' /proc/loadavg; }
for i in $(seq 1 60); do if awk -v l="$(load1)" 'BEGIN{exit !(l<3.0)}'; then sleep 60; awk -v l="$(load1)" 'BEGIN{exit !(l<3.0)}' && break; fi; need_probe_time "waiting for a quiet CPU"; log "box busy (load $(load1)) - waiting for a quiet CPU before the speed probe"; sleep 60; done
# re-check after wait_deploy: a merge that landed between the first check and here restarts zoe-data (sig #29/#35)
for svc in flue-zoe-brain-2x zoe-data; do ts=$(systemctl --user show -p ExecMainStartTimestamp --value $svc); age=$(( $(date +%s) - $(date -d "$ts" +%s 2>/dev/null || echo 0) )); [ $age -lt 180 ] && { log "$svc restarted ${age}s ago (post-deploy) - waiting"; sleep $((180-age)); }; done
# 2026-10-27: the brain-window lock is taken HERE, just before the probe (it used to be taken at the top and held through every wait above, the docs
# chain, the deploy and CI), bounded by the deadline, and after one more look at the night gate + clock. Nothing is stopped yet if any of these exit.
if bash "$LANDING_DIR/night_busy.sh" voice; then log "night pause appeared while waiting (night_busy voice) - exit 9"; exit 9; fi
need_probe_time "about to take the brain lock"
# 2026-10-29: a push during the waits above (deploy, CPU, panel) also wastes the probe - look at the head once more before Kokoro is stopped.
CUR=$(gh pr view "$PR" --json headRefOid --jq .headRefOid 2>/dev/null); [ "$CUR" != "$HEAD_SHA" ] && { log "head moved (or unreadable: '${CUR:0:8}') before the probe started - nothing stopped; re-queue (exit 5)"; exit 5; }
exec 9>/tmp/zoe-brain-window.lock; flock -w $(( $(left) - PROBE_NEED + 60 )) 9 || { log "brain-window lock busy (waited as long as the night deadline allows) - exit 9"; exit 9; }
need_probe_time "holding the brain lock"
# Kokoro is paused only now - after wait_deploy - so a long deploy wait no longer costs minutes of TTS downtime (units log review 2026-10-04)
# 2026-10-29: the TEST-LANE lock is taken BEFORE Kokoro is stopped (landing_probe_window, landing_lib.sh): flock is not FIFO, agents' test runs
# kept winning the lane and the probe waited 60+ min with Kokoro already stopped = Zoe mute in the daytime. A lane not won in time = exit 9, Kokoro untouched.
landing_pre_stop(){ ! bash "$LANDING_DIR/night_busy.sh" voice && [ "$(left)" -ge 900 ]; }   # re-run after the lane wait
run_probe(){ KOKORO_TRAP=1; flock -w 120 /tmp/zoe-voice-harness.lock timeout -k 20 "$(landing_probe_timeout "$(left)")" nice -n 5 "$LANDING_PYTHON" scripts/maintenance/voice_regression_probe.py --samples 20 --stt remote --service-dir "$PWT/services/zoe-data" </dev/null 8>&- 2>&1 | grep -E 'probe —|medians|status=|SKIP|ERROR' | cut -c1-200; }
# The probe always runs to completion (capped by `timeout`); a push mid-probe shows up as artifact revision != current head below -> exit 5, nothing armed.
PROBE_T0=$(date +%s)
landing_probe_window $(( $(left) - 900 > 60 ? $(left) - 900 : 60 )) "$PR" "$HEAD_SHA" run_probe; PROBE_RC=$?; KOKORO_TRAP=0   # the window restarted Kokoro; the trap restarts it only if we die mid-probe
[ "$PROBE_RC" = 9 ] && { log "test lane busy, household not quiet, or the pre-stop gate said no - Kokoro untouched - exit 9"; flock -u 9; exec 9>&-; exit 9; }
systemctl --user start kokoro-tts.service
flock -u 9; exec 9>&-    # 2026-10-27: the probe is over - release the brain lock NOW, not after the CI / merge wait below
git -C "$LANDING_REPO" worktree remove --force "$PWT" >/dev/null 2>&1
for i in $(seq 1 90); do curl -sf -m 3 http://127.0.0.1:10201/health 2>/dev/null | grep -q pipeline_loaded.:true && break; sleep 2; done; log "kokoro back: $(curl -s -m 3 http://127.0.0.1:10201/health | head -c 60)"
PROBE_STATUS=$(python3 -c "import json,sys;d=json.load(open(sys.argv[1]));print(d.get('status'), (d.get('revision') or {}).get('commit','')[:8])" "$LANDING_VOICE_ARTIFACT" 2>/dev/null); log "probe status/rev: $PROBE_STATUS"
landing_artifact_fresh "$LANDING_VOICE_ARTIFACT" "$PROBE_T0" || { log "probe artifact was not written by THIS attempt (stale same-head result) - NOT arming - exit 9, re-queued"; exit 9; }
case "$PROBE_STATUS" in *" ${HEAD_SHA:0:8}") :;; *) log "probe artifact is not for this head (${HEAD_SHA:0:8}) - the probe did not run or was cut short; NOT arming - exit 9, re-queued"; exit 9;; esac
# keep THIS probe's artifact per PR: the shared slot is overwritten by the next probe, and the voice loop's retry decision must read this one
case "$PROBE_STATUS" in pass*) :;; *) mkdir -p "$LANDING_ARTIFACT_DIR"; cp -f "$LANDING_VOICE_ARTIFACT" "$LANDING_ARTIFACT_DIR/$PR.json" 2>/dev/null; log "PROBE NOT PASS - not arming auto-merge; leaving #$PR for triage"; exit 4;; esac
# rerun the PR's latest voice-gate run so the verdict reads the fresh head-bound artifact
RUN=$(gh run list --workflow voice-gate.yml --limit 30 --json databaseId,headSha --jq ".[]|select(.headSha==\"$HEAD_SHA\")|.databaseId" | head -1)
if [ -n "$RUN" ]; then
  ATT0=$(gh run view "$RUN" --json attempt --jq .attempt 2>/dev/null); case "$ATT0" in ''|*[!0-9]*) ATT0=0;; esac
  if gh run rerun "$RUN" >/dev/null 2>&1; then
    log "gate rerun $RUN (was attempt $ATT0)"; st=pending/
    # wait for the NEW attempt (landing_gate_state): the previous attempt's conclusion is not this verdict (2026-11-11, #2408)
    for i in $(seq 1 60); do st=$(landing_gate_state "$RUN" "$ATT0"); case "$st" in completed/*) break;; esac; [ "$(left)" -gt 120 ] || break; sleep 10; done
    log "gate: $st"
  else log "gate rerun of $RUN could not be started - the recorded verdict is the previous attempt's, not this probe's (informational; the deploy gate is the block)"; fi
else log "no voice-gate run for this head yet (not a voice-path PR?)"; fi
# EVIDENCE REUSE (2026-11-11): the probe's evidence is for PROBED_SHA. When the head moves after the probe - `strict` makes the PR go BEHIND
# when anything else merges, or someone pushes - the lander MAY carry on at the new head instead of re-probing, but ONLY when
# landing_evidence_reusable says so (landing_lib.sh -> voice_gate_check.py --allow-reuse: the probed commit is an ancestor of the new head and
# the tree diff between them touches nothing voice-relevant). Refused or unreadable = the old exits (5 / 12 -> a fresh probe). Bounded by
# ZOE_LANDING_REBIND_MAX (default 3) per landing, so the loop below always ends.
PROBED_SHA=$HEAD_SHA; REBINDS=0; REBIND_MAX=${ZOE_LANDING_REBIND_MAX:-3}
case "$REBIND_MAX" in ''|*[!0-9]*) REBIND_MAX=3;; esac
# rebind <new-head> <why>: 0 = HEAD_SHA is now <new-head> (evidence reused); 1 = not (the caller exits as before)
rebind(){
  local new=$1 why=$2 got out
  [ "$REBINDS" -lt "$REBIND_MAX" ] || { log "$why: rebind cap ($REBIND_MAX) reached - a fresh probe instead"; return 1; }
  [ "$(left)" -gt 600 ] || { log "$why: too close to the night deadline to rebind"; return 1; }
  REBINDS=$((REBINDS+1))
  git -C "$WT" fetch -q --no-write-fetch-head origin "+refs/heads/$BR:$LREF"; got=$(git -C "$WT" rev-parse "$LREF" 2>/dev/null)
  { [ -n "$new" ] && [ "$got" = "$new" ]; } || { log "$why: the branch tip here (${got:0:8}) is not the PR head (${new:0:8}) - cannot check reuse"; return 1; }
  if out=$(landing_evidence_reusable "$PROBED_SHA" "$new"); then
    log "$why: probe evidence for ${PROBED_SHA:0:8} REUSED at ${new:0:8} (rebind $REBINDS/$REBIND_MAX)"; printf '%s\n' "$out" | while IFS= read -r l; do log "  $l"; done
    HEAD_SHA=$new; return 0
  fi
  log "$why: probe evidence for ${PROBED_SHA:0:8} NOT reusable at ${new:0:8}"; printf '%s\n' "$out" | while IFS= read -r l; do log "  $l"; done
  return 1
}
while :; do
gh pr checks "$PR" --json name,state --jq '[.[]|select(.name=="validate" or .name=="secret-scan" or .name=="voice-gate")|"\(.name)=\(.state)"]|join(" ")' | xargs -I{} echo "[$(date +%T)] #$PR checks: {}"
CHK=$(gh pr checks "$PR" --json name,state --jq '[.[]|select(.name=="validate")|.state]|join("")'); if [ "$CHK" = FAILURE ]; then echo "$HEAD_SHA" >> "$LANDING_HOME/validate-failed-heads.txt"; log "validate FAILED on head $HEAD_SHA - NOT arming; fix + push (exit 7, sig #42)"; exit 7; fi
NOWHEAD=$(gh pr view "$PR" --json headRefOid --jq .headRefOid)
if [ "$NOWHEAD" != "$HEAD_SHA" ]; then
  rebind "$NOWHEAD" "head moved (${HEAD_SHA:0:8} -> ${NOWHEAD:0:8})" && continue
  log "PR head moved past the probed commit (${NOWHEAD:0:8} != ${HEAD_SHA:0:8}) and the evidence is not reusable there - NOT arming (sig #40); re-queue"; exit 5
fi
MS=$(gh pr view "$PR" --json mergeStateStatus --jq .mergeStateStatus)
case "$MS" in
  DIRTY) log "mergeState=DIRTY after the probe - NOT arming; re-queue at the FRONT (exit 12)"; exit 12;;
  BEHIND)
    # strict: bring it up to date, then reuse the evidence if what came in from main is voice-free (else a fresh probe: exit 12 as before)
    gh pr update-branch "$PR" >/dev/null 2>&1 || { log "mergeState=BEHIND and update-branch failed - re-queue at the FRONT (exit 12)"; exit 12; }
    NEW=$HEAD_SHA; for i in $(seq 1 12); do sleep "${ZOE_LANDING_UPDATE_POLL_S:-5}"; NEW=$(gh pr view "$PR" --json headRefOid --jq .headRefOid 2>/dev/null); [ -n "$NEW" ] && [ "$NEW" != "$HEAD_SHA" ] && break; done
    { [ -n "$NEW" ] && [ "$NEW" != "$HEAD_SHA" ]; } || { log "mergeState=BEHIND, update-branch ran, but the head did not move - re-queue at the FRONT (exit 12)"; exit 12; }
    rebind "$NEW" "updated with main (was BEHIND)" && continue
    log "mergeState=BEHIND after the probe and the evidence is not reusable at the updated head - NOT arming; re-queue at the FRONT for a fresh probe (exit 12)"; exit 12;;
esac
# last look before arming: the PR must still be a non-draft (it was checked at the top; a convert-to-draft since then is the owner pulling it back)
[ "$(gh pr view "$PR" --json isDraft --jq .isDraft 2>/dev/null)" = false ] || { log "PR is a draft (or unreadable) at arm time - NOT arming (exit 6)"; exit 6; }
# Never leave a voice PR armed behind this process: --match-head-commit only binds the request, a later writer push would keep the arm and merge an
# unprobed head. So any exit after arming while the PR is not MERGED disarms (EXIT trap), and the wait below disarms the instant the head moves.
# anything armed since the hold was taken (an agent, a human) is dropped too, and re-armed on exit; unreadable = not arming
landing_voice_hold_sweep "$PR" || { log "pre-arm sweep failed (open-PR list unreadable) - NOT arming; re-queue at the FRONT (exit 12)"; exit 12; }
ARMED=1   # lander_exit (EXIT trap) disarms from here on unless MERGED
# HEAD_SHA is the probed head, or a head rebind() proved the probe's evidence reusable at - never anything else
gh pr merge "$PR" --squash --auto --match-head-commit "$HEAD_SHA" >/dev/null 2>&1 && log "auto-merge armed (bound to ${HEAD_SHA:0:8}, evidence from the probe of ${PROBED_SHA:0:8}: a later push cannot merge unprobed)"
landing_wait_merged "$PR" "$HEAD_SHA" | while IFS= read -r l; do log "$l"; done; WAIT_RC=${PIPESTATUS[0]}
[ "$WAIT_RC" = 0 ] && exit 0
# head moved after arming (auto-merge already DISARMED): back to the top, which rebinds only if the evidence is reusable there, else exit 5
[ "$WAIT_RC" = 5 ] && { log "head moved after arming - auto-merge DISARMED at once; checking evidence reuse"; continue; }
FINAL=$(gh pr view "$PR" --json state,mergeStateStatus --jq '"\(.state) \(.mergeStateStatus)"'); log "state: $FINAL"
case "$FINAL" in MERGED*) exit 0;; *" BEHIND") log "went BEHIND in the wait (auto-merge DISARMED) - checking evidence reuse"; continue;; esac
log "not merged within the wait - auto-merge DISARMED (never left armed); the probe passed, so the voice loop re-queues it at the FRONT (exit 12)"
exit 12
done
