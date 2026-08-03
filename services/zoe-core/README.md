# zoe-core

**Zoe's reasoning/orchestration core — the Pi agent (Gemma 4) that binds the
other services together and calls Zoe's abilities.**

This is the *center* of Zoe: the brain. The other services are leaf functions —
`zoe-data` stores/serves data, `zoe-auth` authenticates, `zoe-database` is the
database. `zoe-core` is the thing that reasons, decides, and orchestrates them.

Built on **[`pi`](https://www.npmjs.com/package/@earendil-works/pi-coding-agent)**
(the extensible agent framework, run on local **Gemma 4 E4B-QAT**). Zoe's
capabilities are Pi **extensions/tools**; her personality and memory are wired in
via Pi's extension hooks.

> **Core ≠ monolith.** zoe-core *orchestrates and delegates*; it does not absorb
> the code of `zoe-data`/`zoe-auth`/`zoe-database`. Abilities stay modular
> (extensions/tools). The retired Docker monolith that once held this name was
> removed from the working tree and remains in git history only — do not revive it.

> **Status: wired default `core` brain fallback.** zoe-core (Pi on Gemma 4 E4B-QAT)
> is the code default in `services/zoe-data/brain_dispatch.py` (`pi --mode rpc` via
> `zoe_core_client.py`) — the fallback lane below the live `flue` sidecar
> (`labs/flue-zoe-brain`, reached via `ZOE_BRAIN_BACKEND=flue`, live on this
> deployment since 2026-07-03). `zoe_agent.py` is the *legacy* last-resort fallback,
> not the brain. Dispatch priority: **flue > core > legacy**. Extend zoe-core; do
> not retire it. Only the OLD Docker monolith that once held this name is retired
> (git history only).

## Architecture (target)

```
                 ┌───────────────── zoe-core (Pi / Gemma 4) ─────────────────┐
   user ──▶ intent fast-path ─miss─▶  the brain: reason + call tools         │
   (speed cache, top commands)       ├─ native tools  → zoe-data endpoints   │
                                     ├─ delegation     → Hermes / OpenClaw    │
                                     ├─ memory packet  → layered memory       │
                                     └─ soul/personality                       │
                 └───────────────────────────────────────────────────────────┘
   zoe-core registers as an agent in Multica (peer to Hermes / OpenClaw).
   Omnigent orchestrates above; not called from inside the brain.
```

## Build bricks

1. **Provider** — Pi runs on local Gemma. ✅ done (`extensions/provider-local-gemma.ts`)
2. **Soul** — Zoe's persona replaces Pi's default coding-assistant prompt. ✅ done (`SOUL.md` + `extensions/soul.ts`)
3. **Memory** — MemPalace packet injected per turn via `before_agent_start`, fetched from zoe-data's internal `/api/memories/for-prompt` (compact, cited, fail-open). ✅ done (`extensions/memory.ts`). Hindsight/Graphiti compose into the same packet later. The packet rides in the conversation TAIL, not the system prompt — see the KV-prefix contract below.
4. **Abilities** — native zoe-data tools + delegation tools (Hermes/OpenClaw); safety rails as `tool_call` gates.
5. **Cutover (benchmark-gated)** — only after the Samantha tests + Pi-vs-`zoe_agent` benchmarks pass: point chat/voice at the zoe-core brain, intent fast-path in front, retire `zoe_agent.py`. Until then, lab-only; `zoe_agent` stays production.

## Brick 1 — local Gemma provider

`extensions/provider-local-gemma.ts` registers a Pi provider `local-gemma`
pointing at the host model server (`GEMMA_SERVER_URL`, default
`http://127.0.0.1:11434/v1`, OpenAI-compatible). `package.json` declares the Pi
dependency and the extension manifest; `tsconfig.json` type-checks the extension
(Pi loads `.ts` directly via jiti — no build step).

Smoke test (integration; skips if `pi` or the model server are unavailable):

```bash
python -m pytest services/zoe-core/test/test_brick1_provider.py -v
```

## KV-prefix contract

llama.cpp reuses cached KV only for an **exact common prefix** of the tokenized
request — `--cache-reuse` is off for Gemma's shared-KV + SWA attention, where KV
shifting is unsupported. So every byte that varies turn-to-turn re-prefills
everything after it, and the two things at the FRONT of every request are frozen
by construction:

- **System prompt** (`extensions/soul.ts` → `extensions/memory.ts`) carries only
  text whose inputs are per-PROCESS constants: SOUL.md, and the static
  `MEMORY_USAGE_DIRECTIVE` (gated on `ZOE_CORE_USER_ID`, which zoe-data bakes into
  each worker's spawn env). The **volatile** memory packet is returned as a Pi
  `custom` message instead, which Pi appends *after* the user message and converts
  to a `user` turn on the wire — the model reads the same bytes, just later.
- **Tool block** (`extensions/abilities.ts`) is disclosed monotonely: a domain
  stays active for `ZOE_CORE_DISCLOSURE_WINDOW_TURNS` turns (default 6 — zoe-data
  replays `history[-12:]`) after it was last relevant, instead of being recomputed
  from the last message alone. `setActiveTools` is still called on *every* turn:
  that call is the unconditional strip of Pi's coding builtins.

The `/api/memories/for-prompt` fetch lives **only** in `extensions/memory.ts` for
this lane — `zoe_core_client._compose_message` deliberately does not add it (see
`ZOE_CHAT_INJECT_DB_MEMORY` in `routers/chat.py`).

Deterministic suite (no model, no `node_modules`; needs Node ≥ 22.18 for built-in
TypeScript type stripping):

```bash
npm --prefix services/zoe-core test
# or: node --test services/zoe-core/test/prefix_stability.test.ts
```

`services/zoe-core/` is in no GitHub workflow; the suite reaches CI through
`services/zoe-data/tests/test_zoe_core_prefix_stability.py`, which runs on the
Jetson's full-directory lane.
