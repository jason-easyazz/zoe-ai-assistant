# Stage A — Channel-Agnostic Turn Core (`fast_tiers.resolve`)

**Status:** DRAFT for review (no code yet)
**Depends on:** #742 (chat fast-path) — *merged*. This is the next step.
**Satisfies:** the Flue decision doc §5 **Phase 1 hard prerequisite** — "the channel-agnostic
core does not yet exist as a reusable unit … must be extracted before any Flue channel can call
it." Also keeps §3.1 (fast path independent of Flue) intact.

---

## 1. Goal

Turn the deterministic sub-second tiers into **one callable, channel-agnostic unit** that **every**
avenue — web chat, voice (touch panel + Jabra), LiveKit real-time WebRTC, WhatsApp, and (next)
Telegram-via-Flue — invokes the same way (full inventory in §2a):

```
outcome = fast_tiers.resolve(text, user_id, session_id, ctx)
if outcome is not None:
    <render the outcome the channel's own way>     # TTS / SSE / Telegram text
else:
    <fall through to the channel's brain lane>      # unchanged
```

The core returns a **renderable text answer + metadata, or `None`**. Channels keep their own
**I/O** (STT/TTS, SSE, Telegram send) and their own **brain lanes**. Nothing about the brain or
the channel-specific gates changes in Stage A.

### What this is NOT (explicit non-goals for Stage A)
- **Not** a merge of the two brain lanes (`brain_dispatch` vs `pi_hybrid`). That is **Stage B**.
- **Not** absorbing channel-specific pre-brain gates (voice form-fill / PIN / panel broadcasts;
  chat approval-risk / capabilities / AG-UI events / action-form `pi_hybrid`). Those stay put.
- **Not** a write/forms path. Writes that need slot-extraction or a UI form continue to defer
  (the `allow_writes=False` philosophy from #742 holds).

---

## 2. Current state (post-#742)

| Tier | Component | Shared today? |
|------|-----------|---------------|
| Tier-0 | `intent_router.detect_intent` → `execute_intent` | shared module, but **called inline** in each channel |
| Tier-1 | `semantic_router.route` | shared module |
| Tier-1.5 | `expert_dispatch.dispatch` via **`fast_path.resolve`** | **shared core** (voice + chat both call it) |

So Tier-1.5 is already one core. The gap: **Tier-0 is still re-implemented inline** in voice and
chat, and there is **no single entry** that runs Tier-0 → Tier-1 → Tier-1.5 and hands back a
uniform result. Telegram-via-Flue needs exactly that single entry.

`fast_path.resolve` is called from 3 sites today:
- `routers/voice_tts.py` — `voice_command` (~L3648), `extra_ctx={"db": db, "panel_id": panel_id}`, writes allowed.
- `routers/chat.py` — `chat_stream_generator` (~L1931) and the non-stream `chat` (~L3220), `allow_writes=False`.

---

## 2a. Channel inventory — every avenue (the full picture)

A "channel" = an input/output surface that wants a Zoe answer. The shared core must serve **all**
of them; only the **I/O** (how text arrives, how the answer is rendered) and the **brain lane**
differ. The touch panel and skybridge are *not* channels — they are a **rendering surface** and a
**voice-side resolver**, called out below so they aren't mistaken for tiers.

| Avenue | Entry point | Input I/O | Output I/O | Uses the deterministic core today? |
|--------|-------------|-----------|------------|-----------------------------------|
| **Web chat** | `routers/chat.py` `/api/chat/` (stream + non-stream) | text | SSE / dict, AG-UI events | **Yes** — `fast_path.resolve` (Tier-1.5) via #742; Tier-0 still inline |
| **Voice (panel + Jabra)** | `routers/voice_tts.py` `/voice/command`, `/voice/turn` | STT (Moonshine/whisper) | Kokoro TTS + panel cards/forms | **Yes** — skybridge → public-intent → `fast_path.resolve` → brain |
| **LiveKit (real-time WebRTC)** | `routers/voice_livekit.py` `_run_pipeline` / `_run_text_pipeline` | STT (reuses `_transcribe_audio`) | Kokoro TTS over WebRTC data | **NO — straight to `brain_oneshot`** (intentional: conversation mode — see §4.5, core is opt-in/off-by-default here) |
| **Telegram** | today via OpenClaw (`routers/openclaw.py`); next via Flue `@flue/telegram` | text | text | **No** — not wired to the core yet |
| **WhatsApp** | `_WHATSAPP_FLOW` inside `chat.py` | text | text/dict | Inherits chat's core (it's a chat sub-flow) |
| **Touch panel** *(surface, not a channel)* | WebSocket push + skybridge `/resolve` endpoint | n/a | cards / forms / nav / audio | Renders voice's & chat's outcomes; has its own skybridge resolver |
| **Skybridge** *(voice-side resolver, not a tier)* | `skybridge_service.resolve_skybridge_request`; `/skybridge/resolve` | text + context | spoken summary + **panel cards** + identity/auth flow | Voice-only (chat = 0 refs); runs **before** `fast_path` |

**Two gaps this exposes:**
1. **LiveKit pays full brain cost for everything** — "what time is it" over LiveKit hits the LLM
   because it never calls the fast tiers. This is the **exact gap #742 fixed for chat**, still open
   on the real-time path. Stage A closes it (see §4.5).
2. **Tier-0 is still inline** in voice, chat, *and* LiveKit — three copies of the same regex front.

---

## 3. Design

### 3.1 New module: `services/zoe-data/fast_tiers.py`

`fast_path.py` is **renamed/promoted** to `fast_tiers.py` (or `fast_path.resolve` grows; rename
preferred for clarity). It exposes one entry:

```python
async def resolve(
    text: str,
    user_id: str,
    session_id: str,
    *,
    router_decision: dict | None = None,   # precomputed Tier-1 route (voice already has one)
    extra_ctx: dict | None = None,         # channel extras (db, panel_id, …)
    allow_writes: bool = True,             # chat passes False; voice/Telegram True
    run_tier0: bool = True,                # run the Tier-0 read-intent shortcut first
) -> TurnOutcome | None:
    """
    Deterministic sub-second turn core. Runs, in order:
      Tier-0  detect_intent  → if a READ intent → execute_intent → TurnOutcome
      Tier-1  semantic_router.route (unless router_decision supplied)
      Tier-1.5 expert_dispatch.dispatch (the current fast_path body)
    Returns a renderable TurnOutcome, or None → caller falls to its brain lane.
    Never raises; any internal error returns None (a turn is never broken by the core).
    REQUIRED: the error path must log via logger.warning(...) before returning None
    (mirror the existing fast_path.py L72-73) so routing failures stay visible in
    production instead of silently degrading to the brain.
    """
```

### 3.2 Return type

**Do NOT introduce a new renamed type.** `expert_dispatch.DispatchResult` already carries
`domain / reply / intent / ui` and is what every current `fast_path` consumer reads via `.reply`.
Renaming `reply`→`text` would force a churn edit at every call site for no functional gain. Instead,
**`TurnOutcome` IS `DispatchResult` with one added field** — keep `reply` as the canonical text and
add `tier` for provenance:

```python
@dataclass
class DispatchResult:          # existing type, in expert_dispatch.py
    domain: str
    reply: str                 # the spoken/printed answer — UNCHANGED field name
    intent: str = ""
    ui: dict | None = None
    source: str = "expert_dispatch"
    meta: dict = field(default_factory=dict)
    tier: str = ""             # NEW: "tier0" | "tier1.5" — provenance, for metrics/debug

# fast_tiers re-exports it under a channel-neutral alias so callers can read either:
TurnOutcome = DispatchResult
```

The Tier-0 branch wraps `execute_intent`'s string into the same `DispatchResult(reply=..., tier="tier0")`.
Calling convention stays **byte-identical**: existing code keeps reading `.reply`; only the optional
`.tier` is new.

**`add_to_chat_ctx` is NOT a field on this schema — it is a channel-side persistence decision.**
The core only *produces* a reply; whether a given channel *commits* that reply to conversation
memory is the adapter's call. Most channels persist normally. LiveKit's parallel-fast+brain mode
(§4.5/§8.3) is the one exception: it speaks the fast reply but does **not** persist it (its
`add_to_chat_ctx=False` decision), because the brain's concurrent reply is the authoritative turn —
so a stateless expert answer never poisons the brain's next-turn context. This lives in the LiveKit
adapter, not in `DispatchResult`; the `tier` field is enough for the adapter to recognise a
fast-tier reply and apply the rule.

### 3.3 Tier-0 read-only shortcut (the new bit)

Before the router, run `detect_intent(text)`. **Only** when it yields a **read** intent whose
`execute_intent` returns plain text (time/date/weather/list_show/calendar_show/reminder_list…)
do we short-circuit into a `TurnOutcome(tier="tier0")`. For **write / form / panel / delegation**
intents, `resolve` returns control (returns `None` or skips Tier-0) so the channel's existing
handlers own them — voice still shows its forms, chat still emits its AG-UI/action-form cards.

The read-vs-write split is **already encoded in the public `expert_dispatch.dispatch(..., write_ok)`
contract** (a `write_ok=False` call returns `None` for write intents — exactly #742's behavior) and
in `intent_router` intent names (`*_show/_list/_status` = read). Stage A reuses that **public**
surface; it does **not** depend on the package-private `_plan` helper. If a shared read/write
classifier is needed directly, promote `_plan`'s classification to a named public function
(`expert_dispatch.classify(domain, text) -> kind`) as part of Stage A rather than reaching into the
underscore API.

**Ambiguity → brain (margin check).** Beyond the absolute confidence threshold, when the top two
routes score within a small margin (e.g. < 0.05) the utterance is ambiguous — return `None` and let
the brain handle it rather than guessing a domain. This is a standard semantic-router safeguard
(§8.2) and belongs in `semantic_router` next to the threshold.

---

## 4. The migration, file by file (behavior-preserving)

### 4.1 `fast_path.py` → `fast_tiers.py`
- Add the Tier-0 read shortcut + `TurnOutcome`. Keep the existing Tier-1/1.5 body unchanged.
- Keep a thin `fast_path` shim (`from fast_tiers import resolve`) for one release so nothing
  breaks mid-migration.

### 4.2 `routers/chat.py` (both paths)
- Replace the inline `fast_path.resolve(...)` calls with `fast_tiers.resolve(..., allow_writes=False)`.
- The **Tier-0 block** currently in chat (`detect_and_extract_intent` → `execute_intent`) stays for
  the **form/panel/write** intents it renders specially; the **read** intents it used to answer
  inline are now caught earlier by `fast_tiers` (same text, just sourced from the core). Verify the
  rendered bytes are identical.
- Rendering of a hit is unchanged: non-stream returns the dict; streaming emits
  `TextMessage*`/`RunFinished` + `_record_run_state("completed")` (the #742 fix).

### 4.3 `routers/voice_tts.py` (`voice_command`)
- Replace the `fast_path.resolve(...)` call with `fast_tiers.resolve(..., extra_ctx={"db": db, "panel_id": panel_id})`.
- Voice's **public-intent fast-path** (`detect_intent`+`execute_intent`, ~L3542) is the same Tier-0
  shortcut `fast_tiers` now does — fold it in **only if** replay stays byte-identical; otherwise
  leave voice's richer version (policy/scope gates) in place and let `run_tier0=False` for voice.
  *(Decision deferred to implementation; replay is the arbiter.)*

### 4.4 New: `channels/telegram_adapter` (skeleton only, lab)
- A thin adapter that calls `fast_tiers.resolve(text, user, session)`; renders `outcome.reply` or,
  on `None`, forwards to the brain. **Not wired to Flue yet** — that's Phase 1 proper. Included
  here only to prove the core is genuinely channel-agnostic.

### 4.5 `routers/voice_livekit.py` — OPT-IN and conversation-safe (NOT default-on)
LiveKit today does **STT → `brain_oneshot` → TTS** with no fast tiers. It is tempting to wire the
core in the same way as the panel, but **LiveKit is a *conversation* mode, not a *command* mode**,
and that changes the calculus. The code is built for dialogue: energy-VAD end-of-speech (~600ms),
push-to-talk, COOLDOWN turn-taking, and `brain_oneshot → run_zoe_core(session_id=…)` so **the brain
holds conversational continuity and Zoe's personality** across turns.

**Mechanics vs. quality:**
- The fast path does **NOT** touch the conversational plumbing — VAD, barge-in/PTT, turn-taking and
  cooldown are all *upstream* of the LLM step; the core only substitutes the "produce a reply" box.
- It **CAN** degrade conversational *quality*: expert replies are terse, templated, and largely
  **stateless**, so a contextual dialogue turn ("what about Saturday?", "yeah, add that one") that
  the router mis-catches would get a robotic, context-blind answer mid-conversation.

The cost/benefit **flips** from command mode: on LiveKit the latency win matters less (you're already
in a flowing exchange) and tone/context consistency matters more. So:
- **Brain-first stays the default.** The brain is what makes LiveKit feel like a real person; the
  core does not displace it by default.
- Fast-path interception is **opt-in via a per-channel flag, OFF by default**, and even when on it is
  **narrow**: only unambiguous, *context-free* reads (time/date/weather/"what's on my list") at a
  **high** confidence threshold; anything conversational or context-dependent flows to the brain.
- **Preferred long-term (Option B) — the documented best practice (§8.3):** the field's pattern is
  **parallel fast + brain**, not bypass. On end-of-turn, fire `fast_tiers` *and* the brain
  concurrently; if the fast tier has a confident factual answer, speak it immediately for instant
  feedback, but mark it **`add_to_chat_ctx=False`** so it never enters conversation memory — the
  brain's reply is the authoritative turn. Pair with streaming + preemptive generation. This keeps
  Zoe's voice and continuity while still feeling instant. Decision deferred to implementation.
- **Gate = conversation-quality review, not just a latency smoke test** (see §5, gate 3).
- Skybridge cards are not added to LiveKit (audio-only, no panel); it would consume the spoken
  `reply` only.

**Net rule:** command-mode channels (web chat, voice panel) adopt the core aggressively;
conversation-mode (LiveKit) adopts it conservatively or not at all. **Same core, per-channel
aggressiveness** — the `run_tier0` flag plus a per-channel enable/threshold knob carry this.

### 4.6 Touch panel & skybridge — what does *not* change
- **Touch panel is a rendering surface, not a channel.** `fast_tiers` returns a `reply` + optional
  `ui` hint; the **voice/chat channel** decides whether to push cards/forms/nav + TTS to the panel
  (voice) or AG-UI components (chat). All panel broadcasts, `get_active_form` field-filling, and
  `_broadcast_intent_nav` stay exactly where they are. Telegram/LiveKit (no panel) just render text.
- **Skybridge stays a voice-side resolver, untouched in Stage A.** It is voice-only, produces panel
  cards + an **identity/auth challenge** (security that must never be bypassed by a generic core),
  and runs *before* the tiers. Keeping `run_tier0=False` for voice (§4.3) preserves its priority;
  replay proves the shared core didn't steal a query skybridge should have answered with a card.
  Its standalone `/skybridge/resolve` endpoint (panel calls it directly) is unaffected.
- **Convergence is later (Stage B/C), not now.** Skybridge's card logic overlaps `expert_dispatch`'s
  domains, and `DispatchResult.ui` already exists; a future step can have the core emit one
  structured result that both the panel renderer and chat AG-UI consume — with its own replay gate.

### 4.7 Telegram & WhatsApp
- **Telegram** is the Flue Phase-1 target: the §4.4 adapter calls `fast_tiers.resolve` and renders
  `reply` as text, brain on a dev/cloud model. No panel, no skybridge cards.
- **WhatsApp** is a sub-flow inside `chat.py` (`_WHATSAPP_FLOW`), so it **inherits chat's
  `fast_tiers` automatically** once §4.2 lands — no separate wiring.

---

## 5. Verification gates (must pass before merge)

1. **Voice replay byte-identical.** `tests/replay_samples.py --last 110` (no `--execute`) produces
   the same routing + spoken text as pre-change. Any divergence blocks the merge.
2. **Chat latency probe unchanged.** `zoe_latency_probe.py` — `show shopping list` stays <60ms,
   `what time is it` Tier-0, no regression vs the saved baseline.
3. **LiveKit conversation-quality gate.** Because LiveKit is a conversation, not a command
   box, the bar is **not** just latency. With fast-path interception OFF (default), a multi-turn
   dialogue must behave exactly as today. With it opt-in ON, a live multi-turn conversation —
   including contextual follow-ups ("what about Saturday?", "yeah add that") — must stay coherent
   and in-voice, and only unambiguous context-free reads may short-circuit. Reviewed by a human on a
   live call, not just `test_voice_livekit_*`. If tone/continuity regresses, interception stays OFF.
4. **Unit tests.** Existing `test_fast_path.py`, `test_voice_routing.py`, `test_fastpath_coverage.py`
   green; add `test_fast_tiers.py` covering the Tier-0 read shortcut + the write/None deferral.
5. **Greptile + validate + GitGuardian** green; threads resolved.

---

## 6. Risk & rollback

- **Risk:** Tier-0 folding changes which tier answers a read (router vs regex), altering wording.
  *Mitigation:* read intents resolve through the **same `execute_intent`** regardless of tier, so
  the text is identical; `tier` is metadata only. Replay enforces this.
- **Risk:** voice's policy/scope gates sit *between* its Tier-0 and the brain; folding Tier-0 could
  skip them. *Mitigation:* `run_tier0=False` for voice keeps voice's gated version; voice still gets
  the shared Tier-1/1.5. Voice only adopts the shared Tier-0 if replay proves the gates are honored.
- **Rollback:** the `fast_path` shim + per-channel call sites mean reverting is a one-line
  re-point per channel; no data migration.

---

## 7. After Stage A

- **Stage B** — brain seam: `BrainInputs → stream[BrainEvent]` so the brain backend is swappable;
  `pi_hybrid` and voice-TTS stay channel-side. This is where Flue becomes the Tier-2 backend.
- **Phase 1 proper** — wire the Telegram adapter to the Flue `@flue/telegram` channel, forwarding
  to `fast_tiers.resolve` (this doc's core), brain on a dev/cloud model per §2b.

---

## 8. Industry alignment & best practices

This design is not bespoke — it is the established pattern for multi-channel + tiered-routing
systems. Capturing the alignment so the plan is defensible and so we adopt the known safeguards.

### 8.1 Architecture: hexagonal / ports-and-adapters (our `fast_tiers` core + tag→profile)
The "platform-agnostic conversation core + thin per-channel adapters" shape is the standard
omnichannel-bot architecture, and the formal name is **hexagonal architecture (ports & adapters)**:
- **Driving (input) ports** = each channel adapter forwards messages into the core
  (`processUserMessage(user, text)`); our channels calling `fast_tiers.resolve(text, tag, …)`.
- **Driven (output) ports** = the core calls out to the brain, TTS, persistence behind interfaces.
- The core depends **only on interfaces**, never on a channel's web server / SDK / DB — so we can
  add a channel by writing one adapter and test the core without standing up a real LLM/DB. Our
  **tag→profile** is exactly the adapter-selects-behavior idea; central session/Postgres state is
  the "shared context readable from any channel" the omnichannel guides call for.
  Refs: [hexagonal for GenAI chatbots](https://shivaramp.medium.com/hexagonal-architecture-for-genai-chatbots-decoupling-ai-logic-from-the-rest-fef1a162330c),
  [omnichannel core+adapters](https://futureagi.com/glossary/omnichannel-cx-solutions/),
  [Haptik omnichannel voice](https://www.haptik.ai/blog/omnichannel-voice-ai).

### 8.2 Routing: cascade + the threshold is load-bearing (+ a margin check)
The recommended cascade is **rule/keyword filter → semantic router → LLM catch-all** — exactly our
Tier-0 → Tier-1 → brain. Two safeguards to bake in:
- **Threshold is the load-bearing hyperparameter.** Per-route (per-domain) thresholds, re-tuned
  whenever routes/utterances/embedding model change. We already have per-domain thresholds in
  `expert_dispatch`; the profile (§8.1) carries a per-channel multiplier (stricter for LiveKit).
- **Margin check for ambiguity (new).** When the top two routes are close (e.g. 0.76 vs 0.74,
  margin < 0.05), treat it as ambiguous and **fall through to the brain** rather than guessing.
  Add this to `semantic_router` alongside the absolute threshold.
  Refs: [Aurelio threshold optimization](https://docs.aurelio.ai/semantic-router/user-guide/features/threshold-optimization),
  [three-tier routing](https://www.mindstudio.ai/blog/set-up-ai-model-router-llm-stack-c2610),
  [semantic router fast-path](https://sureprompts.com/blog/semantic-router-implementation).

### 8.3 Conversation mode (LiveKit): don't bypass the LLM — run fast + brain in parallel
LiveKit's own latency guidance **keeps the LLM always-on** and buys latency elsewhere, which
confirms §4.5. The production pattern is **parallel SLM + LLM**: on end-of-turn, fire a fast model
*and* the brain concurrently; the fast reply goes to TTS immediately (~300ms) for instant feedback
but is **NOT committed to conversation memory** (`add_to_chat_ctx=False`) — the brain's answer
becomes the official turn. For us, deterministic `fast_tiers` is an even-faster stand-in for that
SLM on the unambiguous factual subset. Combine with **streaming** (total latency → ≈ `max(stages)`,
not the sum) and **preemptive generation** (start the brain on the partial transcript). Avoid
aggressive turn-detection tuning — it "makes the conversation feel less natural."
  Refs: [parallel SLM+LLM](https://webrtc.ventures/2025/06/reducing-voice-agent-latency-with-parallel-slms-and-llms/),
  [LiveKit agent latency](https://livekit.com/blog/understand-and-improve-agent-latency).

### 8.4 What we adopt into the plan
1. Name the architecture **ports-and-adapters**; keep the core free of channel/vendor deps (§8.1).
2. Add a **margin check** to `semantic_router`; per-channel threshold multiplier in the profile (§8.2).
3. LiveKit (§4.5): adopt **parallel fast+brain** with **`add_to_chat_ctx=False`** for any instant
   reply, plus streaming + preemptive generation — never a bare LLM bypass (§8.3).
