---
type: Reference
title: Zoe Voice Pipeline
description: The end-to-end voice path (STT → brain → TTS), how it's measured, and the regression corpus — plus the load-bearing caveat that the warm replay harness understates real live latency.
tags: [voice, stt, tts, performance, testing]
timestamp: 2026-06-26T00:00:00Z
---

# Zoe Voice Pipeline

How a spoken turn flows through Zoe, and how we measure it without regressing. The three stages are
**rocks** — never swap them, only optimise around them (see [CANONICAL.md](../CANONICAL.md) and
`VISION.md` principle 1). Topology/ports live in [runtime-topology.md](runtime-topology.md).

## The path

1. **STT — Moonshine v2 Medium.** Runs **in-process inside `zoe-data` on CPU** (onnxruntime GPU
   discovery fails on Tegra). Warmed at startup via `warm_moonshine` (`services/zoe-data/main.py`,
   `routers/voice_tts.py`). Since **#854 Moonshine is the ONLY live STT engine** — faster-whisper was
   removed from the live path (it cold-loaded onto a memory-starved GPU and clouded accuracy); whisper
   helpers remain defined for offline tooling but never run on a live turn. `_run_moonshine` also runs
   a `_strip_wake_word` pass removing the "Hey Zoe" wake bleed (Moonshine emits the wake on its own
   line; greeting-prefixed homophones like "hey joey" strip, bare real names like "Joe" are kept).
2. **Brain — Gemma 4 E4B-QAT + MTP**, host-native `llama-server` on `:11434`.
3. **TTS — Kokoro**, sidecar on `127.0.0.1:10201`, via a waterfall in `routers/voice_tts.py`:
   **Kokoro → Edge TTS → espeak-ng** (each falls back to the next).

Per-stage timings are exported to Prometheus as `zoe_voice_stage_seconds`
(`services/zoe-data/voice_metrics.py`), scraped at `:8000/metrics`.

## Measuring it — the replay harness

Jason's saved WAVs at **`~/.zoe-voice-samples`** (~313 clips and growing) are a **permanent
regression corpus** — `ZOE_VOICE_SAVE_AUDIO=1` auto-captures real turns, so the corpus (and the bar)
**evolves with real use**. Replay-gating **every** voice change is MANDATORY (root `AGENTS.md`); the
said-vs-did mapping must not regress — "can't do it" on a sample is a bug, not an excuse.

- Harness: `scripts/perf/measure_voice.py` + `scripts/perf/measure_tts.py` (set `ZOE_PERF=1`); they
  wrap `services/zoe-data/tests/replay_samples.py`.
- **Always run under `flock /tmp/zoe-voice-harness.lock`** — two Kokoro loads (~2.3 GB each) will OOM
  the memory-tight box.
- Session hygiene: each harness run uses a **fresh brain session id** (`replay-<epoch>`; samples
  within a run share it). A fixed id once grew the flue sidecar's durable session past the model
  context (8288 > 8192 tokens → HTTP 500 every turn, 2026-07-07). The flue client's
  brain-unreachable fallback text now classifies as **ERROR**, never OK — a dead brain lane can't
  silently pass the gate.

## Regression + speed gate — `voice_regression_probe.py` (fleet tool, evolving)

`scripts/maintenance/voice_regression_probe.py` is the **baseline-compared** wrapper any agent (or a
human, or the scheduled timer) runs to catch drift on TWO axes at once:
- **function** — the corpus OK-rate must not drop / CANT_DO+ERROR must not rise (Zoe mustn't lose an ability);
- **speed** — per-stage medians (STT / brain / e2e) must not regress beyond a ratio + absolute-ms gate.
It mirrors `zoe_latency_probe.py`: `--update-baseline` to set the bar, baseline at
`~/.cache/zoe/voice_regression_baseline.json`, a `…_trend.jsonl` history, non-zero exit + `WARN` on
regression. It self-guards: **SKIPs if available memory is low** (never OOMs the box) and runs the
harness under the shared flock. Scheduled daily off-peak via the
`scripts/setup/systemd/zoe-voice-regression.{service,timer}` templates (operator installs to
`~/.config/systemd/user/`). Numbers are RELATIVE (warm harness) — used for *drift vs baseline*, not
as live performance.

## The gate emits a heartbeat, and the deploy path checks it

*"A gate that can silently not-run is not a gate."* This gate was once deadlocked from birth (it
re-took a flock its caller already held, timed out ~17 min every run, and NEVER once succeeded —
yet merged work claimed to be replay-gated). The deadlock is fixed (#1292, self-serializing); the
generalized lesson is a **result artifact + a checker**, mirroring the router self-train ratchet's
`replay_gate_passed` (a *skip* is not a *pass*).

- **Result artifact (produced by `voice_regression_probe.py` on EVERY run):**
  `~/.cache/zoe/voice_regression_last.json` (override `ZOE_VOICE_RESULTS`), also appended to
  `…_trend.jsonl`. Machine-readable contract — keep these keys stable:

  ```json
  {"status": "pass|fail|skip|error", "timestamp": "…Z",
   "said_vs_did_regressions": ["FUNCTION: …"], "per_stage_speed_deltas": {"stt_ms": {"cur_ms": …, "baseline_ms": …, "delta_ms": …, "ratio": …}, …},
   "baseline_ref": {"path": "…", "created_at": "…Z", "ok_rate": …},
   "reason": "…", "summary": {"n_samples": …, "ok_rate": …, "medians_ms": {…}}}
  ```

  A **skip** (box too tight), **timeout**, or **error** (harness couldn't run) MUST still write an
  artifact with `status != "pass"` — an *absent* file is never "nothing wrong". `summary` +
  `created_at` are retained for the router self-train `replay_gate` reader.

- **Deploy-path checker — `scripts/maintenance/voice_gate_check.py`:** the cheap counterpart the
  blessed deploy (`deploy_live.sh`) invokes. If the incoming git diff touches the **voice runtime
  path** (`voice_tts.py` / `zoe_core_client.py` / `fast_tiers.py` / `*kokoro*` / `*moonshine*`;
  override `ZOE_VOICE_GATE_PATHS`), it asserts a **fresh** (`< ZOE_VOICE_GATE_MAX_AGE_H`, default 24h)
  **passing** artifact **matching the current baseline** before the restart — else it fails loudly
  (non-zero exit) and the deploy is refused. Non-voice deploys are a no-op pass. **It never runs the
  heavy Kokoro harness** (that would OOM the box under flock) — it only reads the artifact the gate
  produced. Standing rule: *any mandatory loop/gate/job must emit a heartbeat that something checks.*
  Pinned by `tests/unit/test_voice_gate_check.py` (missing → block, stale → block, fresh pass →
  allow; skip/error/baseline-drift all block).

## The caveat that bites (read this)

The replay harness uses **warm models and stops *before* TTS**, so **its numbers UNDERSTATE real live
latency** — sometimes by a lot. Don't quote harness timings as live performance. As measured live
(2026-06-26, will drift — re-measure, don't cite as fixed): STT ~1.9 s (p90 ~8 s), brain ~4.8 s,
first-audio ~5 s p50 / ~12 s p90. Two live-only effects the warm harness misses: **memory-starved
cold STT** (warmup skipped under pressure) and **wake-word bleed** on the first command. Honest
*measurement* over guessing (`VISION.md` principle 4) — when you change the path, measure live, not
just the harness.

## Failure modes that are easy to misdiagnose (2026-07-14 / -15)

All were reported as "the wake word gets the first use wrong" or "the voice is choppy / broken into
pieces". None was a model problem — Moonshine and Kokoro were fine. Symptoms in the voice path are
usually **plumbing**, so measure the audio before blaming the model. Several of these were latency
hacks from the slow-**CPU** Kokoro era that became pure downside once Kokoro moved to GPU — when
synthesis is fast, splitting/stitching for speed only adds pauses.

**1. Dead air between wake and capture (STT looks like it mis-hears).**
The daemon closed the mic on wake, played the chime with a *blocking* `subprocess.run`, then opened a
fresh mic stream — several hundred ms in which the user was already talking. Those words were deleted
before STT ever saw them, so a *correct* transcript of a *mutilated* recording looked like a bad model:

    "Hey Zoe, what's my name?"           -> "My name."
    "Hey Zoe, what's on my calendar?"    -> "That's not my calendar this week."

It only bit when the wake word and command were spoken **in one breath**; pausing after "Hey Zoe" let
the hole land in silence, which is why it seemed intermittent. Tell: the capture starts *hot* (no
lead-in silence) and the raw Moonshine lines begin at `"Zoe."` with `"Hey"` chopped off. Fix: record
from the still-open wake stream (`record_command(pa, stream=...)`), chime fire-and-forget, pre-roll
widened to ~1.6 s. Pinned by `tests/unit/test_voice_wake_no_dead_air.py`.

**2. TTS slower than real time (reply plays back chopped).**
`turn_stream` synthesizes the reply sentence-by-sentence and feeds a single persistent `aplay` pipe.
That only works if synthesis outruns playback. On the ONNX/**CPU** backend Kokoro ran at **RTF
~1.0–1.8x** — slower than real time — so the pipe *had* to run dry at every chunk boundary (ALSA
underrun -> gap). Short chunks made it worse: per-call overhead pushed a 10-char stub to RTF 1.8x,
so the very chunking that bought fast first-audio was what starved the pipe. Fix: Kokoro on CUDA
(`ZOE_KOKORO_BACKEND=pytorch`), RTF **0.08x**. **Diagnostic: if replies ever sound chopped again,
check `curl localhost:10201/health` for `device` and `degraded` first** — a busy box can OOM the
CUDA init and silently drop back to CPU.

**3. Per-sentence chunking split short replies mid-sentence (#1330 / #1331).** Two compounding
issues once the pipe no longer starved: (a) Kokoro pads every utterance with ~0.4–0.5 s of silence
front and back, and the panel concatenated the chunks keeping all of it → ~0.9 s dead air at each
sentence join → trim it in the daemon (`_trim_chunk_silence`, keep ~130 ms tail); (b) the server's
`_extract_first_unit` broke the first chunk at the first comma/colon — even *inside* a number
("The time is 8:" ⏸ "05…") — and each fragment is a standalone utterance with sentence-final prosody,
so a short reply sounded broken. Fix: only sentence-boundary chunks for short replies; clause-break
only long openings; every boundary needs a following space. Pinned by `test_voice_first_audio.py` /
`test_voice_invariants.py`.

**4. Voice stitch made ONLY time/weather choppy — a live-`.env` landmine (2026-07-15).**
Tell: *chat replies (e.g. "meaning of life") are smooth but time/weather are choppy.* They take
different paths — chat streams through the brain + `_extract_first_unit`; the **fast-path** domains
(time/weather/date/list/calendar via `fast_tiers.resolve`) are synthesized in
`turn_stream._wrapped()`'s `elif reply_text:` branch, which first tries `voice_stitch.stitch_reply`.
Stitch assembles time/weather from **cached word-level segments glued with a 70 ms micro-pause**
(`_GAP_MS`); with each segment's own baked-in silence it measured **600–840 ms gaps between words**
("it's" ⏸ "twenty-two" ⏸ "degrees"…). It was a CPU-era latency hack — obsolete on GPU (a fresh
full-sentence synth is ~0.3 s with **zero** internal gaps). **The code default is OFF; the choppiness
came from `ZOE_VOICE_STITCH_ENABLED=1` set in the live `services/zoe-data/.env`.** Fix: set it to `0`
+ restart zoe-data. No repo change is needed — verified nothing git-tracked sets `=1` (the code
default in `voice_stitch.py` is OFF and no `.env.example` / installer / provisioning file references
it), so the `=1` was a purely local live-`.env` override with **no template source to correct**. If a
host-level provisioning script outside this repo ever sets it, fix it there too. **Diagnostic: if
time/weather (but not chat) go choppy, `grep ZOE_VOICE_STITCH services/zoe-data/.env` FIRST.**

Meta-lesson: the fast-path and the brain path have **separate chunkers** (`_split_sentences` +
`stitch_reply` vs `_extract_first_unit`), so a voice-naturalness fix must cover BOTH — fixing one
leaves the other symptom live (exactly how #1331 fixed chat while time/weather stayed choppy).

## Voice selection — "Zoe's voice" (user-facing)

Zoe's speaking voice is a **household setting**, picked from the touch panel's "Zoe's voice"
settings card (or by voice: "change your voice to ember").

- **Preference:** `app_settings.tts_voice` (migration `0018`), managed by
  `services/zoe-data/voice_settings.py`. Per-synth resolution: explicit override → persisted pref
  (5 s in-process TTL cache) → `ZOE_KOKORO_VOICE`/`af_sky`. Fail-open — a broken DB never breaks speech.
- **Catalogue:** the loaded voices bin (`ZOE_KOKORO_VOICES`, an NPZ) is the single source of truth;
  `GET /api/voice/voices` lists it, the UI never hardcodes names.
- **Preview:** `POST /api/voice/preview` synthesizes a **server-fixed** sample sentence
  (`voice_settings.PREVIEW_TEXT`) in any catalogue voice; the panel plays the returned WAV.
- **Cache correctness:** the sidecar phrase cache (memory + `~/.zoe/kokoro_cache/`) keys on
  `<voice>|<text>` — a voice switch never replays stale audio in the old voice.

**Operator step — enabling the custom `zoe_*` blended voices** (they appear in the picker only once
the augmented bin is installed):

```bash
cd labs/kokoro-voice-blend
python3 blend_zoe_voices.py --emit-bin          # writes the augmented voices bin
# point ZOE_KOKORO_VOICES (zoe-data env AND kokoro-tts.service env) at the new bin, then:
systemctl --user restart kokoro-tts.service     # sidecar loads the new tensors
systemctl --user restart zoe-data.service       # picks up the env change
```
The catalogue endpoint re-reads the bin by mtime, so zoe-data shows the new names without a code change.
