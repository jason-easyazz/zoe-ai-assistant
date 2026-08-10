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
3. **TTS — Kokoro on CUDA** (PyTorch, RTF ~0.08, live-verified `device":"cuda"` on
   `:10201/health`), out-of-process sidecar on `127.0.0.1:10201`, via a waterfall in
   `routers/voice_tts.py`: **Kokoro → Edge TTS → espeak-ng** (each falls back to the next).
   PyTorch/CUDA is the sidecar's sole backend; it falls back to CPU on its own only if CUDA
   cannot load, reporting `degraded=true` on `/health`. zoe-data holds no in-process TTS model.

Per-stage timings are exported to Prometheus as `zoe_voice_stage_seconds`
(`services/zoe-data/voice_metrics.py`), scraped at `:8000/metrics`.

## Measuring it — the replay harness

Jason's saved WAVs at **`~/.zoe-voice-samples`** (1001 curated clips as of 2026-08-04, and growing)
are a **permanent regression corpus** — `ZOE_VOICE_SAVE_AUDIO=1` auto-captures real turns, so the
corpus (and the bar) **evolves with real use**. Replay-gating **every** voice change is MANDATORY
(root `AGENTS.md`); the said-vs-did mapping must not regress — "can't do it" on a sample is a bug,
not an excuse.

- Harness: `scripts/perf/measure_voice.py` + `scripts/perf/measure_tts.py` (set `ZOE_PERF=1`); they
  wrap `services/zoe-data/tests/replay_samples.py`.
- **Always run under `flock /tmp/zoe-voice-harness.lock`** — two Kokoro loads (~2.3 GB each) will OOM
  the memory-tight box.
- Session hygiene: each harness run uses a **fresh brain session id** (`replay-<epoch>`; samples
  within a run share it). A fixed id once grew the flue sidecar's durable session past the model
  context (8288 > 8192 tokens → HTTP 500 every turn, 2026-07-07). The flue client's
  brain-unreachable fallback text now classifies as **ERROR**, never OK — a dead brain lane can't
  silently pass the gate.

### The corpus contract — auto-captured, so it needs curating

The corpus is **untrusted input**: it grows by itself from whatever the wake word fired on, including
TV false-wakes and captures written by a resampling-era pipeline. Two rules make it gate-safe.

**1. Every member SHOULD be 16 kHz mono 16-bit PCM — but off-contract is not the same as unusable.**
16 kHz mono s16 is the capture contract. Missing it is a real signal about the capture path, and it
is REPORTED as drift; it is not grounds for eviction. The replay path
(`replay_samples.py` → `routers.voice_tts._run_moonshine` → `_prepare_audio_for_moonshine`,
`voice_tts.py:2071`) **resamples off-rate audio to 16 kHz and downmixes multi-channel** before
transcription, so a 24 kHz mono s16 capture is a perfectly good regression sample. The only input it
refuses is a rate it cannot honestly resample (`sr <= 0`), which it explicitly declines to pretend is
16 kHz. So the quarantine class is exactly the audio STT itself cannot consume: unparseable RIFF,
zero frames, or `rate <= 0`.

Measured 2026-08-04 over 1151 files: **5 unusable** (not valid RIFF at all), **95 × 24 kHz drifted
but transcribable** (a 2026-06-21..07-13 resampling-era window — these STAY in the corpus and are
reported), and **50 clear non-speech**. The first executed run predated this distinction and moved
all 150; the 95 were restored, leaving the live top-level corpus at **1099 WAVs**. #1642 is the
non-speech class one file wide.

The right fix for capture drift is a **rate assertion at the SAVE path** — catching it when the file
is written, not deleting the evidence afterwards. Curating a shrunken corpus and re-baselining
against it makes the gate agree with itself while measuring less.

**2. Quarantine is a MOVE into a dated subdirectory, never a delete.**
`scripts/maintenance/curate_voice_corpus.py` audits every top-level WAV (stdlib format probe + the
**real** `voice_vad` Silero path) and moves failures into `quarantine-format-YYYYMMDD/` and
`quarantine-nonspeech-YYYYMMDD/` beside a `manifest.json` recording file, reason, scores, mtime and
the VAD model sha. Dry-run by default; `--execute` under `flock /tmp/zoe-voice-harness.lock` so no
probe enumerates the corpus mid-move. It has no delete path at all (AST-pinned by
`tests/unit/test_curate_voice_corpus.py`), so a wrong call is always reversible with `mv`.

**Quarantine subdirectories are excluded from replay BY DESIGN, and that is a verified property, not
a convention.** Every corpus consumer globs `<corpus>/*.wav` — non-recursive:
`replay_samples.py::_select` (the SSOT behind `voice_regression_probe.py` and
`scripts/perf/measure_voice.py`) and `test_voice_barge_in.py`'s real-voice replay. The unit test
executes `_select`'s real source against a fixture tree containing a quarantine subdir, so making
any of them recursive — which would silently re-admit quarantined audio to the gate — goes red.

**The quarantine line is 0.20 peak speech probability, NOT the runtime 0.50.**
`voice_vad.speech_threshold()` = 0.5 is the live barge-in decision and is deliberately not reused:
the corpus median peak is 0.829 with ~11% under 0.5, and that 11% is quiet, distant or
clipped-but-real speech — the hardest and most valuable gate samples. A peak under 0.20 means the
model never once, in any 32 ms hop of the whole recording, thought it heard speech. Files in
`0.20 ≤ peak < 0.50` are reported as **BORDERLINE and kept** (61 of them). Consequence, stated
plainly: the specific TV false-wake that reddened #1642 scores 0.366 and is **kept** — the fixture
fix is what handles that file; curation only removes what is unambiguous.

**Curating changes the gate's input, so re-run the probe after a curation pass** and decide
explicitly whether to `--update-baseline`. Post-curation the corpus is 93.9% above the runtime
threshold (was 89.4%), so both the OK-rate and the per-stage medians move for a reason the
baseline does not know about.

## Regression + speed gate — `voice_regression_probe.py` (fleet tool, evolving)

`scripts/maintenance/voice_regression_probe.py` is the **baseline-compared** wrapper any agent (or a
human, or the scheduled timer) runs to catch drift on TWO axes at once:
- **function** — the corpus OK-rate must not drop / CANT_DO+ERROR must not rise (Zoe mustn't lose an ability);
- **speed** — per-stage medians (STT / brain / e2e) must not regress beyond a ratio + absolute-ms gate.
It mirrors `zoe_latency_probe.py`: `--update-baseline` to set the bar, baseline at
`~/.cache/zoe/voice_regression_baseline.json`, a `…_trend.jsonl` history, non-zero exit + `WARN` on
regression. It self-guards: **SKIPs if available memory is low** (never OOMs the box) and runs the
harness under the shared flock.

**STT mode — `--stt remote` is the nightly default** (unit template sets
`ZOE_VOICE_REPLAY_STT=remote`). In-process mode lazy-loads a SECOND Moonshine next to the live
service's warm one, which is why the gate demanded 1500MB and skipped for days on a box whose
steady state leaves ~350–1200MB. Remote mode POSTs each WAV to the live `/api/voice/transcribe`
(auth: `ZOE_DEVICE_TOKEN` — `DEVICE_TOKEN` also honoured as the fallback name, matching
`zoe_latency_probe` — in the environment, provisioned in `~/.hermes/.env`): measured
2026-07-27, a full run peaks at **445MB** and the per-mode memory floor is 700MB vs 1500MB.
Transcripts are engine-identical across modes (same Moonshine, same box), so baselines carry
over; the replay JSON records `stt_mode`. Router/`fast_tiers` deliberately stay in-process —
only the harness runs them with `allow_writes=False`; the live endpoints would execute the
commands for real. Flip back to `inprocess` only when the live service itself is the thing
under test. Expect ~5% single-turn brain flake on a busy box: one CANT_DO in 20 fails the gate
by design (said-vs-did is zero-tolerance) — re-run before treating it as a real regression.

**Write isolation takes TWO mechanisms, because a turn has two executors.** `allow_writes=False`
governs `fast_tiers` only. On brain fall-through the turn reaches the flue sidecar, whose tools run
with `ZOE_BRAIN_ALLOW_WRITES=true` (both lanes' `.env`), so a corpus command — "remember X", "add
bread to the list", "turn on the kitchen light" — used to execute a REAL write into live zoe-data on
every gate run. The probe's soft-delete swept only `events` and `list_items`; reminders, notes,
journal entries, people, users, lists, MemPalace memories, Home Assistant device state and Music
Assistant playback all leaked silently, and each NEW mutating tool leaked by default.

`replay_samples.py` now sends a per-request **replay marker** — a ` zoe-replay:1` envelope line
riding ahead of the ` zoe-uid:` identity line, bound to the turn's AbortSignal by the sidecar and
read at the write gate (`zoe_flue_client._wrap_message_with_replay` → `src/replay-mode.ts` →
`runWrite`). The tool reports the write as done and commits nothing. Three properties are
load-bearing:

- **It returns SUCCESS text, not a refusal** — which is why this is not simply
  `ZOE_BRAIN_ALLOW_WRITES=false`. `_classify` scores a turn on the reply TEXT and never reads the
  database, so success-shaped isolation leaves every verdict unchanged; the env-flip refusal text
  matches `_CANT_DO_RE` and would redden the gate on every write command in the corpus. (It is also
  a module-LOAD const, so flipping it needs a sidecar restart in both directions.)
- **Reads are untouched** — the gate still needs real recall to score said-vs-did.
- **Absent marker = today's bytes.** Live traffic is unaffected; the seam also strips any
  user-typed marker line so a user cannot forge it and void their own writes.

The soft-delete sweep stays as a safety net for the `fast_tiers` half and `--execute` runs. Do NOT
grow it class-by-class — that race is unwinnable, which is the gap the marker closes.

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
   "reason": "…", "summary": {"n_samples": …, "ok_rate": …, "medians_ms": {…}},
   "non_pass_streak": 0, "non_pass_alert_after": 3, "non_pass_alert": false}
  ```

  A **skip** (box too tight), **timeout**, or **error** (harness couldn't run) MUST still write an
  artifact with `status != "pass"` — an *absent* file is never "nothing wrong". `summary` +
  `created_at` are retained for the router self-train `replay_gate` reader.

  **Skip-streak alarm** (a gate that green-skips forever is not a gate): `non_pass_streak` counts
  consecutive runs with `status != "pass"` (a real pass resets it to 0). Once it reaches
  `--alert-after-non-pass` (default 3, env `ZOE_VOICE_ALERT_NON_PASS_RUNS`), `non_pass_alert`
  flips true and the low-memory **skip path exits 4** instead of 0, so
  `zoe-voice-regression.service` (and its timer) goes visibly red instead of recording SUCCESS on
  every skipped run. The unit also gates startup on a bounded Postgres :5432 TCP wait
  (`scripts/maintenance/wait_for_port.py`, `ExecStartPre`) — it is a USER unit, so
  `After=docker.service` cannot order it after the dockerized DB at boot. Pinned by
  `tests/unit/test_voice_probe_streak.py`.

- **Deploy-path checker — `scripts/maintenance/voice_gate_check.py`:** the cheap counterpart the
  blessed deploy (`deploy_live.sh`) invokes. If the incoming git diff touches the **voice runtime
  path** (`voice_tts.py` / `zoe_core_client.py` / `fast_tiers.py` / `*kokoro*` / `*moonshine*`, plus
  the **live** brain lane — `labs/flue-zoe-brain-2x/` deployed source, `zoe_flue_client.py`,
  `brain_dispatch.py`, `flue-zoe-brain-2x.service` — the dormant zoe-core fallback's manifest, and the
  live router's model directory `services/zoe-data/models/*`, which holds the stage-1 checkpoint that
  decides which tool a voice turn fires (swapping that file re-routes every turn with **no code diff
  at all**; it is a directory glob so the next head added there gates by default, and the offline
  training copies in `labs/setfit-router/artifacts/` stay ungated) — plus the rest of that router:
  its `functiongemma-router.service` serving unit and its `router_two_stage.py` +
  `semantic_router.py` decision modules, gated for different reasons (serving config vs. the logic
  that picks the tool) because any one leg alone leaves a hole. Their regression class is silent by
  construction too: `decide()` returns `None` on any failure and the caller keeps the weaker
  similarity route, while a plain logic edit just returns a different tool without erroring. The
  routers' tests, the `labs/` harnesses and the offline `scripts/maintenance/router_*.py` tooling
  stay ungated, which is what the literal paths (rather than a `*router*` wildcard) buy — **plus the
  LiveKit/WebRTC ingest lane**: `livekit_aiortc.py` (the *selected* production backend —
  `ZOE_LK_USE_AIORTC=1` overrides a code default of `0`), `routers/voice_livekit.py` (registered
  unconditionally, owns the WebRTC turn's VAD/barge-in/endpointing/pipeline call) and
  `services/livekit/config.yaml` (the on-demand container's serving config). **Read the next section
  before treating a green gate on those three as verification;**
  override `ZOE_VOICE_GATE_PATHS`), it asserts a **fresh** (`< ZOE_VOICE_GATE_MAX_AGE_H`, default 24h)
  **passing** artifact **matching the current baseline** before the restart — else it fails loudly
  (non-zero exit) and the deploy is refused. Non-voice deploys are a no-op pass. **It never runs the
  heavy Kokoro harness** (that would OOM the box under flock) — it only reads the artifact the gate
  produced. Standing rule: *any mandatory loop/gate/job must emit a heartbeat that something checks.*
  Pinned by `tests/unit/test_voice_gate_check.py` (missing → block, stale → block, fresh pass →
  allow; skip/error/baseline-drift all block).

### The gated set is NOT all equally evidenced — the LiveKit/WebRTC lane (read before believing a green)

**The replay corpus does not traverse the LiveKit lane.** `~/.zoe-voice-samples` is replayed through
`POST /api/voice/transcribe` — the HTTP lane. The probe chain (`voice_regression_probe.py` →
`scripts/perf/measure_voice.py` → `services/zoe-data/tests/replay_samples.py`) contains **zero**
references to livekit/webrtc/aiortc, and the always-on Pi daemon posts HTTP too. So for a diff
touching only `livekit_aiortc.py` / `routers/voice_livekit.py` / `services/livekit/config.yaml`:

> **A fresh passing artifact certifies HTTP-corpus-path non-regression and the live service's import
> health — and nothing about the code that changed.**

That premise is pinned by `test_replay_corpus_does_not_traverse_the_livekit_lane`: if someone adds a
LiveKit stage to the probe, the test goes red and this section must be rewritten rather than silently
becoming an understatement.

- **What actually verifies these files:** their deterministic `ci_safe` suites, which run in
  **`validate` — the REQUIRED, locally-runnable, blocking gate** — not this advisory/deploy one. As of
  #1636/#1652: `test_livekit_audio_frame_bytes.py` (ingest **fidelity** — a whole utterance through
  `_AudioStream`), `test_livekit_vad_segmentation.py` (what the agent *does* with that stream),
  `test_livekit_media_authz.py` + `test_voice_livekit_session_harness.py` (the HTTP media endpoints,
  server and browser side), `test_livekit_failure_paths.py`, `test_livekit_stream_tts.py`,
  `test_voice_livekit_{fast_tier,health,lifecycle,ondemand}.py`. When you change a file in this lane,
  **the test you add there is the verification**; the replay run is the forcing function that makes
  you look.
  - **Accuracy note, because a list like this rots into a lie:** `test_livekit_aiortc_tasks.py` is
    **not** in that set — it carries no `ci_safe` marker, so it runs only in the Jetson
    full-directory lane, never in `validate`. Checked by grep on this commit, not assumed.
  - **And the trap one layer up, fixed in #1636:** the fidelity suite `importorskip`s `av`/`aiortc` at
    module scope, so until that PR installed those wheels in the slim lane it **skipped** in
    `validate` and read green while proving nothing. `test_livekit_ci_dep_guard.py` now fails the lane
    if they go missing again — a module-scope `importorskip` inside a gating suite must always be
    paired with a guard that makes its absence loud.
- **Why gate them anyway:** the tuple is a **forcing function, not an isolation harness** — the same
  bargain already accepted for every serving unit in it. Ungated, a change to the *selected* WebRTC
  backend reaches the box with nobody looking, which is how a bug that made ~25–33% of every frame on
  that path FFmpeg plane padding carrying stale PCM (silent input emitting near-full-scale audio into
  the VAD and Moonshine) survived from 2026-05-18 to 2026-08-04 and tripped no gate at either end.
- **Cost, measured not guessed:** `livekit_aiortc.py` 3 commits ever (last 2026-06-28),
  `routers/voice_livekit.py` 15 (last 2026-07-21), against 111 for the already-gated
  `routers/voice_tts.py` — a handful of replay runs a year.
- **The real fix is DEFERRED, and it is a project, not an omission.** A true LiveKit-lane probe needs
  the LiveKit container up (7880 + the 50000–50200 UDP range), a synthetic WebRTC *publisher* pushing
  corpus WAVs as an Opus track, token minting, the agent loop attached, and a second ~2.3 GB
  Moonshine+brain+Kokoro load under `flock /tmp/zoe-voice-harness.lock` on a box where two Kokoro
  loads OOM. **The small piece of it has since SHIPPED, and not as a probe stage:**
  `test_livekit_audio_frame_bytes.py` (#1636) feeds corpus WAVs — plus a deterministic speech-shaped
  synthetic, so CI runs it too — straight into `_AudioStream` and asserts the emitted PCM (envelope
  correlation, duration, silent tail), with `_drain_padded` reproducing the pre-fix read as a
  permanent negative control. No server, no GPU, no flock, and it lives in `validate` where it
  **blocks** — stronger than any advisory artifact. What remains deferred is only the end-to-end
  WebRTC leg (publisher + container + live stack); the ingest arithmetic is now covered
  deterministically.
- **Deliberately ungated — the browser side, all of it.** The vendored publisher
  `services/zoe-ui/dist/lib/livekit/livekit-client.umd.min.js`, and equally the pages that drive it:
  `dist/voice.html`, `dist/touch/voice.html`, `dist/js/auth.js` (#1652 changed all three). They run in
  the panel's browser, not on the Jetson, so a deploy gate on the box governs nothing about them and
  no probe on the box could exercise them. Their verification is the node harness
  `dist/test_voice_livekit_session.js`, run in `validate` by `test_voice_livekit_session_harness.py` —
  which since #1652 composes the **real** `js/auth.js` fetch interceptor under the real page helper,
  because a harness that stubs the innermost layer is structurally blind to every wrapper above it.

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

### Which samples the gate replays — capture time, not filename (fixed 2026-08-05)

`--last N` decides what every gate verdict MEANS, and until 2026-08-05 it did not mean what it
said. `replay_samples.py::_select` sorted by FILENAME, and corpus names are `HHMMSS_millis.wav` — a
**time of day with no date**. So `--last 20` returned the twenty highest times-of-day in the entire
corpus, with every capture date interleaved, while `voice_regression_probe.py --samples`,
`scripts/perf/measure_voice.py --last` and this document all called it "newest N". Same class as
#1642, where `sorted()[0]` picked "whichever sorts first today" and left a test red for weeks.

**Measured over the live 1003-file corpus: the name-sorted last 20 and the capture-time-sorted last
20 shared ZERO files**, and ten of the twenty name-sorted samples were from 2026-06-20 — the oldest
capture day in the corpus. The gate had been scoring a fixed, arbitrary, mostly-ancient slice.

Selection is now `(mtime, basename)`, newest last. **mtime is trustworthy here, verified not
assumed:** 998 of 1003 files have an mtime whose HH:MM:SS equals their filename's exactly (the other
5 differ by one second — the name carries milliseconds, so `083032.938` is written at `083033`), and
`ctime` equals `mtime` to the nanosecond on every file, so nothing has ever copied, rsynced or
restored the corpus. The WAVs are bare 44-byte canonical RIFF with no BWF `bext`/`LIST` chunk and
there are no sidecar metadata files, so **mtime is the only true timestamp the corpus carries**. If
it is ever bulk re-timestamped, the fix is to put the date in the capture filename — not to trust
the name again.

`--since` is unchanged and **still filename-based on purpose**: it is a time-of-day filter spanning
every date, not a recency filter. It was always honest about that, and silently redefining it would
be the same bug pointed the other way. It now says so loudly in `--help` and prints a note to stderr
when used. **`--since-date YYYY-MM-DD`** is the capture-time counterpart, consistent with `--last`.

**Consequence for the baseline — this needs an operator decision, once.** The
2026-07-16 baseline (brain 1868 ms, e2e 1896 ms, STT 579.5 ms, OK 19/20) was measured over the
name-sorted slice, and it stores only aggregates — no file list — so nothing detects the swap. With
zero overlap the next probe scores twenty entirely different recordings and any movement is the
sample change, not a regression. **Run a fresh probe, read the diff as informational, then
`--update-baseline` deliberately.** Do not treat the first post-change run as a regression signal.

## Stopping the brain does NOT guarantee it restarts (2026-07-26)

Freeing RAM by stopping `llama-server` — the documented move for a build or training window —
has a trap that cost ~20 minutes of total voice outage on 2026-07-26.

**What happened.** Brain stopped for a Docker build. Build succeeded. Brain then refused to
start, crash-looping with:

```
ggml_backend_cuda_buffer_type_alloc_buffer: allocating 2493.32 MiB on device 0:
cudaMalloc failed: out of memory
```

...while `MemAvailable` showed **6.5 GB free**. Dropping page cache changed nothing.

**Why.** This is Tegra unified memory: the brain needs a ~2.5 GB *contiguous CUDA* buffer, and
that is a different resource from free system RAM. While the brain was down, the **Kokoro
sidecar expanded into the freed GPU memory and did not give it back** (`kokoro-tts.service`,
PyTorch/CUDA backend, ~2.45 GB RSS holding `/dev/nvmap`). System RAM was plentiful; GPU memory
was not.

**The ordering that works** — stop Kokoro too, and start the brain FIRST so it claims its
buffer before anything else can:

```bash
systemctl --user stop llama-server.service
systemctl --user stop kokoro-tts.service     # ← the step that is easy to forget
# ... do the RAM-hungry work ...
systemctl --user start llama-server.service  # ← brain first: it needs the CONTIGUOUS buffer

# WAIT — do not just probe once. The model takes ~60-120s to load, so a single `curl`
# fails immediately and the next line would start Kokoro into the memory the brain is
# still claiming — reproducing the exact OOM this ordering exists to prevent.
for i in $(seq 1 120); do
  curl -sf http://127.0.0.1:11434/health 2>/dev/null | grep -q ok && break
  sleep 2
done
curl -sf http://127.0.0.1:11434/health | grep -q ok || { echo "brain did not come up — do NOT start Kokoro"; exit 1; }

systemctl --user start kokoro-tts.service    # Kokoro fits in what remains
```

**Diagnosing it:** `sudo fuser -v /dev/nvmap` lists the GPU holders — that is the question to
ask, not `free -m`. A CUDA OOM with gigabytes of free system RAM always means a *GPU* holder,
so find it rather than dropping caches.

**Two process notes from the same incident:** run the long build in the BACKGROUND (a
foreground timeout killed it with the brain already stopped — silent Zoe, no build running),
and if a `trap` restarts the brain on exit, do not also call `start` explicitly — the race
produces a "Job failed" that looks like a hard failure while the service is actually mid-load.

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

**1b. The residual of that fix: wake-word bleed on the wake path only.**
Widening pre-roll to ~1.6 s (`PREROLL_CHUNKS=20` x 1280 @ 16k) deliberately reaches back to *before*
"Hey" — at 12 chunks it opened mid-phrase and ate the command onset. The cost is that the wake phrase
itself is now inside essentially every wake-triggered capture, where it corrupts the head of the
command. **Follow-up turns take a different path** (`FOLLOWUP_LOOKBACK_CHUNKS=4`, no wake word spoken),
which makes them a free control group — the asymmetry between the two is the measurement:

| metric (Pi `voice.log`, n=393 wake / 607 follow-up) | wake path | follow-up |
|---|---|---|
| wake token leaks into the transcript | 26.7% | 4.4% |
| user repeats within 60s (near-duplicate utterance) | 27.2% | 16.6% |

Verified by transcribing the bursts of a suspect capture separately: burst 1 is literally `"Hey Zoe"`,
and removing it turns `"Let's see what they're like"` back into `"What's the weather like?"`. Matched
pairs recur throughout the log — the same phrase misheard on wake, then correct on the repeat seconds
later (`"Two on the one."` -> `"Turn on the light"`, `"Most of the time"` -> `"What's the time?"`).

Two failure shapes, and only one is fixable in software today:

- **Text bleed-through** — the wake word survives into the transcript because Moonshine split it
  across lines (`["Hey", "Zoe. Show me my contacts."]`) or a pre-roll filler line preceded it
  (`["Yeah.", "Hey Zoe.", ...]`), so the leading-wake-line drop never fired. `_strip_wake_word` now
  tolerates both (`stt_wake_strip.py`).
- **Acoustic corruption** — the wake word perturbs the command head with no wake token left in the
  text (`"What's the weather like?"` -> `"Let's see what they're like"`). This is the *majority* of
  the damage and no transcript-side rule can reach it.

**Do not "fix" this by trimming the pre-roll audio.** Measured over 54 wake-path corpus clips, cutting
the wake burst changed 19 transcripts: ~6 repaired, ~4 broken (`"Turn on the light"` -> `"10 on the
light"`), rest a wash — the same result `_prepare_audio_for_moonshine` records for every other
per-sample edit. Narrowing `PREROLL_CHUNKS` re-opens the onset clipping in **1** above. A real fix
needs the daemon to send the *wake-fire offset* as metadata so the server can drop transcript lines
that end before it — selection, never resampling.

**2. TTS slower than real time (reply plays back chopped).**
`turn_stream` synthesizes the reply sentence-by-sentence and feeds a single persistent `aplay` pipe.
That only works if synthesis outruns playback. On a **CPU** backend Kokoro ran at **RTF
~1.0–1.8x** — slower than real time — so the pipe *had* to run dry at every chunk boundary (ALSA
underrun -> gap). Short chunks made it worse: per-call overhead pushed a 10-char stub to RTF 1.8x,
so the very chunking that bought fast first-audio was what starved the pipe. Fix: the Kokoro
PyTorch sidecar on CUDA, RTF **0.08x** (it falls back to CPU only if CUDA cannot load, and reports
`degraded=true` on `/health` when it does). **Diagnostic: if replies ever sound chopped again,
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


## Endpointing fast-tail rollout (staged 2026-07-28)

`ZOE_VAD_TAIL_MS=640` is live on zoe-pi (`/home/pi/.zoe-voice/.env.voice`): the daemon
closes a turn after 640 ms of consecutive DEEP silence (Silero prob < `ZOE_VAD_TAIL_DEEP_PROB`,
default 0.10) instead of the full 800 ms — ambiguous pauses still wait the full tail, and a
Silero inference failure counts toward the long tail but never the deep counter (a broken VAD
degrades to the old timeout, never a hang and never an early cut). Deployed two-step from a
clean main worktree (code flag-off first — proven byte-identical no-op — then the flag). No
backup copy is kept beside the installed daemon — git is the history and rollback is the flag,
so a stale `.bak-*` would just be a second source of truth for a panel diagnostic to trip over.
Rollback: set `ZOE_VAD_TAIL_MS=0` and
`systemctl --user restart zoe-voice` — behaviour is byte-identical to pre-flag. Corpus evidence
(#1573): −160 ms median tail on ~80 % of turns, +2.7 pt false-cut upper bound; the probe
(`scripts/perf/measure_endpointing.py`) can exercise old and new behaviour for before/after.
