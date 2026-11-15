# [code] Speed loop: guarded real replay adapter

The metric is paired **warm pre-TTS milliseconds** (STT + resolve + brain), not
speech-end-to-first-sound. The real backend is implemented; **no real run has
been performed in this task**. Nothing imports this lab in production. Nothing
here changes config, commits, pushes, starts a model, or installs a unit.

[code] `run_window.sh` checks guards, calibrates if missing, runs bounded child
experiments, and writes `runs/window/morning.md`. Each child imports the canonical
replay harness, using child-local STT with an untimed warm-up. Brain calls
are dry (`replay_isolation=True`), fast-tier writes are disabled, and the user is
synthetic. It still consumes the household's ONE brain slot: an owner-granted
quiet window is required. This is not deployment or voice-gate evidence.

## Source evidence and the three classes

[code] All locations below are in this checkout; loader refuses stale references.
`F` = `services/zoe-data/zoe_flue_client.py`, `T` =
`services/zoe-data/routers/voice_tts.py`, `S` =
`services/zoe-data/voice_first_sound.py`, `M` =
`services/zoe-data/memory_service.py`, `D` = `scripts/setup/zoe_voice_daemon.py`.

| Knob | Class | Executable read / definition |
|---|---|---|
| ZOE_FLUE_PREPOST_CONCURRENT | a, automatic | F:1948 |
| ZOE_BRAIN_VOLATILE_AFTER_USER | a, automatic | F:3246 |
| ZOE_MEMORY_TICK_DEFER_S | a, automatic | M:429 |
| ZOE_CONTINUITY_CEILING_S | a, automatic | F:1636 |
| _HOUSEHOLD_FETCH_BUDGET_S | b, owner code patch | F:1179 |
| _CONTINUITY_PORTRAIT_TIMEOUT_S | b, owner code patch | F:1628 |
| _PERSONA_REFRESH_BUDGET_S | b, owner code patch | F:509 |
| ZOE_VOICE_FAST_FIRST_AUDIO | a, unexercised TTS | T:1209 |
| _FIRST_UNIT_CLAUSE_MIN | b, owner code patch | T:1204 |
| _FIRST_UNIT_SOFT_CAP | b, owner code patch | T:1205 |
| ZOE_FIRST_SOUND_CLAUSE | a, unexercised TTS | S:26 |
| ZOE_FIRST_SOUND_CLAUSE_MIN_CHARS | a, unexercised TTS | S:63 |
| ZOE_FIRST_SOUND_CLAUSE_MIN_WORDS | a, unexercised TTS | S:64 |
| ZOE_VAD_CLEAN_TAIL_MS | c, panel only | D:233 |
| ZOE_VAD_CLEAN_FALL_CHUNKS | c, panel only | D:234 |
| ZOE_VAD_HESITATION_TAIL_MS | c, panel only | D:236 |

**a:** process environment read at call time. Four pre-TTS flags are measured;
four TTS flags are excluded because this corpus adapter never synthesizes audio.
**b:** needs owner flip/code patch and service restart for production. These five
entries are Python constants, not environment controls. They could be measured
in a separately patched private checkout, but this adapter deliberately does not
patch source; saying they are technically live-service-only would be false.
**c:** panel daemon import-time environment constants: unavailable to the Jetson
replay. All excluded entries appear in the morning report.

[code] Instrument construction evidence:

- `scripts/perf/measure_voice.py:142-178` builds a replay subprocess using the
  service interpreter; `:202-235` projects per-row timings and route/heard hashes.
- `services/zoe-data/tests/replay_samples.py:97-105` loads `.env` with
  `setdefault`, so explicit child overrides win. `:553-629` loads env, initializes
  DB/router/tiers and imports voice functions **in that process**. `:645-710`
  runs STT locally (remote is an optional instrument mode), then fast tiers in-process with `allow_writes=False`.
  `:711-735` runs the brain with replay isolation. `:769-776` emits JSON.
- `scripts/perf/measure_tts.py:139-148` imports the real first-unit extraction
  functions in-process; `:201-229` applies them to whole replies and calls Kokoro.
  That measures synthesis of an extracted unit, not streaming clause-release time.
- `typed_env.py:1-16` documents call-time reads. `_f` registry declarations are
  metadata, not executable reads: the first-sound reference was corrected to S:26.
- `scripts/maintenance/voice_regression_probe.py` is the deployment regression
  instrument. Its default corpus directory is read as AST data, never imported
  or printed. We do not invoke it or write its shared gate artifacts.

[code] `instrument.py` runs the same underlying replay as the timing orchestrator,
with its JSON/manifest files intercepted **in memory**. It does not call the outer
probe: that would write the shared gate result and cannot interleave turns. The
wrapper emits the same numerical stage totals plus outcome fields. Replay prints,
stderr and raw text JSON never reach files. Driver parsing positively selects
numeric fields and hashes outcome fields; unknown child keys are discarded.

## One real experiment

1. Check every guard: current windows.json span, `night_busy.sh voice` exit **1**,
   >=1500 MiB MemAvailable, and >=10 min since a conversation logger timestamp.
   Unknown activity, unreadable logs or a guard error refuses. The actual tracked
   night guard is `scripts/landing/night_busy.sh`; the requested maintenance path
   does not exist. That guard also requires the owner's daytime permission marker.
2. Acquire nonblocking `/tmp/zoe-voice-harness.lock`, recheck guards, bound the
   experiment by both wall cap and window close. Recheck guards during child runs.
3. Pin a seeded subset of top-level WAVs by content hash; keep names private in
   process memory. Refuse an insufficient corpus. Same subset and order in both arms.
4. For each turn launch A, B, A, B as fresh process groups with explicit automatic
   flags in the child env only. Capture stage timings and said-versus-did signature
   (verdict + route + heard hash). Average each arm's two observations.
5. KEEP requires median saving >=15 ms, exact one-sided sign p<=.01, unchanged
   outcomes, no stage median regression >5 ms, and p90 no more than 5 ms worse. Errors, unstable outcome, budget
   expiry or activity cancel evidence. Kill child process group and release lock.
6. Append sanitized per-turn results to replay-ledger.jsonl; KEEP updates only the
   in-memory lab incumbent. Resume restores those values. Write an unapplied product-default patch
   and draft PR body, then summarize sequential measured deltas. No live apply.

[code] Source protection resolves each allowed symbol's unique AST definition/read,
checks its exact line, and rejects forbidden enclosing function/class names and
symbols. It never authorizes a target merely because the metadata line is positive.
Tests plant wrong lines, a different symbol's line, a forbidden constant and a read
inside an auth function; each must refuse, with an allowed-function positive control.

## Calibration and small wins

[code] A calibration runs three independent A/A interleaved batches. It records
paired-difference standard deviation **sigma**, observed false-KEEP count/rate,
stages exercised and sample-size planning numbers. Calibration is bound to HEAD +
source hashes (dirty changes included), config, env-file/inherited-env hashes,
interpreter, corpus hashes and seed. A stale/missing calibration causes calibration;
nonzero false keeps or insufficient N refuses experiments. No numeric real noise
or real false-keep claim exists before the first window.

[inferred] With approximately normal independent paired noise and 80% power at
one-sided alpha=.01, the sign/median planning approximation is:

`N(delta) = max(7, ceil((pi/2) * ((2.32635 + .84162)*sigma/delta)^2))`

| Measured paired sigma (ms), illustrative | 15 ms effect | 30 ms effect |
|---|---:|---:|
| 100 | 701 | 176 |
| actual calibration | calibration.json turns_15 | calibration.json turns_30 |

These are **distinct turn pairs**, four child observations per pair. They are
planning estimates, not guarantees under skew, autocorrelation, p90 or correctness
gates. At the exact 15 ms KEEP boundary there cannot be 80% probability that the
sample median clears 15 ms: a symmetric estimator clears it only about half the
time. The table describes detection vs zero; comfortably retaining a 15 ms saving
requires lowering a predeclared practical threshold or measuring a larger effect.
The loop does not quietly lower its 15 ms policy. Three A/A batches give a very
weak false-keep estimate (0/3 is not evidence of a 1% long-run rate). Independent
held-out confirmation and search-wide error control remain required before apply.

## Loop efficiency

[inferred] Rank by stage's plausible saving x knob range / common measurement
cost. These priorities are hypotheses, not measurements. Skip stages unexercised
in calibration; this conservatively skips memory if no brain turns ran. Every K
experiments (default 3), refresh both A observations. Between refreshes reuse the
first cached A while **still measuring a fresh A** drift sentinel on every turn:
logical cached-A B fresh-A B; saves one child per pair. >15 ms sentinel drift
invalidates the experiment/cache. Cache keys include full incumbent + corpus;
KEEP changes invalidate reuse naturally. Use `--refresh-every 1` for strictly
fresh ABAB throughout. Fresh children do not reproduce multi-turn KV-cache wins.
Early stop can only DISCARD: even all remaining wins could not pass the fixed-N
sign test, or an outcome already differs. Tests compare KEEP sets with reuse and
early stop on/off, and verify ranking/filtering preserve eligible candidates.

## Coordinator commands (inside an owner-granted window only)

[code] The committed proposed window is weekdays **10:00–14:00 local**. A timer
wake is not authorization: the night guard's owner daytime marker must also permit
voice work. Do not change it from the lab. windows.json rejects maintenance spans
01:55–03:30 and 04:00–04:55. Start with dry-run; it walks all guards and the lock
without importing service code, loading a model or sending a request.

```sh
labs/speed-loop/run_window.sh --dry-run
labs/speed-loop/run_window.sh --calibrate --turns 64 --cap 3600 \
  --env-source /home/zoe/assistant/services/zoe-data/.env \
  --output labs/speed-loop/runs/first-window
labs/speed-loop/run_window.sh --turns 64 --cap 3600 --max-experiments 1 \
  --env-source /home/zoe/assistant/services/zoe-data/.env \
  --output labs/speed-loop/runs/first-window
```

Read calibration.json first: if turns_15 >64, choose a larger N and new output
folder and recalibrate. The child interpreter defaults to the service py312 venv;
pass `--python` to override. `--service-dir` defaults to THIS checkout's zoe-data;
never silently auto-resolves to live code. No command above prints env values or
the corpus path. The outer driver is stdlib only.

[code] Optional USER templates are **not installed**. Review paths before these
owner-only commands (templates assume the merged checkout at `%h/assistant`):

```sh
install -Dm644 labs/speed-loop/speed-loop.service ~/.config/systemd/user/speed-loop.service && install -m644 labs/speed-loop/speed-loop.timer ~/.config/systemd/user/speed-loop.timer
systemctl --user daemon-reload && systemctl --user enable --now speed-loop.timer
```

No template or script changes the permission marker. Runtime guards override the
schedule. Removing/disabling the timer is the owner's normal rollback.

## Offline verification and remaining proof

[code] `test_speed_loop.py` retains the original fake-backend net;
`test_replay.py` exercises real child env, ABAB, lock exclusivity/release, timeout,
activity cancellation, sanitization, calibration, stage filtering and cache reuse
using only FakeInstrument. Test fixtures use synthetic text. Fake ground-truth
results are not real performance results.

[lab] This build's test execution status is recorded in the coordinator report.
No real voice instrument, API call, model load, config change, restart, timer
installation, git write or live deployment was performed. First-window noise,
false keeps, latency, outcome stability, activity-log parser compatibility,
private env/import integration, wrapper write-refusal compatibility, and the
synthetic-user corpus coverage are unproven. TTS, actual first audio, panel VAD,
combined retained changes and real multi-turn caching remain outside this adapter.

[code] Activity parsing follows `logging_setup.py:69-70` (ISO timestamp including
numeric offset, level, logger). Child-local STT is deliberately used: remote STT
logs under the watched voice logger and would self-cancel. The wrapper primes the
real STT function once before the timed replay. Each child thus loads another
Moonshine copy while it runs; 1500 MiB is the existing in-process harness floor,
not a measured peak for this adapter. A failed model/import/warm-up refuses the
arm. No TTS model is loaded. The warm-up and replay share one event loop; import-time pools/async locks in
the imported pipeline remain an integration risk for the first owner window.

[inferred] The cap is 3600 s per experiment (including child startup and warm-up);
three calibration batches plus experiments must still fit inside the window. The
four-fresh-child design is conservative and expensive. Startup/warm-up cost is
not counted as a voice latency saving. A persistent instrument worker is a future
optimization requiring isolation and cache-parity proof, not an assumed speedup.

[code] Admission checks require 1500 MiB before each child. While that child holds
its admitted memory, polling uses an 800 MiB emergency reserve, not another full
1500 MiB admission budget. Window/activity/night guards remain active. Polling
runs every two seconds; an independent watchdog kills the group at its deadline
if a guard read stalls. A household turn can contend until the next poll.
