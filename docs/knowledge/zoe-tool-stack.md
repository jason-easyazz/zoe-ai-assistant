---
type: Reference
title: Zoe Tool Stack
description: The agent tooling installed in Zoe and how the pieces relate — knowledge graph, source cache, agent orchestration, the brain, memory, and skill security.
tags: [tooling, architecture, agents]
timestamp: 2026-06-18T00:00:00Z
---

# Zoe Tool Stack

Descriptive map of the tools in Zoe's agent layer. See [index](index.md). For *binding rules* on each tool, the `AGENTS.md` contracts are authoritative; this page records what each tool is and how they connect.

## Knowledge & source
- **codebase-memory** (MCP server) — repo code intelligence / code graph, re-indexed on demand. This is the maintained source of architecture and code-graph context. (Graphify is **fully retired**: `graphify-out/` is no longer committed, the `refresh_graphify.sh` / `zoe-graphify-refresh.timer` auto-refresh is removed, and the `graphify_search` MCP tool + agent-sync rebuild trigger have been deleted. Use codebase-memory for all code-graph queries.)
- **serena** (MCP server) — LSP symbol-level navigation (find_symbol, find_referencing_symbols, rename), complements codebase-memory's graph.
- **opensrc** (`~/.local/bin/opensrc`, vercel-labs) — third-party source cache at `~/.opensrc/`, refreshed weekly by `~/bin/refresh-opensrc.sh` (`zoe-opensrc-refresh.timer`) and pinned to Zoe's *installed* versions. Bare `opensrc path pypi:<pkg>` resolves to the latest published version, so pin versions to match the running stack.

## Agents & orchestration
- **Pi** (`pi`, earendil-works/pi-coding-agent) — the local coding-agent runtime; backs the **Zoe** brain agent on local Gemma 4 E2B (`:11434`). Reads `AGENTS.md` on demand, supports progressively-disclosed skills, and has no built-in subagents (use an extension such as `pi-subagents`, pi-spawns-pi, or Multica routing).
- **Multica** (`multica`, multica-ai) — agent/board orchestration. A systemd daemon auto-detects runtimes (`pi`, `hermes`, `cursor`, `openclaw`, …) and runs autopilots; squads let a leader agent route to members.
- **Hermes** (`hermes`, NousResearch, GPT-5.4) — default engineering agent (planning, code, review, repair, Greptile loops). Heavy work is delegated here from the lean voice brain via `escalate_to_hermes` / `a2a_delegate`.
- **OpenClaw** (`openclaw`) — Pi plus multi-agent orchestration, a skills marketplace, and messaging ("OpenClaw is Pi, plus everything built on top"). Runs local Gemma 4 E2B, not Codex. Explicit/manual fallback, not the default route.
- **mcporter** (`~/bin/mcporter-safe` wrapper → npm `mcporter`) — CLI MCP client; `intent_router._run_mcporter` uses it as the out-of-process fallback for intents with no in-process direct executor, spawning `services/zoe-data/mcp_server.py` over stdio per `~/.mcporter/mcporter.json`. **Never bake `POSTGRES_URL` (or any rotatable secret) into mcporter.json**: MCP stdio children get a sanitized env, and a pre-set key blocks `bootstrap_runtime_env()`'s canonical `.env` load, so the baked value silently rots on rotation — root cause of the 2026-07 `ok:false` write-intent class. The subprocess self-loads the current credential from `services/zoe-data/.env`; on pool-init failure it exits 1 loudly (regression-tested in `tests/test_mcporter_fallback.py`).

## Memory & safety
- **MemPalace** — ChromaDB-backed semantic memory plus user portrait and open-loops engine (in `services/zoe-data`). Consolidated nightly ("dreaming"). A periodic lint pass (contradictions / stale / orphans) is the missing third operation to add.
- **SkillSpector** (`~/.local/bin/skillspector`, NVIDIA) — security scanner for agent skills/extensions. Gate before installing or promoting a skill/extension; the static stage is conservative, so use the LLM stage plus human judgement for verdicts. See the root `AGENTS.md` "Skill & extension safety" contract.

## Testing & regression
- **Voice regression + speed harness** — Jason's real-voice corpus (`~/.zoe-voice-samples`, auto-growing via `ZOE_VOICE_SAVE_AUDIO`) replayed through the live voice path. `scripts/maintenance/voice_regression_probe.py` is the baseline-compared gate (function OK-rate + per-stage speed, `--update-baseline`, trend log, memory-self-guarded, flock'd); it wraps `scripts/perf/measure_voice.py` / `measure_tts.py`. **Replay-gating every voice change is mandatory** (root `AGENTS.md`). Scheduled daily via `scripts/setup/systemd/zoe-voice-regression.{service,timer}`. Full doc: [voice-pipeline.md](voice-pipeline.md). Warm-harness numbers are relative (drift vs baseline), not live performance.
- **Latency probe** — `scripts/maintenance/zoe_latency_probe.py`: light chat/voice/health latency check vs a saved baseline (`--update-baseline`); the pattern the voice probe mirrors.
