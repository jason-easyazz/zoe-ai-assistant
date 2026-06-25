# scripts/ — operational scripts

## Purpose

Operational scripting for Zoe hosts: setup, maintenance, deployment, migrations, testing helpers, and systemd unit templates.

## Ownership

- `setup/` — host/platform provisioning, including `setup/jetson/` systemd unit templates.
- `maintenance/` — recurring operational jobs: `graphify_openrouter_cli.py` (repo-owned OpenRouter Graphify launcher), `graphify_local_refresh.py` / `graphify_local_probe.py` (offline local probes; not sufficient for full-corpus scheduled refresh), `prune_worktrees.sh` (stale worktree cleanup), `greploop_guard.py` (Greptile fix-packet loop), `zoe-memory-lint.py` (report-only memory Lint CLI — prints contradictions/stale/orphan/duplicate findings, never mutates stored memory), triage generators, and `pi_intent_probe.py` for local Pi/Gemma ambiguous-intent timing evidence.
- `perf/` — speed-regression harness: `measure_speed.py` (brain TTFT + gen tok/s probe vs the live llama-server), `measure_voice.py` (voice e2e latency + said-vs-did correctness, wrapping `services/zoe-data/tests/replay_samples.py`), and `measure_tts.py` (Kokoro TTS time-to-first-audio — times the live sidecar synth of the first speakable clause, reporting cache hit/miss; sources reply text from the replay corpus or a plain `--replies-file`). All are read-only and gated behind `ZOE_PERF=1`; see `perf/README.md` for the before/after workflow and the captured baseline.
- `deploy/`, `migrations/`, `testing/`, `train/`, `utilities/`, `preview/` — task-specific script groups.

## Local Contracts

- Scripts belong in a category subfolder, never in the repository root.
- Installed systemd user units live in `~/.config/systemd/user/`; the copies here are templates. Keep both in sync when changing a unit.
- Scripts run by timers/CI have no login session: prefix user-service systemctl calls with `XDG_RUNTIME_DIR=/run/user/$(id -u)`.
- Graphify is **retired**: `graphify-out/` is no longer committed (it is gitignored), and the nightly auto-refresh (`refresh_graphify.sh` + the `zoe-graphify-refresh` systemd timer/service) has been removed from the repo. Code intelligence is now provided by the codebase-memory MCP, re-indexed on demand. Do not re-introduce a timer that commits `graphify-out/` into the repo.
- `graphify_local_refresh.py` and `graphify_local_probe.py` remain as offline evidence/probe tools for small/scope probes; they are not wired to any recurring timer.
- `prune_worktrees.sh` is dry-run by default; never pass `--execute` without operator review of the candidate list. Skips dirty, locked, live-checkout, unmerged, and recently-active worktrees.

## Work Guidance

Log to journal (systemd) or an explicit log path; scripts that fail silently have already cost this repo a stale knowledge graph.

## Verification

For timer-driven scripts, run once manually with the unit's exact ExecStart line and check `systemctl --user status <unit>` after the next scheduled fire.

## Child DOX Index

No child AGENTS.md files yet.
