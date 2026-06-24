---
type: proposal
title: "zoe-core (Pi brain) per-turn latency — root cause + measured breakdown"
status: draft
owner: jason
created: 2026-06-25
scope: services/zoe-core, services/zoe-data/zoe_core_client.py, services/zoe-data/fast_tiers.py
verified_against:
  - live llama-server (Gemma 4 E4B-QAT + MTP, --ctx-size 8192 --parallel 1 --cache-reuse), measured 2026-06-25
  - Pi agent-loop source @earendil-works/pi-agent-core dist/agent-loop.js (installed global pi 0.79.3)
  - scripts/perf/measure_speed.py + measure_voice.py replay over ~/.zoe-voice-samples (10-sample corpus)
do_not_change:
  - models (Gemma 4 E4B-QAT brain + MTP drafter, Moonshine v2 STT) are rocks — optimise around, never swap
  - this is a PROPOSE-ONLY record; it lands no code until Jason reviews
related:
  - ./brain-speed-tuning.md       # llama.cpp/MTP tuning — targets the LEGACY zoe_agent prompt cliff
  - ./brain-prompt-tools-audit.md # prefill trim — also LEGACY zoe_agent path
---

# zoe-core turn latency — root cause + measured breakdown

> **PROPOSE-ONLY.** No change to the agent core lands from this doc until reviewed.
> The rocks (Gemma 4 E4B-QAT + MTP brain, Moonshine v2 STT) are untouched.
>
> **Scope note:** the existing `brain-speed-tuning.md` / `brain-prompt-tools-audit.md`
> docs target the **legacy `zoe_agent.py`** brain (3,773-tok prompt + 27 tool schemas
> that overran a 4096 ctx-window — the prefill cliff). That brain is no longer the
> live path. This doc characterises the **live `zoe-core` (Pi) brain** that every voice
> turn now routes through (`brain_dispatch.brain_streaming → zoe_core_client.run_zoe_core_streaming`),
> whose latency profile is materially different.

## TL;DR

The brain is **not** the slow part. Isolated llama-server TTFT is **65–800 ms** and
decode is **~21–24 tok/s** — both healthy for E4B-QAT on Orin NX. The ~5 s a user feels
on a voice turn decomposes into three structural costs, none of which is a prefill cliff
(zoe-core's prompts are small and fit the 8192 ctx-window comfortably):

1. **Decode-bound reply** — replies run 40–60 tokens at the rock's fixed ~21 tok/s ≈
   **2–3 s of pure decode**. Inherent to the model; only shorter replies cut it.
2. **The 2-LLM-call tool pattern** — a tool turn (weather, calendar, …) makes **two**
   sequential Gemma calls: call #1 emits a tool call (no spoken text), the tool runs,
   call #2 synthesises the answer. The user hears **nothing for ~5 s**. This is
   **correct and required** (you need the live data to answer) — not a redundancy to cut.
3. **~1–1.2 s fixed per-call overhead** — Node/OpenAI-SDK setup + RPC + small-prompt
   prefill before the first byte of each LLM call.

The biggest *felt* problem is the **silent gap on tool turns** (#2) and the fact that
**voice turns that could be answered deterministically in ~300 ms instead pay the full
brain loop** because the voice channel runs with Tier-0 read shortcuts disabled.

## Measured breakdown

### Brain in isolation (`measure_speed.py`, warm, 544-tok prompt)

| metric | value |
|---|---|
| TTFT (warm, prefix-cached) | **65 ms** |
| TTFT (cold, cache-busted, 544 tok) | **184 ms** |
| TTFT (soul prompt, first call) | **340–800 ms** |
| decode | **~21 tok/s** (≈ 3 s for 64 tok) |

Prefill scaling shows a cliff **above ~4 k tokens** (544 tok → 65 ms, 2 k → 79 ms,
**4 k → 10.2 s, 6 k → 14 s**). zoe-core's real prompts are far below this:

### Actual prompt sizes zoe-core sends (captured via logging proxy)

| turn | system chars | msgs | tools | est. tokens |
|---|---:|---:|---:|---:|
| chat ("Where is Geraldton?") | 1,934 | 2 | 0 | **~525** |
| tool call #1 ("weather…") | 1,862 | 2 | 2 | **~777** |
| tool synthesis #2 | 1,862 | 4 | 2 | **~871** |

So zoe-core **never approaches the prefill cliff** on normal turns — progressive
disclosure (`abilities.ts` `setActiveTools`) keeps the tool-schema block tiny and the
soul is only ~1.2 KB. The legacy-brain prefill-trim work does **not** apply here.

### Voice end-to-end (`measure_voice.py`, 10-sample corpus, all OK)

| metric | median | p90 | max |
|---|---:|---:|---:|
| stt_ms | 623 | 887 | 1,827 |
| resolve_ms | 0 | 8 | 972 |
| **brain_ms** | **3,293** | **7,879** | **8,483** |
| e2e_ms | 3,446 | 8,522 | 8,780 |

Bimodal: 3/10 turns short-circuited to a fast tier (**e2e 305–450 ms**, brain_ms=0);
the 7 brain turns ran 2.5–8.5 s. The heavy tail is the 2-call tool turns + long replies.

### Per-turn LLM-call structure (from `agent-loop.js`, confirmed live)

`runLoop` (`@earendil-works/pi-agent-core/dist/agent-loop.js`):

```
while (hasMoreToolCalls || pending) {
  message = await streamAssistantResponse(...)   // ← LLM CALL
  toolCalls = message.content.filter(toolCall)
  if (toolCalls.length) { executeToolCalls(...); hasMoreToolCalls = true }  // loop again → 2nd LLM CALL
}
```

- **no-tool turn** = **1** LLM call.
- **tool turn** = **2** LLM calls (decide-tool → execute → synthesise). Verified live:
  weather/calendar turns emit `assistant message_start` twice with one `toolcall_start`
  between them. This is intrinsic; removing it would break function.

## Root cause, ranked

1. **Decode time at the rock's fixed throughput.** ~21 tok/s × a 2–3-sentence reply =
   2–3 s. Unavoidable without changing the model (forbidden) or shortening replies.
2. **2 sequential LLM calls on tool turns**, with the user hearing nothing during call #1
   + tool exec + call #2 prefill (~5 s of silence). Correct, but the *silence* is the UX
   problem, not the calls themselves.
3. **Voice defers everything to the brain.** `fast_tiers.CHANNEL_PROFILES["voice"]` sets
   `run_tier0=False`, so deterministic idempotent reads (weather/calendar_show/time_query/
   list_show/reminder_list/timer_status/date_query) that the intent router *correctly*
   classifies fall through to the full 2-call brain loop instead of a ~300 ms Tier-0 answer.
4. **~1 s fixed Node/SDK/RPC overhead per LLM call.** Small but real; partly intrinsic to
   spawning the request through the OpenAI client each turn.

What is **already optimised** (do not redo): worker is warm + LRU-bounded and **prewarmed
on wake** (`zoe_core_client.prewarm`, called from `voice_tts._prewarm_brain_for_panel` and
LiveKit), facts cache is warmed concurrently in the speech window, deltas stream
token-by-token, and `--ctx-size` is already 8192 with `--cache-reuse` + MTP.

## Proposed changes (each independently shippable, replay-gated)

### P1 — Enable Tier-0 read shortcuts for voice on unambiguous idempotent reads (highest value)
- **Change:** flip `CHANNEL_PROFILES["voice"]["run_tier0"]` to `True` (keep
  `defer_domains={people, memory}` and the margin check). The shared Tier-0 path only
  short-circuits the 7 idempotent read intents whose `execute_intent` returns finished text.
- **Effect (measured eligibility on corpus):** weather/calendar_show/time_query/list_show
  drop from 2.5–8.5 s → ~300 ms; conversational follow-ups ("about this week",
  "I was asking about…") correctly return `intent=None` and still go to the brain.
- **Risk:** medium. A deterministic Tier-0 answer may be *toned* differently than the
  brain's contextual reply (e.g. mid-conversation about an empty calendar). The voice
  channel deliberately deferred these once; this must be **replay-gated over a larger
  corpus** with said-vs-did diffing before it lands, and Jason should review the tone of
  the Tier-0 replies for the read intents.
- **Interaction with the upstream voice gate (must verify before implementing):**
  `run_tier0=False` for voice is deliberate because the voice channel runs **its own
  richer public-intent Tier-0 *before* `fast_tiers.resolve()`** — `voice_tts.py`
  (~L3893–3994) does the public-intent detection plus a **B3/B4 scope gate + PIN
  challenge** (`_can_use_voice_intent`, `resource="scope_gate"`). That upstream gate, not
  the shared `fast_tiers` Tier-0, is the real policy/scope guard. Confirmed present as of
  this doc (HEAD 687fd21). An implementer of P1 must re-verify that upstream gate is still
  intact and decide the ordering: either (a) wire the deterministic read answer into the
  *existing* upstream voice Tier-0 (preferred — keeps the scope gate authoritative), or
  (b) flip `run_tier0=True` only for the idempotent read intents *and* confirm the
  upstream gate still runs first so a scoped/unsafe read can't bypass it. Do **not** flip
  the flag blind on the assumption that `fast_tiers` Tier-0 carries the scope check — it
  does not.
- **Test gate:** `measure_voice.py` over the full `~/.zoe-voice-samples` corpus — require
  zero CANT_DO/ERROR delta and confirm the brain-turn count drops without said-vs-did
  regressions.

### P2 — Stream an instant "working on it" cue during tool turns (UX, not raw latency)
- **Change:** when the first LLM call resolves to a tool call (and only then), surface the
  already-emitted `__TOOL__:{phase:start}` sentinel as a short spoken/visual ack so the
  user isn't met with 5 s of silence. The final answer is unchanged.
- **Risk:** low-medium. Adds an utterance; must be opt-in per channel and must never
  double-speak. Needs a said-vs-did pass to confirm the ack is stripped from the
  transcript used for verdict classification.

### P3 — Shorten voice replies further (decode reduction)
- **Change:** tighten `_VOICE_BREVITY` (currently "1–2 short sentences") toward a hard
  token cap via `max_tokens_override` on voice turns, trading a sentence for ~1 s.
- **Risk:** medium — directly changes output. Behavior change, needs Jason's sign-off and
  said-vs-did proof that answers stay complete.

### P4 — (already largely done) keep the prefix KV-resident
- The 8192 ctx + `--cache-reuse` + small zoe-core prompt already avoid the legacy cliff.
  No action unless prompt growth (history/memory packet) pushes a turn over ~4 k tokens —
  watch `zoe_core_client._compose_message` history window (`history[-12:]`) and the memory
  packet size if replies regress.

## What NOT to do
- Do **not** remove the second LLM call on tool turns — it is required to answer from live
  data. There is no redundant round-trip to cut.
- Do **not** swap the model, MTP drafter, or STT (rocks).
- Do **not** flip `run_tier0` for voice without (a) a full-corpus replay gate + Jason
  review **and** (b) confirming the upstream voice public-intent Tier-0 + scope gate
  (`voice_tts.py` ~L3893–3994) still runs first — the shared `fast_tiers` Tier-0 does
  **not** carry the scope/PIN check.

## Decision
P1 is the highest-value, behavior-sensitive change and is **proposed, not shipped** here:
it needs a larger replay corpus and a tone review. P2 is the safest pure-UX win. This doc
is the review artifact; implementation PRs follow per-item once approved.
