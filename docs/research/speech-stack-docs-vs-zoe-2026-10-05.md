---
type: Research
title: Speech stack vs its own documentation — what the docs say, what Zoe does, what is left (2026-10-05)
description: Read-only, documentation-grounded delta on top of inference-speech-stack-2026-10-03.md. For Moonshine, Kokoro, openWakeWord, Silero VAD, the Pi 5 panel daemon (PyAudio/PortAudio, aplay/Pulse), shairport-sync + nqptp and the Mac virtual panel - parameters set vs documented ranges, documented features unused, documented pitfalls Zoe hits, and the one change per tool that would most improve latency or accuracy with the measurement that proves it. Ends with a Pi 5 best-practice section (thermal, power, audio server) and a ranked gap list. Rocks untouched; nothing run on the Pi or the live services.
tags: [speech, voice, moonshine, kokoro, openwakeword, silero, pi5, shairport-sync, nqptp, portaudio, mac-panel, docs-audit, research]
timestamp: 2026-10-05T21:40:00+08:00
status: research-only - no code, flag, unit or service changed; nothing executed on the Pi; no PR, no commit
---

# Speech stack vs its own documentation (2026-10-05)

Extends [inference-speech-stack-2026-10-03.md](./inference-speech-stack-2026-10-03.md)
(cited below as **"10-03 §n"**). That record measured the live stack and compared it with the field
and with llama.cpp upstream. **This record does not repeat it.** It asks a narrower question: for each
speech tool, is Zoe using the tool the way the tool's own README, docs, source and changelog say to?

**Rocks are fixed** (Moonshine v2 Medium, Kokoro, Gemma 4 E4B+MTP). Nothing here swaps one. Every
recommendation is an option, a call pattern or a hardware condition around a rock.

## Evidence labels

- **[src]** file:line in this repo (worktree `agent-a9119ed1a8b35686e`, branch base `25948580`), or a
  read-only file on the box (venv package, unit file, `~/.cache/zoe/*.json`).
- **[doc]** URL fetched 2026-10-05 (the fetch tool summarises pages with a small model, so quoted
  strings are what it returned; where a page truncated or 404'd I say so and downgrade the claim).
- **[inf]** my inference from the above - not stated by either.
- **[unverified]** could not confirm at a primary source or on the box. **The Pi was not touched**
  (task rule), so every Pi runtime value is cited from an earlier record, not re-measured today.

## 0. Findings in ten lines

1. **The Pi is the weakest link, and it is the documented one.** The 2026-10-04 Pi review recorded
   `get_throttled = 0xe0000` (cap/throttle/soft-limit have each occurred), idle 76-82 C and no fan
   device registered **[src]** `docs/knowledge/log-review-pi-2026-10-04.md:25-26,129-131`. The Pi docs
   start throttling the Arm cores at 80 C and recommend the Active Cooler **[doc]**. Every Pi stage
   (wake, Silero, capture, aplay, speaker-shadow p95 376 ms) runs on that CPU.
2. **Silero on the Pi is fed non-contiguous audio.** `_vad_prob` runs two 512-sample windows per
   1,280-sample chunk and drops the other 256 samples, every chunk **[src]** `scripts/setup/zoe_voice_daemon.py:702-713`.
   Silero is a stateful stream model (64-sample context + GRU state) **[doc]**. The server side hit the
   sibling bug (context) and fixed it with measurement, `services/zoe-data/voice_vad.py:12-29`; the Pi
   copy was never revisited, and the nightly VAD stage tests only the server loader **[src]**
   `scripts/maintenance/voice_regression_probe.py` header.
3. **No `reset_states()` anywhere on the Pi** - one global torch-hub model shared by the endpointer,
   barge, follow-up and ambient threads **[src]** `zoe_voice_daemon.py:671-696`, grep: zero hits. No
   `torch.set_num_threads(1)` (every upstream example sets it) **[doc]**.
4. **Moonshine keyterms are plumbed and OFF**, and the docs now publish a tuning table (default boost
   2.0 removes "about a quarter of the errors on the words you listed for at most a quarter of a
   point on everything else") **[doc]**. 10-03 recorded `keyterm_boost` as undocumented; it is
   documented. `ZOE_MOONSHINE_KEYTERMS` is absent from `services/zoe-data/.env` **[src]**.
5. **Upstream's Moonshine latency table is pessimistic by its own admission** (Linux x86 / Pi 5
   columns "taken before the build-optimization fix ... read pessimistically") **[doc]** - so the
   269 / 802 ms figures 10-03 §2.2 used are upper bounds, and the 0.1.2 build fix (8-15 % faster
   streaming) is already in the live 0.1.3 **[doc]** CHANGELOGS.md.
6. **Kokoro's documented generator is discarded by the sidecar.** `KPipeline.__call__` yields one
   result per `split_pattern` segment **[doc]**; the sidecar concatenates all of them and
   `/synthesize_stream` emits ONE queue item **[src]** `scripts/setup/kokoro_sidecar.py:739-745,865`.
   The endpoint called "stream" does not stream within a unit.
7. **openWakeWord: the documented default backend is TFLite; the Pi runs ONNX**, and the maintainer-
   linked issue says the ONNX mel-spectrogram model emits "a slightly different numerical
   distribution" than TFLite, on which the classifiers were trained **[doc]** issue #336. Zoe's own
   Mac work found the extreme form (scores ~0) **[src]** `scripts/setup/mac_panel/mac_backend.py:45-79`.
   Whether the Pi pays a milder version of that is unmeasured. The live threshold (0.45) is below the
   documented design point (0.5) **[doc]**.
8. **openWakeWord has a documented speaker-specific second stage Zoe does not use** (custom verifier,
   >= 3 positive clips) - exactly the TV-false-wake class the speaker-gate step 1 could not close
   (2 of 5 TV clips accepted) **[src]** `docs/research/speaker-gate-step1-results-2026-10-05.md:30-32`.
9. **PyAudio `exception_on_overflow=False` is set on every read** (the doc default is True)
   **[doc]**, so capture overruns on a throttled Pi are invisible **[src]** `zoe_voice_daemon.py:1416,1460,1845`.
10. Shairport-sync/nqptp: no unit or config for them lives in this repo (`grep` finds docs only), so
    nothing can be compared to the documented settings without reading the Pi; the one doc-vs-box
    mismatch visible from here is that AIRPLAY2.md recommends ALSA or PipeWire backends and Zoe runs
    `-o pulseaudio` **[doc]** + **[src]** memory note `project_panel_airplay_speaker`.

---

## 1. Moonshine v2 Medium (STT, in-process in zoe-data)

### 1.1 What Zoe sets

- `Transcriber(model_path, resolved_arch)` - **no `options`, no `update_interval`, no
  `spelling_model_path`** **[src]** `services/zoe-data/routers/voice_tts.py:2536`. Arch from
  `ZOE_MOONSHINE_ARCH=MEDIUM_STREAMING` **[src]** `services/zoe-data/.env:246`.
- One call shape: `transcribe_without_streaming(audio, sr)` on the whole post-endpoint WAV
  **[src]** `voice_tts.py:2627`, under `_moonshine_infer_lock` (singleton, serial).
- Audio prep: identity for mono 16 kHz (deliberate: per-sample edits regressed clips), linear-interp
  resample only off-rate **[src]** `voice_tts.py:2549-2607`.
- Version `moonshine-voice 0.1.3`, ORT 1.23.2 CPU **[src]** `~/.zoe/venvs/zoe-data-py312/.../moonshine_voice-0.1.3.dist-info`.

### 1.2 Documented options vs what Zoe uses

The installed `libmoonshine.so` exposes these option strings **[src]** (`strings` on
`moonshine_voice/libmoonshine.so`): `vad_threshold`, `vad_window_duration`, `vad_hop_size`,
`vad_look_behind_sample_count`, `vad_max_segment_duration`, `max_tokens_per_second`,
`transcription_interval`, `use_speculative_decoding`, `decode_incomplete_lines`, `keyterms`,
`keyterm_boost`, `context`, `context_max_terms`, `identify_speakers`, `return_audio_data`,
`save_input_wav_path`, `log_api_calls`, `log_ort_run`, `ort_providers`, `skip_transcription`.

| option | documented | Zoe | note |
|---|---|---|---|
| `ort_providers` | "Moonshine runs its models on ONNX Runtime's CPU execution provider on every platform ... a measured decision, not an omission" **[doc]** `docs/execution-providers.md` | CPU (unset) | Matches. Confirms 10-03 §2.2: no GPU route. |
| `keyterms` / `keyterm_boost` | default boost 2.0; 1.0 = "a sixth to a fifth of the term errors for no measurable cost"; 3.0 = "a third ... for between a quarter and six tenths of a point"; "Do not go above 4.0"; 100 unused terms cost ~0.5 pt, 1,000 cost 1 pt; ~1 ms per 100 terms; "Only the streaming architectures support any of this" **[doc]** `docs/models/domain-customization.md` | **OFF** (flag default empty) **[src]** `voice_tts.py:2461-2507` | The live arch is a streaming one, so it is eligible. Plumbing + feature-detect already built. |
| `context` | "a list is more precise than a passage" **[doc]** | unused | Keep to `keyterms`; the list is the better tool. |
| `update_interval` | default 500 ms, "a floor rather than a fixed cadence" **[doc]** `docs/using/transcription.md` | unused (batch call) | Only matters in a streaming lane (10-03 action 4). |
| `use_speculative_decoding` | default true; verifies the previous hypothesis, "Medium Streaming on MacBook Pro from ~103 ms to ~74 ms" **[doc]** CHANGELOGS.md 0.1.1 | n/a on the batch path | Only pays when `update_transcription()` runs during recording (10-03 §2.2 already says so). |
| `decode_incomplete_lines` | default true; false = "encode as audio arrives but wait until the line is complete before decoding" **[doc]** CHANGELOGS.md 0.1.3 | n/a | A knob for a future streaming lane: false trades partials for CPU while the user is still talking. |
| `max_tokens_per_second` | parsed as a float; **no default or semantics published** **[doc]** `core/moonshine-c-api.cpp` | unused | Plausibly a hallucination ceiling on silence/echo clips (Zoe's junk-transcript filter exists for this, `VOICE_IGNORE_TRANSCRIPTS`). **[unverified]** semantics. |
| `vad_threshold` etc. | parsed, no defaults published **[doc]** | unused | The library segments a batch clip into lines with its own internal VAD (Zoe handles the leading "Hey Zoe." line via `_strip_wake_word` **[src]** `voice_tts.py:2630-2640`). **[unverified]** whether the internal VAD ever splits or drops a quiet tail on batch clips; the replay `empty: 2` rows in the 2026-10-05 13:26 run are the first place to look **[src]** `~/.cache/zoe/voice_regression_last.json`. |
| `save_input_wav_path`, `log_api_calls` | the documented way to prove the transcriber "received clean, undistorted audio" **[doc]** `docs/using/debugging.md` | unused | Cheap diagnostic for any future STT regression; Zoe already saves audio at the daemon (`ZOE_VOICE_SAVE_AUDIO`), so this only adds the post-resample view. |

### 1.3 Pitfalls the docs name - does Zoe hit them?

| documented pitfall | Zoe | verdict |
|---|---|---|
| "mono" audio required; arbitrary sample rate handled by the library **[doc]** `transcription.md` | Zoe resamples itself and keeps 16 kHz mono identity **[src]** | Fine. (Doc says the library handles any rate; Zoe's own comment claims it does not resample in `load_wav_file`. The identity fast path means it is never exercised live - no risk, no gain.) |
| Streaming "no longer logs `Memory is empty` or drops hypotheses when short chunks arrive faster than encoder lookahead, including on medium-streaming (#218)" - fixed in 0.1.5 **[doc]** CHANGELOGS.md | Not hit: batch path | Only bites a streaming lane on 0.1.3 (10-03 §2.2 already flags the version dilemma). |
| Upstream latency table stale | 10-03 §2.2 quoted 269 / 802 ms | **Correct that record**: `docs/using/benchmarks.md` says those columns "were last taken before the build-optimization fix ... so they read pessimistically" **[doc]**. Treat 802 ms (Pi 5) / 269 ms (x86) as upper bounds, which makes the Orin streaming figure *better* than 10-03 assumed, not worse. **[inf]** |
| "Latency" definition | "the time interval between when the system detects the user has stopped speaking and when the final transcript reaches the application" via `lastTranscriptionLatencyMs` **[doc]** | Zoe's `stt_ms` (566 ms median, 2026-10-05 13:26) is *full file* decode, not that definition. Do not compare the two directly. |

### 1.4 Documented features Zoe does not use

Streaming events (`LineStarted/Updated/TextChanged/Completed`), `identify_speakers` diarization
(downloaded models, off by default), word timestamps, spelling mode, LoRA fine-tune (`moonshine-voice[lora]`,
0.1.3), and - new in 0.1.5 - Moonshine's own **streaming TTS including a faster Kokoro port** ("4x faster on
short sentences and 2x on long ones on a Raspberry Pi 4 ... uses about 85 MB more memory") **[doc]**
CHANGELOGS.md 0.1.5. That last item is a *same-weights* alternative runtime for the Kokoro rock, on CPU;
B5.1 already measured and rejected ORT-CUDA for quality and speed (10-03 §2.3), and the ORT-CPU/Orin
figure is unmeasured. Not a recommendation - an unmeasured candidate. Also: 0.1.5 is held for the
decoder-step regression (10-03 §1.5), so this is moot until that is resolved.

### 1.5 The one thing that would help most: turn keyterms on (accuracy)

Latency's best lever remains streaming STT (10-03 action 4) - not repeated. The **accuracy** lever the
docs now quantify is `keyterms` at boost 1.0-2.0 with a short household vocabulary (room names, device
names, family names, the wake phrase variants). Per the docs: removes ~a quarter of term errors at 2.0,
~0.1-0.2 of that at 1.0, ~1 ms of cost per 100 terms, installed once.

- **Why it fits:** the failures that hurt "Samantha" feel are proper nouns the decoder "would otherwise be
  unlikely to produce" **[doc]**; the docs say it "cannot help with a new accent, dialect or recording
  environment" **[doc]** so it will not fix far-field noise.
- **Risk:** Zoe's own corpus work found Moonshine "extremely sensitive to *any* per-sample edit"
  **[src]** `voice_tts.py:2556-2562`; a decoder bias is a different perturbation, so assume it can flip
  a clean clip until the replay says otherwise.
- **Proof:** `scripts/maintenance/voice_regression_probe.py` replay of `~/.zoe-voice-samples` (1,318 files
  **[src]** `ls`), WITHOUT then WITH `ZOE_MOONSHINE_KEYTERMS`, same session, Kokoro paused for headroom.
  Gate: said-vs-did regressions = 0, `ok` not below the 18/20 of the 2026-10-05 13:26 run, and a
  per-term error count on a labelled subset (the clips whose reference text contains a listed term).
  This is the sequence `docs/knowledge/moonshine-0-1-5-upgrade.md` §4 already prescribes - the only new
  input is the documented boost table to choose the first value (start at 1.0, not the default 2.0).
- Effort: XS (env var + zoe-data restart, authorised). Impact: accuracy on named entities.

---

## 2. Kokoro TTS (sidecar, own venv)

### 2.1 What Zoe sets

`KPipeline(lang_code="a", repo_id=..., device="cuda")` **[src]** `kokoro_sidecar.py:522`; voice `af_sky`
(a single stock voice, env `KOKORO_VOICE`) **[src]** `:62`; `speed` passed through, cache only at 1.0
**[src]** `:808`; `_pipeline(text, voice=voice, speed=speed)` - **no `split_pattern`, no callable speed,
no voice blend** **[src]** `:739`; `torch.cuda.empty_cache()` before every inference **[src]** `:737`;
24 kHz output **[src]** `:63`.

### 2.2 Documented behaviour vs use

| doc statement | Zoe |
|---|---|
| `__call__(text, voice=None, speed=1, split_pattern=r'\n+', model=None)`; the generator yields `(graphemes, phonemes, audio)` per segment **[doc]** README + `kokoro/pipeline.py` | The sidecar iterates the generator, **collects every segment, `torch.cat`s, wraps one WAV** **[src]** `:739-745`. First audio therefore waits for the *whole* unit. |
| English text is chunked to a 510-phoneme limit by `en_tokenize`, "waterfall" breaking at `! . ? : ; ,` and brackets/quotes; past the limit the string is truncated with a warning **[doc]** `pipeline.py` | Zoe's upstream hands one sentence/clause (`_extract_first_unit`, `voice_tts.py:1088`, clause min 60 chars). A >510-phoneme unit would be split by Kokoro itself; the sidecar then still concatenates. No truncation risk seen; **[unverified]** whether any live reply unit exceeds 510 phonemes (long list readbacks). |
| Voices: "comma-separated: `af_bella,af_jessica`" are stacked and the **mean** taken **[doc]** `pipeline.py` | Single voice. Blending is a *voice-design* option (warmer/cleaner af_sky), not a latency one. Only worth a listening A/B; the phrase cache keys on voice so it self-invalidates **[src]** `kokoro_sidecar.py:83-90`. |
| `speed` accepts a float or a **callable of phoneme count** returning a per-chunk speed **[doc]** | Unused. A callable could slow very short interjections ("Okay.") a touch and speed long ones - prosody polish only. **[unverified]** benefit. |
| `model=` lets one `KModel` serve several pipelines **[doc]** | n/a (one language). |
| Output "24,000 Hz" **[doc]** | Matches `_SAMPLE_RATE` **[src]**; the daemon plays at 24 kHz via `aplay -r 24000` **[src]** `zoe_voice_daemon.py:424`. PulseAudio then resamples to the sink rate with its default `resample-method = speex-float-1` **[doc]** `pulse-daemon.conf(5)`. Whether the Jabra sink runs at 16/44.1/48 kHz is **[unverified]** (Pi untouched); the cost is CPU on the throttling Pi, not latency, unless the CPU is capped. |

### 2.3 Pitfalls hit

- **Streaming that isn't.** `/synthesize_stream` docstring promises "first audio ... long before the full
  reply is done" but `_produce()` puts exactly one item **[src]** `kokoro_sidecar.py:836-875`. It is
  correct *because* callers send one short unit - which is also why the per-call fixed cost (~0.27 s,
  10-03 §1.6) is paid once per sentence **and** would be paid again per clause if 10-03 action 6
  (shorter first unit at the router) is done as written.
- Dormant upstream (kokoro/misaki 0.9.4, last commits Aug 2025) is in 10-03 §2.3; the docs add no new
  guidance and no fp16/compile advice. `espeak-ng` is the documented G2P fallback **[doc]** README;
  **[unverified]** that it is installed in `kokoro-py310` (an out-of-vocabulary word without it
  falls to misaki's own fallback or is dropped silently).

### 2.4 The one thing: do the clause split INSIDE the sidecar, as the generator intends

Set `split_pattern` to a punctuation regex (`(?<=[,;:.!?])\s+`, i.e. the same boundaries Kokoro's own
waterfall prefers) and have `/synthesize_stream` push each `result.audio` as it is produced instead of
after `torch.cat`. Net effect for a 60-120 character first unit: first audio after the **first clause's
forward pass** while the fixed per-call cost (allocation, voice load, G2P setup, the `empty_cache`) is
paid **once**, not once per router-level clause. This is a refinement of 10-03 action 6, not a duplicate:
that row moves the split to the router (more HTTP calls, more fixed costs, more seams); this moves it
to where the library already supports it.

- **Proof:** extend `scripts/perf/measure_tts.py` (times sidecar synthesis on a complete reply, 10-03
  §3 row 6) to record time-to-first-byte on `/synthesize_stream` for 20 stored replies of 60-200 chars,
  ABAB against today's single-chunk behaviour; plus a listening A/B for prosody seams (Kokoro
  conditions each segment independently - the same caveat 10-03 gave row 6). Gate: median TTFB for
  >= 60-char units down by >= 100 ms with no clip/click at joins (check with `aplay` of the
  concatenated stream, not just the per-chunk WAVs).
- Effort: S (sidecar only; no zoe-data change if the endpoint stays binary-compatible, since callers
  already read raw PCM). Pairs with the existing `_feed_pcm_chunk` gapless player **[src]** `zoe_voice_daemon.py:2509`.

---

## 3. openWakeWord (`hey_zoe.onnx`, Pi daemon)

### 3.1 What Zoe sets

`Model(wakeword_models=[hey_zoe.onnx], inference_framework="onnx"[, enable_speex_noise_suppression=True
if `speexdsp_ns` imports])` **[src]** `zoe_voice_daemon.py:3481-3503`; framework is hard-wired `"onnx"` on the
Pi **[src]** `:432-433`. No `vad_threshold`, no `custom_verifier_models`, no `patience`/`debounce_time`
(a code comment says debounce "was crashing the daemon" **[src]** `:500-501`). 1,280-sample int16 chunks
**[src]** `:134,3674`. Score gate: own state machine - `mx >= WAKEWORD_THRESHOLD` N times within a window
(code defaults 0.28 / 2 hits / 0.8 s **[src]** `:240,348-349`; live 0.45 / 3 in 1.0 s **[src]** 10-03 §1.7),
re-trigger guard 3 s, `oww.reset()` after each turn **[src]** `:3235`. `speexdsp-ns` is commented out of
`pi-requirements.txt` **[src]** `scripts/setup/pi-requirements.txt:21`, so the Pi most likely runs
without it **[unverified]** (the import is guarded).

### 3.2 Parameters vs documented ranges

| parameter | documented | Zoe | verdict |
|---|---|---|---|
| frame size | "multiples of 80 ms" (1,280 samples), 16-bit 16 kHz PCM **[doc]** README | 1,280 int16 **[src]** | Exactly right. |
| threshold | "trained to work well with a default threshold of 0.5 ... determine the best for your environment" **[doc]** | 0.45 live; 0.28 code default; near-miss logging between 0.18 and the threshold **[src]** `:3688` | Lower than the design point. A model that needs 0.45 (or 0.28) to fire is scoring low on the owner's voice; see 3.3 (ONNX distribution). |
| `patience` | `predict(..., patience={model: N}, threshold={model: t})`: N **consecutive** 80 ms frames above threshold; `threshold` is **required** when `patience` or `debounce_time` is passed; `debounce_time` is incompatible with `patience` **[doc]** `model.py` docstring | Home-grown "N hits in a window" (not consecutive). The "crash" in the code comment is the documented requirement for the `threshold` dict, not a library bug **[inf]**. | Zoe's rule is looser than `patience` (non-consecutive hits count). Not wrong; but it can be satisfied by two separate noise bursts inside 1 s. A measurable difference, see 3.4. |
| `vad_threshold` | Silero gate: a positive is zeroed unless the max VAD score over the 3 frames from 0.4-0.56 s earlier clears the threshold; default 0 (off) **[doc]** `model.py` | off | Kills non-speech false accepts (door slam, clatter). Does **not** help the TV class (TV is speech). Needs the oww-bundled Silero ONNX, extra CPU per frame. |
| `enable_speex_noise_suppression` | "very lightweight", for stationary noise, X86/Arm64 Linux **[doc]** | guarded; likely not installed | Jabra Speak 750 does AEC in hardware (10-03 §2.4); stationary-noise NS is a small, separate gain. |
| `custom_verifier_models` + `custom_verifier_threshold` (default 0.1) | speaker-specific logistic-regression second stage; **>= 3 positive clips per speaker**, >= 10 s of that speaker's non-wake speech, ~5 s of room noise; "only works well when deployment acoustic environments match training conditions" **[doc]** `docs/custom_verifier_models.md` | unused | See 3.4. |
| `inference_framework` | `"tflite"` is the default and "preferred for efficiency"; on Linux both runtimes install; `ncpu` default 1 for ORT sessions **[doc]** `model.py`, `utils.py` | `"onnx"` | See 3.3. |
| false-reject / false-accept targets | "< 5 % false-reject and < 0.5 per hour false-accept with appropriate threshold tuning" **[doc]** README | Zoe has **no measured wake FRR/FAR** (only near-miss log lines) **[src]** `:3688-3691` | The documented acceptance criterion is not being measured. |

### 3.3 Pitfall: ONNX features vs TFLite-trained classifiers

openWakeWord issue #336: "The mel spectrogram ONNX model outputs values in a slightly different
numerical distribution than the TFLite model", so embeddings fall outside the classifier's training
distribution; observed as near-zero scores on macOS ARM64 **[doc]**. Zoe's Mac backend carries this fact
(it forces TFLite there, `mac_backend.py:45-79,405-418`) - but the Pi, an aarch64 Linux box, runs the
**same ONNX path** and is documented nowhere as validated. I cannot show the Pi's severity from here:
issue #336 is macOS-specific and a search for Pi/aarch64 ONNX-vs-TFLite issues returned nothing
**[doc]**. **[inf]** it is *plausible* that the Pi's low-ish operating threshold (0.45 vs 0.5; code
default 0.28) is a mild form of the same effect. **[unverified]**.

Pi 5 can run TFLite: openWakeWord installs `tflite-runtime` on Linux **[doc]** README, and PyPI 0.6.0
still uses it (the repo tree now loads `ai_edge_litert` **[doc]** `utils.py`; Zoe's Mac shim already
registers either name **[src]** `mac_backend.py:44-79`). The blocker is the artifact: a `.tflite` build
of `hey_zoe` "is not known to this repo" **[src]** `docs/knowledge/mac-virtual-panel.md:81`.

### 3.4 The one thing: measure FRR/FAR with the documented tools, then add the custom verifier

Two steps, in order, because step 1 decides whether step 2 is needed:

1. **Instrument.** Replay the corpus's wake audio through the Pi's exact Model (ONNX, same threshold
   logic) offline and report (a) score distribution on owner "Hey Zoe" clips, (b) FRR at the live
   threshold, (c) FAR/hour on the corpus's non-wake speech + the five TV false-wake clips
   **[src]** `speaker-gate-step1-results-2026-10-05.md:30-32`, (d) the same under TFLite *if* a
   `hey_zoe.tflite` exists or can be exported from the training notebook **[unverified]** that the
   notebook still exports TFLite. This is the first wake measurement Zoe would have. Runs on the Orin
   (CPU, small) - no Pi change.
2. **Custom verifier.** Train `train_custom_verifier` from >= 3 owner "Hey Zoe" clips (the corpus has
   hundreds), >= 10 s of owner non-wake speech, ~5 s of room noise, ship the pickle next to
   `hey_zoe.onnx`, load via `custom_verifier_models={"hey_zoe": path}`. It is a *wake-time* speaker
   filter that costs nothing on the turn path - unlike the CAM++ gate, which scores the whole clip
   after wake and, in step 1, accepted 34/50 near-silence clips and 2/5 TV clips **[src]**
   `speaker-gate-step1-results-2026-10-05.md:33-36`. Caveat from the docs: it assumes the deployment
   acoustics match the training clips - the corpus *is* the deployment audio.
- **Gate:** TV false-wake clips 0/5 accepted at owner FRR < 10 %; no rise in the corpus FAR; adds
  < 5 ms per frame on the Pi (measure with `predict(..., timing=True)` **[doc]**). Pi deploy is an
  operator step (`deploy-pi-voice.sh`, memory `reference_panel_voice_deploy`).
- Effort: M (data prep + training + Pi deploy). Impact: fewer TV/other-speaker wakes; speaker-specific
  security is explicitly *not* claimed by the docs ("reducing false positives").

---

## 4. Silero VAD (Pi daemon; server has its own ONNX copy)

### 4.1 What Zoe sets

- Pi: `torch.hub.load("snakers4/silero-vad", "silero_vad", force_reload=False, trust_repo=True)` -
  the JIT model, cached 2026-04-14 (v6.x) **[src]** `zoe_voice_daemon.py:677-696` + 10-03 §1.7. One
  global instance shared across threads (endpointer, barge, follow-up, ambient). `_vad_prob` takes the
  max over 512-sample windows of each 1,280-sample chunk **[src]** `:699-723`.
- Endpointing thresholds: speech >= 0.35 **[src]** `:150`, deep-quiet < 0.10 **[src]** `:222`,
  tail 640 ms deep / 800 ms ambiguous **[src]** `:149,170` (live: 10-03 §1.7), barge 0.75 live
  **[src]** `:296`. Server: `voice_vad.SileroVAD`, ONNX v6.0, 64-sample context, per-stream state +
  `reset()` **[src]** `services/zoe-data/voice_vad.py:12-29,125-141`.

### 4.2 Parameters vs documented

| parameter | documented (`get_speech_timestamps` / `VADIterator`) | Zoe | verdict |
|---|---|---|---|
| `threshold` | 0.5 default **[doc]** `utils_vad.py` | 0.35 (endpoint), 0.4 (ambient), 0.35 (follow-up), 0.75 (barge) | 0.35 is deliberately sensitive for end-of-speech detection; fine. |
| `neg_threshold` | defaults to `max(threshold - 0.15, 0.01)` - a hysteresis band **[doc]** | endpoint: `deep` < 0.10 plus the ambiguous band 0.10-0.35 **[src]** `:222-239` | Zoe's band is *wider* than the library's (0.25 vs 0.15) and its "deep" criterion is tighter; it is the library's idea implemented by hand and tuned on the corpus (the 640 ms tail). Right shape. |
| `min_silence_duration_ms` | default 100 **[doc]** | 640 ms deep / 800 ms ambiguous | Much longer - appropriate for a *turn* endpoint (the default is for segmenting audio, not ending a turn). Not a defect; do not "tune toward" 100. |
| `speech_pad_ms` | default 30 **[doc]** | none; the recorded clip includes all quiet up to the endpoint | The ~700-880 ms of tail in every clip is what 10-03 action 2 attacks. |
| window | "512 samples at 16 kHz, 256 at 8 kHz" **[doc]**; the model keeps a 64-sample context + state **[doc]** `OnnxWrapper` | **1,280-sample chunks, 2 windows scored, 256 samples discarded per chunk** **[src]** `:708` | **Pitfall, below.** |
| threads | examples call `torch.set_num_threads(1)`; ORT session `inter/intra_op_num_threads = 1`; "one audio chunk (30+ ms) takes less than 1 ms ... on a single CPU thread" **[doc]** | torch default (Pi 5: 4 intra-op threads) **[src]** grep: no `set_num_threads`; **[inf]** | For a ~1 ms op the thread-pool handoff can cost more than the op, and steals cores from oww/aplay while throttled. **[unverified]** magnitude. |
| `reset_states()` | Present on the model and called in `VADIterator.__init__`; it zeros `_state` and `_context` **[doc]** `utils_vad.py` | **never called** **[src]** | The GRU state carries from the end of one turn / barge session into the next. Probably small (the model decays through silence) but it means the first chunks of a turn are scored with another stream's state. **[unverified]** magnitude. |
| `onnx=True` | supported; "ONNX implementations may achieve 4-5x faster execution under optimal conditions" **[doc]** README | torch JIT on the Pi | Pi has `onnxruntime` already (oww needs it). The server's copy proves the ONNX loader works in this repo. |

### 4.3 Pitfall hit: discarded samples (and an uncovered path)

Per chunk: windows at [0:512] and [512:1024]; [1024:1280] is never scored; the next call starts at 1,280.
So the model's recurrent context and 64-sample context are stitched across a 256-sample gap every
two windows - 20 % of the signal is skipped, and every Silero consumer on the Pi (endpoint, barge,
follow-up, ambient) uses it. For comparison, the server's sibling bug (missing 64-sample context) was
measured: v6.0 median speech-hop fraction 0.09 vs 0.36 and detection lag 128 ms vs 0 ms, v6.2.1 detected
nothing (0/44 vs 42/44 clips) **[src]** `voice_vad.py:12-29`. The torch-hub JIT model handles the 64-sample
context internally **[inf]**, so the Pi bug is the *gap*, not a missing context - the magnitude is
**[unverified]**, but it is the same class of fault and the nightly probe cannot see it: the VAD stage
runs "the service's REAL `voice_vad.SileroVAD`", not the Pi's `_vad_prob` **[src]**
`scripts/maintenance/voice_regression_probe.py` module docstring.

### 4.4 The one thing: make the Pi's VAD stream contiguous, and give it a gate

Carry the 256-sample remainder across chunks (buffer exactly as `voice_vad.SileroVAD.process_hops`
already does, which "buffers arbitrary-size frames internally"), `reset_states()` at turn start and when
the barge monitor opens, `torch.set_num_threads(1)` once - or reuse `voice_vad.SileroVAD` itself on the
Pi (onnxruntime is already a Pi requirement; ~2.3 MB model).
- **Proof (offline, no Pi change):** run the corpus (1,318 files) through (a) the Pi's current
  `_vad_prob` pattern and (b) contiguous 512 hops with the same model; compare per-clip max prob,
  speech-onset lag, and **the endpoint decision the `_Endpointer` would make** (`tail_rule`, close time)
  at the live thresholds. Then add (a) as a second VAD stage in the nightly probe so the Pi path has a
  gate at all. Live proof: the BARGE_DECIDE ledger (`outcome=commit|resume|ceiling`, `speech_ms`,
  `heard_chunks`) **[src]** `zoe_voice_daemon.py:1348` and `mac_panel/barge_lab_summary.py` - commit
  precision and the endpoint close time (720-880 ms today, 10-03 §1.7) before/after.
- Effort: S. Pi deploy (voice-path file: replay-gated, `voice_gate_check.py`). Impact: endpoint/barge
  accuracy; a win only if the offline diff shows it - otherwise the result is "the gap was harmless,
  now proven", which is worth having.

---

## 5. The Pi 5 panel daemon: PortAudio / PyAudio, aplay / Pulse, barge, announce poller

### 5.1 Capture (PyAudio over PortAudio)

- `stream.read(CHUNK_SIZE, exception_on_overflow=False)` everywhere **[src]** `:1416,1460,1845,3031,3060`;
  PyAudio's default is `exception_on_overflow=True` **[doc]** PyAudio docs. With False, an input overrun
  (CPU starved: throttled Pi, Silero + oww + ORT in one process) returns silently-short or stale audio.
  The wake stream is kept open and reused for the command so there is no re-open gap **[src]** `:3711-3725`
  - a good design - but there is no overflow signal at all. Fix = wrap in `try/except IOError`, count,
  and log once a minute while behaving as today. XS, no behaviour change, and it would have shown whether
  the 2026-10-04 throttling coincided with dropped frames.
- Stream opens at 16 kHz mono 1,280 frames on every platform **[src]** `:3588-3597`. PortAudio "doesn't
  provide sample rate conversion if you request a sample rate that is not supported by the native audio
  API" **[doc]**; on ALSA/Jabra 16 kHz is native (USB headset class) - fine; on macOS CoreAudio the host
  API itself converts (default `paMacCoreConversionQualityMax`) **[doc]** `pa_mac_core.h`, so the
  Mac's 16 kHz request works without a fallback, whereas the Mac *output* path has an explicit
  reopen-at-device-rate fallback **[src]** `mac_backend.py:263-290`. **[inf]**, consistent.
- Blocking read vs callback: docs describe read/write as "safe to call in a tight loop" **[doc]**; Zoe
  uses blocking reads on the main thread plus queues to consumers - matches.

### 5.2 Playback (aplay / PulseAudio)

- Persistent `aplay -q -t raw -f S16_LE -c 1 -r 24000 [-D dev]` fed raw PCM for gapless sentences
  **[src]** `:424,2509-2536`; no `--buffer-time` / `--period-time`. PulseAudio defaults to 4 fragments of
  25 ms (a 100 ms buffer) and `resample-method = speex-float-1` **[doc]** `pulse-daemon.conf(5)`;
  measured sink latency 64 ms and aplay+sink start 70-90 ms **[src]** 10-03 §2.4.
- `module-suspend-on-idle` default timeout is **5 s** **[doc]** `module-suspend-on-idle.c`
  (`uint32_t timeout = 5;`, argument `timeout=<s>`) - this upgrades 10-03 row 8's claim from "loaded"
  to a documented number; the suspended-vs-running first-reply cost is still unmeasured.
- Bookworm's default is PipeWire ("PipeWire replaces PulseAudio ... can be controlled with any
  application which controls PulseAudio") **[doc]** raspberrypi.com news. The Pi runs `pulseaudio` plus a
  `pw-headless3` user unit **[src]** `log-review-pi-2026-10-04.md:28`. Two audio servers on one box is
  allowed by the docs' compatibility statement but is **[unverified]** as a source of device-hold
  contention; the log review found no errors, so leave it.
- Barge-in duck uses `pactl set-sink-input-volume` on the *player's sink-input*, not the sink
  **[src]** `:1127-1200` - the right target when shairport-sync shares the sink (the duck must not duck
  music), and the Mac mirrors it with an in-process gain **[src]** `mac_backend.py:21-40`.

### 5.3 The one thing (daemon)

Add the capture-overrun counter (5.1) and log `vcgencmd get_throttled` / `measure_temp` on each turn's
timing line (a `subprocess` call, ~ms, or read `/sys/class/thermal/thermal_zone0/temp`). Together they
turn "the Pi is probably throttling during turns" (today **[inf]**) into a per-turn fact in the existing
log, with zero behaviour change and no model change. Proof = two weeks of lines correlated with the
stage timings (`TTFA`, endpoint close time) the daemon already logs.

---

## 6. shairport-sync + nqptp (AirPlay 2 "Zoe Panel")

No unit, conf or install script for either lives in this repo - only docs and the picker code
(`grep -rIl 'shairport\|nqptp'` finds knowledge docs, `routers/music.py` and its tests). The live args
are known only from memory (`shairport-sync 5.1`, `-o pulseaudio -a "Zoe Panel"`, nqptp enabled,
`zoe-airplay` user unit, linger on) **[src]** memory `project_panel_airplay_speaker`. So this section
compares the docs to what is *knowable*, and lists what to read on the Pi (read-only) to finish it.

| documented requirement | status |
|---|---|
| nqptp needs "exclusive access to ports 319 and 320 ... cannot coexist with ... full PTP service daemons" **[doc]** nqptp README / AIRPLAY2.md | Pi: nqptp `active`, `NRestarts=0` **[src]** `log-review-pi-2026-10-04.md:28`. Check `ss -ulpn | grep -E ':31[9]|:320'` shows only nqptp **[unverified]**. |
| After (re)starting nqptp, "restart Shairport Sync" **[doc]** nqptp README | If nqptp is ever restarted by the boot ordering/OOM, shairport must follow. A `PartOf=`/`BindsTo=` between the two units would encode it. **[unverified]** whether the live units do. |
| "Audio backends: direct ALSA recommended for best quality; PipeWire compatible"; "does not work well on virtual machines outputting to ALSA, PipeWire or PulseAudio" (timing) **[doc]** AIRPLAY2.md | Zoe uses the **pulse** backend (so music, TTS and barge-in duck share one mixer - the design reason). Docs do not forbid it; the shairport config documents a `pa{}` section (`application_name`, `sink`) **[doc]** `scripts/shairport-sync.conf`. A hardware-direct ALSA path would forfeit the shared mixer, so the pulse choice is right *for Zoe*; the doc trade-off is sync quality. |
| "audio_backend_buffer_desired_length_in_seconds 0.2"; underflow fix = raise it ("`19845` as triple the default for the ALSA backend ... resolves dropout problems on a Pi Zero") **[doc]** conf + TROUBLESHOOTING.md | Zero journal lines in the 2026-10-04 window (`log-review-pi-2026-10-04.md:57`) so no evidence of underflow. But the Pi throttles; if music ever glitches that is the first knob. |
| TROUBLESHOOTING: Wi-Fi power-save "is bad as the device needs to be always connected"; USB DAC stutter from shared USB bandwidth/interrupts **[doc]** | `iw dev wlan0 get power_save` and the Jabra+PanaCast USB sharing (section 8) are the two relevant checks. **[unverified]** whether the panel is on Wi-Fi. |
| Latency: "The delay is usually around two seconds" for realtime AirPlay 2 **[doc]** | Matches `panel-as-ma-speaker-feasibility.md` ("~2 s buffering"); relevant only to MA music, never to voice. |
| Hooks `run_this_before_play_begins` / `run_this_after_play_ends` (+ metadata/MQTT) **[doc]** conf | **Unused feature.** They could tell the daemon "music is playing" (to raise the wake/barge thresholds against music bleed, or lower music volume at wake). Idea only; the Jabra's hardware AEC + `_ignore_wake_until` are the current mechanisms. **[unverified]** value. |
| `disable_standby_mode`, `volume_range_db` 60, `default_airplay_volume -24` **[doc]** | Defaults; nothing to change absent a complaint. |

**One thing:** none for latency (it is not on the voice path). For accuracy-of-experience: a unit
ordering guard (nqptp -> shairport) and a one-time read-only audit of `/etc/shairport-sync.conf` against
this table, recorded in `docs/knowledge/`. Proof: restart nqptp on a spare moment and watch shairport
re-sync, vs today's unknown.

---

## 7. The Mac virtual panel (PR #1865)

- **Wake word:** forces TFLite via a `tflite_runtime` shim because ONNX scores ~0 on macOS ARM64 -
  matches issue #336 exactly **[src]** `mac_backend.py:45-79,405-418` + **[doc]** issue. Note the issue is
  **still open with no maintainer response** **[doc]**, and the openWakeWord tree moved from
  `tflite_runtime` to `ai_edge_litert` **[doc]** `utils.py`; the shim registering either under the old name
  is the right hedge. Gap: `hey_zoe.tflite` does not exist in the repo, so the Mac default is
  `hey_jarvis` **[src]** `mac-virtual-panel.md:80-81` - the Mac therefore validates the *daemon*, not the
  wake model. Obtaining or exporting a `.tflite` of `hey_zoe` would also serve section 3's TFLite-vs-ONNX
  measurement. **[unverified]** whether the training notebook still exports TFLite.
- **PortAudio:** input at 16 kHz mono 1,280 (no fallback; CoreAudio converts - see 5.1), output a
  PyAudio stream with a 20 ms block + explicit reopen-at-device-rate and per-block linear resample
  **[src]** `mac_backend.py:124-135,263-290`. Docs say PortAudio itself does not resample unless the host
  API does; the explicit fallback is the correct defensive reading **[doc]**.
- **Silero / ducking:** the in-process gain duck dies with the player (cannot leak), better than the
  `osascript` system volume - consistent with `afplay` having no live volume control **[src]**
  `mac_backend.py:21-40`. Preflight checks mic permission ("Privacy & Security > Microphone") **[src]**
  `preflight.py:165`, the documented macOS gate.
- **What the Mac cannot prove** (already stated in-repo): Pi thermals, Jabra AEC, PulseAudio sink-input
  duck, shairport mixing, and USB brownout. This record's Pi findings (sections 4, 5, 8) are therefore
  **not testable on the Mac**; the Mac is useful for the VAD/endpoint *logic* replay in 4.4 because the
  daemon logic is shared.

---

## 8. Pi 5 best practice (documentation-cited)

| topic | what the docs say | Zoe's Pi |
|---|---|---|
| Throttling | "When the core temperature is between 80 C and 85 C, the Arm cores will be progressively throttled back. If the temperature reaches 85 C, both the Arm cores and the GPU will be throttled back." **[doc]** `raspberrypi.com/documentation/computers/raspberry-pi.html` | idle **76-82 C**, 81.8 -> 76-77 C within a minute, 2.4 GHz at the sample; `thermal_zone0` is the only thermal device **[src]** `log-review-pi-2026-10-04.md:26`. |
| `vcgencmd get_throttled` | bit 0 `0x1` under-voltage, 1 `0x2` Arm frequency capped, 2 `0x4` currently throttled, 3 `0x8` soft temperature limit; bits 16-19 (`0x10000/20000/40000/80000`) = "has occurred" since boot **[doc]** `computers/os.html` | `0xe0000` = bits 17+18+19 occurred (cap, throttle, soft limit), live bits clear, **no bit 16** (no under-voltage) **[src]** `:25`. i.e. throttling is real and recent; power is not the proximate cause. |
| Cooling | "use one of the official fan options: Active Cooler"; fan curve: off < 50 C, 30 % at 50, 50 % at 60, 70 % at 67.5, 100 % at 75 C, 5 C hysteresis **[doc]** | No fan device registered **[src]** `:26`; the log review already asked for an Active Cooler or confirming the overlay **[src]** `:129-131`. With the documented curve a cooler would be at full speed by 75 C - the Pi sits above that at idle, so it is either absent or not driven. |
| Power | "USB-C power; 5 V at 5 A (25 W); or 5 V at 3 A (15 W) with a 600 mA peripheral limit" and "The fan connector pulls from the same current limit as USB peripherals" **[doc]** | `EXT5V_V` 5.12 V, no under-voltage **[src]** `:25`. The PSU rating (5 A vs 3 A) is **[unverified]**. If it is a 3 A supply the USB peripherals share a 600 mA limit **[doc]**. |
| USB current override | `usb_max_current_enable=1` "change the max USB current limit from 600 mA to 1.6 A even if power supply is not capable of 5 A" **[doc]** `computers/config_txt.html` | **[unverified]** whether set; docs add the caution to check the supply first. |
| Audio server | Bookworm: PipeWire is the default, PulseAudio-API compatible **[doc]**; PulseAudio defaults: 4 x 25 ms fragments, `resample-method speex-float-1`, `module-suspend-on-idle` timeout 5 s **[doc]** | PulseAudio + `pw-headless3`; suspend-on-idle loaded **[src]** 10-03 §1.7 + log review. |
| USB audio | shairport troubleshooting: Pi USB DACs can stutter from "shared bandwidth/interrupts" on the Pi's USB hub **[doc]** | **House trap:** Jabra PanaCast camera + Jabra Speak 750 share the Pi's USB power; TTS during video "browns the camera OFF THE BUS" (only a physical replug or cold power-cycle revives it); do not enable per-turn face checks until a **powered USB hub** is fitted **[src]** memory `project_panel_identity_phase1` (2026-07-19), echoed in `log-review-pi-2026-10-04.md:137`. |

**Checks to run when the Pi may be read again (all read-only):** `vcgencmd get_throttled`,
`vcgencmd measure_temp`, `cat /sys/class/thermal/cooling_device*/type` (a fan shows as
`pwm-fan`/`cooling_fan`), `vcgencmd get_config usb_max_current_enable`, `vcgencmd pmic_read_adc`
**[unverified]** (command exists on Pi 5; not fetched), `pactl list short sinks` (sink sample rate),
`ss -ulpn | grep -E ':319|:320'`, `iw dev wlan0 get power_save`. Pass condition: `get_throttled` stays
`0x0` across a run of 20 voice turns.

---

## Gaps ranked

Impact x effort, with the measurement that proves each. "Instrument" = the replay corpus
`~/.zoe-voice-samples` (1,318 files) via `scripts/maintenance/voice_regression_probe.py` (nightly
2026-10-05 13:26: pass, STT 566 ms, brain 1,507 ms, e2e 1,660 ms, ok 18/20, empty 2 **[src]**
`~/.cache/zoe/voice_regression_last.json`), or the BARGE_DECIDE ledger.

| # | gap | impact | effort | proof |
|---|---|---|---|---|
| 1 | Pi thermal: no fan, throttling history, 76-82 C idle (docs: throttle from 80 C) | High - degrades every Pi stage and the speaker-gate p95 (376 ms) **[inf]** | Hardware (Active Cooler, ~minutes) + verify fan overlay | `get_throttled` stays `0x0` over 20 turns; before/after endpoint close time, `TTFA`, speaker-shadow p95 (`speaker_shadow_embed.py`) |
| 2 | Silero (Pi) fed non-contiguous audio + no `reset_states` + default threads + no gate | Medium (endpoint + barge decisions) - magnitude unverified | S | Offline corpus diff of `_vad_prob` vs contiguous (probs, onset lag, `_Endpointer` close time); then BARGE_DECIDE commit/resume ratio; add a Pi-path VAD stage to the nightly probe |
| 3 | Moonshine keyterms off (docs: -25 % term errors at default boost, ~free) | Medium (named entities, the Samantha-feel failures) | XS (env + restart; authorised) | Replay without/with, boost 1.0 first; said-vs-did = 0 regressions, `ok >= 18/20`, per-term error count on a labelled subset |
| 4 | Kokoro: clause split + streaming inside the sidecar (generator discarded) | Medium-High on TTFA of long first units (supersedes 10-03 row 6's mechanism) | S | `measure_tts.py` extended to TTFB on `/synthesize_stream`, 20 replies 60-200 chars, ABAB; join-click check; listening A/B |
| 5 | openWakeWord: no measured FRR/FAR; ONNX-vs-TFLite on the Pi unmeasured; no verifier for TV false wakes | Medium (false wakes, wake margin) | M (measure first = S; verifier = M + Pi deploy) | Offline wake replay: owner FRR at live threshold, TV 0/5 accepted at FRR < 10 %, verifier cost < 5 ms/frame (`predict(timing=True)`); TFLite A/B only if a `hey_zoe.tflite` can be produced |
| 6 | PyAudio overruns invisible (`exception_on_overflow=False`) + no per-turn throttle/temp log | Low alone, but it converts #1 and #2 from inference to fact | XS | Two weeks of counters/log lines vs stage timings |
| 7 | Moonshine streaming lane (carry-over from 10-03 action 4), now with corrected expectations: upstream table is pessimistic; 0.1.5 fixes #218 but is held for the decoder regression | Largest *candidate* latency win, still upstream-unproven on Orin | L | Unchanged from 10-03: in-process streaming replay after the RAM chain (`--swa-full`, `--lazy-mode`) |
| 8 | shairport/nqptp: config not in repo; unit ordering; USB/Wi-Fi checks | Low (not on voice path) | XS (read-only audit, doc) | `ss`/`iw`/conf diff recorded in `docs/knowledge/`; nqptp-restart resync test |
| 9 | Pulse suspend-on-idle (10-03 row 8) - now documented 5 s default | Low-Medium (<= 70-90 ms first reply) | Config | The 0.2 s silence bench, suspended vs running, 10 reps each |
| 10 | Kokoro voice blend / callable speed / Moonshine-native Kokoro on CPU | Taste / unmeasured | S-M | Listening A/B; `measure_tts.py` on the CPU port (RAM +85 MB per docs) - only if RAM allows |

## Recommendation

1. **Order of work:** #1 (cooler) first - it is cheap, documented, and without it every Pi
   measurement below carries a throttling confounder. Then #6 (counters + temp/throttle on the turn log)
   so the next fortnight of live data is interpretable.
2. **Then the two S-effort code items with offline proofs that need no Pi:** #2 (Silero contiguity,
   corpus diff first - if the diff is zero, close it as "proven harmless") and #4 (Kokoro in-sidecar
   split, TTFB ABAB). Both are replay/bench-gated and rock-safe.
3. **Quick accuracy win behind the existing flag:** #3 (keyterms, boost 1.0 -> 2.0, household vocabulary),
   run as the replay sequence `moonshine-0-1-5-upgrade.md` §4 already describes.
4. **Measure before building:** #5 starts with the offline wake FRR/FAR instrument, which Zoe has never
   had; the custom verifier is the follow-up only if TV/other-speaker false wakes show up in it.
5. **Do not pursue** (docs-grounded): swapping the endpoint `min_silence_duration_ms` toward the library
   default 100 (that default segments audio; Zoe's 640 ms deep tail is a turn endpoint), enabling
   Silero `onnx` as a speed play (it is ~1 ms either way; the point is contiguity), software AEC on top
   of the Jabra (10-03 §2.4), or `update_interval`/`vad_*` Moonshine options without a streaming lane.
6. **Corrections to 10-03 from this pass:** (a) `keyterm_boost` *is* documented (default 2.0, table in
   `docs/models/domain-customization.md`); (b) the 269 / 802 ms Moonshine figures are upstream-admitted
   pessimistic; (c) `module-suspend-on-idle`'s default timeout is 5 s from the module source; (d)
   `Kokoro /synthesize_stream` does not stream within a unit - relevant to row 6's design.

## Sources fetched 2026-10-05

- Moonshine: `github.com/moonshine-ai/moonshine` (README, `docs/execution-providers.md`,
  `docs/moonshine-vs-whisper.md`, `docs/using/{transcription,benchmarks,debugging}.md`,
  `docs/models/domain-customization.md`, `core/moonshine-c-api.{h,cpp}`, `CHANGELOGS.md`), PyPI
  `moonshine-voice`; releases via `gh api` (v0.1.5 2026-08-24, v0.1.3 2026-08-18).
- Kokoro: `github.com/hexgrad/kokoro` README, `kokoro/pipeline.py`.
- openWakeWord: `github.com/dscripka/openWakeWord` README, `openwakeword/model.py`, `utils.py`,
  `docs/custom_verifier_models.md`, issue #336. (`docs/custom_models.md`, `docs/models/README.md`
  returned 404; training-notebook details are therefore **[unverified]**.)
- Silero: `github.com/snakers4/silero-vad` README, `src/silero_vad/utils_vad.py`, the pyaudio-streaming
  example notebook, wiki Quality-Metrics.
- shairport-sync: `AIRPLAY2.md`, `scripts/shairport-sync.conf`, `TROUBLESHOOTING.md`; `mikebrady/nqptp` README.
- Raspberry Pi: `computers/raspberry-pi.html` (thermal, Active Cooler curve, power), `computers/os.html`
  (`vcgencmd get_throttled`), `computers/config_txt.html` (`usb_max_current_enable`), Bookworm news post.
  (The fan-control `dtparam` table was not present on the fetched config.txt page.)
- PulseAudio: Debian man page `pulse-daemon.conf(5)`, `module-suspend-on-idle.c` source. (The
  freedesktop module docs returned 403.)
- PyAudio docs (`people.csail.mit.edu/hubert/pyaudio/docs/`), PortAudio v19 API overview and
  `pa_mac_core.h`.
- On-box read-only: `libmoonshine.so` strings, `moonshine_voice/transcriber.py`, `services/zoe-data/.env`,
  `~/.cache/zoe/voice_regression_{last,trend}.json*`, user systemd units, memory notes.
