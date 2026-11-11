---
type: research-lane
title: Lane 3 - bench numbers measured 2026-11-11 (existing instruments only)
date: 2026-11-11
---

# Lane 3 - bench numbers, 2026-11-11 (22:30-01:00 AWST)

Code under test: `main` @ `1ddb70951` (= the live checkout's HEAD at 22:30). The live checkout had moved to `7ad2630f4` by
00:55, when a voice-PR landing (#2408) and its deploy ran mid-session. Every number below is for `1ddb70951`. The live checkout has one tracked edit
(`CAPABILITIES.md`) and four untracked files, so live-rooted runs cannot be commit-bound. The harnesses ran from a clean detached
worktree (`~/.worktrees/samantha-bench-1111`) instead. The scratch stack serves that worktree's code and reads only settings from the
live checkout. The live Flue `dist/` and `node_modules/` were symlinked in read-only, after checking that the live Flue `src/` is
byte-identical and older than its build (the same recipe as `samantha_weekly.sh`). So the bar run below is `evidence.bound: true`
for commit `1ddb70951` / tree `cd62e73f`.

Labels: [Proven]/[Experimental]/[Speculative] x [code]/[live]/[record]/[contradicted]. "live" here means "measured tonight on
this box". Live-brain runs used the scratch stack: a throwaway Postgres plus a second zoe-data and Flue. They shared only the
llama-server slot and the router. Nothing touched the household Postgres or MemPalace.

## Headline table

| instrument | commit | when (AWST) | result | vs last baseline | label |
|---|---|---|---|---|---|
| Samantha bar, full, samples=3 (control arm) | 1ddb70951 (bound) | 11-11 23:00-23:22 | 29 scored: 26 PASS, 2 FAIL (S8, S11), 1 SKIP (S16). Status `error` because the teardown was unproven (scratch capture still in flight, `in_flight=6`) | baseline = S1-S8 only (09-30, `11c6c0f2`): S8 regressed (single-run, known flake cell). Against the last full run (10-09, `4bb903d2`, 23 cells, 18 PASS / 3 FAIL / 2 SKIP): S10 and S21 now PASS (both "expected FAIL" targets), S24 now PASS (was FAIL), S11 now FAIL (was PASS), S28-S33 are new and all PASS | [Proven][live] verdicts; [Experimental] as a reliability claim (k=1) |
| Bar, ZOE_MEMORY_NOW_CARD=on arm (harness arm only) | 1ddb70951 (bound) | 11-12 00:10-00:32 | 29 scored: 27 PASS, 1 FAIL (S11), 1 SKIP (S16). Status `error` (same unproven teardown, `in_flight=6`) | S8 PASS here; S11 FAIL as in control | [Experimental][live] (k=1) |
| Bar baseline coverage | - | file dated 2026-09-30 | **8 of 29 cells baselined (S1-S8). Unchanged since 11-03.** Tonight could not re-record: the launcher refused, exit 4, dirty live checkout | - | [Proven][record] |
| Persona drift battery, live (60 turns, one session) | 1ddb70951 (bound) | 11-12 00:47-00:51 (268 s) | status ok, **no drift**. Position scores T1 0.92, T20 0.92, T60 0.92, mid 0.96. Every cell is stable across positions; the always-FAIL cells are `acknowledges_first` and `voice_marker` at T1/T20/T60, plus `contractions@mid`. Turn latency p50 2175 ms, p90 2788 ms; teardown proven. (The first attempt at 23:43 was REFUSED: MemAvailable 1530 < 1900 MB floor while a voice lander ran.) | no baseline exists (not recorded: a baseline record was not requested and nothing should overwrite the bar's state tonight) | [Proven][live] for this run; [Experimental] as a trend (n=1) |
| Warmth probes on the persona transcript (synthetic, AU) | 1ddb70951 | 00:52 | follow_up 1/1, locale 60/60, no_ctx_ack 60/60, no_parrot 1/4 (3 FAIL), no_extras n/a (0 applicable). `--after-guard` gives identical counts | old QA record: follow_up 2/7, locale 87/91, no_parrot 3/7 (different transcript, not comparable) | [Experimental][live] (small n, generic script) |
| Persona drift `--controls` (instrument proof) | 1ddb70951 | 22:36 | OK: 9/9 injected faults redden their cell and flag drift | - | [Proven][code] |
| Warmth probes (#2326) | 1ddb70951 | 22:36 | Not runnable live: the instrument scores a QA transcript and no new transcript exists. Unit pins 212/212 (with forget + minor-affect, see below). Last record (QA rounds, before the guard): follow_up 2/7, no_extras 2/3, locale 87/91, no_parrot 3/7 | - | [Proven][record] for the old counts; [code] for the pins |
| Minor-affect safety cell (a) (`minor_affect_fp.py`) | 1ddb70951 | 22:36 | PASS: retained detector on minor lines, FP 0/110 (Wilson95 upper 3.4%), recall 20/20 (lower 0.84). Repo fixture is invented speech-shaped text, not real speech | first run on record here | [Proven][code] |
| Forget-completeness probe (#2300) + minor-affect cells (#2302) + warmth probe tests | 1ddb70951 | 22:36 | 212 passed, 0 failed, 0 skipped (pytest, zoe-data py3.12 venv) | - | [Proven][code] |
| Continuity cells C1/C3/C4 `--selftest` | 1ddb70951 | 22:36 | instrument proof OK (perfect / amnesiac / silent / stale / decaying references each go the declared way). **Live carry / re-tell / stale numbers still unmeasured: no live adapter exists** | - | [Proven][code]; live = zero |
| ZMB (zoe memory bench) store tier, arm Z0 | 1ddb70951 (clean) | 22:36 (14 s) | 171 PASS / 1 FAIL (`I2.third_party_fragment`, an expected-FAIL target) / 41 SKIP (live-clone or embedder cells); controls red 173/173; all 14 axes CLAIMABLE. Matches the last full store run on 10-07 except that 5 axes are new | no ZMB baseline file | [Proven][code] |
| Person bench `--controls` | 1ddb70951 | 22:36 | instrument proof OK (all negative-control arms red) | - | [Proven][code] |
| Person bench `--checkout` (P1, P3.d, P4 in-process, 3 seeds) | 1ddb70951 | 22:36 | arm `system`: every half PASS (P1.a-d 60/60, P3.d 5/5, P4.a-d 60/60); the restraint-off and selector-bypassed controls go red | last checkout record 10-12 | [Proven][code] |
| Person bench live (P family) | - | not run | needs ~40 min of brain slot (last full runs 10-08/10-09: 2149 s and 2418 s); over budget | last full 10-09 `4bb903d2` | not measured |
| Day-sim (default mode) | - | 11-12 00:46 | REFUSED by its own gate: "1 deploy.yml run(s) queued/in progress" (the lander's merge was deploying). **Exit code was 0 on that refusal**, so a wrapper reading rc alone would call it green | last full 10-12 (allowlisted mode, 9/10, verdict FAIL, `4bad5961`); no day-sim baseline file exists | [Proven][live] refusal |
| Smart Turn offline corpus comparison (#2280) | - | not run tonight | Record 11-04: fixed tail cuts 4.7% (a calibration floor); every Smart Turn closer cuts 1.3-3.2x more; inference p50 118 ms on the Jetson. The extend-only arms are still "run pending" | - | [Proven][record] |
| Voice regression probe, nightly (04:30) | 8ae79cda | 11-11 04:32 | FAIL on SPEED only: STT 795 ms vs 488 ms baseline (1.63x); said-vs-did 18/18 OK, 0 regressions; cold first turn PASS (TTFT 504 ms, cache 99%) | baseline 10-29 | [Proven][record] |
| Voice regression probe, latest PR (#2406 head) | 47f7ef89 (clean) | 11-11 20:49 | PASS: 18/18 OK; STT 484 / brain 1424 / e2e 1598 ms (ratios 0.99 / 0.98 / 1.04); cold TTFT 614 ms (1.24x, pass) | baseline 10-29 | [Proven][record] |

## Per-instrument notes (commands, locks, durations)

- **Offline batch** (`scratchpad/bench/offline_batch.sh`), one invocation under `flock -x /tmp/zoe-test-lane.lock nice -n 5`,
  22:35:49-22:36:28 (39 s), every rc 0. Checked first: `night_busy.sh test` rc 1 (= may run) and no lander. Steps: pytest
  (`services/zoe-data/tests/test_forget_completeness_probe.py test_minor_affect_safety_cells.py test_warmth_probes.py`, py3.12 venv
  plus overlay, JUnit 212/0/0/0); `minor_affect_fp.py --json`; `continuity_cells.py --selftest`; `persona_drift.py --controls`;
  `samantha_person.py --controls`; `samantha_person.py --checkout --seeds zmb-v1,fresh-a,fresh-b`;
  `zoe_memory_bench.py --tier store --results <scratchpad>`. Side effect: ZMB appended one line to `~/.cache/zoe/zmb_trend.jsonl`
  (its default trend). The person bench `--checkout` wrote its own default `samantha_person_checkout.json`.
- **Bar control arm**: `ZOE_PERF=1 ZOE_NIGHT_BUSY=... nice -n 5 python3 scripts/perf/bar_reliability.py run --out-dir <scratchpad>/bar_ab
  --arm control=<wt> --arm candidate=<wt> --arm-env candidate:ZOE_MEMORY_NOW_CARD=on,ZOE_MEMORY_NOW_CARD_HARNESS=1 --k 1 --step-min 35
  -- --samples 3 --keep-replies`. Started 23:00. That was after the household-quiet cut-in and the night_busy checks, with no lander
  and MemAvailable 6.5 GB at the start. It ran 1328 s and held the brain slot about 22 min. Scratch stack RAM: about 665 MB in total
  (zoe-data 514 MB, Flue 124 MB, Postgres 28 MB). Recall-packet size: 146 packets, mean 1189 chars, p95 2350 chars (~588 tokens),
  max 2628. Ask latency over 49 asks: p50 543 ms, p90 6.3 s, max 9.9 s.
  - S8 FAIL: after 32 filler turns the sister fact came back but the dad/lighthouse fact did not (`dad_found: []`, 0 filler errors).
    This is the documented S8 retrieval-edge flake (see samantha-bar.md "Flake rate"). A single red is a hypothesis, not a regression.
  - S11 FAIL: confirm, store, idempotence and recall all passed. "Forget that." left the coriander in the recall packet, and the reply
    did not say it forgot. This is the same leg that failed on 10-09 (the forget routing). [Proven][live] for this run.
    k=1 does not tell a flake from a regression.
  - Teardown: forget-synthetic removed 59 rows plus the Postgres rows, but one demo user still had 6 captures in flight when the wait
    expired. Teardown unproven means `status: error`, so `bar_reliability` rejected the run and stopped before the candidate arm.
    The scratch stack was destroyed afterwards, so nothing persists. The bar left
    `~/.cache/zoe/samantha_bar_pending_teardown.json` (23:20). It names scratch-only ids. Left in place per the instrument's contract.
- **Batch 1** (`scratchpad/bench/live_batch.sh`) went on to persona drift at 23:43. It was refused by the scratch floor (1530 MB <
  1900 MB). A voice lander (`land_voice_pr.sh 2408`) had started meanwhile, so my gate skipped the day-sim.
- **Batch 2** (`scratchpad/bench/live_batch2.sh`) waited, polling every 30 s, until there was no lander, `night_busy voice/test` both
  rc 1 and MemAvailable >= 1950 MB, with a 01:00 launch cutoff. Then it ran three steps in order, each under
  `flock -w 900 /tmp/zoe-voice-harness.lock nice -n 5`: the candidate bar arm (00:10), the day-sim (00:46, refused: deploy in
  progress) and persona drift (`persona_drift.py --results/--trend <scratchpad>`, 00:47-00:51). Nothing started after 00:47.
- **Warmth probes**: `flock -x /tmp/zoe-test-lane.lock <py312> services/zoe-data/warmth_probes.py <persona transcript as {in,out}
  jsonl> --country AU [--after-guard]`. The transcript is the battery's generic synthetic script, not household speech.

## A/B: control vs ZOE_MEMORY_NOW_CARD=on (harness arm only)

The instrument supports it: `--arm-env` / `ZOE_HARNESS_ARM_ENV` layers the override onto the scratch zoe-data only, so the live
service env is untouched. `bar_reliability.py run --k 1` ran the control arm, then stopped: the control's unproven teardown made it a
rejected run. The candidate arm was therefore run directly:
`ZOE_HARNESS_ARM_ENV='{"ZOE_MEMORY_NOW_CARD":"on","ZOE_MEMORY_NOW_CARD_HARNESS":"1"}' flock -w 900 /tmp/zoe-voice-harness.lock nice -n 5
python3 scripts/perf/samantha_bar.py --samples 3 --results/--trend/--pending <scratchpad>`. Same commit and same bound tree; the result
records `scratch.arm_env` = both overrides. Start 00:10, 1280 s.

| metric | control (now-card off) | candidate (now-card on) |
|---|---|---|
| bar cells PASS / FAIL / SKIP (of 29) | 26 / 2 (S8, S11) / 1 | 27 / 1 (S11) / 1 |
| differing cells | S8 FAIL | S8 PASS |
| recall packet chars mean / p95 / max | 1189 / 2350 / 2628 | 1222 / 2509 / 3052 |
| est. tokens p95 | 588 | 628 |
| ask latency p50 / p90 / max (49 asks) | 543 / 6291 / 9851 ms | 400 / 3290 / 9472 ms |
| teardown | unproven (in_flight=6) | unproven (in_flight=6) |

Reading, honestly:

- The only verdict difference is S8, the documented flake cell (one ask per fact after 32 fillers). At k=1 this is noise until
  repeated. No collateral regression was seen, but k=1 cannot show one either. [Experimental]
- The latency gap is not attributable to the card. The runs were 70 min apart; the control ran while the household could still use
  the brain and the candidate ran after midnight. [Speculative]
- Did the card actually serve? Not verified. The scratch stack's own logs (`NOW_CARD ... served=`) are removed with the stack, and
  the bar does not record a sources-used share. The +33 mean / +159 p95 packet chars are consistent with an extra block, but they are
  not proof. **Sources-used share: not measured (zero)**, because no instrument output reports it.
- `ZOE_CONVERSATION_NOTES` and `ZOE_RECALL_EVERY_TURN` A/Bs were not run (one A/B only, within budget). Brain slot used tonight:
  about 22 + 21 + 4.5 = 48 min.
- **Instrument finding:** both bar runs ended with the same unproven teardown, one demo user with 6 captures in flight. On the
  scratch stack the post-turn capture backlog does not drain inside the quiesce wait. As a result every full bar run tonight was
  `status: error`, which makes `bar_reliability` (pass^k and the A/B admissibility) unable to accept ANY run. Until that is fixed,
  the k=3 night command cannot succeed. [Proven][live] x2 (both runs)

## Nightly voice-gate STT failure: what the artifacts say

- **The 795 ms nightly is not a one-off. It is the 04:30 slot.** From `~/.cache/zoe/voice_regression_trend.jsonl` (1133 lines;
  self-tests excluded): runs at 04:xx AWST have median STT **745 ms (n=62)**, against **535 ms (n=913)** for every other hour.
  12 of the last 14 nightlies failed on STT speed (10-28 to 11-10: 732, 820, 679, 970, 776, 856, 746, 811, [692 pass], 797, 882, 737,
  [713 pass], 795). Daytime PR probes cluster at 450-550 ms. [Proven][record]
- **The slowdown is inside zoe-data's STT, not in the harness or HTTP.** zoe-data logs its own per-request time
  (`voice/transcribe panel=replay-harness ... STT=`). The server-side median was 785 ms at 11-11 04:3x, against 445-480 ms for the
  daytime probes the same day. Per audio-second it was 229 ms against about 155-160 ms. [Proven][record]
  (`~/.zoe-logs/zoe-data.app.log`)
- **Ruled out:**
  - Memory pressure. MemAvailable was 2650 MB at 04:30, more than in the day, and swap was flat at 1789 MB
    (`~/.zoe-logs/memhist/2026-11-11.log`). [contradicted]
  - A cold zoe-data. zoe-data had been up 63 min, since the night window restored it at 03:27, and the Moonshine warm-up finished in
    2.85 s. Across 141 probes, uptime does not predict STT time: daytime probes 6-35 min after a restart have a median of 514 ms.
    [contradicted]
  - The STT mode. The nightly unit and the lander both use `--stt remote` against the live service. [contradicted]
  - Omi pendant transcription in the same process. No OMI lines fall in the 11-11 04:3x window. Probe windows with Omi activity have
    a median of 515 ms, against 495 ms without. [contradicted]
  - Concurrent timers. Only the 04:15 Serena restart is near, and its indexing is lazy. System timers fire at 04:38 and 04:48, after
    the run. [Speculative] that any of them matters.
- **Unexplained, with candidates.** Something makes CPU-side Moonshine about 1.5x slower in that slot. Moonshine runs on CPU
  (voice-pipeline.md).
  - (a) The whole voice stack is freshly re-placed after the night window: llama, Kokoro, the router and zoe-data were all
    restarted at 03:26-03:27 after `compact_memory`. Daytime restarts touch only zoe-data. [Speculative]
  - (b) The nightly unit runs the probe without `nice` (the lander uses `nice -n 5`), so the probe process competes equally with
    zoe-data for CPU. [Speculative]
  - (c) CPU clocks or thermal state at that hour. The governor reads schedutil now, at max 1.98 GHz. Nothing logs it at 04:30.
    [Speculative]
- **Consequence.** The nightly is compared against a baseline recorded in the evening (10-29 23:50), so the gate fails on a time-of-day
  effect, not on code. The latest PR probe (20:49) is the trustworthy speed number: STT 484 / brain 1424 / e2e 1598 ms.
  Said-vs-did was 18/18 in both runs. [Proven][record]
- **Cheapest discriminating test (not run; it needs the 04:30 slot):** log `/proc/loadavg` plus `scaling_cur_freq` per core in
  memhist, or in the probe, around 04:30. Or run one extra probe at about 05:30 the same morning. If 05:30 is normal, the cause
  is transient post-window state; if not, it is time-of-night.

## Could not run / could not verify

- **Re-recording the bar baseline: refused.** `bar_baseline_tonight.sh --check` exited 4 because the live checkout is dirty
  (1 tracked edit + 4 untracked). So coverage stays 8 of 29. `samantha_weekly.sh` would also refuse `--require-bound` for the same
  reason (the service tree has a tracked modification). Fix: the owner cleans or commits `CAPABILITIES.md` and the 4 stray files.
- **pass^k (k=3) and an admissible A/B**: not run. Six bar runs at samples=3 is about 2.2 h of brain slot, against the 90-min budget.
  The k=1 control is all there is, so S8 and S11 cannot be called flaky or regressed from this run.
- **Continuity cells live**: the live adapter is not built. The C1 (7-day carry), C3 and C4 numbers are unmeasured, so they count
  as zero.
- **Warmth probes on real QA**: no new QA transcript exists, so the "after" numbers for the doctrine and the conventions line on the QA
  script are unmeasured. Tonight's numbers come from the synthetic persona transcript only.
- **Day-sim**: refused (a deploy was in flight). It produced no fresh number; the last record is 10-12.
- **Person bench live**: not run (about 40 min of slot).
- **Voice replay / Smart Turn extend-only**: not run tonight. The replay needs the voice lock, and a lander held the box from about
  23:30. Smart Turn is CPU-heavy and was not in the run list.
- **Turn-taking counts** (`turn_taking_metrics.py`): not run. It needs the Pi's `voice.log`, which holds household voice-event text.
  Out of scope here.
