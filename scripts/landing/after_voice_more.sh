#!/bin/bash
# rolling voice landings: whenever nothing else is landing/chaining, land the lines in voice-pending.txt (one batch), then loop.
# After each batch a still-open PR goes back in the queue (landing_requeue_decision, landing_lib.sh), except:
#   - CONFLICTING -> needs-hand-merge.txt (parked, not retried)
#   - probe NOT PASS (rc 4): a speed-only fail is re-queued ONCE per (pr, head); anything else -> probe-failed.txt (parked)
# ZOE_LANDING_ONCE=1 runs a single cycle (tests / dry runs).
. "$(dirname "${BASH_SOURCE[0]}")/landing_lib.sh"
cd "$LANDING_REPO" || exit 1
P=$LANDING_HOME; mkdir -p "$P/logs"
while true; do
  sleep "${ZOE_LANDING_LOOP_SLEEP:-90}"; while landing_others_busy || grep -qE '^[0-9]+$' "$P/chain-pending.txt" 2>/dev/null; do sleep 60; done
  grep -E '^[0-9]+ ' "$P/voice-pending.txt" | awk '!seen[$1]++' > "$P/queue-voice-batch.txt" 2>/dev/null; [ -s "$P/queue-voice-batch.txt" ] || { [ -n "${ZOE_LANDING_ONCE:-}" ] && exit 0; sleep 300; continue; }
  : > "$P/voice-pending.txt"; bash "$LANDING_DIR/land_queue.sh" "$P/queue-voice-batch.txt"
  while read -r pr rest; do [ -z "$pr" ] && continue; case "$(gh pr view "$pr" --json state --jq .state 2>/dev/null)" in MERGED|CLOSED) continue;; esac
    if [ "$(gh pr view "$pr" --json mergeable --jq .mergeable 2>/dev/null)" = CONFLICTING ]; then echo "$pr $rest" >> "$P/needs-hand-merge.txt"; echo "[$(date '+%F %T')] voice-loop #$pr PARKED: conflicting, needs a hand merge (needs-hand-merge.txt) - not retried" >> "$LANDING_LOG"; continue; fi
    rc=$(awk -v p="$pr" '$1==p{r=$2} END{print r}' "$P/last-results.txt" 2>/dev/null)
    case "$(landing_requeue_decision "$pr" "$rc")" in
      park-probe-failed) echo "$pr $rest" >> "$P/probe-failed.txt"; echo "[$(date '+%F %T')] voice-loop #$pr PARKED: probe NOT PASS and not retryable (probe-failed.txt) - not retried" >> "$LANDING_LOG";;
      retry-once) echo "[$(date '+%F %T')] voice-loop #$pr speed-only probe fail - ONE automatic re-queue" >> "$LANDING_LOG"; echo "$pr $rest" >> "$P/voice-pending.txt";;
      drop) :;;
      *) echo "$pr $rest" >> "$P/voice-pending.txt";;
    esac
  done < "$P/queue-voice-batch.txt"
  [ -n "${ZOE_LANDING_ONCE:-}" ] && exit 0
done
