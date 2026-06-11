# scripts/ — operational scripts

## Purpose

Operational scripting for Zoe hosts: setup, maintenance, deployment, migrations, testing helpers, and systemd unit templates.

## Ownership

- `setup/` — host/platform provisioning, including `setup/jetson/` systemd unit templates (e.g. `zoe-graphify-refresh.service` / `.timer`).
- `maintenance/` — recurring operational jobs: `refresh_graphify.sh` (nightly fail-closed OpenRouter/Gemini knowledge-graph refresh), `graphify_openrouter_cli.py` (repo-owned OpenRouter Graphify launcher), `graphify_local_refresh.py` / `graphify_local_probe.py` (offline local probes; not sufficient for full-corpus scheduled refresh), `prune_worktrees.sh` (stale worktree cleanup), `greploop_guard.py` (Greptile fix-packet loop), triage generators.
- `deploy/`, `migrations/`, `testing/`, `train/`, `utilities/`, `preview/` — task-specific script groups.

## Local Contracts

- Scripts belong in a category subfolder, never in the repository root.
- Installed systemd user units live in `~/.config/systemd/user/`; the copies here are templates. Keep both in sync when changing a unit.
- Scripts run by timers/CI have no login session: prefix user-service systemctl calls with `XDG_RUNTIME_DIR=/run/user/$(id -u)`.
- The recurring Graphify timer must call `refresh_graphify.sh`, which runs Graphify from a clean `origin/main` snapshot through OpenRouter with Gemini fallback and syncs `graphify-out` only after extraction, clustering, provider-error checks, and path normalization pass. It must fail closed and leave committed graph artifacts untouched on provider/auth/quota errors, extraction failure, missing graph output, or sync failure.
- `graphify_local_refresh.py` and `graphify_local_probe.py` are offline evidence tools for small/scope probes. Zoe's local Gemma model is not sufficient for the full-corpus scheduled refresh; do not wire it to the recurring timer unless a larger local model is installed and accepted by a full clustered probe.
- Graphify refresh tooling must never require a clean live working tree and must never use `graphify update` (inflates the graph).
- `prune_worktrees.sh` is dry-run by default; never pass `--execute` without operator review of the candidate list. Skips dirty, locked, live-checkout, unmerged, and recently-active worktrees.

## Work Guidance

Log to journal (systemd) or an explicit log path; scripts that fail silently have already cost this repo a stale knowledge graph.

## Verification

For timer-driven scripts, run once manually with the unit's exact ExecStart line and check `systemctl --user status <unit>` after the next scheduled fire.

## Child DOX Index

No child AGENTS.md files yet.
