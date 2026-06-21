# Zoe Samantha Harness — Strategy

> The Phase-8 strategy doc the harness plan called for. Authoritative record of
> what Zoe's core is becoming, what's built, and what's left. Companion to
> [`zoe-evolution-harness-plan.md`](./zoe-evolution-harness-plan.md) (north-star)
> and the ADRs under `docs/adr/`.

## North Star

Zoe is a local, relationship-centered, **Samantha-style** assistant. Her brain is
**Gemma 4** running locally on the Jetson via the **`pi`** agent framework, with a
deterministic **intent fast-path in front for speed**. Stronger/cloud agents
(Hermes, OpenClaw) are bounded specialists the brain *calls as tools*, behind
Zoe's trust envelope; Multica is the governed control plane. The goal is not more
agents — it is a harness that lets Zoe safely become more herself.

## Architecture (the `services/zoe-core` brain)

`zoe-core` is the reasoning core: the `pi` agent on Gemma 4 E2B. Capabilities are
**Pi extensions**, auto-discovered and composed:

```
user → intent fast-path (speed cache, top commands)
        └─miss─→ zoe-core (Pi / Gemma 4 E2B)
                  ├─ soul.ts          — Zoe's persona (system prompt)
                  ├─ memory.ts        — per-turn cited memory packet (before_agent_start)
                  ├─ abilities.ts     — capability registry: auto-discovers abilities/*.ts,
                  │                     permission-gated, progressively disclosed
                  │     └─ calendar / lists / reminders / timers / notes / journal /
                  │        people / media / home / info  (→ zoe-data via internal dispatch)
                  └─ provider-local-gemma.ts — points Pi at the host model server
   Hermes / OpenClaw = tools the brain delegates to. Multica = governed control plane.
```

Key properties:
- **One Pi session per user-conversation.** The acting user is resolved **per turn**
  from `ZOE_CORE_USER_ID` (set by zoe-data per session) — never baked at module
  load, no default identity. User-scoped tools/memory **fail closed** if the user
  is unknown; read-only tools (time/weather) still work.
- **Capabilities reuse the existing backend.** Domain tools map an `action` enum to
  an existing intent and call `POST /api/system/intent-dispatch` (internal-token,
  allowlisted), which runs `intent_router.execute_intent` — the same fulfillment
  the live chat uses. No reimplementation.
- **Progressive disclosure.** Only the always-on core + relevance-matched tools are
  active per turn, so a ~2B model isn't drowned in 56 tools (keyword/example now;
  vector Tool-RAG is the documented upgrade).
- **Permission envelope** (`read-only / user-data:read|write / fs:write / network /
  browser / home-device:action / code:mutate / credential:access`) validated before
  every tool execution.

## Tool decisions

- **Gemma 4** is the local model family; **E2B** is the production baseline until
  benchmarks justify E4B/12B/speculative pairs. The provider auto-discovers models,
  so a model swap is a config change.
- **MemPalace** stays primary fast recall. **Hindsight** (experience/reflection,
  Postgres-native) and **Graphiti** (temporal relational truth) are *measured
  bake-offs*, started as deferred sidecars (FalkorDB-Lite, never Neo4j on the
  Jetson). **Graphify** is code/system understanding. **Multica** is the approval/
  governance plane.
- **`pi` is adopted as the core brain** (this build), and as the engineering/
  self-evolution runtime later (CASE-style harness kernel).

## Memory architecture

Layered, not one database. Live today: MemPalace facts + portrait + a per-turn
**cited packet** (`GET /api/memories/for-prompt`) injected into the brain. The
packet policy: compact (≤~12 items), every line cites an evidence id, superseded/
archived dropped, disputed surfaced as `(uncertain)`, user corrections take
precedence. **Memory-v2** (designed, not yet built): bi-temporal supersession
columns, recency+importance+relevance scoring, and a nightly **L1 behavioral-memory**
extraction (the "remembers like Samantha" layer). Source: *Second Me* (L0/L1/L2),
Generative Agents (scoring/reflection), Zep/Graphiti (bi-temporal), Hindsight.

## Self-evolution loop (harness kernel — future)

Notice → Explain → Search → Evaluate → Propose → Approve → Execute → Verify →
Learn → Retire, governed by Multica. From CASE: an event-sourced run ledger, a
deterministic outcome→action gate, **evidence that is "unable to lie"** (re-run +
hashed, not asserted), and retrospectives that propose *staged amendments* — never
auto-rewrite prompts/tools.

## Safety rules

- No unscoped/unknown-user writes (fail closed).
- Reads free; writes/device/credential/code gated (per-action approval at cutover).
- Auto-recall allowed; auto-retain gated by evidence/admission.
- No production cutover before lab proof (Samantha tests + benchmarks).

## Success metrics — the "Samantha tests"

Continuity, emotional recall, user identity, safe autonomy, ambient awareness,
voice latency, graceful self-improvement. Plus: chat-latency regression < 10%,
compact cited memory packets, zero unscoped durable writes, every self-evolution
change backed by proposal + evidence + verification + rollback.

## Rollout status (2026-06-17)

| Phase | State |
|---|---|
| Brick 1 — Pi on local Gemma (`provider-local-gemma.ts`) | ✅ merged |
| Brick 2 — Zoe's soul (`soul.ts` + `SOUL.md`) | ✅ merged |
| Brick 3 — memory packet injection (`memory.ts` + `/for-prompt`) | ✅ merged |
| Brick 4a — capability registry (`abilities.ts`, per-turn multi-user) | ✅ merged |
| Brick 4b — 9 domain tools + internal dispatch | 🔄 in review |
| `memory.ts` per-turn multi-user fix | 🔄 in review |
| Samantha acceptance suite (identity/memory/action/continuity/latency e2e) | authored, runs at completion |
| Benchmark gate (Pi-brain vs `zoe_agent`) | next |
| **Cutover** (chat/voice → zoe-core; retire `zoe_agent`) | **gated** on the two above; benchmark-gated, owner-approved |
| Memory-v2 / harness kernel | designed, future |

Everything to date is **additive and lab-only**: `zoe_agent` remains the
production brain. zoe-core is built and proven *beside* it; the cutover is a
deliberate, benchmark-gated decision, not an automatic step.
