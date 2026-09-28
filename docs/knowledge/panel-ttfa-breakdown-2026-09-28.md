---
type: Reference
title: Panel time-to-first-audio breakdown (2026-09-28)
description: Measured per-stage breakdown of the Pi panel's time-to-first-audio for ten real turns on 2026-09-28, built by joining the Pi daemon and zoe-data logs and checked with direct Kokoro, STT and Flue-stream measurements. Root cause of the missing ~1 s is a 1000 ms persistence-flush delay in @flue/runtime 2.1.1. Ends with ranked fixes; #1 (speaker-ID off the critical path, #1760) and #3 (early text ahead of the flush, #1761) landed the same evening — see the status column.
tags: [voice, latency, ttfa, panel, flue, moonshine, kokoro, measurement]
timestamp: 2026-09-28T19:05:00+08:00
---

# Panel time-to-first-audio breakdown (2026-09-28)

This is a measurement and a plan. Nothing here is implemented. The voice path it describes
is in [voice-pipeline.md](voice-pipeline.md), and the topology is in
[runtime-topology.md](runtime-topology.md).

## TL;DR

- **The missing ~1 s is not the brain.** `brain_ttft_ms` (309–402 ms) is correct, but it only
  covers the first token. The Flue 2.x sidecar then goes quiet for about a second, and the
  first speakable sentence arrives in one burst. The cause is `CANONICAL_FLUSH_DELAY_MS = 1e3`
  in `@flue/runtime` 2.1.1. The runtime only publishes a `text_delta` to `observe()`
  subscribers after its batch has been written to storage. The first delta goes out at once;
  every later one waits for the 1 s flush timer. Median first token → first speakable unit
  is **1.08 s**. The same mechanism causes the "bursty then stalled" rhythm that
  `voice_cadence_guard.py` and `_pace_delivery` work around.
- **STT runs on the whole clip after the endpoint, at ~0.25 s per second of audio.** The clip
  includes the 1.6 s pre-roll on wake turns and ~0.8–1.0 s of trailing silence. That is why
  longer utterances are slower: 0.27 s for a 1.8 s clip, 2.0–2.2 s for 8–9.6 s clips.
- **The daemon's TTFA clock misses two costs.** It starts after the endpoint tail
  (~0.8–1.0 s) and after a **synchronous speaker-ID shadow score, 0.22–1.12 s** (median
  0.54 s over all turns). That score is thrown away in shadow mode.
- The "0.80 s" short turn was the cached first-turn-of-day **greeting** chunk. That turn's
  real reply reached the Pi 2.38 s after the POST. Every brain turn today had a TTFA of
  2.2 s or more.

## What the daemon's TTFA measures

Source: `scripts/setup/zoe_voice_daemon.py`, `_do_single_turn_stream`. The installed copy on
the Pi matches the repo, except that the repo also has the flag-dark B1.1 block.

- **Start (`t0`)**: just before `requests.post(.../api/voice/turn_stream)`. By then the
  endpoint tail, WAV encode, base64 and `_speaker_claim_for_turn` (resemblyzer) have all
  run, so none of them are in TTFA.
- **End**: the first PCM chunk written to `aplay`'s stdin, after the base64 and WAV decode
  and the leading-silence trim. The PulseAudio and USB start-up comes after this point.
- The `turn_stream TTFA=` line is logged **after playback drains**. Its timestamp is not
  the first-audio time; use `t0 + TTFA`.
- `silence_timeout=1.50s` in the `Recorded …` line is the pre-speech amplitude timeout.
  In VAD mode the tail that actually closes a turn is `VAD_ENDPOINT_SILENCE_S=0.8`, or
  `ZOE_VAD_TAIL_MS=640` of deep quiet. `POST_PLAY_COOLDOWN_S=1.5` only re-arms the wake
  word after the conversation ends. The follow-up window opens ~0.2 s after playback
  drains. Neither is on the TTFA path.

## Method

- **Join.** Pi `voice.log` lines (`Recorded …`, `Speaker ID (shadow)`, `turn_stream TTFA`)
  were joined with the ms-resolution JSON copy of the zoe-data log
  (`~/.zoe-logs/zoe-data.stderr.log`). The joined events are `STT_CAPTURE`,
  `voice/turn_stream STT=`, the `:3579` POST, `VOICE TIMING`, and the Kokoro `/synthesize`
  lines, matched per turn on `request_id`. `zoe-data.app.log` only has whole seconds, so it
  cannot be used for this. The two clocks agree to within 1–3 ms (NTP on both, checked with
  20 ping-pongs over one ssh session). llama-server's own `print_timing` gave the token
  rate.
- **Derived stages.**
  - *upload* = (`STT_CAPTURE` − STT) − `t0`.
  - *first-unit wait* = first Kokoro completion − 0.31 s (the bench synth time) −
    (`:3579` POST + `brain_ttft`).
  - *deliver* = (`t0` + TTFA) − first Kokoro completion.
- **Direct measurements.** All ran under `flock /tmp/zoe-voice-harness.lock`, with no deploy
  run in progress and MemAvailable 0.94–1.2 GB.
  - **Kokoro**: nine unique sentences, warm, direct POSTs to `:10201`, all cache misses.
  - **STT**: today's eleven clips, 3 reps each, through the live
    `/api/voice/transcribe` with `panel_id=replay-harness`. That is the same warm
    `_run_moonshine`, and it does not capture into the corpus. The in-process run was
    tried first, inside a `MemoryMax=650M` scope. The second Moonshine copy was OOM-killed
    inside that scope and the box was not touched, so the in-process path is not safe at
    this headroom.
  - **Flue**: 7 turns on a throwaway `replay-ttfa-probe-*` session, with the
    replay-isolation envelope so no writes happen. Each NDJSON delta was timestamped and
    run through a verbatim copy of `_extract_first_unit`.
  - **Pi output**: `aplay -D pulse` of 0.2 s of silence, 4 reps.
- **STT vs length**: 178 live panel `turn_stream` rows (Aug–Sep) from
  `~/.zoe-voice/voice_stt.jsonl`, plus 1651 replay rows. Duration comes from `audio_bytes`,
  because `audio_duration_seconds` is null on live rows.

## Per-turn timeline

These are the ten turns with audio, 18:24–18:27 AWST. Times are in seconds. "Brain" means
Flue lane with `packet=skipped` and a prompt-cache hit (14–81 prompt tokens). The eleventh
clip, at 18:27:07, returned an empty transcript and produced no audio.

| turn (start) | clip s | kind | spk-ID (outside) | upload | STT | STT→dispatch | brain TTFT | first-unit wait | Kokoro | deliver | **TTFA** |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 18:24:15 | 3.92 | greeting + brain | 0.34 | 0.09 | 0.59 | 0.16 | 0.32 | 0.90 | 0.31 | — | **0.80** (greeting; reply at 2.38) |
| 18:24:25 | 2.24 | brain | 0.23 | 0.09 | 0.42 | 0.05 | 0.31 | 1.13 | 0.31 | 0.04 | **2.35** |
| 18:24:37 | 8.32 | brain, hit 8 s cap | 0.87 | 0.12 | 2.17 | 0.10 | 0.39 | 1.17 | 0.31 | 0.08 | **4.34** |
| 18:24:51 | 1.84 | brain | 0.23 | 0.09 | 0.35 | 0.09 | 0.34 | 1.06 | 0.31 | 0.05 | **2.28** |
| 18:25:02 | 4.88 | scope-held prompt (no brain) | 0.71 | 0.10 | 1.15 | STT→first synth 0.41 | — | — | — | 0.13 | **1.79** |
| 18:25:44 | 9.60 | brain, hit 8 s cap | 1.12 | 0.15 | 2.01 | 0.09 | 0.39 | 1.08 | 0.31 | 0.11 | **4.13** |
| 18:26:06 | 1.84 | brain | 0.22 | 0.10 | 0.27 | 0.08 | 0.38 | 1.01 | 0.31 | 0.03 | **2.18** |
| 18:26:20 | 5.28 | Skybridge fast path | 0.84 | 0.11 | 0.98 | STT→first synth 1.15 | — | — | — | 0.11 | **2.34** |
| 18:26:32 | 3.44 | brain | 0.37 | 0.10 | 0.71 | 0.07 | 0.39 | 1.06 | 0.31 | 0.10 | **2.74** |
| 18:26:46 | 4.80 | brain | 0.73 | 0.11 | 1.39 | 0.10 | 0.40 | 1.28 | 0.31 | 0.13 | **3.72** |

Each brain row sums to its TTFA by construction. The first-unit wait is the residual, and
it absorbs any live Kokoro variance of about ±0.1 s. The Skybridge turn spent 0.52 s between
STT and `voice/command`, then took ~0.6 s to synthesize a one-line reply. That is one
sample and is not investigated here.

## Stage table

The "brain" row set is the seven brain turns without the greeting turn. The speaker-ID row
covers all ten turns.

| stage | median ms | max ms | how measured | confidence |
|---|---|---|---|---|
| Endpoint tail (speech end → recording closed) — **outside TTFA** | ~850 | ~1010 | Trailing-quiet energy estimate on the ten clips (the config floor is 640/800 ms). The two cap-hit clips end mid-speech. | medium-low |
| Speaker-ID shadow score (resemblyzer on the Pi) — **outside TTFA** | 540 (370 brain turns) | 1120 | Pi log: `Recorded` → `Speaker ID (shadow)`, ms timestamps, same clock. Grows with clip length. | high |
| Upload (Pi POST → STT start; base64 + LAN + decode + tempfile) | 99 | 146 | Cross-host join, clock offset ≤ 3 ms | medium-high |
| **STT (Moonshine, whole clip, post-endpoint)** | 710 | 2170 | Server `STT=` log. Reproduced within ±0.1 s by the direct bench on the same clips. Live fit **0.25 s per s of clip** (n=178). | high |
| STT done → brain dispatch (router, identity, scope) | 87 | 100 | Server log (`pre_brain_ms` 22–94) | high |
| Brain TTFT (dispatch → first token) | 386 | 402 | `VOICE TIMING brain_ttft_ms`. llama prompt-eval ~190–220 ms for 17–37 tokens. | high |
| **First token → first speakable unit (Flue 1 s flush)** | 1077 | 1278 | Residual per turn. Probe: first delta alone at 0.26–0.28 s, then **0.84–1.09 s of silence**, then 17–26 deltas within 2 ms, while llama decodes steadily at 20–27 tok/s. | high (mechanism) / medium (per-turn value) |
| Kokoro first-unit synth | 305 (20–55 chars) / 396 (~100 chars) | 411 | Direct `:10201` bench, warm, unique text, cache miss. Fixed cost is ~0.27 s. | high |
| Deliver (server synth done → Pi writes first PCM to aplay) | 84 | 132 | Cross-host residual (b64 WAV is 110–300 KB at 24 kHz) | medium |
| aplay + Pulse sink start (Jabra) — **after the TTFA stamp** | 70–90 | 90 | Pi: 0.2 s silent buffer via `aplay -D pulse`, 4 reps. Sink latency is 64 ms. | medium |
| **TTFA (daemon clock)** | **2740** | **4340** | Pi log | high |
| **End of speech → first sound** (tail + spk-ID + TTFA + sink) | **≈ 4000** | **≈ 6300** | Sum of the above | medium |

### STT scales with clip length, and the clip is padded

| live clip length | 1–2 s | 2–3 s | 3–4 s | 4–5 s | 5–6 s | 8–10 s |
|---|---|---|---|---|---|---|
| median STT (n) | 0.35 (30) | 0.41 (47) | 0.68 (41) | 0.72 (34) | 0.98 (15) | ~2.0 (5) |

Wake-word turns prepend `PREROLL_CHUNKS=20`, which is 1.6 s of audio from before the wake
word. Follow-ups prepend 0.32 s. Every clip also ends with the ~0.8–1.0 s tail. At
0.25 s/s, a typical wake turn spends ~0.5–0.6 s of its STT on non-command audio. The
existing corpus evidence warns against editing the clip: trims regressed as many clips as
they fixed ([voice-pipeline.md](voice-pipeline.md), `_prepare_audio_for_moonshine`).

### The Flue flush, measured directly

Probe turns i=1–6 on a warm session with a prompt-cache hit. Burst times are from the POST.

| turn | first delta | next burst | deltas in burst | first unit ready | llama decode rate |
|---|---|---|---|---|---|
| 1 | 0.258 | 1.098 (whole reply) | 17 | 1.099 | 21.5 tok/s |
| 2 | 0.281 | 1.365 | 21 | 1.365 | 19.3 |
| 3 | 0.260 | 1.353 → 2.390 → 2.922 | 26/21/11 | 1.354 | 22.2 |
| 4 | 0.276 | 1.265 | 26 | 1.266 | 27.4 |
| 5 | 0.284 | 1.372 → 2.413 → 2.792 | 26/23/10 | 1.373 | 24.0 |
| 6 (446-token prefill) | 0.951 | 1.953 | 24 | 1.954 | 25.1 |

Where the flush happens: `node_modules/@flue/runtime/dist/conversation-stream-store-*.mjs`
calls `enqueueCanonical([...assistant_text_delta], () => this.emit({type: "text_delta"}))`.
The emit is the publish callback of a batched storage write.
`sql-agent-execution-store-*.mjs` flushes that batch on a microtask if the last flush
started 1 s or more ago, and otherwise on a `setTimeout(CANONICAL_FLUSH_DELAY_MS = 1e3)`.
`text_end` forces a flush, so a short reply arrives whole at the end of generation. The
sidecar's `src/streaming.ts` pushes each observed event at once, so the sidecar is not the
delay. Its header describes `observe()` as synchronous, and the runtime does not behave
that way.

## Ranked fixes

Savings are per median brain turn and user-perceived: end of speech to first sound. Each one
is tied to a measured number above.

| # | fix | side (file) | expected saving | evidence | risk | status (2026-09-29) |
|---|---|---|---|---|---|---|
| 1 | **Take the speaker-ID shadow score off the critical path.** In shadow mode the claim is discarded (`_speaker_claim_for_turn` returns `None`), yet it runs before the POST. Score it in a background thread after the POST starts, with the same one-row-per-turn JSONL contract. When shadow mode ends and the claim is acted on, send it in a follow-up call instead of blocking the upload. | Pi daemon (`scripts/setup/zoe_voice_daemon.py`) | **0.37 s median** (0.54 s over all turns), up to **1.12 s** on long clips | Pi log, high | Very low. Nothing the server sees changes. The Pi CPU is idle while it waits for the server. | ✅ **landed #1760** (2026-09-28, deployed to the Pi 00:38): scoring runs in a background thread after the POST starts; active mode still scores inline. |
| 2 | **Stream audio to Moonshine during recording.** The live model is already `MEDIUM_STREAMING`, and `moonshine_voice` 0.1.3 has `create_stream` / `add_audio` / `stop`. The server only has to finish the last part at the endpoint, instead of transcribing 2–10 s of padded clip. This needs a chunked upload lane (daemon → zoe-data). The panel uses HTTP `POST /api/voice/turn_stream`, not `/ws/voice/`. | Pi daemon + zoe-data (`routers/voice_tts.py`, new ingest route) | Estimated **~0.4–0.5 s median**, **~1.7–1.9 s** on 8 s turns. The post-endpoint cost becomes roughly constant instead of 0.25 s/s. | STT scaling is measured (high). The finalize latency is **not measured**: the in-process stream bench was OOM-capped at this headroom. | Medium-high. Transport change, and streaming and batch transcripts can differ, so it must be replay-gated on said-vs-did. Measure finalize latency first, when ≥1.5 GB is free. | ⬜ open — pinned in `IDEAS.md` (*Streaming STT during recording*); needs ≥ 1.5 GB quiet headroom and a chunked upload lane. |
| 3 | **Stop the 1 s flush from delaying first audio.** Option (a): in the sidecar's provider wrapper (`src/providers/capped-completions.ts`), tap the model stream's own `text_delta` events and publish them to the turn stream, so publishing no longer waits on persistence. Keep `observe()` for tools, the terminal and dedupe. Option (b): patch `CANONICAL_FLUSH_DELAY_MS` down to ~50–100 ms (more SQLite appends per turn). Option (c): report upstream that `observe()` is gated on persistence. | Flue sidecar (`labs/flue-zoe-brain-2x`) | **~0.3 s median** (0.17–0.82 s), from the probe's first-unit lengths (23–82 chars ≈ 6–20 tokens at the measured ~45 ms/token) vs today's ~1.0 s wait. It also removes the burst cadence. | Root cause in the runtime source, and the probe (high) | Medium. (a) touches the live brain seam and tool-round ordering. (b) edits a third-party dist file, which `labs/AGENTS.md` bump rules must cover. | ✅ **landed #1761** — option (a) as an `instrument()` interceptor tap (`ZOE_FLUE_EARLY_TEXT`, default on). Live sidecar after deploy: `first_sentence_ms` median **749 ms** warm on the 21:26 20-turn probe (min 481; first sentence − first delta 464 ms, was ~1.08 s). |
| 4 | Flip **B1.1 speculative turn-start** (built, flag-dark). The turn fires after 320 ms of deep quiet while recording continues. | Pi daemon + zoe-data (existing) | ~0.3–0.5 s (tail 0.64–0.8 s minus 0.32 s), stacking with #2 | Design note + the tail measured here | Medium. Needs the Pi proof and cancel-rate evidence already in [b1-speculative-turn-start.md](../architecture/b1-speculative-turn-start.md). | ⬜ open — B1.1 still flag-dark; the panel is on since 2026-09-28 evening, so the Pi proof is now possible (tracker §0). |
| 5 | After #3 only: allow a **first-clause first unit** (for example ≥ 24 chars at `,;:`) instead of `_FIRST_UNIT_CLAUSE_MIN = 60`. Before #3 this gains nothing, because the probe shows the text arrives all at once. | zoe-data (`routers/voice_tts.py`) | ~0.2–0.4 s more | Probe token timing | Prosody: every split is a standalone Kokoro utterance, with the pitch reset the docstring warns about. Needs an ear check. | ⬜ open — now unblocked by #3; replay-gated follow-up (tracker B1.x). |
| 6 | Small items. Pre-spawn `aplay` on the first header frame (≤ 0.05 s). Trim Kokoro's baked ~0.4 s lead and tail silence server-side before base64 (~30 % fewer bytes; ≤ 0.05 s of deliver). Shorten the 1.6 s pre-roll (~0.2 s of STT on wake turns, but a known accuracy risk). | Pi / zoe-data | ≤ 0.1 s each | Pi bench / sizes | Low for the first two. Pre-roll needs replay evidence. | ⬜ open. Related and landed: the 8 s cap is now 12 s (#1766, applied on the Pi), so the two cap-hit turns above would not be cut. |

The top three together save about **1.1 s** of a ~4.0 s median end-of-speech → sound. (#1 and #3 landed on 2026-09-28 — #1760, #1761; the stage table above is the BEFORE measurement and has not been re-measured live since.) Adding
#4 brings it to about 1.4–1.6 s. The two stages left are the brain's ~0.39 s TTFT and
Kokoro's ~0.3 s fixed synth cost. Both are rocks, so work on them means optimising around
them, not replacing them.

Voice-path changes (#2–#5) must be replay-gated against `~/.zoe-voice-samples` before merge.
#1 and #6 only touch the daemon's timing, but the fresh live measurement above is the bar
they must beat.

## Side findings

- **Two of ten turns hit the 8 s recording cap mid-speech** (`stop=max_duration`, trailing
  quiet 0.16 s). Those transcripts end mid-sentence. That is a correctness problem as well as
  the two slowest turns. #2 would allow a longer cap without paying for it in STT.
- **Tests write into the live STT audit log.** `/home/zoe/.zoe-voice/voice_stt.jsonl` holds
  5,097 `panel_id=test-panel` rows, including rows written during this session. Live rows
  also log `audio_duration_seconds: null`, so the log cannot relate STT time to clip length
  without deriving it from `audio_bytes`.
- The first-turn-of-day greeting masks first-turn latency in the daemon's TTFA figure. Read
  first-turn TTFAs as greeting latency.

## Re-measuring

1. Pull `^2026-…` lines from the Pi `voice.log`, and the matching window from
   `~/.zoe-logs/zoe-data.stderr.log` (JSON, ms, `request_id`).
2. Per turn, join `Recorded` → `Speaker ID` → `STT_CAPTURE` / `STT=` → `:3579` POST →
   `VOICE TIMING` → first `10201/synthesize` → `t0 + TTFA`.
3. For the Flue burst, stream a replay-isolated probe session and timestamp NDJSON deltas.
   The signature is one early delta, then a ~1 s gap.
4. Run any bench under the harness flock with MemAvailable above 700 MB. Measure STT through
   the live `/api/voice/transcribe` with a `replay-` panel id, not in-process.
