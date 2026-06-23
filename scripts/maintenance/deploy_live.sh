#!/usr/bin/env bash
# deploy_live.sh — the ONLY blessed way to update the live zoe-data service.
#
# Structural rule (see memory: no-concurrent-checkout-drivers, hermes-pr-pipeline-worktree):
#   • /home/zoe/assistant is the LIVE service tree and serves MAIN, always.
#   • ALL editing / branching / PR-merge work happens in a separate worktree
#     (/home/zoe/.worktrees/zoe-dev), so it can never disturb what's live.
#
# This script REFUSES to deploy if the live tree is on a feature branch — so a stray
# `systemctl restart` can never quietly ship un-reviewed branch code as "live".
set -euo pipefail

LIVE="${ZOE_LIVE_TREE:-/home/zoe/assistant}"
SERVICE="${ZOE_SERVICE:-zoe-data}"

cur="$(git -C "$LIVE" branch --show-current || true)"
if [[ "$cur" != "main" ]]; then
    cat >&2 <<EOF
✗ REFUSING TO DEPLOY: the live tree $LIVE is on '$cur', not main.
  The live service must serve main. Do feature work in /home/zoe/.worktrees/zoe-dev,
  merge the PR, then re-run this script. To re-pin the live tree:
      git -C $LIVE checkout main
EOF
    exit 1
fi

echo "▶ live tree on main — pulling latest…"
git -C "$LIVE" pull --ff-only origin main

echo "▶ restarting $SERVICE…"
systemctl --user restart "$SERVICE"

PORT="${ZOE_PORT:-8000}"
code=""   # init before the loop so `set -u` can't trip if it never runs
for _ in $(seq 1 45); do
    code="$(curl -s -o /dev/null -w '%{http_code}' -m 3 "http://127.0.0.1:${PORT}/health" || true)"
    [[ "$code" == "200" ]] && break
    sleep 2
done
echo "▶ health=$code  (live = main @ $(git -C "$LIVE" rev-parse --short HEAD))"
[[ "$code" == "200" ]] || { echo "✗ health check failed" >&2; exit 1; }
