# scripts/ — operational scripts

## Purpose

Operational scripting for Zoe hosts: setup, maintenance, deployment, migrations, testing helpers, and systemd unit templates.

## Ownership

- `setup/` — host/platform provisioning, including `setup/jetson/` systemd unit templates.
- `maintenance/` — recurring operational jobs: `graphify_local_probe.py` (offline, observe-only local Graphify probe in a throwaway snapshot; never writes `graphify-out/` into the repo), `prune_worktrees.sh` (stale worktree cleanup), `greploop_guard.py` (Greptile fix-packet loop), `zoe-memory-lint.py` (report-only memory Lint CLI — prints contradictions/stale/orphan/duplicate findings, never mutates stored memory), triage generators, and `pi_intent_probe.py` for local Pi/Gemma ambiguous-intent timing evidence.
- `perf/` — speed-regression harness: `measure_speed.py` (brain TTFT + gen tok/s probe vs the live llama-server), `measure_voice.py` (voice e2e latency + said-vs-did correctness, wrapping `services/zoe-data/tests/replay_samples.py`), and `measure_tts.py` (Kokoro TTS time-to-first-audio — times the live sidecar synth of the first speakable clause, reporting cache hit/miss; sources reply text from the replay corpus or a plain `--replies-file`). All are read-only and gated behind `ZOE_PERF=1`; see `perf/README.md` for the before/after workflow and the captured baseline.
- `deploy/`, `migrations/`, `testing/`, `train/`, `utilities/`, `preview/` — task-specific script groups.

## Local Contracts

- Scripts belong in a category subfolder, never in the repository root.
- Installed systemd user units live in `~/.config/systemd/user/`; the copies here are templates. Keep both in sync when changing a unit.
- Scripts run by timers/CI have no login session: prefix user-service systemctl calls with `XDG_RUNTIME_DIR=/run/user/$(id -u)`.
- Graphify is **retired**: `graphify-out/` is no longer committed (it is gitignored), and the nightly auto-refresh (`refresh_graphify.sh` + the `zoe-graphify-refresh` systemd timer/service) plus the repo write-back wrapper (`graphify_local_refresh.py`) have been removed. Code intelligence is now provided by the codebase-memory MCP, re-indexed on demand. Do not re-introduce any script or timer that writes/commits `graphify-out/` into the repo.
- `graphify_local_probe.py` remains only as an offline, observe-only probe (runs in a throwaway snapshot); it never syncs output back into the repo and is not wired to any recurring timer.
- `prune_worktrees.sh` is dry-run by default; never pass `--execute` without operator review of the candidate list. Skips dirty, locked, live-checkout, unmerged, and recently-active worktrees.
- Destructive maintenance scripts must be dry-run by default, require explicit execution flags, and print the candidate list before deleting or rewriting anything.
- Git history rewrites must run only in a disposable bare mirror clone with typed confirmation. Force-pushing rewritten history requires a separate explicit push flag, typed push confirmation, and exact confirmation of the resolved `origin` push URL.
- Live deploy rollback may ignore gitignored/untracked runtime artifacts so recovery can proceed, but it must abort before `git reset --hard` when tracked worktree or index changes are present.
- Docker cleanup must not stop/remove containers or prune volumes; recurring cleanup is limited to dangling images and build cache.

## Work Guidance

Log to journal (systemd) or an explicit log path; scripts that fail silently have already cost this repo a stale knowledge graph.

## Verification

For timer-driven scripts, run once manually with the unit's exact ExecStart line and check `systemctl --user status <unit>` after the next scheduled fire.

## Child DOX Index

No child AGENTS.md files yet.
