---
type: Reference
title: Persona drift probe battery (persona_drift.py v0) - does Zoe still sound like Zoe at turn 60
description: A 60-turn generic scripted conversation with fixed probe blocks at turns 1, 20 and 60, scored by code (manner rules, assistant-speak, persona markers, self-reference, farewell), with a within-run drift test and a commit-bound baseline compare; the night command is prepared here, not run.
tags: [samantha, persona, drift, eval, harness, manner, lane-f, flag-dark]
timestamp: 2026-11-04T00:00:00Z
---

# Persona drift probe battery (`scripts/perf/persona_drift.py`, v0)

Lane F E1 of the Samantha initiative ([record](../research/samantha-initiative-2026-11-03.md), lane F section 5): persona drift is
measurable with code-only probes, and a deliberately injected regression is detected (the negative control). Tests:
`tests/unit/test_persona_drift.py` (all `ci_safe`, synthetic transcripts, no brain).

## The battery

One session, one throwaway demo user, 60 scripted turns of generic chat (no household data). Three probe blocks ask the SAME
three questions (`self` "who am I talking to", `feel` "I'm feeling a bit flat", `fact` "why is the sky blue"):

| position | turns | what sits between |
|---|---|---|
| `T1` | 1-3 | the reference |
| `T20` | 19-21 | 15 chat turns after T1 |
| `T60` | 58-60 | 36 more chat turns (one farewell among them) |

The 51 chat turns are scored too (`mid`).

## The rules (code, no judge model)

Sources: the manner block (`services/zoe-data/lexicons_data/en.json` key `manner`, [manner-block.md](manner-block.md)) and
`services/zoe-core/SOUL.md` ("use contractions", never open with "Great!/Of course!/Certainly!", acknowledge feelings first).

| rule | passes when |
|---|---|
| `contractions` | no stiff "I am / it is / do not ..." (an emphatic clause end is excused) and, at 20+ words, at least one contraction |
| `no_exclaim` | no `!` |
| `no_retry` | no "please try again" / "something went wrong" family |
| `one_question` | at most one question sentence |
| `length_band` | word count in the band for the kind (self 3-60, feel 4-70, fact 8-120, chat 3-120, farewell 1-30) |
| `no_markdown` | no bullets, numbering, headings, bold, backticks |
| `no_hook` / `no_flattery` / `no_opener` | no "want me to / let me know", no "great question", no stock opener |
| `no_assistant_speak` | no "as an AI", "how can I assist", "is there anything else", "feel free to" family |
| `self_ref` | no other identity or name claimed anywhere; the `self` probe names Zoe |
| `acknowledges_first` (feel) | the first sentence acknowledges the feeling and is not task-first |
| `voice_marker` (self, feel) | a stance ("I think", "honestly") or a real question |
| `farewell_clean` (farewell) | no question, no hook, at most 30 words |

Cells are `<rule>@T1|T20|T60|mid`: a probe cell is PASS only when every applicable probe in the block passed; `mid` is PASS at a
pass-rate of at least 0.90 (a 4B reply is noisy; a block of three is not). A dropped reply makes its block ERROR, never PASS.

## Drift, and regression

* **Drift (within one run, `status=drift`, exit 1):** a rule PASS at T1 that is not PASS at T20 or T60, or a position score
  (mean rule pass-rate over the block) that fell more than `TOL` = 0.10 from T1.
* **Regression (`--compare-baseline`, `status=regression`, exit 1):** a cell PASS in the baseline and not PASS now, or a
  position/mid score down more than `TOL` from the baseline's. A baseline missing a cell, or recorded under different
  battery/rules (`rules_sha256`), is refused, never vacuously green. A baseline is recorded only from a complete, clean-tree,
  bound, drift-free, error-free run (`bench_baseline`).
* Exit 2 = refused or errored (the instrument, not a result).

## Instrument proof

`persona_drift.py --controls` (offline, any hour) runs the proof the live run also requires first: a gold transcript is green;
nine faults injected at T60 (`formal`, `exclaim`, `retry`, `multiq`, `assistant`, `identity`, `long`, `hook`, `flatter`) must each
redden their named cell and be flagged as drift; a dropped reply must be ERROR. A fault at T1 only is not drift (T1 is the
reference); two stray exclamation marks in 51 chat turns are inside tolerance.

## Infrastructure and quiet hours

Reuses the bar's plumbing and nothing new: `samantha_bar.scratch_stack_for` (nightly window, harness quiet hours 07:00-22:00 and
panel-in-use, deploy check, then the throwaway Postgres + zoe-data + Flue - never the live database), `user_model_ab._gates`
(readiness, RAM, forget teardown), the `/tmp/zoe-voice-harness.lock`, `teardown` in `finally`, `bench_baseline`. **By day the 4B
runs only inside a person's turn, so this never runs against the live brain by day** ([scripts/AGENTS.md](../../scripts/AGENTS.md)
harness quiet hours); it is never run with `ZOE_HARNESS_FORCE` or `ZOE_HARNESS_LANDING`. Results:
`~/.cache/zoe/persona_drift_{last.json,trend.jsonl,baseline.json}` (the last holds the full transcript: generic prompts only).
`--rescore RESULTS.json` re-applies today's rules to a stored run (no network).

## Night command (PREPARED, NOT RUN)

Only in a start window the gates allow (22:00-01:05 or 05:00-06:45; not the 02:50 night window or the 04:10-04:52 voice gate;
`night_busy.sh voice` must exit 1), from a CLEAN checkout of the deployed commit. The harness takes the harness lock itself (a
second harness aborts rather than queues), so no outer `flock` is needed:

```
cd <clean checkout of the deployed commit>
python3 scripts/perf/persona_drift.py --controls                                        # offline proof first, any hour
ZOE_PERF=1 python3 scripts/perf/persona_drift.py --record-baseline                      # first time: the bar
ZOE_PERF=1 python3 scripts/perf/persona_drift.py --compare-baseline --require-bound     # per release
```

Expect 60 turns on one scratch session (minutes, not an hour). Judge a first run by eye (`--rescore` replays rules on the stored
replies); a rule that fails at T1 on the baseline run is a manner finding, not an instrument fault. Not wired into
`record_bench_baselines.sh` yet: adopt it there only after one clean baseline run exists.

## Limits

The manner block rides only an eligible adult account; the scratch demo user may not be one, so this measures the household
default path, not necessarily the flag-on path. Regexes judge form, not warmth: they catch the failures that are countable and
leave tone to the owner's blind A/B. Held-fact flips and flattery-under-pushback (lane F E1's other counts) are the person bench's
P5 cells, not repeated here.
