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
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

require_clean_tree() {
    local phase="$1"
    git -C "$LIVE" update-index -q --refresh
    if [[ -n "$(git -C "$LIVE" status --porcelain)" ]]; then
        cat >&2 <<EOF
✗ REFUSING TO DEPLOY: live tree $LIVE has uncommitted changes during $phase.
  Commit, stash, or move those changes before running deploy_live.sh.
EOF
        git -C "$LIVE" status --short >&2
        exit 1
    fi
}

require_no_tracked_dirt() {
    local phase="$1"
    git -C "$LIVE" update-index -q --refresh
    if ! git -C "$LIVE" diff --quiet || ! git -C "$LIVE" diff --cached --quiet; then
        cat >&2 <<EOF
✗ REFUSING TO ROLLBACK: live tree $LIVE has tracked changes during $phase.
  Rollback uses git reset --hard and would overwrite tracked work.
EOF
        git -C "$LIVE" status --short >&2
        exit 1
    fi
}

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

require_clean_tree "pre-pull"

echo "▶ live tree on main — pulling latest…"
# Capture the current tip BEFORE the pull for a reliable rollback. Don't rely on
# ORIG_HEAD: a no-op ff pull (common — the live tree often already sits at main's
# tip) does NOT update ORIG_HEAD, so `reset --hard ORIG_HEAD` would roll back to a
# stale commit, or abort under `set -e` (skipping the recovery restart) if it was
# never set.
prev="$(git -C "$LIVE" rev-parse HEAD)"

# Voice replay-gate heartbeat ("a gate that can silently not-run is not a gate").
# Resolve what main is ABOUT to become without merging yet, so the gate runs
# against the incoming diff — and a block leaves the live tree at $prev, so a
# retry re-evaluates the same change instead of fast-forwarding past it and
# skipping the gate. If the incoming change touches the voice runtime path
# (STT/brain/TTS), a FRESH, PASSING replay-gate artifact must already exist; a
# missing/stale/skipped/failed artifact BLOCKS the deploy before any restart.
# Non-voice deploys are a no-op pass. This never runs the heavy Kokoro harness.
git -C "$LIVE" fetch --quiet origin main
target="$(git -C "$LIVE" rev-parse FETCH_HEAD)"
if ! python3 "$SCRIPT_DIR/voice_gate_check.py" --repo "$LIVE" --diff "${prev}..${target}"; then
    echo "✗ REFUSING TO DEPLOY: voice-path change without a fresh passing replay-gate result (see above)." >&2
    exit 1
fi

# Fast-forward to EXACTLY the gate-checked SHA — not `pull --ff-only origin main`,
# which runs a second fetch and could advance the tree to a commit pushed after
# the gate ran (a silent bypass of the very gate above). Greptile P1 on #1344.
git -C "$LIVE" merge --ff-only "$target"

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
if [[ "$code" != "200" ]]; then
    # The new commit is unhealthy — roll the live tree back to the pre-pull tip
    # captured above and restart, so a bad main can't leave the service down.
    echo "✗ health check failed — rolling back to $(git -C "$LIVE" rev-parse --short "$prev")" >&2
    # Runtime-generated files under the live checkout are gitignored (for example
    # data/*.db, data/logs/, data/backup/, backups/, checkpoints/, logs/, *.log,
    # and Python caches). Do not let those untracked artifacts block recovery.
    require_no_tracked_dirt "pre-rollback"
    git -C "$LIVE" reset --hard "$prev"
    systemctl --user restart "$SERVICE"
    exit 1
fi
