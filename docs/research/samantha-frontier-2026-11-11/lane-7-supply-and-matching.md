---
type: research-lane
title: "Lane 7 - memory supply funnel, why recall misses (synthetic lab), and said-vs-did on promises"
date: 2026-11-11
status: read-only on everything live (Postgres via a read-only transaction, COUNT/GROUP BY only; MemPalace chroma.sqlite3 opened mode=ro, metadata keys grouped, no document text read; zoe-data logs counted by marker). Lab is synthetic, offline, retrieval-only, run under the test-lane lock.
code: origin/main 1ddb70951
lab_branch: labs/recall-match-lab (worktree ~/.worktrees/recall-match-lab), commit 08296d690 (not pushed, no PR)
---

# Lane 7 - supply, matching, promises

Labels: [Proven] measured directly; [Experimental] measured through a proxy or on synthetic data; [Speculative] a reading.
Sources: [live] the box tonight, [code] origin/main 1ddb70951 file:line, [record] an earlier lane or doc, [contradicted] a claim now shown wrong.
Window: 30 days = created/started on or after 2026-10-12 (UTC). Member = the one non-synthetic `role='member'` account with real turns
(guest sentinel, `guest-*`, and `user_filters.SYNTHETIC_USER_RE` excluded).

## Headline

1. **The commitments ledger's "31 of 33 missed" is a replay-harness artifact, not household behaviour [Proven][live+code][contradicts lane 1 finding 4].**
   All 33 rows have a `replay-` session id (`tests/replay_samples.py:275` mints `replay-<hex>`), all 33 are kind `remind`, all 33 have
   `tools='[]'`, all 33 are due at 07:00Z (15:00 local), one per session. The replay gate replays the voice corpus as the member's id with
   `replay_isolation` (writes deliberately not committed), the write-claim guard is skipped for replay (`brain_dispatch.py:917`, `:989`),
   but the commitments recorder is NOT (`brain_dispatch.py:847-868`, `commitments.py:399-444` have no replay check). So the ledger has
   recorded **0 household promises in 14 days**. The household said-vs-did number on promises is unmeasured, i.e. zero data.
2. **Panel supply has nearly stopped.** 394 real member user turns in 30 days produced 48 durable rows (38 approved); since 2026-11-03,
   1 row in 9 days. Idle consolidation read 224 member panel turns since 11-01 in 25 passes and every pass returned zero candidates
   (`stored=0 rejected=0 skipped=0 dropped=0`) [Proven count; cause unverified].
3. **The pendant is the unharvested line, and it ends at a flag.** 30 days: 2,263 recordings (126 h) -> 945 with a transcript (111 h)
   -> 9,926 speaker segments, of which 12 are attributed to the wearer -> 33 day reviews -> 106 candidates -> **0 offered, 0 approved,
   0 written** (`ZOE_OMI_DAY_REVIEW=shadow`, raise off by contract). 106 candidates = 3.5 per day, vs the panel's ~0.1 per day now.
4. **Recall misses because one shared distinctive word is not enough** (lab, synthetic, see B): the production gate needs a capitalised
   name, two rare keys, or a clock time; on 20 synthetic oblique turns it found the needed fact on 2.

## A. Supply funnel

### A1. Panel / chat (30 days) [live]

| stage | count | method | label |
|---|---|---|---|
| Real member user turns | **394** on 22 days (387 panel, 7 other) | `chat_messages` role=user joined to `chat_sessions`/`users`, member, not guest, not synthetic | [Proven] (lane 1 reports 308/25 days with its filter; same order, the gap is the filter, not investigated) |
| Real member turns, 11-05..11-11 | 53 | same | [Proven] |
| of which brain conversation | ~30-32 (57-60%) | `SEAM_CONTINUITY` 30 and `PROVENANCE_REPLY` 32 member lines, same window, harness excluded; one per brain turn | [Experimental] |
| of which fast-tier / non-brain (commands, nothing to remember) | ~21-23 (~40%) | remainder | [Experimental] |
| Durable rows written for the member | **48** (38 approved, 4 superseded, 2 archived...) | MemPalace `mempalace_drawers` metadata, `added_ts`/`added_at` >= 10-12, grouped | [Proven] |
| by writer | idle_consolidation 17, voice_turn_digest 10, digest 8, voice 4, synthesis 4, conversation 2, voice_regex 1, turn_digest 1, consolidation 1 | `source` key | [Proven] |
| by authority (approved) | model_from_transcript 18, user_stated_derived 15, model_from_turn 4, user_stated 1 | `authority_class` | [Proven] |
| Sessions that yielded a row | 15 of 62 member sessions (24%) | row `session_id` in the member's chat sessions | [Proven] |
| Rows per local day | 10-12: 16, 10-13: 6, 10-14: 7, 10-19: 4, then 0-2 per day; 11-02: 4; **11-03..11-11: 1** | | [Proven] |
| Rows per turn | 0.12 over 30 d; ~0.02 since 11-03 | 48/394; 1/~60 | [Proven] |
| Idle consolidation on member panel sessions, 11-01..11-11 | 25 passes over 224 turns: stored 0, rejected 0, skipped 0, dropped 0 | `MEMORY_IDLE_CONSOLIDATE session=voice-panel*` lines (harness `bar-*` sessions: 274 passes, 220 stored) | [Proven] |
| Idle consolidation sweep failures | 55 (33 "db pool exhausted", 2 db not ready, 20 blank) | `idle consolidation sweep failed` | [Proven] |
| Per-turn `turn_digest` stores, real users | 3 since ~10-18 (harness 266) | `memory_digest: turn_digest: stored/superseded`, harness tag or demo/bar id excluded | [Proven] |
| Ingest-side drops (all users) | IDENTITY_FACT_BLOCKED 24, paraphrase skip 14, retracted 1, guest discard 1, affect-not-stored 1 | log markers | [Proven]; small, not the leak |

**Where the panel leaks** [Experimental]: not at ingest (drops are small) but at extraction. The extractor sees the member's user turns
and returns nothing (25 of 25 passes), and the per-turn digest stores ~nothing for the real member while it stores hundreds for the
harness. Why could not be verified without reading turn text: either the member's panel speech carries few self-statements (about 40%
are commands, the rest short questions), or the extractor fails quietly for voice-panel transcripts. The test that separates them is
in "next experiments" (E1).

### A2. Omi pendant (30 days) [live]

| stage | count | loss | label |
|---|---|---|---|
| Recordings synced | 2,263 on 30 days (126.4 h) | | [Proven] |
| Non-empty transcript (`status=done` with text) | 945 recordings (111.1 h); 1,189 `empty_transcript` (14.4 h, short clips) + 129 done-but-empty | 52% of recordings, 11% of audio | [Proven] |
| Speaker segments | 9,926 (99 h); speaker `other` 7,347, `unsure` 2,461, `unverified` 98, **`you` (the wearer) 12**, mixed 8; 0 owner labels, 0 tagged persons; `omi_wearer_samples` 0 rows | wearer attributed on 0.1% of segments | [Proven] |
| Analysed by a day review | 1,066 recordings in 33 reviews (30 ready, 3 failed: 10-27, 10-28, 11-08); 144 model calls, 23 chunks skipped | | [Proven] |
| Lines read / dropped before the model | 50,286 lines; 25,178 dropped as routine, 1,546 distress, 768 media, 57 Zoe's own | | [Proven] |
| Candidates proposed (after verify) | **106**: follow_up 27, list_item 22, reminder 24, owner_fact 16, person 16, calendar_event 1; model items dropped by verify ~590 (bad_kind 147, no_intent 95, not_first_person 78, not_durable 60, bad_person 48, not_exact 34, ...) | | [Proven] |
| Offered to the member | **0** | 106 -> 0 | [Proven]; cause = flag: `ZOE_OMI_DAY_REVIEW=shadow` ("no mention, no raise, no voice", `day_review_raise.py:3-5`), `ZOE_DAY_REVIEW_RAISE` off [code] |
| Approved / decided | 0 (`decided_via` null on all 106, all `pending`) | | [Proven] |
| Durable rows (`written_ref`) | 0 | | [Proven] |

**Biggest loss: the offer stage, by flag** (106 -> 0). The candidates exist; nobody is shown them. Second: **speaker attribution**,
missing input (the wearer is not enrolled: 0 wearer samples, 12 of 9,926 segments read as `you`), which starves `owner_fact`
(it needs first-person words with no `someone_else` evidence, `omi_day_review.py:24-28`). Third, job reliability: 3 of 33 reviews
failed, the 11-10 night was SIGTERMed during `omi_day_review`, the 11-11 review took ~25 min for 90k prompt tokens, and the re-pass
(better ASR + ECAPA) is merged dark and its one rehearsal failed at setup in 5 s with a RuntimeError [record: lane 5]. The empty
transcripts are mostly short clips (11% of audio), not the main loss.

### A3. Calendar, contacts, HA as supply [live]

| source | count | usable on a turn? |
|---|---|---|
| Calendar `events` | 18, all `source=local`, **0 upcoming**, 4 created in 30 d; `calendars` 0 (no synced external calendar) | ~0 |
| People / contacts | 26 rows (17 live), **0 birthdays**, `person_important_dates` 0, `contact_fields` 0, `source_contacts` 0, relationships 3 | thin |
| Reminders | 9 (6 active), 1 created in 30 d | ~0 |
| Lists | 4 lists, 124 items | yes, but a command surface, not life knowledge |
| HA presence (`ZOE_HA_PRESENCE=shadow`) | 190 observations, **all `unknown`** | 0 |

### A4. Conclusion (rows per day)

| supply line | durable rows/day now | unharvested signal/day |
|---|---|---|
| Panel/chat | ~0.1 (1 row in 9 days; 1.6/day averaged over 30 d, front-loaded on 10-12..10-14) | ~13 member turns/day, ~0 extracted |
| **Omi pendant** | 0 | **3.5 candidates/day already proposed and held in shadow**, from ~3.7 h/day of transcript; more once the wearer is enrolled |
| Calendar/contacts/HA | ~0 | ~0 (no external calendar, no birthdays, presence blind) |

[Experimental] If Zoe is to "know your life", the pendant has the most unharvested signal by more than an order of magnitude, and the
first gate on it is a flag plus an approval surface, not a model. Turning the offer on also needs the speaker problem fixed, or
owner facts stay rare and person/follow-up candidates carry the wrong speaker.

## B. Why recall misses

### B1. The tests a row must pass before `ZOE_RECALL_EVERY_TURN` injects it [code]

| # | test | where | effect on real turns |
|---|---|---|---|
| 0 | Mode `on` (live: `shadow` => measured, never injected) | `recall_every_turn.py:59-66`, `:366-368` | today nothing is injected at all |
| 1 | Message non-empty, <= 512 chars | `:109-110` | - |
| 2 | Not a guest id | `:113-114` | 64 lines skipped `guest` [record: lane 1] |
| 3 | Household traffic and not a harness id (unless `ZOE_RECALL_EVERY_TURN_HARNESS`) | `:115-118` | - |
| 4 | Speaker verdict: on a spoken turn the gate must have CONFIRMED the speaker (no verdict = no) | `:121-123` | an unverified voice is skipped |
| 5 | Not an off-record turn | `:126-127` | - |
| 6 | "Claimed by another block": if recall floor / verify / continuity / hop / offer / risk / raise / brief fired, nothing is injected (only measured) | `zoe_flue_client.py:3007-3011`, `recall_every_turn.py:366` | 2 of 17 eligible member turns [record: lane 1] |
| 7 | Warm-up: no cached index for the member => that turn carries nothing (build starts in background; TTL 120 s, stale-while-revalidate) | `:249-256`, `:291-293`, `:175` | 2 of 19 lines `warming` [record: lane 1]; with ~24 zoe-data restarts/day the first turn after each restart misses unless the wake warm prewarmed |
| 8 | Pool = the member's own approved durable rows with authority >= owner-derived (`user_stated_derived`); inferred rows never | `:211`, `memory_service.py:1846-1851` | pool=54 for the member [record: lane 1]; of the member's 38 approved rows written in 30 days, 22 are model-inferred and excluded [live] |
| 9 | Trigger prefilter: the turn must share a stem KEY, a clock time, or (row routine AND turn plan cue) | `:269` | keys = first 4 letters of words >= 5 chars (`recall_gate.py:204-212`), stop words / months / WEEKDAYS dropped (`:287`) |
| 10 | Score >= threshold 2.0: name 2.5, weak (sentence-initial) name 2.0 if rare, rare key (df<=2) 1.5, mid key (df<=6) 0.8, common 0.3, lexical capped 4.0, clock 2.0, routine x plan 1.2. No similarity signal: rows enter as source `pool`, so `_W_SIM` never applies | `recall_gate.py:80`, `:97-98`, `:544-588`, `recall_every_turn.py:271-272` | **one shared distinctive word = 1.5 < 2.0 => no injection**; a lower-case relationship word ("my sister") is never a name |
| 11 | Authority cap: a row with auth < 3 cannot pass on key overlap alone | `recall_gate.py:584-586` | moot here (test 8 already excludes them) |
| 12 | Fresh re-read by id within 0.25 s; text unchanged | `:297-305`, `:45` | a slow Chroma read drops the whole turn (`skipped=error`) |
| 13 | Speakable: no private class (health, money, mood, private), no off-the-wall class (health, money, grief, family_conflict, affect), not muted; other-member rows only on owner pull | `:131-152` | silently removes affect/health rows (affect is ON for the household) |
| 14 | Not forgotten / tombstoned (fails closed) | `:155-163` | - |
| 15 | Budget: <= 3 rows, <= 90 est. tokens, rows cut at 160 chars | `:39-43`, `:309-316` | - |

**Session carry-over bug from the 11-05 replay: NOT fixed on main [Proven][code].** `recall_gate.decide` still re-admits a held row
for `sticky=2` turns with no check that it still shares a key/name/clock with the current turn (`recall_gate.py:82`, `:618-620`); no
commit since #2293/#2303 changes it (`git log` on recall_gate since 11-04: only #2303's trace). It does not affect
`ZOE_RECALL_EVERY_TURN`, which keeps no session state (`recall_every_turn.py:259-261`); it blocks `ZOE_RECALL_GATE=enforce`.

### B2. Lab result [Experimental, synthetic]

See "Lab branch and results" for the table. In one line: the production test as coded injects the needed fact on 18/20 direct
turns, 2/20 oblique turns, 0/20 irrelevant turns. Lowering its threshold to 1.5 gives 8/20 oblique but injects on 10/20 irrelevant
turns (common-stem collisions such as "like", "play", "time", "morning"). bge-small cosine RANKS the needed fact top-3 on 14/20
oblique turns, but no threshold separates them from irrelevant turns (0.56: 12/20 oblique vs 9/20 irrelevant; 0.64: 2/20 vs 2/20).
A 150-token card with activation uncorrelated to the asks covers 8 of 40 facts and hits 4/20 direct, 5/20 oblique.

### B3. What it implies [Speculative, from synthetic numbers plus the live counts]

- **Not a threshold.** Lexical: lowering it trades oblique hits for false injects 1:1. Cosine: the same, at every point of the sweep.
- **The real-turn 1 in 17 is mostly a speech-shape problem, plus a store problem.** The gate is good on direct speech, and household
  speech to a panel is rarely direct about a stored fact. With a 54-row pool and ~0.1 new rows per day, many turns also have no
  relevant row to find.
- **Different eligibility rule, not a better matcher:** move from "inject only when a test fires" to "always carry a small fixed
  budget": the member's top-k by similarity (k=2-3, ~60-90 tokens, oblique top-3 14/20 here) and/or a card of the most-used facts.
  Both pay rows on every turn; the open question is whether the 4B brain ignores irrelevant rows (not measured here; see E3). The
  card alone is weak unless activation tracks what is asked; similarity top-k has the better coverage.
- Cheap safe fix inside the current design: let a single rare key plus a relationship word (sister, mum, partner, dog...) count as
  a name, closing the "camping"/"saving"/"my sister" class of misses without the common-stem collisions (needs its own lab run).

## C. Said vs did on promises

### What `missed` means [code]
- A promise is recorded only from Zoe's own reply text (`commitments.py:316-341`): a first-person future cue ("I'll remind you",
  `lexicons_data/en.json` commitments.promise) AND a resolvable time in one sentence; questions and offers/conditions are skipped by
  the guards ("want me to", "would you like", "I can", "if you", "let me know" ...; `_extract_with` `:342-366`). So the ledger counts
  **promises, not offers**.
- At the due time `sweep` -> `_check_remind` (`:769-791`): a reminder for the member created at most 15 min before the reply and due
  within 30 min of the promised time, naming the subject (`reminder_covers` `:591-616`) => `kept`. Otherwise in shadow the row closes
  `missed` with `shadow:would_fulfil` (subject known, within the 20-min grace) or `shadow:would_ownup` (`:777-779`).
  `missed` = "no matching reminder existed; in enforce Zoe would have made it late or owned up".

### What the 33 rows are [live]
`commitments` GROUP BY: 31 `missed`/`shadow:would_fulfil`, 2 `open`; all kind `remind`; `tools='[]'` on all 33; 1 user; 33 sessions
and 33 replies; **session prefix `replay-` on 33 of 33**; due time 07:00Z on 33 of 33; promise lead 15-39 h ("tomorrow"). Created at
all hours (00-23 local), matching nightly and PR-time replay runs, not the member's usage pattern.

Why this happens [code]: `tests/replay_samples.py:275` (the voice replay gate) calls the brain as the member (`--user` default, `:769`)
in a `replay-<hex>` session with `replay_isolation`, so its writes are not committed by design. `_claim_guarded`/`_claim_guarded_text`
return the reply untouched for replay (`brain_dispatch.py:917`, `:989`), but `_provenance_tracked_stream` -> `_provenance_commit` ->
`commitments.schedule_record` (`brain_dispatch.py:847-868`, `:873-907`, `:1017-1025`) has no replay check and `_walled`
(`commitments.py:399-412`) checks only tracked/distress/off-record. [Speculative] the corpus holds one "remind me tomorrow at 3"
style sample (one row per run, identical due time); every replay then records a promise that, by design, never gets a reminder. Ledger rows from household sessions: **0** [Proven].

### Does the 0-tool-calls finding explain it? [code+live]
No. The ledger rows are replay turns. On household turns the ledger can barely record a broken promise at all: the ledger reads the
reply AFTER the write-claim guard (`brain_streaming` wraps `_claim_guarded` inside `_provenance_tracked_stream`, `:1017-1025`), and
the guard (#2287, #2312: standing promises "I'll remind you at 2:30" with no write this turn are rewritten to an honest line,
`write_claim_guard.py:134-145`, `:374-391`) removes the very sentence the ledger would match. So on a household turn a promise with no
tool call is corrected (and leaves no ledger row); a promise with a real write is recorded and should close `kept`. `WRITE_CLAIM_BLOCKED`
29 and `WRITE_CLAIM_CORRECTION` 16 lines, all synthetic, 0 household [record: lane 2]. Combined: in the window there is no evidence
that the household brain promised anything [Proven count; Experimental meaning: both detectors are lexical]. #2309 (reply-to-offer
binding, `intent_router.py:2274`) turns a "yes" after "want me to remind you?" into a reminder in the fast tier; it is the offer path,
which the ledger correctly ignores.

### Smallest change, and the number that proves it
1. **Stop the contamination** (one condition): skip `commitments.schedule_record` when the turn carries `replay_isolation` (pass the
   flag into `_provenance_tracked_stream`, or have `_walled` return `replay` for a `replay-` session). Then purge or relabel the 33
   rows (an owner step; this lane writes nothing).
2. **Count promises made, not only promises that survived the guard**: feed the ledger the PRE-guard reply plus the guard verdict and
   add a terminal status `corrected` (guard rewrote it in the same turn). Then every timed promise the brain makes is either a dated
   reminder (`kept`) or corrected in the same turn (`corrected`), and nothing silently disappears.
3. Optional, the "always becomes a reminder" variant: in enforce, when the guard would correct a `remind` promise that has a parsed time
   and subject, create the reminder through the existing path (`commitments._make_reminder`, `:618`) before the reply is spoken and let
   the promise stand. This is a write from model text; it needs an owner decision.

**Proof number:** on household traffic only (`traffic_class=household`, no `replay-`/`qa` sessions), with `ZOE_COMMITMENTS=enforce`:
`(kept + corrected) / promises_made` = 1.00 over >= 20 promises, and report `kept / promises_made` beside it (the "she did it"
share). Today's denominator is 0, so first measure how often the household brain promises at all.

## Lab branch and results

Branch `labs/recall-match-lab` in `~/.worktrees/recall-match-lab`, commit **08296d690** (one commit, not pushed, no PR). Files:
`labs/recall-match-lab/{fixtures.py, run_lab.py, README.md}` (318 lines of code) and a Child DOX Index entry in `labs/AGENTS.md`;
`tools/audit/validate_structure.py` passes. Run under `flock -x /tmp/zoe-test-lane.lock`, CPU, bge-small-en-v1.5 from the local
fastembed cache (`local_files_only`, `HF_HUB_OFFLINE=1`; nothing downloaded). Production method (i) is the REAL
`recall_every_turn._ranked` + `recall_gate` code imported as a library on an in-memory index (not a re-implementation); every row
given authority 4 and treated as speakable, so tests 1-8 and 12-14 are not exercised. Note the recall gate's own similarity constants
are for MemPalace's MiniLM, not bge-small; method (ii) is a separate signal.

All [Experimental]; synthetic, 60 turns, one invented world, written by the experimenter: nothing certain about real household speech.

| method | direct hit | oblique hit | irrelevant turns injected | relevant turns, wrong rows only |
|---|---|---|---|---|
| (i) production as coded (threshold 2.0) | 18/20 | 2/20 | 0/20 | 1/40 |
| (i-b) production, threshold 1.5 | 20/20 | 8/20 | 10/20 | 5/40 |
| (i-c) production, threshold 0.8 | 20/20 | 9/20 | 11/20 | 6/40 |
| (ii) bge-small cos top-3 >= 0.67 (tuned on even turns), all 60 | 19/20 | 0/20 | 1/20 | 1/40 |
| (ii) same, held-out odd turns only | 10/10 | 0/10 | 1/10 | 1/20 |
| (iii) standing card (8 facts, ~146 tok, always present) | 4/20 | 5/20 | 20/20 | 31/40 |
| (iv) union (ii)+(iii), all 60 | 19/20 | 5/20 | 20/20 | 16/40 |
| (iv) union, held-out odd turns only | 10/10 | 2/10 | 10/10 | 8/20 |

Cosine rank of the needed fact: direct top-1 20/20; oblique top-1 12/20, top-3 14/20, top-5 16/20. Sweep (top-3): threshold 0.50 ->
oblique 13/20, irrelevant 12/20; 0.56 -> 12/9; 0.60 -> 6/7; 0.62 -> 6/4; 0.64 -> 2/2; 0.68 -> 0/0 (direct 17/20).

## Next experiments (pass/fail)

| # | experiment | pass | fail |
|---|---|---|---|
| E1 | Why idle consolidation extracts nothing from panel turns: replay the extractor offline on a synthetic panel-style session (commands + a few self-statements), and add a counts-only `facts=` field to `MEMORY_IDLE_CONSOLIDATE` (model returned N items before verify) | extractor returns >= 1 fact per planted self-statement on synthetic panel text; live `facts=` > 0 on some member sessions | 0 on synthetic self-statements = extractor bug; live `facts=0` with synthetic pass = speech carries no facts (supply must come from elsewhere) |
| E2 | Omi offer: owner flips `ZOE_OMI_DAY_REVIEW=on` + `ZOE_DAY_REVIEW_RAISE` for 14 days (owner decision; this lane flips nothing) | >= 1 review offered on >= 10 of 14 days and >= 30% of candidates approved; durable rows from Omi >= 1/day | approvals < 10% (candidates are noise: fix verify first) or the raise never fires |
| E3 | Wearer enrolment for the pendant (`omi_wearer_samples` 0 rows), then re-run one day review | `you` share of segments > 20% on a day the owner wore it; owner_fact candidates up | `you` still < 1% |
| E4 | Lab: always-on similarity top-k (k=2, <= 60 tokens) vs gate, scored on the brain's answer (not retrieval) in the lab sidecar with a synthetic store, 60 turns | oblique answers using the fact >= 50% with irrelevant-turn answer quality unchanged (blind judge, >= 95% of baseline) | irrelevant-turn quality drops > 5% (the 4B is distracted: keep gating) |
| E5 | Lab: "relationship word + one rare key counts as a name" rule in `_score`, same 60 turns | direct 20/20, oblique >= 6/20, irrelevant <= 1/20 | irrelevant > 2/20 |
| E6 | Commitments: skip replay turns, record pre-guard text with a `corrected` status; 14 days household only | denominator >= 20 promises; (kept + corrected) / made = 1.00 | any household promise neither kept nor corrected; or denominator < 5 (the brain rarely promises; drop the metric) |
| E7 | Recall gate carry-over fix (sticky re-entry needs a shared key) and re-run the 11-05 synthetic set | later-turn add precision >= 0.8 | < 0.6 |

## Could not verify

- **Why panel extraction returns zero** (A1): needs turn text or a new counts-only marker (E1). Not read, by the privacy rail.
- **Turn kind per turn** (command vs conversation): no tier marker on `chat_messages` (metadata carries only `traffic_class`,
  `user_id`); the ~40% non-brain share is inferred from brain-turn markers vs chat rows [Experimental].
- **Turn-level linkage of rows**: `user_turn_id` on memory rows matched a chat message id only 3 times (different id spaces); the
  per-session linkage (15 of 62 sessions) is the proxy.
- **The 394 vs lane 1's 308 member turns**: filter difference not reconciled.
- **Whether the replay corpus sample is a "remind me" utterance**: inferred from 33 identical due times, not read.
- **Whether `tools='[]'` on replay turns means no tool call or that replay isolation hides tool sentinels**: not traced.
- **Household promise rate**: 0 ledger rows and 0 household WRITE_CLAIM firings; both detectors are lexical, so a promise phrased
  outside their cues is unmeasured.
- **Omi re-pass setup failure cause** and the 3 failed day reviews: counted from lane 5 / table status, not root-caused.
- **The standing card with real activation**: the lab used seeded random activation (a lower bound); real `access_count` is in
  MemPalace metadata but was not used, to keep the lab off live data.
- **Brain effect of any always-on block**: retrieval only, by design; no llama call was made.
