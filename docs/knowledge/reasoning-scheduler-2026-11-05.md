---
type: Reference
title: Reasoning scheduler (2026-11-05) - one choke point for "no daytime 4B outside a person's turn"
description: brain_scheduler wraps httpx once, names every zoe-data caller of the 4B brain slot, and counts/refuses a non-turn call between 07:00 and 22:00 unless it is a declared exception. Ships log-only; ZOE_BRAIN_SCHEDULER=enforce refuses. Also the per-start KOKORO_START line.
tags: [scheduler, brain, 4b, slot, policy, samantha-initiative, kokoro, measurement]
timestamp: 2026-11-05T12:00:00+08:00
---

# Reasoning scheduler (2026-11-05)

Policy (owner decision 2, Samantha initiative 2026-11-03, roadmap 5): 07:00-22:00 household time the 4B slot belongs to a
person's turn. Code: `services/zoe-data/brain_scheduler.py`. Tests: `services/zoe-data/tests/test_brain_scheduler.py`.

## How it works

`brain_scheduler.install()` (called in `main.py`'s startup) wraps `httpx.AsyncClient.send`. A generation request to the brain
port (`/v1/chat/completions`, `/completion`, ...) is named by the `brain_scheduler.declare(label)` context if one is set
(`brain_slot_guard.run_background` sets it), else by the calling `module.function` found on the stack. Classes:

| class | who | by day |
|---|---|---|
| `turn` | `zoe_agent.*`, `nlu_extractor.*`, `intent_classifier_llm.*`, `ui_compose.*`, `turn:*` (good-evening composer, portrait refresh) | allowed |
| `post_turn` | person extractor, turn digest, latent-intent, structural reader/verifier, retire judge | allowed while a household turn ended < 900 s ago, else idle |
| `exception` | `wake_prewarm`, `prefix_keeper` (Flue-routed; `brain_scheduler.note()`), `ZOE_BRAIN_SCHEDULER_ALLOW` labels | allowed |
| `request` | contact backfill (explicit HTTP request) | allowed |
| `idle` | everything else: nightly digest passes, portrait synthesis, proactive composer, idle consolidation, new callers | the violation |

Outside the day window an idle call is allowed (`reason=night`).

## Modes and the metric

`ZOE_BRAIN_SCHEDULER`: `off`, `log` (default: decide, count, log, never block), `enforce` (a daytime idle call raises
`BrainSchedulerDeferred` before it reaches the slot; callers already treat a failed call as "no result"). Window:
`ZOE_BRAIN_SCHEDULER_DAY` (`07:00-22:00`), `ZOE_BRAIN_SCHEDULER_POST_TURN_S` (900), `ZOE_BRAIN_SCHEDULER_ALLOW`.

One line per decision, no content: `BRAIN_SCHED caller=... class=... decision=allowed|would_defer|deferred reason=... mode=... idle=N`.
**Idle 4B calls (target 0)** = daytime lines with `class=idle` and `reason` other than `night`:

    grep -h BRAIN_SCHED ~/.zoe-logs/zoe-data.app.log* | grep "class=idle" | grep -v "reason=night" | awk '{print $2}' | sort | uniq -c | sort -rn

## Evidence for the person extractor decision

`BRAIN_SLOT_GUARD` lines in `~/.zoe-logs/zoe-data.app.log*` (2026-10-10 to 11-05): `person_extractor_llm` waited 52-240 s on
average per rotation, 7 of 28 gated calls hit the 240 s ceiling (the 2.5-4.5 min capture lag). It stays `post_turn`: a refusal
loses the fact (`process_text_llm` returns 0, nothing re-queues it) while the slot guard already keeps it off a hot conversation.
To make it stricter, shrink the window; to move it to night, a durable queue is needed first (not built).

## Not covered (other processes)

Operator scripts and timers that call `:11434` from their own process (`scripts/maintenance/*`, `scripts/perf/*`, the Flue
sidecar's own tools) never pass through zoe-data's httpx. Perf/bake-off tools already take `/tmp/zoe-brain-window.lock`; the
Flue sidecar's tool calls are part of a person's turn.

## Kokoro start line

`scripts/setup/kokoro_sidecar.py` logs `KOKORO_START device=cuda|cpu degraded=0|1 reason=... load_s=... pid=...` once per start
(WARNING level when degraded). Starts/day: `journalctl -u kokoro-tts --since today | grep -c KOKORO_START`; degraded starts:
`... | grep -c "KOKORO_START.*degraded=1"`.
