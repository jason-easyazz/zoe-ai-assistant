#!/bin/bash
# docs_merge_chain.sh <pr>... : serial update-branch -> (arm if clean) -> wait MERGED, for docs/CI PRs under strict required checks.
# Never arms a PR with unresolved review threads (counted across ALL pages; an unreadable API counts as "holding", not zero);
# never touches a DRAFT or a held PR (hold lists); never touches voice-scope PRs (those go through land_voice_pr.sh).
. "$(dirname "${BASH_SOURCE[0]}")/landing_lib.sh"
cd "$LANDING_REPO" || exit 1   # gh needs a repo cwd (sig: "failed to run git: not a git repository")
P_RESOLVE=$LANDING_RESOLVE_SCRIPT
HOLD_S=${ZOE_LANDING_CHAIN_HOLD_S:-120}; STEP_S=${ZOE_LANDING_CHAIN_STEP_S:-60}   # poll cadence (tests shrink it)
mkdir -p "$(dirname "$LANDING_LOG")"
log(){ echo "[$(date '+%F %T')] chain $*" >> "$LANDING_LOG"; }
for PR in "$@"; do
  landing_is_held "$PR" && { log "#$PR is on HOLD (hold list) - skipping"; continue; }
  log "=== #$PR"; updated=0; holds=0
  for i in $(seq 1 90); do
    h=$(date -d "@$(landing_now)" +%H%M); if [ "$h" -ge 0350 ] && [ "$h" -le 0455 ]; then log "nightly voice gate window - chain paused (no update/arm until 04:55)"; sleep 300; continue; fi
    [ -e "$HOME/.zoe/night-window/WINDOW_OPEN" ] && { log "#$PR night window open (WINDOW_OPEN) - chain paused, no update/arm until it closes"; sleep 120; continue; }
    landing_is_held "$PR" && { log "#$PR went on HOLD - skipping"; break; }
    st=$(gh pr view "$PR" --json state,mergeStateStatus,autoMergeRequest,isDraft --jq '"\(.state) \(.mergeStateStatus) \(.autoMergeRequest!=null) \(.isDraft)"'); set -- $st; state=$1; ms=$2; armed=$3; draft=${4:-unknown}
    [ "$state" = MERGED ] && { log "#$PR MERGED"; break; }
    [ "$state" = CLOSED ] && { log "#$PR CLOSED"; break; }
    [ "$draft" != false ] && { log "#$PR is a draft (or unreadable) - not touching it"; break; }
    unres=$(landing_unresolved_count "$PR" || echo 99)
    pend=$(gh pr checks "$PR" --json name,state --jq '[.[]|select(.name=="validate" or .name=="secret-scan")|select(.state!="SUCCESS")]|length' 2>/dev/null || echo 9)
    if [ "$unres" != 0 ]; then holds=$((${holds:-0}+1)); log "#$PR holding - $unres unresolved review thread(s) ($holds/3)"; [ "$holds" -ge 3 ] && { log "#$PR still has unresolved threads - skipping for now (re-queued by the loop)"; break; }; sleep "$HOLD_S"; continue; fi
    if [ "$ms" = BEHIND ] || [ "$ms" = DIRTY ]; then
      if [ "$ms" = DIRTY ]; then if $P_RESOLVE "$PR"; then log "#$PR DIRTY -> inventory-only conflict auto-resolved"; sleep "$STEP_S"; continue; else log "#$PR DIRTY - needs a hand merge; skipping"; break; fi; fi
      # local merge + regenerate (generated inventory/live-state stay fresh); fall back to the server-side update
      if RESOLVE_BEHIND=1 $P_RESOLVE "$PR"; then updated=$((updated+1)); log "#$PR branch updated locally + inventory regenerated (#$updated)"; sleep "$STEP_S"; continue; fi
      gh pr update-branch "$PR" >/dev/null 2>&1 && { updated=$((updated+1)); log "#$PR branch updated (#$updated)"; sleep "$STEP_S"; continue; }
    fi
    if [ "$armed" = false ] && [ "$pend" = 0 ] && [ "$(gh pr view "$PR" --json isDraft --jq .isDraft 2>/dev/null)" = false ]; then gh pr merge "$PR" --squash --auto >/dev/null 2>&1 && log "#$PR auto-merge armed (threads 0, required checks green)"; fi
    sleep "$STEP_S"
  done
done; log "CHAIN DONE"
