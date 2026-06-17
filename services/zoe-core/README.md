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

1. **Provider** — Pi runs on local Gemma. *(this brick — `extensions/provider-local-gemma.ts`)*
2. **Soul + memory** — Zoe personality + layered memory packet injected per turn.
3. **Abilities** — native zoe-data tools + delegation tools (Hermes/OpenClaw); safety rails as `tool_call` gates.
4. **Cutover** — chat/voice point at the zoe-core brain; intent fast-path stays in front; retire `zoe_agent.py`.

## Brick 1 — local Gemma provider

`extensions/provider-local-gemma.ts` registers a Pi provider `local-gemma`
pointing at the host model server (`GEMMA_SERVER_URL`, default
`http://127.0.0.1:11434/v1`, OpenAI-compatible). Smoke test:

```bash
./test/brick1_smoke.sh
```
