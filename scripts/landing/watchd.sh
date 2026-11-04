#!/bin/bash
# Persistent landing watcher (2026-10-28): never exits on an alert. Appends each NEW attention line once to the events log,
# plus a line for any required CI job running > HUNG_MIN on an open non-draft PR, plus merges. Run under nohup.
#   ZOE_WATCHD_SKIP         PR numbers to ignore, space separated (default: contents of $ZOE_LANDING_HOME/coord/watchd-skip.txt)
#   ZOE_WATCHD_VOICE_LOG    the voice loop's log (default $ZOE_LANDING_HOME/logs/after_voice_more.log)
#   ZOE_WATCHD_ONCE=1       one pass then exit (tests) ; ZOE_WATCHD_INTERVAL seconds between passes (default 120)
. "$(dirname "${BASH_SOURCE[0]}")/landing_lib.sh"
cd "$LANDING_REPO" || exit 1   # gh needs a repo cwd
mkdir -p "$LANDING_HOME/logs"
LQ=$LANDING_LOG
VL=${ZOE_WATCHD_VOICE_LOG:-$LANDING_HOME/logs/after_voice_more.log}
EV=$LANDING_HOME/logs/watchd-events.log
SEEN=$LANDING_HOME/logs/watchd-seen.txt
SKIP_LIST=${ZOE_WATCHD_SKIP:-$(tr '\n' ' ' < "$LANDING_HOME/coord/watchd-skip.txt" 2>/dev/null)}
SKIP=$(printf '%s\n' $SKIP_LIST | grep -E '^[0-9]+$' | paste -sd'|'); [ -n "$SKIP" ] && SKIP="#($SKIP) " || SKIP='#(NONE) '
HUNG_MIN=45
touch "$EV" "$SEEN" "$LQ" "$VL"
start_lq=$(wc -l < "$LQ"); start_vl=$(wc -l < "$VL")
[ -n "${ZOE_WATCHD_ONCE:-}" ] && { start_lq=${ZOE_WATCHD_FROM_LINE:-0}; start_vl=${ZOE_WATCHD_FROM_LINE:-0}; }
emit(){ key=$(printf '%s' "$1" | sed -E 's/^\[[^]]*\] ?//' | md5sum | cut -c1-16); grep -qx "$key" "$SEEN" && return; echo "$key" >> "$SEEN"; echo "[$(date '+%F %T')] $1" >> "$EV"; }
prev_open=""
while true; do
  { tail -n +"$((start_lq+1))" "$LQ"; tail -n +"$((start_vl+1))" "$VL"; } \
    | grep -E "PROBE NOT PASS|PARKED|needs a hand merge|unresolved review thread|validate=FAILURE|FAILED|CONFLICTING|already failed validate" \
    | grep -vE "$SKIP" | while IFS= read -r l; do emit "$l"; done
  open=$(gh pr list --state open --json number,isDraft --jq '.[]|select(.isDraft|not)|.number' 2>/dev/null | sort -n | tr '\n' ' ')
  if [ -n "$prev_open" ]; then
    for n in $prev_open; do case " $open " in *" $n "*) ;; *) s=$(gh pr view "$n" --json state --jq .state 2>/dev/null); emit "#$n left the open list: $s";; esac; done
  fi
  [ -n "$open" ] && prev_open="$open"
  now=$(date +%s)
  for n in $open; do
    echo "#$n " | grep -qE "$SKIP" && continue
    # armed + green + BLOCKED = something invisible holds it (an unresolved thread the tools missed, a stale review)
    st=$(gh pr view "$n" --json mergeStateStatus,autoMergeRequest --jq '"\(.mergeStateStatus) \(.autoMergeRequest!=null)"' 2>/dev/null)
    if [ "$st" = "BLOCKED true" ]; then
      req=$(gh pr checks "$n" --required 2>/dev/null | awk '{print $2}' | sort -u | tr '\n' ' ')
      unres=$(landing_unresolved_count "$n") || unres=unknown   # a failed read is "unknown", never 0 or a partial count
      [ "$req" = "pass " ] && emit "#$n armed + required checks green but BLOCKED: $unres unresolved thread(s) $(date +%F)"
    fi
    gh pr checks "$n" --json name,state,startedAt 2>/dev/null \
      | python3 -c "
import json,sys,datetime
n,now,lim=sys.argv[1],int(sys.argv[2]),int(sys.argv[3])
for c in json.load(sys.stdin):
    if c.get('state') in ('IN_PROGRESS','QUEUED','PENDING') and c.get('name') in ('validate','zoe-data-py312','secret-scan') and c.get('startedAt'):
        t=int(datetime.datetime.fromisoformat(c['startedAt'].replace('Z','+00:00')).timestamp())
        if now-t>lim*60: print(f'#{n} {c[\"name\"]} running {(now-t)//60} min (possible hang)')
" "$n" "$now" "$HUNG_MIN" 2>/dev/null | while IFS= read -r l; do emit "$l"; done
  done
  [ -n "${ZOE_WATCHD_ONCE:-}" ] && exit 0
  sleep "${ZOE_WATCHD_INTERVAL:-120}"
done
