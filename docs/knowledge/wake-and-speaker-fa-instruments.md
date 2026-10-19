---
type: Runbook
title: Wake-word FAR/FRR and speaker false-accept instruments (panel voice)
description: How to measure the two live panel voice risks - the "Hey Zoe" threshold (0.45, background near-misses up to 0.44) and the ResNet34 voice password (false accepts open a private session) - with scripts/perf/measure_wake.py and scripts/maintenance/speaker_fa_review.py, what each number means, the owner steps, and the measured results.
tags: [voice-path, wake-word, openwakeword, speaker-id, resnet34, measurement, runbook]
timestamp: 2026-10-19T00:00:00+08:00
---

# Wake-word FAR/FRR and speaker false-accept instruments

Deep dive 2 (lane I, items V-4 and V-1 / master todo 10) found two live voice risks with no measurement behind them:

- **Wake word.** The panel fires at `WAKEWORD_THRESHOLD=0.45` (3 hits within 1.0 s, 5 s apart). The model design point is
  0.5, and logged background near-misses reach 0.4496. 27 of 303 wakes since 07-15 ended in the empty-transcript chime.
- **Voice password.** Since 2026-10-18 23:13 ResNet34 acts (`ZOE_SPEAKER_MODEL=resnet34`, `ZOE_VOICE_SESSION=1`). An
  accepted voice opens a private panel session, so a false accept leaks private answers. The thresholds (0.233 for clips of
  2 s or less, 0.265 for longer ones) were set on public impostors and were always meant to be re-derived on labelled
  household turns.

Both tools are **read-only on every live system**. Household audio and transcripts never enter the repo: outputs live
under `~/.zoe/voice-gate/` (directory 700, files 600, never written through a symlink), and both tools refuse any other output path. The wake replay refuses model files whose sha differs from `models.lock` (there is no override). Neither tool
changes a threshold. Moving one is an owner voice-path step, replay-gated like any other.

## 1. Wake word: `scripts/perf/measure_wake.py`

| sub-command | needs | does |
|---|---|---|
| `nearmiss` | the Pi log (ssh, read-only) | near-miss score bands since `--since`, wake outcomes (turn / empty chime / nothing), wakes 01:00-05:00, near-miss then a wake 1.5-10 s later (retry proxy) |
| `run` | the pinned model files, 22:00-07:00 | replays `hey_zoe.onnx` frame by frame (1280-sample int16 chunks, as the daemon feeds it) over positives and negatives, applies the daemon confirmation state machine at every threshold 0.20-0.80, writes `~/.zoe/voice-gate/wake/{scores.json,report.md,report.json}` |
| `report` | a saved `scores.json` | re-renders the curve and the recommendation without the model |

**Sets.**

- `positive`: the panel wake turns in `~/.zoe-voice-samples`. Each clip is joined to its `Recorded command:
  duration=X` line in `voice.log` by time and exact length (the daemon logs the clip length, pre-roll included). Clips
  from before 2026-07-16 are excluded, because the 0.96 s pre-roll of that time began inside the phrase (#1326).
- `followup`: the panel follow-up turns. This is household speech at the panel mic with no wake phrase needed. It is
  reported, but it is not part of the FA objective (a mid-sentence "Zoe" is legitimate there).
- `tv`: `quarantine-tv-falsewakes-20260719`. These 5 clips did false-wake live, and a recommended threshold must wake on 0 of them.
- `libri`, `kokoro`: the speaker bake-off public impostor crops (`~/.cache/zoe-speaker-bakeoff/impostors`): 1,112 LibriSpeech test-clean crops (1.26 h) and 560 Kokoro TTS voice crops (0.42 h), clean and synthetic far-field. The replay runs at about 3x real time on the Jetson under `nice`, so the full LibriSpeech dev + test set (10.8 h, `--negatives libri=~/.cache/zoe-speaker-bakeoff/src/LibriSpeech`) is a 4-hour run that would reach the 02:00 night window; the default keeps a run near one hour.
- `omi`: the Omi pendant recordings (`~/.zoe/omi-recordings`), which are household conversation on another mic.
  `libri` + `kokoro` + `omi` are pooled into the FA-per-hour objective.

**What the numbers mean.**

- Positive FRR is a **cost** measure: the share of real wakes that a given threshold would lose. Every positive DID
  wake live, so the replay cannot see attempts that never woke. The near-miss retry proxy is the only window on those.
- The recommendation compares FRR against the live threshold **in the same replay**, so the replay's own losses cancel.
  The rule: the highest threshold whose FRR is within `--frr-budget` (default 5 percentage points) of the live threshold
  and that wakes on 0 of the TV clips, with 30 or more positives required. If no TV clip was measured, it gives no recommendation (an empty set is not 0/N). The report also gives the answer at budgets
  of 1, 2 and 5 points.
- **The near-miss logging understates the band.** The daemon logs the FIRST frame of at least 0.18 after a 12 s
  cooldown, not the peak of the episode. A frame that crosses the threshold but fails the 3-hit confirmation is not
  logged at all. So "max 0.44" is a lower bound on how close background audio gets.

**Owner steps.**

1. One-time setup, outside the repo. The feature models must be the panel bytes, which `models.lock` pins:
   `python3 -m pip install --no-deps --target ~/.cache/zoe-wake-fa/pylib openwakeword==0.6.0`, then copy
   `melspectrogram.onnx` and `embedding_model.onnx` from the panel venv
   (`.zoe-voice/venv/lib/python3.11/site-packages/openwakeword/resources/models/`) to `~/.cache/zoe-wake-fa/features/`.
   The model comes from the backup `~/.zoe/backups/panel/hey_zoe.onnx`. A sha mismatch is refused.
2. Run it after 22:00: `flock -x -w "$(python3 scripts/maintenance/harness_quiet.py brain-window-wait --budget-min 45)" /tmp/zoe-voice-harness.lock nice -n 10 python3 scripts/perf/measure_wake.py run`.
   It refuses during the harness quiet hours and below 1500 MB `MemAvailable`. It also holds the 01:50-04:55 brain window
   (`harness_quiet.brain_window_verdict`, `--force` does not skip it): it refuses a start inside it or one whose estimated
   length (clip count, or `--budget-min`) would still be alive at 01:50, and stops at a 25-clip checkpoint if the window is
   about to open. A run that stops early (memory under the floor for 10 min, or the window) marks `notes.aborted` in
   `scores.json` / `report.json`, prints an INCOMPLETE banner, gives no threshold recommendation and exits 4; `report`
   keeps it incomplete when re-rendering. `speaker_fa_review.py offline-fa` takes the same window guard.
3. To move the threshold, edit `WAKEWORD_THRESHOLD` in the Pi `.env.voice`, restart `zoe-voice`, and watch a week of
   `nearmiss` (empty-chime share, near-miss band, retry proxy). That is a voice-path change and it belongs to the owner.

## 2. Voice password: `scripts/maintenance/speaker_fa_review.py`

| sub-command | does |
|---|---|
| `collect` | panel turns since `--since` (default 2026-10-13, the start of the dual-mode ResNet34 lines) -> `~/.zoe/voice-gate/speaker-fa/sheet.csv`. It takes the score, length, outcome and transcript snippet from the Pi `voice.log` (read-only ssh), and the server verdict from `~/.zoe-logs/zoe-data.app.log*` (NOT journald). It links the corpus clip of the turn when `ZOE_VOICE_SAVE_AUDIO` kept one. Re-running merges, and existing labels are kept. |
| `label` | walks the unlabelled rows: `m` = me, `n` = not me, `s` = skip, `q` = quit. `--play` plays each clip first. Editing the CSV `label` column by hand works too. |
| `score --owner <member id>` | the owner id is ground truth from the operator, never inferred from the claims being graded. False accept = a "not me" row the gate accepted. False reject = a "me" row not accepted as the owner, and an unscored turn counts as a reject. Both come with 95 % Wilson intervals at the live thresholds, plus a what-if sweep of one global threshold. Fewer than 30 labelled rows are anecdotes, not rates. |
| `offline-fa` | the public impostors (bake-off LibriSpeech and Kokoro, clean and far-field) against the LIVE enrolled ResNet34 profile, using the panel embedder (`scripts/setup/zoe_speaker_resnet34.py`, sha-pinned, self-tested). The profile is read from the Pi and held in memory only. It is guarded like a voice harness. |

Rows by era: `resnet34-dual` rows come from the dual week (ResNet34 was logged but did not act; the server verdict was
Resemblyzer). `resnet34` rows come after the flip (the `Speaker claim` line is the ResNet34 claim). `score --acting-only`
keeps only the second kind. `collect` also lists the rows where the panel-side accept and the server identity disagree.
They share thresholds, so a disagreement means a consent, model-id or join problem.

**How the owner uses it:** run `collect` every few days, label new rows with `label --play` (`--play-cmd` must be an audio player: aplay, paplay, pw-play, ffplay or play), then run `score --owner <member id>`. Once 30
or more labelled rows exist, compare FA and FR at the live pair with the sweep. A threshold change is
`ZOE_SPEAKER_RESNET34_THRESHOLD_SHORT/_LONG` on both hosts, which is an owner step.

## 3. Results

Measured 2026-10-19 (read-only).

**Live near-misses** (`nearmiss`, Pi `voice.log`):

| window | near-misses | per day | max | at or above 0.40 | at or above 0.44 | wakes | empty chime | night wakes | retry proxy |
|---|---|---|---|---|---|---|---|---|---|
| 07-15 .. 10-19 | 1,372 | 14.2 | 0.4496 | 130 | 21 | 303 | 27 (8.9 %) | 13 | 12 |
| 10-16 .. 10-19 | 28 | 8.4 | 0.3754 | 0 | 0 | 2 | 0 | 0 | 0 |

The panel has had **no wake since 2026-10-16 07:20**. So no turn has run since the ResNet34 flip, and the speaker sheet
holds only the 8 dual-week rows (7 accepted at the live thresholds, all claimed as the one enrolled member and still unlabelled, and 1 with no ResNet34 profile yet).

**Wake replay** (`run`, 2026-10-19 22:15-22:50, the pinned model + panel feature files, onnxruntime 1.23.2 on the
Jetson vs 1.24.4 on the Pi). Sets: 267 wake turns (07-16 .. 10-16), 858 follow-ups, 1,112 LibriSpeech crops (1.26 h),
560 Kokoro crops (0.42 h), 31 Omi recordings (0.65 h), 5 TV false wakes.

| threshold | positive FRR (k/267) | TV woke | LibriSpeech FA/h | Kokoro FA/h | Omi FA/h | follow-ups woke | pooled FA/h |
|---|---|---|---|---|---|---|---|
| 0.30 | 9.7 % (26) | 3/5 | 2.39 | 0 | 1.54 | 25/858 | 1.72 |
| 0.40 | 12.7 % (34) | 2/5 | 2.39 | 0 | 0 | 23/858 | 1.29 |
| **0.45 (live)** | **13.1 % (35)** | **2/5** | **1.59** | 0 | 0 | 23/858 | **0.86** |
| 0.55 | 13.9 % (37) | 2/5 | 0.80 | 0 | 0 | 23/858 | 0.43 |
| 0.65 | 15.0 % (40) | 2/5 | 0 | 0 | 0 | 21/858 | 0 |
| 0.70 | 17.6 % (47) | 2/5 | 0 | 0 | 0 | 21/858 | 0 |

Clip peak scores: positives p10 0.757 / p50 0.993; TV clips 0.998, 0.964, 0.853, 0.001, 0.001; LibriSpeech p99 0.047,
max 0.959 (4 crops above 0.6); Kokoro max 0.038; Omi max 0.446; follow-ups p99 0.992 (17 of the 23 that wake say "Zoe").

What it says:

- **The replay itself loses 13.1 % of real wakes at the live threshold** (35 of 267). This is the instrument floor:
  the first frame at or above 0.45 lands 1.28-1.36 s into the 1.6 s pre-roll, so the feature window is cut short on some
  clips. 14 of the 35 are empty-chime wakes, which are likely false wakes whose trigger fell before the pre-roll. The
  recommendation rule compares against this floor, not against zero.
- **No threshold fixes the TV.** Two of the five quarantined TV clips confirm at every threshold up to 0.80, and three
  peak at 0.85-0.998: to this model they ARE "Hey Zoe". So the 0/5 gate is out of reach for a threshold. The fix is at
  model level: openWakeWord `custom_verifier_models`, a speaker-verified wake (ResNet34 is already resident on the Pi),
  or a retrained model with TV negatives. The tool therefore makes no recommendation under its full rule.
- **Threshold-only, public and household speech:** 0.55 costs +0.8 pp of replayed wakes and halves pooled FA
  (0.86 -> 0.43/h). 0.65-0.67 costs +1.9 pp and clears all of LibriSpeech, Kokoro and Omi (0 wakes in 2.33 h).
- **The live band:** 130 near-misses at or above 0.40 since 07-15, and none above 0.3754 since 10-16. Raising the
  threshold moves it further from that band. Only about 2 % of real wakes peak between 0.45 and 0.67, so the cost
  of going as far as 0.65 is small.

**Recommendation (owner step, not applied):** raise `WAKEWORD_THRESHOLD` from 0.45 to **0.55** now. It is the +1 pp
option: about 1 real wake in 130 lost, and half the public-speech false wakes. Run `nearmiss` for a week (empty-chime
share, retry proxy). If the retry proxy does not rise, take the +2 pp step to 0.65. Treat the TV false wakes as a
separate model-level item; a threshold cannot reach them. The change is a voice-path edit to the Pi `.env.voice` and
a `zoe-voice` restart, which belong to the owner.

**Offline speaker FA** (`offline-fa`, 2026-10-19 22:52): 1,672 public clips against the live enrolled ResNet34 profile
(1 profile) at the live thresholds 0.233 / 0.265:

| slice | clips | accepted | rate [95 % CI] | max score | accepted at the FAR 0.1 % tier |
|---|---|---|---|---|---|
| all | 1,672 | 18 | 1.1 % [0.7 - 1.7] | 0.337 | 1 |
| LibriSpeech | 1,112 | 10 | 0.9 % [0.5 - 1.6] | 0.312 | 1 |
| Kokoro (synthetic) | 560 | 8 | 1.4 % [0.7 - 2.8] | 0.337 | 0 |
| Kokoro clean | 280 | 8 | 2.9 % [1.5 - 5.5] | 0.337 | 0 |
| Kokoro far-field | 280 | 0 | 0.0 % [0.0 - 1.4] | 0.263 | 0 |
| clips of 2 s or less | 586 | 5 | 0.9 % [0.4 - 2.0] | 0.312 | 1 |

This reproduces the 0.9-1.4 % of the threshold derivation against the real enrolled profile: about 1 stranger turn in
90 opens the voice session. The weak slice is clean synthetic speech (2.9 %). The FAR 0.1 % tier (0.309 / 0.351) would
have let 1 of the 1,672 through. Re-derive the threshold from the labelled household sheet (30 or more rows) before
moving it. The sheet is empty of post-flip turns today because the panel has not woken since the flip.
