---
type: Reference
title: Voice feel - what is built but dark, and the order to switch it on
description: Every built-but-dark voice-feel item (speed flags, first clause, listen pace, panel reflex, first-sound instrument) with what the owner would hear, its flag, its state, its one number with a before value, its A/B step, and the recommended switch-on order.
tags: [voice, latency, feel, switch-on, ab-test, flags]
timestamp: 2026-11-15T00:00:00Z
---

# Voice feel: built but dark, and the order to switch it on

Written 2026-11-15 from the live logs on this box and a lab A/B. It is a plan, not a record of a flip: nothing here was
turned on. Numbers are counts and timings only. Labels: **[live]** read from the running box's logs, **[lab]** measured in a
harness process with the flag set in that process only, **[code]** read in the source, **[inferred]** reasoned, not measured.

The owner's question was whether Zoe sounds more human yet. Honest answer: not yet. The felt number is **speech end to first
sound on the panel: p50 2.73 s, p90 4.47 s, 8.5% of turns under 1 s (47 turns, one week to 2026-11-14)**, from the felt
scorecard (`labs/felt-scorecard/`). Nothing below has moved it yet.

## The table

| # | Item | What the owner would hear | Flag (allowed values) | State | The number, before | The A/B step | Order |
|---|---|---|---|---|---|---|---|
| 1 | First-sound instrument, #2495 | Nothing (log-only) | none | Merged. **Needs a panel deploy**: the deployed panel build predates it [live ground truth] | Speech end to first sound, p50 2.73 s / p90 4.47 s, 8.5% under 1 s (inferred from older panel lines) | Deploy the panel (`DRY_RUN=1` first), then read `FIRST_SOUND` / `PCM_WRITE` rows in the scorecard after a few days | 1 |
| 2 | Pre-POST timing and `PANEL_KV_MISS cause=`, #2484 | Nothing (measure-only) | none (always on) | Merged and **already live** [live: 14 `FLUE_PREPOST` lines, all harness traffic] | Pre-POST total p50 30 ms / p95 282 ms (14 harness turns); 0 household turns logged yet | None to run: read it. Wait for household turns and for a miss that carries `cause=` | 0 (done) |
| 3 | First clause released early, #2466 | The first words arrive sooner, from a shorter first phrase | `ZOE_FIRST_SOUND_CLAUSE` (bool), `ZOE_FIRST_SOUND_POLISH_EARLY` (unset follows the clause flag), `ZOE_KOKORO_STYLE_PIN` (bool), `ZOE_KOKORO_STYLE_PIN_LEN` (1..510, 120) | **Draft** (flag-dark). The pin also needs a Kokoro sidecar restart | Lab: first audio, chat shape, median 2.93 s off vs 2.77 s on (10 pairs), first unit 78 vs 60 characters [lab] | `scripts/perf/measure_first_sound.py` arms `off` / `clause+polish` / `clause+polish+pin`, then the replay gate with the flags on, then an ear check | 2 |
| 4 | Clean end-of-turn tail | She starts answering about 160 ms sooner on half the turns | `ZOE_VAD_CLEAN_TAIL_MS` (default dark, 560 is the candidate; panel environment) | Built in the daemon, dark. **Needs a panel deploy and the panel's env** | About 160 ms earlier on 124 of 246 corpus turns, 2% cut mid-sentence [documented in voice-pipeline.md] | Corpus replay for cut-offs, then the owner's ear | 3 |
| 5 | Blocks after the user's words, #2485 (B3) | Almost nothing: the offline A/B below finds the flag saves about 14 prompt tokens (0.02 s) on the second turn, not the 1 to 2 s the first reading hoped for | `ZOE_BRAIN_VOLATILE_AFTER_USER` (bool) | Merged dark | Panel turns that re-prefilled over 800 prompt tokens: 38 of 149 (25%), first-prompt-tokens p50 208 / p95 1695 [live, 2026-10-19 on] | `scripts/perf/measure_prompt_cache_ab.py`: scripted two- and three-turn conversations through the real seam, flag off vs on (ABAB), compare the second turn's first-prompt tokens; `--offline` is the safe no-model mode, `--live-slot` needs the slot to itself (see the script doc and the result block below) | 4 |
| 6 | Continuity ceiling, #2485 (B6) | Almost nothing | `ZOE_CONTINUITY_CEILING_S` (float in (0,10], 3.0 default, 0.4 candidate) | Merged dark | Continuity block p50 1 ms / p95 1 ms on harness turns; matched on 0 of 19 member turns [live + scorecard] | Same A/B as 5; matters only when the database is slow | 6 |
| 7 | Pre-POST blocks concurrent, #2486 | Nothing you can hear | `ZOE_FLUE_PREPOST_CONCURRENT` (bool) | Merged dark | The blocks it overlaps sum to 6 ms p50 / 19 ms p95 of a 30 ms / 282 ms pre-POST total [live, harness] | `FLUE_PREPOST total_ms`, 40 harness turns per arm, plus the byte-identical test | 7 |
| 8 | Prompt-read access ticks coalesced, #2490 | Nothing you can hear (household memory block meets its 0.40 s budget) | `ZOE_MEMORY_TICK_DEFER_S` (float seconds, 0 = at once, 1.0 candidate) | Merged dark | 27 of 171 `SEAM_HOUSEHOLD` lines were `budget_exceeded`, all from synthetic harness users; none of 11 household fetches missed [live] | `SEAM_HOUSEHOLD ms=` before and after, harness burst of 3 concurrent fetches | 8 |
| 9 | Listen pace, #2597 | She waits longer for a person who pauses, shorter for one who does not | `ZOE_LISTEN_PACE` (off, shadow, on) | **Draft**, 1 of 5, pure decision logic, nothing wired | Cut-offs per 100 turns: unmeasured on the panel until row 1 is deployed | Shadow first; needs row 1's `still_talking` signal | 5 |
| 10 | Panel reflex ("mm", "I heard you"), #2470 + #2571 to #2573 | A short non-word sound at the end of your speech, well under 1 s | Panel `REFLEX_ENABLED` (default off) | #2470 merged but **not wired into the daemon, and no sound bank exists**; #2571 to #2573 are drafts that only log what it would have done | Share of turns with any sound under 1 s: 8.5% (all lexical) | Shadow decisions on `FIRST_SOUND` for a week, then bank, then wake turns only | 9 |

The order column is the recommended switch-on order, row 0 being already done. Rows 6 to 8 are last because their measured
effect on a household turn is within a few tens of milliseconds.

## Recommended order, and why

1. **Deploy #2495 (row 1).** It cannot be heard, costs about 37 microseconds per audio chunk, and every panel-side item below
   needs its numbers: the real first-sound time, and whether chunked audio leaves gaps.
2. **First clause (row 3), the `clause+polish` arm first, the style pin second.** It is the only dark item that is
   server-side only (no panel deploy), and the only one whose job is the felt number. Expect a small gain, not the 0.4 to
   0.8 s the older audit hoped for (see below). Turn on the clause arm alone first; add the pin only after an ear check.
3. **Clean tail (row 4).** About 160 ms on half the turns, which is a bigger and surer saving than the clause arm, at a 2%
   cut-off cost. It needs a panel deploy anyway, so it rides with row 1's.
4. **Blocks after the user's words (row 5).** Aimed at the 1 to 2 s slow turns (a quarter of panel turns), but the offline A/B (below) shows it saves only about 14 tokens; do not expect to hear it. Demote it unless a live-slot run says otherwise.
5. **Listen pace (row 9)**, shadow only, once row 1 gives it a signal.
6. Rows 6 to 8: a flip is cheap but worth tens of milliseconds; do them when the box is being flipped anyway.
7. **Reflex (row 10)** last in time, but the only item that can move the "under 1 s" share at all. Everything else leaves a
   brain turn at about 2 s at the floor.

## What each A/B must say

For every flag: run it **off then on, interleaved, the same prompts, a quiet box, warm**; the harness numbers are relative
(it is warm and stops before the panel), so judge the difference, not the level. Said-versus-did must not regress: run
`scripts/maintenance/voice_regression_probe.py` against the corpus `~/.zoe-voice-samples` with the flag on, under
`flock /tmp/zoe-voice-harness.lock`, and compare with the stored baseline. A flip is the owner's, after the lander's replay.

- Instrument per number: pre-POST ms is `FLUE_PREPOST`; first reply text and time to first token is `VOICE TIMING`
  (`brain_ttft_ms`) plus, from #2466, the `fs_*` fields; cache re-prefill is `FLUE_PROMPT_CACHE first_prompt_n` and
  `PANEL_KV_MISS`; the audible number is `FIRST_SOUND` after row 1.
- Sample size: a stable median needs about 30 pairs per arm; a stable p95 needs 60 to 100. The box was contended during the
  lab run (speech-to-text alone ranged 0.9 s to 4.6 s), and one resampled brain reply swings a pair by seconds, so 10 pairs
  decided nothing. Run on a quiet box.
- Improved: median first sound down by at least 100 ms with no rise in the p90, or (for the cache items) the second turn's
  first-prompt tokens median down by at least 300 tokens (about 0.45 s at the measured 1.478 ms per token). Regressed: any
  said-versus-did loss, p90 up by 200 ms or more, or an ear-check complaint.
- Rollback for all of them: unset the flag and restart `zoe-data` (the pin also needs the Kokoro sidecar restarted). No
  data migration is involved in any of them.

## What the lab run showed, and its limits

**First clause (#2466), [lab], 2026-11-15.** `measure_first_sound.py`, live services, replay envelope (no writes), 20 prompts
(10 chat-shaped, 10 memory-shaped) under `off` and `clause+polish`; the pin arm could not run because the live Kokoro
sidecar is the old code. Chat shape: first audio median **2.93 s off, 2.77 s on**, paired median difference **-0.05 s**,
faster in 5 of 10 pairs; first unit **78.5 characters off, 59.5 on**; the wait from the model's first token to a releasable
first unit was **0.21 s in both arms**; Kokoro synthesis of that unit **0.51 s off, 0.44 s on**. Brain turns with no tool call
(15 pairs): median first audio **3.19 s off, 2.80 s on**, faster in 10 of 15. The honest reading: the arm does what it says
(shorter first unit, shorter synthesis) and the direction is right, but the gain in this sample is between about 0.05 and
0.4 s and not distinguishable from noise. Limits: n of 10 and 15; the replies are re-sampled each run so pairs do not share
text; the box was loaded; the harness is warm and stops before the panel, so it is relative. Not measured: prosody of a clause
spoken alone, the seam between two chunks, the pinned style, gaps on the panel.

**Pre-POST blocks, [live].** Of the 14 turns logged since the timing line went live, none was a household turn. Pre-POST
total p50 30 ms, p95 282 ms; the blocks #2486 overlaps total 6 ms p50 and 19 ms p95, about a tenth of the whole. So #2486
cannot save more than about that on a plain turn. (The unit test's synthetic rig, with 250 ms of invented per-block delay, shows 254 ms sequential against 118 ms concurrent [lab]: that proves the overlap works, not that the real delays are that large.) The 50 to 400 ms the earlier audit guessed has not shown up on harness
traffic; it can only be confirmed or dropped once household turns appear in `FLUE_PREPOST`.

**Cache re-prefill, [live].** 48 `PANEL_KV_MISS` lines since 2026-10-18 (26 `first`, 22 `warm`); all predate #2484's
`cause=`, so the cause of any miss is still unknown. That is the first thing to read after a day of household use.

**Blocks after the user's words (#2485), [lab], offline, 2026-11-15.** `scripts/perf/measure_prompt_cache_ab.py --offline
--tokenizer server --pairs 30`: the real seam's prompt building (synthetic turn rig, flag set in the lab process only, nothing
sent, nothing written), the sidecar's stale-block elision, the llama-server's own `/tokenize` (tokenization only, no slot), 30
scripted conversations per shape x 6 shapes x 2 arms (ABAB), three turns each. Second-turn **uncached tokens, median: 142 off vs
128 on (plain), 140 vs 129 (recall then plain), 426 vs 408 (recall every turn), 369 vs 354 (after a write), 141 vs 126 (now-card
change), 142 vs 128 (session start with bridge)**: the flag saves **12 to 15 tokens at the median, about 0.02 s at 1.478
ms/token, in 30 of 30 pairs in every shape; the threshold is 300**. Share of second turns over 800 uncached tokens: 0% in both
arms (p90 of the worst shape 551 off, 528 on). Reading: the flag keeps the user's own words cached, which is a dozen tokens;
the recall block of the NEW turn is uncached in both arms, and the live 25% of turns over 800 must come from something else
(tool-block growth, a session change, slot eviction: read `PANEL_KV_MISS cause=`). So this flag cannot fix the slow turns
and is not worth a switch-on for feel. Limits: synthetic filler words, a guessed Gemma-style template, a synthetic 3.4k-token
system and tool prefix (it fixes absolute size only), scripted replies (no said-versus-did signal: not assessed), no timings.
Not measured: the live slot (`--live-slot` needs the slot to itself; see the script doc), sidecar tool-block behaviour.

## Where the numbers come from

- Live logs: `~/.zoe-logs/zoe-data.app.log*` (`FLUE_PREPOST`, `PANEL_KV_MISS`, `SEAM_HOUSEHOLD`, `FLUE_PROMPT_CACHE`,
  `VOICE TIMING`); the felt scorecard `labs/felt-scorecard/scorecard.py`.
- Lab: `scripts/perf/measure_first_sound.py` (first clause), `scripts/perf/measure_brain_prefix_cache.py` (prefix-cache
  shapes), `scripts/perf/measure_prompt_cache_ab.py` (flag A/B on consecutive turns), `tests/test_flue_prepost_concurrent.py` (the synthetic pre-POST rig).
- Design notes behind the rows: [Voice pipeline](voice-pipeline.md); the panel reflex and the listen-pace design are
  recorded in the PRs named in the table.
