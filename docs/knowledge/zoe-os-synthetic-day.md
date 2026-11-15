---
type: Runbook
title: Zoe OS synthetic day - scripted demo-user traffic so a shadowed verb reaches its judged count in a day
description: A seeded, repeatable script of list add, list remove, reminder create and alarm create requests driven through the chat API by demo users, one at a time behind safety guards, then erased; and the report that joins it with the shadow log and judges each verb against the owner's pass mark.
tags: [zoe-os, shadow, harness, demo-users, runbook]
timestamp: 2026-11-15T00:00:00Z
---

# Zoe OS synthetic day

**What it is.** The owner's pass mark for moving a verb to the kernel (>= 200 compared actions, >= 99.5% agreement, every disagreement
explained, kernel unavailable < 1%, hop p90 < 10 ms, a forget and a member delete both reaching the kernel) would take weeks of real
traffic. `scripts/maintenance/os_synthetic_day.py` replays a deterministic plan (seed 20261115, about 1,900 requests: ~530 list add, ~300 list
remove, ~340 reminder, ~300 alarm, 400 small talk, guest refusals) from three demo users (two adults, a child) and one guest, so each verb
reaches at least 250 expected judged acts in roughly 4-5 hours at the default pace. Text comes only from templates and an invented vocabulary
(`ADJ`, `NOUN`, `LISTS`, ...); nothing real. The plan includes planned `not_expressible` requests (recurring or date-only reminders, far-out
reminders, "in the morning" and repeating alarms): the kernel is EXPECTED to decline those, and the report treats that many as explained.

**Before the shadow can see demo users.** Shadow proposes only for members in the kernel roster (`people.json`; `os_shadow.roster()`), so demo
ids must be in it or every action is `member_not_in_roster` (not compared). `prepare` mints the ids and prints the roster command; the roster
is read at kernel start, so restart the kernel unit after writing it. `run` refuses to start if the ids are missing (`--skip-roster-check`
overrides). Re-write the roster WITHOUT the demo ids afterwards (the next `prepare` mints fresh ones).

## Commands (repo root; token from `ZOE_INTERNAL_TOKEN` in the environment or `--token-file`, never printed)

```bash
# 1. dry run: plan summary only, sends nothing, no credentials needed
python3 scripts/maintenance/os_synthetic_day.py run --dry-run
# 2. mint demo ids, add them to the roster, restart the kernel unit
python3 scripts/maintenance/os_synthetic_day.py prepare --state /tmp/osd.state.json
# 3. 20-request smoke run (guards on, cleanup at the end), then read the report
python3 scripts/maintenance/os_synthetic_day.py run --state /tmp/osd.state.json --out /tmp/osd.results.jsonl --max-count 20 --pace 5
# 4. the full day: a SEPARATE state and results file (step 3 erased its demo users and advanced its checkpoint), so prepare + roster again
python3 scripts/maintenance/os_synthetic_day.py prepare --state /tmp/osd-full.state.json
python3 scripts/maintenance/os_synthetic_day.py run --state /tmp/osd-full.state.json --out /tmp/osd-full.results.jsonl --pace 5 --hard-stop 21600
# 5. the verdict
python3 scripts/maintenance/os_synthetic_day_report.py --results /tmp/osd-full.results.jsonl --log ~/.zoe-logs/zoe-data.app.log [--explained notes.json]
```

`--cleanup never` keeps the demo users between chunks (resume with the same `--state`; the final chunk must use `always`, or run the `cleanup`
subcommand). A run that stops at `--hard-stop`, `--max-count` or an exception still runs cleanup unless `never`.

## The guards (checked before every batch of `--batch` requests, default 5; a failed guard pauses and re-checks every `--poll` seconds)

| guard | clear when |
|---|---|
| night | `scripts/landing/night_busy.sh test` exits 1 (0 = pause; a missing or crashing script counts as busy) |
| memory | `MemAvailable` >= `--mem-floor-mb` (default 1200) |
| voice lock | nobody holds `/tmp/zoe-voice-harness.lock` (tried, never kept) |
| household | no log line in the last `--talk-minutes` (10) whose logger is in `--talk-loggers` (default `routers.voice_tts`); reads timestamp and logger name only, never a message body |

Requests go strictly one at a time (the brain has one slot), `--pace` seconds apart. The household guard sees panel voice turns; a household
member typing in web chat is not detected: run in a quiet window.

## Cleanup and what remains

At the end (always, also after an exception) the driver sends one deliberate "forget that" as a demo adult (after a marker add if none was
sent), then for each demo user: deletes the lists, alarms and reminders it owns (`DELETE /api/lists|alarms|reminders/...`), calls
`POST /api/memories/users/<id>/forget-synthetic` (`MemoryService.delete_user`, which calls `os_shadow.forget_user`: the member delete reaching
the kernel), and verifies: nothing owned remains and a second forget removes 0. Failure exits non-zero and prints the demo id and counts.

## Reading the report

One row per verb: planned, sent, judged (agree + disagree, minus planned `not_expressible`), agree, disagree, unavailable, agree rate, hop
p50/p90, PASS/FAIL with each failed condition named. Disagreement lines are mapped to the request in flight (by time; requests never overlap)
and listed as ids to read. Any disagreement not accounted for needs an entry in `--explained` (`{"result:reason": "why"}`); an unknown result
code counts as a disagreement. Overall adds: the "forget that" was answered, every member delete returned 200 and verified empty, and no
`OS_SHADOW forget NOT confirmed` line is in the window. Exit code 0 only on OVERALL PASS. Results hold ids, verbs, classes, codes and
durations; never request or reply text.

## What it does NOT prove

- It is scripted traffic from demo users: it cannot show how the household really speaks. Real traffic and the voice-corpus replay cover that.
- Shadow log lines carry no member id, so the report cannot separate synthetic acts from any real ones in the same window (the household guard
  limits this; a judged count far above the plan's expected acts is the sign).
- The kernel-side receipt count for a member going to zero is not read; "reached the kernel" rests on the delete returning 200 and the absence
  of an unconfirmed-forget line.
- It does not judge what Zoe said back; expected acts per request are [inferred] from the code and the report measures what really happened.
