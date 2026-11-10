#!/usr/bin/env bash
# Run only the tests a diff can affect (inner loop). See affected_tests.py / tests/AGENTS.md.
# Usage: scripts/maintenance/affected_tests.sh [<local-base-ref>] [--dry-run] [--json FILE|-] [-- <pytest args>]
exec python3 "$(dirname "$(readlink -f "$0")")/affected_tests.py" "$@"
