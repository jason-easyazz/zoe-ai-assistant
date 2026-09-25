# B1.1 — Speculative turn-start with a speculation gate

Status: **flag-dark** (`ZOE_SPECULATIVE_TURN`, default OFF on both the server and the
Pi daemon). Voice-path change → replay-gated before any deploy; no live result is
claimed here. Program entry: `beat-the-bar-2026-program.md` B1.1.

## The dead time being reclaimed

On the panel lane the Raspberry Pi daemon (`scripts/setup/zoe_voice_daemon.py`) records
from the open mic stream and closes the turn only after the full endpoint tail —
`VAD_ENDPOINT_SILENCE_S` (0.8 s), or `ZOE_VAD_TAIL_MS` of consecutive *deep* quiet
(640 ms live) — and only then POSTs the WAV to `/api/voice/turn_stream`
(`routers/voice_tts.py::voice_turn_stream`), which runs Moonshine STT and launches
`voice_command(stream=True)` (the fast tiers + brain). Nothing upstream of the POST
starts until the tail has elapsed, so the tail is pure dead time on every turn.

Smart Turn v3 (`voice_turn.py`) is consumed only by the LiveKit lane
(`routers/voice_livekit.py`); the daemon has no end-of-turn classifier. The panel lane's
"first verdict" is therefore the daemon's deep-quiet counter, which `_Endpointer`
already maintains in VAD mode independently of the `ZOE_VAD_TAIL_MS` flag.

## Design decision

**The gate lives server-side, in the turn stream; the daemon owns the verdict.** Only the
daemon hears the room, so it decides commit/cancel; only the server holds the audible
frames, so it enforces "nothing audible for a cancelled turn". The brain and STT start at
the first verdict; the reopen window costs nothing when first audio is not ready before
it closes (the common case — brain TTFA ≫ 800 ms) and degrades to today's timing when it
is (fast paths wait for the verdict, i.e. for the same tail they wait for now).

Rejected: **daemon-side gate** (buffer audio on the Pi) — the daemon would need to know
which frames are audible and re-implement the stream's error semantics; and a cancelled
speculative turn would still have run the brain with no way to tell the server.
Rejected: **continuous audio streaming, server decides** — a transport rewrite of the
panel lane (the LiveKit lane already is that); out of scope for a spike.

## Protocol

1. **First verdict** (`ZOE_SPECULATIVE_TAIL_MS`, default 320 ms of consecutive deep
   quiet after confirmed speech): the daemon POSTs the audio-so-far to
   `/api/voice/turn_stream` with `{"speculative": true, "turn_id": "<hex>"}` from a
   background thread and **keeps recording on the same mic stream** (no reopen — the
   Jabra rejects a second input stream). Exactly one speculation per recording.
2. **Server (flag ON)**: registers a `SpeculationGate` for `turn_id` *before* STT, runs
   STT + `voice_command` immediately, forwards non-audible frames (`transcript`) at once
   and **holds** audible frames (`chunk`+base64 line, `full_audio`, greeting/filler
   chunks) in order until the gate resolves. From the first held frame on, every frame
   is held (ordering — a `done` must never overtake the audio it closes).
3. **Verdict** — `POST /api/voice/turn_stream/speculation {turn_id, action}`:
   - `commit` — the recording closed with no further speech: release held frames, then
     pass through live.
   - `resolve` + `audio_base64` (final utterance) — speech resumed after the first
     verdict: the server transcribes the final audio and compares it with the
     speculative transcript (`transcripts_equivalent`, LiveKit-style normalised
     equality). Equivalent → release (the resume was noise); otherwise → cancel.
   - `cancel` — explicit drop.
   Cancel drops the held frames, closes the upstream generator (which cancels the
   `voice_command` task), and ends the stream with
   `{"done": true, "cancelled": true, "reply": ""}`. The daemon then runs the **normal**
   turn on the full recording, handing over the speaker claim it already scored.
4. **Safety valve**: no verdict within `ZOE_SPECULATIVE_MAX_HOLD_MS` (default 5000) →
   cancel. Fail-closed toward silence: a lost commit yields a silent turn (the daemon's
   existing "transcript but no audio → never re-POST" rule applies), never audio for a
   turn nobody confirmed.
5. **Flag OFF** (server or daemon): the `speculative`/`turn_id` fields are never read,
   the endpoint answers 409, the daemon never fires — byte-identical to today.

## Failure modes

- **Double-speak.** Held frames are released only once, only on a commit verdict, only
  on the speculative connection; the daemon never re-POSTs a turn whose stream carried a
  transcript unless that stream said `cancelled`. Pinned by tests (b) and the negative
  control (d) below.
- **Cancelled-but-executed brain call.** The speculative brain runs on a *prefix* of the
  utterance; a cancel arrives after `sub_task.cancel()`, but a write-intent that already
  committed (add-to-list, create-event) cannot be undone, and the normal turn on the full
  utterance may then write again ("add milk" → cancelled → "add milk and eggs"). This is
  the inherent cost of speculation and the reason for the cancellation-rate gate. Phase 2
  (not in this PR): mark speculative dispatch so write-intents defer their side effect to
  commit, or restrict speculation to read-only tiers. Until then the flag stays dark.
- **RAM.** A cancelled speculative turn plus its normal re-run is two STT passes and up
  to two brain calls; a `resolve` is one extra STT pass. The brain is single-lane
  (llama-server), so a cancelled request is a queue slot, not a second model. The gate
  buffer holds base64 WAV sentences (tens of KB). Measure, do not assume: RAM must be
  flat across the replay run.
- **Verdict lost / late.** Max-hold cancels; a verdict for an unknown `turn_id` is 404
  and the daemon treats "nothing played" as its existing no-audio path.

## Metrics to add (replay harness + probe)

- `endpoint_wait_delta_ms`: recording end − speculative fire (the reclaimed dead time);
  logged by the daemon per turn, aggregated as median in `measure_voice.py` when the
  replay drives the daemon protocol.
- `speculation_rate` / `cancellation_rate`: server counter
  `zoe_voice_speculation_count{outcome=commit|equivalent|cancel|hold_timeout}` (added in
  `voice_metrics.py`); the probe reports `cancel/(commit+equivalent+cancel)`.
- `interruptions_per_conversation`: cancels per conversation-mode session (the daemon
  already counts conversation turns); reported alongside `ok_rate`.

## Gate criteria (before the flag ever leaves dark)

- Replay corpus (`~/.zoe-voice-samples` via `voice_regression_probe.py`, under the
  harness flock): said-vs-did `ok_rate` no regression; per-stage speed no regression.
- `cancellation_rate < 30 %` on a live panel week with the flag on for the operator only.
- RAM flat (`mem_available_mb` before/after the replay run within noise).
- Zero double-speak incidents in the daemon log (`speculation: committed` never followed
  by a second `turn_stream TTFA` for the same `turn_id`).

## What this PR ships

- `services/zoe-data/voice_speculation.py` — gate, registry, equivalence, frame gating.
- `routers/voice_tts.py` — wraps the turn stream when `speculative` + flag ON; the
  verdict endpoint.
- `scripts/setup/zoe_voice_daemon.py` — `_Endpointer.speculative_ready()`,
  `_SpeculativeTurn`, the `record_command` hook, verdict POST, cancelled-frame handling.
- Tests: `services/zoe-data/tests/test_voice_speculation_gate.py`,
  `tests/unit/test_voice_daemon_speculation.py` (both `ci_safe`).

Not proven: daemon-side timing on the Pi (panel powered off at authoring time) and the
replay gate. Both are required before staging the flag.
