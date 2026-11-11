---
type: research-lane
title: Lane 5 - brain budget (daytime slot, night window, hardware dreams)
date: 2026-11-11
---

# Lane 5 - brain budget

Read-only lane, worked 2026-11-11 ~22:30 AWST. No slot-taking request, no flag, no restart, no model load,
no pytest. Every number is a count/timing; no prompt text was printed or copied (the journal lines used
are numeric `print_timing` / `release` / `get_availabl` lines only). Device: Jetson Orin NX 16 GB "Super"
(`/proc/device-tree/model`) [live].

Labels: [Proven]/[Experimental]/[Speculative] x [code]/[live]/[record]/[contradicted].

## 0. Answer first

Reference note: every `src/...`, `server-context.cpp` and `server-task.cpp` file:line in this lane is third-party llama.cpp source
(the installed b11982 checkout), not a file in this repository.

1. **Daytime 4B outside a turn is real, small, and measurably costly.** Since #2296 went live
   (first `BRAIN_SCHED` line 2026-11-07 02:57) the choke point logged **273 `would_defer` calls**: 263
   `memory_digest._facts_call` and 10 `proactive.composer.compose_message`. After a daytime idle call, the
   next household-sized turn paid a **full re-prefill (>= 3,500 tokens, median ~6.0 s) 15% of the time
   (23/152)** vs a **3.9% base rate (124/3,157)** [Experimental][live, heuristic join, section 1d].
2. **b11982 offers nothing new that fixes it.** `--slot-save-path` is NOT set (no save/restore endpoints
   live); `--cache-ram 512` + `--cache-idle-slots` (default on) is the only mechanism, and saved state
   is SWA-truncated in b11982 exactly as the 2026-10-29 record found for b11932
   (`src/llama-kv-cache.cpp:2080`) [Proven][code]. Save/restore stays rejected.
3. **The night window is not the binding constraint; reliability and input are.** Last 8 nights: 5
   completed, 1 refused (no lever set fit), 1 never ran, 1 killed by SIGTERM mid-job. On completed
   nights the 12B worked **3.9-5.6 min** of a **65 min** cap, except 11-11 (35.1 min, 25 min of it
   one `omi_day_review` over 90k prompt tokens). 12B decode is now **6.1-6.2 tok/s** (lane D's 3.62 is
   stale [contradicted]).
4. **Of the shadow jobs, only self-review has actually run in a night window** (2 nights, 0 calls, 0 flagged).
   Extract pre-pass, omi re-pass and the orchestrator's pre-sleep phase have not yet run in a real night (the omi re-pass
   rehearsal today failed in 5 s with a setup error); the self-evolution improver has **0 of 14
   measured nights** (two daytime dry-runs: 1 win, 1 no-win; it runs on a hosted model, not the box).
5. **Highest-value unspent minute:** a reliable night (3 of 8 lost) plus moving the 263 daytime
   `_facts_call`s into the ~55 idle minutes. Daytime cognition is worth having only as
   *event-triggered, sub-second-visible* work; nothing measured today needs 8 s of 4B at 10:00.

## 1. Slot save/restore and daytime cognition

### 1a. Live llama-server (ps, flags only) [Proven][live]

`ps -eo args` for the brain (PID up 2h09m at read, restarted 20:26 by the omi re-pass rehearsal restore):

| flag | value |
|---|---|
| binary | `~/llama.cpp-b11982/build-jetson/bin/llama-server` (props `build_info b1-8a199ad`, checkout 2026-10-29) |
| `--parallel` | 1 (`/props total_slots 1`) |
| `--ctx-size` | 8192 |
| `--cache-ram` | **512** (#2348 is applied live) |
| `--slot-save-path` | **absent** -> `/slots?action=save/restore` returns "not supported" (`server-context.cpp:5836`) |
| KV | `--cache-type-k/v q8_0`, `--swa-full`, `--flash-attn on` |
| MTP | `--spec-type draft-mtp --spec-draft-n-max 4 --spec-draft-p-min 0.6` |
| other | `--metrics`, `--load-mode mmap+mlock`, `--reasoning off`, no `--mmproj` (`/props modalities` all false) |
| router | separate `llama.cpp/build-jetson-new` 270M on :11436, ctx 1024, `--cache-ram 64`, CPU |

`free -m`: MemAvailable **1,792 MiB**, swap used 1,805 MiB [live].

### 1b. /slots, /metrics (GET only) [Proven][live]

- `/slots`: one slot, idle, `n_prompt_tokens 4592` resident, `speculative true`.
- `/metrics` since the 20:26 restart (~2 h): prompt tokens evaluated 10,911 vs cached **100,258** ->
  **reuse 90.2%** (the #2348 baseline was 85.3% at 1024 MiB) [live, 2 h sample, small].
  `n_tokens_max 5765`. MTP: 161 accepted / 382 drafted = 0.42 per draft token. `requests_deferred 0`.
- `scripts/maintenance/brain_cache_report.py --since "2026-11-11 20:25"`: 24 tasks, 4 re-prefilled
  >= 1000 tokens, 0 host-cache evictions.

### 1c. Choke-point counts (#2296) [Proven][live]

Source: `grep BRAIN_SCHED ~/.zoe-logs/zoe-data.app.log{.2,.1,}` (2026-11-07 02:57 to 11-11 22:30).

| caller | class | decision | count |
|---|---|---|---|
| person_extractor_llm | post_turn | allowed | 901 |
| turn_digest | post_turn | allowed | 874 |
| latent_intent_detector | post_turn | allowed | 842 |
| **memory_digest._facts_call** | **idle** | **would_defer** | **263** |
| structural_reader | post_turn | allowed | 150 |
| prefix_keeper | exception | allowed | 98 |
| structural_verifier | post_turn | allowed | 96 |
| memory_digest._is_contradiction | idle | allowed (night) | 64 |
| quote_retire_judge | post_turn | allowed | 56 |
| memory_digest._facts_call / _emotional_call | idle | allowed (night) | 16 / 15 |
| wake_prewarm | exception | allowed | 10 |
| **proactive.composer.compose_message** | **idle** | **would_defer** | **10** |

- Per day (idle class): 11-07 44, 11-08 189, 11-09 126, 11-10 4, 11-11 5. The 11-08/11-09 spikes sit
  on days with ~1,300 post-turn helper calls each (QA-chat rounds), so idle consolidation scales with
  conversation volume, not the clock [live].
- `would_defer` by hour: 08h 59, 14h 79, 21h 57, 19h 23, 15h 18, 20h 17, 13h 10, 07h 5, 12h 3, 10h 2.
- Mode is `log` (default, `brain_scheduler.py:23,104`); nothing was refused.
- `_facts_call` reaches the 4B by day through idle consolidation (`memory_idle_consolidation.py:342`
  imports `_extract_facts_with_gemma`) [code; path inferred from import, not traced at runtime].

### 1d. Did the next household turn pay? [Experimental][live]

Method (`scratchpad/tools/full.py`, journal `-o short-iso` since 11-07 03:00, joined with the
`BRAIN_SCHED` timestamps). "Household-sized turn" = task with `n_tokens >= 3500` (the panel prefix is
~4.5k); this heuristic will also catch any large aux prompt. "Full re-prefill" = `prompt eval` >= 3,500
tokens.

| measure | value |
|---|---|
| tasks with n_tokens >= 3500 since 11-07 | 3,157 |
| full re-prefills among them | 124 (**3.9%**), median **6,026 ms**, p90 7,730 ms |
| ... first task after a brain restart | 13 |
| ... preceded by a daytime idle 4B call | 26 |
| ... preceded by a post-turn/exception helper only | 29 |
| ... no logged background call in between | ~56 (cause not established: prompt-shape divergence is the record's candidate) |
| daytime idle calls followed by a household-sized turn within 2 h | 152 |
| ... of which that turn re-prefilled >= 1,000 / >= 3,500 tokens | 26 / **23 (15%)** |
| idle-call task size (task right after the call) | median 546 tokens |

Reading: a daytime idle call roughly **quadruples** the odds that the next turn re-prefills fully, and
the cost when it does is ~6 s, larger than lane D's 3.6 s [contradicted: lane D used a ~2.3k
prefix; today's resident prompt is ~4.6k]. Amortised it is ~0.7 s per idle call. `prefix_keeper` (98
re-warms) already removes most helper damage: per brain process, a household turn right after an aux
task missed (>= 1000 tokens) 14/79, 5/17, 4/20 and 1/2 times, not far above turn-after-turn (38/405,
78/458, 27/194, 3/20) [live].

**Other measured facts:**
- Bucketed prompt-eval since 11-09 17:32 (all tasks): <300 tok n=2,189 mean 147 ms; 300-999 n=148
  909 ms; 1,000-1,999 n=120 1,995 ms; 2,000-3,499 n=22 4,017 ms; >=3,500 n=36 6,818 ms.
- `selected slot by LRU` 748 vs `by LCP similarity` 1,771 since 11-09 17:32; host-cache evictions
  (`removing oldest entry`) 123 since 11-06 [live].
- 11-09 is an outlier: **23 brain loads, 9 failed with "unable to allocate CUDA buffer"** (18 error
  lines since 11-04), the landing-heavy day [live].

### 1e. What b11982 actually offers (from the installed binary and source) [Proven][code]

| mechanism | b11982 fact | for Zoe |
|---|---|---|
| host prompt cache `--cache-ram N` | default 8192 MiB; live 512. On a new task with `f_keep < 0.5` or an LRU pick, the slot is saved and the best cached prompt loaded (`server-context.cpp:1860-1897`; `server-task.cpp:1838-1912`; candidates with `f_keep < 0.25` skipped) | this is what already restores the panel prefix after a helper |
| `--cache-idle-slots` | default on with cache-ram; saves idle slots at each task launch; clears them only with unified KV (`server-context.cpp:1663-1672, 3071-3083`) | harmless on 1 slot |
| `--slot-save-path` + `/slots/{id}?action=save/restore/erase` | present (`server-context.cpp:5836-5858, 6753`), not enabled live | same data as the host cache |
| SWA truncation | `llama_kv_cache::state_write` drops SWA-masked cells per sequence (`src/llama-kv-cache.cpp:2072-2083`); with `--swa-full` no SWA checkpoints are made (`server-context.cpp:4391-4405`) | a restored ~4.6k prompt resumes only if the new prompt diverges within the last ~512 positions; otherwise full re-prefill. Unchanged since the 2026-10-29 record |
| `--cache-reuse N` | KV shifting, default 0 | not equivalent to re-prefill; stays rejected |
| `--kv-unified-per-slot N` | new knob: per-slot ctx limit when `-kvu` | relevant only to a second slot |
| `--lazy-mode MODE` | exists; default `auto` = lazy only for tensors > 4 GiB | the 2026-10-29 record's RAM offset for a second slot is NOT on by default |
| `--sleep-idle-seconds` | exists, default off | irrelevant |
| hit logging | cache hits/loads use `SRV_TRC`/`SLT_TRC` (trace only) | hits stay invisible at default verbosity; misses are inferred |

Conclusion: no b11982 feature makes a deep-diverging daytime call free. The levers remain (i)
`prefix_keeper` re-warm (live), (ii) a second pinned slot (lab-planned in
`brain-slot-isolation-2026-10-29.md` section 6; RAM now 1.8 GB available, record said 0.8), (iii)
prefix-forked helpers (that record's section 3f), (iv) moving the work to night.

### 1f. What would Zoe DO with 8 s of 4B at 10:00?

Measured demand today is two callers: idle fact consolidation (263) and the proactive composer (10).
- **Fact consolidation** already has a post-turn path (`turn_digest` 874, `person_extractor_llm` 901)
  and a nightly path (12B digest). The daytime idle pass is a third copy; its only unique value is
  freshness between a turn and the night, which the post-turn passes already cover
  [Experimental][code+live counts].
- **Proactive composing** (10 calls in 5 days) is the one daytime job with a real user-visible
  outcome, and it is event-shaped (a calendar change, a reminder), so it can run as a *turn-adjacent*
  call (post-turn class) or be templated; bge-small + code pick *what* to say, the 270M can choose a
  tool, neither can write a natural sentence [Speculative].
- Things code + bge-small + the 270M cannot do and the 4B can: free-text synthesis (a sentence that
  joins two facts), contradiction judgement between two memories, summarising a long Omi segment.
  None of them must happen at 10:00: each is deadline-free until the person next speaks
  [Speculative].

**Verdict:** daytime cognition should be *pre-composition for the next likely turn* (e.g. a reply to
"what's on today?" after a calendar change), bounded to <= 600 prompt tokens, issued only post-turn or
inside the wake pre-warm, never on a timer. Everything else belongs to the night window.

## 2. The night window as the cognition budget

Source: `~/.zoe-logs/night-window.log` (2,581 lines), `~/.zoe/night-reports/*.json` (status fields
only), `journalctl --user -u zoe-night-window.service` [Proven][live].

| night | outcome | 12B healthy -> restore | 12B work | jobs (s on 12B) |
|---|---|---|---|---|
| 11-04 | ok | 02:51:37-02:55:32 (3.9 min) | 200 s | digest 15, dreaming 40, night_mind 105, omi_day_review 40 |
| 11-05 | ok | 02:51:09-02:55:44 (4.6) | 240 s | 15 / 40 / 60 / 125 |
| 11-06 | **refused**: "no lever set fits" (MemAvailable 9,109 after stops), restore NOT HEALTHY (router) | - | 0 | - |
| 11-07 | ok | 02:50:45-02:55:20 (4.6) | 240 s | 120 / 10 / 85 / 25 |
| 11-08 | ok, exit 5 | 02:52:11-02:57:46 (5.6) | 300 s | digest **degraded** 200, 10, night_mind idle, struggle 5, omi 80 |
| 11-09 | **no run** (timer re-started 05:00 on 11-09; no window line) | - | 0 | - |
| 11-10 | **killed** SIGTERM (status 143) at 03:08 during omi_day_review; no report | 02:51:23-03:08:18 (16.9) | ~1,000 s | digest **degraded** 435, 45, 30, 5, self_review idle 5, omi (killed) |
| 11-11 | ok | 02:51:32-03:26:38 (35.1) | 2,075 s | digest **degraded** 385 (4B fallback ok 25 s), 55, 105, 5, self_review idle 5, **omi 1,515 (90,252 prompt / 6,541 gen)** |

- **Completed 5 of 8**; the cap is 65 min (`night_window.py:422`). Median 12B work on a completed night is
  ~4 min; the window used **7-57%** of its cap [live].
- 12B speed: prefill 203-205 tok/s, steady decode 6.14-6.19 tok/s (11-10, 11-11) [live]; lane D's
  3.62 tok/s / 136 tok/s are stale [contradicted].
- Digest returned rc 3 (`degraded`, `night_window.py:1958`) on 3 of the last 3 runs where it ran with
  data; the 4B fallback finished it on 11-11 in 25 s [live].
- Brain-down time 11-11: 2,154 s (zoe-data 2,187 s) - the cost of the window is that Zoe is deaf for
  ~36 min when omi is heavy [live].

**Shadow jobs:**

| job | PR | runs in a real night so far | evidence |
|---|---|---|---|
| self-review | #2333 | 2 (11-10, 11-11), mode `shadow` | summaries: calls 0, flagged 0, turns_household 78 / 19; status `idle` both nights |
| extract pre-pass | #2352 | 0 | not in the 11-10/11-11 job lists; `~/.zoe/night-extract/` holds only `.lock` and `.erased.json` |
| omi re-pass | #2358 | 0 real; 1 rehearsal 11-11 20:25 **failed (rc 1) in 5 s**, `setup_error: RuntimeError` | `night-window/logs/20261111-202547-omi_repass-pre.log` |
| night orchestrator pre-sleep | #2362 | 0 real (timer now 02:30); catch-up ran 20:32: backup ok 36 s, export ok 3 s | `night-window.log:2580-2581` |
| self-evolution improver | #2304 | **0 of 14 measured nights**; `state.json`: status running, wins 0, losses 0, measured_nights 0; no timer installed (`systemctl --user list-timers`) | `nights.jsonl`: 2 daytime dry-runs (11-05 win, winner C2; 11-07 no win); the model is a hosted Claude model, not the box |

No win record vs the sampling control exists yet: the stop rule's clock has not started [live].

**Is the window binding?** No [Experimental]. On 4 of 5 completed nights ~60 min of the 65 were
unspent. The binding constraints are (a) **night reliability** (3/8 lost: one RAM-lever refusal plus restore
failure, one missed timer, one SIGTERM), (b) **inputs** (self-review 0 flagged twice, struggle
proposals 0 tokens, night_mind idle 2 nights), (c) **digest rc 3** on the 12B. Only omi-heavy days
(11-11) approach the cap.

**Highest-value unspent minute:** move the daytime `_facts_call` consolidation into the window and set
`ZOE_BRAIN_SCHEDULER=enforce` for that caller. Sizing: 263 calls in ~5 days = ~53/night at ~546 prompt
tokens (measured) and an assumed ~100 output tokens. On the 12B (204 / 6.2 tok/s) that is ~2.4 min
prefill + ~14 min decode = **~16 min/night**, which fits the unspent ~55 min. On the 4B after restore
(~600 / ~23 tok/s) it is ~4-5 min. Second: give self-review real input (a random sample of unflagged
turns), since it has spent 0 calls in 2 nights. [Speculative arithmetic from measured rates; the
output length is assumed.]

## 3. Honest hardware dreams

Today's RAM picture [live, `ps` grouped by role, RSS]: brain 5.6 GB, Kokoro 2.2 GB, zoe-data 1.5 GB,
router 0.6 GB, Serena 0.4 GB, an agent test run (pytest under `/tmp/zoe-test-lane.lock`) 0.4 GB,
postgres 0.2 GB, omnigent container 0.12 GB. The agent-session process tree (parent PID 5826,
"server", up 5 d 18 h) holds **62 processes, 1,753 MB RSS** (four Claude Code CLI processes 630, 333,
140, 107 MB) - about equal to the box's whole MemAvailable (1,792 MB). Swap used 1.8 GB.

### 3a. DGX Spark (128 GB unified) [Speculative]

Hardware facts only from the 2026-10-07 scout record (273 GB/s, $4,699) [record: unverified].
- **Changes:** RAM stops being the governor. Removes the night window's stop/start (11-06 refusal,
  11-02/11-09 CUDA allocation failures, 36 min of deafness on 11-11), allows a second slot and a resident 12B, and
  hosts the dev fleet without touching voice.
- **Does not change:** decode is bandwidth-bound; 12B on the Orin NX does 6.2 tok/s, and a
  273 / ~102 GB/s ratio suggests ~15-17 tok/s for the 12B on a Spark, still slower than today's 4B
  (23 tok/s per `/metrics`). A daytime 12B is a background thinker, not a voice brain. The 12B's
  output would still feed the same delivery gaps: digest rc 3, self-review with no input, memory
  delivery, follow-through and turn-taking are software and survive any box.
- **Rock note:** running a larger brain by day would require the owner to lift a rock.

### 3b. Audio-native brain (rock-adjacent) [Experimental]

- b11982 has `tools/mtmd/models/gemma4a.cpp` (audio encoder) and `--mmproj` [code]. Loading an mmproj
  disables only `ctx_shift` and `cache_reuse` (`server-context.cpp:1403-1412`); `can_speculate()` is
  just `!!spec` (`:561`), so the source does not forbid MTP + multimodal [code]. Whether drafting
  works across audio chunks at runtime is unverified [record: tone deep dive 2026-10-26 says the same].
- No E4B mmproj on disk (`~/models/gemma4-e4b-qat/` holds only the model + MTP draft) [live].
- Cost [record]: 25 audio tokens per second -> a 10 s turn adds ~250 prefill tokens (~0.4 s at ~600
  tok/s); projector RSS unmeasured. It cannot fit beside the live stack at 1.8 GB available.
- Keeping Moonshine stays correct: hearing tone is the only prize, and the LISTEN record shows
  audio-LLMs largely follow words, not prosody [record].

### 3c. Dev fleet off the box [Experimental]

Evidence that reliability tracks engineering activity: 11-09 (landing day) had 23 brain loads, 9 of them
failed with CUDA allocation errors; 11-10 and 11-11 had 1-2 loads each [live]. Right now agent sessions hold ~1.75
GB, i.e. the RAM a second slot (+238 MiB) or an mmproj would need several times over [live].
A correlation over one bad day is not causation [Experimental].

## Experiments

| # | hypothesis | procedure | pass | fail | effort | needs owner? |
|---|---|---|---|---|---|---|
| E1 | Daytime idle 4B calls cause most avoidable full re-prefills | Flip `ZOE_BRAIN_SCHEDULER=enforce` for 7 days (consolidation then runs post-turn/night only); re-run this lane's join (`full.py`) on the next 7 days vs the last 5 | full re-prefill share of household-sized turns falls from 3.9% to <= 3.0%; zero lost facts (night digest count of facts >= daytime baseline) | share unchanged (+/- 0.5 pp) -> the idle calls are not the cause; revert | 1 h + 7 days wait | yes (live flag) |
| E2 | A bounded daytime call (<= 600 prompt / 150 gen tokens, post-turn or wake-prewarm only, followed by `prefix_keeper`) is free for the next turn | Owner window 22:00-01:50, `night_busy.sh voice` clear, harness lock held; 40 interleaved pairs from corpus transcripts: A = turn, turn; B = turn, bounded call, keeper, turn. Read prompt-eval ms of turn 2 from the journal; also fire 10 turns 0.5 s after a call starts and log queue wait | B minus A p95 prompt-eval <= 300 ms; 0 full re-prefills in B; queue wait <= the call's remaining time and <= 2 s | any full re-prefill in B, or p95 delta > 300 ms, or wait > 2 s | 0.5 day script + 1 h window | yes (uses the live slot) |
| E3 | The night window can absorb the daytime consolidation | Run the idle-consolidation pass as a 4B-fallback job after restore for 3 nights (E1 on) | finishes in <= 5 min/night; window total stays <= 45 min; fact count matches the daytime path within 10% | > 10 min or fact loss | 0.5 day | yes |
| E4 | Night reliability, not cap, is binding | Fix and watch: omi re-pass setup error, digest rc 3, missed-timer cause; count completed nights over 14 | >= 13/14 completed, digest ok on 12B >= 12/14 | <= 11/14 | 1-2 days | partly (night runs) |
| E5 | MTP and the Gemma 4 audio encoder co-operate on b11982 (rock-adjacent) | Inside a night window after the 12B jobs, or an owner lab window with Kokoro + zoe-data stopped: lab llama-server on a lab port with the live model + MTP + an E4B mmproj; `/slots` shows `speculative true`; 20 text turns + 20 corpus clips (<= 10 s) | spec on; text draft acceptance within 10% of control; RSS delta <= 600 MB; added TTFT <= 400 ms per 10 s clip | server refuses MTP with mmproj, acceptance collapses, or RSS > 600 MB | 1 day (download + window) | yes (model download, RAM) |
| E6 | Box reliability tracks dev activity | 7 days with all agent sessions/tests/Serena off the Orin (remote host via SSH only), vs the prior 7 days | brain CUDA-alloc failures 0 (vs 9 in the window); night completed 7/7; MemAvailable p10 >= 3 GB | no change in failures/refusals | 0.5 day setup | yes |
| E7 | A Spark-class box removes RAM failures but not software gaps [Speculative] | Without buying: classify every night failure and brain reload of the last 30 days as RAM-caused or not | RAM-caused >= 70% -> the box helps; software-caused (digest rc 3, timer, idle inputs) listed separately as the survivors | RAM-caused < 50% -> hardware is not the lever | 2 h read-only | no |

## Could not verify

- Cache **hits** and host-cache loads (trace-level only in b11982); misses are inferred from
  `prompt eval` sizes.
- Which caller sent each journal task; the join of `BRAIN_SCHED` timestamps to journal tasks is
  heuristic ("household-sized" = `n_tokens >= 3500`).
- Whether any household turn queued behind an in-flight idle call (`requests_deferred` is 0 since the
  20:26 restart only; earlier processes not visible).
- Root cause of the 11-09 missed night (timer restarted 05:00) and the 11-10 SIGTERM at 03:08.
- Why ~56 full re-prefills had no logged background call (prompt-shape divergence is the record's
  candidate, not re-tested here).
- Live mode of `ZOE_SELF_REVIEW`, `ZOE_NIGHT_EXTRACT`, omi re-pass flags: not in `~/assistant/.env`;
  the self-review summary itself reports `mode: shadow`.
- MTP + mmproj at runtime; E4B mmproj size and RSS; any DGX Spark figure.
- `--cache-ram 512` vs 1024 effect: only a 2 h `/metrics` window on the current process (90.2%).
