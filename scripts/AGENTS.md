# scripts/ — operational scripts

## Purpose

Operational scripting for Zoe hosts: setup, maintenance, deployment, migrations, testing helpers, and systemd unit templates.

## Ownership

- `setup/` — host/platform provisioning, including `setup/jetson/` systemd unit templates (e.g. `zoe-graphify-refresh.service` / `.timer`).
- `maintenance/` — recurring operational jobs: `refresh_graphify.sh` (nightly knowledge-graph refresh), `greploop_guard.py` (Greptile fix-packet loop), triage generators.
- `deploy/`, `migrations/`, `testing/`, `train/`, `utilities/`, `preview/` — task-specific script groups.

## Local Contracts

- Scripts belong in a category subfolder, never in the repository root.
- Installed systemd user units live in `~/.config/systemd/user/`; the copies here are templates. Keep both in sync when changing a unit.
- Scripts run by timers/CI have no login session: prefix user-service systemctl calls with `XDG_RUNTIME_DIR=/run/user/$(id -u)`.
- `refresh_graphify.sh` extracts from a clean git worktree snapshot of `origin/main`; it must never require a clean live working tree and must never use `graphify update` (inflates the graph).

## Work Guidance

Log to journal (systemd) or an explicit log path; scripts that fail silently have already cost this repo a stale knowledge graph.

## Verification

For timer-driven scripts, run once manually with the unit's exact ExecStart line and check `systemctl --user status <unit>` after the next scheduled fire.

## Child DOX Index

No child AGENTS.md files yet.
