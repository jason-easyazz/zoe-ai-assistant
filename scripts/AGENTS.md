# scripts/ — operational scripts

## Purpose

Operational scripting for Zoe hosts: setup, maintenance, deployment, migrations, testing helpers, and systemd unit templates.

## Ownership

- `setup/` — host/platform provisioning, including `setup/jetson/` systemd unit templates (e.g. `zoe-graphify-refresh.service` / `.timer`).
- `maintenance/` — recurring operational jobs: `refresh_graphify.sh` (nightly fail-closed OpenRouter/Gemini knowledge-graph refresh), `graphify_openrouter_cli.py` (repo-owned OpenRouter Graphify launcher), `graphify_local_refresh.py` / `graphify_local_probe.py` (offline local probes; not sufficient for full-corpus scheduled refresh), `prune_worktrees.sh` (stale worktree cleanup), `greploop_guard.py` (Greptile fix-packet loop), `zoe-memory-lint.py` (report-only memory Lint CLI — prints contradictions/stale/orphan/duplicate findings, never mutates stored memory), triage generators, and `pi_intent_probe.py` for local Pi/Gemma ambiguous-intent timing evidence.
- `deploy/`, `migrations/`, `testing/`, `train/`, `utilities/`, `preview/` — task-specific script groups.

## Local Contracts

- Scripts belong in a category subfolder, never in the repository root.
- Installed systemd user units live in `~/.config/systemd/user/`; the copies here are templates. Keep both in sync when changing a unit.
- Scripts run by timers/CI have no login session: prefix user-service systemctl calls with `XDG_RUNTIME_DIR=/run/user/$(id -u)`.
- The recurring Graphify timer must call `refresh_graphify.sh`, which runs Graphify from a clean `origin/main` snapshot through OpenRouter with Gemini fallback and syncs `graphify-out` only after extraction, clustering, provider-error checks, and path normalization pass. It must fail closed and leave committed graph artifacts untouched on provider/auth/quota errors, extraction failure, missing graph output, or sync failure.
- After a successful rebuild, the script also lands the refreshed graph in git so the committed `graphify-out/` stays in sync with HEAD: from the disposable `origin/main` snapshot worktree (never the live checkout) it commits only `graphify-out/`, pushes branch `chore/graphify-refresh-<head>`, and opens a `gh pr create` PR against `main`. It skips cleanly when `graphify-out` matches `origin/main`, reuses the branch and never opens a duplicate PR (checks `gh pr list --head`), and stays fail-closed — any git/`gh` error logs, writes the error marker, and exits non-zero without committing or corrupting the live tree. The live-tree rsync still runs so local `graphify query` stays current.
- `graphify_local_refresh.py` and `graphify_local_probe.py` are offline evidence tools for small/scope probes. Zoe's local Gemma model is not sufficient for the full-corpus scheduled refresh; do not wire it to the recurring timer unless a larger local model is installed and accepted by a full clustered probe.
- Graphify refresh tooling must never require a clean live working tree and must never use `graphify update` (inflates the graph).
- `prune_worktrees.sh` is dry-run by default; never pass `--execute` without operator review of the candidate list. Skips dirty, locked, live-checkout, unmerged, and recently-active worktrees.

## Work Guidance

Log to journal (systemd) or an explicit log path; scripts that fail silently have already cost this repo a stale knowledge graph.

## Verification

For timer-driven scripts, run once manually with the unit's exact ExecStart line and check `systemctl --user status <unit>` after the next scheduled fire.

## Child DOX Index

No child AGENTS.md files yet.
