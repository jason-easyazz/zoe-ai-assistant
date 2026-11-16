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

| # | Item | What the owner would hear | Flag (allowed values) | Read by / takes effect on | State | The number, before | The A/B step | Order |
|---|---|---|---|---|---|---|---|---|
| 1 | First-sound instrument, #2495 | Nothing (log-only) | none | No flag. The panel daemon's own code: lands on the panel's next deploy and `zoe-voice` restart | Merged. **Needs a panel deploy**: the deployed panel build predates it [live ground truth] | Speech end to first sound, p50 2.73 s / p90 4.47 s, 8.5% under 1 s (inferred from older panel lines) | Deploy the panel (`DRY_RUN=1` first), then read `FIRST_SOUND` / `PCM_WRITE` rows in the scorecard after a few days | 1 |
| 2 | Pre-POST timing and `PANEL_KV_MISS cause=`, #2484 | Nothing (measure-only) | none (always on) | No flag. `zoe-data`, always on | Merged and **already live** [live: 14 `FLUE_PREPOST` lines, all harness traffic] | Pre-POST total p50 30 ms / p95 282 ms (14 harness turns); 0 household turns logged yet | None to run: read it. Wait for household turns and for a miss that carries `cause=` | 0 (done) |
| 3 | First clause released early, #2466 | The first words arrive sooner, from a shorter first phrase | `ZOE_FIRST_SOUND_CLAUSE` (bool), `ZOE_FIRST_SOUND_POLISH_EARLY` (unset follows the clause flag), `ZOE_KOKORO_STYLE_PIN` (bool), `ZOE_KOKORO_STYLE_PIN_LEN` (1..510, 120) | `zoe-data`, read per turn from its process environment (fixed when the unit starts): takes effect on a `zoe-data` restart. The pin also needs the Kokoro unit (`kokoro-tts`) restarted onto the sidecar code that accepts the pin; the flag itself is not read there | **Draft** (flag-dark). The pin also needs a Kokoro sidecar restart | Lab: first audio, chat shape, median 2.93 s off vs 2.77 s on (10 pairs), first unit 78 vs 60 characters [lab] | `scripts/perf/measure_first_sound.py` arms `off` / `clause+polish` / `clause+polish+pin`, then the replay gate with the flags on, then an ear check | 2 |
| 4 | Clean end-of-turn tail | She starts answering about 160 ms sooner on half the turns | `ZOE_VAD_CLEAN_TAIL_MS` (default dark, 560 is the candidate; panel environment) | The PANEL daemon, once at import from the panel's `.env.voice`: takes effect on a restart of the panel's `zoe-voice` user unit (a `zoe-data` restart does nothing) | Built in the daemon, dark. **Needs a panel deploy and the panel's env** | About 160 ms earlier on 124 of 246 corpus turns, 2% cut mid-sentence [documented in voice-pipeline.md] | Corpus replay for cut-offs, then the owner's ear | 3 |
| 5 | Blocks after the user's words, #2485 (B3) | Fewer slow turns right after a memory-using turn (the long ones, about 1 to 2 s) | `ZOE_BRAIN_VOLATILE_AFTER_USER` (bool) | `zoe-data`, per turn from its process environment: restart `zoe-data` | Merged dark | Panel turns that re-prefilled over 800 prompt tokens: 38 of 149 (25%), first-prompt-tokens p50 208 / p95 1695 [live, 2026-10-19 on] | Two-turn sessions (a memory turn then a plain turn), flag off vs on, compare the second turn's first-prompt tokens | 4 |
| 6 | Continuity ceiling, #2485 (B6) | Almost nothing | `ZOE_CONTINUITY_CEILING_S` (float in (0,10], 3.0 default, 0.4 candidate) | `zoe-data`, per turn from its process environment: restart `zoe-data` | Merged dark | Continuity block p50 1 ms / p95 1 ms on harness turns; matched on 0 of 19 member turns [live + scorecard] | Same A/B as 5; matters only when the database is slow | 6 |
| 7 | Pre-POST blocks concurrent, #2486 | Nothing you can hear | `ZOE_FLUE_PREPOST_CONCURRENT` (bool) | `zoe-data`, per turn from its process environment: restart `zoe-data` | Merged dark | The blocks it overlaps sum to 6 ms p50 / 19 ms p95 of a 30 ms / 282 ms pre-POST total [live, harness] | `FLUE_PREPOST total_ms`, 40 harness turns per arm, plus the byte-identical test | 7 |
| 8 | Prompt-read access ticks coalesced, #2490 | Nothing you can hear (household memory block meets its 0.40 s budget) | `ZOE_MEMORY_TICK_DEFER_S` (float seconds, 0 = at once, 1.0 candidate) | `zoe-data` (memory service), per read from its process environment: restart `zoe-data` | Merged dark | 27 of 171 `SEAM_HOUSEHOLD` lines were `budget_exceeded`, all from synthetic harness users; none of 11 household fetches missed [live] | `SEAM_HOUSEHOLD ms=` before and after, harness burst of 3 concurrent fetches | 8 |
| 9 | Listen pace, #2597 | She waits longer for a person who pauses, shorter for one who does not | `ZOE_LISTEN_PACE` (off, shadow, on) | `zoe-data` would read it per call, but nothing calls it yet: no effect at any value today | **Draft**, 1 of 5, pure decision logic, nothing wired | Cut-offs per 100 turns: unmeasured on the panel until row 1 is deployed | Shadow first; needs row 1's `still_talking` signal | 5 |
| 10 | Panel reflex ("mm", "I heard you"), #2470 + #2571 to #2573 | A short non-word sound at the end of your speech, well under 1 s | Panel `REFLEX_MODE` (off default, shadow, on) | The PANEL daemon, once at import, from the panel's `.env.voice`: the daemon's own switch is `REFLEX_MODE` (off, shadow, on; `on` still runs as shadow), the module's `REFLEX_ENABLED` is ignored by it; restart `zoe-voice` on the panel | #2470 merged but **not wired into the daemon, and no sound bank exists**; #2571 to #2573 are drafts that only log what it would have done | Share of turns with any sound under 1 s: 8.5% (all lexical) | Shadow decisions on `FIRST_SOUND` for a week, then bank, then wake turns only | 9 |

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
4. **Blocks after the user's words (row 5).** The only dark item aimed at the 1 to 2 s slow turns (a quarter of panel turns).
5. **Listen pace (row 9)**, shadow only, once row 1 gives it a signal.
6. Rows 6 to 8: a flip is cheap but worth tens of milliseconds; do them when the box is being flipped anyway.
7. **Reflex (row 10)** last in time, but the only item that can move the "under 1 s" share at all. Everything else leaves a
   brain turn at about 2 s at the floor.

## What each A/B must say

For every flag: run it **off then on, interleaved, the same prompts, a quiet box, warm**; the harness numbers are relative
(it is warm and stops before the panel), so judge the difference, not the level. Said-versus-did must not regress: run
`scripts/maintenance/voice_regression_probe.py` against the corpus `~/.zoe-voice-samples` with the flag on, under
`flock /tmp/zoe-voice-harness.lock`, and compare with the stored baseline. A flip is the owner's, after the lander's replay.

- Noise, stated once: the only measured spreads at this head are the felt scorecard (first sound p50 2.73 s, p90 4.47 s over 47
  turns), the first-clause lab (a single resampled brain reply swings a pair by seconds; speech-to-text alone ranged 0.9 s to
  4.6 s on the loaded box) and `FLUE_PREPOST` (p50 30 ms, p95 282 ms over 14 harness turns). **A paired standard deviation per
  item is not yet measured.** Sample size for a paired timing A/B is therefore a formula, not a number: n pairs is about
  7.85 x SD^2 / effect^2 (80% power, 5% two-sided); take the SD from the first 30 pairs, then decide whether to go on. For a
  yes/no count (a rate), zero events in n trials bounds the rate under 3/n (95%).
- Said-versus-did must not regress for ANY item: `scripts/maintenance/voice_regression_probe.py` against `~/.zoe-voice-samples`
  with the flag on. That is the floor, not the threshold below.

**Each item judged on its OWN number** (an item whose effect is below what its instrument can resolve says so, and is judged on
the mechanism, never on a felt gain):

| # | Own metric | Instrument at this head | Expected effect | Turns needed | Yes / no rule |
|---|---|---|---|---|---|
| 1 | Coverage and cost of the instrument | `FIRST_SOUND` and `PCM_WRITE` lines (daemon) | none (log-only) | 3 days of household turns | Yes: one `FIRST_SOUND` per wake turn and `underrun_max_ms` no worse than before the deploy. No felt-time claim |
| 3 | Speech end to first sound (`server_first_ms`), and first-unit length | `FIRST_SOUND` (panel) and `scripts/perf/measure_first_sound.py` (lab arms); first-unit characters from the lab | Lab: 0.05 to 0.4 s, not distinguishable from noise at 10 to 15 pairs | At least 30 pairs per arm for a median; recompute from their SD (formula above) | Yes: paired median down at least 100 ms AND p90 not up AND ear check clean. If the SD says a 50 ms effect needs more turns than the box will give, say "unresolved" and keep it dark; the shorter first unit alone is a mechanism check, not a gain |
| 4 | Recording close time (`close=` / `quiet_ms` in `FIRST_SOUND`) and cut-off rate | `FIRST_SOUND` on the panel; corpus replay for cut-offs | About 160 ms earlier on half the turns, 2% cut mid-sentence (corpus, 246 turns) | Close time is set by the daemon's own rule: 50 turns per arm; cut-offs: the full 246-turn corpus | Yes: median close down at least 100 ms on the turns where it fires AND cut-offs at most the corpus 2% AND no said-versus-did loss |
| 5 | Re-prefilled prompt tokens on the turn after a memory turn (`first_prompt_n`), and the share of panel turns over 800 | `FLUE_PROMPT_CACHE first_prompt_n`, `PANEL_KV_MISS` | 38 of 149 panel turns (25%) re-prefilled over 800 tokens | Share 25% to 10% needs about 97 turns per arm (80% power); the two-turn session A/B (a count, no box timing noise) needs 10 sessions per arm | Yes: second turn's median first-prompt tokens down at least 300 AND the over-800 share at or under 10% |
| 6 | `continuity_ms` inside `FLUE_PREPOST` | `FLUE_PREPOST continuity_ms=` | None while it sits at 1 ms p50 / 1 ms p95; only a slow database moves it | Read a week first: count of turns with `continuity_ms` at or over 400 (not yet measured) | If that count is zero, there is nothing to measure and it stays dark. Yes only if such turns exist and the ceiling cuts their wait with no lost block |
| 7 | Wall time of the concurrent blocks vs their sum, inside one turn | `FLUE_PREPOST` per-block fields, `total_ms` | At most 6 ms p50 / 19 ms p95 of a 30 ms / 282 ms total | A live A/B is NOT resolvable: the effect is under the noise of `total_ms` (SD not yet measured; p95 is nine times p50) | Judged on mechanism: the byte-identical test passes, and in a turn the group's wall time is under the sum of its blocks. Stated plainly: no felt-time claim |
| 8 | Share of `SEAM_HOUSEHOLD` fetches over the 0.40 s budget under a burst of concurrent fetches | `SEAM_HOUSEHOLD budget_exceeded` vs `ms=` | 27 of 171 over budget, all synthetic users; 0 of 11 household fetches | 60 bursts per arm: zero over budget in the on arm bounds the rate under 5% | Yes: on arm zero over budget with the off arm above zero. Not a prompt-token item: it saves database writes, and this head logs no count of tick writes, so the write saving itself is NOT measured by any current instrument |
| 9 | Cut-offs per 100 turns that listen pace would change (shadow) | None yet: needs `still_talking` from row 1 and is not wired | Unmeasured | Not yet measured | No flip: shadow only; revisit when row 1 has a week of data |
| 10 | Share of turns with any sound under 1 s; post-close talk; false starts | `FIRST_SOUND`, `REFLEX_DECISION` (panel log); `labs/voice-reflex-sim/sim.py` (stack part 4/4) | 8.5% today, all lexical | 7 days AND 100 turns, whichever is longer. 100 turns cannot show a 3-per-100 rate: zero events only bound it under 3% | Per `docs/knowledge/voice-reflex.md` (arrives with the reflex stack, #2612): post-close talk at most 3 per 100 turns, false starts at most 3 per 100 reflex turns, ear test 60%. `on` stays blocked |

- Rollback is per reader, not one command. **`zoe-data` flags** (rows 3 except the pin's sidecar side, 5 to 8): remove the line from
  the `zoe-data` environment files (`~/assistant/.env` or `services/zoe-data/.env`) and restart `zoe-data`. **Kokoro** (row 3's
  pin): unset the `zoe-data` flag, restart `zoe-data`, and restart `kokoro-tts` only if the sidecar was changed. **Panel flags**
  (rows 4 and 10): remove the line from `.env.voice` in the panel's voice directory and restart the `zoe-voice` user unit on the
  panel host (`zoe-pi`); restarting `zoe-data` leaves these live. **Rows 1 and 2**: no flag; row 1's rollback is the daemon `.bak`
  the deploy prints. No data migration is involved in any of them.

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

## Where the numbers come from

- Live logs: `~/.zoe-logs/zoe-data.app.log*` (`FLUE_PREPOST`, `PANEL_KV_MISS`, `SEAM_HOUSEHOLD`, `FLUE_PROMPT_CACHE`,
  `VOICE TIMING`); the felt scorecard `labs/felt-scorecard/scorecard.py`.
- Lab: `scripts/perf/measure_first_sound.py` (first clause), `scripts/perf/measure_brain_prefix_cache.py` (prefix-cache
  shapes), `tests/test_flue_prepost_concurrent.py` (the synthetic pre-POST rig).
- Design notes behind the rows: [Voice pipeline](voice-pipeline.md); the panel reflex and the listen-pace design are
  recorded in the PRs named in the table.
