# Lane 2-L — What the real traffic says (measured 2026-10-19)

Privacy: this report has counts, rates, durations, categories, intent/tool/flag names and per-day timestamps only. It contains no utterance text, no transcript fragments, no names, no memory contents and no location. Free-text fields were bucketed inside throwaway scripts (`scratchpad/l2/*.py`, run with `python3 -I`), and only the category counts were printed. All reads were read-only: aggregate SELECTs, a `mode=ro` sqlite open, and log greps/awk.

## 0. The finding that changes how to read everything else: most of the live traffic is the test harness

"Organic" here means a household request. It is identified by session prefix (`voice-panel-zoe-touch-pi-`, `telegram-`, `web_`), by the STT log `panel_id=zoe-touch-pi`, or by a timestamp within 90 s of one of those turns. Everything else is the replay harness, the bar/day-sim harness, probes, or test panels.

| instrument | total | organic | harness/test share | source |
|---|---|---|---|---|
| Brain turns (`BRAIN_LANE`, 09-25..10-19) | 5,556 | 154 | **97.2%** | `~/.zoe-logs/zoe-data.stderr*.log` |
| STT calls since 10-06 | 4,457 | 173 | **96.1%** (`replay-harness`, `test-panel`, probes) | `~/.zoe-voice/voice_stt.jsonl` |
| STT calls, all history (06-08..10-18) | 19,607 | 1,613 | 91.8% | `voice_stt.jsonl` + `.1` |
| Router shadow rows | 10,893 | 1,033 | 90.5%. Only 972 distinct utterance hashes; 9,973 rows come from hashes seen 5 or more times | `services/zoe-data/data/router_head_shadow.jsonl` |
| `reply_sources` rows | 398 | 0 | 100% `replay-` sessions | Postgres `reply_sources` |
| Intent-miss file (recreated 10-18) | 359 | 0 | 100%. 46 distinct texts, all written 10-18 between 16:00 and 22:00 during harness runs | `~/training/data/intent-misses.jsonl` |
| Voice-regression runs | 739 runs × a 20-clip corpus | — | — | `~/.cache/zoe/voice_regression_trend.jsonl` |

Zoe's heaviest user is her own test harness. It runs through the same single zoe-data worker, the same GPU and the same logs. A metric computed without a traffic-class filter mostly measures the harness. All figures below are organic-only unless labelled otherwise.

## 1. Volume and channels

**Members active** (Postgres `chat_messages.metadata.user_id`, organic sessions): 2 distinct member accounts all-time and 1 in the last 60 days. Every organic voice turn in `chat_messages` is attributed to a member: 0 are attributed to guest or sentinel accounts. 61 `users` rows exist, and 59 of them are synthetic or test accounts.

**User turns per channel.** Postgres `chat_messages`, `role='user'`. Its last row is dated 10-15 UTC (10-16 morning local), and the table contains no LiveKit or HA channel.

| channel | all-time (07-04..10-15) | days used | last 60 d (08-20..10-19) | days used (60 d) |
|---|---|---|---|---|
| voice / panel | 866 | 44 | 216 | 15 |
| Telegram | 106 | 20 | 33 | 6 |
| web chat | 18 | 4 | 6 | 1 |
| LiveKit | — | — | 3 on-demand room sessions in 14 d (`routers.voice_livekit` log) | — |
| HA (Assist/voice) | — | — | 0 evidence; 9 `/api/ha/control` POSTs from the panel browser in 24 d | — |
| probes / tests inside `chat_messages` | 30 | 6 | 2 | 1 |

**Active days, last 60 days** (voice/Telegram/web user turns, from `chat_messages`, by UTC date; panel STT rows from `voice_stt.jsonl` in brackets):
08-20 2 [3] · 08-21 2 [1] · 08-22 4 [5] · 08-23 1 [2] · 08-24 2 [2] · 08-25 12 [14] · 08-26 7 [10] · 08-27 – [2] · 08-31 7 [5] · 09-02 2 · 09-03 3 [3] · 09-28 41 [39] · 10-04 8 Telegram [1] · 10-05 6 web · 10-09 16 Telegram [1] · 10-10 1 · **10-12 114 [94]** · 10-13 4 [7] · 10-14 18 [39] · 10-15 5 [28] · 10-16 [4] · 10-17..10-19 0.

Use is bursty, with roughly 1 heavy day per 2–3 weeks. Zoe was used on 9 of the last 30 days and had 0 panel voice turns after 10-16.

**Hour of day, local time.** Real panel STT rows (`voice_stt.jsonl`, `zoe-touch-pi`):
- All-time (n=1,613): peaks at 06–08 (161/211/157) and 21–22 (148/250). The trough is 10–15 (15–38 per hour).
- Last 60 days (n=269): the 18:00 hour is 66 (one heavy evening), 21:00 is 42, 23:00 is 23 and 06:00 is 21. There is almost nothing between 08:00 and 12:00.

**Commands vs conversation.** These are organic router decisions (`two_stage_domain`) from the shadow log, active mode.

| window | routed turns | conversation (`chat`) | commands | top command domains |
|---|---|---|---|---|
| 30 d | 282 | **205 (73%)** | 77 (27%) | memory 17, smart_home 13, people 10, weather 10, calendar 9, time 7, lists 6, music 2, reminders 2, notes 1, timers 0 |
| 60 d | 316 | 225 (71%) | 91 | smart_home 19, memory 19, weather 11, people 11, time 10, calendar 9 |
| all (since 07-14) | 1,033 | 562 (54%) | 471 | time 76, smart_home 71, weather 69, calendar 67, music 50, lists 48 |

The mix has shifted from commands, which were 46% of turns all-time, to conversation, at 73% in the last 30 days. The regex intent layer (`~/.zoe/data/pi-intent-shadow.jsonl`, organic, last 30 d) agrees: 192 of 226 turns (85%) are `fallback`, i.e. no deterministic intent.

## 2. Router truth

**Shadow log schema has changed.** Only 40 rows, all from 07-14, carry the legacy fields (`head_pred`, `agree`, `head_ms`). On those rows agreement is 29/40 and `head_ms` has p50 0.68 ms and max 6.2 ms. The other 10,845 rows are two-stage `mode=active` records with these fields: `two_stage_domain`, `two_stage_tool`, `head_conf`, `gated`, `reason`, `similarity_routed`, `two_stage_ms`. The only available agreement measure compares the two-stage head with the similarity baseline: `two_stage_domain == similarity_routed`. **That is not ground truth.**

- **Agreement, organic:** 553/1,033 = 0.54 overall (all traffic: 0.55).
- **Organic agreement by ISO week:** W29 0.46 · W30 0.61 · W31 0.60 · W32 0.54 · W33 0.58 · W34 0.43 · W35 0.56 · W36 0.44 · W40 0.62 · W41 0.42 · W42 0.58. There is no trend.
- **Organic agreement by predicted domain (n / agree / mean confidence):** chat 562/0.59/0.82 · time 76/0.87/0.99 · smart_home 71/**0.00**/0.96 · weather 69/0.84/0.97 · calendar 67/0.70/0.87 · music 50/**0.00**/0.89 · lists 48/0.52/0.91 · memory 39/0.03/0.93 · people 30/0.50/0.86 · reminders 12/0.33/0.84 · timers 7/1.00/0.97 · notes 2/0.00/0.64. Smart home, music and notes score 0.00 by construction because the similarity router has no examples for them. Memory scores 0.03 for a similar reason. Agreement is therefore structurally capped for 4 of 12 domains.
- **Confidence vs agreement, organic, 0.05 bins (agree/n):** 0.30 3/7 · 0.35 4/9 · 0.40 10/18 · 0.45 7/23 · 0.50 13/40 · 0.55 7/33 · 0.60 7/50 · 0.65 10/32 · 0.70 14/41 · 0.75 24/40 · 0.80 12/32 · 0.85 23/47 · 0.90 31/59 · **0.95+ 387/599 (0.65)**.
  - Excluding the four no-similarity domains: <0.50 0.42 · 0.50–0.60 0.31 · 0.60–0.70 0.23 · 0.70–0.80 0.49 · 0.80–0.90 0.46 · 0.90–0.95 0.57 · ≥0.95 0.77.
  - There is a step up at 0.70 (0.23 → 0.49) and the real knee is at 0.95. **The 0.70 gate (`ZOE_ROUTER_HEAD_MIN_CONF` default) cannot be properly re-measured** because gated rows record `two_stage_domain=chat` and do not record the head's own top-1. `semantic_router._two_stage_rec` drops `head_top` even though `router_two_stage.decide` returns it.
- **Gate outcomes, organic:** 440/1,033 gated (43%). Reasons: `chat_top` 90, `low_conf` 27, `below_gate` 27. `failed` = 0.
- **`two_stage_ms`, organic** (on the critical path in active mode): p50 252, p90 734, p95 848, p99 1,162, max 1,599 ms. Across all traffic the p50 is 171 ms.
- **Intent misses:** the path is still `~/training/data/intent-misses.jsonl` (`intent_router.py:1714`). The directory was recreated 10-18 11:39. The file holds 359 rows, 46 distinct, all harness replay, with **no intent field**, so "misses per intent per week" cannot be computed. A parallel record (`~/.zoe/data/pi-intent-miss-evidence.jsonl`) does not exist.
- **Organic regex intent hits, last 30 d** (`pi-intent-shadow.jsonl`, 226 turns): greeting 9, people_create 5, acknowledgement 4, calculate 3, lets_talk 2, list_add 2, and 1 each for status_check, people_search, list_get_items, panel_confirm_code, calendar_create, date_query.

## 3. STT and voice quality (real panel only)

Source: `voice_stt.jsonl` (`panel_id=zoe-touch-pi`, last 60 d, n=269) and `VOICE TIMING` log lines (n=133 organic).
- **Empty transcripts:** 29/269 = **10.8%**. Transcripts under 8 characters: 3 (1.1%). Errors: 0. All-time, 228 of 1,613 rows are empty (14%).
- **Wakes vs turns from the panel daemon** (`/api/voice/wake` vs `/api/voice/turn_stream`, 09-28..10-16): 265 wakes and 214 turns, so **51 wakes (19%) never became a turn**. Per day (wakes/turns): 09-28 50/39 · 10-12 112/94 · 10-14 47/39 · 10-15 27/23. The panel-side wake near-miss log lives on the panel host and was not read (out of scope).
- **Clip length.** Duration is recorded for 61 rows: 1 s 10, 2 s 16, 3 s 12, 4 s 13, 5 s 3, ≥7 s 7. Estimated from bytes (all 269 rows): ≤1 s 50, 2 s 67, 3 s 48, 4 s 56, 5 s 19, 6–9 s 15, ≥10 s 14. The typical command is 1–4 s.
- **Per-stage timings, organic voice:**
  - STT: p50 0.63 s, p90 1.72 s, p95 2.17 s, max 9.6 s.
  - Pre-brain: p50 79 ms, p90 607, p95 971, max 6,783.
  - **Brain time to first token:** p50 1,117 ms, p90 **5,386**, p95 **6,390**, max 9,452.
  - The memory packet is skipped on the Flue lane (0 ms).
  - Server time to first byte on `turn_stream`: p50 909 ms, p95 3,236 ms.
  - `/api/voice/transcribe`, all traffic: p50 554 ms, p95 2,183 ms.
- **Chat lane vs deterministic tier, organic:**
  - 154 brain turns: 148 `ok`, 4 `interrupted/consumer_disconnected`, 1 `caller_cancelled`, 1 `fallback` (stream error).
  - Deterministic/expert paths fired on a small minority: `SKYBRIDGE_GATE` 50 (show 14, current 8, turn_on 6, turn_off 6, identity vetoed 3, create_event 2); `EXPERT_ACTIVE` weather 1; `INTENT_GATE` 3, of which 2 were vetoes (router disagreed or was low-confidence).
- **Marker counts per day, last 14 days, organic `BRAIN_LANE`:** 10-04 4 · 10-05 5 · 10-10 10 · 10-12 57 · 10-13 5 · 10-14 25 · 10-15 16 · 10-16 1. Harness `BRAIN_LANE` over the same span: 10-04 234 · 10-05 325 · 10-06 247 · 10-07 188 · 10-08 240 · **10-09 1,894** · 10-10 341 · 10-13 76.
  - `ROUTER_SHADOW` in the app log: 202 organic.
  - There is no `TIER` marker in the logs.
- **Speaker ID:** the `voice speaker verdict` line was emitted 111 times (10-07..10-13), **all for the harness panel**: verified=False 83, True 28. It was emitted **0 times for the real panel**. Real-panel turns arrive with `identified_user_id` already set, so the server-side verdict is skipped (`routers/voice_tts.py:3405-3418`). The result is that verified-vs-guest per day is unmeasurable for real traffic. 2 of the 4 `struggle_events` carry `is_guest=1`.

## 4. Where she fails

- **`struggle_events`** (Postgres, migration 0050): **4 rows total**, all `mode=shadow`, dated 10-14 and 10-15: CORRECTION/answer_quality, MISHEAR/stt, FALLTHROUGH/router_training (music), VERBOSE/verbosity. The daily summaries for 10-17 and 10-18 recorded total=0, but there were no turns on those days. On 10-14 and 10-15 there were about 41 brain turns and 4 signals.
- **Feedback:** `chat_feedback` has 0 rows ever, although `ZOE_CONVERSATION_FEEDBACK` is default ON. The `llm_call_log.feedback` column is null on all 5,842 rows.
- **Corrections applied:** `CORRECTION_APPLIED` 6 (10-06 1, 10-07 1, 10-09 4). All are the same kind with `changed=4` and there is no session, so this looks like harness repetition. 0 can be attributed to real turns.
- **Write-claim guard:** `WRITE_CLAIM_BLOCKED` 32. All were on 10-12, in two 16-line bursts at 19:23 and 19:51 (action: replaced 30, shadow 2). Neither burst lines up with real brain turns (only 1 turn in the 19:00 hour), so this looks like a test pattern and **0 can be attributed to real turns**.
- **Restraint (`ZOE_RESTRAINT` default shadow):** 1,110 `RESTRAINT` lines, of which 15 are organic. On every organic packet turn the shadow verdict would have **withheld 7–11 items** (6 turns at 7, 3 at 9, 2 at 10, 2 at 11, 1 at 8). `RESTRAINT_MUTE` appears 9 times (action=mute).
- **Tombstones:** 1,201 `memory_tombstones shadowing` lines (10-07 176 · 10-08 88 · 10-09 628 · 10-10 74 · 10-11 78 · 10-12 79 · 10-13 78). They are background or unattributed; the roughly 78/day floor exists even on days without traffic.
- **Proactive** (Postgres `proactive_deliveries`, `proactive_ledger_lines`):
  - 14 deliveries, all of kind `open_loop`: 12 greeting-shaped, 2 cue-shaped. **Outcome resolved for 0**: 12 `unknown`, 2 null. `PROACTIVE_LEDGER_OUTCOME` shows the same 12 × outcome=unknown, voiced=?.
  - 18 ledger lines: open_turn 12, gap 4, cue 2.
  - The last sweep shows open=2, closed=0.
  - `ZOE_PROACTIVE_SPOKEN=0`.
- **Open loops:** 12 created, all in the last 30 days. 4 resolved, 8 open. `OPEN_LOOPS` extraction ran 114 times with no session plus 35 harness runs.
- **Samantha bar** (`~/.cache/zoe/samantha_bar_trend.jsonl`, 67 runs 09-28..10-09): statuses were ok 30, partial 32, regression 5. **There have been no runs since 10-09.** In the latest run every scored scenario passed and S12/S16 were skipped. Historically weak scenarios: S10 12 FAIL / 1 PASS, S24 5/1, S21 5 FAIL + 4 ERROR / 2 PASS, S1 5/30, S4 6/31.
- **Day-sim** (34 runs 10-03..10-12): overall PASS 10, FAIL 19, ERROR 4, SKIP 1. **The last run (10-12) was overall FAIL**, with S9b failing; S9b's history is 6 FAIL / 1 PASS. Five of 16 cells are perpetually SKIP (1b, 7b, 2, S9c, plus recent skips of 1r/7r/7s).
- **Person checkout:** 7 runs 10-08..10-10. All were partial or error, and the cells are empty.
- **Voice regression trend** (739 runs): pass 454, fail 131, skip 88, error 22, no status 44.
  - The bad days were 10-12 (14 pass / 29 fail), 10-14 (21/27) and 10-17 (4/8).
  - Most failures come from an ok-rate of 0.90–0.95 against a 1.00 baseline (31 runs). In 4 runs the ok-rate collapsed to 0.15. In 22 runs the probe could not run.
  - The latest run (10-18) shows ok 19/20, empty 1, STT 169 ms per audio-second.

## 5. Memory in use

**MemPalace drawers** (`~/.mempalace/chroma.sqlite3`, read-only): 307 rows, 305 of which belong to real members.
- Status: archived 156, approved 133, superseded 14, pending 2.
- Source: person_created 50, journal_created 48, note_created 47, idle_consolidation 42, conversation 34, digest 16, operator_restore 16, voice 11, nightly_session_scrub 11, voice_turn_digest 11.
- Type: person 102, fact 68, journal 48, note 47, emotional_moment 9, relationship 8, health 7, event 6.
- **Authority class: unset on 274/305 (90%)**. user_stated_derived 15, model_from_transcript 12, user_stated 3, model_from_turn 1.
- Age: <7 d 29, 7–30 d 179, 30–90 d 27, ≥90 d 70.
- Access: 0 accesses 16, 1–4 144, 20+ 143.
- Audit collection: 19,831 events (ingest 12,026, edit 6,606, archive 861, archive_duplicate 141, delete_user 82, forget_erase 4). With 12k ingests against 307 live rows, the store's write traffic is dominated by synthetic users.

**Night observations** (Postgres): 42 rows, all `user_stated_derived` (current) or `pending` (held 11). Night threads: 26, of which 23 are open/wait.

**Recall packets, organic:**
- `SEAM_RECALL` fired on 14 of 154 brain turns, at about 12 bullets / 1.4k characters.
- `SEAM_CONTINUITY` was empty on 72 of 74.
- **`PROVENANCE_REPLY`: 68 of 82 organic brain replies (83%) served and used 0 memory sources**, and 11 served 12 and used 1–2.
- `RECALL_GATE` (shadow) served 0–3.
- Flue context budget, organic: system p50 2,721, tools p50 723, history p50 93 (0 on 37/116).

**Night mind** (Postgres `night_runs`; `~/.zoe/night-reports/*.json`):

| night | turns in | moments proposed/verified | observations written | threads created/updated | prompt/completion tokens |
|---|---|---|---|---|---|
| 10-13 | 109 | 31/20 | 20 | 17/0 | 5,370/1,852 |
| 10-14 | 30 | 9/5 | 5 | 4/1 | 2,431/850 |
| 10-15 | 18 | 6/5 | 5 | 3/2 | 1,684/694 |
| 10-16 | 20 | 5/4 | 4 | 2/2 | 1,605/622 |

There are no night_mind rows for 10-17 or 10-18 because there were no turns. The night window ran every night 10-12..10-18 with outcome ok and exit 0. It used **2.6–12.7 min of a ~63-min cap**, and zoe-data was down 152–757 s per night. Jobs were digest, dreaming, night_mind and index_compaction, and all were ok. There are 104 report files in total, 90 of them from the 10-09..10-11 trial days.

**Morning brief:** there is no per-item log marker (`ZOE_BRIEF_ON_FIRST_TURN=1` is live), so items per day cannot be measured.

**Open loops:** 12 created and 4 closed in 30 d.

## 6. Features used vs built (last 30 days, organic)

| capability | live flag / state | 30-day organic evidence | verdict |
|---|---|---|---|
| conversation (brain) | `ZOE_BRAIN_BACKEND=flue` | 205 routed chat turns; 154 brain turns in 24 d | the main use |
| memory recall | `ZOE_SEAM_RECALL_INJECT=true` | router memory 17 (recall_memory 15, remember_fact 2) | weekly |
| home control | `ZOE_SKYBRIDGE_ONLY=true`, `ZOE_HA_VOICE_ENABLED=true` | router smart_home 13; skybridge turn_on/off 12; 9 panel `/api/ha/control` | weekly |
| people/contacts | `ZOE_PERSON_*=1`, `ZOE_CONTACTS_CONVERSATIONAL=1` | router people 10; `people` rows +2; regex people_create 5 | weekly |
| weather | expert active | router 10; `EXPERT_ACTIVE` weather 1 | weekly |
| calendar | — | router 9; `events` +6 real rows | weekly |
| time/date | — | router 7 | weekly |
| lists | — | router 6; `list_items` +4; 3 browser shopping adds and 3 deletes | occasional |
| Omi pendant | `ZOE_OMI_SYNC_ENABLED=1` | **273 recordings in 4 days (10-13..10-16)**; 59% empty transcript | heavy, then stopped |
| Omi day review | `ZOE_OMI_DAY_REVIEW=shadow` | 4 reviews, **14 candidates, 0 decided** | built, never completed |
| Telegram | bot live | 31 user msgs over 5 days; 7 voice notes | occasional |
| reminders | — | router 2; 1 real `reminders` row | rare |
| music | `ZOE_MUSIC_DISCOVERY=on` | router 2; **`music_play_history` last row 07-26; listening events last 08-09** | **unused 30 d** |
| timers | — | router 0 (7 all-time); 0 `set_timer` | **unused 30 d** |
| notes / journal | — | router notes 1; `notes` last 07-07; `journal_entries` 0 ever | **unused 30 d** |
| relationships graph | `ZOE_RELATIONSHIP_GRAPH_ENABLED=1` | `person_relationships` last 07-14 | **unused 30 d** |
| face ID | `ZOE_FACE_ID_ENABLED=true` | `face_profiles` last 07-19; no face verdict evidence | **no evidence** |
| LiveKit | `ZOE_LIVEKIT_ONDEMAND=true` | 3 room sessions in 14 d | rare |
| web search | — | no tool-name logging exists | unmeasurable |
| voice enrolment | `ZOE_ENROLL_OFFER=on` | 4 offers, 6 enrol POSTs, 1 result | one-off |
| proactive raises | `ZOE_PROACTIVE_LEDGER=1`, `SPOKEN=0` | 14 deliveries, 0 outcomes | built, outcome blind |

**Used daily:** nothing. The household used Zoe on 9 of 30 days. **On active days the steady uses are conversation, memory recall, home control, people, weather and calendar.**

**Built but unused in 30 days:** music and music discovery, timers, notes/journal, the relationship graph, face ID, Omi day-review decisions, and spoken proactive (switched off).

## 7. Health signals

- **zoe-data restarts:** `NRestarts=0`. Systemd restarts are not the issue; *starts* are.
  - The journal (from 10-11) shows 126 starts in 8.5 days, including 2 `Failed with result` (10-13, 10-18).
  - The app log shows **237 startups in 14 days (about 17/day)**: 10-05 21 · 10-06 20 · 10-09 46 · 10-11 21 · 10-13 21 · 10-15 20 · 10-18 20.
  - The gap from the last request before a start to the first request after it is p50 167 s and p90 1,960 s.
  - The deploy log was not found as a separate file.
- **Crash-loop watcher** (`~/.zoe-logs/crash-loop-watch.log`): 0 crash-loop alerts. 226 unhealthy samples, concentrated on 10-09 (68), 10-10 (41) and 10-11 (87), mostly in waking hours. The nightly 02:00 samples are the designed sleep.
- **Kokoro** (journal, from 10-11): **178 CUDA pipeline loads in 8 days**. 11 CUDA memory-pressure warnings, 5 CUDA load failures and **1 fatal "CUDA OOM after retries" (10-12)**. Starts per day: 10-12 86, 10-14 100.
- **Brain:** `flue-zoe-brain-2x` failed 6 times (10-11 1, 10-12 2, 10-13 1, 10-18 2). `llama-server` logged 32 prompt-cache evictions.
- **Hang / stall signature.** Cheap polling routes taking more than 5 s: 10-13 19:00 23 and **10-13 20:00 100**; 10-04 11:00–13:00 12. Max durations on cheap routes reached 104–162 s (announcements, display prefs, panel config).
- **5xx** (09-25..10-19, 482,892 requests): 219 total, 159 of them on 10-13 and 34 on 10-04.
  - By route: `/api/skybridge/timers` 54 (503) · `/api/system/display/preferences` 42 · `/api/panels/{panel}/config` 26 · `/api/ha/entities` 23 (502) + 16 (503) · `/api/voice/profiles/sync` 19 (500) · `/api/proactive/inbox` 14 · `/api/voice/announcements` 12 · `/api/voice/enroll` 3.
- **4xx noise:** 12,187 × `GET /api/v1.0/public/device` 404 (an unknown poller, about 500/day). 2,459 × `/api/notifications/pending` 403. 468 × `/api/ha/entities` 403 (the panel calls this as guest).
- **p95 latency by route:**

  | route | p50 | p95 |
  |---|---|---|
  | `/api/chat/` (harness) | 2.6 s | 7.6 s |
  | **Telegram chat** | **6.6 s** | **10.6 s** |
  | `/api/voice/turn_stream` | 0.9 s | 3.2 s |
  | `/api/music/now-playing` | 41 ms | 3.0 s |
  | `/api/weather/current` | 12 ms | 1.7 s |
  | `/api/proactive/inbox` | 63 ms | 43 s |
  | `/api/ui/actions/pending` | — | 272 ms |

- **Event-loop lag:** #2117 shipped, and `zoe_event_loop_lag_seconds` plus `zoe_event_loop_lag_max_seconds` exist in `services/zoe-data/event_loop_lag.py`, which `main.py:1209` starts. 3 `EVENT_LOOP_LAG` warnings (0.88–0.92 s) appeared after the 10-18 23:xx restart.
- **Health script noise:** `health-systemd.log` still probes retired Hermes, giving 135 false warnings.

## 8. Evidence-ranked priorities (frequency × household impact)

1. **Harness traffic owns the live box.** 97% of brain turns and 96% of STT calls are harness traffic. The harness drives about 17 zoe-data starts/day (p50 about 167 s offline each) and Kokoro reloads 178 times in 8 days (5 CUDA failures and 1 OOM). It also contaminates every live metric.
   - Points at: `scripts/maintenance/voice_regression_probe.py`, the bar/day-sim harnesses, and the missing traffic-class tag in `middleware.logging` and the shadow logs.
   - Fix: a mandatory harness session-prefix or header that every log and table records, plus a quiet-hours or separate-instance policy.
2. **Brain first-token tail on voice.** p90 5.4 s and p95 6.4 s (n=133), on top of STT p95 2.2 s and pre-brain p95 1.0 s. This is the household's most frequent pain on its main use (73% conversation).
   - Points at: the Flue lane (`ZOE_BRAIN_BACKEND=flue`), `FLUE_PROMPT_CACHE`, and llama-server cache evictions (32).
3. **Availability: stalls and restarts while people use her.**
   - 10-13 20:00: 123 cheap requests over 5 s and 159 5xx in one evening.
   - 2 failed exits and 6 Flue brain failures.
   - The nightly sleep takes zoe-data down 2.5–12.6 min.
   - Points at: the single worker (`EVENT_LOOP_LAG` is now measurable), `/api/skybridge/timers` and `/api/system/display/preferences` 503s, and deploy cadence.
4. **Wakes that go nowhere.** 51/265 wakes (19%) never became a turn, and **10.8% of real STT transcripts are empty**. The household hears silence or a non-answer about once every five to ten tries.
   - Points at: panel VAD/wake threshold, `ZOE_STT_STREAM_UNDER_SPEECH`, Moonshine streaming. The struggle detector caught 1 MISHEAR.
5. **Failures are not captured.** 4 struggle events (shadow), 0 `chat_feedback` rows, 0 organic correction or write-claim trips, no intent label in the miss file, and proactive outcomes 0 of 14 resolved.
   - The household's real failure rate is unknown, because the instruments either see only the harness or record nothing.
   - Points at: `struggle_detector.py` (shadow), `conversation_feedback.py`, `intent_router.py:1714`, `proactive.ledger` outcome resolution.
6. **Omi pendant: heavy use, broken loop.**
   - 273 recordings in 4 days, 160 (59%) of them with empty transcripts.
   - All recordings are speaker `unverified`. The 14 day-review candidates have **0 decisions**, and 205 `omi_sync` notifications were never delivered.
   - Points at: `ZOE_OMI_DAY_REVIEW=shadow`, the Omi transcription path, and `OMI_EMPTY_TRANSCRIPT` (64).
7. **Memory rarely reaches replies.**
   - 83% of organic brain replies used 0 memory sources.
   - Continuity was empty on 97% of turns.
   - Seam recall fired on 9% of turns.
   - 90% of live memories have no authority class.
   - Points at: `SEAM_CONTINUITY` / `SEAM_RECALL` (`zoe_flue_client`), `RECALL_GATE` (shadow), and the provenance backfill.
8. **Speaker ID is unmeasured on the real panel.** It produced 0 verdict lines for `zoe-touch-pi`, the server-side gate is bypassed when the panel sends `identified_user_id`, and 2 of 4 struggle events were stamped guest.
   - Points at: `routers/voice_tts.py:3405-3418`, `ZOE_SPEAKER_ID_THRESHOLD=0.70`.
9. **The router instrument cannot judge its own gate.**
   - Gated rows drop `head_top`.
   - Agreement against similarity is structurally 0 for smart_home, music and notes.
   - Organic `two_stage_ms` is p95 848 ms on the critical path.
   - The real confidence knee is at 0.95, not 0.70.
   - Points at: `semantic_router._two_stage_rec`, `router_two_stage.min_conf`.
10. **Telegram is slow.** p50 6.6 s and p95 10.6 s end-to-end, though at low volume (16 turns in 24 d).
    - Points at: the `/api/chat/` non-streamed path for `node` callers.

**Three built-but-unused items to decide on:**
- **Music + discovery** (`ZOE_MUSIC_DISCOVERY=on`). There were 50 music turns all-time, then 2 in 30 d, and the last play was 07-26. That pattern looks like breakage rather than lost interest.
  - *Promote (fix):* add a provider health check and surface "music is down" rather than failing silently.
- **Omi day review** (`ZOE_OMI_DAY_REVIEW=shadow`). There are 14 pending candidates and 0 decisions.
  - *Promote* into the next-interaction review the owner already chose, or *retire* the shadow writer.
- **Notes / journal / timers.** Notes were last used 07-07, there are 0 journal entries ever, and timers saw 0 use in 30 d (7 all-time).
  - *Retire* notes and journal as separate surfaces (fold them into memory). Keep timers routed but cut their test and harness weight.

## Instrument gaps and privacy hazards found (counts only)

- **Raw household text on local disk or in logs:**
  - `ZOE_ROUTER_SHADOW_TEXT=1` writes raw text into 10,560 shadow rows.
  - `intent_router.py:1710` logs `intent_miss: <raw text>` at INFO, giving thousands of app-log lines.
  - `ROUTER_SHADOW text=` puts raw text into 202 stderr lines.
  - `voice_stt.jsonl` stores full transcripts.
  - All of this is local, but it contradicts the hash-only intent in `semantic_router._head_shadow`.
- **Unmeasurable today:** morning-brief items, brain tool calls by name (needed for web-search usage), the real-panel speaker verdict, intent-miss labels, and the panel wake near-miss log (on the panel host, not read).
