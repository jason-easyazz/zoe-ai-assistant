---
type: Reference
title: Zoe Voice Pipeline
description: The end-to-end voice path (STT → brain → TTS), how it's measured, and the regression corpus — plus the load-bearing caveat that the warm replay harness understates real live latency.
tags: [voice, stt, tts, performance, testing]
timestamp: 2026-07-16T00:00:00Z
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
2. **Brain — Gemma 4 E4B-QAT + MTP**, host-native `llama-server` on `:11434`. Since **#1322 a
   two-stage router runs as a fast-tier FRONT** for the brain (`ZOE_ROUTER_HEAD=active`, live-verified):
   a SetFit/MLP head (`models/router_head_mlp.joblib`) shortlists the top-3 domains + a chat gate,
   then a resident FunctionGemma sidecar (`:11436`, CPU, ~600 MB) does a shortlist-restricted GBNF
   decode (strict 1.5 s timeout). The Gemma brain stays the rock and the fallback for every
   gate-abstain, shortlist miss, sidecar failure, timeout, or malformed decode — never an error to
   the user (~14.8% of turns fall through to the brain; router decision p50 ~393 ms). This front is
   the biggest single contributor to the post-2026-07-02 median drop (see *Latency wins* below).
3. **TTS — Kokoro on CUDA** (`ZOE_KOKORO_BACKEND=pytorch`, set by `kokoro-tts.service`; RTF ~0.08,
   live-verified `device":"cuda"` on `:10201/health`), sidecar on `127.0.0.1:10201`, via a waterfall
   in `routers/voice_tts.py`: **Kokoro → Edge TTS → espeak-ng** (each falls back to the next). NB the
   *code* default in `scripts/setup/kokoro_sidecar.py` is still `onnx`/CPU — the live speedup comes
   from the systemd unit forcing `pytorch`, not from the code default.

Per-stage timings are exported to Prometheus as `zoe_voice_stage_seconds`
(`services/zoe-data/voice_metrics.py`), scraped at `:8000/metrics`.

## Measuring it — the replay harness

Jason's saved WAVs at **`~/.zoe-voice-samples`** (~790 clips and growing) are a **permanent
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
harness under the shared flock.

**Run it from a git worktree with no flags** — and that now holds for the lower-level
`scripts/perf/measure_voice.py` and `measure_tts.py` run DIRECTLY, too. The voice path needs the LIVE
`services/zoe-data/.env`, which is gitignored and therefore absent in a worktree. `--service-dir`
auto-resolves through ONE ladder shared by all three entrypoints (`scripts/lib/service_dir.py`, so
they can't drift): explicit flag (always wins) → this repo's `services/zoe-data` if it has a `.env` →
the **main worktree's** (found via git's `--git-common-dir`, not a hardcoded host path). If no `.env`
resolves anywhere it falls back to the in-tree path so the **loud skip/error still fires**
(`status=error`, exit 2) — the ladder fixes the *default*, never the failure mode; a skip is never
quietly upgraded to a pass. Pinned by `tests/unit/test_probe_dsn_resolution.py`. Scheduled daily off-peak via the
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
latency** — sometimes by a lot. Don't quote harness timings as live performance. Two live-only
effects the warm harness misses: **memory-starved cold STT** (warmup skipped under pressure) and
**wake-word bleed** on the first command. Honest *measurement* over guessing (`VISION.md` principle
4) — when you change the path, measure live, not just the harness.

> **STALE live numbers, kept only as a marker.** An older live snapshot (2026-06-26) read STT ~1.9 s
> (p90 ~8 s), brain ~4.8 s, first-audio ~5 s p50 / ~12 s p90. **These predate the July latency work
> (two-stage router, Kokoro→CUDA, filler racing, greeting cache) and are no longer representative** —
> the warm-harness brain median alone fell ~1.75× over the same window (see *Latency wins* below). No
> fresh full-path *live* re-measure has been captured yet; **re-measure live before quoting any live
> figure**, and do not treat the 2026-06-26 numbers as current.

## Latency wins since 2026-07-02 (what moved the bar)

A batch of latency work landed after the July-2 baseline was set. The warm regression harness
(relative, drift-only) shows the aggregate: brain median **3294 → 1868 ms (~1.76× faster)**, e2e
**2842 → 1896 ms (~1.50× faster)**, STT ~flat (587 → 579 ms), OK-rate unchanged at 19/20. The harness
can't attribute per-commit, but the landed work that drove it:

- **Two-stage router ACTIVE — #1322** (`ZOE_ROUTER_HEAD=active`): SetFit/MLP shortlist + FunctionGemma
  sidecar resolves ~85% of turns off a fast tier so the full Gemma generation runs on only ~14.8% of
  turns — the single biggest measured contributor to the brain/e2e drop.
- **Kokoro on CUDA + per-sentence silence trim — #1330** (plus the earlier CPU→CUDA flip): RTF
  ~1.0–1.8× (CPU, pipe-starving) → **~0.08× (CUDA)**, and `_feed_pcm_chunk` trims each chunk's baked-in
  ~0.4–0.5 s silence so multi-sentence replies stop playing "in pieces".
- **Thinking / tool filler racing — #1106 / #1113 / #1116** (+ panel live-activity strip #1103): the
  spoken filler races the *first audio frame* of the real reply (not just any frame / the already-done
  stream), cutting perceived dead air on brain turns.
- **First-turn-of-day greeting cache — #1228**: a pre-warmed, instant greeting clip is prepended as its
  own leading sentence (flag-gated `ZOE_VOICE_GREETING_ENABLED`, default OFF), covering first-audio
  latency on the day's first turn.
- **Segment-stitch audio caching — #1232, documented #1340**: built to assemble common time/weather
  replies from cached word segments. **It is currently DISABLED on purpose** (`ZOE_VOICE_STITCH_ENABLED=0`,
  live-verified) — obsolete once Kokoro moved to GPU, where a fresh full-sentence synth is ~0.3 s with
  zero internal gaps and stitch only *added* 600–840 ms inter-word pauses (see failure mode #4). Listed
  here for provenance, not as a live win; the audio-caching win that stuck is the sidecar phrase cache +
  greeting cache, not stitch.

**Regression baseline refreshed 2026-07-16.** The gate baseline
(`~/.cache/zoe/voice_regression_baseline.json`) was ratcheted from the stale 2026-07-02 numbers (brain
3294 ms, e2e 2842 ms, STT 587 ms) to the post-speedwork reality (**brain 1868 ms, e2e 1896 ms, STT
579.5 ms**, OK 19/20) via `voice_regression_probe.py --samples 20 --update-baseline`. Why: left on the
July-2 bar the gate compared against an easy, ~1.75× slower target, so a silent brain slowdown could
regress most of the July wins and still "pass". The new bar holds the gains.

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
