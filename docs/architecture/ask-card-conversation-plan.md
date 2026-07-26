---
type: architecture-plan
status: planned
audience: human-first (Jason) + all agents
---

# Ask-card live conversation + generative answer tiles

**Goal (Jason's words):** a pathway to *stay talking to Zoe*, and an Ask Zoe card that is
"where I'd want to see Zoe create tiles to show answers and results".

**The decision (2026-07-16):** build both **into the Ask card of the estate
(`touch/home.html`)** — *not* a separate page like `voice.html`.

---

## 1. Why the Ask card, not a separate file

1. **It's one experience.** "Keep talking" and "see the answers as tiles" are two halves of
   the same conversation. Splitting them across surfaces re-creates the multi-page sprawl the
   estate exists to end.
2. **The estate is the single front door.** `home.html` is the sole kiosk entry
   (`start-kiosk.sh`, `config.json`, `provision-server.py`). Another surface = another copy of
   auth, theme, dock, wake, ambient-return to keep in sync.
3. **The tiles vision lives here.** The Ask card is where Zoe should compose answers as cards.
   If the conversation happens elsewhere, the tiles fragment with it.
4. **LiveKit is transport, not a surface.** The client is a module — lazy-loaded only when a
   conversation starts. Continuous talk needs no separate page.

**Unblocks:** `voice.html` is the last legacy touch page held back from retirement, precisely
because the estate has no continuous-conversation surface. Phase 1 removes that blocker.
(See the legacy-touch retirement; the operator's call was **build this first, then retire it**.)

---

## 2. What already exists — reuse, don't rebuild

This is mostly **wiring existing pieces into the estate**, not greenfield.

**LiveKit (backend — no changes needed):**
- `services/zoe-data/routers/voice_livekit.py` — `/livekit-token`, `/livekit-health`, the agent
  loop, internal `ws://127.0.0.1:7880`, `LIVEKIT_API_KEY`.
- **On-demand by design:** `ZOE_LIVEKIT_ONDEMAND=true` (default) leaves the ~560 MB container
  stopped; it starts on the first `/livekit-token` request and is reaped after
  `ZOE_LIVEKIT_IDLE_TIMEOUT_S` (300 s) with no participants. **This must be preserved** — the box
  is memory-tight.
- Client bundle already vendored: `livekit/livekit-client.umd.min.js` (what `voice.html` loads).

**Conversation entry (today, on voice.html):**
- `voice_tts.py::_broadcast_lets_talk_ui` → navigates to `/touch/voice.html` **and** broadcasts a
  transient `voice:start_conversation` (`delay_ms: 2500`, never enqueued — no replay loop) which
  the page uses to auto-open the mic.
- `chat.py::_INTENT_PANEL_NAV` keeps `lets_talk → /touch/voice.html?conv=1` ("phone-call voice
  mode (still its own surface)").

**Generative tiles (built, flag-dark):**
- `services/zoe-data/ui_compose.py` — `compose_card(user_message, answer_text, user_id)`,
  gated by **`ZOE_COMPOSE_UI` (default OFF)**.
- `services/zoe-data/ui_catalog.py` — the component catalog.
- `services/zoe-data/card_contract.py` — `validate_component` / `validate_component_action`.
- `services/zoe-ui/dist/touch/js/zoe-compose.js` — the tree renderer. **`chat.html` already
  renders it; the estate does not load it yet.**

**Estate (today):**
- Ask surface is text-only: `ask:{ … <div class="aa" id="askOut">Just ask — I'll answer here.</div> }`.
- Voice is event-driven via `window.ZoeEstateVoice` (orb listening/done, transcript → dock
  "Heard", answers → `askOut`) + server-pushed UI actions. There is **no live session**.

---

## 3. Architecture

The Ask card gains a **conversation mode** — a state of the existing surface, not a new page.

```
idle Ask card ──(enter: "let's talk" | Ask-card control)──► conversation mode
   │                                                             │
   │  lazy-load livekit-client.umd.min.js  (NEVER on estate boot)│
   │  POST /livekit-token → join room → mic open                 │
   │  orb: listening · live transcript streams into the card     │
   │  answers render as generative tiles (Phase 2)               │
   │                                                             │
   ◄──(exit: done | navigate | sleep | idle | error)─────────────┘
        leave room · stop tracks · RELEASE MIC · ambient-return
        (container self-reaps after ZOE_LIVEKIT_IDLE_TIMEOUT_S)
```

Key properties:
- **Lazy** — the WebRTC/LiveKit client loads on entry only. The estate boot stays light.
- **Bounded** — exit always tears the session down; the mic is never left open.
- **Reuses the rocks** — STT/brain/TTS unchanged; LiveKit is a transport consumer.

---

## 4. Phases (PR-sized)

### Phase 1 — Ask-card live conversation *(unblocks voice.html retirement)*

- **PR-1a — conversation mode in the Ask card.** Lazy-load the vendored LiveKit client on entry;
  `POST /livekit-token`; join; orb → listening; stream the live transcript into the Ask card;
  barge-in via the session. Handle the transient `voice:start_conversation` push (auto-open mic
  after `delay_ms`) in the estate.
- **PR-1b — entry points + teardown.** Repoint `_broadcast_lets_talk_ui` and `chat.py`'s
  `_INTENT_PANEL_NAV` `lets_talk` → `/touch/home.html?domain=chat&conv=1`; add an Ask-card
  control to start/stop. Teardown on done/navigate/sleep/idle/error (leave room, stop tracks,
  release mic). **Replay-gated** (it touches `voice_tts.py`).
- **PR-1c — retire `voice.html`.** Delete it + sever the last refs (`orb-loader.js` guard, the
  auth.js map entry). Hand to the legacy-touch retirement effort.

### Phase 2 — Answers as generative tiles in the Ask card

- **PR-2a — one renderer in the estate.** Load `zoe-compose.js` in `home.html`; render
  `zoe.component` trees in the Ask card, reusing the path `chat.html` already proves. No new
  renderer.
- **PR-2b — wire compose into the answer path.** Call `ui_compose.compose_card` for Ask/
  conversation answers behind `ZOE_COMPOSE_UI`; validate via `card_contract`; **always fall back
  to plain text**. **Voice is never blocked on compose** — compose only after TTS has started
  (the standing rule).
- **PR-2c — lab-prove, then flip `ZOE_COMPOSE_UI`.** Operator flip after a panel check.

---

## 5. Guardrails

- **Rocks untouched** — Gemma 4 E4B (brain), Moonshine (STT), Kokoro (TTS). LiveKit is a
  consumer, never a swap.
- **Preserve LiveKit on-demand + idle reap.** ~560 MB; the box is memory-tight. Never pin the
  container up, never load the client on estate boot.
- **Mic hygiene** — every exit path releases the mic. No open session on a kiosk.
- **Voice never blocked on composition**; compose is flag-gated with a text fallback.
- **Replay-gate** every `voice_tts.py` touch against `~/.zoe-voice-samples` (said-vs-did + per-
  stage speed), under `flock /tmp/zoe-voice-harness.lock`.
- Panel-verify at the real **1280×720**; ambient-return/sleep must still win over a stale session.

---

## 6. Verification

1. Headless (1280×720): enter → LiveKit client fetched **only** on entry; exit → tracks stopped,
   mic released; navigate/sleep during a session tears it down.
2. Live: `/livekit-health`; container starts on first `/livekit-token` and **reaps** after idle.
3. Replay corpus green for the `voice_tts.py` repoint (PR-1b).
4. Tiles: `card_contract` validation tests (incl. hostile trees) + an estate render harness;
   text fallback proven when compose fails/off.
5. Panel: "let's talk" opens the Ask card in conversation mode; answers render as tiles once
   `ZOE_COMPOSE_UI` is on.

---

## 7. Decisions + open questions

**✅ RESOLVED — the agent already does the whole turn server-side (2026-07-16).** `_agent_loop()`
in `voice_livekit.py` connects to the room, runs VAD per participant, and routes the utterance
through the **existing rocks**: STT via `voice_tts._transcribe_audio` (Moonshine) → brain via
`zoe_core_client` (with `_prewarm_brain` on VAD IDLE→LISTENING so it's warm; **brain-first is the
conversation-mode default**, with a sub-second fast-tier that returns `None` to defer to the
brain) → TTS via `voice_tts.synthesize` (Kokoro). It pushes transcript/reply payloads to
participants with `publish_data(..., reliable=True)`. Backend transport: `livekit-ffi` with an
automatic **aiortc** fallback — **the Jetson requires it and `ZOE_LK_USE_AIORTC=1` is already set
in the live `.env`** (native WebRTC can't init a PeerConnection on the Tegra kernel).

**⇒ This makes the estate client THIN.** PR-1a does not implement a turn — it only has to:
join the room with a `/livekit-token`, publish the mic track, subscribe/play Zoe's audio track,
and read the `publish_data` payloads to render the transcript (and, in Phase 2, the tiles).

**✅ DECIDED — inline, not fullscreen (Jason, 2026-07-16).** Conversation mode is a *state of the
Ask card*: the dock (speakers / now-playing / timer chips), home button and orb all stay put, and
the transcript + tiles render in the Ask card's content area. Rationale: hiding the dock
mid-conversation would remove music/timer controls exactly when they're wanted, and it's the
smaller build. **Cheap to reverse** — the LiveKit session, transcript and tile rendering are
identical either way, so promoting to fullscreen (or a hybrid that keeps the dock but enlarges
the orb) is a later chrome-toggle, not an architecture change.

**⬜ OPEN — wake-word interplay.** The panel-side voice daemon owns wake today. Decide whether a
live LiveKit session suspends it to avoid double-listening (the daemon and the room both holding
the mic). Resolve during PR-1a.

**Required either way (both designs):** a live session must suppress the **ambient-return** drift
(no bouncing home mid-sentence) and the **idle→sleep** timer. The timer-finish alarm (z85) still
surfaces above the conversation — correct.

## 8. NEXT ACTION

**PR-1a** — Ask-card conversation mode: lazy-load the LiveKit client on entry → `/livekit-token` →
join → publish mic → play Zoe's track → render the `publish_data` transcript; suppress
ambient-return + idle-sleep while live; release the mic on every exit. Resolve the wake-word
question (§7) as part of it. Phase 1 must land before `voice.html` retires.
