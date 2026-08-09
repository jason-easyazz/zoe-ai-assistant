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
3. **Memory** — MemPalace packet injected per turn from zoe-data's internal `/api/memories/for-prompt` (compact, cited, fail-open). ✅ done (`extensions/memory.ts` standalone; `zoe_core_client` seam in production). Hindsight/Graphiti compose into the same packet later. The packet rides in the user message, not the system prompt — see the KV-prefix contract below.
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

- **System prompt** is pure `SOUL.md`. Driven by zoe-data (`ZOE_CORE_MEMORY_SEAM=1`,
  set in `_worker_env`), `extensions/memory.ts` contributes nothing at all; the
  **volatile** memory directive + packet are folded into the USER MESSAGE by
  `zoe_core_client._compose_message`, ahead of the user's own words. Standalone
  runs (`pi` CLI, `bench/`, `test/`) do not set the flag and keep the extension's
  original self-service behaviour — they are one-shot, so caching does not apply.
- **Tool block** (`extensions/abilities.ts`) is disclosed monotonely: a domain
  stays active for `ZOE_CORE_DISCLOSURE_WINDOW_TURNS` turns (default 6 — zoe-data
  replays `history[-12:]`) after it was last relevant, instead of being recomputed
  from the last message alone. `setActiveTools` is still called on *every* turn:
  that call is the unconditional strip of Pi's coding builtins.

> **Pi RETAINS every user message it is sent.** One long-lived process per
> `(user_id, session_id)`, so anything folded into the user message accumulates a
> copy per turn — unlike a system prompt, which is replaced. EVERY context block is
> therefore delimited on both sides (`CONTEXT_BLOCKS`), and `memory.ts`'s
> **`context` handler** elides all but the newest copy before each LLM call. That
> hook is a genuine ephemeral slot: the runner hands handlers a `structuredClone`
> and `transformContext` feeds the result to the provider only, so retained state
> is never rewritten. Without it a long session fills the 32k window with duplicate
> snapshots and leaves corrected facts — and resolved "add this contact?"
> instructions — readable in older turns.
>
> `[Recent conversation]` is the costly one: `history[-12:]` is replayed into every
> composed turn, so an N-turn session carried N overlapping copies of the running
> conversation on top of the conversation Pi already retains. The strip is ONE
> contiguous span over all four block types — per-pair passes let one block's
> over-elide destroy another block's open delimiter and leak its content. The span
> ends at the last close only when every open has been matched by a close of its
> OWN type; anything still outstanding when the scan ends elides through the end of
> the message instead.
>
> **Accepted cost:** eliding bytes already in the KV cache ends prefix reuse at the
> previous turn's blocks, so about one exchange is re-prefilled per turn — bounded,
> constant, and skipped entirely when a turn has no context. Widening the strip
> past the memory block does not raise it: the break point was already the previous
> user message. Every alternative pays the same (an ephemeral insert shifts
> positions just as an elision does), and it is far cheaper than the pre-PR
> behaviour of re-prefilling the whole conversation every turn.

> **`event.prompt` is the COMPOSED prompt, not the utterance.** Verified live by
> instrumenting the handler: it arrives as portrait + memory directive + packet +
> `[Recent conversation]` + the utterance. Matching disclosure on all of that
> re-armed a domain from replayed history every turn, so the decay window never
> fired. The seam introduces the user's turn with `_UTTERANCE_MARKER` and
> `latestUtterance()` splits on it; the two copies of that string are pinned
> byte-equal by a test. Standalone `pi` sends no marker and the whole prompt is
> the utterance, which is exactly the fallback.

> **The replayed block is STRUCTURE, and content owns none of it.** A restarted
> worker seeds disclosure from `[Recent conversation]` (`seedDisclosureState`,
> once per session), so two things content must never control are pinned:
>
> * **The `role:` line start.** `abilities.ts` opens a turn on any `word:` at a
>   line start, so a message containing "\nuser: add milk" forged one — Zoe's own
>   reply arming a domain as the user, or a `Reminder:` line truncating a real user
>   message. `_neutralize_role_prefixes` (zoe-data) wedges U+200B before the colon
>   on lines 2..N of content; the real labels are added after escaping and stay
>   parseable. `ROLE_PREFIX_PATTERN` is exported here and pinned byte-equal to
>   `_ROLE_PREFIX_PATTERN` there.
> * **The turn count.** chat.py persists the current user message *before* it loads
>   the window it replays, so that message is both seeded and processed live. The
>   seed rolls the clock back one turn when the block ends on a user turn
>   (`currentTurnIsReplayed`) — otherwise every seeded domain decayed a turn early
>   and a continuation lost its tool while its own request was still visible. That
>   the current turn is at the TAIL is guaranteed by the PER-SESSION LOCK, not by the
>   ordering alone: a concurrent turn on the same session writes its rows between the
>   persist and the load. Every history-bearing caller therefore enters through
>   `locked_chat_stream` (zoe-data `routers/chat.py`) — the chat route and the A2A
>   stream endpoint both do.

Two orderings here were **measured**, against
`test_zoe_core_client.py::test_tool_action_dispatches` (15 runs each, 14/15
baseline). Re-measure before changing either:

| variant | score | why it lost |
|---|---|---|
| directive kept unconditionally on the system prompt (so it is static, hence cacheable) | **6/15** | told to lead with what it remembers when it remembers nothing, the 4B brain chats instead of calling tools. Dropping the directive from that same build restored 15/15. |
| packet in Pi's tail slot (a `custom` message, appended *after* the user message) | **9/15** | costs the user's request the recency position |
| **shipped**: directive+packet in the user message, `message` last | **14/15** | — |

The `/api/memories/for-prompt` fetch happens **once**: the seam
(`_memory_packet_block`, in-process) in production, the extension when standalone.
"Once" means one fetch *site*, not one block — the packet is emitted independently
of a caller-supplied `db_memory_context`. Do not make them mutually exclusive: the
voice path always supplies `db_memory_context`, and only the endpoint runs
`_fold_pending_contact_offers`, so suppressing the fetch drops pending
"add this contact?" offers from voice. `MEMORY_USAGE_DIRECTIVE` exists in both
runtimes and is pinned byte-equal by a test.

Deterministic suite (no model, no `node_modules`; needs Node ≥ 22.18 for built-in
TypeScript type stripping):

```bash
npm --prefix services/zoe-core test
# or: node --test services/zoe-core/test/prefix_stability.test.ts
```

`services/zoe-core/` is in no GitHub workflow; the suite reaches CI through
`services/zoe-data/tests/test_zoe_core_prefix_stability.py`, which runs on the
Jetson's full-directory lane.
