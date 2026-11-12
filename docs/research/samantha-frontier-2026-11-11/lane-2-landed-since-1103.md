---
type: research-lane
title: "Lane 2 - what landed since 2026-11-03: live, shadow, dark, or not deployed"
date: 2026-11-11
---

# Lane 2 - landed since 2026-11-03 vs what actually runs

Scope: 133 commits on origin/main since 2026-11-03 (HEAD 1ddb70951). The live checkout is on main with 0 commits behind, and its HEAD file reads 1ddb70951. zoe-data pid 1899714 started 2026-11-11 20:50:54 local, 32 s after the last merge (20:50:22). Every merged zoe-data change is therefore loaded [Proven][live].

Method: flag defaults come from `services/zoe-data/flag_registry.py` (file:line below) plus the reading module. Live values come from the running process env (scratchpad live_flags.txt, names and modes only). Execution evidence is marker counts in `~/.zoe-logs/zoe-data.app.log{,.1,.2,.3}` (2026-11-01 to 2026-11-11 22:30), `night-window.log`, and Postgres `SELECT count(*)`. Markers are split into "synthetic" and "real". Synthetic means the line carries `traffic=harness`, or a user on the synthetic allowlist or a `demo_bar`/`bar`/`test` user. Everything else is counted as real. The split is [Experimental], because a QA session run under a real member id still counts as real. Real household volume is small: the nightly self-review counted 78 household turns for 2026-11-09 and 19 for 2026-11-10 (night-window.log lines 2453, 2549).

Verdict key: LIVE-EXECUTING (on, with markers or rows from real turns) / LIVE-SHADOW (runs, writes logs or rows only) / ON-0-RUNS (flag on, zero markers) / MERGED-DARK / OFFLINE (built as an instrument, not runtime) / NOT-BUILT / PANEL-NOT-DEPLOYED.

## 1. Roadmap items 1-23 (initiative section 6)

| # | Item | PR(s) | Flag, code default | Live | Execution evidence | Verdict |
|---|---|---|---|---|---|---|
| 1a | Recall gate shadow to enforce | #2293 (replay record), #2303 (opt-in trace) | ZOE_RECALL_GATE, default `shadow` (flag_registry.py:802) | unset, so shadow | RECALL_GATE: 357 lines; 346 synthetic, 11 real (1 user, last 2026-11-10); `would_add` is `-` on almost every line [live] | LIVE-SHADOW. Enforce not started; the replay said "do not enforce yet" [record] |
| 1b | Voice history 3 to 8 rows | #2292 | ZOE_BRAIN_KEEP_TURNS, default 8 (flag_registry.py:141; labs/flue-zoe-brain-2x/src/context-window.ts) | unset, so 8 | flue-zoe-brain-2x runs `dist/` built 2026-11-10 15:01, after the last flue src change (2026-11-09 23:04Z); the built bundle contains `DEFAULT_KEEP_TURNS = 8` and the env var is absent from the process [live]. No per-turn marker | LIVE-EXECUTING [Proven deployed, unmeasured per turn] |
| 2 | Night-mind readers to serve | #2268, #2265 | ZOE_NIGHT_MIND, default `off` (flag_registry.py:587; night_mind.py:194-202) | `serve` (zoe-data and the night window .env) | WRITE side: night_runs `ok` on 11-04, 11-05, 11-07, 11-10 and 11-11; night_observations 90 rows, 16 dated since 11-03 [live]. SERVE side: routers/memories.py:1209 adds `night_notes` to the packet but logs nothing at info level, so there are 0 serve markers [code] | LIVE-EXECUTING (write). Serving is unmeasured, which counts as zero |
| 3 | Duck-decide-resume lab; addressedness and closing detector on | none since 11-03 (#2280 only adds `barge_policy_sim.py`) | panel BARGE_DUCK_ENABLED, default false (scripts/setup/zoe_voice_daemon.py:559) | not in the panel daemon's process env (grep count 0) [live] | lab not run; no record | MERGED-DARK (pre-existing code) |
| 4 | One voice for every tier | #2271 | none | always on | deployed; no distinctive marker found | LIVE-EXECUTING [code]; unmarked |
| 5 | Coverage instrument | #2269 (coverage line), #2295 (bar cells) | ZOE_RECALL_EVERY_TURN, default `off` (flag_registry.py:799) | `shadow` | RECALL_EVERY_TURN mode=shadow: 84 lines, all real, 2 users, 2026-11-08 to 2026-11-11 (14 / 58 / 9 / 3 per day) [live] | LIVE-SHADOW |
| 6 | Hygiene: boot KV warm, backend default flue, unread flags, retired lanes | #2270, #2265, #2266, #2264, #2272, #2284, #2273 | ZOE_BRAIN_BACKEND default `flue` (flag_registry.py:134) | `flue` is set explicitly, so the new default is not what decides it | code removals deployed; "45 unread env vars" not checked | LIVE-EXECUTING (removals) |
| 7 | Now card | #2281 | ZOE_MEMORY_NOW_CARD, default `off` (flag_registry.py:525) | unset | now_cards 0 rows; NOW_CARD / NOW_CARD_BUILD markers 0 [live] | MERGED-DARK |
| 8 | Outcome edge + first-presence re-score | #2286 | ZOE_OUTCOME_EDGES, default `off` (flag_registry.py:692; proactive/outcome_edges.py:66) | `on` | proactive_outcome_edges 8 rows: 7 kind=day with no outcome, 1 open_loop outcome=unknown (ledger_sweep). OUTCOME_EDGE: 1 real line (11-11). OUTCOME_RESCORE: 6 lines, all `traffic=harness` [live] | LIVE-EXECUTING, but thin: no resolved outcome yet |
| 9 | Smart Turn v3.2 on the Pi | #2280 (offline eval only) | none on the panel | n/a | eval verdict "NOT YET": every policy that saves 300 ms or more cuts speakers off 1.7-3.2x more often (docs/knowledge/smart-turn-offline-eval-2026-11-04.md) [record]; no Smart Turn code in zoe_voice_daemon.py | NOT-BUILT (at the wall) |
| 10 | HA presence reader + mmWave | #2267 | ZOE_HA_PRESENCE, default `off` (flag_registry.py:363) | `shadow` | `ha_presence_poll` registered; presence_observations 188 rows from 2026-11-07 to 2026-11-11 across 2 entities, ALL state=`unknown`; logs show 188 "-> unknown" lines [live]. No mmWave | LIVE-SHADOW, but it observes no signal |
| 11 | Proactive policy log-only | #2283 | ZOE_PROACTIVE_POLICY_LOG, default `''`/off (flag_registry.py:762; proactive/policy_log.py:42) | `log` | PROACTIVE_POLICY / _PASS markers: 0 ever [live]. The only call site is the orb poll with pending items (proactive/pull.py:309) | ON-0-RUNS |
| 12 | pass^k + collateral damage; 23-cell baselines | #2278, #2321, #2295, #2282 | n/a | n/a | offline bar tooling [code] | OFFLINE |
| 13 | Self-evolution Phase 1 (GEPA on manner.md) | #2304 | unit template `zoe-self-improver.service`, not installed (no user unit) [live] | n/a | ~/.zoe/self-improver/nights.jsonl: 2 daytime dry runs (11-05, 11-07; the second found "no admissible candidate beat the sampling control"); state.json measured_nights=0 | MERGED-DARK |
| 14 | Capability-gap backlog | #2294 | none | n/a | `scripts/maintenance/capability_gap_backlog.py` is not scheduled; no backlog file in ~/.zoe; the module is only imported by self_review.py:802 [code][live] | MERGED-DARK (offline tool) |
| 15 | Memory views over pointers | #2301, #2300 (probe) | ZOE_MEMORY_EPISODES false (:513), ZOE_MEMORY_SLOT_CHECK off (:530), ZOE_DORMANT_THREADS off (:281), ZOE_SPEAKABLE_PROVENANCE false (:897) | EPISODES=1, SLOT_CHECK=shadow, DORMANT=shadow, PROVENANCE unset | EPISODES and DORMANT have NO runtime caller: only memory_views.py uses them, and only the offline scripts/maintenance/memory_views_report.py calls that, so 0 markers [code]. SLOT_CHECK sits in the packet path (routers/memories.py:1154) but logs only when it finds a conflict: 0 lines [live] | LIVE-SHADOW (slot check, silent); 2 "on" flags have no runtime effect |
| 16 | Identity layers: per-member register + drift battery | #2297, #2282 | ZOE_MEMBER_REGISTER, default `off` (flag_registry.py:505) | `on` | MEMBER_REGISTER: 21 lines, 1 real user, chars=59 every time; member_register table has 0 rows [live]. The 59 chars are only `situational_line()` (member_register.py:164), so no per-member register is stored or served. Drift battery is offline | LIVE-EXECUTING with an empty payload |
| 17 | Household problem-solving loop | #2298 | ZOE_PROBLEM_LOOP, default `off` (flag_registry.py:770) | `shadow` | AUTHORITY_SHADOW: 25 lines, 24 synthetic, 1 real (11-07); PROBLEM_LOOP markers 0; problem_loop.py:16 says nothing in the live path calls the loop [code] | LIVE-SHADOW (authority table only) |
| 18 | Harness out of the household process; retired-runtime slice 2; cut list | #2272, #2284, #2273 | n/a | n/a | slices landed. zoe-data's scheduler still registers 8 `multica_autopilot_*` jobs (ground truth) [live] | PARTIAL |
| 19 | Turn-act micro-classifier on the 270M router | none | | | | NOT-BUILT |
| 20 | Slot save/restore, daytime background reasoning | #2296 (scheduler, log-only) | ZOE_BRAIN_SCHEDULER, default `log` (flag_registry.py:150) | unset, so log | BRAIN_SCHED mode=log: 3395 lines; `idle ... would_defer` 263 [live] | NOT-BUILT (slot restore). The scheduler choke point is LIVE-SHADOW |
| 21 | Gemma hears the flagged turn (audio encoder) | none | | | | NOT-BUILT |
| 22 | Style-only adapters | none | | | | NOT-BUILT |
| 23 | Audio-event labels; multi-room arbitration | none | | | | NOT-BUILT |

Tally (primary verdict): LIVE-EXECUTING 5 (2, 4, 6, 8, 16; items 8 and 16 thin or empty, item 1b also deployed). LIVE-SHADOW 5 (1, 5, 10, 15, 17). ON-0-RUNS 1 (11). MERGED-DARK 4 (3, 7, 13, 14). OFFLINE 1 (12). PARTIAL 1 (18). NOT-BUILT 6 (9, 19-23). No roadmap item is PANEL-NOT-DEPLOYED.

## 2. The eight first experiments (section 8)

| # | Experiment | Status | Evidence | Verdict |
|---|---|---|---|---|
| 1 | Recall gate replay | done 2026-11-05 (#2293); trace #2303 is opt-in and unset | record: synthetic add precision 0.63 on n=24; "do not enforce yet" [record] | DONE, failed the 0.8 bar |
| 2 | Baseline the loop that exists (D E1) | no record found | outcome edges 8 rows with 0 resolved outcomes, well under n=20 [live] | NOT RUN |
| 3 | Endpoint arbiter offline | done (#2280) | "NOT YET": more cut-offs than today [record] | DONE, failed |
| 4 | Duck-decide-resume lab | not run | BARGE_DUCK_ENABLED absent at the panel [live] | NOT RUN |
| 5 | Now card shadow | not started | flag unset, 0 rows [live] | NOT RUN |
| 6 | One voice for every tier | shipped (#2271) | no owner blind test recorded (grep "blind test" finds only older docs) [record] | SHIPPED, success test not run |
| 7 | Proactive policy log-only | flag `log` | 0 markers [live] | RUNNING WITH ZERO DATA |
| 8 | GEPA improver | 2 daytime dry runs | 0 of 14 measured nights; unit not installed [live] | NOT RUNNING |

## 3. The panel

- Build 5bfd9c1344b4 is #2359, "fix(voice): first turn - no wake chime under the first words", merged 2026-11-09. The Pi's BUILD.json says it was deployed 2026-11-09T05:29:46Z, and `zoe-voice` is `active` (read-only ssh) [Proven][live].
- The ground truth reports the panel's daemon sha `df075387f6f3` as "DIFFERS from checkout". That sha is exactly `sha256(zoe_voice_daemon.py)` at 5bfd9c134 [Proven][code]. So the panel runs #2359 as merged, with no hand edits.
- These ARE running at the wall: #2315 (cut-off fixes), #2337 (speaker ID never dies silently), #2359 (wake chime off by default; WAKE_CHIME_ON_WAKE default false, daemon line 591), named guests 1/4 (#2339) and 2/4 (#2341).
- PANEL-NOT-DEPLOYED: only #2343 (named guests 4/4: upload the unmatched-voice embedding when the server asks) and #2346 (5/5: `voice_clip_play` clip playback). That is 36 added lines in total (`git diff 5bfd9c134 origin/main -- scripts/setup/zoe_voice_daemon.py`). Both are inert while ZOE_GUEST_VOICE_LEARN and ZOE_NAMED_GUESTS are off server-side, so the gap does not matter until those flags flip. No other panel-shipped file (enroll flow, announce, unit, deploy script) changed after 5bfd9c134.
- Smart Turn: the panel daemon has no Smart Turn code. `ZOE_SMART_TURN_ENABLED=1` in zoe-data is a July flag (255e825e9, 2026-07-06). It gates Smart Turn only on zoe-data's LiveKit barge-in (Silero) path: routers/voice_livekit.py:159-164 and the call at :1001. Its default is false (flag_registry.py:891). The wall panel talks to zoe-data through its own daemon, not LiveKit. In 7 days of logs, voice_livekit's only lines are 132 "LiveKit on-demand mode" boot lines and 0 smart-turn lines. Those smart-turn lines are debug level, so a zero there is weak evidence; still, I found no LiveKit session evidence at all. Verdict: the flag is effectively inert for the household [Proven code][Experimental live]. #2280 did not ship any runtime Smart Turn.
- Barge duck-decide-resume is off at the panel (item 3).

## 4. Other fix waves (one line each)

- QA round 4 (#2347), round 5 (#2344, #2345, #2340), round 6a/6b (#2350, #2351, #2355), round 7 (#2367, #2368): unflagged prompt, router and memory code; deployed. LIVE (no per-fix marker) [code+live checkout].
- Trust / write-claim guards (#2287, #2312): unflagged. WRITE_CLAIM_BLOCKED 29 lines and WRITE_CLAIM_CORRECTION 16 lines, ALL synthetic; 0 real firings [live]. LIVE, no household firing observed.
- Medical safety floor (#2356, #2366): unflagged. MEDICAL_SAFETY 15 lines, all bar or qa sessions; 0 household firings [live]. LIVE.
- Minor-affect safety cells (#2302): unflagged code plus measured cells [code]. LIVE.
- Forget fixes (#2275, #2277, #2279, #2300, #2308, #2329, #2338, #2340, #2367): unflagged. MEMORY_FORGET_SYNTHETIC lines are bar-only [live]. LIVE.
- Honesty doctrine (#2309), warmth (#2326): prompt-side LIVE. ZOE_EMOTIONAL_TURN_GUARD (default off, :291) is `on` live, but all 22 markers are synthetic `mode=shadow` from 11-07/11-08, and there are 0 lines since. ON-0-RUNS since it was flipped.
- Named guests 1/4-5/5 + backfill (#2339, #2341, #2342, #2343, #2346, #2357): ZOE_NAMED_GUESTS, ZOE_GUEST_VOICE_LEARN and ZOE_NAMED_GUEST_MEMORY are all default off (:576, :351, :578) and unset; named_guest_turns 0 rows; 0 NAMED_GUEST markers. MERGED-DARK; the panel half of 4/4 and 5/5 is also PANEL-NOT-DEPLOYED.
- Lists by voice + share (#2402, merged 2026-11-11): ZOE_VOICE_LIST_SHARE default true (:1015); no list created since the merge (lists rows dated 11-05, 11-07, 11-08 only) [live]. LIVE, unexercised. #2320 (named list stays that list): LIVE.
- Alarms (#2323, #2290, UI #2317, #2319): ZOE_ALARMS default false (:98), `on` live. alarms 0 rows, alarm_events 0 [live]. "alarm tick failed" fired 125 times (10 on 11-05, 115 on 11-06), with 0 since 11-06 [live]. ON-0-RUNS: the ring engine runs, but no alarm has ever been set.
- Night orchestrator (#2362, merged 20:04 today): the presleep catch-up ran at 20:32 (backup ok 36 s, memory export ok 3 s) [live]. The first full window under it is tonight at 02:30. LIVE (presleep phase only so far).
- Omi night re-pass (#2358): ZOE_OMI_NIGHT_REPASS default off (:650), absent from the night .env; no window has run a repass job. MERGED-DARK.
- extract_prepass (#2352): ZOE_NIGHT_EXTRACT default off (:581), not set. MERGED-DARK.
- Transcript check (#2365, #2406): ZOE_TRANSCRIPT_CHECK default off (:966), unset; 0 markers. MERGED-DARK.
- Conversation notes (#2360): default off (:215), unset; conversation_notes 0 rows. MERGED-DARK.
- Self-review (#2333): ZOE_SELF_REVIEW=shadow (zoe-data and night .env). It ran in the 11-10 and 11-11 windows, both `idle`: "78 / 19 household turns, 0 flagged, no model call due". LIVE-SHADOW, but it has produced nothing yet.
- Self-evolution improver (#2304): MERGED-DARK (item 13).
- Capability gap (#2294): MERGED-DARK, offline (item 14).
- Anticipation (#2322): ZOE_ANTICIPATION=shadow; 0 ANTICIPATION markers [live]. ON-0-RUNS.
- Session bridge (#2305, ZOE_VOICE_SESSION_BRIDGE default false :1032), person profile answers (#2262, :722), caregiver curation (#2158, #2159; ZOE_MEMORIES_CURATORS :507; memory_curation_audit 0 rows), device mishear guard (#2316, :252), Kokoro idle trim (#2348, default 0 :434): all unset. MERGED-DARK.
- Brain `--cache-ram 512` (#2348): the live llama-server process args show `--cache-ram 512` [live]. LIVE.
- ORT arena off (#2349), routing small fixes (#2313, #2306, #2328, #2330): unflagged. LIVE.
- Struggle detector (pre-existing, default shadow :928): 18 STRUGGLE lines (real), struggle_events 13 rows since 11-03, all mode=shadow; struggle_proposals 0 rows; turn_outcomes 0 rows [live]. LIVE-SHADOW.
- Night windows, last 7 nights (night-window.log, journal):
  - 11-05: ok
  - 11-06: refused (no lever set fits)
  - 11-07: ok
  - 11-08: ok, but night_mind idle and digest degraded
  - 11-09: NO window ran (only zoe-backup at 02:35)
  - 11-10: night_mind ok and self_review idle, then the service was Terminated (status 143) at 03:08 with no summary or report
  - 11-11: ok (36.9 min; digest degraded on the 12B, ok on the 4B)
  - No window has listed extract_prepass, omi_repass, conversation_notes or now_card as a job.

## 5. Changes in live behaviour without a flag (item 5 of the brief)

- History 3 to 8 (#2292): deployed in the flue dist (2026-11-10 15:01 build contains DEFAULT_KEEP_TURNS = 8; env unset) [Proven live]. No per-turn marker.
- Backend default flue (#2270): live sets ZOE_BRAIN_BACKEND=flue explicitly, so the changed default is NOT what selects the lane [Proven live]. The boot KV warm removal has no distinctive log string to count (could not verify).
- One voice phrase set (#2271): deployed (zoe-data restarted 2026-11-11 20:50 on HEAD 1ddb70951). No unique log marker found [Proven deployed, unmeasured].

## 6. Docs lag (stale claims; not fixed)

- docs/PLANS.md:20 - the live-flag block is dated 2026-10-18 / re-read 2026-11-02, and says `ZOE_ROUTER_SHADOW_TEXT=1` is still set. Live it is `0`.
- docs/PLANS.md:25 - "until the owner sets it ... the readers stay off and nothing it wrote is served". Live is ZOE_NIGHT_MIND=serve.
- docs/PLANS.md:26 - lists "`ZOE_NIGHT_MIND` readers" under "Still dark / off". Stale.
- docs/PLANS.md:21-24 - the ON / enforce / shadow lists omit flags that are now set:
  - on: ZOE_OUTCOME_EDGES, ZOE_MEMBER_REGISTER, ZOE_MANNER_BLOCK, ZOE_EMOTIONAL_TURN_GUARD, ZOE_ALARMS, ZOE_MEMORY_EPISODES=1
  - shadow: ZOE_RECALL_EVERY_TURN, ZOE_MEMORY_SLOT_CHECK, ZOE_DORMANT_THREADS, ZOE_HA_PRESENCE, ZOE_ANTICIPATION, ZOE_PROBLEM_LOOP, ZOE_SELF_REVIEW
  - log: ZOE_PROACTIVE_POLICY_LOG
- docs/PLANS.md:33 - member register "Built (flag-dark)". Live is `on`.
- docs/PLANS.md:37 - "`ZOE_PROBLEM_LOOP` default off; next = owner sets `shadow`". Live is already shadow.
- docs/PLANS.md:38 - warmth pass says `ZOE_EMOTIONAL_TURN_GUARD`, default off. Live is `on`.
- docs/ROADMAP.md:37-39 - "zoe-data's readers are still off because the flag is not set". Live is serve.
- docs/CANONICAL.md:114 - the llama-server flags line says `--cache-ram 1024`. The live process and the tracked unit (llama-server.service:165) use 512 since #2348.
- Item 15 doc framing (PLANS / memory-views.md): ZOE_MEMORY_EPISODES=1 and ZOE_DORMANT_THREADS=shadow are set live but have no runtime caller. Nothing in any doc says that a flip does nothing.

## 7. Could not verify

- Whether night-mind quotes are actually served on real turns: `night_notes` is not logged (routers/memories.py:1221).
- When each flag was flipped live (no flag-change audit read), so the "since live" windows are the whole log window.
- The source of the 21 MEMBER_REGISTER lines is inferred to be `situational_line` only (member_register table 0 rows, chars constant at 59). This is inferred, not traced per turn.
- The real vs synthetic split for lines without a traffic tag (MANNER_BLOCK 194, RECALL_EVERY_TURN 84) rests on the user id alone [Experimental].
- Why no night window ran on 2026-11-09, and what sent SIGTERM on 2026-11-10 03:08.
- 132 "LiveKit on-demand mode" lines in 7 days suggest frequent zoe-data restarts (deploys); not counted against systemd.
- The nightly voice gate on main failing STT speed (795 ms vs 488 ms baseline) was reported by ground truth; its cause was not investigated in this lane.
- Addressedness and closing-detector status (item 3) and the "45 unread env vars" removal (item 6) were not checked.
