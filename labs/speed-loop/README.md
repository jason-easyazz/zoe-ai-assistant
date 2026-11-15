# [lab] Speed loop

Borrow autoresearch's pattern: one metric, one bounded experiment, one narrow
surface, keep/discard, repeat, morning ledger. No agent SDK, subscription, network,
model, service import or extra dependency. Any coding agent follows [program.md](program.md).
The driver changes a candidate dictionary, never repository or runtime settings.

The number is paired median **first-response milliseconds**, negative delta = faster.
Fake numbers are invented. Real first sound remains unmeasured here.

## One proposed window

[lab] windows.json proposes Monday–Friday **10:00–14:00 local time**, no weekends.
Edit after checking actual occupancy. This is not evidence the house is empty.
The loader rejects overnight, overlapping, and maintenance-overlapping spans
(01:55–03:30, 04:00–04:55), even if someone adds them to the proposal.
Starts outside a window refuse; every arm checks the deadline. A partial paired
corpus is ABANDON, never KEEP. Budget = 64 distinct synthetic turn IDs per arm,
30 simulated seconds per experiment, 100 experiments / 600 simulated seconds per
invocation by default. Real elapsed time is additionally bounded by the caller's timeout.

Offline demonstration (explicit synthetic Monday; does not authorize real work):

```sh
~/.zoe/agent-tools/bin/quiet-wait.sh
flock -x -w 14000 /tmp/zoe-precheck.lock timeout 30 python3 labs/speed-loop/loop.py \
  --fake-start 2026-11-16T10:00:00+00:00 --seed 7 --max-experiments 100
```

Follow the host memory/quiet rules before running. To follow program.md exactly,
use `--max-experiments 1` with the same seed and output directory. The seeded
schedule covers every knob once before trying extra values. Without
--fake-start the fake clock starts at current local time; elapsed measurement time
is still simulated. The driver does not wait for future windows or run as a daemon.
Artifacts must remain inside this lab. Use a fresh `--output` directory for a new
study; continuing one ledger restores its best and skips tried values. Do not mix
seeds, surfaces, corpora or budgets in one study. A crashed proposal/ledger write is
not transactional: review orphan proposal files before continuing. One driver at a time.

## KEEP rule and noise

[code] Run incumbent/candidate on the same N turn IDs, interleaving arm order with
a seeded shuffle. Re-measure the incumbent every experiment, rather than compare
with a stale stored median. KEEP requires all four:

- Paired median (after − before) ≤ **−15 ms**.
- Exact one-sided sign test p ≤ **0.01**, ignoring exact ties.
- Every paired said-versus-did outcome signature identical.
- Candidate p90 ≤ incumbent p90 + **5 ms** (nearest-rank percentile).

64 turns per arm is the proposed default: with the fake independent ±8ms uniform
arm noise, a true 20ms saving always has the right sign and p = 2^-64; the paired
median typically clears 15ms. [inferred] For noisier real measurements, if only
65% of paired deltas favor the candidate, about 64 pairs gives borderline sign
evidence (42/64 gives p about 0.0084); it does NOT guarantee power or a 15ms median.
Choose sample size/noise from a pilot; 20ms is not detectable under arbitrary noise.
The 0.01 test is per experiment, not family-wise corrected; repeated search can
select noise. Independently confirm retained changes before a PR, and use a held-out
corpus. Outcome equality is only as strong as the instrument's signature.

surface.json contains code defaults, not live flag values. Allowed values are
conservative lab candidates, not a claim of safety. Env names are registered in
flag_registry.py; constant entries require a reviewed code diff. References and
defaults are checked by the local tests. The loader enforces a positive symbol/file
allowlist plus a non-removable denylist and refuses unknown/redirection targets.
It never executes a file path or arbitrary command from the surface.

## Morning tally

Read runs/<run>/ledger.jsonl: knob, numeric/bool value, n, median before/after,
paired delta_ms, verdict/reason enums, duration seconds, p. No utterance, reply,
raw ID, exception string or backend payload is stored. KEEP emits
keep-NNNN.patch.json (target, line, old/new value, full accumulated candidate).
This is a proposal description, not an automatically applicable git patch.
Proposals may overlap other builders' work; the coordinator reviews them.

| Kept change | Paired proven ms | Running total ms | Evidence |
|---|---:|---:|---|
| knob = value | −delta_ms | sum of retained deltas | ledger row / revision |

Totals add sequential measured deltas, not independent predictions. The synthetic
100-experiment result belongs in [fake-tally.json](fake-tally.json), currently
**not_run** because the mandatory quiet-wait cannot access the user service bus
inside this sandbox. The fake model has four helpful, four harmful,
four inert and four outcome-breaking knobs. Effects are deliberately unrelated to
real knob performance. “Found” counts distinct helpful knobs retained, “missed”
counts helpful knobs never retained; this does not mean every optimal value found.
The ground-truth ms is the final retained configuration's noiseless saving.

## ReplayBackend: deliberately unavailable

Without --i-own-the-box it refuses; with that flag it still raises
NotImplementedError. There is no subprocess, HTTP call, lock acquisition or env
read in the stub. It must not be mistaken for a working real adapter.

[code] Future adapter contract, after owner grants exclusive use of the one brain slot:

1. Require a real clock (never --fake-start), explicit isolated worktree/service-dir,
   fixed private clip manifest, synthetic user, scratch result paths and quiet window.
   Run quiet-wait outside the harness lock; recheck window and remaining wall cap.
2. Spawn a killable process group under `flock /tmp/zoe-voice-harness.lock`, with
   timeout ≤ remaining window/run/experiment cap. Kill and reap the WHOLE group
   on expiry. Do not implement real work as the cooperative synchronous fake call.
3. Use scripts/maintenance/voice_regression_probe.py with --service-dir,
   --baseline, --results, --trend, --samples N, --timeout, --user and --sample-dir;
   use a pinned baseline manifest (not newest-N) and never --update-baseline on
   an experimental arm. The scripts/perf/measure_voice.py instrument supports
   --files-from <same-private-manifest> --service-dir --user --json --timeout.
   ZOE_PERF=1 and candidate flag overrides belong to that lab child's env only.
   Budget must include both instruments and both arms, not N new clips per tool.
4. Current instruments resolve service env and brain endpoints: audit isolation
   before enabling them. Constant candidates need a private patched worktree,
   never source-file edits on the live checkout. No service restart here.
5. Parse per-turn numeric latency and stable route/action/outcome signatures from
   private artifacts; align by hashed corpus IDs, require exact N and no errors.
   Aggregate-only output cannot support this loop's paired rule: extend the adapter
   or instrument explicitly; never fabricate a paired first-sound result.

The warm replay harness stops **before TTS**. Its timings are **relative drift**,
not real speech-end-to-first-sound latency, and cannot validate TTS chunk or panel
endpoint knobs. Those need isolated endpoint replay and a first-PCM instrument
(e.g. measure_tts.py plus actual playback timing), semantic checks, and owner listening.
The one slot requires the box to itself even during daytime. No live replay,
latency win, deployment safety or production improvement is proved by this lab.
