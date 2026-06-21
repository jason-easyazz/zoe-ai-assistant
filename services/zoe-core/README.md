# zoe-core

**Zoe's reasoning/orchestration core — the Pi agent (Gemma 4) that binds the
other services together and calls Zoe's abilities.**

This is the *center* of Zoe: the brain. The other services are leaf functions —
`zoe-data` stores/serves data, `zoe-auth` authenticates, `zoe-database` is the
database. `zoe-core` is the thing that reasons, decides, and orchestrates them.

Built on **[`pi`](https://www.npmjs.com/package/@earendil-works/pi-coding-agent)**
(the extensible agent framework, run on local **Gemma 4 E2B**). Zoe's
capabilities are Pi **extensions/tools**; her personality and memory are wired in
via Pi's extension hooks.

> **Core ≠ monolith.** zoe-core *orchestrates and delegates*; it does not absorb
> the code of `zoe-data`/`zoe-auth`/`zoe-database`. Abilities stay modular
> (extensions/tools). The retired Docker monolith that once held this name is
> archived at `docs/archive/retired-services/zoe-core/` — do not revive it.

> **Status: lab-only / additive.** zoe-core is the destination brain (Pi on
> Gemma 4 E2B), built and proven *beside* the live system. `zoe_agent` remains
> the production chat brain until zoe-core clears the Samantha tests +
> Pi-vs-`zoe_agent` benchmarks — then cutover. Nothing here is wired into
> production yet.

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
3. **Memory** — MemPalace packet injected per turn via `before_agent_start`, fetched from zoe-data's internal `/api/memories/for-prompt` (compact, cited, fail-open). ✅ done (`extensions/memory.ts`). Hindsight/Graphiti compose into the same packet later.
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
