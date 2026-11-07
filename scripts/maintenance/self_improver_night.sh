#!/bin/bash
# self_improver_night.sh - one night of the self-evolution Phase 1 improver (manner.md, shadow-apply only).
# Called by zoe-self-improver.service (TEMPLATE, not installed - docs/knowledge/self-improver.md has the owner steps).
#
# Every gate lives in scripts/perf/self_improver.py (fail closed): the 03:30-04:25 slot, harness quiet hours + panel,
# `night_busy.sh test`, zoe-voice-regression not active, no land_voice_pr.sh, the brain-window lock, MemAvailable, the
# fourteen-night stop rule, the reflection key. The key comes from the unit's EnvironmentFile by NAME
# (ZOE_IMPROVER_ANTHROPIC_KEY); this script never reads, prints or passes it on the command line.
#
# Usage: self_improver_night.sh [--dry-run] [--cells flaw,good] [--deadline 04:25]
# Exit: 0 measured / 2 unmeasured or error / 3 skipped by a gate / 4 refused (stop rule decided, no key, night already ran).
set -u
here=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd) || exit 2
cd "$here" || exit 2
exec nice -n 5 /usr/bin/python3 "$here/scripts/perf/self_improver.py" night "$@"
