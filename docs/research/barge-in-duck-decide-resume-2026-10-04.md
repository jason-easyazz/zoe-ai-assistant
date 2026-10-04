---
type: research
title: Barge-in — duck → decide → resume (2026-10-04)
date: 2026-10-04
status: research-only — no code, flag, unit or env changed by this document
description: Deep dive on P2 from the 2026-10-03 companion-field audit. Field half — how Vapi, LiveKit Agents, Pipecat, OpenAI Realtime, Gemini Live, Hume EVI, OVOS and Voice-Light decide a real interruption from a backchannel, duck vs hard-stop, and trim the stored assistant turn to what was heard. Our half — the live barge-in path traced end to end (Pi daemon → zoe-data turn_stream → Flue client abort → sidecar), what the three stores hold after a barge-in today, and exactly where ducking, the decide window and heard-text truncation (A3) hook in. Ends in a flag-dark design, costs on both boxes, a measurement plan on the existing harnesses, negative controls and a go/no-go against VISION.
---

# Barge-in — duck → decide → resume (2026-10-04)

Research date: 2026-10-04. Read-only: nothing on the Orin or the Pi was changed, no service was
restarted, no chat or voice turn was sent to the live API. The one live read was a
read-only `ssh zoe-pi` probe of the audio route and the barge-in env names (values quoted below are
barge/audio knobs only — no tokens, no household data).

Evidence labels: **[src]** checked in this tree at the quoted file:line · **[live]** read from the
running Pi 2026-10-04 · **[doc]** official upstream docs or paper · **[2nd]** secondary source ·
**[inf]** my inference · **[unverified]** a claim this pass could not check.

The rocks (Gemma 4 E4B-QAT + MTP, Moonshine v2 Medium, Kokoro) are untouched by every proposal
here; Silero VAD on the Pi stays the only detector. No new model is proposed on either box.

## 0. TL;DR

1. **Today Zoe's barge-in is a hard stop with three disagreeing memories of it.** The Pi fires
   on Silero after an 800 ms grace (3 of 6 chunks ≥ 0.75, or 2 consecutive ≥ 0.95), kills `aplay`
   at once and breaks the sentence stream [src]. After that: (a) zoe-data persists to
   `chat_messages` every sentence it *emitted* (sentence-level, ≥ what was played) [src]; (b) with
   `ZOE_FLUE_ABORT_ON_CANCEL` off (live default) the Flue sidecar finishes the turn on the single
   llama slot and stores the **whole** reply as said [doc-in-repo]; (c) with it on, Flue **drops the
   partial** and the model sees `<signal type="submission_aborted">` instead — it no longer knows
   what it said [doc-in-repo]. The Flue lane never reads `chat_messages`, so (a) never reaches the
   brain [src].
2. **The interrupting words are lost.** `_BargeMonitor` scores mic chunks but retains none; after
   the kill the daemon plays the follow-up beep and opens a *fresh* stream, so the user must repeat
   what they said over Zoe [src]. Every field runtime seeds the next user turn with the
   interrupting audio; Zoe's own dormant LiveKit lane already does (`…seeds_frames` test) [src].
3. **The field converged on the same three moves.** Gate the stop on *evidence*, not VAD onset
   (Vapi `numWords` 0–10, `voiceSeconds` 0.2 s; LiveKit `min_duration` 0.5 s, `min_words`; Hume
   `min_interruption_ms` 800; Pipecat `MinWordsUserTurnStartStrategy` *only while the bot speaks*)
   [doc]. Treat a VAD hit with no words as **false** and **resume** (LiveKit
   `false_interruption_timeout` 2.0 s, `resume_false_interruption` true; Voice-Light: fade −15 dB
   over 450 ms, pause, commit only on a 0.82 floor-take score or 900 ms of sustained speech, else
   resume the *same* generation) [doc]. **Truncate history to what was heard** (OpenAI
   `conversation.item.truncate` "remove[s] the text transcript for the unplayed portion"; Gemini
   Live "only the information already sent to the client is retained"; LiveKit "truncates its
   conversation history to include only the portion of the speech that the user heard") [doc].
4. **It all fits on the Pi for free.** Playback is `aplay -D pulse` into PulseAudio with the Jabra as
   the default sink and `pactl` present [live]. A duck is one `pactl set-sink-input-volume` on the
   aplay stream, applied at the mixer so audio already queued is ducked too (no re-plumbing of the
   persistent raw-PCM `aplay`). The decide window is a pure state machine next to the existing
   `_BargeDetector`, testable on the same scripted-mic rig. Jetson RAM delta: **0**. Heard-text
   truncation (A3) is a string operation at the sidecar's `applyPolicies`, where three control
   envelopes are already stripped [src].
5. **Verdict: GO, in three flag-dark phases**, each measured on existing harnesses:
   P1 duck→decide→resume on the Pi (daemon only, `BARGE_DUCK_ENABLED=false`); P2 STT-assisted
   decide for short bursts (`BARGE_DECIDE_STT=false`, measures GPU contention first); P3 heard
   prefix on the wire + sidecar trim (`ZOE_VOICE_HEARD_ENVELOPE=0`, `ZOE_BRAIN_HEARD_TRIM=0`),
   paired with A1. The one real trade is honest: a true interruption is *acknowledged* as fast as
   today (the duck) but *stopped* ~0.7–0.9 s later; Voice-Light and LiveKit accept the same trade.

## 1. The idea and where it sits

P2 in [companion-field-vs-samantha-2026-10-03.md §2](companion-field-vs-samantha-2026-10-03.md):
*"Duck → decide → resume instead of hard cancel. On barge-in, fade Kokoro playback −15 dB over
~450 ms. If the speech stays a backchannel or noise … restore volume and continue. Otherwise cancel
and trim the stored assistant turn to what was played."* It refines three tracked items in
[beat-the-bar-2026-program.md](../architecture/beat-the-bar-2026-program.md): **B1.2** (false
interruption → 2 s timer → resume), **B1.3** (Unmute policy: text-confirmed interrupt at once,
VAD-only needs evidence, MinWords 2–3) and **B1.7** (spoken-text-only context). On the brain side it
depends on **A1** (abort on cancel, flag-dark since 2026-10-03) and *is* **A3** (heard-text
truncation) from [flue-and-agent-runtimes-2026-10-03.md §4](flue-and-agent-runtimes-2026-10-03.md).
This record is the design-grade pass those entries asked for: the exact hook points, the state
machine, the numbers to copy, and the gate.

## 2. Field evidence

| Runtime | Duck or hard stop? | Real interruption vs backchannel | History after the cut | Numbers |
|---|---|---|---|---|
| **Vapi** `stopSpeakingPlan` | Hard stop, then a backoff window | `interruptionPhrases` → instant clear; `acknowledgementPhrases` ("okay", "right", "uh-huh", "yeah", "mm-hmm", "got it") → ignored even past the word gate; else `numWords` (0 = VAD via `voiceSeconds`) | Not documented ("Clear pipeline → backoff → ready") | `numWords` 0 (0–10), `voiceSeconds` 0.2 s (0–0.5), `backoffSeconds` 1.0 (0–10); VAD detect 50–100 ms, transcription-based 200–500 ms [doc 1, 2] |
| **LiveKit Agents** | Pauses speech; **resumes** after a false interruption | `mode` adaptive (cloud audio model) or `vad`; `min_duration` 0.5 s; `min_words` 0 (needs STT); no final transcript within `false_interruption_timeout` → `agent_false_interruption`, resume if `resume_false_interruption` | "automatically truncates its conversation history to include only the portion of the speech that the user heard before interruption" | timeout 2.0 s; adaptive: 86 % precision / 100 % recall at 500 ms overlap, rejects 51 % of VAD barge-ins, median 216 ms of audio to trigger, ≤ 30 ms inference, **cloud-only** [doc 3, 4, 5] |
| **Pipecat 1.0** | Hard stop (`InterruptionFrame`) | `MinWordsUserTurnStartStrategy(min_words)` — "triggers after just 1 word" when the bot is silent; the minimum applies **only while the bot is speaking**; `KrispVivaIPUserTurnStartStrategy(threshold 0.5)` is a licensed interruption-prediction model | Assistant context aggregated from TTS text frames [2nd — 1.0 migration page does not state it] | `enable_interruptions` true; `use_interim` true [doc 6, 7] |
| **OpenAI Realtime** | Hard stop; server cancels the response on VAD (`response.cancelled`) | `server_vad` threshold 0.5, `prefix_padding_ms` 300, `silence_duration_ms` 500; `semantic_vad` eagerness low/medium/high; `interrupt_response` can be turned off | **Client** sends `conversation.item.truncate {item_id, content_index, audio_end_ms}`; the server "cut[s] the audio … and remove[s] the text transcript for the unplayed portion" | defaults as listed [doc 8, 9] |
| **Gemini Live** | Hard stop; `serverContent.interrupted = true` | Server VAD: `startOfSpeechSensitivity`, `endOfSpeechSensitivity`, `prefixPaddingMs` (20), `silenceDurationMs` (~800 server-side) | "the ongoing generation is canceled and discarded. Only the information already sent to the client is retained in the session history"; pending function calls cancelled by id | ≥ 500 ms client silence recommended [doc 10] |
| **Hume EVI** | Hard stop ("stops generating … stops streaming response audio"); client must stop playback and clear queued audio | `min_interruption_ms`: how long the user must speak before EVI yields; higher values "let EVI continue through brief sounds or hesitations" | Not documented | 50–2,000 ms, **default 800 ms** [doc 11, 12] |
| **OVOS** | No listen-while-speaking by default; `hybrid_listen`/`continuous_listen` are experimental ("may cause mycroft to hear its own TTS") | Wake word or VAD (`speech_begin` 0.1 s, `silence_end` 0.5 s, `vad_pre_wake_enabled`) | n/a | **Ducking exists for media, not for TTS**: `tts.pulse_duck` (PulseAudio `media.role=phone`), `tts.ocp_duck` (lower media volume), `tts.ocp_cork` (pause media); amounts unspecified [doc 13, 14] |
| **Voice-Light** (arXiv 2609.20995) | **Reversible duck**: fade toward −15 dB over 450 ms, pause by 500 ms; paused audio resumable for ≤ 800 ms | Causal floor-take adapter on ASR encoder layers; commit at score > 0.82 or after 900 ms sustained speech; a short burst scoring as feedback "resumes the same generation without adding a user turn" | Durable history built "only from audio ranges that the browser confirms it rendered"; speculative work carries generation ids and never enters history until promoted | 450 ms / −15 dB / 500 ms / 0.82 / 900 ms / 800 ms; generation + TTS halt after 350 ms unresolved overlap [doc 15] |

What the table says, in order of certainty [inf]:

- **Nobody mature stops on VAD onset alone any more.** Every runtime with a tunable has an
  evidence gate: words (Vapi, LiveKit, Pipecat), a duration floor (LiveKit 0.5 s, Hume 0.8 s,
  Voice-Light 0.9 s) or an acoustic model (LiveKit adaptive, Krisp VIVA). Zoe's 3-of-6 rule is a
  **240 ms** duration floor — tighter than all of them — and has no word gate.
- **The backchannel rule is lexical when STT exists, otherwise temporal.** Vapi's two phrase lists
  are the cheapest useful version: an *interrupt* list that always wins and an *ack* list that never
  does. Pipecat's scoping (min-words only while the bot speaks) is the right scope for Zoe: outside
  playback the follow-up listener must stay as sensitive as it is.
- **Resume is normal.** LiveKit resumes by default after 2 s of no transcript; Voice-Light resumes
  the same generation. The reversible duck is what makes resume *feel* right: the user gets
  immediate acoustic feedback that Zoe heard them, without Zoe losing her sentence.
- **Truncate-to-heard is the contract, and it is the client's job to know where.** OpenAI makes the
  client send `audio_end_ms`; Gemini keeps "what was sent"; LiveKit keeps "what was heard" from its
  own playout. The side that owns the speaker owns the number. For Zoe that is the Pi daemon.
- **Only OVOS ducks, and only music.** No open self-hosted assistant ducks its *own* TTS on
  overlap; Voice-Light is the published design for it. That is the genuinely new piece P2 adds.

## 3. Our system today — the barge-in path, end to end

### 3.1 Pi daemon (`scripts/setup/zoe_voice_daemon.py`)

- **Knobs** [src :242–292]: `BARGE_IN_ENABLED` (default true), `BARGE_IN_THRESHOLD` (default 0.5,
  **0.75 live** [live]), `BARGE_MIN_CHUNKS`=3 of `BARGE_WINDOW_CHUNKS`=6 (80 ms chunks: ~240 ms of
  speech in ~480 ms), `BARGE_GRACE_MS`=800 after the first write, fast path
  `BARGE_FAST_CHUNKS`=2 consecutive ≥ `BARGE_FAST_PROB`=0.95. All validated at import; a bad value
  keeps the default and warns.
- **Detector** `_BargeDetector` [src :785–867]: pure state machine anchored to playback start
  (`new_playback`), rejects chunks captured before `started_at + grace`, fires **once** per
  playback (`feed` → `reason` "window" or "fast").
- **Fire** `_fire_barge_in` [src :869–880]: logs the window, sets `_barge_in_requested`
  (:873) and calls `proc.terminate()` on the player **directly**, because "the stream loop only
  polls the flag at network-chunk boundaries, which can be seconds away while the brain generates
  the next sentence" (:874–876). This is a hard stop with no reversal path.
- **Two feeders, one detector** [src :900–1040]: `_BargeMonitor` opens its own mic stream for the
  whole turn (the Jabra refuses a second input stream) and scores every chunk; `_barge_in_vad_thread`
  reads the wake stream's `_BARGE_QUEUE`, which is only open outside a turn (announcements).
  **Neither retains audio** — the monitor loop is `read → _vad_prob → feed` and discards the
  chunk (:949–990).
- **Playback** [src :1966–1992]: sentence WAVs are stripped to PCM, silence-trimmed, and written
  to **one persistent `aplay -t raw … -D pulse`** via stdin (`AUDIO_OUTPUT_DEVICE=pulse` [live]).
  A whole sentence (1–4 s of audio) is written per frame, so at any instant up to a sentence sits in
  PulseAudio's stream buffer ahead of the speaker. There is no gain control anywhere in the daemon
  (no `pactl`, `amixer` or software scaling — grep confirms).
- **Stream loop** `_do_single_turn_stream` [src :2082–2320]: checks `_barge_in_requested` only at
  NDJSON line boundaries (:2142–2145) and `break`s. It never calls `r.close()`; the HTTP
  response is released when the function returns (after the aplay drain at :2270–2285), so the
  server-side `GeneratorExit` lands at function exit, and only once the next line arrives if the
  loop is blocked in `iter_lines` [inf]. A barge before first audio returns `True` without
  re-POSTing (:2306–2311, the 2026-07-07 duplicate-write lesson).
- **After the kill** [src :2636–2654]: `voice_command` treats the turn as played
  (`played_audio=True`), plays the follow-up beep and opens `_follow_up_listen` on a **fresh**
  stream that first drains 150 ms (:2459–2465) and keeps a 4-chunk lookback only from then on
  (:2473). **The words the user said over Zoe are gone**; they must speak again after the beep.
- **Cooldown** [src :318, :2667]: `POST_PLAY_COOLDOWN_S` (0.4 live) guards the **wake word**
  only, not barge-in; it is irrelevant to the decide window but bounds how soon a committed
  interruption's *next* turn may re-arm wake.
- **Pinned by** `tests/unit/test_voice_daemon_barge_in.py` (scripted mic + fake clock; grace,
  stale backlog, window/fast path, "real interruption one second in stops within 300 ms", single
  blip never triggers) [src].

### 3.2 zoe-data (`services/zoe-data/routers/voice_tts.py`)

- `voice_turn_stream` [src :4978] → `voice_command(stream=True)` as `sub_task`; the wrapper's
  `finally` cancels `sub_task` on client disconnect (:5405–5411).
- Sentence emission `_emit_sentence` [src :4349–4380]: **appends to `full_reply_parts` before
  synthesis** (:4358, then `_synthesize_kokoro_sidecar` at :4366). So the "heard" record counts
  the sentence being synthesised at cancel time and every sentence already on the wire, whether or
  not the Pi played it.
- Persistence [src :4562–4574]: in the generator's `finally`, `heard_reply = " ".join(full_reply_parts)`
  → `_schedule_voice_chat_save(session_id, "", heard_reply, …)` → `chat_messages` as an
  `assistant` row [src :2905–2935]. Comment at :4562: *"Persist whatever the user actually HEARD"* —
  true at sentence granularity, upper-bounded.
- `_load_voice_history` [src :143] reads the last 3 `chat_messages` rows — but on the Flue lane
  the packet is lazy and **not consumed by the sidecar** (`zoe_flue_client.py` :801–804 [src]).
- There is already a **played-ACK primitive**: `POST /announcements/{id}/played` [src :2217–2235]
  ("`played_at` is what 'heard' means"). Replies have no equivalent.

### 3.3 Flue client (`services/zoe-data/zoe_flue_client.py`)

- A1 [src :323–407]: `ZOE_FLUE_ABORT_ON_CANCEL` (per-call env read, **default OFF**, :339–343)
  adds `x-zoe-abort-on-cancel: 1` to the streaming POST (:1588–1589) and, on
  `GeneratorExit`/`CancelledError` after admission (:1700–1706), schedules a guarded
  `POST …/abort` naming `x-flue-submission-id` (:366–377). The `FLUE_ABORT` log line carries
  `emitted_chars`/`emitted_deltas` (:1633, :396) — a character count of what the *client* received,
  not what was played.
- The stream loop yields deltas as they arrive (:1626–1633); sentence TTS starts during generation.

### 3.4 Sidecar (`labs/flue-zoe-brain-2x/src/`)

- `streaming.ts` [src :627–644]: `abortTurn(reason)` calls `guard.abort(instanceId, submissionId)`;
  `cancel()` on the ReadableStream (:777–788) marks the stream finished and, if opted in, aborts
  with reason `disconnect`. Without the opt-in, "the turn keeps running to completion inside Flue".
- `turn-guard.ts` [src :8–14, :96–125]: Flue aborts per **instance**, so the guard refuses an abort
  whose submission is no longer the latest admission; A5 deadlines `ZOE_FLUE_FIRST_CHUNK_MS`
  (30 000) / `ZOE_FLUE_STALL_MS` (10 000) arm only on opted-in turns (:29–30).
- `providers/capped-completions.ts` `applyPolicies` [src :211–234]: strips the three control
  envelopes (` zoe-spec:`, ` zoe-replay:`, ` zoe-uid:` — outermost first, both parsers `^`-anchored),
  elides stale blocks, windows, strips coding builtins, discloses tools, caps. **This is the A3
  hook**: a fourth envelope on the next user message can drive a wire-only rewrite of the previous
  assistant message, exactly as `elideStaleBlocks` rewrites older user messages (`context-blocks.ts`
  :100–115).

### 3.5 What the stored assistant turn holds after a barge-in today

| Store | A1 off (live) | A1 on | Granularity | Read by the brain? |
|---|---|---|---|---|
| **Flue session store** (`zoe-brain.db`, the brain's own history) | The **whole** reply, "stored as said" ([voice-pipeline.md §Brain side](../knowledge/voice-pipeline.md), live store had 0 aborts in 4,620 submissions) | **Nothing**: "Flue drops the aborted partial. The model sees the interrupted user message and then `<signal type="submission_aborted">Submission was aborted.</signal>` as a user turn" [doc-in-repo] | turn | **Yes** — the only history Gemma sees on the Flue lane |
| **`chat_messages`** (zoe-data) | Sentences emitted up to the disconnect | same | sentence, ≥ played | No (Flue lane); yes for digest/search/legacy lane |
| **Pi daemon** | Knows which sentence chunks it wrote to `aplay` and when; **records nothing** | same | sentence + wall clock | No |

So the unheard tail is **kept** today (A1 off) and the heard head is **lost** with A1 on. Neither
state matches the field contract, and the one party that knows the truth (the Pi) never says it.

## 4. Design — flag-dark, three phases

### 4.1 Phase 1 — duck → decide → resume on the Pi (daemon only)

**Duck mechanism.** The player is an `aplay` client of PulseAudio (`Server Name: pulseaudio`,
default sink = the Jabra Speak 750, `/usr/bin/pactl` present [live]). Right after
`_register_tts_process` the daemon resolves its sink-input index once
(`pactl list short sink-inputs`, filter by the aplay client), then a duck is a single
`pactl set-sink-input-volume <idx> -15dB` and a restore is `… 0dB` (or the cached absolute
volume). Sink-input volume is applied at the mixer, so **audio already queued in the stream is
ducked too** — which is why this beats scaling PCM in Python before `stdin.write` (that could not
touch the up-to-a-sentence already buffered). Not touched: the sink volume (would duck the
AirPlay-2 "Zoe Panel" output and may map to the Jabra's USB hardware gain) and
`play_audio_b64`'s file player (the announcement path gets the same duck because `_fire_barge_in`
is shared). A fade (`BARGE_DUCK_RAMP_MS`, Voice-Light's 450 ms) is two or three steps; v1 ships a
single step and measures whether the Jabra's echo canceller cares. Pause (Voice-Light's 500 ms)
is deferred: `pactl` has no per-stream cork, and `SIGSTOP` on `aplay` under-runs the pulse stream
in a way this pass did not test [unverified].

**Decide window.** The existing detector keeps firing exactly as today; its fire becomes
**stage 1: DUCK** instead of kill. A new `_BargeDecider` (same file, same pure-state-machine
shape, driven from the monitor thread — *not* the stream loop, which can be blocked in
`iter_lines`) then watches the same Silero probabilities:

| Event | Rule (defaults) | Source |
|---|---|---|
| Sustained speech since onset ≥ `BARGE_COMMIT_SPEECH_MS` (900) | **COMMIT** | Voice-Light 900 ms; LiveKit `min_duration` 0.5 s; Hume 800 ms |
| Speech ends (prob < threshold for `BARGE_RESUME_SILENCE_MS` = 400) before the commit budget, STT off | **RESUME** | LiveKit `resume_false_interruption`; Voice-Light non-floor feedback |
| Speech ends early, STT on (Phase 2) | ship the burst → `interruptionPhrases` → COMMIT; ≤ 1 word or `acknowledgementPhrases` → RESUME; ≥ `BARGE_DECIDE_MIN_WORDS` (2) → COMMIT | Vapi order: interrupt list → ack list → `numWords` |
| Nothing resolved within `BARGE_DECIDE_MAX_MS` (2000) | **RESUME** (fail toward Zoe finishing; a real interrupter will keep talking and re-trigger) | LiveKit `false_interruption_timeout` 2.0 s |
| Stream ends (reply finished) while deciding | RESUME is a no-op; retained audio handed to the follow-up listener instead of the beep | — |

From onset the decider **retains mic chunks** (a ring of `FOLLOWUP_LOOKBACK_CHUNKS` before
onset, then everything) so a COMMIT hands the interrupting utterance to the turn as its recording:
the daemon continues recording on the same stream to the normal endpoint and runs `_turn_fn` on it
**without** the follow-up beep. Zoe's dormant LiveKit lane already does this
(`test_voice_barge_in.py::test_flag_on_cooldown_barge_triggers_stop_playback_and_seeds_frames`)
and it is the fix for finding §0.2.

**COMMIT** does what today's fire does — `_barge_in_requested.set()`, `proc.terminate()` — plus an
explicit `r.close()` from the stream loop so the server-side cancel (and A1's abort) lands at the
commit instant rather than at the next NDJSON line. It also records the **played prefix**: the
index of the last sentence chunk whose write time plus duration is ≤ commit time minus the stream
latency (`BARGE_PLAYOUT_LATENCY_MS`, default 100 from the measured "aplay + the Pulse sink add
~70–90 ms" [src :279]) → `(heard_chunks, heard_ms)`. Sentence-level, like the sidecar's TTS
split; good enough for A3 (the Flue deep dive already scoped A3 to sentence alignment).

**RESUME** restores the volume and clears the decider; the stream loop never noticed. The log line
gains `decision=duck|resume|commit t_decide=<ms> speech_ms=<n>`, so the next false fire is
diagnosable from the log alone (the same discipline the 2026-09-28 fix set).

**Flags (all in `.env.voice`, daemon naming, default = today's behaviour):**

| Flag | Default | Meaning |
|---|---|---|
| `BARGE_DUCK_ENABLED` | `false` | off = byte-identical hard stop |
| `BARGE_DUCK_DB` | `-15` | duck depth |
| `BARGE_DUCK_RAMP_MS` | `0` | 0 = single step; 450 = Voice-Light fade |
| `BARGE_COMMIT_SPEECH_MS` | `900` | sustained speech → commit |
| `BARGE_RESUME_SILENCE_MS` | `400` | quiet after a burst → resume (STT off) |
| `BARGE_DECIDE_MAX_MS` | `2000` | ceiling on the ducked window |
| `BARGE_PLAYOUT_LATENCY_MS` | `100` | played-prefix estimate |
| `BARGE_SEED_NEXT_TURN` | `true` (only read when duck is on) | retained audio becomes the next turn, no beep |

### 4.2 Phase 2 — STT-assisted decide (Pi → Jetson, optional)

A short burst (< 900 ms, ended) cannot be told from "uh-huh" acoustically on Silero alone. With
`BARGE_DECIDE_STT=true` the daemon POSTs the retained burst to the existing
`/api/voice/transcribe` (Moonshine, resident — **0 RAM delta**) and applies Vapi's order with two
lists: `BARGE_INTERRUPT_PHRASES` (`stop|wait|hang on|hold on|no|shut up`) and
`BARGE_ACK_PHRASES` (`uh-huh|mm-hmm|mhm|yeah|yes|yep|okay|ok|right|sure|got it|i see`), then
`BARGE_DECIDE_MIN_WORDS` (2). Cost is latency, not memory: the replay baseline puts Moonshine at
~374 ms median for a full utterance; a sub-second burst should be faster [unverified], and the call
can contend with llama-server on the GPU mid-generation — Phase 2's gate measures exactly that
(§6). Pipecat's scoping applies: the word gate exists **only while Zoe is speaking**; the normal
endpointer and follow-up listener are untouched.

### 4.3 Phase 3 — heard prefix on the wire + sidecar trim (A3)

- **Daemon → zoe-data**: on the *next* `/turn_stream` POST the daemon adds
  `prev_interrupted: {heard_chunks, heard_ms, session_turn}` (or, simpler and immediate, a
  `POST /api/voice/turn_stream/heard` ACK mirroring the announcement `played` ACK). zoe-data uses it
  twice: to **correct `chat_messages`** (replace the sentence-level upper bound with the played
  prefix; the digest and transcript search then stop seeing unheard text) and to wrap the next
  brain message with a fourth envelope ` zoe-heard:<chars>` (`ZOE_VOICE_HEARD_ENVELOPE`, default
  off) — the client already knows the chunk→chars mapping because it emitted the sentences.
- **Sidecar** (`ZOE_BRAIN_HEARD_TRIM`, default off): `applyPolicies` strips the envelope first
  (same `^`-anchored, outermost-first pattern as `speculative-turn.ts`) and keeps a per-session
  map `{assistant message index → heard prefix}`. On every later model call it rewrites that
  assistant message on the wire to `<heard prefix> [interrupted here]`. Two cases, one code path:
  A1 **off** → the stored message is the full reply, so this is a trim; A1 **on** → Flue dropped the
  partial, so the policy **inserts** the heard prefix as an assistant message before the
  `submission_aborted` signal turn (and may elide that signal, which carries no information once
  the prefix is there [inf]). The store stays append-only; nothing is written to `zoe-brain.db`.
- **Prompt cache**: the rewritten message is the last assistant turn when first rewritten (only the
  suffix changes) and is byte-stable on every later turn, so `FLUE_PROMPT_CACHE f_keep` should be
  unchanged — that is a measured gate, not an assumption (§6).

## 5. Cost — RAM and latency on both boxes

| | Pi 5 (8 GB, 6.6 GB available [live]) | Jetson Orin NX (RAM-gated, < 0.5 GB free) |
|---|---|---|
| Phase 1 | `pactl` subprocess ~ms and a few MB transient; ring buffer ≤ 2 s × 32 KB/s = 64 KB; no model. Happy path (no barge) **unchanged**: the detector runs today | **0** |
| Phase 2 | one HTTP POST per short burst | **0 RAM** (Moonshine resident); ~300–400 ms of STT work that may overlap llama-server generation — contention is the thing to measure |
| Phase 3 | one extra field per turn | ~0 ms string ops in `applyPolicies`; no store writes; prompt cache stable by design (verify) |

Latency of the user-visible events [inf, to be measured]:

- **Acknowledgement** (Zoe audibly yields): today's fire time (~160–240 ms after speech start, the
  pinned ≤ 300 ms) **+ the pactl call** (tens of ms [unverified on Pi 5]) + Pulse mixer latency
  (tens of ms). About the same as today's silence, perceived as "she's listening".
- **Hard stop on a real interruption**: +900 ms (sustained) or burst-end + 400 ms quiet (STT off),
  or burst-end + STT (~0.4–0.7 s, Phase 2). This is the trade. Voice-Light accepts 900 ms; LiveKit's
  VAD mode waits for a transcript; Hume defaults to 800 ms. Zoe is at −15 dB throughout, which is
  −15 dB louder than today's silence and the user's words are no longer lost.
- **False barge (noise, backchannel)**: today = the reply dies and the user re-asks;
  with duck → a 0.4–2 s dip, then Zoe carries on. That is the win P2 was ranked for.
- **Brain-side**: with A1 on, the abort moves from "next NDJSON line" to the commit instant
  (`r.close()`), freeing the slot sooner for the seeded next turn.

## 6. Measurement plan — on the harnesses that exist

1. **Unit, CI (`ci_safe`)** — extend `tests/unit/test_voice_daemon_barge_in.py`'s scripted-mic +
   fake-clock rig with the decider: (a) 300 ms burst → `duck`, `resume`, player never terminated,
   volume restored (assert the pactl calls through a fake); (b) 1.2 s sustained → duck ≤ t+300 ms,
   commit ≤ t+1.1 s, player terminated, retained audio returned with the 4-chunk lookback;
   (c) burst ends, STT fake says "stop" → commit; "uh-huh" → resume; "yeah I think so" → commit
   (word gate); (d) 2 s unresolved → resume; (e) **flag off → today's path byte-for-byte** (the
   existing "stops playback within 300 ms" test must still pass unchanged). Add the sidecar
   `heard_trim.test.ts` beside `speculative_turn.test.ts`: envelope stripped, trim vs insert,
   stability across two rounds, and a prompt-prefix parity assertion using
   `prompt_cache_prefix.test.ts`'s helper.
2. **Negative controls (break the fix → red):** remove the `BARGE_DUCK_ENABLED` read → (e) must
   go red; remove the envelope parse → the trim test goes red; feed the decider a VAD that returns
   the −1 failure sentinel → it must resume (a broken VAD may never commit); A1 off + trim on must
   still trim (there *is* a full reply to trim), A1 on + trim on must insert.
3. **Lab on the Pi (operator-run, panel on)** — play one 20 s Kokoro reply, inject three sets
   through the room: 10 backchannels, 10 real interruptions, and clips from the corpus quarantine
   folders `quarantine-nonspeech-20260804` and `quarantine-tv-falsewakes-20260719` (non-speech and
   TV false wakes — ready-made negative material; aggregates only, no clip names in results). Read
   `decision=` lines. Targets: false-commit on backchannels/noise ≤ 10 % (LiveKit's adaptive model
   rejects 51 % of VAD false positives; the bar for a temporal+lexical gate is lower, so this is a
   first bar, not a claim); real-interruption commit ≤ 1.1 s from speech onset; resume on noise
   ≥ 90 %; no self-interruption regression (the 2026-09-28 class — the grace is unchanged).
4. **Voice replay gate** — `scripts/maintenance/voice_regression_probe.py` must stay PASS and
   `scripts/perf/measure_voice.py` medians (STT / brain / e2e) must not move: Phases 1–2 do not
   touch its path; Phase 3 is a voice-path change in zoe-data + sidecar and is replay-gated per
   AGENTS.md. The probe's VAD stage is the lock that the Silero loader is unchanged.
5. **Phase 2 contention** — with Phase 2 on, run 20 scripted bursts while the brain is generating
   (the `test_voice_barge_in` corpus + a 1 s-in interrupt, as the A1 record proposed) and compare
   the next turn's `brain_ttft_ms` and the `FLUE_EARLY_TEXT first_delta_ms` distribution with
   Phase 2 off. Ship only if the median shift is < 50 ms [inf threshold, to agree with Jason].
6. **Samantha-bar scenario for A3** — "interrupt mid-answer, then ask 'what were you saying?'"
   (the Flue deep dive's gate): Zoe must resume from the heard prefix, not from the unheard tail,
   and not claim to have said it. Pair it with `FLUE_PROMPT_CACHE f_keep` before/after.
7. **Live count** — after an operator flip, a week of `decision=` lines gives the real
   duck/resume/commit mix; the 2026-09-28 incident runbook's log-reading recipe applies.

## 7. Go/no-go against VISION

| Principle | Verdict |
|---|---|
| 1 Rocks fixed | **Pass.** No model swap; Silero stays the detector, Moonshine is optionally *reused*, Kokoro untouched. |
| 2 Local, private, fast | **Pass.** Everything on the Pi and the box; the burst never leaves the house. Hot path unchanged when nothing barges. |
| 3 Lab-prove, flags off | **Pass.** Five flags, all default = today; Pi rig + panel lab before any flip; Phase 3 replay-gated. |
| 4 Build it to STICK | **Pass.** Unit lanes with negative controls; log line carries the decision; the existing "stops within 300 ms" test is the regression lock for flag-off. |
| 6 Borrow the piece | **Pass.** Voice-Light's reversible duck + Vapi's phrase order + LiveKit's resume-on-no-words + OpenAI's truncate semantics — four pieces, no framework. |
| 8 Voice first | **Pass.** Makes talking over Zoe natural; nothing on the touch surface. |
| 9 Understand first | This record is that pass. Two things it did **not** verify (§8) gate Phase 1's first PR, not the decision. |
| W3 RAM gate | **Pass.** Jetson delta 0 in every phase. |

**GO**, phased: **P1 now** (daemon-only PR, flag-dark, with the Pi rig tests), **P2 after P1's
lab numbers** (it exists to fix whatever false-commit rate P1 shows on short bursts), **P3 with A1**
(flip `ZOE_FLUE_ABORT_ON_CANCEL` and `ZOE_BRAIN_HEARD_TRIM` in the same operator window, because
A1 alone makes the brain *forget* what it said and A3 alone leaves it *overclaiming*). The honest
cost is the ~0.7–0.9 s later hard stop on a true interruption, bought with a −15 dB acknowledgement
at today's speed, resumable false barges, and the interrupting words finally being heard.

## 8. Unknowns (to settle in Phase 1's first PR, none blocks the decision)

- `pactl set-sink-input-volume` round-trip on the Pi 5, and whether the Jabra's Pulse sink applies
  sink-**input** volume in software for the aplay stream (expected, standard Pulse mixing; verify
  with a tone and a mic) [unverified].
- Silero's probability on hums ("mm-hmm") through the Jabra: if hums score < 0.75 they never duck
  (fine); if they score high, Phase 1 commits only after 900 ms, so a hum still resumes [inf].
- Moonshine latency on a 0.3–0.9 s burst, and GPU contention with a generating llama-server
  (Phase 2 gate) [unverified].
- `SIGSTOP`/`SIGCONT` on `aplay` under the pulse plugin as a pause primitive [unverified]; v1 ducks
  only.
- The exact bytes Flue puts in context after an abort were read from
  [voice-pipeline.md](../knowledge/voice-pipeline.md) (2026-10-03, measured then), not re-measured
  here. The sidecar's own `src/` has no abort-time persistence code (the drop happens inside
  `@flue/runtime`, whose installed bundles reference `stopReason` in four files); this pass did
  not trace that path, so "drops the partial" rests on the 2026-10-03 live measurement alone
  [unverified here].
- Pipecat 1.0's assistant-context truncation rule was not found on a primary page this pass [2nd].

## 9. Sources

1. Vapi, Speech configuration — https://docs.vapi.ai/customization/speech-configuration
2. Vapi, Voice pipeline configuration (stopSpeakingPlan order, ranges, latencies) — https://docs.vapi.ai/customization/voice-pipeline-configuration
3. LiveKit, Turn handling tuning (parameters/defaults) — https://docs.livekit.io/agents/logic/turns/tuning/
4. LiveKit, Turns overview (truncation to what was heard, false interruption resume) — https://docs.livekit.io/agents/logic/turns/
5. LiveKit, Adaptive interruption handling (86 % / 100 % / 51 % / 216 ms / ≤ 30 ms, cloud-only) — https://livekit.com/blog/adaptive-interruption-handling ; context blog — https://livekit.com/blog/turn-detection-and-interruption-handling
6. Pipecat, User turn strategies — https://docs.pipecat.ai/api-reference/server/utilities/turn-management/user-turn-strategies
7. Pipecat, Migrating to 1.0 (strategy renames) — https://docs.pipecat.ai/pipecat/migration/migration-1.0
8. OpenAI, Realtime conversations guide (`conversation.item.truncate`) — https://developers.openai.com/api/docs/guides/realtime-conversations.md
9. OpenAI, Realtime VAD guide + server-event reference (server_vad defaults 0.5 / 300 / 500) — https://developers.openai.com/api/docs/guides/realtime-vad.md ; https://developers.openai.com/api/reference/resources/realtime/server-events
10. Google, Gemini Live API guide (interrupted, activity detection) — https://ai.google.dev/gemini-api/docs/live-guide
11. Hume, EVI interruptibility — https://dev.hume.ai/docs/speech-to-speech-evi/features/interruptibility
12. Hume, EVI interruption configuration (`min_interruption_ms` 50–2000, default 800) — https://dev.hume.ai/docs/speech-to-speech-evi/configuration/interruption
13. OpenVoiceOS, ovos-dinkum-listener README (hybrid/continuous listen, VAD keys) — https://github.com/OpenVoiceOS/ovos-dinkum-listener
14. OpenVoiceOS, ovos-config PR #324 (`tts.pulse_duck`, `tts.ocp_duck`, `tts.ocp_cork`) — https://github.com/OpenVoiceOS/ovos-config/pull/324 ; ovos-audio #36 (`media.role=phone`) — https://github.com/OpenVoiceOS/ovos-audio/issues/36
15. Voice-Light (arXiv 2609.20995) — https://arxiv.org/html/2609.20995v1
16. In-repo: [voice-pipeline.md](../knowledge/voice-pipeline.md) §"Panel barge-in" and §"Brain side of a barge-in"; [incident-runbook.md](../knowledge/incident-runbook.md) §12; [flue-and-agent-runtimes-2026-10-03.md](flue-and-agent-runtimes-2026-10-03.md) A1/A3; [companion-field-vs-samantha-2026-10-03.md](companion-field-vs-samantha-2026-10-03.md) P2; [beat-the-bar-2026-program.md](../architecture/beat-the-bar-2026-program.md) B1.2/B1.3/B1.7.
