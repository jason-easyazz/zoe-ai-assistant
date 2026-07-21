---
type: plan
title: Perf & memory hardening — measure, change, keep-only-if-better
description: Sequenced plan for the remaining speed/memory levers and the loose ends from 2026-07-20/21, where every change carries its own gate and is reverted unless the measurement improves.
---

# Perf & memory hardening plan (2026-07-21)

**The rule for every item: MEASURE → CHANGE → RE-MEASURE → keep only if better,
revert otherwise.** Gates already exist — use them, never bypass:
`scripts/perf/measure_speed.py` (brain TTFT/tok-s, `ZOE_PERF=1`),
`voice_regression_probe.py` under `flock` (said-vs-did, ≥2G free),
`zoe_ground_truth.sh` (what runs), `/metrics` (cache hits),
`free -m` before/after. Voice-path changes are replay-gated **mechanically**
(the brain unit is in `voice_gate_check.py` since #1494).

Baseline to beat (measured live 2026-07-21, post-#1494 reboot):
cold persona ~3.2s · warm slot 0.18–0.69s · available ≈1061Mi at boot ·
all /health 200.

## Phase 0 — debts from the last change (do first)

- [ ] **0a. Replay gate for #1494** — the only thing between the brain tuning
      and "done". Runs at 04:30 via timer, or manually in a ≥2G window.
      **Keep-if:** status:pass, said-vs-did no regression. **Else:** restore
      pre-#1494 unit (git 687fd219), reboot.
- [ ] **0b. Runbook apply-bug fix** — `brain-kv-cache-tuning.md` apply steps
      crash a WARM box (CUDA can't get the 2.6G load buffer once Kokoro+agents
      fill memory; proven twice tonight). Rewrite: "apply + reboot" as the
      blessed path; "park kokoro-tts, restart, unpark" as the no-reboot path.
      Doc-only, no gate.

## Phase 1 — cheap speed, each independently gated

- [ ] **1a. Cache-hit visibility** — read `/metrics` after 24h of real traffic;
      record `--cache-ram` hit rate in the runbook. No change, evidence only.
- [ ] **1b. Prefill batch sweep** — `ZOE_PERF=1 measure_speed.py` baseline, then
      `-ub {256,512,1024}` × `-b {1024,2048}` on the unit (reboot per step or
      quiet-window restart per 0b). **Keep-if:** prompt-processing improves ≥10%
      with no gen-speed loss and boot stays clean. **Else:** revert flag.
- [ ] **1c. Sentence-streamed LiveKit TTS** (#1469, built, flag OFF) — flip in a
      lab window. **Keep-if:** replay gate green AND first-audio improves on
      `measure_tts.py`. **Else:** flag off again.
- [ ] **1d. `--slot-save-path` spike** (only if >2 hot personas emerges as a real
      pattern) — snapshot/restore persona KV on NVMe. **Keep-if:** restore
      <0.5s and no /health flakiness. Lab first, per VISION.

## Phase 2 — memory pressure, each independently gated

- [ ] **2a. `MemoryHigh` caps on agent units** (openclaw-gateway, hermes-agent,
      github-runner) via drop-ins, sized from `zoe_ground_truth.sh` +
      `systemctl show MemoryCurrent` observations. **Keep-if:** no unit enters
      throttle-thrash (journal) in 48h and brain headroom floor rises.
- [ ] **2b. ccd-cli fleet cleanup (W3.1, PLANS.md)** — the largest single
      reclaim (~3.6G swap historically). **Keep-if:** swap-in-use drops and no
      agent workflow breaks.
- [ ] **2c. Retirement completion → stop hermes-agent + openclaw-gateway**
      (~500Mi) — ONLY behind the §8/executor-migration gates already written.
      Not a shortcut; the gates are the plan.
- [ ] **2d. Dev-fleet offload (hardware)** — the standing conclusion: a small
      x86 box for Serena/runners/agents frees ~2G and removes the warm-box
      load-order fragility class entirely. Operator purchase decision.

## Phase 3 — correctness loose ends (order by user impact)

- [ ] **3a. `zoe-voice-regression.service` boot race** — add
      `After=docker.service` + a Postgres wait to the unit; a permanently-red
      unit masks real failures. **Test:** reboot → unit green.
- [ ] **3b. Hermes Codex auth** — fix or explicitly accept `openrouter/free`
      fallback; today it degrades silently on every turn.
- [ ] **3c. `expert_dispatch` split-brain** — first teach `zoe_flue_client` to
      honour a supplied `db_memory_context`, THEN reroute through
      `brain_dispatch`. Voice path ⇒ replay-gated. (Task #12 has the full
      sequencing and the trap.)
- [ ] **3d. Pin Multica image digests** (`:latest` → digest). **Test:**
      containers healthy after `docker compose up -d`.
- [ ] **3e. `panel_presence_events`** — delete table + purge timer, or build the
      writer; decide, don't leave a daily no-op job.

## Standing discipline

One change per PR, its gate named in the PR body, revert path stated. If the
gate can't run (headroom, lock), the change WAITS — a skipped gate is not a
pass. Every claim of improvement cites a number from before AND after.
