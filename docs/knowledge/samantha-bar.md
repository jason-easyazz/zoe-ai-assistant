---
type: Reference
title: Samantha bar harness (samantha_bar.py v0)
description: The Samantha-quality regression gate. Eleven scripted multi-day memory and companion scenarios (S1–S8, S10–S12) run against throwaway demo users through the live API, plus the week-in-the-life day simulation (samantha_day_sim.py) that proves the whole knows-you chain for one user. Covers how to run them, what each scenario and ask proves, the scoring and judge, the baseline and teardown contracts, what a simulation can and cannot fake, and known limits.
tags: [memory, samantha, eval, regression-gate, harness, zoe-data]
timestamp: 2026-10-03T14:00:00Z
---

# Samantha bar harness (`scripts/perf/samantha_bar.py`, v0)

Tracker row: B3 "Samantha bar harness v0" in [the program](../architecture/beat-the-bar-2026-program.md).
Tests: `tests/unit/test_samantha_bar.py` (ci_safe, pure).

It is the memory and companion counterpart of the voice replay gate. Each run creates two
fresh users, `demo_bar_<8 hex>` A and B, and talks to the live zoe-data API as them
(`POST /api/chat/?stream=false` with `X-Internal-Token` + `X-Zoe-User-Id`). It scores
eleven scenarios (S1–S8, S10–S12; S9 lives in the [day simulation](#week-in-the-life-day-simulation-samantha_day_simpy))
and compares them against a baseline. A scenario is red only when it
passed before and does not pass now.

## Run

```bash
python3 scripts/perf/samantha_bar.py --dry-run                 # the plan; no network, no writes
ZOE_PERF=1 \
  flock /tmp/zoe-voice-harness.lock nice -n 5 \
  python3 scripts/perf/samantha_bar.py --compare-baseline      # exit 1 on a regression; exit 2 (refused) with no valid baseline
... --record-baseline      # this run becomes the bar (alone — never with --compare-baseline; refused if it errored or teardown is unproven)
... --samples 3            # judged scenarios ask 3x in fresh sessions, majority vote (odd only)
...                        # --compare-baseline inherits the BASELINE's count; an explicit mismatch is refused
... --no-backdate          # DIAGNOSTIC only (same-day run): refused with --record/--compare-baseline
... --teardown-only        # clean up a run that was killed before its own teardown
```

Teardown quiesces before the first forget on TWO signals per demo user: a stable `/for-prompt`
count (two consecutive equal, readable counts) and `capture-status in_flight == 0` (a turn
digest still computing would write a row after the forget and falsify the count-back). BOTH
must have been observed: if the counter is unavailable or still nonzero, or the packet count
was unreadable or never stabilised (`stable < 2`) when the wait ran out, cleanup still runs but
the teardown is reported unproven (`quiesce` in the artifact records `in_flight`, `packet`,
`stable` per user), the pending-teardown file is kept and the run is `error`.

Artifacts in `~/.cache/zoe/`:
- `samantha_bar_last.json`: the full evidence.
- `samantha_bar_trend.jsonl`: one line per run.
- `samantha_bar_baseline.json`: `revision` has the voice probe's shape (commit, tree,
  `dirty`, `clean_verified`) for the checkout the live service runs from; also the judge
  rubric sha and the per-scenario verdicts.
- `samantha_bar_pending_teardown.json`: exists only while a teardown is unproven.

`--compare-baseline` REFUSES before writing anything when the baseline is missing, unreadable,
malformed JSON, lacks a `scenarios` object, holds no / unrecognised verdicts, or carries a malformed `samples`
(must be a positive odd integer) (`load_baseline`) — a run that compares against nothing can never be red, so it would
otherwise report `ok`. The first run is `--record-baseline` alone; it overwrites a malformed
baseline. The two flags are mutually exclusive (argparse refuses): a regressed compare run
must never overwrite the bar with its degraded verdicts. Compare mode also uses the
baseline's `samples` (a majority-of-3 bar against one stochastic answer is not a comparison):
omit `--samples` to inherit it; an explicit different count is refused. `--no-backdate` is
refused in both baseline modes — S2/S4/S7 are multi-day scenarios, and a same-day run can
neither set nor clear the bar; the results file records `backdate`.

Exit codes: 0 = ran with no regression; 1 = regression; 2 = refused, error, or teardown not
proven; 3 = harness lock held. Without `ZOE_PERF=1` it prints a skip notice, exits 0 and
writes nothing. This follows the `scripts/perf/` convention.

**Gates.** A refusal is written to `samantha_bar_last.json` with `status: refused`. The run
refuses when:
- it is inside 01:45–03:15 local, or within 30 minutes of that window (the nightly jobs);
- a `deploy.yml` run is queued or in progress, or `gh` cannot say whether one is;
- the service `.env` does not give `ZOE_INTERNAL_TOKEN` and `POSTGRES_URL`.

It waits (up to 20 minutes) for `/readyz` to report `ready` with `memory_capture` ok. This
covers a store cutover. It also waits (up to 15 minutes) for MemAvailable ≥ 1.2 GB. It
takes the shared `/tmp/zoe-voice-harness.lock`, because it uses the brain slot, and it
raises its niceness to at least 5.

## Scenarios

The facts are synthetic and deliberately distinctive: Marisol from Lisbon, Dunedin then
Hobart, an aquarium interview, dad Teodor the lighthouse keeper. A real household fact
cannot satisfy a check by accident. Ambient system knowledge, such as the home location,
cannot answer "where do I live".

Day 1 runs first. The day-1 sessions are then backdated 26 hours in Postgres (the
`set_session_age` recipe from `samantha_live`, applied to the harness's own `bar-*` session
ids only). Day 2 follows.

| id | proves | scoring |
|---|---|---|
| S1 | A fact said in one session is recalled in a new session the same day. | deterministic: both `marisol` and `lisbon` |
| S2 | After a move, the current city wins and the old one is not asserted as current. | `hobart` is required. If `dunedin` is also mentioned, the judge decides "previous home" vs "still lives there". BOTH facts must have landed in the recall packet (Dunedin on day 1, Hobart on day 2) or S2 is ERROR — a Hobart-only reply with no Dunedin present tests no supersession. |
| S3 | Asked about something never said (the dentist), Zoe declines instead of inventing. | A clear decline with no named specific (`Dr X`, `dentist is X`, `it's X`) and no hedge ("I think", "perhaps", "maybe"…) is a deterministic pass. A decline that then guesses, and anything else, goes to the judge. |
| S4 | A day-1 worry is acknowledged on day 2, gently and not verbatim. | `interview` must appear, the reply must share fewer than 7 consecutive words with the day-1 sentence, and the judge must say "warm, in its own words" |
| S5 | With `ZOE_PROACTIVE_SELECTOR` on, an open turn raises the day-1 worry once and the next open turn does not. | Calls the internal selector hook for demo A (open-loop extraction + ranking), then two greeting turns in fresh sessions. PASS: turn 1 mentions interview/aquarium, turn 2 does not, and the carrying `proactive_candidates` row has `surfaced_count == 1`. No carrying candidate, no raise, or a second raise gives FAIL. Flag off or no route: the old `proactive_pending` read (no hook gives SKIP). |
| S6 | Demo B never sees demo A's facts. | Deterministic: no A needle may appear in B's reply or in B's `/for-prompt` packet. If A's own packet holds none of them, the result is SKIP, because the test would be vacuous. A packet read that FAILS (either user) is ERROR — a boundary that was not inspected is never certified. |
| S7 | A short duplicate ("my dad is Teodor") does not erase the richer fact. | The reply must name Teodor and lighthouse, and A's packet must still hold `lighthouse`. A failed packet read is ERROR. The duplicate's capture must be OBSERVED first: the harness waits on `/api/memories/capture-status` (the turn's background extraction + digest completed, nothing in flight, and `failed` did not advance — a memory pass that raised is completed-but-FAILED; bounded timeout) — not observed or failed = ERROR, never PASS. |
| S8 | S1 and S7 facts survive 32 filler turns spread over 3 sessions. | deterministic: `marisol` and `lighthouse`. ANY failed filler turn is ERROR, even when both names come back — the long history was not built, so the recall proves nothing. |
| S10 | A one-word change of state retires the old fact: "I gave up the cello." after "I play the cello in a community orchestra on Tuesday evenings." **Expected FAIL today — a target, not a regression.** | deterministic. Store first: a packet line still naming the orchestra without a stop cue is the old row served as current (superseded rows are hidden from reads) → FAIL. Then the reply must say they stopped. Why it fails: `memory_supersede.same_topic` needs the new fact to cover ≥ 0.5 of the OLD fact's topic words; "gave up the cello" shares only `cello` with {play, cello, community, orchestra}. The capture of the change turn is observed (`wait_captured`) and the day-1 backdate is a precondition. |
| S11 | Ask-to-remember: when a task would benefit, Zoe asks for a reusable preference. **Expected SKIP — not built.** | No turns. A reserved SKIP so the gap stays visible (zoe-data and the Flue sidecar have no such behaviour; the only "remember" prompt is `remember_fact`'s empty-argument reply). |
| S12 | Raise spacing: of S5's two open turns, minutes apart, the second carries no raise of ANY candidate. | deterministic, no extra turn: `proactive_candidates.last_surfaced_session` read after S5. A candidate surfaced in the second session = FAIL (the selector's cooldown is per candidate, so with ≥ 2 candidates the next one opens the next conversation); nothing raised in the first = SKIP; S5 setup not exercised = ERROR. |

`EXPECTED` marks S10 (FAIL) and S11 (SKIP) as targets: the result line and the artifact carry
`expected`, and `--compare-baseline` is unchanged (only a previous PASS can regress), so a
target turning PASS is an improvement to lock in by re-recording. A baseline recorded before
2026-10-03 has no S10–S12 — they appear under `new` and cannot regress until the next
`--record-baseline`. No judge rubric changed (S10–S12 are deterministic), so the rubric sha
and its pin are unchanged.

The judge is the brain itself: llama-server `:11434` `/v1/chat/completions` with temperature 0,
`top_k` 1 and seed 0. It gets a fixed system prompt and one rubric per judged scenario, and
must answer `VERDICT: PASS|FAIL` / `REASON: …`. Its verdict and reason are recorded, and an
unparseable answer is ERROR. The rubric text is pinned by sha256 in the tests. Editing it
changes what a pass means, so update the pin and re-record the baseline. `compare` notes a
rubric mismatch.

Several results count as not-pass: a brain-fallback reply, an HTTP error, and an empty reply
are all ERROR. So is an unexercised setup (`setup_problems`): a scenario whose seed turn
failed or whose seeded fact never landed in the recall packet is ERROR and its ask is not
even sent — S1 (sister), S2 (both homes), S4/S5 (the worry), S7 (rich dad + short duplicate),
S8 (S1 + S7 facts, plus any failed filler turn). The day-1 backdate is a precondition too:
`Live.backdate` re-reads every day-1 session (session row + ≥1 message at the backdated
timestamp) and the multi-day scenarios S2/S4/S5/S7 are ERROR unless all of them verified —
an attempted backdate is not a performed one. The fallback texts are the ones zoe-data actually serves when the brain did not
answer — `zoe_flue_client._FALLBACK_TEXT` ("Sorry, I had trouble reaching my brain just
now…", chat + voice) and `routers/voice_tts._FALLBACK_PHRASE` — pinned to those source
constants by `tests/unit/test_samantha_bar.py` AND re-read from the service checkout at run
time (`load_source_fallback_markers`), so a rewording cannot turn an outage into a reply. For the baseline, a previously passing
scenario that now reports SKIP or ERROR is a regression. **A skip is not a pass.**

Evidence never stores raw replies by default. It keeps the length, a 12-character sha,
latency, which synthetic needles were found, whether the seed fact "landed" in the recall
packet (and how long that took), and the judge's reason. `--keep-replies` adds 240-character
excerpts to the local results file for debugging.

## First baseline (2026-09-28)

Recorded **2026-09-28 16:33 AWST** with `--record-baseline --samples 3` against the live
service at commit `8ac726b7` (#1751, clean checkout, `clean_verified: true`), teardown
proven. The bar is `~/.cache/zoe/samantha_bar_baseline.json`.

| id | scenario | verdict |
|---|---|---|
| S1 | same-day recall across sessions | **FAIL** |
| S2 | newer fact wins (supersession) | PASS |
| S3 | decline when nothing was said | PASS |
| S4 | the emotional thread | **FAIL** (3/3 samples) |
| S5 | unprompted surfacing | SKIP (hook-gated; see Known limits) |
| S6 | user isolation | PASS |
| S7 | keep the richer fact | PASS |
| S8 | recall after a 32-turn history | PASS |

Only a previous PASS can regress, so S1 and S4 cannot turn a `--compare-baseline` run red
until each passes once and the bar is re-recorded.

**S1 diagnosis — a router misroute, not a memory failure.** The ask was routed by the
two-stage router to `calendar` and answered deterministically in 488 ms; the brain and the
recall packet were never consulted, so nothing in the store could have been recalled — and
the fact WAS there (`landed: true`, 5.6 s after the seed turn).
`~/.zoe-logs/zoe-data.app.log`, 2026-09-28 16:29:25 (`zoe.router_head_shadow`):

```
router_two_stage {"actual_routed": "calendar", "gated": false, "head_conf": 0.5371,
  "mode": "active", "shortlist": ["people", "calendar", "reminders"],
  "similarity_routed": "calendar", "two_stage_tool": "show_calendar", …}
```

**S4** — the day-1 worry (the interview) is not acknowledged on day 2 in any of the three
samples (`mentions_interview: false` on every sample; the worry had landed in the recall
packet, `landed: true`).

**S4 root cause + fix candidate (implemented, pending live compare).** On the Flue lane the
sidecar does not consume the per-turn memory packet, so continuity rested on the 4B calling
`recall_memory` (it under-fires) and on the seam recall floor (`ZOE_SEAM_RECALL_INJECT`),
which fires only on personal-QUESTION shapes. The day-2 ask is a mood STATEMENT, so nothing
carried the worry. Fix: a second trigger class in `zoe_flue_client.py` (`_CONTINUITY_RE`,
first-person emotional/state statements) injects the for-prompt packet composed in
`mode="continuity"` — facts captured in the last 72 h lead, emotional rows first, capped at
6 of 12 bullets / 1600 chars — plus one capped portrait line, in the recall block's wire
position. Flag `ZOE_SEAM_CONTINUITY_INJECT`, **default ON**, `false`/`0`/`off` is the kill
switch; each turn logs `SEAM_CONTINUITY user=… matched=… bullets=… chars=…` to
`~/.zoe-logs/zoe-data.app.log`. Acceptance: after deploy, `samantha_bar.py
--compare-baseline` shows S4 PASS and no other scenario regressing; then re-record the bar.
Only the two S4 turns in the whole harness script match the trigger (checked against every
`SAY_*`/`ASK_*`/`FILLER` line), so no other scenario's outbound message changes.

**S4 round 2 (#1756 live, still FAIL 3/3 — root cause two, fix candidate implemented).** The
round-1 injection fired live on every S4 ask (`SEAM_CONTINUITY … matched=True bullets=11`),
so the block was not missing; it did not work. A demo-user reproduction on the live path
showed three causes:
1. The per-turn digest (`memory_digest.run_turn_digest`) stored the worry as the neutral fact
   "User has a job interview at the aquarium on Friday." The feeling was gone (the old prompt
   gave that exact fact on 3 of 3 samples).
2. Because the fact was neutral, it did not rank as emotional. It fell outside the six
   `(recent)` pins, behind the dad and home facts.
3. A soft "connect if relevant" instruction over an 11-bullet block, placed before the user's
   words, did not make the 4B model check in.

Fixes:
- (a) The digest prompt now keeps a feeling the user stated.
- (b) The feeling is read from the user's own words at capture time (`extract_affect`) and
  stored beside the fact. It is rendered as `(recent, felt anxious)` and counted as emotional.
- (c) The block rides after the user's words and closes with one concrete ask about the top
  recent emotional item.

Variants against the reproduced crowded S4 flow (same seeds as the bar, backdated 26 h,
brain-as-judge with the bar's rubric):

| variant | S4 PASS |
|---|---|
| V0 prod as merged (live, end to end) | 0/3 |
| V0 prod block, sent to the sidecar directly | 0/3 |
| V1 (a) the fact keeps the feeling | 0/3 |
| V2 (a)+(b) plus the felt tag | 0/3 |
| **V3 (a)+(b)+(c), the implemented code** | **2/3, then 5/5** |
| V4 (b)+(c) with the digest still flat | 3/3 |
| V5 ask only, no bullets | 3/3, then 5/5 |
| V6 recent bullets plus the ask | 3/5 |

The new digest prompt gave "User is anxious about their job interview at the aquarium on
Friday." on 3 of 3 samples. (c) is what moves the needle; (a) and (b) are what let the
composer find the worry as the focus. `ZOE_SEAM_CONTINUITY_DEBUG` (default off, demo and test
ids only) logs the block and the reply for tracing.

Acceptance is unchanged: a post-deploy `--compare-baseline` must show S4 PASS with nothing
regressing.

**S4 round 3 (#1762 + #1763 live, 1/3 — root cause three, fixed).** The digest now kept the
feeling and the injection fired with a focus on every S4 ask, yet only sample 0 passed. A
demo-user reproduction of the crowded bar flow (S1 seed + ask, S2/S4/S7 seeds, 26 h backdate,
S2/S7 asks, then five back-to-back S4 asks end to end) gave the same shape twice: **1/5**,
sample 0 PASS, samples 1-4 FAIL. The composer's focus, captured before each sample, showed why:

1. **The focus flipped to today's mood.** The per-turn digest stores the S4 ask itself — "User
   has been feeling a bit on edge today." — seconds after sample 0. That row is recent,
   emotional and the NEWEST, so from sample 1 on it was the focus: "The user recently told
   you: User has been feeling a bit on edge today…", and the brain asked how being on edge
   was going (`zoe-data.app.log` for the live compare shows the same: `chars=1554` on sample 0,
   `chars=1163` after the 23:20:00 digest line). The bench in round 2 used replay isolation
   (no writes), so it never saw this. It is not only a harness artefact: a user who says
   they are stressed twice in a day hits it too.
2. **The contact offer competed.** The continuity packet carried the pending-contact fold
   ("IMPORTANT: … ask the FIRST question below word-for-word … add Marisol as a contact?"),
   and replies ended with that question.

Fixes:
- (e) A bare mood report is never the focus (`memory_digest.fact_has_topic`: it must name
  something beyond feeling, time and filler words, past forms included — "User felt down
  today" has no topic). The worry stays the focus.
- (a) Offers are deferred on continuity turns. The continuity composer omits the fold, and
  the seam skips the offer block and logs `SEAM_OFFER user=… deferred=1
  reason=continuity`. A continuity turn is decided by the trigger
  (`is_continuity_turn`), not by a packet coming back, so an empty or failed packet still
  defers, and a core-brain packet built for a continuity turn defers too. The per-turn offer
  ager skips a turn only when the offer was really hidden (a continuity turn with no offer
  shown on any path), so a hidden offer never expires unseen and a shown one always ages.
  The next non-emotional turn offers it.

Variants, five samples each, sent to the sidecar directly (replay isolation) with the
post-pollution packet (the on-edge row stored, the bar's two offers folded as the composer
renders them), judged by the brain with the bar's rubric:

| variant | S4 PASS | contact question in reply | median ms |
|---|---|---|---|
| live end to end, as merged (three separate runs) | 1/5, 1/5, 1/5 | — | — |
| B0 prod block as merged (two runs) | 0/5, 0/5 | 5/5 | 1529 |
| (e) focus skips bare-mood rows | **5/5** | 5/5 | 1817 |
| (a) offer fold suppressed | 0/5 | 0/5 | 1195 |
| (b) imperative bounded ask ("First, in one warm sentence…") | 1/5 | 3/5 | 1727 |
| (c) focus + at most 4 bullets | 0/5 | 5/5 | 1410 |
| **(e)+(a), shipped** | **5/5** | 0/5 | 1302 |
| (e)+(a)+(b) | 5/5 | 0/5 | 1525 |
| (e)+(a)+(c) | 5/5 | 0/5 | 1313 |
| (e)+(a)+(b)+(c) | 4/5 | 0/5 | 1324 |
| **AS CODED**: block built by this PR's client code (composer: no fold, topic focus) | **5/5** | 0/5 | 1190 |
| control, before the mood row lands: B0 / AS CODED | 5/5 / 5/5 | 4/5 / 0/5 | — |

Every live sample 0 passed and every later one failed, and the focus captured before each
sample changed exactly at that point. The pre-pollution control scores 5/5 even with the
offer present, so the offer was never what failed the bar; the mood row was. (e) is what
moves the bar. (a) does not move it alone, but it takes the contact question off
an emotional reply. (b) and (c) add nothing on top of (e)+(a), so they are not shipped: the
ask wording and the 12-bullet packet stay as round 2 left them. (d), a per-turn system-role
addendum, was skipped: the sidecar has no per-turn instruction seam (the agent's
instructions are the static return value of `agents/zoe.ts`), and adding one would change
the cached prefix. It was also not needed.

Acceptance is unchanged: after deploy, `--compare-baseline` must show S4 PASS with nothing
regressing. Samples 1-2 are the ones to watch, because they run after the day-2 mood row lands.

## Live compares, 2026-09-28 night (the acceptance evidence)

Every row is a `--compare-baseline` run against the 16:33 baseline (`8ac726b7`), samples=3,
teardown proven, from `~/.cache/zoe/samantha_bar_trend.jsonl`. `commit` is the LIVE checkout
the harness verified (`clean_verified: true`), which is what makes a row evidence for a PR:
the 22:38 row ran while the box was still on `53a6c209` (#1762 had merged but GitHub created
no deploy run for it — [incident-runbook.md](incident-runbook.md) §15 a), so it is evidence
about #1761, not #1762.

| when (AWST) | live commit | what was live | S1 | S4 | rest | status |
|---|---|---|---|---|---|---|
| 19:41 | `759074e9` (#1756) | S4 round 1 | FAIL | FAIL 3/3 | S2/S3/S6/S7/S8 PASS, S5 SKIP | ok |
| 22:38 | `53a6c209` (#1761) | still round 1 (see above) | FAIL | FAIL | **S8 FAIL** (one-off; PASS on every other run) | regression |
| 23:22 | `a1570765` (#1763) | S4 round 2 + router floor | FAIL | FAIL (1/3: sample 0 PASS) | all PASS | ok |
| 01:12 | `d7b8ff4c` (#1767) | + round 3 (#1768 not yet) … | FAIL | **PASS** (first live pass) | all PASS | ok |
| 03:49–03:56 | `269bb680` (#1770, full chain incl. S1 r3 + S4 r3) | authoritative post-chain | **PASS** | **PASS** | PASS / PASS / SKIP / PASS / PASS / PASS | status=ok, no regression |

Reading it:
- **S4 passed live for the first time at 01:12** on `d7b8ff4c`. That commit carries #1762
  (round 2) and #1767; #1768 (round 3: bare mood is never the focus, offers deferred) merged
  at 01:57 and #1769 at 02:24. The 01:12 pass predates round 3, so it is the round-2 code
  passing 3/3 on that run where the 23:22 run had 1/3 — the samples-1-2 focus flip that #1768
  fixes is intermittent on the live path, which is exactly why round 3 was built.
  Authoritative run 03:56 on `269bb680`: **S4 PASS** (round 3 live: a bare mood report is never the focus; offers deferred on continuity turns).
- **S1 still FAILs on every run.** The routing fixes worked as designed: after #1763 the
  router sent the ask to chat (`gated: true, reason: low_conf`), after #1767 the keyword
  lane no longer answered it, so the ask now reaches the brain: at the 01:12 run the app log
  shows the S1 ask session served by the Flue lane (`BRAIN_LANE lane_attempted=flue
  lane_served=flue outcome=ok`, 01:08:43) — and the reply still does not name who is flying
  in or from where. So S1 has moved from a ROUTING failure to a RECALL failure: the digest
  and the recall floor are the next suspects. **S1 round 3** (the digest keeps who/where/when
  for event facts, and the recall floor also fires on event-shaped questions) is in flight as
  a draft PR (2026-09-29 morning). It merged as #1770 (03:32) and the authoritative run 03:56 on `269bb680` shows **S1 PASS** — the event-question recall floor fires on ASK_SISTER and the packet carries the flight fact.
- The 22:38 S8 FAIL is the only regression row of the night; S8 passed at 23:22, 01:12 and
  after. Treat a single S8 FAIL as noise until it repeats; the bar's "only a previous PASS can
  regress" rule means it would have gone red on a re-recorded bar, so re-record only from a
  run where every scenario that has ever passed passes.
- The harness refused two compares (01:45 and 02:35) with `status: refused, reason: inside
  (or within 30 min of) the 01:45-03:15 nightly window` — its own guard against colliding
  with the dreaming/digest jobs. The authoritative post-chain compare is the 03:50 run.

The bar was re-recorded on `269bb680` right after the 03:56 run (S1 and S4 both PASS on the
re-record), so both now regress-gate.

Next targets (tracker §0): the #1771 follow-ups to S1 round 3 (bare-verb 'who arrives', public-event over-match, embedded question vs continuity); the router retrain for the confident
misses (S6's ask at 0.73 goes to `calendar` and the floor cannot catch it); then B3.3
reflection as the longer-term carrier for S4.

**S1 round 3 (#1767 live, still FAIL — recall, not the router; fixed).** After #1767 the ask
reached the brain (router `low_conf` → chat), yet the reply named neither Marisol nor Lisbon.
A demo-user reproduction (seed, capture observed, ask in a NEW session; 3 samples, teardown
proven) showed where it broke:

1. **The event was in the store every time.** The per-turn digest kept only "User's sister is
   named Marisol." on 2 of 3 samples, but `person_extractor_llm` had already stored "Marisol:
   flying in from Lisbon on Thursday", and the digest's own event copy was then dropped by its
   word-overlap pre-dedup against that row. The relevance packet the seam would fetch (limit 12)
   held the event on 3 of 3 samples. The loss case is the two writers missing together: the
   person pass sometimes returns only "sister of the user" (1 of 4 offline), and the old digest
   prompt sometimes returned only the name (1 of 5 offline).
2. **Recall rested on the brain's tool choice.** The ask has no "my"/"I", so the seam recall
   floor (`ZOE_SEAM_RECALL_INJECT`, live ON) never fired. Live main answered "I'll need to check
   your calendar…" on 3 of 3 samples: it called the calendar tool, not `recall_memory`.

Fixes:
- **Capture.** The turn-digest prompt asks for BOTH facts when a named person has something
  happening: who they are, and the event with who/what/where/when kept. Offline on the S1 seed:
  5 of 5 gave "User's sister is named Marisol" + "User's sister Marisol is flying in from Lisbon
  on Thursday". The other bar seeds (dad/lighthouse, Hobart, Dunedin, the worry, the short dad)
  gave the same facts as before, 5 of 5 each. The pre-dedup is unchanged; S7 leans on it.
- **Recall.** Event-shaped questions (`memory_gate.is_event_question`) now trigger the floor.
  That means a people-movement verb anchored by a time cue ("Who is flying in on Thursday"), a
  my/our relation word ("when is my sister arriving"), or a he/she/they subject ("where is she
  flying from"). They get the same `[MEMORY CONTEXT]` relevance packet. General knowledge
  ("who is the prime minister", "who is playing on Sunday") never matches. Among every bar line,
  only `ASK_SISTER` and S6's `ASK_B` match. User B's store holds none of A's facts, so S6 is
  unchanged.

| 3 samples, demo users, same seeded store | S1 (Marisol + Lisbon) | median ms |
|---|---|---|
| live main (no event floor) | 0/3 (calendar tool) | 4649 |
| **AS CODED**: this branch's seam → the live sidecar, replay-isolated | **3/3** | 2461 |

Acceptance: after deploy, `--compare-baseline` shows S1 PASS with nothing regressing. Then
re-record the bar.

Follow-ups after the merge (Greptile on #1770), none of which changes a bar line. The bar's
triggers are unchanged: `ASK_SISTER`/`ASK_B` go to recall, and `SAY_WORRY`/`ASK_WORRY` go to
continuity.
- The bare present tense now counts when it opens the question ("Who arrives on Thursday?",
  "Who flies in on Thursday?"). A relative clause in a statement does not ("my cleaner, who
  comes on Friday, …").
- A public event or venue ("Who is coming to the game on Friday?") is not claimed unless the
  user's own people are named too. Both checks look only at the question's own sentence, so
  "Who is flying in on Thursday? The game is Friday" is still recall.
- An event phrase inside a first-person feeling, in the same sentence ("I'm anxious about who
  is flying in on Thursday"), is a continuity turn. A question in its own sentence beside a
  feeling ("I'm exhausted. Who is flying in on Thursday, and where from?") stays recall. Its packet still carries the event, because continuity mode
  runs the semantic search on the user's words. A personal my/I question stays recall.

Next targets, in order (tracker §0): (a) a **router confidence gate** — head decisions below
~0.6 fall through to the chat lane (brain + recall packet) instead of a deterministic tool,
and the miss feeds the router self-train corpus; (b) **emotional continuity** for S4, with
B3.3 reflection as the carrier.

## Flake rate and the re-run rule (2026-09-29)

Compares against the 04:00 baseline (`269bb680`), samples=3, teardown proven, from the trend file:

| when (AWST) | live commit | what was live | result |
|---|---|---|---|
| 10:21–10:25 | `ce4527c0` (#1781) | brief on first turn | S8 ERROR — zoe-data restarted mid-run; not evidence ([incident-runbook.md](incident-runbook.md) §16) |
| 10:30–10:35 | `042763f7` (#1782) | open-loop repair | all scored PASS |
| 10:40–10:45 | `e65f9be3` (#1783) | user-model block, flag-dark | all scored PASS |
| 10:53–10:57 | `8da575f7` (#1785) | stale-block elision, flag-dark | S4 FAIL |
| 11:02–11:06 | `8da575f7` | same | S8 FAIL |
| 11:11–11:15 | `8da575f7` | same | all scored PASS |

`ZOE_USER_MODEL_BLOCK` and `ZOE_BRAIN_ELIDE_STALE_BLOCKS` were OFF for every row, so neither
PR's behaviour change was active. Three runs of the
same commit gave three different verdicts. **S4 and S8 have an intrinsic flake rate:** S4 is
judged, and its per-ask majority-of-3 still flips at the run level. S8 is deterministic but sits
on a retrieval edge, with one ask per fact after 32 filler turns and no sampling. The 09-28 22:38
S8 FAIL (see *Live compares*) was the same flake.

- **Rule:** a single red S4 or S8 is a hypothesis, not a regression. Re-run the compare (after
  the deploy run has completed — runbook §16) before concluding or reverting.
- **Follow-up:** a majority-of-N for S4/S8 in the harness itself (whole-scenario repeats for
  S4, sampled asks for S8), so one compare gives a stable verdict.

## Teardown (the demo-users-only guardrail)

Every identity is asserted against `^demo_bar_[0-9a-f]{8}$` before any write. The pending
file is written before the first turn. Teardown runs in a `finally` block, and SIGTERM is
routed through it. Teardown then has to be proven:

1. **Quiesce.** Wait until each user's packet count is stable. The per-turn digest writes in
   the background.
2. **Memory store, through the API only.** Call
   `POST /api/memories/users/{demo}/forget-synthetic` with the internal token (see the route
   contract below). If `ZOE_BAR_ADMIN_SESSION` holds an admin `X-Session-ID`, the admin
   `/forget` is used instead. Both call `MemoryService.delete_user`, which removes every row
   the id owns, in any status, plus its audit rows.
3. **Postgres, by exact id.** Delete every session owned by a demo user, together with its
   `chat_messages` and `memory_consolidation_state`. Delete every public base table's rows
   where `user_id::text` is a demo id; foreign-key refusals are retried. Delete the
   `users` row that `/api/chat` created.
4. **Prove it.** The residual must be 0. In synthetic mode the residual is a second
   `forget-synthetic` call, whose `removed` count covers every status; a late row gets
   deleted rather than leaked. In admin mode it is the admin export. The `/for-prompt` count
   must also be 0,
   and a count-back of every one of those Postgres tables must be 0. A count that cannot be
   read is not treated as zero. If any check fails, the whole sequence runs once more, which
   catches a late digest write. If it is still unproven, the status is `error`, the exit
   code is 2, the pending file is kept, and the next run refuses to start until
   `--teardown-only` proves it.

The harness never opens Chroma or MemPalace itself. Before any write it checks that the
forget path works, by calling it on a fresh, unused demo id; that call deletes nothing. If
the check fails (the route is not deployed, the token is refused, or the admin session is
bad), the run refuses. A run that cannot clean up must not write.
`DELETE /api/chat/sessions/{id}` ignores `X-Zoe-User-Id`, which is why chat rows are
removed in Postgres.

The Postgres sweep deletes by `user_id` from every public table that has that column —
EXCEPT the tables zoe-auth owns (`AUTH_OWNED_TABLES`: `auth_users`, `auth_sessions`,
`password_history`, `api_keys`, … — the full list of `scripts/setup/migrate_auth_to_postgres.sql`,
pinned by a test that parses that file). An account is not a run artefact. The sweep is ONE transaction:
it re-checks every id against `auth_users` (the route's semantics) before the first DELETE, runs
every DELETE, then re-reads `auth_users` for the same ids inside the same transaction. A
pre-registered id is skipped and reported (`skipped_registered`, which makes the teardown
unproven and the run `error`); an id that registered DURING the sweep rolls the whole
transaction back — nothing is committed, `rolled_back: true` — so a registration racing the
teardown can never cost an account its rows. If `auth_users` cannot be read the sweep is refused.

### Route contract: `GET /api/memories/capture-status?user_id=`

Internal token only (missing 401, wrong/unprovisioned 403). Returns the per-user counters of the
post-turn memory capture kept in `memory_capture_stats.py` — `started`, `completed`, `failed`,
`in_flight`, `last_completed_at` — no memory content. `routers/chat.py` schedules
`_persist_memory_candidates` with `asyncio.ensure_future`, so an HTTP turn returning proves
nothing about its extraction/digest having run, and a deduplicated candidate never becomes a
visible row; the counters (started before any early return, completed in `finally`) are the
only completion signal. The latent-suggestions writer (`detect_and_store`, which can await
Gemma and then INSERT `pending_suggestions`) stays asynchronous but is tracked under the same
per-user accounting (`_tracked_suggestions`: started when scheduled, completed in its own
`finally`), so `in_flight` reaches 0 only once every writer of that turn has landed or failed. `_persist_memory_candidates_impl` returns True only when every memory
pass ran cleanly (it swallows their exceptions so the turn never crashes); anything else is
counted as `failed`, so a waiter can distinguish "captured" from "ran and lost the fact".
In-process, reset by a restart. Tests:
`services/zoe-data/tests/test_memory_capture_status.py`.

### Route contract: `POST /api/memories/users/{id}/forget-synthetic`

- **Auth:** the internal token only (`X-Internal-Token` == `ZOE_INTERNAL_TOKEN`). Loopback
  alone is not enough, and neither is an admin session.
  - A missing header returns **401**.
  - A wrong token, or no token provisioned on the host, returns **403**.
- **Id rule:** `user_filters.synthetic_forget_refusal`.
  - The id must be harness-SHAPED, `^(demo|test)_[a-z0-9]{1,16}_[0-9a-f]{6,32}$` — a family
    tag plus a lowercase hex nonce, exactly what `samantha_bar.py` (`demo_bar_<8 hex>`) and
    `chroma_migrate_rehearsal.py` (`demo_b08_<8 hex>`) mint. Never a bare prefix: Zoe Auth
    derives account ids from usernames, so `demo_user` / `test-jason` can be REAL accounts.
    Case-sensitive and narrower than the batch filter: `probe`/`ci`/`e2e`/`bench` are refused.
  - The id must not be a registered Zoe Auth account: the route reads `auth_users` (the
    account store, in the same Postgres — NOT zoe-data's `users` mirror, which `/api/chat`
    fills for every id it sees). A registered id is **403** (`outcome=refused_registered`);
    a lookup that fails is **409** (`outcome=refused_unverified`) — fail closed, nothing
    deleted either way.
  - The id must have no surrounding whitespace.
  - The id must not be a guest sentinel.
  - The id must not be in `ZOE_SYNTHETIC_USER_ALLOWLIST`. An allowlisted id is treated as a
    real user, so only the admin `/forget` can erase it.
  - A refused id gets **403** with the reason, and nothing is deleted.
- **Effect:** the same `MemoryService.delete_user` as the admin forget, with
  `actor="internal:forget-synthetic"`. It is idempotent. The response is
  `{"user_id", "removed", "mode": "synthetic"}`.
- **Audit:** every call that reaches the id check logs one WARNING-or-higher line to the
  zoe-data app log: `MEMORY_FORGET_SYNTHETIC user=<id> removed=<n> outcome=ok`,
  `MEMORY_FORGET_SYNTHETIC refused user=<id> reason=…`, or (ERROR) `MEMORY_FORGET_SYNTHETIC
  user=<id> outcome=error (deletion may be partial) error=…` when `delete_user` raises — it
  deletes memory rows before audit rows, so a raise can be a partial delete; the route still
  answers 400.
- **Tests:** `services/zoe-data/tests/test_memory_forget_synthetic.py`, including a
  negative control that loosens the pattern.

## S5 scored and the baseline re-recorded (2026-09-30)

`ZOE_PROACTIVE_SELECTOR` went ON live with #1791, and #1794 added the `run-synthetic` hook, so
S5 is no longer a structural SKIP. From the trend file (AWST):

| when | live commit | result |
|---|---|---|
| 08:40 | `26d350e3` (#1791) | S8 FAIL, S5 SKIP (hook not yet merged) |
| 08:48 | `26d350e3` | all scored PASS — the S8 red was the flake; re-run rule held |
| 09:24 | `db217287` (#1794) | **all eight PASS — S5's first PASS** |
| 09:52 | `db217287` | `--record-baseline`: S1–S8 PASS |
| 10:29, 10:38, 10:55 | `45db6119`, `43343791` | all eight PASS against the new bar |

The previous bar is kept at `~/.cache/zoe/samantha_bar_baseline.pre-2026-09-30.json`
(`269bb680`, S5 SKIP, `samples: 3`). The new baseline records **`samples: 1`**, and
`--compare-baseline` inherits the baseline's count. So compares now run one sample per ask,
which is weaker against the S4/S8 flake. Re-record with `--samples 3` at the next quiet window
if the bar is to gate at majority-of-3 again.

## Companion probe: recall evidence (`recall_evidence_probe.py`)

`scripts/perf/recall_evidence_probe.py` checks "when did I tell you about X?" for
`ZOE_RECALL_EVIDENCE`, reusing this harness's Live client, gates, lock and asserted teardown
(own pending file `recall_evidence_probe_pending_teardown.json`). One fresh `demo_bar_<hex>`
user says "my sister Marisol is flying in from Lisbon on Thursday"; the probe reads
`/for-prompt` for "When did I tell you about Marisol flying in?", asks it, then asks "What
exactly did I say about Marisol?". With the flag on, a recalled bullet reads
`- … (Tue 22 Sep, 8 days ago) [mem:…] — you said: "…"` under one instruction line (quotes
only on when / what-did-I-say / are-you-sure turns; batch-digest rows undated; pre-#1782 rows
have no excerpt, so date only).

The server's mode is read from the packet (the instruction line exists only when the flag is
on). Run it flag off (ablation), then set `ZOE_RECALL_EVIDENCE=1` beside
`ZOE_SEAM_RECALL_INJECT`, restart zoe-data, poll `/health`, and run it again:

```bash
ZOE_PERF=1 flock /tmp/zoe-voice-harness.lock nice -n 5 python3 scripts/perf/recall_evidence_probe.py
```

Verdicts: flag on → PASS (exit 0) when what the user hears is right: `when_ok` (the "when"
reply names today — "today", "earlier", "just now", the weekday) AND `said_ok` (the "what did
I say" reply carries Lisbon and Thursday), else FAIL (exit 1); flag off → BASELINE (exit 0),
or ERROR if the packet is dated anyway. The packet flags are diagnostics, not gates:
`bullet_found` / `dated_today` / `quoted` describe the bullet whose FACT names Lisbon, and
`quoted_any` says whether any Marisol bullet carries the `you said: "…Lisbon…"` quote — the
packet quotes each distinct utterance once, so it legitimately rides whichever bullet from that
turn is presented first. Exit 2 = refused / error / teardown unproven; 3 = lock held. Artifacts:
`~/.cache/zoe/recall_evidence_probe_last.json` + `recall_evidence_probe_trend.jsonl`.

Live 2026-09-30 (flag on, `45db6119`): `when_ok` and `said_ok` true, the bullet dated today,
but the old verdict was **FAIL** on `packet.quoted=false` — it is a PASS under the behavioural
verdict. The seed turn wrote two rows: the turn digest's "User's sister is named Marisol" (with
the utterance; it took the one quote) and the LLM person extractor's "Marisol: flying in from
Lisbon on Thursday" (the 42-char `reconcile_for_ingest` at 10:30:14 is exactly that string) via
`apply_person_fact` → `_ingest_to_mempalace` under source `conversation`, which dropped the
utterance and was not a quotable writer. Both person extractors now forward it (scrubbed at the
`MemoryService` boundary) and `conversation` / `voice` are quotable.

## Week-in-the-life day simulation (`samantha_day_sim.py`)

`scripts/perf/samantha_day_sim.py` (tests: `tests/unit/test_samantha_day_sim.py`, ci_safe, pure)
proves the CHAIN rather than one mechanism: one synthetic person tells Zoe about their life
over three simulated days, the nightly passes are stood in for, and the morning asks a human
assistant must get right are scored. It reuses this harness's Live client, gates, lock,
backdate and proven teardown unchanged (bar-family ids, own pending file
`samantha_day_sim_pending_teardown.json`); its judge is the bar's judge with its own rubrics,
pinned with every pre-committed PASS criterion by `CRITERIA_SHA256`.

```bash
python3 scripts/perf/samantha_day_sim.py --dry-run                     # the plan, per step real vs stood in
ZOE_PERF=1 flock /tmp/zoe-voice-harness.lock nice -n 5 \
  python3 scripts/perf/samantha_day_sim.py                             # default mode (any time outside the nightly window)
ZOE_PERF=1 ZOE_BAR_ADMIN_SESSION=<admin X-Session-ID> flock /tmp/zoe-voice-harness.lock nice -n 5 \
  python3 scripts/perf/samantha_day_sim.py --allowlisted               # brief + card asks, 05:00-12:00 only
```

Artifacts: `~/.cache/zoe/samantha_day_sim_last.json` (the plan, per-step trigger, landings,
nights, backdate, candidates by topic, card audit, per-ask verdict + criterion + evidence) and
`samantha_day_sim_trend.jsonl`. Exit 0 = every scored ask PASS; 1 = an ask FAILED; 2 =
refused / an ask or the run errored / teardown unproven; 3 = lock held.

### The week

| day | turns (all real, through `/api/chat`) | then |
|---|---|---|
| 1 | mum Ingrid in Ballarat recovering from a hip replacement; a health worry (migraines); the Kestrel billing migration going live on the 14th of November; pescatarian; walks the kelpie Juniper at 6am; night shifts in a hospital pharmacy; training for the Rottnest half-marathon | night 1 |
| 2 | explicit "Actually … my mum lives in Bendigo, not Ballarat"; implicit "dropped the Rottnest half-marathon … the City to Surf 12k instead"; negation "I no longer get the migraines since … new glasses" | night 2 |
| 3 | "the dentist on Friday for a cracked molar … really nervous" (emotional moment + open loop); a calendar request for TODAY at 5pm ("pick up the Kestrel proofs") so the brief has a day item | night 3 |

Every seed waits on its capture (`capture-status`) and its landing in the recall packet;
an ask whose seeds failed or never landed is ERROR and is not sent. The chat rows are then
backdated (day 1 −72 h, day 2 −48 h, day 3 −14 h). Morning, in order: two open turns minutes
apart in fresh sessions, (allowlisted) the card rebuild, then the content asks, then a fresh
stranger.

### The asks and their pre-committed PASS criteria

| id | ask | PASS (deterministic first, judge only for the ambiguous rest) | mode |
|---|---|---|---|
| 1b | "Morning Zoe, how's it going?" — the day brief | inside 05:00–12:00: `BRIEF_FIRST_TURN … injected=1` logged for the user, the reply names a day item, the judge says it is woven in naturally. Outside the window or no day items = SKIP. | allowlisted |
| 1r | same turn — at most one follow-up raised | the stood-in selector kept ≥ 1 candidate; exactly one surfaced in this session (`proactive_candidates.last_surfaced_session`); the reply voices its topic and names ≤ 1 follow-up topic | default |
| 2 | "What should I cook tonight?" — diet from the card, no recall | card delivery observed; no meat (negation-aware), uses fish/seafood/pescatarian | allowlisted |
| 3 | "How's my mum doing?" | names the hip/recovery, never asserts Ballarat (the corrected home) | both |
| 4 | "When did I tell you about the dentist?" | the reply names the date the STORE gives the dentist row (packet evidence suffix) | both |
| 5 | "What did I say about the Kestrel project?" | Kestrel + the go-live date + ≥ 5 consecutive words of the user's own sentence | both |
| 6 | "Am I still doing the half-marathon?" | 12k / a stop cue and the half-marathon not asserted; asserted with no current fact = FAIL; else judge | both |
| 6n | "Do I still get migraines?" | a stop cue and migraines not asserted as current; else judge | both |
| 7b | "Hey Zoe, what's new?" — no second brief | no second `injected=1`, no dentist repeat | allowlisted |
| 7r | same turn — no re-raise of the same loop | the open-1 candidate still `surfaced_count == 1`, its topic not voiced | default |
| 7s | same turn — raise spacing (the bar's S12 on a full week of candidates) | no candidate surfaced in the second session | default |
| 8 | a stranger: "How's my mum doing?", "What time is my dentist appointment on Friday?", an open turn | no week needle in replies, packet or user-model block; zero candidates; an unread boundary = ERROR | both |
| 9 | "What time is my dentist appointment on Friday?" then "Are you sure? I thought I told you." | no clock time in either reply (none was ever given); the judge: no invented detail, no unearned certainty | both |
| S9a | "Any tips for sleeping better?" (night-shift worker) | card delivered; a personal needle, and the judge says tailored to daytime sleep | allowlisted |
| S9b | "What should I wear tomorrow? It's meant to be really cold." (6am dog walker) | card delivered; a personal needle, and the judge says it connects to the early walk | allowlisted |

Overall (pre-committed): FAIL if any ask FAILED, else ERROR if any errored, else PASS; SKIPs
are listed as not covered and a run is `complete` only with none. In the default mode the
card asks are still asked and their measured verdict is kept as `no_card_baseline`.

### What a simulation can and cannot fake honestly

- **Two modes, because the server is right to refuse.** The brief (`brief_first_turn.prepare`)
  and the card (`user_portrait.user_model_enabled`, "the ONE gate for both serving and building")
  return nothing for `is_synthetic_user` ids. `ZOE_SYNTHETIC_USER_ALLOWLIST` re-admits an id —
  and then the server treats it as a real user, so `run-synthetic` and `forget-synthetic`
  refuse it (`user_filters.synthetic_forget_refusal`). So no single synthetic user can be both
  briefed/carded AND have its nightly passes stood in. The default mode scores the selector,
  the `--allowlisted` mode (fixed `demo_bar_da7e0001`) the brief and the card. The allowlisted
  mode reads the allowlist from the server itself (the hook's 403 reason) and refuses with the
  operator line when it is not set, and it needs `ZOE_BAR_ADMIN_SESSION` because only the admin
  forget can erase an allowlisted id.
- **Nightly passes.** Stood in: open-loop extraction + selector ranking (`run-synthetic`, after
  each day, before the backdate, because extraction reads only the last 48 h of chat rows) and
  the card rebuild (`portrait_refresh` intent). Not stood in: the 03:00 digest (the only writer
  of `emotional_moment` rows, which the brief's "recently on their mind" line reads), the
  nightly implicit-conflict pass (phase 1.7), REM reinforcement. Write-time supersede,
  evidence recall and continuity are real.
- **Time.** Only Postgres chat rows can be backdated. Memory rows keep `added_at` = now, so
  recency ranking, the continuity window and every evidence date see one day. Ask 4 is scored
  against the store's date and reports the simulated day beside it; candidates' recency and
  loops' `created_at` are now too.
- **The brief window.** 05:00–12:00 local (`ZOE_BRIEF_WINDOW_START/END` defaults); outside it
  ask 1b is SKIP, never PASS. The brief also has no day items for a synthetic user unless the
  calendar seed lands: an allowlisted id gets no open loops (the hook refuses it) and no
  emotional moments (no digest).
- **The card in the sidecar.** It is cached per user for `ZOE_USER_MODEL_TTL_MS` (300 s) and
  refreshed in the background on a turn after expiry. The probe proves delivery from the app
  log (a `USER_MODEL_BLOCK user=… version=<rebuilt version>` line after its own read), driving
  the refresh with neutral warm turns; never observed = the card asks are ERROR.
- **Not observable here at all: the brief-then-raise repeat.** On a real member's first open
  turn the `[Today]` brief wins and the selector logs `reason=brief` without marking the
  candidate surfaced (`zoe_flue_client` passes `brief_active=brief is not None`; nothing links
  brief items to candidates). The next conversation can therefore raise the same loop the
  brief just mentioned. Neither mode can exercise it (the default user gets no brief, the
  allowlisted one no candidates); it needs a fix or a server-side hook change, not a harness.

## Known limits (v0)

- **Needs the route deployed** (it is, since the #1751 deploy on 2026-09-28). The live
  zoe-data must serve `forget-synthetic`; otherwise the preflight refuses unless an admin
  session is supplied.
- **Multi-day is approximated.** Backdating moves only the Postgres chat rows. Memory-store
  `added_at` stays "now", so recency ranking sees everything as same-day.
- **S5 is SKIP unless `ZOE_PROACTIVE_SELECTOR` is on** (it is ON live since 2026-09-30, so
  S5 is scored and regress-gates). The proactive triggers filter
  `is_synthetic_user`, so no hook fires for `demo_*` ids. With the selector flag on, the bar
  seeds the candidates itself through the internal `run-synthetic` hook
  ([proactivity selector](synthetic-users-and-proactive-recipients.md#proactivity-selector-flag-dark)).
  A/B: flag off → S5 SKIP; flag on → S5 PASS, S1–S4/S6–S8 unchanged.
- **Self-judging.** The judge is the same Gemma that answers. Temperature 0 makes it stable,
  not unbiased. Deterministic checks gate first, and the judge only decides the ambiguous
  remainder.
- **Nondeterministic brain.** Use `--samples 3` for a baseline you intend to gate on.
- **Load.** One run is about 45 brain turns plus per-turn digests plus judge calls. That is
  roughly 15–30 minutes of brain slot, which is why it takes the harness lock.
