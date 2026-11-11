---
type: research-lane
title: Lane 1 - re-measuring the 2026-11-03 "open circuit" numbers on the live box
date: 2026-11-11
status: measurement only. Nothing flipped, restarted, written or dispatched. Counts and aggregates only; no utterance, name or id copied.
builds_on: docs/research/samantha-initiative-2026-11-03.md (sections 0, 9, 12), samantha-initiative-2026-11-03/lane-A-current-state.md, docs/research/recall-gate-shadow-replay-2026-11-05.md
evidence: ~/.zoe-logs/zoe-data.app.log{,.1,.2,.3} (2026-11-01 13:08 to 2026-11-11 22:30, de-duplicated lines, marker counts only); ~/.zoe-logs/night-window.log; ~/.zoe-logs/memhist/*.log; systemd user journal (retained from 2026-11-07 02:29 only); panel daemon log /home/pi/.zoe-voice/voice.log (numeric TTFA / tail fields only, read over ssh); read-only SELECT COUNT/aggregate on zoe-database; scripts/maintenance/zoe_ground_truth.sh; code at origin/main 1ddb70951.
---

> Correction (same day, lane 7 section C): the commitments row below (0 of 33 kept, 31 missed) counts voice-replay sessions the ledger did not exclude. Household promises recorded: 0. Read that row as an instrument fault, not as household behaviour.

# Lane 1 - circuit re-measure (2026-11-11)

Labels: **[Proven]** measured directly with the stated method; **[Experimental]** measured but the instrument is a proxy or inferred
from parts; **[Speculative]** a reading of the numbers, not measured. Source tags: **[live]** box today, **[code]** origin/main
1ddb70951 with file:line, **[record]** the 11-03 record, **[contradicted]** the record or a doc is now wrong.

**Traffic split used everywhere.** Since 2026-11-04 22:39 the harness tags its lines `traffic=harness`. A line is counted as harness when
it carries that tag, when its `user=` matches `user_filters.SYNTHETIC_USER_RE`, or when its session starts `qa`/`demo`/`bench`/`probe`/`test`.
The last rule matters: 49 `FLUE_TOOLS`/`BRAIN_LANE` lines on 2026-11-09 came from `qa` sessions with no traffic tag (their seam lines
were tagged; the brain-side lines were not). Main log window is therefore **2026-11-05 00:00 to 2026-11-11 22:30 (7 days)** unless stated.

**When the flags went live.** `services/zoe-data/.env` was last modified 2026-11-08 20:59 local and carries `ZOE_NIGHT_MIND`,
`ZOE_OUTCOME_EDGES`, `ZOE_RECALL_EVERY_TURN`, `ZOE_MEMBER_REGISTER`, `ZOE_MANNER_BLOCK` (names only read). First markers:
`RECALL_EVERY_TURN` 2026-11-08 02:44; first `proactive_outcome_edges` row 2026-11-08 13:04Z (21:04 local, 5 min after the env write);
`MEMBER_REGISTER` 2026-11-09 03:02; `MANNER_BLOCK` present since 2026-11-01 (on before this round's window). `ZOE_NIGHT_MIND=serve`
has no zoe-data marker of its own, so its flip date is bounded only by the env mtime (on by 2026-11-08 20:59) [live]. Every new-flag
number below therefore covers **3 to 3.5 days**, not 7.

## Table

| metric | 11-03 | today | window / n | method | label |
|---|---|---|---|---|---|
| 1a Brain tool calls per household brain turn | 1 in 51 | **0 in 51** | 11-05..11-11, 51 household Flue turns (44 panel, 7 ask) | `FLUE_TOOLS` lines with `tools=[...]` non-empty, harness excluded | [Proven] [live] |
| 1b `recall_memory` calls, household | 0 | **0** | same, n=51 | `FLUE_TOOLS recall_memory=called:yes` | [Proven] [live] |
| 1c `recall_memory` calls, harness (control) | n/a | 31 of 1,102 | same | same marker, harness lines | [Proven] [live] |
| 2 Continuity injection matches, household | 0 of 91 | **0 of 30** (0 of 63 since 11-01) | 11-05..11-11, member turns | `SEAM_CONTINUITY matched=True`; harness control 17 of 961 | [Proven] [live] |
| 3a Recall gate mode | shadow | **shadow** (env unset, code default shadow) | 349 lines | `RECALL_GATE mode=`; `recall_gate.py:64-72` | [Proven] [live+code] |
| 3b Recall gate decisions, household | 54 real in 26 days (11-05 replay) | **9** (would_add 2 turns / 2 rows; would_drop 9 turns / 164 rows) | 11-05..11-11 | `RECALL_GATE` household lines, `would_add`/`would_drop` id counts | [Proven] [live] |
| 3c Recall-every-turn coverage line, eligible member turns | instrument missing | **relevant row found 1 of 17; another block claimed 2 of 17; injected 0** (shadow) | 11-08 02:44..11-11, 19 member lines (2 skipped `warming`) | `RECALL_EVERY_TURN rows= claimed= injected= skipped=` | [Proven] [live] |
| 3d Same line, skipped as guest | n/a | 64 lines skipped `guest` (62 on 11-08/11-09) | same | `skipped=guest` | [Proven] count / [Speculative] mostly the 11-09 QA burst |
| 3e Reply provenance: member brain replies with memory served / used | "83% used none" (10-29) | **served 4 of 32 (12.5%), used 2 of 32 (6%)** | 11-05..11-11 | `PROVENANCE_REPLY served= used=`, member lines | [Experimental] proxy for coverage |
| 4a History floor in code | 3 turns | **8 turns** (`ZOE_BRAIN_KEEP_TURNS` unset in the sidecar) | n/a | `labs/flue-zoe-brain-2x/src/context-window.ts:208-224`; built dist (mtime 11-10 15:01) returns 8 | [Proven] [code+live] |
| 4b History actually carried, household | ~164 tok avg | **p50 48 tok, mean 142, zero on 20 of 51 turns** | 11-05..11-11, n=51 | `FLUE_CONTEXT_BUDGET history=` | [Proven] [live] |
| 5 Packet: system / tools / history / tail (tokens) | ~2,836 / 752 / 164 / - | **mean 3,320 (p50 2,950, p90 4,352) / 683 / 142 / 105** | 11-05..11-11, n=51 household | `FLUE_CONTEXT_BUDGET` | [Proven] [live] |
| 6a Night-mind reader serves on a turn | readers off | **0 proven** (upper bound 3) | 11-08..11-11 | no zoe-data marker for the block; `night_threads.last_raised_at` set on 0 of 42; `BRIEF_FIRST_TURN` 0 lines in 11 days; `now_cards` 0 rows; recall floor (the only packet path that can carry the block) fired on 3 household turns | [Proven] 0 raised / [Experimental] the 3 |
| 6b Night windows, last 8 nights | 5 ok of 8 | **12B loaded 6 of 8; clean summary 5 of 8; night_mind job ok 5, idle 1** | 11-04..11-11 | `NIGHT_WINDOW_SUMMARY` + per-job lines, night-window.log; `night_runs` table | [Proven] [live] |
| 7a Outcome edges written | 0 | **8 rows (3 synthetic); outcomes welcomed 0 / ignored 0 / resolved 0; 1 `unknown`, 7 pending** | 11-08 21:04..11-11 | `proactive_outcome_edges` GROUP BY outcome, touch_point | [Proven] [live] |
| 7b Dated open loops | n/a | 19 rows, all dated; 6 unresolved (5 past due), 13 resolved (all 09-29..10-20) | all time | `open_loops` counts | [Proven] [live] |
| 7c Promise follow-through (commitments, shadow) | not measured | **0 of 33 kept; 31 `missed` (`shadow:would_fulfil`), 2 open** | 10-28..11-11 | `commitments` GROUP BY status, resolution | [Proven] count / [Experimental] meaning |
| 8a Real member user turns / days per 30 d | 349 / 19 | **308 / 25** | 10-12..11-11 | section-12 join, re-run reproduces 11-03 as 349 / 19 (414 total) | [Proven] [live] |
| 8b Distinct members | 1 | **1** | same | same | [Proven] [live] |
| 8c Non-member share of user rows | ~16% | **57%** (synthetic 249 = 35%, guest 157 = 22%, of 714) | same | same | [Proven] [live] |
| 8d Real turns, last 8 days | n/a | **62** (9,10,7,3,2,14,15,2 per day, 11-04..11-11) | 8 d | same | [Proven] [live] |
| 9a Kokoro starts per day | 24-51 dev days, 2 quiet | **22*, 33, 35, 27, 11*** (07-22h: 17, 23, 32, 19, 9) | 11-07..11-11 (* partial day) | journal `systemd: Started Kokoro` | [Proven] [live] |
| 9b zoe-data starts per day / in 07:00-22:00 | ~17/day | **24/18, 25/18, 25/23, 27/22, 10/8**; 0 crash exits | 11-07..11-11 | journal `systemd: Started`; 0 `Main process exited` | [Proven] [live] |
| 9c Waking MemAvailable | ~500-650 MB | **now 1,826 MiB; waking median 1,745-2,513 MB per day; daily min 120-1,681 MB** | 11-06..11-11, 180 samples/day | /proc/meminfo; memhist 07:00-22:00 `available` column | [Proven] [live] |
| 9d Event-loop stalls | 34 since 11-01, max 0.74 s | **one incident 11-05 23:00..11-06 04:00: 125 lines, max 1,808 s; otherwise 195 lines, median 0.79 s, p90 1.21 s, max 2.46 s, 142 in waking hours** | 11-05..11-11 | `EVENT_LOOP_LAG` | [Proven] [live] [contradicted: worse] |
| 10 Speech end to first sound, panel | ~3.2 s median, ~6.5 s p90 (inferred) | **~2.7 s median, ~5.4 s p90 (inferred)**: TTFA p50 2.05 s / p90 4.63 s (n=64) + endpoint close p50 640 ms / p90 800 ms (n=12) | 11-04 22:30..11-11 | Pi `turn_stream TTFA=` + `Recorded command ... close=quiet=Nms` (lane 2-I method) | [Experimental] [live] |
| 10b Server brain TTFT, panel | p50 1.38 s / p90 5.87 s (10-19) | **p50 1.87 s / p90 3.34 s** (n=44) | 11-05..11-11 | `VOICE TIMING brain_ttft_ms` | [Proven] [live] |
| 11a Manner block on member brain turns | n/a | **30 of 30** | 11-05..11-11 | `MANNER_BLOCK` within 8 s of each member `SEAM_CONTINUITY` | [Proven] [live] |
| 11b Member register on member brain turns | n/a | **0 of 16**; all 21 lines are KV warms between 22:00 and 06:00 | 11-09 03:00..11-11 | `MEMBER_REGISTER` vs member turns; `member_register` table 0 rows | [Proven] [live] |
| 11c Now card | n/a | **off; 0 markers; `now_cards` 0 rows** | all | flag unset (code default off) | [Proven] [live] |
| 11d Conversation notes | n/a | **off; 0 `CONVERSATION_NOTES*` lines; `conversation_notes` 0 rows** | all | flag unset | [Proven] [live] |

## Findings

1. **The memory circuit is still open at the conversation surface, and now measured more sharply.** In seven days the household had 51
   brain turns; the brain called no tool on any of them (harness: 69 tool turns, 31 `recall_memory`, so the tool works when asked).
   Continuity matched 0 of 30 member turns although it is on by default (`zoe_flue_client.py:1621-1625`, env unset = on) [Proven].
   Reply provenance shows memory served on 4 and used on 2 of 32 member replies (6%) against the record's 60% target [Experimental].
2. **The new every-turn recall would not close it if switched on today.** On 17 evaluated member turns its lexical relevance found a
   row once (and that turn was already claimed by the recall floor); `pool=54` durable rows for the member. Flipping
   `ZOE_RECALL_EVERY_TURN` from shadow to on would inject on about 1 turn in 17 [Proven count; Speculative projection]. The break
   has moved from "the injector is shadow" to "the injector's relevance test plus a 54-row pool rarely matches real speech".
3. **Night mind writes; nothing reads it into a turn.** The 12B wrote on 5 of 8 nights (90 observations, 42 threads), but 0 threads
   have ever been raised, the first-turn brief (`ZOE_BRIEF_ON_FIRST_TURN=1` live) has logged 0 `BRIEF_FIRST_TURN` lines in 11 days,
   the now card is off with 0 rows, and the remaining reader (`routers/memories.py:1202-1225`) is reached only through the
   shape-gated recall floor (3 household turns) [Proven 0 raised].
4. **The outcome edge is live but has measured no outcome.** 5 member edges in 3 days, none with an outcome; the dated-loop
   follow-through number cannot be computed yet (n=0 outcomes). The commitments ledger, in shadow, records that 31 of 33
   reminder promises Zoe made since 10-28 were not backed by a reminder (`commitments.py:27-30`: `missed` = a breach she would
   have acted on) [Proven count; contradicted as a household number]. Corrected (see the note at the top and lane 7): all 33 rows are
   replay-harness artifacts, so this is not a follow-through number; real household promises recorded are zero.
5. **Of the four new wires, only the manner block executes on real turns.** Manner: 30 of 30. Member register: 0 of 16 member turns;
   the table is empty, so the only line it renders is the late-night situational line (`member_register.py:138-145`), seen only on
   overnight KV warms. Now card and conversation notes: off.
6. **History floor is in code and the built sidecar, but turns carry little history** (p50 48 tokens, none on 20 of 51) because voice
   sessions roll after idle and `ZOE_VOICE_SESSION_BRIDGE` is unset. The persona/system share grew: mean 3,320 tokens vs 2,836 on 11-03.
7. **Usage did not grow.** 308 real member turns on 25 of 30 days (349 / 19 on 11-03), still one member, 62 real turns in the last
   8 days; non-member rows rose to 57% of user rows (an 11-09 QA burst of ~312 rows).
8. **Reliability: memory is better, restarts and stalls are worse.** Waking MemAvailable medians ~1.7-2.5 GB (was ~0.5-0.65 GB), but
   Kokoro starts 27-35 per full day and zoe-data 24-27 per day, 18-23 of them in waking hours (all clean stops, i.e. deploys and probes),
   and event-loop stalls now reach 2.46 s outside a 5-hour 11-05/06 incident whose stalls reached 1,808 s.
9. **Latency improved at the panel.** Inferred end of speech to first sound ~2.7 s median / ~5.4 s p90 (was ~3.2 / ~6.5); server brain
   TTFT p90 3.34 s (was 5.87 s on 10-19).

**Verdict: open, with one wire closed.** Manner rides every member turn. Every path that would carry what Zoe knows into a reply
(tool, continuity, recall gate, every-turn recall, night-mind readers, now card, notes, register) shows zero or near-zero executions on
real turns. The break is no longer "flags are off". It is two things: the relevance and trigger tests that decide injection almost
never match real household speech, and the stores they read are thin (54 durable rows, 0 register rows, 0 notes, 0 now cards).

## Could not verify

- **Night-mind block injection per turn.** zoe-data writes no info marker when `night_mind.prompt_block` returns a block. The 3 recall-floor
  household turns could have carried one. Measuring this needs a counts-only marker (`NIGHT_MIND_SERVE user= notes=`) next to `SEAM_RECALL`.
- **Exact flip times** for `ZOE_NIGHT_MIND=serve`, `ZOE_MEMBER_REGISTER`, `ZOE_OUTCOME_EDGES`: there is no flag-change log, only the env
  file mtime (11-08 20:59) and first markers.
- **Kokoro / zoe-data starts for 11-04..11-06**: the user journal begins 2026-11-07 02:29.
- **True memory coverage ("relevant memory existed and was used").** `RECALL_EVERY_TURN` measures lexical relevance against the member's
  own durable rows only; `PROVENANCE_REPLY used=` is a text-overlap proxy. Neither is a judged relevance score.
- **Guest-skipped recall lines (64).** Whether they are unverified household voices or QA harness turns cannot be split from the line.
- **Why the first-turn brief never logs.** `brief_first_turn._prepare` returns without logging outside its window, on a continuity turn,
  when the day is claimed, or when there are no items (`brief_first_turn.py:384-434`); which one applies needs a debug-level read.
- **Panel latency** is inferred (TTFA + tail), as on 11-03; only 12 `Recorded` lines in the window carried a `close=` value, and the
  panel daemon build differs from the checkout (ground truth), so its log fields may not match current code.
- **Commitment `missed` semantics.** The shadow verdict says Zoe would have acted; whether each promise was a real promise was not judged.
