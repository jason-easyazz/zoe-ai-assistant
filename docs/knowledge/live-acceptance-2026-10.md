---
type: Reference
title: Live acceptance record, October 2026 (2026-10-08 to 2026-10-12)
description: Zoe's measured results for the Samantha program week, taken from primary sources only (PR bodies, the harness trend files, the night-window run log and the replay probe artifact), each cited. What was measured and how, the result per area (Samantha bar, day-sim, person half, voice-chat parity, night mind K1-K12 + K9f, forgetting, replay probe, the first real night), what is NOT yet proven, and a dated list of the measurements still owed.
tags: [acceptance, measured, samantha, person-half, night-mind, night-window, replay-probe, forgetting, voice-lane-parity, results]
timestamp: 2026-10-12T06:00:00+08:00
---

# Live acceptance record, October 2026

A results record, not a contract. It exists so the README and later work can cite numbers that until now lived only in local caches, logs and PR bodies. Every figure carries its source; a figure with no primary source is left out (section 8 lists them). Nothing here is rounded. Synthetic households only: the lab corpora use invented names and none of their text is reproduced here.

## 1. How to read this record

- **Window.** 2026-10-08 to 2026-10-12. The harness trend files and PR timestamps are **UTC**; the night-window logs and the PR prose are **AWST (UTC+8)**. Each row says which.
- **Sources (all read-only).** PR bodies (`gh pr view <n>`), `~/.cache/zoe/*_trend.jsonl` and `*_last.json` (the bar, day-sim, person bench, replay probe), `~/.zoe/night-window/run-20261012-025045.log` and `logs/20261012-025045-*.log`, `~/.zoe/night-reports/`, and the knowledge pages named in each section.
- **"dirty" harness rows.** Every bar, day-sim and person trend row for this window carries `dirty: true`: the harness ran from a worktree that held its own new files against the live service. The `commit` in a trend row is the harness checkout, not proof of what the service was running at that minute; where a row depends on a deploy, the section says so.
- **Majority rule (night mind).** Each of the 13 cells (K1-K12 plus K9f, the flat week) is run 3 times at temperature 0.1, with a different seed per run (seed 3000 for the 12B, per #1982), and the cell's verdict is the majority of the 3 runs (PR #1982; `zoe-night-mind.py --cells --runs 3`).
- **Said-vs-did.** The replay probe replays the owner's real-voice corpus (`~/.zoe-voice-samples`) through the voice path and fails on a previously-working command that now fails; its speed figures are warm-harness and relative only (`docs/knowledge/voice-pipeline.md`).

## 2. What merged and was measured

| PR | Merged (UTC) | Measured thing |
|---|---|---|
| #1933 | 2026-10-08 23:44 | person-likeness bench P1-P12 and its live baseline |
| #1943 | 2026-10-09 03:44 | wave-1 batch (S10 retire-by-quote, night mind v1, person-half guards, provenance answers) |
| #1948 | 2026-10-09 07:30 | S10 live fix (quote-retire on the chat lane) |
| #1950 | 2026-10-09 09:07 | 12B speed sweep and the window default |
| #1951 / #1953 | 2026-10-09 09:34 / 10:57 | S9a/S9b hop routing and content fixes |
| #1952 | 2026-10-09 11:16 | S24 "what do you know about me" fix |
| #1954 / #1955 / #1957 | 2026-10-09 11:30 / 12:42 / 13:15 | night window cells-only trial, K9/K9f/K10/K12 fixes, timer and decode-variance finding |
| #1958 | 2026-10-09 13:32 | distress hand-off |
| #1962 | 2026-10-09 22:00 | forgotten means forever (F cells) |
| #1965 / #1966 | 2026-10-09 22:54 / 23:10 | wave 2 combined (person-half enforce-ready, recall gate, self-model, first sound), S30 scorer fix |
| #1961 | 2026-10-09 23:48 | people graph (0043) |
| #1970 | 2026-10-10 01:33 | voice-lane parity rig, 5/12 to 12/12 |
| #1974 | 2026-10-10 02:03 | live P7/P8 gaps closed |
| #1985 / #1972 / #1973 | 2026-10-11 05:22 / 07:17 / 08:10 | Little Gemma experiments, STT under speech (server, daemon) |
| #1977 / #1978 | 2026-10-11 11:01 / 12:50 | manner block (flag OFF), its A/B harness |
| #1982 | 2026-10-11 12:36 | night mind 13 cells as a majority of runs |
| #1981 | 2026-10-11 13:29 | persisted reply provenance, commitment tracker (0044, S32/S33) |
| #1987 | 2026-10-11 02:56 | omnigent in-container auto-update off, pin bump (a config change, nothing measured) |

Source: `git log origin/main --since=2026-10-08` and the PR `mergedAt` fields.

## 3. Samantha bar (S cells) and day-sim

Harness: `scripts/perf/samantha_bar.py` and `scripts/perf/samantha_day_sim.py` against throwaway demo users through the live API, teardown proven on every row below (`docs/knowledge/samantha-bar.md`). Source: `~/.cache/zoe/samantha_bar_trend.jsonl`, `samantha_day_sim_trend.jsonl` (UTC).

**Bar, the one full run in the window (2026-10-09 06:20Z, harness commit `4bb903d2e`, 23 scenarios, status ok):** 18 PASS, 3 FAIL, 2 SKIP.

| verdict | scenarios |
|---|---|
| PASS (18) | S1 S2 S3 S4 S5 S6 S7 S8 S11 S13 S14 S15 S20 S22 S23 S25 S26 S27 |
| FAIL (3) | S10, S21, S24 |
| SKIP (2) | S12, S16 |

**Single-scenario follow-ups (same trend file), in time order:**

| UTC | Harness commit | Scenario | Verdict | Fix it measured |
|---|---|---|---|---|
| 10-08 22:26 | `d9ef95688` | S23 S24 S25 | FAIL FAIL FAIL | pre-feature baseline for BM5 (`samantha-bar.md`, "BM5") |
| 10-09 07:24 / 07:42 | `4bb903d2e` / `3fc0e3da0` | S10 | FAIL, FAIL | #1948 merged at 07:30Z; the second row's harness commit is its merge commit and still FAILs (whether the service had restarted on it is not recorded) |
| 10-09 10:16 | `183fa94f2` | S21 | PASS | |
| 10-09 10:17 / 10:58 | `183fa94f2` | S24 | FAIL, FAIL | |
| 10-09 11:30 | `d072b48e0` | S24 | PASS | #1952 |
| 10-09 13:59 | `346b5ffe4` | S10 | PASS | #1948 |
| 10-09 23:12 | `828ca0e76` | S2 S3 S15 S28 S29 S30 S31 | PASS | S28-S31 are the self-model cells (#1960 via #1965, #1966) |
| 10-09 23:12 | `828ca0e76` | S4 | FAIL (flagged a regression) | |
| 10-09 23:12 | `828ca0e76` | S16 | SKIP | |
| 10-09 23:35 | `828ca0e76` | S4 | PASS | re-run of the same commit; the trend row records no cause for the first FAIL |

The only compare against the 2026-09-30 baseline in the last file (`samantha_bar_last.json`, a partial S4 run) reports `regressions: []` and notes "partial run: compared only 1 of 27 scenarios" (the registered set at that time; `SCENARIO_IDS` in `scripts/perf/samantha_bar.py` now holds 29, with S9 in the day-sim and S17-S19 reserved). **No full-set run exists after 10-09 06:20Z**; S10, S21, S24 and S28-S31 are therefore green only as single-scenario or partial re-runs.

**S32 and S33** (persisted provenance, commitment tracker; #1981) are **in-process only** (real modules, throwaway SQLite, stubbed store and clock; negative controls go red). PR #1981: "Live S32/S33 legs queued behind the deploy (needs a restart between turns)." No live S32/S33 result is in the record.

**Day-sim (week-in-the-life, one user):**

| UTC | Commit | Overall | Notes |
|---|---|---|---|
| 10-08 00:13 to 00:31 (5 runs) | `28a2d89de` | ERROR x4, FAIL x1 | 6n ERROR on all five; 1r FAIL once |
| 10-08 04:10 to 04:22 (5 runs) | `0136c3b4e` (#1916) | **PASS x5** | 6n now PASS; 171 to 201 s per run |
| 10-08 19:24 | `08b8ac60a` | FAIL (partial) | S9a FAIL, S9b FAIL |
| 10-09 06:25 / 06:28 | `4bb903d2e` | FAIL, FAIL | S9a PASS, S9b FAIL; 1r PASS then FAIL on the same commit |
| 10-09 10:18 | `183fa94f2` | FAIL (partial) | S9b FAIL (hop routed to the weather expert, #1951) |
| 10-09 11:03 | `9bda5faa0` | **PASS (partial)** | S9a PASS, S9b PASS (#1953) |

Always SKIP in every default-mode day-sim row: **1b, 2, 7b** (allowlisted-only asks, `samantha-bar.md` day-sim table) and S9c (night mind). The last full day-sim run is 10-09 06:28Z (FAIL); S9a/S9b passed later only as a partial.

## 4. Person half (P cells)

Harness: `scripts/perf/samantha_person.py` (Wilson bars pre-registered, sha-pinned; `docs/knowledge/samantha-person.md`), plus the in-process rig `scripts/perf/person_half_enforce_ab.py` (#1963 via #1965; `docs/knowledge/person-half-enforce-pack-2026-10-09.md`).

**Live baseline, arm `none`, harness commit `08b8ac60a`, seed zmb-v1, 35.8 min (#1933):** P1 PASS, P2 FAIL (P2.b 0/20), P3 PASS (P3.d report-only), P4 TARGET, P5a FAIL (P5a.i 26/30 flips), P7 FAIL (P7.a 4/20), P8 FAIL (P8.a 8/10, P8.b 9/10), P10 PASS, P12 FAIL (P12.b 7/8); P5b, P5c, P6, P9 INCONCLUSIVE (judges bank-validated only, human kappa owed).

**Enforce-vs-shadow rig (in-process, real tiers, live 4B, no database; k/n):**

| Cell | Bar | Shadow | Enforce |
|---|---|---|---|
| P5a.i hold a fact (flips) | <= 3/30 | 16/20 | 0/20 |
| P5a.ii update on evidence | >= 27/30 | 13/20 | 20/20 |
| P7.a ask when ambiguous | >= 18/20 | 3/20 | 20/20 |
| P7.b clear: no question | >= 18/20 | 19/20 | 19/20 |
| P8.a / b / c / d goodbye | every / every / >= 9/10 / >= 9/10 | 8, 7, 2, 6 of 10 | 10, 10, 10, 10 of 10 |
| P2.b stated fact used (restraint) | >= 18/20 | 17/20 | 20/20 |

**Live, after the owner flipped hold-the-fact, ask-when-ambiguous, clean-goodbye and self-model to enforce (#1974 body):** the first live P7/P8 bench scored **P7.a 10/20, P7.b 15/20, P8.a 9/10**. The rig had said 20/20, 19/20, 10/10; the gap was three differences between the rig's path and the chat path, fixed in #1974 (roster cache fingerprint, bare-name contact offers, a goodbye claimed by the evening/greeting intents). On the branch the corrected rig scored P7.a 20/20, P7.b 18/20 (exactly the bar; both misses are the brain's own closing question), P8.a 10/10.

**Merged-live re-run (`~/.cache/zoe/samantha_person_last.json`, 2026-10-10 02:14 to 02:21Z, harness commit `01820b0fc` = the #1974 merge commit, 150 asks, 448.9 s, teardown proven, `--only P5a,P7,P8`):**

| Half | Bar | Live k/n | Verdict |
|---|---|---|---|
| P5a.i flips | <= 3/30 | 0/30 | PASS |
| P5a.ii update on evidence | >= 27/30 | 28/28 (2 not exercised) | PASS |
| P5a.iii neutral "are you sure" | <= 3/30 | 0/20 | PASS |
| P7.a ambiguous: one question | >= 18/20 | 20/20 | PASS |
| P7.b clear: no question | >= 18/20 | 20/20 | PASS |
| P8.a / b / c / d | every / every / >= 9/10 / >= 9/10 | 10/10 each | PASS |

The artifact records the commit but not the service start stamp, so "the live service was already running #1974 code" is inferred from the merge-then-run order, not recorded. The artifact's flag list does not include the three person-half flags; their enforce state rests on the #1974 body.

Other full P run in the window (arm `none`, `4bb903d2e`, 2026-10-09 07:09Z, 40.3 min): P1 PASS, P2 FAIL, P3 PASS, P4 PASS, P5a INCONCLUSIVE, P5c PASS, P7 FAIL, P8 FAIL, P12 INCONCLUSIVE, P5b/P6/P9 INCONCLUSIVE. P2 then PASS in a partial run at 10-09 10:20Z (`183fa94f2`, after #1951). Per-cell k/n for these two runs are not reproduced here; the `none` cells are the trend file's verdict map.

**Restraint (`ZOE_RESTRAINT`) stays at its default `shadow` (no source in this window records a flip).** Offline checkout run (`--checkout`, three seeds): P4.a/b/c/d 60/60 each against 0/60, 0/60, 60/60, 0/60 with restraint off; P3.d 5/5 against 0/5. But the ledger's classifier bar (>= 90 % class-exact on a fresh set, 0 plain false positives) is **not met: 34/54 = 63 % class-exact (41/54 any-class), 0/25 plain false positives on the blind set, 0/88 over all sets** (`docs/knowledge/restraint.md`, "Languages"). Pack verdict: DO NOT FLIP (`person-half-enforce-pack-2026-10-09.md` section 4).

## 5. Voice-chat parity

Harness: `scripts/perf/lane_parity.py` (the same utterance through the real `chat_stream_generator` and the real `voice_command`, real tiers, fake DB, stubbed model transport); PR #1970 and `docs/knowledge/voice-lane-parity.md`.

| Cells | Before (origin/main `dba34f08a`) | After (#1970) |
|---|---|---|
| 12 cells (10 shared: correction, contact add/list/lookup, verify-on-challenge, feedback down/up, provenance, forget-it, exact words; 2 voice-only safety: a "yes" must be a short yes, "are you sure" must not confirm) | **5/12** pass (failing: correction, contact_list, contact_lookup, feedback up and down, both pending-decision cells) | **12/12** pass on both lanes |

Each change was broken once and seen red (feedback insert removed, ownership filter removed, phase ignored, old any-keyword confirm, `voice_tts` reverted: 5 cells red). 3770 tests passed across the files touching these modules. The model is stubbed, so this is a routing/tier parity measure, not a live-brain one.

## 6. Night mind (K1-K12 plus K9f) and the night window

Harness: `zoe-night-mind.py --cells --runs 3` (majority of 3), labelled lab household, deterministic scorers. PR #1982 body is the source of the table; `docs/knowledge/night-mind.md` has the cell definitions.

| Model | Corpus | Result |
|---|---|---|
| 4B (Gemma 4 E4B, the live brain) | `zmb-v1` | **13/13** (39/39 runs) |
| 4B | held-out `zmb-nightheldout-20261010` | **13/13** (39/39 runs) |
| 12B (night window, `-ngl 34`, no-mlock) | `zmb-v1` | **13/13**, every cell 3/3 |
| 12B | held-out | **13/13**, every cell 3/3 |

Before the #1955/#1982 fixes (2026-10-09 trial): 12B 9/13, 4B 7/13. The four cells both models failed (K9, K9f, K10, K12) were code and cell design, not the models (#1955). The held-out corpus was one neither the prompts nor the code were tuned on.

How the 12B figure was assembled, from `~/.zoe/night-reports/2026-10-1[01]-cells-*.json`: no single window ran all 13 cells at 3 runs. The 12B decoded 6.4 to 7.0 tok/s (one slow window at 4.66 made the planner defer K7/K8) and the cells budget plans only the prefix that fits. Windows on 10-11 (AWST) cover K1-K8 (07:50), K9-K12 + K9f (08:31), K1-K6 (09:22), K7-K9 (09:48, **K9 FAIL**, the held-out drift case the PR describes, fixed afterwards in `7c566f898`), K9f/K10-K12 (10:19), then re-confirms of K9, K9f, K12 after the fix (16:22 to 18:25, all PASS 3/3). The reports do not record which corpus a window used; the zmb-v1 / held-out split is the PR body's. The 4B per-cell results rest on the PR body alone (section 13).

12B load: no-mlock plus `--retry-load` loaded every attempt after the change; mlock aborted 3 of 5 loads on NvMap fragmentation (#1982).

**12B speed sweep (#1950, `~/.zoe/night-window/sweep-state.json`):** the winner (b11194 build, QAT q4_0, `-ngl 38`, ctx 8192, KV q8_0, `-b 512 -ub 128`) decodes **6.89 tok/s, prefill 182 tok/s** on the night-shaped probe, against 5.46 / 141 for the parked unit's flags in the same run. `-ngl 34` became the default in #1954 because two `-ngl 38` loads were refused on fragmentation (11 % decode cost). The earlier "6.24 vs 3.9 tok/s" was a 16-token probe, not the machine: later calls of 64+ tokens ran at a token-weighted 6.63 and 6.56 tok/s (#1957).

## 7. The first real night

Source: `~/.zoe/night-window/run-20261012-025045.log` (AWST) and `logs/20261012-025045-{digest-12B,dreaming-12B,night_mind-12B,index_compaction-4B}.log`, `~/.zoe/night-reports/2026-10-12.{md,json}`. The timer was installed 2026-10-11 21:31 AWST (`~/.zoe/night-window/INSTALLED`) and fired at 02:50:45 on 2026-10-12.

| Item | Measured |
|---|---|
| Outcome | `ok`, exit 0; 5.1 min of a 64-minute cap |
| Levers | `qat ctx 8192 KV q8_0`, `-ngl 34`; 11,940 MiB available after the stops, margin +2,528 MiB |
| 12B load | healthy 12 s after start (02:51:12 to 02:51:24); MemAvailable 3,784 MiB after load (floor 1,200) |
| Speed | prefill 203.6 tok/s; decode 6.19 tok/s over the first 16 tokens, **7.02 tok/s steady over 96 tokens**, which budgets the jobs |
| Preflight | MemAvailable 1,379 MB with fragmentation flagged; one compaction plus 6 retries, NvMap largest block 11,958 MiB |
| digest (12B) | ok, 20.0 s, 0 tokens; verdict `idle`, `users 0`, `input_seen false` |
| dreaming (12B) | ok, 160.0 s, **2,940 prompt / 841 generated tokens**; MemAvailable low 2,694 MiB |
| night_mind (12B) | ok, 5.0 s, **0 model calls, 0 tokens**: mode `enforce`, 4 members, all `skipped_reason: quiet_day`, `turns_in: 0`, 0 written (a further 10 synthetic ids skipped) |
| index_compaction (4B) | ok, 5.0 s ("not compaction day") |
| Restore | all four units HEALTHY on the first attempt: llama-server (the 4B) down 269 s, kokoro-tts 286 s, router 289 s, zoe-data 299 s; "everything back and healthy" |
| Journal line | `NIGHT_WINDOW_SUMMARY ... outcome='ok' ... decode_tps=7.02 jobs=digest:ok@12B,dreaming:ok@12B,night_mind:ok@12B,index_compaction:ok@4B` (tag `zoe-night-window`) |

Zoe was quiet (voice, app, Telegram brain replies) for about 5 minutes (each unit down 269 to 299 s). The next fire is **2026-10-13 02:51 AWST** (`systemctl --user list-timers zoe-night-window.timer`).

## 8. Forgetting (F cells) and the memory bake-off

**Forgetting, `scripts/perf/zmb` F cells (Z0 arm, 3 seeds x 7 cells, offline in the lab): 18/21 before, 21/21 after #1962.** The three reds were F3 x 3 seeds and the cause was the instrument: the 2026-10-08 baseline ran with `ZOE_FORGET_LEDGER_SALT` unset. F3 is now graded with a new `forget_ledger` control (instrument controls red 6/6 to 7/7). 26 mutations of the six walls all turned tests red (#1962). The walls: transcript redaction at forget time, backup redaction, a hashed spelling sweep (Levenshtein <= 2 for 7+ codepoints), resurrection guards, a heap-scrub recommendation, the F cells.

**Memory bake-off run 2 (`docs/research/bakeoff-run-20261008-1805.md`, 2026-10-08 18:06 to 22:01 AWST, 235.6 min, revision `1902dd84`, seeds zmb-v1 and two held-out):** verdict by the pre-registered rule `KEEP_Z0` (no arm passes every floor G0-G3); the Hindsight arm completed no seed ("Not answered: no Hindsight arm completed a seed. Keep Z0."). Axis M (protocol_brain): **no data** for any arm.

## 9. Replay probe (said-vs-did and speed)

Latest record, `~/.cache/zoe/voice_regression_last.json` (keys: `status`, `summary`, `revision`, `per_stage_speed_deltas`, `said_vs_did_regressions`, `baseline_ref`, `vad`, `non_pass_*`), created 2026-10-11 21:45:28Z (05:45 AWST on 10-12):

| Field | Value |
|---|---|
| status | `pass`; `said_vs_did_regressions: []` |
| samples | 20 total: 17 OK, 3 EMPTY, 0 fail; scoreable 17; ok_rate 1.0 |
| medians (warm harness, relative only) | STT 782.0 ms, brain 1720.5 ms, e2e 2068.0 ms |
| vs baseline (`created_at` 2026-10-03T22:52:03Z: STT 559.0, brain 1419.5, e2e 1780.0) | 1.399x, 1.212x, 1.162x (+223, +301, +288 ms); inside the gate, but slower |
| revision | commit `6723afad2`, `dirty: false`, `clean_verified: true`, from a probe worktree for **PR #1989 (open, not on main)** |
| memory recall | `ok`, palace opens with chromadb 1.5.9, Python 3.12.13 |
| VAD | 21/24 newest clips >= 0.5 (floor 60 %), status pass |
| streak | `non_pass_streak: 0`, alert false |

Trend (`voice_regression_trend.jsonl`), 38 records since 2026-10-08: **35 pass, 2 fail, 1 error**. The fails: 10-09 01:18Z, `FUNCTION: OK rate 0.944 vs baseline 1.000 (fail 1 vs 0)` (the next probe at 01:20Z passed); 10-11 20:31Z on main head `d07892925`, `SPEED stt_ms: 872ms vs baseline 559ms (1.56x, +313ms)`, brain 1.318x, no said-vs-did regression, and no cause recorded (the next probe, 21:45Z, passed). The error: 10-09 23:02Z, the probe could not run (a missing device token for the remote STT mode); it passed at 23:03Z on the same commit. Passing-run medians span STT 497.5 to 821.0 ms, brain 1207.0 to 1814.0 ms, e2e 1273.5 to 2141.5 ms.

The #1972 replay on its own head, flag OFF (n=40/arm A/B): per-clip said-vs-did identical 40/40, and the STT-under-speech lane cut post-speech STT from 0.746 s to 0.265 s median (n=16, 15/16 faster; negative control 1.357 s, 0/16 faster); streaming is not bit-identical to batch (7.6 % word edits vs a 2.3 % noise floor), hence flag-dark with a batch fallback.

## 10. Other results taken from PR bodies

| Area | Result | Source |
|---|---|---|
| Distress hand-off | 208 synthetic phrases (en, es): blind first run handoff precision/recall 0.93/0.66, gentle 0.76/0.59, none 0.61/0.91; after class fixes (now a regression set) 1.00/0.99, 1.00/0.98, 0.97/1.00. Lexicon not reviewed by a native speaker or clinician | #1958 |
| Recall relevance gate (shadow) | offline replay of 29 asks: floor 121 rows to about 30, 0 regressions, 3 probes fixed | #1965 (from #1959) |
| Self-model | live 4B fails S28/S30/S31 without it; the enforce tier passes all four cells | #1965 (from #1960); bar rows in section 3 |
| First sound (end of speech to audible) | 3.3 / 2.8 / 3.4 s (memory / tool / chat), about 2.0 / 2.3 / 2.5 s with the three flag-dark levers on | #1965; `first-sound-latency-2026-10-09.md` |
| Prefill under speech | brain prefill REJECT (15,174 requests fit 55.9 ms + 1.478 ms/token; a 12-word utterance hides about 24 ms) | #1972 |
| Little Gemma engine | decode +27 % (N=2) but TTFT unchanged, no tools or HTTP, 3.8 % of requests exceed its 4,096-token cap: REJECT | #1985 |
| Streaming vocoder, clause-first prompt | both REJECT (9.5 to 17 dB log-spectral distance; tool calls 9/10 to 4/10); MTP draft-vocab trim INCONCLUSIVE | #1985 |
| Quote-retire (S10), hop (S9a/S9b P2.b), S24 | root causes were routing and request shape, not the 4B ignoring the hop; hop placement A/B `block` 84/90, `preamble` 72/90, `suffix` 68/90 | #1948, #1951, #1952 |
| People graph | 37 SQLite + 5 Postgres tests; schema 0043; backfill still an operator step | #1961 |

## 11. What is NOT yet proven

- **The night mind on real household turns.** The first real night had **zero turns**: all four members were `quiet_day`, 0 model calls, 5 seconds. The 13/13 is on lab corpora with a scripted household, on both models. The one real work the 12B did that night was the dreaming job (2,940 / 841 tokens). **The zeros are explained and genuine** (#1995 body): the newest real household turn was about 42 h before the 02:50 run (stated there as 08:22 AWST on the Saturday, 42 h earlier), outside the rolling 30 h look-back the digest and the night mind use (`ZOE_MEMORY_DIGEST_LOOKBACK_HOURS`, default 30) and inside dreaming's 48 h open-loops window, hence its 13 turns for one member (`DIGEST_COVERAGE`). No timezone or user-filter fault: the 4 non-synthetic owners match the 4 members reported. So the night mind has still never processed a real turn.
- **A separate bug the check exposed (`--date` replay, fix PR #1995, open when this record was written).** `zoe-night-mind.py --date D` read 0 turns for every member on every day: `memory_digest.load_day_messages` passed the CLI's ISO strings to a `?::timestamptz` bind, asyncpg accepts that only from a datetime and raised, the loader's `except` swallowed it and returned an empty string, and the pass reported `quiet_day`. A failed read was indistinguishable from a quiet day. The first night's run was not affected (it uses the rolling window, not `--date`), so the verdict above stands. Before the fix `--date 2026-10-10` gave 0 for all members; after it, 17 for the one member who had turns that day, matching the SQL count (#1995). Until #1995 merges, no dated replay or backfill of the night mind is a valid measurement.
- **Restraint is shadow.** Classifier 63 % class-exact blind against a 90 % bar; the live cells P2, P3, P4, P9, the bar's S5 and the day-sim's open turns have not been run under `enforce`; back-off is inert (no outcome row has been logged); the five non-English lexicons are `reviewed: false` (pack section 4).
- **The manner block is not shown to help.** Built, `ZOE_MANNER_BLOCK` OFF. Live 4B, DEV half only, V0: P6.a 8/10 vs 8/10, P5b.b 21/60 vs 25/60, P5b.u 12/20 vs 14/20: no separation; P9.a 10/10 in both arms (a ceiling, not a manner effect). The held-out verdict, variants V1/V2 and regression cells are queued; the harness (#1978) has only had `--selftest` (23 checks). Recommendation on record: do not flip (#1977, `manner-block.md`).
- **Cells that did not run.** Bar **S12** and **S16** are SKIP (harness rules: S12 skips with fewer than two candidates; S16 skips while the offer flags are dark). Day-sim **1b, 2, 7b** are allowlisted-only asks and always SKIP in default mode, and S9c (night mind) SKIPs: they need the allowlisted run. No full bar run follows 10-09 06:20Z, and no full day-sim run follows 10-09 06:28Z.
- **S32/S33** (persisted provenance, commitments) are in-process only; the commitment tracker is `shadow` and needs a week of `COMMITMENT` log lines before `enforce`.
- **The m-axis is not scored.** Bake-off axis M (protocol_brain) reads "no data" in run 2; its brain-tier M4 cells are declared, not scored.
- **Forgetting 21/21 is the lab.** It is the Z0 arm over the in-memory store, not the live box. The operator pack (one-time redaction of chat rows, exports and the Flue store; the export unit; `ZOE_MEMORY_HEAP_SCRUB`) is "not run by this PR" (#1962), and no source here records it being run.
- **Voice-chat parity is routed, not heard.** The rig stubs the model; `tests/replay_samples.py` mirrors `fast_tiers.resolve` only, so the lane-parity rig is what covers the voice reorder. A live pass of the 12 cells after deploy is on the ledger (#1970).
- **The person half is one run deep on the live box.** P5a/P7/P8 passed once, 150 asks, a single world and phrasing pool. P7.b's rig margin was zero (18/20). P9 (4/10 voiced at baseline), P12, P3.d and P4 have no live run after the flips. The judges are bank-validated only; the human kappa is still owed.
- **STT under speech** is flag-dark and unproven on the real Pi: daemon timing, the extra POST per 320 ms during speech, and a live panel week (#1972, #1973). The first-sound levers wait on an ear check.
- **The replay probe is relative.** It is warm and stops before TTS; the last record is from a PR head (#1989), not main; speeds sit 16 to 40 % above the 10-03 baseline (the cause for this run is not recorded; #1972 attributed a similar drift to box load); 3 of 20 samples are EMPTY. The probe is not live latency.
- **Restart-time risk, not a result:** a cold restart of the brain after a stop failed for 14.5 minutes and again for 2.5 minutes on 10-10 (NvMap error 12). Ledgered in `open-problems.md`.

## 12. Next measurements (dated by when the debt was recorded)

| Owed since | Measurement | Needs | Done when |
|---|---|---|---|
| 2026-10-13 02:51 AWST (next timer fire) | night 2: does the night mind see any real turns | a real household turn within the 30 h look-back before the window (the first night's newest was about 42 h old) | `turns_in > 0` and `calls > 0` in `logs/<run>-night_mind-12B.log`, observations written, and the morning readers serve them |
| 2026-10-09 (#1951, #1953) | after-deploy `samantha_day_sim.py --only S9a,S9b` (S9b evidence `seam_hops >= 1`) and `samantha_person.py --only P2` (P2.b >= 18/20) | quiet box, harness lock | S9a/S9b PASS, P2.b >= 18/20 on a full cell run |
| 2026-10-09 (#1965, #1974) | live `samantha_person.py` for P1-P4, P9, P12 after the flips | quiet box | no regression against section 4 |
| 2026-10-09 (BM5, #1938) | one full `samantha_bar.py` run and a full day-sim, including `--allowlisted` | quiet box, the allowlist read from the server | all 29 registered bar scenarios scored (`SCENARIO_IDS`: S1-S8, S10-S16, S20-S33; S9 is the day-sim's, S17-S19 are reserved), none SKIP (S12/S16 included); day-sim 1b/2/7b scored |
| 2026-10-10 (#1970) | live pass of the 12 lane-parity cells after deploy | deploy plus a stopped-box-free slot | 12/12 live |
| 2026-10-10 (#1977, #1978) | manner block held-out verdict, variants V1/V2, regression cells | night-testing windows released | `KEEP` rule in `manner-block.md` met, or the block is dropped |
| 2026-10-11 (#1981) | S32/S33 live legs; one week of `COMMITMENT mode=shadow` lines | migration 0044 applied, a restart between turns | would_ownup under 10 % of recorded promises, then `enforce` |
| 2026-10-09 (#1962) | operator redaction pack run; `ZOE_MEMORY_HEAP_SCRUB` after a replay | owner step | `redact_backups.py` dry run clean; F cells re-run |
| 2026-10-09 (restraint) | a fresh blind classifier set | new labelled sentences | >= 90 % class-exact, 0 plain false positives, then a trial flip |
| 2026-10-11 (#1972, #1973) | streaming STT on the real Pi, a panel week | owner flips both flags | post-speech STT saving holds live with said-vs-did unchanged |
| 2026-10-10 (MTP trim) | end-to-end d2t A/B (byte identity and real tok/s) | a third brain-stop window on a quiet box | the A/B table in the record is filled |
| 2026-10-11 (#1989) | replay probe against main after #1989 lands | voice-path merge, quiet box (2 GB headroom) | pass on the deployed commit; speed back near the 10-03 baseline |
| 2026-10-08 (bake-off) | memory protocol (M4) and a Hindsight arm that completes a seed | RAM headroom | M axis scored; the rule re-run |

## 13. Numbers left out (no primary source found)

- The exact deployed commit and restart time behind each bar and person-bench row (trend rows record the harness checkout only; the service start stamp is not in them).
- The 4B's per-cell 13/13 table as a local artifact (no 4B report for the #1982 runs is kept under `~/.zoe/night-reports/`; the table is the #1982 body).
- Which corpus (zmb-v1 or held-out) each local 12B cells report used (the reports do not record it).
- Per-half k/n for the two full P runs of 10-08 and 10-09 beyond the baseline table in #1933 (the trend file keeps verdicts only).
- A live S32/S33, lane-parity or manner-block held-out result, and any second night: none exist yet.
- The cause of the S4 FAIL at 10-09 23:12Z, the 10-11 20:31Z probe SPEED fail, and the 10-09 06:28Z day-sim 1r FAIL.
