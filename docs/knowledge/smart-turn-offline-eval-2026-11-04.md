---
type: Reference
title: Smart Turn vs the panel's fixed tail, offline (2026-11-04)
description: Samantha lane E E1. Smart Turn v3.2 was compared with the panel daemon's live 640deep/800 silence tail on 979 of the owner's real-voice corpus clips. Early cut-offs, lag after the true end of speech, natural pauses over 600 ms and a synthetic 1 s and 2 s mid-utterance pause were measured. Verdict - NOT YET. Smart Turn wins on lag but cuts the speaker off 1.3 to 3x as often, so it trips the initiative's kill rule. No fixed-tail change is recommended on this evidence alone.
tags: [voice, endpointing, smart-turn, turn-taking, vad, panel, measurement, samantha, lane-e]
timestamp: 2026-11-04T22:20:00+08:00
---

# Smart Turn vs the panel's fixed tail, offline (2026-11-04)

Lane E, experiment E1 of the [Samantha Initiative](../research/samantha-initiative-2026-11-03.md) has three criteria:
- **Pass:** at least 90 % correct end-of-turn, and a median tail at least 300 ms shorter.
- **Kill:** under 80 % correct, or more cut-offs than today.

**Result: NOT YET.** Two facts decide it:
- Every Smart Turn policy that saves 300 ms or more of median lag cuts the speaker off **more often than today**: 1.7-3.2x (8.1-15.0 % of turns against 4.7 %) with the 800 ms cap, and the 1.6 s-cap variants get down to 6.3 % (1.3x) only by over-waiting on 17-18 % of turns. That trips the kill rule.
- The Smart Turn policy with the fewest cut-offs saves only 240 ms of median lag (p50), and it still cuts more than today.

Smart Turn also costs about 120 ms of CPU per check on this box, which the lag figures below leave out. A retuned fixed tail (`480deep/800`) gets a similar saving with no model and fewer added cuts. Offline only: nothing live was changed.

## Method

Instrument: `scripts/perf/smart_turn_offline_eval.py` (its docstring is the full spec). The metrics are pinned by `tests/unit/test_smart_turn_offline_eval.py`.

**Ground truth.** The true end of speech is the end of the last 32 ms hop that the repo's Silero scores at 0.35 or above. That is `voice_vad`, ONNX v6, with the 64-sample context, so each model input is 576 samples. 0.35 is the daemon's own speech threshold.

**Pre-roll.** Like `record_command`, the endpointer never sees the wake pre-roll. The corpus has no lane tags, so the lane is inferred per clip:
- 20 chunks of pre-roll when command speech follows the 20-chunk window (wake path, 553 clips);
- 4 chunks otherwise (follow-up lookback, 426 clips).

**Room tone.** Each clip is extended with its own trailing room tone, not digital zeros. Zeros score as maximally deep silence and would flatter the deep-quiet rule.

**Fixed arms.** The real daemon `_Endpointer` from `scripts/setup/zoe_voice_daemon.py` is driven with scripted chunk probabilities (each chunk takes the max over its hops, as `_VadStream.feed` does). Live config, verified on zoe-pi `.env.voice` on 2026-11-04: `VAD_ENDPOINT_ENABLED=1`, `VAD_ENDPOINT_SILENCE_S=0.8`, `ZOE_VAD_TAIL_MS=640`.

**Smart Turn arms.** These use the real `voice_turn` scorer, `smart-turn-v3.2-cpu.onnx`, already on the box in `~/models`. After S ms of quiet that follows speech, the scorer reads the capture so far. If P(complete) is at or above T, the turn ends; otherwise it re-checks every 160 ms of continued quiet, and closes anyway at H ms of quiet.

**Definitions.** A **cut** is a close before the true end of speech. **Lag** is the time from the true end to the close. An **over-wait** is lag over 1 s. **Correct** means neither a cut nor an over-wait.

**Run.** 2026-11-04, 20:37-22:11 AWST, `nice -n 10`, with `/tmp/zoe-test-lane.lock` taken per batch. It covered 1,100 of the 1,701 clips in name order: 979 scored, 121 skipped (no speech after the pre-roll, or the wrong format). It stopped there because MemAvailable stayed under the 1,500 MB floor (about 950 MB, with the live brain, Kokoro and zoe-data resident); the aggregates had already been stable within about 1 point from n = 563 to n = 979. The run resumes from `--work-dir`.

## Results — natural clips (n = 979)

| arm | cut-offs | correct | lag mean / p50 / p90 (ms) | over-waits |
|---|---|---|---|---|
| **live 640deep/800** | **46 (4.7 %)** | **95.3 %** | **777 / 768 / 848** | 0 |
| plain 800 | 45 (4.6 %) | 95.4 % | 833 / 832 / 864 | 0 |
| 480deep/800 | 52 (5.3 %) | 94.7 % | 644 / 608 / 816 | 0 |
| plain 640 | 57 (5.8 %) | 94.2 % | 673 / 672 / 704 | 0 |
| plain 480 | 81 (8.3 %) | 91.7 % | 513 / 512 / 544 | 0 |
| plain 1200 | 19 (1.9 %) | 0 % | 1233 / 1232 / 1264 | 959 |
| ST S160 T0.5 H800 | 139 (14.2 %) | 85.8 % | 255 / 208 / 480 | 0 |
| ST S160 T0.95 H800 | 107 (10.9 %) | 89.1 % | 377 / 224 / 832 | 0 |
| ST S320 T0.5 H800 | 97 (9.9 %) | 90.1 % | 396 / 352 / 528 | 0 |
| ST S320 T0.95 H800 | 79 (8.1 %) | 91.9 % | 476 / 368 / 832 | 0 |
| ST S480 T0.5 H800 | 72 (7.3 %) | 92.7 % | 541 / 512 / 544 | 0 |
| ST S480 T0.95 H800 | 61 (6.2 %) | 93.8 % | 590 / 528 / 832 | 0 |
| ST S320 T0.95 H1600 | 62 (6.3 %) | 76.3 % | 622 / 368 / 1616 | 169 |
| ST S480 T0.95 H1600 | 44 (4.5 %) | 77.3 % | 737 / 528 / 1616 | 177 |

The full sweep is T ∈ {0.3, 0.5, 0.7, 0.85, 0.95}, S ∈ {160, 320, 480} and H ∈ {800, 1600}. It reproduces with the command in the script's docstring (its defaults are this sweep).
- **Threshold matters less than the first check.** At a fixed S, moving T from 0.3 to 0.95 removes only 2-4 points of cut-offs.
- **Smart Turn often calls short in-utterance pauses complete.** In this household's speech it frequently scores those pauses at 0.95 or higher.

By lane:

| lane | n | live: cuts / lag p50 | ST S320 T0.95 H800: cuts / lag p50 | ST S480 T0.95 H800: cuts / lag p50 |
|---|---|---|---|---|
| wake path | 553 | 7.0 % / 768 | 10.7 % / 384 | 8.7 % / 528 |
| follow-up | 426 | 1.6 % / 768 | 4.7 % / 368 | 3.0 % / 528 |

**Calibration floor.** The corpus was captured behind the live endpointer, so a perfect replay of the live config would cut 0. The live replay "cuts" 4.7 %, and 38 of those 46 cuts fall in natural pauses over 600 ms. That residual comes from three sources:
- the ONNX VAD on the Jetson against torch on the Pi (read chunk counts as ±1);
- lane inference errors;
- clips from other lanes.

Compare arms against each other, not against 0.

## Mid-sentence pauses

**Natural pauses.** 61 of 979 clips have an internal pause over 600 ms.

| arm | held | cut in that pause |
|---|---|---|
| live | 23 | 38 |
| plain 1200 | 48 | 13 |
| ST S480 T0.95 H1600 | 30 | 31 |
| ST S480 T0.95 H800 | 15 | 46 |

**Synthetic pauses.** On every second clip that has a mid-utterance word boundary (n = 145), the clip's own room tone was inserted at that boundary for 1 s and for 2 s.

How to read the "held" column:
- The audio before the pause is identical in both arms. So the closes before the pause are the "held" count of the 2 s arm, because no arm caps above 1.6 s.
- "Held through" a pause is therefore the 1 s count minus that number.

| arm | held through a 1 s pause | held through a 2 s pause |
|---|---|---|
| live 640deep/800 | 6 / 145 (4 %) | 0 |
| plain 1200 | 114 (79 %), but every natural turn over-waits | 0 |
| ST S160 T0.95 H1600 | 48 (33 %) | 0 (cap) |
| ST S480 T0.95 H1600 | 55 (38 %) | 0 (cap) |
| ST S480 T0.5 H1600 | 32 (22 %) | 0 (cap) |

As an "extend when unsure" signal, Smart Turn holds about a third of real-sounding 1 s mid-clause pauses that the live tail always cuts. It scores the rest as complete. The 2 s hold from the owner's audit is out of reach of any configuration that does not over-wait on every turn.

**Smart Turn inference cost.** Measured on the Jetson with 2 ORT threads: p50 118 ms and p90 149 ms per check, over 9,690 calls. Every model close lands about that much later than the lag in the tables. The Pi has not been measured.

## Recommendation

1. **Do not put Smart Turn in the daemon as the closer.** No threshold meets the initiative's bar.
   - Every policy that saves 300 ms or more cuts more than today: 1.7-3.2x with the 800 ms cap, 1.3x at best with the 1.6 s cap (and 17 % over-waits).
   - The closest, S480 T0.95 H800, saves 240 ms of median lag (p50) for +1.5 points of cut-offs. After about 120 ms of inference that is roughly a 120 ms real gain.
   - The fixed `480deep/800` saves 160 ms of median lag (p50) for +0.6 points, with no model.
2. **Do not retune the fixed tail on this alone.** `ZOE_VAD_TAIL_MS=480` is the cheapest lag lever measured: -133 ms mean lag for +6 cut-offs in 979 turns. The owner's pains include cut-ins, so it is an ear-test candidate, not a default.
3. **Next experiment, if this is pursued: Smart Turn as an extender only.** Keep the live 640 ms close. At that point, consult Smart Turn, and extend to at most 1.6 s when it says "incomplete".
   - By construction it cannot cut more than today.
   - On the synthetic arm it would hold about a third of 1 s pauses.
   - It costs over-waits on turns Smart Turn misjudges.
   - Measure it with `--first-ms 640 --caps 1600` (not run here because RAM stayed under the floor).
   - Pair it with the initiative's trailer lexicon, since the cut-offs concentrate at short pauses after syntactically complete prefixes.

## Limits

- **Ground truth is acoustic, not semantic.** A clip the live endpointer already cut mid-thought looks complete here, so every arm's cut-off count is a lower bound.
- **A Smart Turn cut is not always harmful.** It may be a complete command followed by an afterthought, and the two cannot be told apart without transcripts. None were run, deliberately.
- **The synthetic boundary is a simplification.** It is the internal quiet run nearest the utterance midpoint, and some of those boundaries really are complete.
- **No private data leaves the box.** Only aggregate numbers are recorded here. No audio, transcript or clip name is in the repo, the PR or this record.
