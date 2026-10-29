#!/bin/bash
# autoland.sh: overnight auto-lander. Every ~10 min, for every OPEN, non-draft PR not on the hold list:
#   - voice-path diff (voice_gate_check --scope-only) -> make sure it is in the voice queue (4-field format)
#   - otherwise -> one docs_merge_chain pass (which holds on threads/checks and merges when green)
# Pauses only while night_busy.sh deploy says so (02:45 until the 12B window has finished). Stops at STOP_AT (default 12:00 next day).
# Hold lists: $ZOE_LANDING_HOME/coord/hold.txt and chain-hold.txt (landing_is_held). ZOE_LANDING_ONCE=1 = a single pass.
. "$(dirname "${BASH_SOURCE[0]}")/landing_lib.sh"
C=$LANDING_HOME/coord
P=$LANDING_HOME
STOP_AT=${STOP_AT:-$(date -d 'tomorrow 12:00' +%s)}
quiet(){ bash "$LANDING_DIR/night_busy.sh" deploy; }   # 2026-10-19: measured night window, not a fixed blackout
log(){ echo "[$(date '+%F %T')] autoland $*" >> "$LANDING_LOG"; }
mkdir -p "$C" "$P/logs"; touch "$C/hold.txt"
while [ "$(date +%s)" -lt "$STOP_AT" ]; do
  while quiet; do sleep 300; done
  for row in $(gh pr list --state open --limit 40 --json number,isDraft,headRefName --jq '.[] | select(.isDraft==false) | "\(.number):\(.headRefName)"'); do
    pr=${row%%:*}; br=${row#*:}
    landing_is_held "$pr" && continue
    quiet && break
    gh pr view "$pr" --json files --jq '.files[].path' > "$C/files-$pr.txt" 2>/dev/null || continue
    scope=$(cd "$LANDING_REPO" && python3 scripts/maintenance/voice_gate_check.py --scope-only --changed-files-from "$C/files-$pr.txt" 2>&1 | grep -o 'scope: [A-Z]*' | awk '{print $2}')
    if [ "$scope" = VOICE ]; then
      grep -qE "^$pr " "$P/voice-pending.txt" 2>/dev/null || { echo "$pr $HOME/.worktrees/land-$pr $br pr$pr" >> "$P/voice-pending.txt"; log "#$pr voice-path -> queued for the voice loop"; }
      continue
    fi
    exec 9>"$C/docs-chain.lock"; flock 9
    while pgrep -f 'land_voice_pr\.sh|land_queue\.sh|docs_merge_chain\.sh' >/dev/null; do sleep 5; done
    quiet || bash "$LANDING_DIR/docs_merge_chain.sh" "$pr" >> "$LANDING_LOG" 2>&1
    flock -u 9
    grep -qE '^[0-9]+ ' "$P/voice-pending.txt" 2>/dev/null && sleep 240
  done
  [ -n "${ZOE_LANDING_ONCE:-}" ] && break
  sleep 600
done
log "stopped (STOP_AT reached or single pass done)"
