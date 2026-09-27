# B1.1 — Speculative turn-start with a speculation gate

Status: **flag-dark** (`ZOE_SPECULATIVE_TURN`, default OFF on both the server and the
Pi daemon). Gate + daemon protocol: #1685. Phase 2 (side effects wait for the verdict),
the offline cancel-rate / Smart Turn veto evidence and the Moonshine quiet-clip check:
the B1.1 groundwork PR. Voice-path change → replay-gated before any deploy; no live
result is claimed here. Program entry: `beat-the-bar-2026-program.md` B1.1.

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

   A `turn_id` whose gate is still unresolved is refused (409) rather than replaced — a
   retried POST must not start a second brain call against one verdict slot. Every gated
   stream leads with `{"speculation": "gated", "turn_id"}` (the daemon's proof the server
   is gating). An EMPTY speculative transcript closes the slot and answers `cancelled`
   (`empty_transcript`) directly, bypassing the gate: nothing was processed, the daemon must
   run the full recording, and the daemon's commit routinely wins the slot before STT
   returns (fire→commit ≈ 320-480 ms, shorter than a Moonshine pass) — through the gate a
   won commit would pass a plain `done` and lose the turn.
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
6. **One-sided rollout guard (daemon on, server off)**: the server then answers the prefix
   as an ordinary turn and streams audio — early (while the daemon still records) or late
   (a slow server, after the verdict POST), so timing proves nothing. The proof is the
   ack: a gating server leads with `speculation: gated`; an audible frame on a speculative
   stream that never carried it means an ungated server. The daemon plays nothing, does
   not re-POST (the prefix was processed — the duplicate-write class), logs an ERROR and
   latches speculation off for the process; a `409` from the verdict endpoint (only an
   ungated server answers that) latches the same way. One silent turn, never a second.

## Failure modes

- **Double-speak.** Held frames are released only once, only on a commit verdict, only
  on the speculative connection; the daemon never re-POSTs a turn whose stream carried a
  transcript unless that stream said `cancelled`. Pinned by tests (b) and the negative
  control (d) below.
- **Cancelled-but-executed write.** The speculative turn runs on a *prefix* of the
  utterance; a write that already committed ("add milk") cannot be undone by a cancel,
  and the normal turn on the full utterance then writes again ("add milk and eggs").
  Closed by **Phase 2** below: no side effect runs before the verdict. The residuals it
  does not cover are listed there.
- **RAM.** A cancelled speculative turn plus its normal re-run is two STT passes and up
  to two brain calls; a `resolve` is one extra STT pass. The brain is single-lane
  (llama-server), so a cancelled request is a queue slot, not a second model. The gate
  buffer holds base64 WAV sentences (tens of KB). Measure, do not assume: RAM must be
  flat across the replay run.
- **Prefix processed, audio never arrives** (connection lost after the transcript frame,
  brain error frame): the daemon's existing rule applies — a stream that carried a
  transcript is never re-POSTed, because the server may already have executed a write.
  The rest of the utterance is lost for that turn (WARNING logged). Deliberate: the
  alternative is the duplicate-write class above. Only the EMPTY-transcript case is safe
  to re-run, and the server marks it `cancelled` for exactly that reason.
- **Cancel during STT.** The verdict can resolve the gate before the router's generator
  was ever pulled; `aclose()` on a never-started async generator skips its `finally`
  (where the brain task is cancelled), so the gate primes the upstream to its first
  yield — the transcript line, no brain work — before closing it.
- **Quiet continuation.** Soft speech after the first verdict that never crosses the
  speech threshold still resets the deep counter; the daemon treats ANY non-deep chunk
  after the fire as resumed (→ `resolve`, one extra STT pass, released if equivalent)
  rather than committing a prefix that missed it.
- **Verdict lost / late.** Max-hold cancels; a verdict for an unknown `turn_id` is 404
  and the daemon treats "nothing played" as its existing no-audio path.

## Phase 2 — side effects wait for the verdict

The gate holds what is **audible**; Phase 2 holds what the turn **writes**. Rule: while a
speculative turn is unresolved, reads and chat run; every side effect waits for the
verdict, runs **exactly once** on commit / equivalent-resolve, and is **dropped** on
cancel, non-equivalent resolve or hold timeout. Same flag — nothing new to stage.

**Binding.** The router `bind`s the gate into a `ContextVar` around the `voice_command`
task, and `gate_frames` runs every pre-verdict upstream pull under the same binding
(the chat/brain lane executes inside the lazily-pulled StreamingResponse body, not in
the task). Every task created beneath inherits it; nothing else ever sees it.

**Holds** (`voice_speculation`, all no-ops with nothing bound):

| Where | What waits | Classification used (fail-closed) |
|---|---|---|
| `voice_command`, right after the router decision | the WHOLE turn — before any pending confirmation, history row, intent or brain call | `turn_is_speculation_safe`: regex `detect_intent` ∈ `SPECULATION_SAFE_INTENTS`, Skybridge (domain, action) read-only, routed domain ∈ {chat, weather, time}. No router decision → hold |
| `intent_router.execute_intent` | every non-read intent (the shared write funnel: fast tiers, quick intents, confirmations, music, smart home, timers) | `SPECULATION_SAFE_INTENTS` (a superset of `fast_tiers._TIER0_READ_INTENTS`, pinned) |
| `skybridge_service.resolve_skybridge_request` | non-read Skybridge actions (it has its own list/calendar/people INSERTs) | domain ∈ {clock, weather} or action ∈ {show, status, overview, forecast, identity} |
| `expert_dispatch.dispatch` | write / memory-store kinds, BEFORE the slot-extraction LLM call | the dispatcher's own `kind` |
| `routers/voice_tts._spawn_bg` | every background side effect — user/assistant history rows, memory passes, escalations — queued in spawn order | all of them |
| `POST /api/system/intent-dispatch` | brain **tool** writes (Flue sidecar / zoe-core → a separate request the ContextVar cannot reach) | non-read intent AND the acting user has an unresolved speculative turn (`note_turn_user`) |

A dropped inline effect raises `SpeculativeTurnCancelled` — a `CancelledError` on purpose:
every write site sits under `except Exception` handlers that would otherwise swallow it
and carry on (fall to another tier, synthesize "done", save history). `gate_frames`
treats an upstream that ended that way as the verdict's consequence and still answers
`cancelled`. The two-layer shape is deliberate: the turn-level hold covers side effects
that have no funnel (pending confirmations, introductions, the brain lane of a
write-capable domain); the funnels catch whatever the cheap turn classifier misjudges.

**What a held turn still gains.** STT and routing already ran on the prefix, so a held
write turn still starts ~one STT pass earlier than today; reads/chat keep the full
overlap.

**Residuals (not side effects on user data, accepted):**
- The panel shows the prefix transcript (`voice:transcript` broadcast) before the full one.
- A chat turn's brain session (Flue durable session / zoe-core Pi process) may retain the
  prefix user message when the speculative call is cancelled mid-generation — the next
  turn's context carries both the prefix and the full utterance.
- In-memory panel session touch (`_touch_panel_session`) runs for read/chat prefixes.
- The brain-tool hold is keyed by **user**, not turn (the sidecar does not forward a turn
  id): a same-user write from ANOTHER channel inside the ≤ max-hold window waits for the
  verdict, and on a cancel is refused with `ok: false` — the brain says it could not
  confirm (loud), never a silent loss. Forwarding the turn id through the sidecar envelope
  would remove this and is the follow-up if it ever bites.

Pinned by `services/zoe-data/tests/test_voice_speculation_write_deferral.py` (`ci_safe`):
held-then-once-on-commit / dropped-on-cancel / dropped-on-hold-timeout / equivalent and
non-equivalent resolve, reads immediate, nothing-bound unchanged, background queue order,
the router end-to-end for both the task path and the lazy-body (brain) path, the
intent-dispatch hold, and break-the-fix controls (removing the `execute_intent` guard, the
pull binding, the `_spawn_bg` deferral, the intent-dispatch hold, or the `gate_frames`
cancelled-upstream handling each turns tests red).

## Offline evidence (2026-09-27, Jetson, corpus only — no live claim)

### Cancel rate and saving at the real tail

`scripts/perf/measure_endpointing.py --samples 2000 --tail-flag-ms 640 --speculative-ms
320,400,480,560,640 --smart-turn-veto 0.5,0.8` (and `--tail-flag-ms 0` for the flag-off
800 ms close). All 1171 usable 16 kHz corpus recordings, **untrimmed** (the real
end-of-turn room silence, not digital zeros), through the shipped `_Endpointer` exactly as
`record_command` drives it. `cancel` = a chunk crossed the speech threshold after the fire
(the user was still talking → non-equivalent resolve); `res_q` = only borderline quiet
followed (→ `resolve`, almost surely equivalent, one extra STT pass); `cancel %` =
cancel / (commit + res_q + cancel), the gate's ratio; `cons %` also counts every `res_q`
as a cancel. Saving = close − fire on released turns; `mean/turn` spreads it over every
recording (no-fire and cancels save 0).

Live close (`ZOE_VAD_TAIL_MS=640`, the Pi's value):

| `ZOE_SPECULATIVE_TAIL_MS` | fired | commit | res_q | cancel | **cancel %** | cons % | **median saving** | mean / turn |
|---|---|---|---|---|---|---|---|---|
| **320** (default) | 1081 | 865 | 61 | 155 | **14.3 %** | 20.0 % | **320 ms** | 244 ms |
| 400 | 1022 | 888 | 39 | 95 | 9.3 % | 13.1 % | 240 ms | 187 ms |
| 480 | 980 | 908 | 23 | 49 | 5.0 % | 7.3 % | 160 ms | 126 ms |
| 560 | 925 | 896 | 8 | 21 | 2.3 % | 3.1 % | 80 ms | 62 ms |
| 640 | 0 | — | — | — | inert | — | 0 | 0 |

At 640 the hook is inert by design (the fast tail closes the recording at the same
instant; the daemon warns once). With the fast tail off (800 ms close) the same sweep
gives 17.8 / 12.9 / 9.0 / 6.5 / 5.0 % cancels with 400 / 320 / 240 / 160 / 80 ms median
saving — the numbers to use if `ZOE_VAD_TAIL_MS` is ever unset.

**Reading.** Only the default 320 ms clears both flip thresholds offline (< 30 % cancels,
≥ 250 ms median saving); 400 ms already falls below the saving bar. Both columns are
UPPER bounds on cancels: "speech after the fire" includes post-command noise, a second
talker or TV that the equivalence check would release. The same ceiling shows in the
live endpointer's own figure — it closes before the clip's LAST speech-like chunk on
261 / 1171 recordings (22 %) with no speculation involved, far above the 3.6 % recorded
for the 640 ms deep tail on trimmed utterances (the `ZOE_VAD_TAIL_MS` comment in the
daemon), so most of those "late speech" chunks are not the user's command. The saving is quantised to the 80 ms chunk.

### Smart Turn veto (simulated — the daemon has no veto)

At each fire point the real zoe-data Smart Turn v3.2-cpu (`voice_turn.py`, numpy
log-mel, 4 ORT threads) scores the audio so far; below p the fire is withdrawn until a
new pause, and the scoring time is charged against the saving.

| tail | veto p | fired | cancel | cancel % | median saving | mean / turn | vetoes | scoring ms (median) |
|---|---|---|---|---|---|---|---|---|
| 320 | none | 1081 | 155 | 14.3 % | 320 ms | 244 ms | 0 | — |
| 320 | 0.5 | 1002 | 135 | 13.5 % | 215 ms | 140 ms | 101 | 63–95 |
| 320 | 0.8 | 966 | 130 | 13.5 % | 257 ms | 174 ms | 145 | 63 |
| 400 | 0.8 | 896 | 75 | 8.4 % | 177 ms | 120 ms | 144 | 63 |

**Verdict: NOT a win.** At 320 ms the veto removes 20–25 cancels but withdraws 101–145
fires, most of them at TRUE ends (a prefix scored mid-silence reads as "not finished"),
and every fire pays ~63 ms (95 ms cold, under load) of scoring: mean saving per turn
drops from 244 ms to 140–174 ms while the cancel rate barely moves (14.3 → 13.5 %).
The cancel rate is already under the bar without it; revisit only if the LIVE rate is
not.

### Moonshine quiet clips — `vad_threshold`

`scripts/perf/measure_moonshine_vad_threshold.py --last 20 --thresholds
default,0.5,0.3,0.0 --repeats 3` with the zoe-data venv (in-process moonshine-voice 0.1.3,
MEDIUM_STREAMING, the service's wake-word strip), one model load at a time, `nice -n 15`,
over the replay gate's newest-20 slice. No transcript is printed or recorded — only
empty/non-empty, word count and a pattern category. (0.3 and 0.0 got 2 repeats: the
`MemAvailable` floor stopped the third.)

| arm | EMPTY per run | of the 6 always-EMPTY clips → text | what came back | wall ms, median on the EMPTY set | CPU ms, median on the EMPTY set |
|---|---|---|---|---|---|
| default (live) | 7, 7, 7 | — | — | 58 | 56 |
| 0.5 (explicit) | 7, 7, 7 | 0 | — | 60 | 57 |
| 0.3 | 7, 7 | 1 | one 3-word fragment ("unclear") | 391 | 612 |
| 0.0 (VAD off) | 5, 5 | 2 | one 8-word command-shaped line, one 6-word "unclear" | 781 | 3841 |

- The 7 EMPTY clips are low level (−35 to −46 dBFS). **Four of the six always-EMPTY clips
  stay empty with Moonshine's VAD fully off** — the encoder hears nothing in them; they are
  not lost commands.
- **The engine is not deterministic across loads here:** at the default, 2 clips flip
  between EMPTY and text across identical runs (6 always-EMPTY, 8 ever-EMPTY), and 5 of the
  13 non-empty transcripts change wording between identical runs. So the replay's "7 EMPTY"
  is a count, not a stable set, and any per-clip transcript comparison needs repeats.
- Lower thresholds are not monotonic: at 0.3 two clips that returned text at the default
  (one "likely hallucination", one of the unstable pair) came back EMPTY.
- Cost: 0.0 makes every silent clip a full encoder pass (+~0.7 s wall / +3.8 s CPU) and
  raises the median CPU across all clips from ~2.7 s to ~4.4 s. Wall times were taken
  while the endpointing sweep shared the CPU — read them as relative, not live latency.

**Recommendation: keep the default (no change in this PR).** The best case recovers one
command-shaped clip, only with the VAD fully off, at a real latency and CPU cost and with
hallucination exposure on noise. Next step, if wanted: the operator labels the two
recoverable clips by ear (`210532_834`, `222059_829`, plus the unstable `135639_660`); if
they are real commands, plumb `ZOE_MOONSHINE_OPTIONS` and trial `vad_threshold` 0.3–0.4
behind a head-bound replay that must add no hallucinated command.


## Metrics to add (replay harness + probe)

- `endpoint_wait_delta_ms`: recording end − speculative fire (the reclaimed dead time);
  logged by the daemon per turn, aggregated as median in `measure_voice.py` when the
  replay drives the daemon protocol.
- `speculation_rate` / `cancellation_rate`: server counter
  `zoe_voice_speculation_count{outcome=commit|equivalent|cancel|hold_timeout|empty_transcript}`
  (added in `voice_metrics.py`); the probe reports `cancel/(commit+equivalent+cancel)` —
  `hold_timeout` and `empty_transcript` are not user interruptions and stay out of the ratio.
- `interruptions_per_conversation`: cancels per conversation-mode session (the daemon
  already counts conversation turns); reported alongside `ok_rate`.

## Flip criteria (before the flag ever leaves dark)

All of these, in order; any miss keeps the flag dark:

1. **Offline cancel estimate** below 30 % with ≥ 250 ms median saving at the chosen
   `ZOE_SPECULATIVE_TAIL_MS` — met at the default 320 ms (14.3 % upper bound, 320 ms;
   table above). Re-run if the live `ZOE_VAD_TAIL_MS` changes.
2. **Phase 2 landed** (side effects wait for the verdict) — this PR.
3. **Replay PASS bound to the head** (`voice_regression_probe.py` under the harness flock):
   said-vs-did `ok_rate` no regression, per-stage speed no regression.
4. **RAM flat**: `mem_available_mb` before/after the replay within noise (a cancel is two
   Moonshine passes serialised on one lock plus up to two brain calls).
5. **Operator-only live panel week** with the flag on at both ends:
   - `zoe_voice_speculation_count` `cancel / (commit + equivalent + cancel)` **< 30 %**;
   - measured `endpoint_wait_delta_ms` (recording end − speculative fire) **≥ 250 ms
     median** on committed turns;
   - zero double-speak (`speculation: committed` never followed by a second
     `turn_stream TTFA` for the same `turn_id`) and zero duplicate writes (no list item /
     reminder / event created twice for one utterance);
   - zero "said X, did Y" from an equivalence misjudgement.

## What shipped

- **#1685** — `services/zoe-data/voice_speculation.py` (gate, registry, equivalence, frame
  gating); `routers/voice_tts.py` (turn-stream wrap + verdict endpoint);
  `scripts/setup/zoe_voice_daemon.py` (`_Endpointer.speculative_ready()`,
  `_SpeculativeTurn`, the `record_command` hook, verdict POST, cancelled-frame handling);
  tests `services/zoe-data/tests/test_voice_speculation_gate.py`,
  `tests/unit/test_voice_daemon_speculation.py`.
- **B1.1 groundwork** — Phase 2 (`voice_speculation` binding + holds; hooks in
  `intent_router.execute_intent`, `skybridge_service`, `expert_dispatch`,
  `routers/voice_tts` (`_spawn_bg`, the turn-level hold, `note_turn_user`),
  `routers/system.py` intent-dispatch); `scripts/perf/measure_endpointing.py
  --speculative-ms/--smart-turn-veto`; `scripts/perf/measure_moonshine_vad_threshold.py`;
  tests `services/zoe-data/tests/test_voice_speculation_write_deferral.py`.

Not proven: daemon-side timing on the Pi, the replay gate against either head, and any
live cancel rate. All are required before staging the flag.
