#!/bin/bash
# docs_merge_chain.sh <pr>... : serial update-branch -> (arm if clean) -> wait MERGED, for docs/CI PRs under strict required checks.
# Never arms a PR with unresolved review threads (counted across ALL pages; an unreadable API counts as "holding", not zero);
# never touches a DRAFT or a held PR (hold lists); never touches voice-scope PRs (those go through land_voice_pr.sh).
# While a voice lander holds the VOICE HOLD (landing_lib.sh: a probed voice PR waiting to merge) it neither updates nor arms, and drops
# its own PR's auto-merge if armed - a merge here would put the voice PR BEHIND and waste its probe. The hold is bounded (stale = cleared).
. "$(dirname "${BASH_SOURCE[0]}")/landing_lib.sh"
cd "$LANDING_REPO" || exit 1   # gh needs a repo cwd (sig: "failed to run git: not a git repository")
P_RESOLVE=$LANDING_RESOLVE_SCRIPT
NIGHT_S=${ZOE_LANDING_CHAIN_NIGHT_S:-300}; HOLD_S=${ZOE_LANDING_CHAIN_HOLD_S:-120}; STEP_S=${ZOE_LANDING_CHAIN_STEP_S:-60}   # poll cadence (tests shrink it)
PASSES=${ZOE_LANDING_CHAIN_PASSES:-90}   # passes per PR before the chain moves on (tests shrink it)
mkdir -p "$(dirname "$LANDING_LOG")"
log(){ echo "[$(date '+%F %T')] chain $*" >> "$LANDING_LOG"; }
for PR in "$@"; do
  landing_is_held "$PR" && { log "#$PR is on HOLD (hold list) - skipping"; continue; }
  log "=== #$PR"; updated=0; holds=0; vheld=""
  for i in $(seq 1 "$PASSES"); do
    h=$(date -d "@$(landing_now)" +%H%M); if [ "$h" -ge 0350 ] && [ "$h" -le 0455 ]; then log "nightly voice gate window - chain paused (no update/arm until 04:55)"; sleep 300; continue; fi
    stale=$(landing_window_open); wrc=$?
    [ "$wrc" = 0 ] && { log "#$PR night window open (WINDOW_OPEN) - chain paused, no update/arm until it closes"; sleep 120; continue; }
    [ -n "$stale" ] && log "#$PR WARNING: WINDOW_OPEN is STALE ($stale, owner gone): a failed night restore was never cleared - NOT pausing; wake the box: scripts/night/night_window.sh --restore-only"
    landing_is_held "$PR" && { log "#$PR went on HOLD - skipping"; break; }
    bash "$LANDING_DIR/night_busy.sh" deploy && { log "deploy pause (night_busy deploy) - chain paused, no update/arm"; sleep "$NIGHT_S"; continue; }
    st=$(gh pr view "$PR" --json state,mergeStateStatus,autoMergeRequest,isDraft,headRefOid --jq '"\(.state) \(.mergeStateStatus) \(.autoMergeRequest!=null) \(.isDraft) \(.headRefOid)"'); set -- $st; state=$1; ms=$2; armed=$3; draft=${4:-unknown}; head=${5:-}
    [ "$state" = MERGED ] && { log "#$PR MERGED"; break; }
    [ "$state" = CLOSED ] && { log "#$PR CLOSED"; break; }
    case "$head" in ????????????????????????????????????????) :;; *) log "#$PR head unreadable ('$head') - skipping this pass"; sleep "$STEP_S"; continue;; esac
    [ "$draft" != false ] && { log "#$PR is a draft (or unreadable) - not touching it"; break; }
    # the scope is re-read on EVERY pass (a push can add a voice file after autoland classified the PR): only CLEAR proceeds, anything else goes to the voice lander
    # an unreadable or EMPTY file list would classify as CLEAR: treat it as unknown and skip this pass
    { gh pr view "$PR" --json files --jq '.files[].path' > "$LANDING_HOME/logs/chain-files-$PR.txt" 2>/dev/null && [ -s "$LANDING_HOME/logs/chain-files-$PR.txt" ]; } || { log "#$PR changed-file list unreadable - skipping this pass"; sleep "$STEP_S"; continue; }
    scope=$(cd "$LANDING_REPO" && python3 scripts/maintenance/voice_gate_check.py --scope-only --changed-files-from "$LANDING_HOME/logs/chain-files-$PR.txt" 2>&1 | grep -o 'scope: [A-Z]*' | awk '{print $2}')
    [ "$scope" = CLEAR ] || { [ "$armed" = true ] && gh pr merge "$PR" --disable-auto >/dev/null 2>&1; log "#$PR voice scope is '${scope:-unreadable}', not CLEAR - leaving it to the voice lander (auto-merge disabled if it was armed)"; break; }
    if vh=$(landing_voice_hold_active) && [ "$vh" != "$PR" ]; then
      if [ "$armed" = true ]; then gh pr merge "$PR" --disable-auto >/dev/null 2>&1 && log "#$PR auto-merge dropped: voice PR #$vh holds the merge slot (re-armed here once it is released)"; fi
      [ "$vheld" = "$vh" ] || log "#$PR holding - voice PR #$vh is landing (no update/arm until its hold is released)"; vheld=$vh
      sleep "$STEP_S"; continue
    fi
    vheld=""
    unres=$(landing_unresolved_count "$PR" || echo 99)
    pend=$(gh pr checks "$PR" --json name,state --jq '[.[]|select(.name=="validate" or .name=="secret-scan")|select(.state!="SUCCESS")]|length' 2>/dev/null || echo 9)
    if [ "$unres" != 0 ]; then holds=$((${holds:-0}+1)); log "#$PR holding - $unres unresolved review thread(s) ($holds/3)"; [ "$holds" -ge 3 ] && { log "#$PR still has unresolved threads - skipping for now (re-queued by the loop)"; break; }; sleep "$HOLD_S"; continue; fi
    landing_voice_hold_active >/dev/null && { sleep "$STEP_S"; continue; }   # re-checked right before acting: a hold taken this pass wins
    if [ "$ms" = BEHIND ] || [ "$ms" = DIRTY ]; then
      if [ "$ms" = DIRTY ]; then if $P_RESOLVE "$PR"; then log "#$PR DIRTY -> inventory-only conflict auto-resolved"; sleep "$STEP_S"; continue; else log "#$PR DIRTY - needs a hand merge; skipping"; break; fi; fi
      # local merge + regenerate (generated inventory/live-state stay fresh); fall back to the server-side update
      if RESOLVE_BEHIND=1 $P_RESOLVE "$PR"; then updated=$((updated+1)); log "#$PR branch updated locally + inventory regenerated (#$updated)"; sleep "$STEP_S"; continue; fi
      gh pr update-branch "$PR" >/dev/null 2>&1 && { updated=$((updated+1)); log "#$PR branch updated (#$updated)"; sleep "$STEP_S"; continue; }
    fi
    if [ "$armed" = false ] && [ "$pend" = 0 ] && [ "$(gh pr view "$PR" --json isDraft --jq .isDraft 2>/dev/null)" = false ]; then gh pr merge "$PR" --squash --auto ${head:+--match-head-commit "$head"} >/dev/null 2>&1 && log "#$PR auto-merge armed (threads 0, required checks green)"
      # a hold taken between the check above and this arm: drop it again at once (the lander's pre-arm sweep is the backstop)
      landing_voice_hold_active >/dev/null && gh pr merge "$PR" --disable-auto >/dev/null 2>&1 && log "#$PR auto-merge dropped again: a voice hold appeared"; fi
    sleep "$STEP_S"
  done
done; log "CHAIN DONE"
