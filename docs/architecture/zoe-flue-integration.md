# Zoe ⊕ Flue — the merge, on a shared Pi core

> **North star: one well-organised machine, not a frankenstein.** This plan is
> backed by a four-thread deep dive — Pi internals, Zoe's *real* current
> architecture, Flue's intended integration shape, and industry patterns — which
> all converge on the same design. Honors the rocks in [`../CANONICAL.md`](../CANONICAL.md).
> Supersedes [`zoe-harness-on-flue.md`](zoe-harness-on-flue.md) (now one phase).

---

## 0. The key discovery: it's already one engine

The merge is far cleaner than "glue two foreign runtimes," because Zoe's brain and
Flue are **the same engine consumed at two layers**:

- **Pi** (`@earendil-works/pi-*`, MIT, ~v0.80) is a three-layer stack: `pi-ai`
  (providers) → `pi-agent-core` (the reusable `Agent` loop) → `pi-coding-agent`
  (CLI + extensions + tools).
- **Zoe's brain is Pi.** `services/zoe-core` is `@earendil-works/pi-coding-agent` —
  the **default production brain** (`brain_dispatch.py`, `ZOE_USE_CORE_BRAIN=true`;
  the "lab-only" note in `zoe-core/README.md` is stale). Python `zoe-data` runs it
  **out-of-process**, spawning `pi --mode rpc` per `(user, session)` via
  `zoe_core_client.py`, with `soul` / `memory` / `abilities` / `local-gemma`
  extensions.
- **Flue is Pi too.** `packages/runtime/src/session.ts` imports the same `Agent`
  from `pi-agent-core` and drives it **in-process**.
- Both float `^0.79` → resolve to the **same 0.80.x**. Same family, same version,
  identical event/message vocabulary — **not divergent forks.**

So "merge" = **collapse a process boundary onto the shared Pi core**: move Zoe's
brain from *"Python spawns the `pi` CLI every turn"* to *"Flue hosts the durable Pi
`Agent`,"* keeping the model and behaviour. That's convergence, not a rewrite.

---

## 1. Principles (the anti-frankenstein rules)

From the patterns research (Anthropic *Building Effective Agents*, HumanLayer
*12-Factor Agents*, Vellum):

- **One source of truth per concern** — routing/session → Python core; durable
  workflow/run state → Flue; model weights → llama-server. No concern owned twice.
- **Two thin, explicit, language-neutral seams** — not one fused app, not many
  chatty cross-language calls.
- **The agent is a stateless reducer** (`f(context) → next action`); it owns
  workflow state only and never duplicates session state (12-factor Factor 5).
- **Keep the fast path in front; escalation is an explicit branch**, not the default.
- **Graceful degradation** — if Flue is down, the deterministic path still answers.
  *Flue down ⇒ "agentic/channels down," never "Zoe can't answer."*
- **Integrate, don't rewrite.** Python and Node stay separate processes that can
  ship independently (avoid the distributed monolith).

---

## 2. Who owns what

| **zoe-data** (Python) — front door + fast path + system-of-record | **Flue** (Node sidecar) — the agentic layer, on Pi | **llama-server** — shared model |
|---|---|---|
| All user channels (voice, touch, web, LiveKit); Moonshine STT; Kokoro TTS | Hosts the **Pi `Agent`** = the conversational brain (durable, in-process) | Gemma E4B-QAT (+MTP draft) on `:11434`, OpenAI-compatible |
| Tier-0/1/1.5 deterministic core (`intent_router` → `semantic_router` → `expert_dispatch` → `fast_tiers`) | The autonomous **engineering harness** + durable agentic workflows | The "rock" — behind **one swappable URL seam** |
| Session state, MemPalace memory, auth, DB — **the SoR** | MCP/tool host; observability; the live "what Zoe is doing" event stream | Shared sidecar; both paths point at it by URL |

zoe-data's fast path is **untouched** — that's how zero-regression is guaranteed.

---

## 3. The seams (concrete, from the code — not invented)

**Seam A — brain escalation (the one coarse handoff).**
`fast_tiers.resolve()` returns a finished answer **or `None`**; on `None` the turn
escalates to the brain via `run_zoe_core(message, session_id, user_id) → stream of
text deltas + __TOOL__/__THINKING__ sentinels` (`chat.py`, env-gated by
`_USE_ZOE_CORE`). That contract already has **two implementations** (Pi subprocess +
legacy). **The Flue-hosted brain is a third implementation in the same seam** —
`zoe-data` calls `POST /agents/zoe/:session?wait=result` (or streams) and maps the
result through its existing AG-UI sentinel handlers. Voice/fast-path never route
through Flue → graceful degradation to the local brain lane if Flue is down.

**Seam M — the model (provider).**
Flue's Pi `Agent` reaches Gemma the blessed first-party way (the shipped
`hello-world` example does this for ollama/lmstudio):
`registerProvider('zoe', { api: 'openai-completions', baseUrl: 'http://127.0.0.1:11434/v1' })`,
then `model: 'zoe/gemma-…'`. **The Gemma rock is untouched** — same server both Zoe's
current Pi extension and Flue point at.

**Seam B — tools & memory (MCP / typed tools).**
Zoe's capabilities (memory recall, calendar, lists, abilities) are exposed to the
Flue brain as **tools**: start with `defineTool` HTTP wrappers over zoe-data's
existing `/api/*` (lowest friction; this replaces today's Pi-extension HTTP
callbacks), and promote to **MCP** (`connectMcpServer`, local **stdio**) as the
surface matures. **MemPalace stays the system-of-record** — Flue's `db.ts`
(`sqlite('./data/flue.db')`) holds *only* conversation/run durability, never
business data (per Flue's own docs).

**Seam C — channels (into the front door, not a parallel brain).**
User channels — including **Telegram** — enter **zoe-data's front door** (`/api/chat`
with a `channel` tag → `fast_tiers` selects the channel profile), go through the
fast path, and escalate to the Flue brain only when needed — exactly like voice and
touch. A channel is *another doorway into the one core*, never a separate brain.

---

## 4. Zero-regression guarantees (proven, not assumed)

- **Voice fast-path is independent of Flue** — the rock. Never in the
  STT→router→brain→TTS latency path.
- **Latency gate:** the [`#735` probe](../../scripts/maintenance/zoe_latency_probe.py)
  must show no voice regression for any shared-resource change (harness already
  passed at **+1.5 ms**).
- **Graceful degradation:** Flue/brain-sidecar down ⇒ fall back to the deterministic
  path (and the legacy local brain lane) — Zoe still answers.
- **Lab-prove before prod (Samantha bar):** the Flue brain must reach parity with the
  current Pi-CLI brain on the voice-sample corpus before it serves real users;
  demo-users-only until then.
- **Pin Pi in lockstep:** both sides float `^0.79`; choose one **exact**
  `pi-agent-core`/`pi-ai` version for Zoe *and* Flue so the shared types can't drift.
- **Memory budget:** the Flue Node sidecar (~300 MB) is sized + monitored on the
  16 GB Orin.

---

## 5. What "move the brain to Flue" actually means (the port)

The `Agent` loop is the *same Pi*; what changes is the **extension surface**. Zoe's
four `pi-coding-agent` extensions are re-expressed against Flue's `Agent`/tool model:

| zoe-core extension (`pi-coding-agent`) | Becomes in Flue |
|---|---|
| `soul.ts` (persona) | Agent `instructions` |
| `provider-local-gemma.ts` | `registerProvider('zoe', …:11434)` (Seam M) |
| `memory.ts` (`/api/memories/for-prompt`) | a tool / MCP call (Seam B) |
| `abilities.ts` (zoe-data `/api/*`) | typed tools (Seam B) |

This is a **real port, not a config flip** — but because both already speak the same
Pi vocabulary at the same version, it's convergence on one engine. Bonus: it removes
the per-turn subprocess spawn, the LRU worker pool, the RPC-stream parsing, and the
per-process multi-user workaround in `zoe_core_client.py`.

---

## 6. Migration order (phased, additive, each gated by §4)

- **Phase 0 — Harness on Flue. ✅ DONE.** scout→implement→verify→openPR proven;
  +1.5 ms voice (PR #858).
- **Phase 1 — Telegram as a front-door channel.** Transport (long-poll) → zoe-data
  `/api/chat` with `channel="telegram"` → `fast_tiers`. Retire Hermes Telegram.
  *(The running Flue Telegram bot is re-slotted to this — feed the front door with
  the channel tag, not a separate brain.)*
- **Phase 2 — Flue brain behind the `run_zoe_core` seam.** Stand up the Flue sidecar
  as a *third* brain implementation: `registerProvider` Gemma, port `soul` →
  instructions, emit the text-delta/sentinel contract. **Lab-prove parity** vs the
  Pi-CLI brain on the voice corpus + #735; demo-users only.
- **Phase 3 — Tools & memory (Seam B).** Give the Flue brain Zoe's abilities/memory
  via `defineTool`/MCP, so it has full parity with the extension brain.
- **Phase 4 — Cut over + retire.** Flip `_USE_ZOE_CORE` → Flue for real users once
  proven; retire the per-turn `pi --mode rpc` path, then the Hermes/Multica/OpenClaw
  engineering loops (the harness already lives on Flue).
- **Phase 5 — Thin & enrich.** zoe-data settles into front-door + fast-path + data/IO;
  more channels + durable workflows on Flue; wire the live "what Zoe is doing" UI
  stream (`@flue/sdk`/`@flue/react`) into touch/chat.

---

## 7. What we explicitly do NOT do

- Don't embed Flue in Python — it's a **Node sidecar** (own systemd unit), peer over
  HTTP/SDK.
- Don't route voice through Flue; don't make the brain a hard dependency of a turn.
- Don't build a parallel brain — channels feed the **one** front door/core.
- Don't duplicate state — Flue is a stateless reducer; **zoe-data is the SoR**.
- Don't big-bang; **retire by removing** (git keeps history); **pin Pi**.

---

## 8. Research basis

Four parallel deep-dive threads (2026-06-28), each evidence-backed:
1. **Pi** — confirmed Pi is the shared substrate (Zoe = `pi-coding-agent` CLI/RPC;
   Flue = `pi-agent-core` in-process; same 0.80.x). Recommends converging on Pi.
2. **Zoe's real architecture** — the fast path is Python in-process; the only
   out-of-process hop is the brain lane (`run_zoe_core` → `pi` CLI → llama-server);
   identified the env-gated brain seam.
3. **Flue integration** — Flue is designed to be *added alongside* (Node sidecar,
   never embedded), brain via `registerProvider`, tools via `defineTool`/MCP, `db.ts`
   = runtime state only; SoR stays external.
4. **Industry patterns** — front-door gateway + one HTTP escalation + MCP tool bus +
   shared model sidecar; one-owner-per-concern, stateless-reducer agent, graceful
   degradation. Sources: Anthropic *Building Effective Agents*, HumanLayer
   *12-Factor Agents*, Vellum, the MCP spec, llama.cpp server docs.

---

## 9. Status & next action

- **Now:** Phase 0 done (#858); a Flue Telegram bot runs as an `/api/chat` bridge
  (proof) — to be re-slotted as the Phase-1 front-door channel.
- **Next:** Phase 1 — add the `channel` tag through the front door so Telegram is
  "another channel into the single core," then begin Phase 2 (Flue brain behind the
  `run_zoe_core` seam, lab-proved before any real-user cutover).
