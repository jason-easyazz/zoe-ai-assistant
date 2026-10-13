---
type: research
title: "Making WeSpeaker ResNet34 panel-ready (2026-10-13): INT8 QDQ, ORT options, enrolment, cohort-mean centring, thresholds, adaptation"
date: 2026-10-13
status: measured on the Jetson CPU and the panel Pi 5. No live path, flag, service, unit or profile changed. Aggregates only - no clip names, transcripts, audio or embeddings (docs/knowledge/biometric-retention-policy.md). Owner-corpus speaker labels are INFERRED (bake-off record section 2.2); the labelled-public axis is the one to trust for ranking. The production swap is NOT built here.
description: Every lever the owner asked for, each run as a measured experiment against the bake-off's own trial design (same frozen owner corpus, same impostors, same public axis, baseline reproduced to the digit) with bootstrap CIs and paired deltas. Winners - static INT8 QDQ (2.3-2.8x faster in the same session, 3.5x against the bake-off record; no accuracy loss), ORT spinning off, cohort-mean centring, 5 clips with outlier rejection, length-dependent thresholds, retuned adaptation guards. Losers - dynamic INT8, FP16 (kept as fallback), S8 activations, duration caps, silence trim/strip, length-weighted enrolment, AS-norm on top of centring, threads beyond 2. Recommended panel configuration and a migration note.
evidence_labels: "[measured] this run, scripts/perf/speaker_bakeoff.py + speaker_lab.py | [record] copied from docs/research/speaker-model-bakeoff-2026-10.md | [src] file:line read in this worktree | [derived] arithmetic on measured numbers | [hypothesis] / [unverified] not confirmed"
---

# Making WeSpeaker ResNet34 panel-ready (2026-10-13)

Owner direction (2026-10-13), after the bake-off ([speaker-model-bakeoff-2026-10.md](./speaker-model-bakeoff-2026-10.md)):
"look at the best one and how to optimise it". The best on accuracy was WeSpeaker ResNet34 (sherpa-onnx export,
256-d): owner-corpus strict core EER 1.3 %, public far-field EER 3.3 % - but **369 ms p50 / 1002 ms p95 per clip on the
panel Pi 5** (CAM++ 133 ms), and a 256-d width that collides with Resemblyzer's. This record makes it production-ready
on paper: as fast and as accurate as it can get, with every number comparable to the bake-off's.

## 0. Answer first

**Recommended panel configuration** (details and the full lever table below). The latency headline is the **same-session** FP32 -> INT8 pair; the 369 -> 106 ms line is a secondary cross-session comparison against the bake-off record:

| | bake-off baseline | recommended | change |
|---|---|---|---|
| model file | `wespeaker_en_voxceleb_resnet34.onnx`, FP32, 26.5 MB | same network, **static INT8 QDQ** (U8 activations, S8 per-channel weights, MinMax calibration on 100 public dev-clean clips), 6.8 MB | 3.9x smaller |
| ORT | `ORT_ENABLE_ALL`, 2 threads | `ORT_ENABLE_ALL` (anything below EXTENDED is a trap, section 3.3), 2 threads, **`session.intra_op.allow_spinning=0`** | |
| Pi 5 latency, **same session, same 30 clips** (mean 2.86 s), un-throttled | FP32 **p50 232 ms [228-238] / p95 665 ms** (3 clean passes of 4) | INT8 + spin off **p50 89 ms [87-90] / p95 225 ms** (4 clean passes) | **2.6x p50 / 3.0x p95**; 16-clip sweeps agree: FP32 203 -> INT8 83 - 89 ms (2.3 - 2.4x), 200 -> 71 - 75 ms with spinning off on both (2.7 - 2.8x) |
| Pi 5 latency, 3.65 s clips - **secondary, cross-session** | p50 369 ms / p95 1002 ms [bake-off record, 07:00, Pi at 65 C] | p50 106 ms / p95 242 ms (60 clips, median of 3 passes, un-throttled, Pi at 73 - 81 C, 4 h later) | 3.5x / 4.1x - a different session and a cooler Pi for the baseline, so read it as supporting only; under the 150 ms goal either way |
| Pi peak RSS (fresh process) | ~233 MB [record] | **199 - 218 MB** | same |
| owner strict core, 3-clip enrolment: EER / FRR@FAR1 % / FRR@FAR0.1 % | 1.31 % / 1.61 % / 6.42 % | **0.72 % / 0.48 % / 4.03 %** | EER -0.58 pp [-0.71, -0.45] |
| same, nested design (sessions with 8 or more clips): 3 / 5 / 8 enrolment clips | 1.19 % / 1.31 % / 4.16 % (3 clips, FP32 raw) | 0.69 % / 0.45 % / 2.33 % (3) ; **0.53 % / 0.29 % / 1.85 % (5)** ; 0.47 % / 0.17 % / 1.33 % (8) | |
| owner lenient core (3-clip): EER / FRR@1 % | 3.12 % / 5.80 % | **2.13 % / 3.75 %** | EER -1.02 pp [-1.30, -0.81] |
| public far-field (3-clip): EER / FRR@1 % / FRR@0.1 % | 3.28 % [2.52, 4.04] / 6.41 % / 19.70 % | **2.57 % [1.87, 3.26] / 5.09 % / 16.70 %** | EER -0.72 pp [-1.14, -0.31] |
| public, 4 enrolment clips | 2.95 % (FP32 raw) | 2.36 % / 5.04 % / 16.18 % | |
| enrolment | 3 clips, mean | **5 clips (8 is better still), mean of L2-normalised embeddings, leave-one-out outlier rejection (tau 0.30)** | |
| score normalisation | raw cosine | **public far-field cohort-mean centring** (one 256-float vector); AS-norm not adopted | |
| thresholds | one global | **two length groups (2 s or less / longer)**, re-derive on household shadow data | short-clip FRR 1.52 -> 0.88 % at FAR 1 % |
| runs where | - | on the Pi, in the voice daemon | the Jetson buys no latency (section 6) |

What won, in the order of effect: **INT8 QDQ** (latency, free accuracy-wise), **cohort-mean centring** (nearly halves the owner
EER, -0.8 pp public), **more enrolment clips** (-0.19 pp EER from 3 to 5, -0.36 to 8, paired), **outlier rejection** (free on clean
enrolments, removes about 95 % of the damage of one stranger clip), **length-dependent thresholds** (equalises FAR, halves
short-clip FRR), **spinning off** (about -13 % latency). What lost is in the table in section 1 - notably FP16 (lossless, only
1.3x), dynamic INT8 (slower than FP32), S8 activations (+0.7 pp EER), every duration cap, and silence trim/strip, which
costs +2.3 to +2.5 pp on the owner corpus and **nothing** on the public axis - a signal that part of the owner-corpus
separation is channel, not voice (section 7). Treat the owner-corpus rows as relative evidence only.

Not built here: the production swap (daemon encoder, profile table, thresholds, self-test). Section 8 is the migration note.

## 1. Every lever: baseline vs result, with intervals

Design and baseline are the bake-off's, **reproduced to the digit** before anything else was changed (section 2.1).
Owner = strict core (1,068 clips), 30 enrolment draws, 756 impostor probes per draw; CIs are 95 % bootstrap over probe clips
(200 resamples) and **deltas are paired** (the same resamples for both arms). Public = 40 LibriSpeech test-clean speakers,
far-field, 3 of 4 enrolment clips. "EER" is a rate; deltas are percentage points (pp). `*` = paired CI excludes 0.
Speed rows are Pi 5, `nice 10`, 2 threads unless stated, median of 3 passes with the pass-to-pass range in brackets, and
**only passes the firmware did not throttle** (section 3.1). Speed rows from sweeps A - D use 16 public clips (mean 2.43 s);
the final row group (section 3.4) uses the bake-off's 60 clips (mean 3.65 s).

| # | lever | baseline | result | verdict |
|---|---|---|---|---|
| 1a | ORT graph level none / basic / extended / layout (FP32) | all 203 ms [198-206] | 201 / 200 / 204 / 207 ms | **no effect**; embeddings identical (cosine 1.0, min over 40 clips) - no CAM++-style trap for ResNet34 |
| 1b | `enable_cpu_mem_arena=0`, `enable_mem_pattern=0`, `mode=par,inter=2` (FP32) | 203 ms | 205 / 198 / 216 ms | **no effect**; parallel mode is 6 % slower |
| 1c | intra-op threads 1 / 2 / 4 (FP32) | 203 ms (2) | 301 [301-312] / 203 / 174 [169-176] ms | 4 threads -14 %, 1 thread +48 %; threads cannot reach the goal |
| 1d | `allow_spinning=0` (FP32 / INT8) | 203 / 83 ms | 200 / **71 [69-72]** ms (sweep C); INT8 75 vs 86 in sweep D | **keep for INT8**: about -13 %, and idle worker threads stop burning CPU |
| 2a | **static INT8 QDQ, U8 act / S8 weights, per-channel, MinMax** | FP32 203 [202-208] ms | **89 [84-93] ms** (83 in sweep C; 106 at 3.65 s, section 3.4) | **KEEP**. Accuracy below |
| 2a-acc | its accuracy vs FP32 | owner strict 1.31 / 1.61 / 6.42 | 1.22 [1.06-1.44] / 1.44 / 6.23; delta EER -0.07 [-0.16, +0.01] | no loss. Lenient core EER -0.38 pp* (better). Public EER 3.28 -> 3.30 (delta +0.05 [-0.24, +0.38]); FRR@1 % +0.75 pp [-0.27, +1.68]; FRR@0.1 % +2.34 pp* [+0.58, +4.57] (coarse tail, 476 distinct probes) |
| 2b | static INT8, S8 activations (symmetric) | U8/S8 3.30 % | public EER 3.94 % [3.05-4.86]; delta vs FP32 +0.70 pp* [+0.12, +1.23], FRR@1 % +2.13 pp* | **REJECT** (same speed) |
| 2c | static INT8 variants: per-tensor weights; last Gemm kept FP32; percentile calibration | 3.30 % public EER | 3.22 % / 3.20 % / 3.05 % | all inside noise; **no reason to leave plain MinMax** (public axis only) |
| 2d | dynamic INT8 (`quantize_dynamic`) | FP32 202 ms | 366 [355-377] ms, p95 1132 | **REJECT** on speed (1.8x slower than FP32); accuracy not run |
| 2e | FP16 weights/activations (FP32 I/O) | FP32 202 ms | 154 [152-159] ms; accuracy delta EER -0.00 [-0.02, +0.01] on owner strict, +0.00 on public | lossless but only 1.3x; **fallback** if INT8 ever misbehaves |
| 3a | fbank cost | - | numpy Kaldi front end 3 - 6 ms of 80 - 200 ms | **not a lever** |
| 3b | duration cap (first N s), INT8, owner strict EER | 1.22 % (no cap) | 4 s: 1.33 % (+0.08 pp*) ; 3 s: 1.51 % (+0.28*) ; 2 s: 2.28 % (+1.03*) ; 3 s "densest window": 1.58 % (+0.34*) | **REJECT**. Pi gain (single pass each, 3.65 s mean): p50 flat, p95 235 -> 141 (4 s) / 112 (3 s) / 94 (2 s) ms |
| 3c | edge-silence trim (40 dB), INT8, owner strict EER | 1.22 % | 3.69 % (+2.45 pp* [+2.15, +2.75]); public +0.02 [-0.45, +0.46] | **REJECT** - and a channel warning, section 7 |
| 3d | all-silence strip (35 dB), INT8, owner strict EER | 1.22 % | 3.55 % (+2.33 pp*); public +0.01 | **REJECT** |
| 4 | warm session reuse | cold load 0.04 - 0.13 s, first call 63 - 85 ms (INT8), 200+ ms (FP32) | warm p50 106 ms | load once at daemon start and run one throw-away embedding |
| 5 | Jetson / off-box | - | INT8 on the Jetson CPU: 101 ms (2 thr) / 86 ms (4 thr); FP32 260 ms | **no latency win** vs the Pi's 106 ms, plus network; section 6 |
| 6a | enrolment clips 3 -> 5 -> 8 (nested draws, sessions with 8 or more clips), FP32 raw | n=3: 1.19 % / 1.31 % / 4.16 % | n=5: 1.00 % / 1.01 % / 3.06 % (delta EER -0.19 pp* [-0.27, -0.12]) ; n=8: 0.83 % / 0.72 % / 2.30 % (-0.36 pp* [-0.46, -0.27]) | **KEEP 5, prefer 8** |
| 6b | length-weighted mean (duration, or its square root) | plain mean | n=3 0.00 [-0.05, +0.05]; n=5 -0.16 vs -0.19 for plain; n=8 -0.30 vs -0.36; under contamination +0.12 pp worse | **REJECT** |
| 6c | outlier rejection (drop a clip whose leave-one-out cosine < 0.30, keep 2 or more) | clean n=5: 1.00 % | identical to plain mean on clean enrolments (never fires); with one random stranger among 5 clips: plain mean 1.65 % / 2.47 % / 10.89 % -> **1.06 % / 1.08 % / 3.36 %** (clean is 1.00 % / 1.01 % / 3.06 %) | **KEEP** (tau 0.30 and 0.40 identical). "Always drop the lowest of 5" costs +0.13 pp on clean enrolments - reject |
| 7a | cohort-mean centring (public far-field dev-clean cohort, 240 clips, 40 speakers) | FP32 raw 1.31 % | owner strict 0.79 % (delta -0.51 pp* [-0.66, -0.34]); on INT8: 1.22 % -> 0.72 % (-0.51*), FRR@1 % 1.44 -> 0.48 %; public 3.30 -> 2.57 % (-0.77*) | **KEEP** |
| 7b | S-norm / AS-norm (top-k 20, 50, 100, 200) | raw 1.31 % | owner EER 0.76 - 0.82 % (same as centring: 0.79 %); FRR@0.1 % no better (4.0 - 6.4 %); public EER 2.13 - 2.27 % | **not adopted on top of centring** (centring + AS-norm k=50: owner EER 0.70 %, FRR@0.1 % worse 5.56 %, public 2.04 %; CIs overlap centring alone) |
| 7c | clean-speech cohort instead of far-field | - | owner delta EER +0.05 [-0.11, +0.21]; AS-norm k=20 FRR@0.1 % +7.6 pp* worse | the cohort must share the channel; **far-field only** |
| 7d | which clips make the mean | all 240 clips: 0.72 % | half the speakers: 0.71 / 0.73 %; 24 random clips: 0.71 / 0.75 / **0.86 %** | use 200 or more clips from 40 or more speakers |
| 8 | length-dependent thresholds, speaker-disjoint 2-fold CV, INT8 + centring (paired, section 2.1) | one global threshold: short (2 s or less) FAR 0.70 %, long 1.52 % at a 1 % target (gap -0.86 pp [-1.57, -0.05]) | per-group thresholds: FAR 1.20 % / 1.27 % (gap -0.10 pp [-0.87, +0.89]); FRR short **-0.62 pp [-0.87, -0.37]*** (1.52 -> 0.88 %), long +0.11 [+0.08, +0.14]*, overall +0.01 [-0.04, +0.06]. At FAR 0.1 %: FRR short **-5.02 pp [-5.86, -4.17]*** (9.91 -> 4.87 %), long +0.58 [+0.51, +0.64]*, overall -0.19 [-0.38, -0.03]* | **KEEP** (two groups; three add nothing). The gain is on short clips; overall FRR is unchanged at FAR 1 %. The FAR-gap interval is wide: the FAR equalisation is directional evidence (the global gap excludes 0, the per-group gap does not), not a tight estimate |
| 9 | #2002 adaptation on ResNet34 profiles (paired two-level bootstrap, section 2.1) | its defaults (score 0.85, anchor cosine 0.80) | **never fire** (0 updates); with the retuned gates (score 0.342, anchor 0.45): held-out FRR@FAR1 % **-0.73 pp [-1.34, -0.32]*** (0.77 -> 0.02 %), FRR@0.1 % **-5.12 pp [-7.60, -2.91]*** (5.94 -> 0.67 %), impostor FAR at the fixed 0.1 % threshold -0.09 pp [-0.19, -0.03]*, at the 1 % threshold -0.14 pp [-0.36, +0.05] (no measurable change); section 5 | retune the constants; a score gate alone admits a lookalike |

## 2. Method

### 2.1 What was held fixed, and the reproduction check

- **Owner corpus frozen to the bake-off's.** `~/.zoe-voice-samples` keeps growing; the harness gained
  `index-corpus --before "2026-10-12 19:00"`, which reproduces the bake-off's index exactly: **1,377 clips, 218 sessions,
  duration p5/p50/p95 = 1.6 / 3.12 / 8.08 s, 241 clips of 2 s or less** [measured]. The owner core is rebuilt from the
  same four voters (ECAPA, ResNet34, WeSpeaker CAM++, TitaNet-small) on re-embedded clips, excluding the 4 clips the
  incumbent's preprocessing trims to nothing (`resfail`): **strict 1,068 / lenient 1,278** - identical to the record
  [measured]. (Without that exclusion the ECAPA 2-means partition flips and the core becomes 1,039 / 1,076; the record's
  numbers depend on it.)
- **Impostors and public axis**: the same 476 LibriSpeech + 280 Kokoro far-field clips, the same 40-speaker enrolment
  design, same seeds. Nothing was regenerated.
- **Reproduction** (the new `speaker_lab.py` implementation of the trial design vs the bake-off's `cmd_eval`, ResNet34 FP32):
  owner strict 1.31 / 1.61 / 6.42 % (record 1.3 / 1.6 / 6.4), CI [1.09, 1.56] (record [1.1, 1.5]); lenient 3.12 / 5.80
  (record 3.1); 2 s or less 1.44 / 1.94 (1.4 / 1.9); clean impostors 1.35 (1.3); public far-field 3.28 / 6.41 / 19.70
  (3.3 / 6.4 / 19.7); public 2 s or less 4.07 (4.1); clean probes 1.94 (1.9) [measured]. The other three core models also
  reproduce (ECAPA 3.8 %, CAM++ 2.6 %, TitaNet 3.0 % owner strict EER).
- **New cohort**: LibriSpeech **dev-clean** (40 speakers, speaker-disjoint from the test-clean trial set), 6 crops each in the
  three length buckets, clean and synthetic far-field (480 files, public, built by `prep-cohort`). It is used for
  calibration of the INT8 model, for the centring mean and for S/AS-norm. **No household member is in any cohort.**
- **Pinned versions**: onnxruntime **1.23.2** (Jetson scratch venv, same as the zoe-data venv), **1.24.4** (Pi daemon venv),
  onnx 1.19.1 (quantisation tooling only), numpy 2.2.6 (Jetson scratch venv) / 2.4.4 (Pi), scipy 1.15.3, scikit-learn 1.7.2.
  Model files: FP32 sha256 `5ef208a9da14...57fe94` (matches the sherpa-onnx release `checksum.txt`), INT8 QDQ
  `d1f65aa0e09b...1eae32` (built by `speaker_bakeoff.py quantize`, calibration clips shuffled with `random.Random(3)`).
- **Statistics** - which interval means what:
  - Accuracy deltas in sections 1, 4.1, 4.2 (quantisation, caps, trim, enrolment recipes and clip counts, contamination arm, centring, AS-norm,
    cohort subsets, final pipeline) are **paired bootstraps over probe clips**: both arms are scored on the same 30 enrolment draws and
    the same resampled probe columns (owner path: shared `boot_indices`; public path: shared `public_boot_indices`, explicit since the
    revision of 2026-10-13). They do **not** cover the variance of the enrolment draws, the inferred owner labels or the choice of
    impostors; the strict-vs-lenient swing is far larger than any interval.
  - Length-threshold deltas (row 8) are paired bootstraps over genuine clips and impostor clips with the speaker-disjoint thresholds held as
    cross-validated (both schemes scored on the same resampled clips).
  - Adaptation deltas (row 9, section 5) are paired two-level bootstraps: enrolment draws, then the held-out genuine and impostor clips inside
    each draw, with the same indices for the anchor-only and the adapted profile. The lookalike and admitted-update counts are one value per
    draw, so their intervals are over enrolment draws only. The 30 draws share one clip stream, so they are not independent - read these
    intervals as optimistic.
  - Speed intervals are the range over 3 - 4 passes, not CIs.
  The FAR 0.1 % rows rest on a few hundred distinct impostor clips - read them as an order of magnitude. The shuffled-label control stays at
  50.0 - 50.3 % EER.
- **Run discipline**: every Jetson model step ran under an **exclusive** hold of `/tmp/zoe-voice-harness.lock`, `nice -n 10`, CUDA
  hidden, with the 2 GB `MemAvailable` gate before each model load (the box sat at 1.1 - 2.7 GB through the session, mostly below
  2 GB, so jobs queued for hours behind other agents' voice probes; the 1.5 GB mid-run floor never tripped). The numpy-only analysis
  steps (the `lab` experiments) ran niced without the lock. No run between 02:30 and 04:30. The Pi was only used when its log showed
  no wake word in the previous 5 minutes (the guard measured 22 minutes to 3.9 hours at the start of each run), at `nice 10`, and
  `zoe-voice` stayed `active` throughout. One un-thermally-gated first sweep was discarded (section 3.1).

### 2.2 The levers' trial designs

6x: owner nested draws take sessions with 8 or more eligible clips (2 s or more) and 8 random clips each; n = 3, 5, 8 are
prefixes of the same draw. The "n=3" arm of that design (1.19 %) differs from the bake-off's 3-clip draws (1.31 %, any session with
3 or more clips) only by the session pool; both are reported. Contamination arm: for each of the 30 draws one of 5 clips is replaced
by a random public impostor clip (identical contamination for every recipe). 8: thresholds are derived on one half of the impostor
*speakers* (LibriSpeech speakers and Kokoro voices) and applied to the other half, 20 random splits; FAR is therefore out of sample.
9: owner clips are replayed in capture-time order after the enrolment session (first 70 % adapt, last 30 % is held out), guards
as in PR #2002.

## 3. Speed (panel Pi 5)

### 3.1 The Pi runs hot - measure accordingly

The Pi in the kiosk sat at **65 C when the bake-off ran (07:00) and 73 - 78 C idle through this session** (chromium kiosk at
about half a core). The firmware's soft temperature limit (about 82 C) engaged during FP32 bursts: `vcgencmd get_throttled`
showed the soft-limit and frequency-cap bits and the clock fell from 2.4 to 1.5 GHz. A first 40-clip FP32 sweep was discarded
because the timings were taken throttled (and it pushed the panel to 88 C - it was killed). The harness now gates: it waits
for the SoC to cool to `--max-temp-c`, samples the throttle bits during the run, flags a pass `tainted` if any were set, re-runs
it after another cool-down and never averages a tainted pass. **FP32 at 60 clips could not be re-measured un-throttled** - it always crossed the limit, even from 66 C - so the same-session FP32
comparison uses shorter runs: 16 clips (sweeps A - D) and 30 clips (3 of 4 FP32 passes stayed un-throttled, start 70 - 72 C, end 81.5 C, just under
the limit; the fourth was throttled and dropped). The 60-clip FP32 reference is the record's 369 / 1002 ms (cool Pi) and is a cross-session
comparison only. The INT8 numbers are un-throttled and were measured on a **warmer** Pi than that baseline, so the cross-session speed-up is conservative. Every row in a sweep shares a process; its RSS columns are cumulative and are not quoted
(fresh-process RSS is in section 3.4).

### 3.2 Where the time goes

FP32, 2 threads, 16 clips: fbank 3 - 6 ms, inference 196 - 205 ms; about 91 ms per audio second plus about 38 ms fixed.
INT8: fbank 3 - 7 ms, inference 66 - 85 ms; **about 30 ms per audio second plus about 17 ms fixed** (3.65 s clips).

### 3.3 The INT8 trap (document it, then fence it)

The QDQ model is only fast after ORT fuses it into quantised convolutions, which needs graph level EXTENDED or higher: at
`ORT_ENABLE_BASIC` or `ORT_DISABLE_ALL` the same file takes **214 - 221 ms instead of 83 - 87 ms** (2.6x slower than itself, slower
than FP32), and its embeddings change (cosine to the FP32 reference 0.917 min / 0.966 mean unfused, 0.931 / 0.966 fused). So the
rule that protects CAM++ (`ORT_DISABLE_ALL`) is the *opposite* rule for this file; the daemon must pass the level per model, and the
start-up self-test must check **latency as well as cosine**. Quantised embeddings are not FP32 embeddings: cosine to FP32 over 60
public clips is **0.915 min / 0.966 mean**, so an INT8 profile and an FP32 query do not mix (section 8).
Both ORT versions agree: the INT8 and FP32 embeddings of 60 public clips from the Jetson (ORT 1.23.2) and from the Pi (ORT 1.24.4)
have **cosine 1.0 (min 0.9999999999999998)** - a profile enrolled on either box matches on the other [measured].
FP32 ResNet34 itself shows no drift between any ORT option (none / basic / extended / layout, 1 - 4 threads, arena and
mem-pattern and spinning off, parallel mode): cosine 1.0 on 40 clips, so the `>= 0.9999` check holds for all of them.

### 3.4 Final numbers (public clips; Pi 5; un-throttled passes only; the 60-clip rows are the bake-off's set, mean 3.65 s)

| config | p50 ms (3 passes) | p95 ms | ms per audio-s + fixed | fbank | notes |
|---|---|---|---|---|---|
| FP32, 2 thr, ORT_ENABLE_ALL [record] | **369** | 1002 | 134 | | cool Pi, record |
| FP32, 2 thr (this session, 8 clips, fresh process) | 231 | 588 | 87.5 + 48 | 6 | mean 2.84 s |
| **same-session pair, 30 clips (mean 2.86 s): FP32, 2 thr** | **232 [228-238]** | **665 [664-672]** | 89 + 34 | 3 - 6 | 3 clean passes of 4; the headline FP32 reference |
| **same-session pair, 30 clips: INT8, 2 thr, spin off** | **89 [87-90]** | **225 [222-228]** | 29.7 + 17 | 3 | 4 clean passes; **2.6x p50, 3.0x p95** vs the row above |
| **INT8 QDQ, 2 thr, spin off (recommended)** | **106 [105-108]** | **242 [241-245]** | **29.6 + 17** | 4.1 | un-throttled, Pi 73 -> 81.5 C |
| INT8, 1 thr, spin off | 136 [135-142] | 371 [368-385] | 49.3 + 0 | 3.6 | frees a core, +29 % latency |
| INT8, 4 thr, spin off | about 90 - 97 (**throttled**, indicative) | about 191 | 22.4 + 25 | 4 | every 60-clip pass crossed the limit; 16 and 30 clip passes were clean: 64 (2.43 s) and 78 - 86 (2.86 s) ms, p95 150 - 183 |

Fresh-process RSS (one session, one process): INT8 **154 MB after load, 199 MB peak at 16 clips / 218 MB at 60**; FP32 167 / 224 MB.
Weights drop 26.5 -> 6.8 MB but peak RSS is dominated by activations and the ORT arena, so RAM is unchanged (the daemon already imports
onnxruntime for the wake-word model - **[unverified]** whether that session lets the embedder reuse its allocator; one in-daemon
measurement settles it). Threads: 2 is the right setting (4 buys about 15 % on a Pi whose kiosk already uses a core; 1 costs 29 %).

## 4. Accuracy detail

### 4.1 Quantisation

Owner strict core, 30 draws x 756 impostor probes, FP32 vs INT8 QDQ (U8/S8, per-channel, MinMax), paired:

| axis | FP32 | INT8 | paired delta |
|---|---|---|---|
| owner strict EER / FRR@1 % / FRR@0.1 % | 1.31 / 1.61 / 6.42 | 1.22 / 1.44 / 6.23 | -0.07 pp [-0.16, +0.01] / -0.16 [-0.32, +0.02] / -0.47 [-1.41, +0.40] |
| owner lenient core | 3.12 / 5.80 / 14.73 | 2.74 / 5.13 / 12.16 | -0.38* / -0.72* / -2.01* |
| 2 s or less, owner strict EER | 1.44 | 1.32 | |
| public far-field | 3.28 / 6.41 / 19.70 | 3.30 / 7.14 / 22.01 | +0.05 [-0.24, +0.38] / +0.75 [-0.27, +1.68] / +2.34* [+0.58, +4.57] |

Acceptance rule (the owner's): "within the CI of FP32 on both axes". INT8 point estimates sit inside FP32's own 95 % interval on 8 of 9
metrics; the exception is lenient-core EER, where INT8 is *better* (2.74 % vs an interval of 2.74 - 3.54 %). The only measurable loss
is the public FRR@FAR0.1 % tail (+2.3 pp), which rests on 476 distinct probe clips; on the owner axis the same tail goes the other way.
**Accepted, with the tail flagged as the thing to watch in the shadow week.**

### 4.2 Enrolment and centring, combined (the recommended pipeline vs the baseline)

Paired against FP32 raw 3-clip mean: owner strict EER 1.31 -> **0.72 %** (-0.58 pp* [-0.71, -0.45]), FRR@1 % 1.61 -> **0.48 %** (-1.09* [-1.40, -0.81]),
FRR@0.1 % 6.42 -> 4.03 % (-2.39* [-3.50, -1.10]); lenient core 3.12 -> 2.13 % (-1.02*); public 3.28 -> 2.57 % (-0.72* [-1.14, -0.31]),
FRR@1 % 6.41 -> 5.09 % (-1.31*), FRR@0.1 % 19.70 -> 16.70 % (-2.87*). Nested design with rejection: n=3 0.69 %, **n=5 0.53 %** (-0.14 pp* vs n=3),
**n=8 0.47 %** (-0.22 pp*); FRR@1 % 0.45 / 0.29 / 0.17 %. Score distributions (owner strict, INT8 + centring, raw centred cosine): genuine p5 / p50 / p95 =
0.354 / 0.540 / 0.693, impostor p50 / p95 / p99 = 0.017 / 0.178 / 0.252. Short clips (2 s or less) stay the weak point on the public axis (EER
3.9 - 4.1 % vs 0.5 % over 4 s).

### 4.3 Thresholds (provisional)

Cosine of the centred embedding to the centred centroid, impostors = the public far-field set (in-sample on all 30 draws; the CV rows
in section 1 are the honest FAR): **FAR 1 %: 0.233 (2 s or less) / 0.265 (longer), global 0.253. FAR 0.1 %: 0.309 / 0.351, global 0.342.**
Without centring the same targets are 0.260 / 0.309 and 0.321 / 0.374 (global 0.296 / 0.367). The old `ZOE_SPEAKER_ID_THRESHOLD`
default 0.82 [bake-off record 3.3] rejects every genuine probe at ResNet34's scale (FRR 100 % at 0.00 % FAR). Impostors here are public
speakers, not the household: **these are starting values for the shadow week, to be re-derived on labelled household turns.**

## 5. Interaction with #2002 (guarded speaker-profile adaptation, merged flag-dark)

PR #2002 (merged 2026-10-13, `ZOE_SPEAKER_ADAPT=off` by default; `services/zoe-data/speaker_adaptation.py`) updates the profile as `normalize(0.95 * current + 0.05 * clip)` from turns that pass every
guard: score to the served profile >= 0.85, cosine to the anchor >= 0.80, >= 2 s of speech, >= 600 s apart and <= 5 per UTC day, and a
drift bound (the update is refused if the profile would fall under cosine 0.92 of the anchor). `speaker_lab.adapt_replay` replays exactly
that policy. Results on ResNet34 (INT8 + centring, 30 enrolment draws, owner strict clips replayed in capture order, first 70 % offered,
last 30 % held out; FAR thresholds fixed from the anchor-only trials: 0.253 at FAR 1 %, 0.342 at FAR 0.1 %):

| guard setting | updates applied per replay (mean [95 % CI]) | held-out genuine mean score | FRR@FAR1 % | FRR@FAR0.1 % | impostor FAR at the fixed 1 % / 0.1 % thresholds | worst-case lookalike offered 5 per day for 73 days: admitted updates, score before -> after |
|---|---|---|---|---|---|---|
| #2002 defaults (0.85 / 0.80) | **0** | 0.519 (no change) | 0.77 % (no change) | 5.94 % (no change) | 1.00 % / 0.097 % (no change) | 0; unchanged |
| score and anchor gate at the FAR-1 % threshold (0.253) | 38.4 [34.4, 42.0] | 0.519 -> 0.613 (+0.09 [+0.08, +0.10]*) | 0.77 -> 0.02 % (-0.73 pp [-1.34, -0.32]*) | 5.94 -> 0.61 % (-5.17 pp [-7.64, -2.92]*) | 0.93 % (-0.07 pp [-0.29, +0.11]) / 0.009 % (-0.09 pp [-0.19, -0.02]*) | **8.1 [7.0, 8.8]; 0.321 -> 0.629** (+0.31 [+0.28, +0.33]*) |
| gate at the FAR-0.1 % threshold (0.342) | 38.5 [34.4, 42.1] | 0.519 -> 0.613 (+0.09 [+0.08, +0.10]*) | 0.77 -> 0.02 % (-0.73 pp [-1.34, -0.32]*) | 5.94 -> 0.62 % (-5.16 pp [-7.61, -2.92]*) | 0.89 % (-0.11 pp [-0.30, +0.08]) / 0.004 % (-0.09 pp [-0.19, -0.03]*) | 3.6 [2.4, 5.1]; 0.321 -> 0.452 (+0.13 [+0.07, +0.19]*; crosses the 0.342 threshold) |
| **score gate 0.342 + anchor-cosine gate 0.45** | 38.5 [34.1, 42.1] | 0.519 -> 0.611 (+0.09 [+0.08, +0.10]*) | 0.77 -> 0.02 % (-0.73 pp [-1.34, -0.32]*) | 5.94 -> 0.67 % (-5.12 pp [-7.60, -2.91]*) | 0.86 % (-0.14 pp [-0.36, +0.05]) / 0.004 % (-0.09 pp [-0.19, -0.03]*) | **0; unchanged** |

Deltas are adapted minus anchor-only, paired within each draw (two-level bootstrap, section 2.1). Between gates, paired by draw: the extra anchor
gate vs the FAR-0.1 % gate alone lowers the lookalike's final score by 0.13 [0.07, 0.19] and the admitted updates by 3.6 [1.8, 5.1], with
no change in held-out FRR@0.1 % (+0.000 [+0.000, +0.001]); the FAR-0.1 % gate vs the FAR-1 % gate lowers them by 0.18 [0.12, 0.24] and 4.5 [3.0, 6.1].

Reading: (1) the PR's constants are Resemblyzer-scale and never fire on ResNet34 - they are single global env settings today
(`ZOE_SPEAKER_ADAPT_MIN_SCORE`, `ZOE_SPEAKER_ADAPT_ANCHOR_FLOOR`) and must become per-model settings keyed by `model_id`.
(2) Adaptation helps ResNet34 a lot (held-out FRR@1 % 0.77 -> 0.02 %, FRR@0.1 % 5.9 -> 0.7 %) and does not measurably loosen the impostor
side (FAR at the fixed 1 % threshold moves by -0.14 pp [-0.36, +0.05], i.e. no detectable change; at the 0.1 % threshold it falls). (3) A score gate
set at a FAR threshold is not enough: the single most owner-like public impostor (score 0.32 - 0.36 to the anchor) is admitted and drags itself to 0.63.
The extra rule that stops it costs nothing: require the clip's cosine to the **anchor** to clear a bar above the worst impostor seen (0.45 here, about the
22nd genuine percentile, so roughly 4 owner turns in 5 can still adapt) - the drift bound (final cosine 0.923 against a 0.92 limit) limits the damage but
did not prevent admission. This is the same residual risk #2002 names for a true lookalike; it is narrowed, not removed, and needs the second enrolled
member's margin guard to be meaningful. The lookalike is one impostor per enrolment draw, so the admitted-update and lookalike intervals are over draws.

## 6. Where it runs, and why not the Jetson

- **Pi (recommended).** 106 ms p50 / 242 ms p95 at 3.65 s with no network and no server load; the daemon already computes the shadow
  embedding on the Pi today (Resemblyzer, 243 ms in the record).
- **Jetson CPU (measured, contended: load average 7 - 10, shared with the brain and other agents):** INT8 **101 ms** (2 threads) / 86 ms (4
  threads), FP32 260 ms. So moving to the Jetson does not reach "much faster than the Pi" - INT8 on the Pi is already there.
- **Panel -> Jetson round trip (measured with ping and read-only requests to the existing server; no new listener was opened):**
  ICMP RTT 4.2 ms avg (1.0 - 8.2; 1,400-byte packets 5.9 ms), HTTPS GET to an existing endpoint 5 - 15 ms on a kept-alive connection and
  15 - 32 ms with a new TLS connection. A 3.65 s clip is 117 kB at 16-bit 16 kHz. **[derived]** estimate for an off-box embedding:
  101 ms + 5 - 15 ms request + the upload (about 10 - 30 ms on this LAN) = 120 - 150 ms - slower than the Pi's 106 ms, plus the record's
  233 - 265 MB peak for an ONNX embedder in zoe-data on a box whose RAM gates everything, and a server path for a biometric.
  **Not built, not recommended.**
- **ORT CUDA EP:** not available - the zoe-data venv's onnxruntime 1.23.2 lists only `AzureExecutionProvider` and `CPUExecutionProvider`;
  `onnxruntime-gpu` is not installed. The GPU belongs to the brain (CUDA is hidden by the harness; NvMap sits outside every cgroup
  guard), so it was **not** measured and should not be: a CUDA context costs hundreds of MB the voice brain cannot spare.

## 7. Limits (read before relying on any row)

- **Owner-corpus rows are relative evidence.** Labels are inferred (bake-off 2.2). New here: silence trimming (edge or all) costs
  **+2.3 to +2.5 pp EER on the owner corpus but +0.0 pp on the public axis** (where genuine and impostor clips share the synthetic
  channel). A model that ignored room tone would not care. **[hypothesis, unverified]** the owner-vs-synthetic-impostor
  separation includes a channel component (the real microphone's noise floor in the owner clips against the synthetic noise in
  the impostors); the labelled public axis, not the owner axis, is the ranking evidence, and a real-channel non-owner test is
  still missing (the five quarantined TV clips are too few). The household shadow week fixes this properly.
- **The centring mean is channel-matched.** It helps because the cohort shares the (synthetic far-field) channel; a clean-speech
  cohort does nothing (+0.05 pp). The real panel channel is not synthetic - recompute the mean on real panel-channel audio of
  non-enrolled speakers when any exists, and keep it a versioned artefact.
- **Thermals.** The Pi idles at 73 - 78 C in the kiosk and the soft limit is about 82 C. Per-turn bursts of about 0.1 s are fine;
  sustained benchmarking is not, and a re-run must use the gating in section 3.1. FP32 60-clip timings were not obtainable un-throttled.
- INT8 calibration used 100 public clips; per-tensor / percentile / last-layer-FP32 variants were screened on the public axis only.
- Public-axis FAR 0.1 % rows are coarse (a few hundred distinct impostor clips). English models and English impostors only.
- #2002's numbers are a replay on the same owner clips used for everything else, with the first 70 % as the adaptation stream and the last
  30 % held out; it does not model a real change in the voice (a cold, a new room).

## 8. Migration note (not built here)

1. **`model_id` on every profile row, and match on it.** ResNet34 is 256-d like Resemblyzer, so the daemon's shape check
   (`ref.shape != embedding.shape` skip, `zoe_voice_daemon.py` line 2119 per the bake-off record) would score against stale Resemblyzer
   profiles with no error. The id must name everything that changes the embedding space: model file digest, quantisation, the
   centring-vector version, e.g. `wespeaker_resnet34+i8qdq-u8s8+c1`. INT8 and FP32 profiles do **not** mix (cosine 0.97 between the two kinds
   of embedding of the same clip).
2. **Dual-run (flag-dark, one week).** Keep Resemblyzer as the live shadow scorer; add ResNet34 INT8 as a second scorer in the same
   `speaker_shadow_metrics.jsonl` rows, `model_id` on each. A member has one profile row per `model_id`; ResNet34 rows stay unused until the
   comparison is done. The Pi has the headroom: 106 ms in a background thread after the turn, about 200 MB.
3. **Re-enrolment through the guided panel flow** (`zoe_enroll_flow.py`): **5 clips** per member in one sitting (8 if the household is
   patient: EER 0.53 -> 0.47 %), same room, then outlier rejection at tau 0.30 and a re-ask for any clip rejected. The retention policy keeps no
   audio, so profiles cannot be re-embedded - this is the only path. Until a member re-enrols the new scorer has no profile for them and
   the claim stays `None` (existing behaviour for an unknown speaker). Enrolment embedding can run on either box (bit-identical embeddings
   across ORT 1.23.2 / 1.24.4).
4. **Fence the runtime, as for CAM++.** Per-model graph level in code (INT8 file: EXTENDED or above; never DISABLE_ALL); `allow_spinning=0`; load once
   at daemon start; a start-up self-test that embeds a bundled public clip and asserts (a) cosine to a stored reference vector >= 0.9999, (b) latency
   within 3x of the expected, so an ORT upgrade or a wrong graph level fails loudly. The reference vector is public/synthetic data, not a voiceprint.
5. **Thresholds.** Keyed by `model_id` and length group (2 s or less / longer): start at 0.233 / 0.265 (FAR 1 %) and 0.309 / 0.351 (FAR 0.1 %) on centred
   cosines (section 4.3), then re-derive on the labelled shadow week. `ZOE_SPEAKER_ID_THRESHOLD`'s 0.82 does not carry over. Decide CAM++ vs ResNet34 there
   too: ResNet34 INT8 at 106 ms now costs about what CAM++ cost in the record (133 ms) - the speed reason to prefer CAM++ is gone.
6. **#2002.** Make `ZOE_SPEAKER_ADAPT_MIN_SCORE` and `ZOE_SPEAKER_ADAPT_ANCHOR_FLOOR` per-model settings; for ResNet34 set the anchor floor to 0.45 and the min score to the FAR-0.1 % threshold; keep
   the 0.92 drift bound. Turn adaptation on only after the shadow week and a second enrolled member (margin guard).
7. **Centring vector**: a public artefact built from the cohort (mean of the L2-normalised far-field dev-clean embeddings, 240 clips / 40 speakers) with the *deployed*
   INT8 model and ORT, versioned (`c1`), applied as `normalize(e - mu)` to every embedding before enrolment and scoring. Recipe in section 9; the vector is not committed.
8. **Voice-path rules apply** when the swap ships: replay-gate against `~/.zoe-voice-samples` (said-vs-did must not regress, per-stage speed must not
   regress) under the harness lock, per AGENTS.md; the swap touches `voice_speaker_id.py`, the daemon's encoder / matcher / warm-up, the profile table and the
   threshold semantics.

## 9. Reproduce

```bash
# scratch venv as in the bake-off record plus: uv pip install onnx==1.19.1 ; data: LibriSpeech test-clean + dev-clean
# every Jetson model step: exclusive harness lock, nice 10, MemAvailable >= 2 GB first
python scripts/perf/speaker_bakeoff.py index-corpus --before "2026-10-12 19:00" --out <scratch>
python scripts/perf/speaker_bakeoff.py resfail --corpus-index <scratch>/corpus_index.json
python scripts/perf/speaker_bakeoff.py prep-cohort --libri <dev-clean> --out <scratch>/cohort
python scripts/perf/speaker_bakeoff.py embed --model wespeaker_resnet34 --corpus-index <scratch>/corpus_index.json   # + the other three core models (--no-cohort)
python scripts/perf/speaker_bakeoff.py score --center raw --core-models ecapa,wespeaker_resnet34,wespeaker_campp,titanet_small \
       --save-core <scratch>/core.npz --corpus-index <scratch>/corpus_index.json
python scripts/perf/speaker_bakeoff.py quantize --src wespeaker_en_voxceleb_resnet34.onnx --dst rn34_qdq_u8s8_mm.onnx \
       --mode static --format qdq --act-type uint8 --w-type int8 --calib minmax
python scripts/perf/speaker_bakeoff.py embed --model 'wespeaker_resnet34@file=rn34_qdq_u8s8_mm.onnx' --tag i8_full --corpus-index <scratch>/corpus_index.json
python scripts/perf/speaker_bakeoff.py lab --base wespeaker_resnet34 --corpus-index <scratch>/corpus_index.json --core <scratch>/core.npz --experiment baseline
python scripts/perf/speaker_bakeoff.py lab --base rn34_qdq_u8s8_mm ... --experiment variant --args tag=i8_cap4      # any embedded variant, paired vs the base
python scripts/perf/speaker_bakeoff.py lab --base wespeaker_resnet34 ... --experiment final --args tag=i8_full,center=1
python scripts/perf/speaker_bakeoff.py lab --base i8_full ... --experiment {enrol,norm,lenthr,adapt,centersens} --args tag=i8_full,center=1
# Pi (public clips only, nice 10, thermal-gated; never stop zoe-voice; no wake word in the last 5 minutes):
python scripts/perf/speaker_bakeoff.py bench --model 'wespeaker_resnet34@file=rn34_qdq_u8s8_mm.onnx,spin=0;wespeaker_resnet34' --wavs <60 public wavs> \
       --n 60 --reps 3 --max-temp-c 74 --cool-wait-s 240 --retries 4
python scripts/perf/speaker_bakeoff.py drift --ref 'wespeaker_resnet34@opt=none' --variants '<specs, ;-separated>' --wavs <public wavs>
```

Everything lives in a scratch directory outside the repo; what is committed is `speaker_bakeoff.py`, `speaker_lab.py` and `tests/unit/test_speaker_lab.py`.
The unit tests pin the pure logic on planted embeddings (variants, pre-processing, enrolment recipes, S/AS-norm, paired bootstrap, owner-draw reproduction of the
bake-off's generator, length thresholds, the adaptation guards and drift bound) plus an end-to-end run of every experiment on a synthetic scratch directory; each of
four planted bugs (outlier rejection off, drift bound off, length threshold = global, one-sided S-norm) turns at least one test red.

## 10. Next steps (proposed, not started)

1. Build the swap behind a flag-dark dual-run (section 8) and re-run the voice replay gate; one in-daemon RSS measurement.
2. Shadow week on labelled household turns: re-derive both length-group thresholds, measure the real-channel FAR (the missing piece in section 7), decide the
   AS-norm question again with household data, and settle CAM++ vs ResNet34 INT8.
3. Retune #2002's guard constants per model before it is ever switched on (section 5).
4. Re-measure the Pi FP32 60-clip timing in a cool hour so the speed-up has an un-throttled same-session denominator.
