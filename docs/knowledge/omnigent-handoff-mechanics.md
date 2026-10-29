---
type: Reference
title: Omnigent handoff mechanics
description: How to hand autonomous work to an Omnigent agent on the box - REST alone cannot start a claude-sdk session; the run -r kick that does, plus the in-container gotchas to put in every brief.
tags: [omnigent, agents, runbook, tooling]
timestamp: 2026-10-29T00:00:00Z
---

# Omnigent handoff mechanics

Verified 2026-07-03; re-check against the running container before relying on a detail. See [index](index.md); the review use of
the same fleet is in [omnigent-cross-review.md](omnigent-cross-review.md) and the container wiring in
[omnigent-container-config.md](omnigent-container-config.md). Server: loopback port 6767 of the `zoe-omnigent` container; local
calls bypass auth (`OMNIGENT_LOCAL_SINGLE_USER=1`).

## The trap

REST alone CANNOT start a claude-sdk session. `POST /v1/sessions`, then comments, then `comments/send` stages everything but the
agent never runs: the items list stays empty, because only the UI's WebSocket creates user items.

## The working recipe

1. `POST /v1/sessions {"agent_id": "<id>", "title": "..."}` (list agents with `GET /v1/agents`).
2. Do NOT rely on staging the brief as session comments (`POST /v1/sessions/<SID>/comments`): that staging can fail
   silently and the agent then starts without its task. The brief goes inline with `-p` in step 4.
3. Launch a runner: `POST /v1/hosts/<host_id>/runners {"session_id": "<SID>", "workspace": "/workspace"}` (host from `GET /v1/hosts`).
4. **The kick** (the part REST cannot do):
   ```sh
   BRIEF_B64=$(printf %s "$BRIEF" | base64 -w0)        # base64 survives both shells intact; the container decodes it
   docker exec -d zoe-omnigent sh -c "cd /workspace && omnigent run --server http://127.0.0.1:6767 --harness claude-sdk -r <SID> -p \"\$(echo $BRIEF_B64 | base64 -d)\" --no-log > /tmp/kick.log 2>&1"
   ```
   This is the quoting `scripts/maintenance/cross_review_polly.sh` uses (reuse that wrapper where you can). A bare `"$BRIEF"` inside
   the outer double quotes closes and reopens them, so `sh -c` gets only the first word of the task. The session goes `running` and survives the client shutting down,
   because everything lives in the container.
5. Monitor: `GET /v1/sessions/<SID>` (status) and `.../items`; runner logs are under the in-container host-runner log directory.
   A session that is idle with zero ASSISTANT messages is an alarm, not a clean finish.

## Container gotchas (put these in every brief)

- The repo is mounted at `/workspace` and is the LIVE checkout: work in a worktree under `/workspace/.claude/worktrees/`,
  never edit main directly.
- The database URL in the environment points at `localhost`, which is unreachable in the container: use the `zoe-database`
  service name on the shared network.
- The real MemPalace is NOT mounted (a safety feature): use an isolated `MEMPALACE_DATA_DIR` under `/tmp` for experiments.
- `gh` works (mounted config). Test dependencies (pytest, asyncpg) are not preinstalled: pip-install them.
- `codex_goal` is codex-native only, not claude-sdk.
