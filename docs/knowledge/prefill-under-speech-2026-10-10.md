---
type: Reference
title: Prefill under speech - what hides behind the user's own speech (2026-10-10)
description: Little Gemma's "prefill under speech" tested against Zoe with measurements. Brain prefill (words and prefix warm) is not worth building (at most 13-25 ms); streaming Moonshine during recording is (about 0.3-0.5 s of post-speech STT), shipped as a flag-dark lane with a paired replay A/B. Numbers, controls, enable and rollback recipe.
tags: [voice, latency, stt, moonshine, prefill, llama-server, prefix-cache, ttfa, measurement, flag-dark]
timestamp: 2026-10-10T08:40:00+08:00
---

# Prefill under speech (2026-10-10)

Idea (Little Gemma paper, sec 4.2): do the expensive pre-reply work while the user is still speaking, so little is left when they stop. They cut E4B time-to-first-token after the last word from 2.04 s to 0.16 s on a 929-token dictation. The paper itself says the win is "dictation lengths and the slower 12B tier"; at conversational lengths "what the open turn hides is the ASR pass, not the prefill".

**Verdict, from Zoe's own numbers.** Hiding brain prefill buys nothing here (REJECT, both variants). Hiding the Moonshine pass does (ADOPT behind a default-OFF flag, operator flips). Details below. Where the post-speech time goes is in [first-sound-latency-2026-10-09.md](first-sound-latency-2026-10-09.md) and [panel-ttfa-breakdown-2026-09-28.md](panel-ttfa-breakdown-2026-09-28.md); this record only adds what moves under speech.

## 1. How much brain prefill is there to hide?

llama-server's own per-request log (`prompt eval time`), the whole retained journal, **15,174 requests** (all traffic: voice, Telegram, aux, benches), fitted `prompt_ms = 55.9 + 1.478 x new_tokens`:

| new tokens | requests | median ms | p90 ms |
|---|---|---|---|
| 1 (pure prefix hit) | 2,540 | 54 | 61 |
| 2-20 | 6,393 | 73 | 84 |
| 21-60 | 2,694 | 96 | 128 |
| 61-150 | 1,073 | 191 | 267 |
| 151-400 | 956 | 467 | 656 |
| 401-1000 | 813 | 853 | 1,234 |
| over 1000 | 705 | 4,837 | 6,094 |

Median 16 new tokens, p75 49, p90 400. The voice path specifically (harness, flags off, n=33 first rounds): median 1 new token, p90 59, max 1,293 ([first-sound doc](first-sound-latency-2026-10-09.md)). A 12-word utterance is about 16 tokens = 24 ms of prefill, on top of an unhideable 56 ms floor. The recall and continuity blocks (the tokens that can be hundreds) are appended **after** the user's words inside the same user message ([voice-pipeline.md](voice-pipeline.md), `zoe_flue_client`) and are computed FROM the final transcript, so nothing about them can be prefilled before the words are final.

**Variant A - prefill the user's words as they are committed (the paper's mechanism): REJECT.** Hideable ceiling about 25 ms of a 0.78-1.5 s POST-to-first-delta.

**Variant B - cache-warm the stable prefix at speculative turn start: REJECT.** `scripts/perf/measure_brain_prefix_warm.py`, live llama-server, fresh random 900-token prefixes per rep (nothing cached from an earlier rep), 8 reps per arm, order rotated, server-side `timings` read back (`prompt_n` 12, `cache_n` 902 in every arm):

| arm | what it models | real-request wall, median (min-max) |
|---|---|---|
| hit | healthy voice turn | 85 ms (84-85) |
| swap | slot holds another prompt (Telegram, aux, night job) | 98 ms (94-101) |
| warm | swap, then a cache_prompt warm request 2.5 s before | 84 ms (84-85) |
| warm_late | warm request still running when the turn arrives | 103 ms (98-106) |

The whole swap-in penalty is 13 ms because `--cache-ram` restores the evicted prefix from host RAM; a warm request recovers exactly that (14 ms) and, if it overlaps the real turn on the single slot, costs 18 ms (the contention downside). The harness can see 13-19 ms deltas on 85 ms medians with a 1 ms spread, so "no win" is a measurement, not a blind spot. A cold, never-cached prefix would cost seconds (the fit: 900 tokens = 1.4 s), but `--cache-ram 1024` holds about six 170 MiB entries and the voice prefix is hit by the previous turn; the open question is how often a REAL voice turn arrives with its entry evicted. The live telemetry for it already exists (`FLUE_PROMPT_CACHE ... first_prompt_n` in `~/.zoe-logs/zoe-data.app.log`, docs/knowledge/voice-pipeline.md). Revisit only if a week of real panel turns shows more than about 5 percent with `first_prompt_n` above 400; the warm request would then have to come from the Flue sidecar, which owns the prompt (not built).

## 2. What does move under speech: the Moonshine pass

Today the whole clip, padded with the 0.64-0.8 s endpoint tail, is transcribed after the POST: 0.56 / 0.88 / 0.67 s median (memory / tool / chat turns, first-sound doc), the biggest single stage a prefill-style trick can reach. `moonshine_voice` already exposes `create_stream` / `add_audio` / `stop` on the same MEDIUM_STREAMING model ([panel-ttfa-breakdown-2026-09-28.md](panel-ttfa-breakdown-2026-09-28.md) fix 2; pinned in IDEAS).

### Latency (`scripts/perf/measure_stt_under_speech.py`)
Real household clips, 16 kHz, 2.5-9 s, one Moonshine load, `nice -n 5`, harness lock, order rotated per clip, per-clip arms: `batch` = `transcribe_without_streaming(whole clip)` as the service does; `sess` = the SHIPPED `voice_stt_stream.SttStreamSession` fed 320 ms PCM16 batches paced to wall clock (what the daemon uploads), cost = end of clip to finished transcript; `burst` = the same session fed all at once at the end (negative control: nothing hidden).

| n=16 | batch | sess (under speech) | burst (control) |
|---|---|---|---|
| post-speech STT, median | 0.746 s (p10 0.546, p90 1.115) | **0.265 s** (p10 0.084, p90 1.077) | 1.357 s (p10 0.959, p90 3.106) |
| faster than batch | | **15 / 16** | 0 / 16 |

Paired saving: median **0.466 s** (p10 0.08, p90 0.77); one 8.3 s clip saved 3.5 s (batch 3.59 s vs 0.08 s). The control behaves: with nothing hidden the streaming engine is 1.8x SLOWER than batch (it re-looks at audio on every pass), so the win is the hiding, not a faster engine. It also prices the cost: about 0.6 s of extra CPU per turn, spent while the user speaks (the brain is on the GPU and idle then). Caveat: the load average climbed from 0.9 to 7 during the run (other agents' builds shared the CPU), which hurts `sess` more than `batch` (the stream competes for cores during speech), so the saving is conservative. An earlier raw-API run (n=14, Moonshine API directly, 80 ms chunks, load 2-6) read batch 1.127 s vs 0.160 s.

### Fidelity: the stream is not bit-identical to batch
Counts only, never transcript text (household audio). Same 16 clips:

| comparison | clips with identical normalised text | word edits |
|---|---|---|
| batch vs batch again (the engine's own noise floor) | 14 / 16 | 3 / 131 words |
| batch vs `sess` | 12 / 16 | 10 / 131 words |

So streaming changes about 7.6 percent of words on 4 of 16 clips versus a 2.3 percent noise floor (2 of 16). Pass timing is part of it: the stream's final text depends on where its update passes fell. That is why the lane is flag-dark and gated by the replay A/B below, and why every unsafe shape falls back to the batch STT.

### Replay A/B, said-vs-did (`measure_voice.py --last 20 --stt inprocess`, brain on, writes isolated)
Arm B = `ZOE_REPLAY_STT_UNDER_SPEECH=1` (the clip goes through the shipped session, paced, with the production fallback and wake-word strip). The same 20 newest corpus clips per arm, two holds with the order reversed (AB, then BA), n = 40 turns per arm:

| | batch STT (A) | under speech (B) |
|---|---|---|
| verdict per clip identical A vs B | | **40 / 40** (OK 34, EMPTY 6 in each arm; the same files) |
| hold 2 (BA): clips served by the stream | | 17 hit, 3 batch re-check (the three EMPTY clips: empty stream text always re-checks with batch) |
| hold 2: post-speech STT on the 17 hit clips | 791 ms median | **226 ms median** (saving 493 ms, faster on 16 / 17) |
| hold 2: STT median, all 20 | 753 ms | 216 ms |
| hold 1 (AB): STT median, all 20 | 523 ms | 168 ms (3 of 17 hit clips slower, load average 7; no diagnostics in that hold) |
| transcript wording identical (hold 2) | | 18 / 20 (both differing clips still scored OK) |

The replay's e2e medians (1.9 s vs 1.4 s in hold 2) are NOT claimed: the brain leg is temperature 0.7 and varies more than the STT leg. The harness is warm and stops before TTS, so its numbers are relative.

Replay gate for the shipped code (flag OFF): see the PR body (`voice_regression_probe.py`, head-bound).

## 3. What shipped (flag-dark)

| piece | where |
|---|---|
| session + registry (bounded: 2 sessions, 30 s of audio, 30 s idle TTL) | `services/zoe-data/voice_stt_stream.py` |
| `POST /api/voice/stt_stream/chunk` (409 when off), `/turn_stream` takes the finished text | `services/zoe-data/routers/voice_tts.py` (`_take_stt_stream_text`, `_transcribe_audio(pre_text=)`) |
| daemon uploader (320 ms batches, sender thread, 409 latch; flag `ZOE_STT_STREAM_UPLOAD`) | `scripts/setup/zoe_voice_daemon.py` (`_SttStreamUploader`, `_attach_stt_stream`), stacked on the server PR to keep each under the size limit |
| instruments | `scripts/perf/measure_stt_under_speech.py`, `measure_brain_prefix_warm.py`, `replay_samples.py` arm, `measure_voice.py` (`stt_stream`, `heard_hash` per row) |
| tests (`ci_safe`) | `services/zoe-data/tests/test_voice_stt_stream.py`, `tests/unit/test_voice_daemon_stt_stream.py` |

Safety contract: the WAV is still POSTed with the turn, and the stream text is used only when its sample count equals the WAV's and it is non-empty. A gap in `seq`, a mismatch, an unknown or reused id, a timeout (`ZOE_STT_STREAM_FINISH_TIMEOUT_MS`, 2.5 s), an engine error or empty text all fall back to the unchanged batch STT, so a broken upload costs only the saving. Speculative (B1.1) turns never take a stream. The shared Moonshine inference lock is held per `add_audio` pass (up to about 0.5 s), so a concurrent Telegram transcription can wait that long. Pinned by tests with break-the-fix: removing the sample-count check turns `test_take_falls_back_on_every_failure_shape` red.

## 4. Enable / rollback (operator)

1. Server first: `ZOE_STT_STREAM_UNDER_SPEECH=1` in the zoe-data env, restart zoe-data (a per-call env read, so later flips need no restart). With the daemon flag still off nothing changes.
2. Pi daemon: deploy the new `zoe_voice_daemon.py` (from a worktree, see the panel voice deploy note) with `ZOE_STT_STREAM_UPLOAD=1`, `systemctl --user restart zoe-voice`.
3. Watch `grep STT_STREAM ~/.zoe-logs/zoe-data.app.log`: `outcome=hit` with `finish_ms` small is the lane working; `fallback reason=...` names every miss (`sample_mismatch`, `no_session`, `timeout`, `empty`, `engine:...`).
4. Rollback: unset `ZOE_STT_STREAM_UPLOAD` on the Pi and restart `zoe-voice` (or just unset the server flag: the chunk endpoint answers 409 and the daemon latches itself off for the process). No data to clean up.

Not proven here: daemon timing on the Pi (the uploader is unit-tested with a fake POST only, never on the real panel), the extra HTTPS POST per 320 ms during speech, and the end-of-recording `close()` join (one POST round trip before the WAV POST). Run a panel week and compare `finish_ms` and TTFA before leaving it on.

## 5. Variant C - early transcript for recall / continuity / router

Not built; it is B1.1 ([b1-speculative-turn-start.md](../architecture/b1-speculative-turn-start.md), flag-dark). The router decode (0.30-0.35 s on tool-ish turns), recall (up to 0.57 s) and continuity (0.28-0.8 s) sit between the transcript and the brain POST, so an early transcript could move them, but only for turns whose speculative transcript is equivalent to the final one (offline cancel upper bound 14.3 percent at the 320 ms default) and with Phase 2 holding writes. B1.1 and this STT lane compose in principle (the speculative POST would find a finished stream), but that is deliberately not wired: it needs the Pi week for B1.1 first.

## Re-measure
`flock /tmp/zoe-voice-harness.lock nice -n 5 ~/.zoe/venvs/zoe-data-py312/bin/python scripts/perf/measure_stt_under_speech.py --n 16 --json out.json`; `flock /tmp/zoe-voice-harness.lock python3 scripts/perf/measure_brain_prefix_warm.py --reps 8`; A/B: `ZOE_PERF=1 [ZOE_REPLAY_STT_UNDER_SPEECH=1] python3 scripts/perf/measure_voice.py --last 20 --stt inprocess --json out.json` (from a worktree with a `services/zoe-data/.env` symlink, otherwise the harness silently measures the live checkout's code).
