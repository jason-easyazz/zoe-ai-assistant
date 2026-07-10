# scripts/ — operational scripts

## Purpose

Operational scripting for Zoe hosts: setup, maintenance, deployment, migrations, testing helpers, and systemd unit templates.

## Ownership

- `setup/` — host/platform provisioning, including `setup/jetson/` systemd unit templates.
- `maintenance/` — recurring operational jobs: `deploy_zoe_data_when_ready.sh` (readiness/watch gate with no working-tree/live mutation, but it fetches remote-tracking refs; deploy mode pre-flights target `origin/main` content, builds a temporary pinned remote whose `main` is exactly that SHA, and invokes blessed `deploy_live.sh` with `origin` pointed at the pinned remote; after `deploy_live.sh` returns, the wrapper aborts if live `HEAD` differs from the pre-flighted SHA; post-deploy liveness checks remain informational because `deploy_live.sh` owns restart/rollback), `prune_worktrees.sh` (stale worktree cleanup), `greploop_guard.py` (Greptile fix-packet loop), `reap_stale_serena.py` (dry-run-first reaper for stale per-session Serena MCP servers — orphaned or multi-GB-swapped instances only, 30-min grace, kills require `--execute`; paired hourly user timer `zoe-serena-reaper.timer` in `setup/systemd/`), `zoe-memory-lint.py` (report-only memory Lint CLI — prints contradictions/stale/orphan/duplicate findings, never mutates stored memory), `remediate_ownerless_memories.py` (dry-run-first MemPalace ownerless-row audit/delete/backfill tool; destructive modes require `--execute` and create a whole-store directory backup before Chroma connects), triage generators, and `pi_intent_probe.py` for local Pi/Gemma ambiguous-intent timing evidence.
- `perf/` — speed-regression harness: `measure_speed.py` (brain TTFT + gen tok/s probe vs the live llama-server), `measure_voice.py` (voice e2e latency + said-vs-did correctness, wrapping `services/zoe-data/tests/replay_samples.py`), and `measure_tts.py` (Kokoro TTS time-to-first-audio — times the live sidecar synth of the first speakable clause, reporting cache hit/miss; sources reply text from the replay corpus or a plain `--replies-file`). All are read-only and gated behind `ZOE_PERF=1`; see `perf/README.md` for the before/after workflow and the captured baseline.
- `deploy/`, `migrations/`, `testing/`, `train/`, `utilities/`, `preview/` — task-specific script groups.

## Local Contracts

- Scripts belong in a category subfolder, never in the repository root.
- `setup/kokoro_sidecar.py` persists its hot phrase cache under `~/.zoe/kokoro_cache/` (override `ZOE_KOKORO_CACHE_DIR`; disable with `ZOE_KOKORO_CACHE_PERSIST=0`) — `<sha256(key)>.wav` files plus `manifest.json` (hits/last_used/bytes), reloaded frequency-ranked on startup. Writes happen only off the synth hot path (periodic flush + shutdown) and are fail-open; disk budget via `ZOE_KOKORO_CACHE_MAX_DISK` / `ZOE_KOKORO_CACHE_MAX_DISK_BYTES`. Never point a temp/test instance at the live dir — use a `mktemp -d` dir and a non-10201 `KOKORO_SIDECAR_PORT`.
- Installed systemd user units live in `~/.config/systemd/user/`; the copies here are templates. Keep both in sync when changing a unit.
- Scripts run by timers/CI have no login session: prefix user-service systemctl calls with `XDG_RUNTIME_DIR=/run/user/$(id -u)`.
- Graphify is **retired and fully removed**: `graphify-out/` is not committed (gitignored), and the auto-refresh (`refresh_graphify.sh` + the `zoe-graphify-refresh` systemd timer/service), the repo write-back wrapper (`graphify_local_refresh.py`), and the former observe-only probe + shard tooling (`graphify_local_probe.py`, `graphify_local_shard_matrix.py`, `graphify_shard_sync_plan.py`, `graphify_shard_merge_validate.py`, `graphify_local_model_matrix.py`, `graphify_openrouter_cli.py`) have all been removed. Code intelligence is now provided by the codebase-memory MCP, re-indexed on demand. Do not re-introduce any graphify script, timer, or committed `graphify-out/` artifact.
- `prune_worktrees.sh` is dry-run by default; never pass `--execute` without operator review of the candidate list. Skips dirty, locked, live-checkout, unmerged, and recently-active worktrees.
- Destructive maintenance scripts must be dry-run by default, require explicit execution flags, and print the candidate list before deleting or rewriting anything.
- Git history rewrites must run only in a disposable bare mirror clone with typed confirmation. Force-pushing rewritten history requires a separate explicit push flag, typed push confirmation, and exact confirmation of every resolved `origin` push URL. Pushing to multiple URLs is not atomic: every confirmed destination is attempted regardless of earlier failures, and the run exits non-zero with the full list of any destinations left divergent.
- Live deploy rollback may ignore gitignored/untracked runtime artifacts so recovery can proceed, but it must abort before `git reset --hard` when tracked worktree or index changes are present.
- Docker cleanup must not stop/remove containers or prune volumes; recurring cleanup is limited to dangling images and build cache.

## Work Guidance

Log to journal (systemd) or an explicit log path; scripts that fail silently have already cost this repo a stale knowledge graph.

## Verification

For timer-driven scripts, run once manually with the unit's exact ExecStart line and check `systemctl --user status <unit>` after the next scheduled fire.

For `deploy_zoe_data_when_ready.sh`, run `pytest services/zoe-data/tests/test_deploy_zoe_data_when_ready.py`; the test uses fake git live trees and never restarts production.

## Child DOX Index

No child AGENTS.md files yet.
