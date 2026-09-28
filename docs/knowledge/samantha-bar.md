---
type: Reference
title: Samantha bar harness (samantha_bar.py v0)
description: The Samantha-quality regression gate. Eight scripted multi-day memory and companion scenarios run against throwaway demo users through the live API. Covers how to run it, what each scenario proves, the scoring and judge, the baseline and teardown contracts, and known limits.
tags: [memory, samantha, eval, regression-gate, harness, zoe-data]
timestamp: 2026-09-28T08:45:00Z
---

# Samantha bar harness (`scripts/perf/samantha_bar.py`, v0)

Tracker row: B3 "Samantha bar harness v0" in [the program](../architecture/beat-the-bar-2026-program.md).
Tests: `tests/unit/test_samantha_bar.py` (ci_safe, pure).

It is the memory and companion counterpart of the voice replay gate. Each run creates two
fresh users, `demo_bar_<8 hex>` A and B, and talks to the live zoe-data API as them
(`POST /api/chat/?stream=false` with `X-Internal-Token` + `X-Zoe-User-Id`). It scores
eight scenarios and compares them against a baseline. A scenario is red only when it
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
| S5 | A proactive hook, if one fires, carries the day-1 open loop. | Reads `proactive_pending` for demo A. No hook gives SKIP. An `emotional_followup` without the loop gives FAIL. |
| S6 | Demo B never sees demo A's facts. | Deterministic: no A needle may appear in B's reply or in B's `/for-prompt` packet. If A's own packet holds none of them, the result is SKIP, because the test would be vacuous. A packet read that FAILS (either user) is ERROR — a boundary that was not inspected is never certified. |
| S7 | A short duplicate ("my dad is Teodor") does not erase the richer fact. | The reply must name Teodor and lighthouse, and A's packet must still hold `lighthouse`. A failed packet read is ERROR. The duplicate's capture must be OBSERVED first: the harness waits on `/api/memories/capture-status` (the turn's background extraction + digest completed, nothing in flight, and `failed` did not advance — a memory pass that raised is completed-but-FAILED; bounded timeout) — not observed or failed = ERROR, never PASS. |
| S8 | S1 and S7 facts survive 32 filler turns spread over 3 sessions. | deterministic: `marisol` and `lighthouse`. ANY failed filler turn is ERROR, even when both names come back — the long history was not built, so the recall proves nothing. |

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
  defers. The per-turn offer ager skips the same turns, so a run of emotional turns cannot
  expire an offer it hid.
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

Next targets, in order (tracker §0): (a) a **router confidence gate** — head decisions below
~0.6 fall through to the chat lane (brain + recall packet) instead of a deterministic tool,
and the miss feeds the router self-train corpus; (b) **emotional continuity** for S4, with
B3.3 reflection as the carrier.

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

## Known limits (v0)

- **Needs the route deployed** (it is, since the #1751 deploy on 2026-09-28). The live
  zoe-data must serve `forget-synthetic`; otherwise the preflight refuses unless an admin
  session is supplied.
- **Multi-day is approximated.** Backdating moves only the Postgres chat rows. Memory-store
  `added_at` stays "now", so recency ranking sees everything as same-day.
- **S5 is structurally SKIP for demo users today.** `emotional_followup` and the other
  proactive triggers filter `is_synthetic_user`, so no hook fires for `demo_*` ids
  ([synthetic users](synthetic-users-and-proactive-recipients.md)). S5 starts measuring
  when a lab sets `ZOE_SYNTHETIC_USER_ALLOWLIST`.
- **Self-judging.** The judge is the same Gemma that answers. Temperature 0 makes it stable,
  not unbiased. Deterministic checks gate first, and the judge only decides the ambiguous
  remainder.
- **Nondeterministic brain.** Use `--samples 3` for a baseline you intend to gate on.
- **Load.** One run is about 45 brain turns plus per-turn digests plus judge calls. That is
  roughly 15–30 minutes of brain slot, which is why it takes the harness lock.
