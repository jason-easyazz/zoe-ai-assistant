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
under `~/.zoe/voice-gate/` (directory 700, files 600), and both tools refuse a path inside the checkout. Neither tool
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
- `libri`, `kokoro`: the speaker bake-off public impostor crops (`~/.cache/zoe-speaker-bakeoff/impostors`).
- `omi`: the Omi pendant recordings (`~/.zoe/omi-recordings`), which are household conversation on another mic.
  `libri` + `kokoro` + `omi` are pooled into the FA-per-hour objective.

**What the numbers mean.**

- Positive FRR is a **cost** measure: the share of real wakes that a given threshold would lose. Every positive DID
  wake live, so the replay cannot see attempts that never woke. The near-miss retry proxy is the only window on those.
- The recommendation compares FRR against the live threshold **in the same replay**, so the replay's own losses cancel.
  The rule: the highest threshold whose FRR is within `--frr-budget` (default 5 percentage points) of the live threshold
  and that wakes on 0 of the TV clips, with 30 or more positives required. The report also gives the answer at budgets
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
2. Run it after 22:00: `flock -x -w 3600 /tmp/zoe-voice-harness.lock nice -n 10 python3 scripts/perf/measure_wake.py run`.
   It refuses during the harness quiet hours and below 1500 MB `MemAvailable`.
3. To move the threshold, edit `WAKEWORD_THRESHOLD` in the Pi `.env.voice`, restart `zoe-voice`, and watch a week of
   `nearmiss` (empty-chime share, near-miss band, retry proxy). That is a voice-path change and it belongs to the owner.

## 2. Voice password: `scripts/maintenance/speaker_fa_review.py`

| sub-command | does |
|---|---|
| `collect` | panel turns since `--since` (default 2026-10-13, the start of the dual-mode ResNet34 lines) -> `~/.zoe/voice-gate/speaker-fa/sheet.csv`. It takes the score, length, outcome and transcript snippet from the Pi `voice.log` (read-only ssh), and the server verdict from `~/.zoe-logs/zoe-data.app.log*` (NOT journald). It links the corpus clip of the turn when `ZOE_VOICE_SAVE_AUDIO` kept one. Re-running merges, and existing labels are kept. |
| `label` | walks the unlabelled rows: `m` = me, `n` = not me, `s` = skip, `q` = quit. `--play` plays each clip first. Editing the CSV `label` column by hand works too. |
| `score` | false accept = a "not me" row the gate accepted. False reject = a "me" row not accepted as the owner, and an unscored turn counts as a reject. Both come with 95 % Wilson intervals at the live thresholds, plus a what-if sweep of one global threshold. Fewer than 30 labelled rows are anecdotes, not rates. |
| `offline-fa` | the public impostors (bake-off LibriSpeech and Kokoro, clean and far-field) against the LIVE enrolled ResNet34 profile, using the panel embedder (`scripts/setup/zoe_speaker_resnet34.py`, sha-pinned, self-tested). The profile is read from the Pi and held in memory only. It is guarded like a voice harness. |

Rows by era: `resnet34-dual` rows come from the dual week (ResNet34 was logged but did not act; the server verdict was
Resemblyzer). `resnet34` rows come after the flip (the `Speaker claim` line is the ResNet34 claim). `score --acting-only`
keeps only the second kind. `collect` also lists the rows where the panel-side accept and the server identity disagree.
They share thresholds, so a disagreement means a consent, model-id or join problem.

**How the owner uses it:** run `collect` every few days, label new rows with `label --play`, then run `score`. Once 30
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

**Wake replay and offline speaker FA:** pending the first run after 22:00 (quiet hours), filled in below.
