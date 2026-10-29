#!/bin/bash
# land_queue.sh <queue-file>: sequential voice landings (one probe at a time); each line "pr worktree branch title".
# Records each lander's real exit code in $ZOE_LANDING_HOME/last-results.txt ("<pr> <rc>", rewritten per run) so the voice loop
# (after_voice_more.sh) can tell a speed-only probe fail from everything else. A held PR (hold lists) is skipped without a lander.
. "$(dirname "${BASH_SOURCE[0]}")/landing_lib.sh"
cd "$LANDING_REPO" || exit 1   # gh needs a repo cwd (sig: "failed to run git: not a git repository")
log(){ echo "[$(date '+%F %T')] queue $*"; }
RESULTS="$LANDING_HOME/last-results.txt"; : > "$RESULTS"
# 2026-10-26: check the night pause HERE, before spawning a lander. The night window refuses if a land_voice_pr.sh process is
# alive at the instant it looks - even the few-second invocation that would itself exit 9 on the pause cost a whole 12B night.
while read -r pr wt br title; do
  [ -z "$pr" ] && continue
  if landing_is_held "$pr"; then log "#$pr on a hold list - not spawning a lander"; echo "$pr 10" >> "$RESULTS"; continue; fi
  if bash "$LANDING_DIR/night_busy.sh" voice; then log "#$pr night pause - not spawning a lander"; echo "$pr 9" >> "$RESULTS"; continue; fi
  log "=== #$pr"
  out=$(bash "$LANDING_DIR/land_voice_pr.sh" "$pr" "$wt" "$br" "$title" </dev/null 2>&1); rc=$?
  printf '%s\n' "$out" | tail -6 | cut -c1-170
  echo "$pr $rc" >> "$RESULTS"
  log "#$pr rc=$rc -> $(gh pr view "$pr" --json state --jq .state)"
done < "$1"
log "QUEUE DONE"
