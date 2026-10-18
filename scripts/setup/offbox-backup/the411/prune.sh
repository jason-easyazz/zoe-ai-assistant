#!/bin/sh
# Server-side retention for every household repo (runs in the `prune` container, compose.yaml). The boxes write through
# an APPEND-ONLY rest-server and cannot delete, so this job is the ONLY thing that ages snapshots out.
#
# The policy is the box's RETENTION table (scripts/maintenance/zoe_offbox.py); tests/unit/test_zoe_offbox.py
# pins these flags equal to it. household = one per day for 7 days + one per week for 34 days, NO monthly, and
# TIME-bounded (`--keep-within-*`, relative to the newest snapshot; a count-bounded `--keep-weekly 4` keeps the last
# 4 weeks that HAVE snapshots, so after a gap it keeps anything): while the box backs up nightly, nothing holding
# household words is older than 35 days (34-day window + one day until the next daily prune), the bound in docs/knowledge/forget-reach.md ("forgotten means forever").
# A box that stopped backing up keeps its last 34 days as its restore point. The box's monthly drill alerts on both.
#
# One key file per household: /keys/<hid>.pass (a restic key added for pruning, `restic key add`; never the
# owner's master password). A household without a key file is skipped with a message, never guessed.
set -u
DATA="${DATA_DIRECTORY:-/data}"
KEYS="${PRUNE_KEYS:-/keys}"
rc=0
for keyfile in "$KEYS"/*.pass; do
  [ -f "$keyfile" ] || { echo "prune: no key files in $KEYS"; exit 0; }
  hid=$(basename "$keyfile" .pass)
  repo="$DATA/$hid"
  if [ ! -f "$repo/config" ]; then
    echo "prune: $hid has a key but no repo at $repo - skipped"
    continue
  fi
  export RESTIC_REPOSITORY="$repo" RESTIC_PASSWORD_FILE="$keyfile"
  echo "prune: $hid $(date -u +%FT%TZ)"
  restic forget --tag zoe-household --group-by host,tags --keep-within-daily 7d --keep-within-weekly 34d || rc=1
  restic forget --tag zoe-secrets --group-by host,tags --keep-daily 7 --keep-weekly 4 --keep-monthly 6 || rc=1
  restic forget --tag zoe-artefacts --group-by host,tags --keep-daily 7 --keep-weekly 4 --keep-monthly 6 || rc=1
  restic prune || rc=1
  # Weekly structural check (the box's monthly drill reads a data subset and restores the newest snapshots).
  [ "$(date -u +%u)" = "7" ] && { restic check || rc=1; }
done
exit $rc
