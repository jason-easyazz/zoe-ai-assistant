# scripts/ — operational scripts

## Purpose

Operational scripting for Zoe hosts: setup, maintenance, deployment, migrations, testing helpers, and systemd unit templates.

## Ownership

- `setup/` — host/platform provisioning, including `setup/jetson/` systemd unit templates (e.g. `zoe-graphify-refresh.service` / `.timer`).
- `maintenance/` — recurring operational jobs: `refresh_graphify.sh` (nightly fail-closed provider-backed knowledge-graph refresh), `graphify_local_refresh.py` (local/offline full-corpus experiment, not the scheduled default), `graphify_local_probe.py` (offline Graphify acceptance probe), `prune_worktrees.sh` (stale worktree cleanup), `greploop_guard.py` (Greptile fix-packet loop), triage generators.
- `deploy/`, `migrations/`, `testing/`, `train/`, `utilities/`, `preview/` — task-specific script groups.

## Local Contracts

- Scripts belong in a category subfolder, never in the repository root.
- Installed systemd user units live in `~/.config/systemd/user/`; the copies here are templates. Keep both in sync when changing a unit.
- Scripts run by timers/CI have no login session: prefix user-service systemctl calls with `XDG_RUNTIME_DIR=/run/user/$(id -u)`.
- The recurring Graphify timer must call `refresh_graphify.sh --daily`. The wrapper prefers the OpenRouter backend when `OPENROUTER_API_KEY` is available from the environment, `.env`, or `~/.hermes/.env`; `GRAPHIFY_BACKEND` and `GRAPHIFY_MODEL`/`GRAPHIFY_OPENROUTER_MODEL` may override the default. It must fail closed and leave committed graph artifacts untouched on quota, auth, provider, timeout, missing graph output, or sync failure.
- `graphify_local_refresh.py` remains local/offline evidence tooling for localhost model experiments. Do not wire it to recurring timers for the full Zoe corpus until it has accepted full-corpus evidence.
- Never use `graphify update` (inflates the graph).
- `prune_worktrees.sh` is dry-run by default; never pass `--execute` without operator review of the candidate list. Skips dirty, locked, live-checkout, unmerged, and recently-active worktrees.

## Work Guidance

Log to journal (systemd) or an explicit log path; scripts that fail silently have already cost this repo a stale knowledge graph.

## Verification

For timer-driven scripts, run once manually with the unit's exact ExecStart line and check `systemctl --user status <unit>` after the next scheduled fire.

## Child DOX Index

No child AGENTS.md files yet.
