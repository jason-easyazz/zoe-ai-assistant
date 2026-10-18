#!/usr/bin/env bash
#
# Weekly backup verification (zoe-backup-verify.timer, Sunday 05:30).
#
# 1. ON-BOX, weekly: `zoe_restore_drill.py` restores the newest Postgres dumps into a THROWAWAY pg17 + pgvector
#    container (same image digest as the live zoe-database, own internal network, memory-capped, torn down after)
#    and checks every table's row count against the dump's manifest, the pgvector and Alembic versions and an
#    HNSW build + query; opens the newest MemPalace tarball with the py3.12 venv's chromadb and runs a vector
#    query; and checks the host archive's files against its manifest. (Deep dive 2 S0-8: this script used to
#    restore INTO THE LIVE Postgres instance and count two tables.) It refuses inside the night window, waits for
#    a running backup, and refuses with an alert below its MemAvailable floor.
# 2. OFF-BOX, monthly (architecture deep dive A5): `zoe_offbox.py drill` restores the newest snapshot of every
#    tier from the411, compares sha256 with what this box sent, reads a twelfth of the repo's data, and checks the
#    household tier ages out within 35 days. It gates itself (once per 28 days) and alerts on its own failure.
#    Skipped, with a line, while the off-box backup is not configured.
#
# Each step alerts on its own failure (Telegram, via the crash-loop watcher's sender); this script only folds
# their exit codes into the unit's result. Exit 3 from the on-box drill = refused (night window, low memory, a
# backup still running): not a unit failure, and already alerted when it should be.

set -uo pipefail

HERE="$(dirname "$(readlink -f "$0")")"
errors=0

python3 "$HERE/zoe_restore_drill.py"
rc=$?
if [[ $rc -eq 3 ]]; then
  echo "zoe-backup-verify: on-box restore drill refused (see above)"
elif [[ $rc -ne 0 ]]; then
  echo "zoe-backup-verify: on-box restore drill FAILED (alerted)" >&2
  errors=$((errors + 1))
fi

if [[ -f "$HOME/.config/zoe-backup/offbox.env" ]]; then
  if ! python3 "$HERE/zoe_offbox.py" drill; then
    echo "zoe-backup-verify: off-box restore drill failed (details above)" >&2
    errors=$((errors + 1))
  fi
else
  echo "zoe-backup-verify: off-box backup not configured - monthly drill skipped (docs/knowledge/offbox-backup.md)"
fi

if [[ $errors -gt 0 ]]; then
  echo "zoe-backup-verify: $errors problem(s) detected" >&2
  exit 1
fi
echo "zoe-backup-verify: done"
