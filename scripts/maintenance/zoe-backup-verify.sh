#!/usr/bin/env bash
#
# Backup verification (zoe-backup-verify.timer, DAILY 05:30; the on-box drill runs only when due, ~weekly).
#
# 1. ON-BOX, weekly: `zoe_restore_drill.py` restores the newest Postgres dumps into a THROWAWAY pg17 + pgvector
#    container (same image digest as the live zoe-database, own internal network, memory-capped, torn down after)
#    and checks every table's row count against the dump's manifest, the pgvector and Alembic versions and an
#    HNSW build + query; opens the newest MemPalace tarball with the py3.12 venv's chromadb and runs a vector
#    query; and checks the host archive's files against its manifest. (Deep dive 2 S0-8: this script used to
#    restore INTO THE LIVE Postgres instance and count two tables.) It refuses inside the night window, waits for
#    a running backup, waits up to 30 min for its MemAvailable floor (1100 MB, measured) and then refuses LOUDLY
#    (alert + failed unit). It skips itself (exit 5) while a pass is under ~6.5 days old, so the daily timer
#    retries a refused or failed drill the next morning instead of a week later.
# 2. OFF-BOX, monthly (architecture deep dive A5): `zoe_offbox.py drill` restores the newest snapshot of every
#    tier from the411, compares sha256 with what this box sent, reads a twelfth of the repo's data, and checks the
#    household tier ages out within 35 days. It gates itself (once per 28 days) and alerts on its own failure.
#    Skipped, with a line, while the off-box backup is not configured.
#
# Each step alerts on its own failure (Telegram, via the crash-loop watcher's sender); this script only folds
# their exit codes into the unit's result. Exit 3 from the on-box drill = quiet refusal (night window): not a
# unit failure. Exit 4 = loud refusal (low memory, a backup stuck, or no pass for 10 days): the unit FAILS so a drill
# that never runs is visible in systemctl, not just Telegram. Exit 5 = not due: nothing ran, not a failure.
#
# LOG FORMAT (~/.zoe-logs/backup-verify.log has no journald timestamps): every run is bracketed by
#   zoe-backup-verify: start <ISO-8601 local time with offset>
#   zoe-backup-verify: result <ISO-8601> onbox=<pass|failed|refused-quiet|refused-loud|not-due> offbox=<ok|failed|skipped>
# and then the unchanged last line ("zoe-backup-verify: done" / "zoe-backup-verify: N problem(s) detected"), so
# the ALERT: lines between a start and its result belong to that run.

set -uo pipefail

HERE="$(dirname "$(readlink -f "$0")")"
errors=0
stamp() { date '+%Y-%m-%dT%H:%M:%S%:z'; }

echo "zoe-backup-verify: start $(stamp)"

python3 -u "$HERE/zoe_restore_drill.py"   # -u: ok/FINDING lines stay in order with the ALERT lines (one log)
rc=$?
case $rc in
  0) onbox=pass ;;
  3) onbox=refused-quiet
     echo "zoe-backup-verify: on-box restore drill refused quietly (night window; see above)" ;;
  4) onbox=refused-loud
     echo "zoe-backup-verify: on-box restore drill REFUSED to run (low memory / stuck backup / stale; alerted)" >&2
     errors=$((errors + 1)) ;;
  5) onbox=not-due ;;
  *) onbox=failed
     echo "zoe-backup-verify: on-box restore drill FAILED (alerted)" >&2
     errors=$((errors + 1)) ;;
esac

if [[ -f "$HOME/.config/zoe-backup/offbox.env" ]]; then
  if python3 "$HERE/zoe_offbox.py" drill; then
    offbox=ok
  else
    offbox=failed
    echo "zoe-backup-verify: off-box restore drill failed (details above)" >&2
    errors=$((errors + 1))
  fi
else
  offbox=skipped
  echo "zoe-backup-verify: off-box backup not configured - monthly drill skipped (docs/knowledge/offbox-backup.md)"
fi

echo "zoe-backup-verify: result $(stamp) onbox=$onbox offbox=$offbox"
if [[ $errors -gt 0 ]]; then
  echo "zoe-backup-verify: $errors problem(s) detected" >&2
  exit 1
fi
echo "zoe-backup-verify: done"
