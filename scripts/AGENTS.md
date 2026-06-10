# scripts/ — operational scripts

## Purpose

Operational scripting for Zoe hosts: setup, maintenance, deployment, migrations, testing helpers, and systemd unit templates.

## Ownership

- `setup/` — host/platform provisioning, including `setup/jetson/` systemd unit templates (e.g. `zoe-graphify-refresh.service` / `.timer`).
- `maintenance/` — recurring operational jobs: `graphify_local_refresh.py` (nightly fail-closed local knowledge-graph refresh), `graphify_local_probe.py` (offline Graphify acceptance probe), `refresh_graphify.sh` (legacy/manual cloud-backed refresh), `prune_worktrees.sh` (stale worktree cleanup), `greploop_guard.py` (Greptile fix-packet loop), triage generators.
- `deploy/`, `migrations/`, `testing/`, `train/`, `utilities/`, `preview/` — task-specific script groups.

## Local Contracts

- Scripts belong in a category subfolder, never in the repository root.
- Installed systemd user units live in `~/.config/systemd/user/`; the copies here are templates. Keep both in sync when changing a unit.
- Scripts run by timers/CI have no login session: prefix user-service systemctl calls with `XDG_RUNTIME_DIR=/run/user/$(id -u)`.
- The recurring Graphify timer must call `graphify_local_refresh.py`, which runs against Zoe's localhost model path and syncs `graphify-out` only after an accepted clustered probe. It must fail closed and leave committed graph artifacts untouched on timeout, invalid JSON, truncation, missing graph output, or sync failure.
- `refresh_graphify.sh` is legacy/manual cloud-backed evidence tooling. Do not wire it to recurring timers unless an operator explicitly approves a temporary cloud refresh. It must never require a clean live working tree and must never use `graphify update` (inflates the graph).
- `prune_worktrees.sh` is dry-run by default; never pass `--execute` without operator review of the candidate list. Skips dirty, locked, live-checkout, unmerged, and recently-active worktrees.

## Work Guidance

Log to journal (systemd) or an explicit log path; scripts that fail silently have already cost this repo a stale knowledge graph.

## Verification

For timer-driven scripts, run once manually with the unit's exact ExecStart line and check `systemctl --user status <unit>` after the next scheduled fire.

## Child DOX Index

No child AGENTS.md files yet.
