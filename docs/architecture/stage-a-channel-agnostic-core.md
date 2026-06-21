# Stage A — Channel-Agnostic Turn Core (`fast_tiers.resolve`)

**Status:** DRAFT for review (no code yet)
**Depends on:** #742 (chat fast-path) — *merged*. This is the next step.
**Satisfies:** the Flue decision doc §5 **Phase 1 hard prerequisite** — "the channel-agnostic
core does not yet exist as a reusable unit … must be extracted before any Flue channel can call
it." Also keeps §3.1 (fast path independent of Flue) intact.

---

## 1. Goal

Turn the deterministic sub-second tiers into **one callable, channel-agnostic unit** that any
channel — voice, web chat, and (next) Telegram-via-Flue — invokes the same way:

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
- `routers/voice_tts.py` — `voice_command` (~L3648), `extra_ctx={"db","panel_id"}`, writes allowed.
- `routers/chat.py` — `chat_stream_generator` (~L1931) and the non-stream `chat` (~L3220), `allow_writes=False`.

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
- A thin adapter that calls `fast_tiers.resolve(text, user, session)`; renders `outcome.text` or,
  on `None`, forwards to the brain. **Not wired to Flue yet** — that's Phase 1 proper. Included
  here only to prove the core is genuinely channel-agnostic.

---

## 5. Verification gates (must pass before merge)

1. **Voice replay byte-identical.** `tests/replay_samples.py --last 110` (no `--execute`) produces
   the same routing + spoken text as pre-change. Any divergence blocks the merge.
2. **Chat latency probe unchanged.** `zoe_latency_probe.py` — `show shopping list` stays <60ms,
   `what time is it` Tier-0, no regression vs the saved baseline.
3. **Unit tests.** Existing `test_fast_path.py`, `test_voice_routing.py`, `test_fastpath_coverage.py`
   green; add `test_fast_tiers.py` covering the Tier-0 read shortcut + the write/None deferral.
4. **Greptile + validate + GitGuardian** green; threads resolved.

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
