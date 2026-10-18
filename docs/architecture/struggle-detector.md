---
type: architecture
title: Struggle detector - notice where Zoe struggled, propose how to fix it (Phases 1-2 of the self-correction loop; Phase 3 designed)
status: phase-1 built (ZOE_STRUGGLE_DETECT default shadow); phase-2 built (ZOE_STRUGGLE_PROPOSALS default shadow); phase-3 designed here, not built; failure capture built (ZOE_STRUGGLE_OUTCOMES default off)
owner: services/zoe-data (struggle_signals.py, struggle_detector.py, struggle_digest.py, struggle_proposals.py, turn_outcomes.py, traffic_class.py, routers/struggles.py)
updated: 2026-10-19
---

# Struggle detector

Owner intent: Zoe should *recognise issues and realign* by herself. That is a five-step loop; this document is its
contract. **Phase 1 (this build) is step 1: notice.** It detects, records and reports. It changes nothing Zoe says
(in the default `shadow` mode) and nothing about how a turn is answered.

| step | what | phase | status |
|---|---|---|---|
| 1. notice | per-turn struggle signals, stored + reported | 1 | **built** |
| 2. explain overnight | the night window clusters the week's events into problem statements (code groups; the 12B only rewords the counts) | 2 | **built** |
| 3. propose | a cluster becomes a stored proposal with a fix class and its evidence ids; failed->success pairs become labelled router examples | 2 | **built** |
| 4. prove | a retrained router is scored on held-out failures AND the golden eval + voice replay before it ships | 3 | designed below |
| 5. apply | router retraining adopted automatically **within its gates**; code changes only as PRs; **rocks never change** | 3 | designed below |

## Where it sits (and why it cannot hurt a turn)

```
turn ──► semantic_router.route ──(note_route: deque append)──┐   (+ skybridge_service gate ── note_skybridge: deque append)
     ──► brain_dispatch._log_lane ──(note_brain: deque append)┤   in the turn: two O(1) appends (p99 0.03 ms measured)
     ──► reply out
         └─ existing post-turn task ─► struggle_detector.observe ─► bounded queue ─► ONE worker:
              voice: routers/voice_tts._run_voice_memory_passes      build Turn (joins the notes) ─► detect(prev, cur)
              chat:  routers/chat._persist_memory_candidates          ─► struggle_events row(s) ─► STRUGGLE log line
```

* The hooks sit in the turn's EXISTING post-turn background tasks, never on the reply path. `observe` is a
  `put_nowait`; a full queue (`ZOE_STRUGGLE_QUEUE_MAX`, 256) drops and counts. Every exception is swallowed and counted
  (`stats()`: `hook_errors`, `process_errors`, `store_errors`, `dropped_full`, ...). A store write is bounded (5 s).
* A speculative voice turn that is cancelled never reaches the hook (`_spawn_bg` defers the memory passes until commit).
* Harness / test traffic is never recorded: `replay-*` / `test*` / `probe*` / `ci-` / `e2e` / `bench` panels and
  synthetic users (`user_filters.is_synthetic_user`).
* The voice-path files touched are `routers/voice_tts.py` (one call at the top of `_run_voice_memory_passes`, one kwarg
  at the streaming finaliser) and `fast_tiers.py` (the `on`-only ack). Replay-gated like every voice change.

## The signals (`struggle_signals.py`, pure)

Pair signals compare the turn with the PREVIOUS turn of the same conversation (voice: the panel; chat: the session) by
the same user; on a voice panel the previous turn by another identity also counts (the panel's identity can flip
between guest and member mid-conversation) but then no words are kept. A pair event is ABOUT the previous turn (the one
that failed): its domain, path and shape are that turn's.

| signal | fires when | precision guards (each pinned by a hard-negative test + a negative control) |
|---|---|---|
| RE_ASK | the same request again within `ZOE_STRUGGLE_WINDOW_S` (45 s) after normalisation (wake word, contractions, number words, plurals, fillers) or with only a polite lead-in dropped - also reaching back over up to 3 earlier turns ("what is it?" asked twice around another question) | repeatable commands (next / louder / yes ...); changed slot (time, number, place, name, on/off); a reach-back REPHRASE without domain evidence on both turns |
| REPHRASE | same domain (by evidence: the head's domain; a similarity score >= 0.75 - or >= the router's own 0.62 threshold when similarity alone routed the turn because the head did not decide; a fast reply's own phrasing; an un-vetoed Skybridge claim), other words, no new slot (a slot Zoe's reply already said is not new): shared content words (one at similarity >= 0.55, two at >= 0.45), a pure refinement, or the same lead + time anchor + time frame; the short "what is it?" / "which one?" right after a COUNT-ONLY fast answer ("You have 1 event today."); and, reaching back, an earlier same-domain ask | follow-up openers ("and", "what about"); different fact; added time slot |
| CORRECTION | a repair cue opens the next turn ("no, I said ...", "I meant ...", "not that one", "you misheard"), or opens its second sentence; or the contrast "it's actually X, not Y" where EVERY content word of Y was in the previous exchange (the user's misheard words or Zoe's reply) | "no worries / no thanks / no, it's fine"; a bare "no" answering Zoe's question; "I meant to ..."; a contrast with no real X ("actually I'm not hungry") or a Y new to the exchange |
| NEGATIVE_FEEDBACK | `conversation_feedback.classify` says thumbs_down / correction, or a complaint about the exchange ("you're not listening", "that doesn't make sense") | kind verdicts; sentences merely containing "wrong" |
| CANT_DO | the reply is a capability disclaimer (superset of the replay gate's `_CANT_DO_RE`) on a routed domain, or the brain's degraded fallback (ERROR, any domain) | policy refusals (guest / PIN / privacy); "I can't wait"; unsupported asks (no domain) |
| FALLTHROUGH | the router placed the turn in a fast domain and the brain (or ANOTHER fast domain) answered; or Skybridge recognised a fast domain, the router vetoed it and the brain answered | two witnesses: the two-stage domain AND its similarity score >= 0.70 (or head >= 0.9 for domains without examples); a timed-out head needs similarity >= 0.65; similarity alone needs >= 0.80; a vetoed Skybridge claim needs that domain's similarity >= 0.75 or the head's own top pick; a head-only witness (a domain with no similarity examples) also needs a cue word for that domain, and a sum (`calc_speech`) is never attributed to a fast domain (a calculator follow-up was filed under `music`, 2026-10-15) |
| VERBOSE | a short factual question (<= 12 tokens) got a reply over budget (voice 280 chars / 200 on a fast domain; chat 700 / 500) | open requests (story, explain, why, recipe, tell me about ...) |
| MISHEAR | a wake-word-only transcript, or a voice corrected repeat within 30 s (same frame, one or two words swapped for sound-alikes - including a number homophone heard for a word, "two on the light" -> "turn on the light" - or re-ordered, or a garbled lead-in dropped), or a turn the STT snap recovered (or, in shadow, would have) - `stt_snap` hands it over with `note_snap`, fix `stt_snapped` | a different word that does not sound alike ("milk" -> "eggs"); polarity / number / place swaps; typed text (a typo fix is a RE_ASK) |
| ABANDON | the streamed voice reply ended early with words already heard (barge-in or disconnect - indistinguishable server-side) | an unheard / complete reply |

Each event carries: ts, channel, panel, user scope, prev + current turn ids (detector-minted, `t-<hash>`), signal,
confidence, fix type, domain predicted vs served, path served (`fast` / `brain:<lane>`), latency (route note to
completion), a text-free `pattern` (`<domain>|<lead>:<time frame>:<anchor>`, e.g. `weather|what:future:today`) and
JSON reason codes. Suggested fix types: `router_training`, `router_latency`, `capability_gap`, `stt`, `verbosity`,
`answer_quality`, `stt_snapped` (a misheard command word the STT snap recovered: [voice-pipeline.md](../knowledge/voice-pipeline.md) -> *STT snap*).

## Privacy

* A member's row may carry `summary` = the PII-scrubbed (`memory_service.scrub_pii`, on the RAW words) display form (lower case, wake word stripped, never folded, so a forget's name pattern finds it) of the user words, <= 160
  chars - never Zoe's reply. It is the same text `chat_messages` already keeps for that member.
* No words for: guests (and a voice turn whose speaker the gate REJECTED, recorded as a guest's); a turn queued before a forget of that user (before a delete it is not recorded at all); off-the-record / distress turns (`memory_provenance.is_off_record`); turns naming a forgotten
  entity (tombstone + `memory_forgotten.matches`); a pair across two identities; any lookup failure.
* Reports show example words only from the VIEWER's own rows (admin endpoint, CLI, morning line). Another member's
  words never appear in anyone else's report; guest rows have none to show.
* Forget: `memory_forget_entity` -> `struggle_detector.erase_entity` (after the night mind, fail closed: a store failure
  answers "I can't say it's forgotten yet"). User delete: `MemoryService.delete_user` -> `struggle_detector.delete_user`
  (fail closed). Only a missing table counts as "nothing stored".
* Retention: rows older than `ZOE_STRUGGLE_RETENTION_DAYS` (90) are swept by the worker at most once an hour.

## Flags

| flag | default | meaning |
|---|---|---|
| `ZOE_STRUGGLE_DETECT` | `shadow` | `off` = no detector machinery runs (no queue, worker, notes or rows; the hooks return after one env read and only bump the in-process `observed` / `dropped_off` counters); `shadow` = detect + store + report, nothing user-facing; `on` = shadow + a one-line ack of a bare "that was wrong" that no tier answered + the owner's line in the admin morning brief |
| `ZOE_STRUGGLE_WINDOW_S` | 45 | pair-signal window (clamped 5..300) |
| `ZOE_STRUGGLE_RETENTION_DAYS` | 90 | row retention |
| `ZOE_STRUGGLE_QUEUE_MAX` | 256 | worker queue bound |

## Reports

* `GET /api/system/struggles?days=7&scope=me|guest|all&top=20` (`routers/struggles.py` over `struggle_digest.digest`) - admin PERSONS only (a panel device token is refused).
* `scripts/maintenance/struggle_report.py [--user ID | --guest] [--days N] [--json]` - read-only CLI.
* Nightly: `STRUGGLE_SUMMARY window=1.0d total=N signals=... top=...` beside `MEMORY_REJECT_SUMMARY` (counts only).
* Mode `on` only: one line in the admin's morning brief (`proactive/triggers/morning_checkin.py`).

## Evaluation (2026-10-14)

Offline replay of the app log (`scripts/maintenance/struggle_replay_eval.py`, read-only, LOCAL ONLY; the labelling
sheet and labels live outside the repo). The log held 108 real-panel voice turns in the window (the log only reaches
back to 2026-10-04); every one of the 107 with a predecessor was hand-labelled. Numbers only:

| signal | TP | FP | FN | precision | recall |
|---|---|---|---|---|---|
| MISHEAR | 6 | 0 | 2 | 1.00 | 0.75 |
| FALLTHROUGH | 4 | 0 | 2 | 1.00 | 0.67 |
| CORRECTION | 3 | 0 | 1 | 1.00 | 0.75 |
| REPHRASE | 3 | 0 | 3 | 1.00 | 0.50 |
| NEGATIVE_FEEDBACK | 2 | 0 | 1 | 1.00 | 0.67 |
| ABANDON | 1 | 0 | 0 | 1.00 | 1.00 |
| RE_ASK, CANT_DO, VERBOSE | 0 | 0 | 0 | n/a | n/a |

Honest caveats: these are IN-SAMPLE (the thresholds were tuned on this same sheet - before tuning FALLTHROUGH was 4/8
and a polarity swap produced a false RE_ASK); the counts are small; CANT_DO and VERBOSE cannot be judged from the log
(brain reply text is not logged) and chat turns are not in it. The weather-forecast episode of 2026-10-14 (a forecast
question answered with current conditions, then a re-ask about the day's high) is caught: FALLTHROUGH + REPHRASE on the
re-ask, MISHEAR on the corrected repeat. Re-run after a week of shadow data with the stored rows as the sheet.

### Recall round (2026-10-14 evening, live shadow)

The first night live in shadow flagged 2 events and missed 4 clear struggles: an ask chain after a count-only calendar
answer, a number homophone heard for "turn", an "actually X, not Y" correction, and a Skybridge calendar claim the router
vetoed and the brain served slowly. Each shape now has a rule, invented tests, hard negatives and a negative control
(`tests/test_struggle_recall.py`). Same replay, extended to 130 hand-labelled turns (local only, numbers only):

| labels | before P | before R | after P | after R |
|---|---|---|---|---|
| frozen (labelled before the change) | 1.000 (21/21) | 0.553 (21/38) | 0.967 (29/30) | 0.763 (29/38) |
| one disclosed relabel (an identical question re-asked two turns later, ~35 s: the written RE_ASK definition) | 1.000 | 0.538 (21/39) | 1.000 (30/30) | 0.769 (30/39) |

All four of the evening's misses are caught. Still missed (stated, not hidden): rewordings with no shared word and no
domain evidence ("how are you" -> "I want to know how you are"), garbled STT that is never repeated, and a correction
that says "it's okay, you don't need to" without a cue.

## Phase 2 - explain + propose (built; `struggle_proposals.py`, migration `0057`, flag `ZOE_STRUGGLE_PROPOSALS`)

The night window's `struggle_proposals` job (`scripts/maintenance/zoe-struggle-proposals.py`, after the night mind, before the pendant day
review; best-effort, <= 8 min; the window unit's `--jobs` list carries it) reads the last 7 days of `struggle_events` and turns recurring
problems into **proposals**. **Code groups, counts and thresholds; the 12B writes one sentence per proposal from those counts and nothing else.**

```
struggle_events --(code)--> classify -> cluster by (kind, text-free key) -> recurrence thresholds -> struggle_proposals + struggle_proposal_events (evidence ids)
                                                                                       |-- sentence: template, or the 12B's rewording of the SAME counts (validated word by word: only words from its own facts plus a fixed list of grammar / generic words, no number the facts do not hold - it can rearrange the counts but has no word to introduce a name, a place or a cause - else the template)
                                                                                       |-- morning brief line (mode `on`) / admin API / CLI
                                                                                       `-- accept -> router_example: the router self-train queue (Phase 3 reads it);  others: a task stub + ledger line (no PR)
```

**Kinds** (from the event's fix type; `router_latency` is a sidecar fault and is counted, not proposed): `router_example` (a candidate
training pair), `intent_gap` (a missing or wrong handler), `answer_quality` (prompt / reply-format: wrong brain answers and over-long
replies), `stt_term` (a keyterm / homophone candidate, keyed by reason code + the STT-snap intent tag, never by words).

**Recurrence (what keeps noise out).** A cluster becomes a proposal only if it has at least `ZOE_STRUGGLE_PROPOSAL_MIN_EPISODES` (3)
separate **episodes** (events of one user within 10 minutes are ONE episode - a five-times re-ask chain is one struggle) on at least 2
distinct user-days, with a mean event confidence of at least 0.6. A generic unrouted shape (no domain, no time orientation, no anchor)
cannot be an `intent_gap`. Synthetic and harness users never count; guest rows count as wordless struggles. Reruns update the same
proposal (fingerprint = hash of kind + key); a `proposed` one that stops recurring is withdrawn; a `rejected` one stays suppressed for 30
days and returns only if it recurred since; an `accepted` / `applied` one is never reopened or re-decided, only re-counted. The nightly cap
(`MAX_PER_NIGHT`, 20) limits NEW proposals only: eligible clusters are ranked best first, the overflow is counted as `deferred` and created on a
later night, and an eligible cluster that already has a `proposed` row is refreshed and kept however it ranks (ranking never withdraws).
The 12B rewords a proposal's counts as placeholders (`{episodes}`, `{days}`, `{people}`, `{n:SIGNAL}`) that code renders from the proposal's
own fields; a digit written by the model, a placeholder in front of another field's unit noun or an unknown signal falls back to the template.
The night window runs the proposal pass every night (the quiet-night skip and its precheck were removed on 2026-10-18, owner decision: the 12B
runs every night), so aged-out `proposed` rows are recounted and withdrawn without any special check.

**Failed -> success labelling (how a failed turn gets its label).** The detector records, in the text-free `detail` of a pair event by one
member (`_follow_up`), the fast domain that answered the turn that REVEALED the struggle (`next_domain`; `resolved_route`: served by a fast
path, not a can't-do, not cut off, domain evidenced), how it was served, and when the failed turn finished (`about_ts`). A failed turn that
the brain (or a different domain) served, followed in the same conversation by a re-ask / rephrase / correction that a FAST domain then
answered, takes that domain as its label: `router_example`, `label_source = followup` (strong). The follow-up must not itself have been a
struggle (it was not re-asked or corrected later and has no single-turn event of its own) or it proves nothing and the event keeps its own
fix type. A router-training event with no such follow-up is a weaker `router_example` labelled with the router's own belief
(`label_source = router`). Pairs with different resolved domains are different proposals; a guest's or wordless pair makes no example.

**Privacy (the design point).** A proposal row holds counts, a text-free shape (`<domain>|<lead>:<time>:<anchor>`) and a fixed-vocabulary
sentence - never a household utterance, and the 12B is shown nothing else. A `router_example`'s words are NOT copied: the Phase 3 seam
`queued_router_examples` resolves them at read time from the member's own `struggle_events.summary` (already PII-scrubbed, member-only,
forget-gated), the failed turn's words only, and drops any example that names a thing the member has forgotten since (a ledger lookup
that fails drops it). `struggle_detector.erase_entity` / `delete_user` take the rows out of the proposals first (fail closed;
`struggle_proposals.forget_events` / `forget_user`: links dropped, counts recomputed, the sentence reset to the template, a proposal with no
evidence left deleted); every read joins back to `struggle_events`, so an orphaned link can never show a proposal or an example;
`reconcile` clears the leftovers each night. Audio: `~/.zoe-voice-samples` keeps the real utterance when `ZOE_VOICE_SAVE_AUDIO` is on
(a permanent regression corpus, never pruned); the seam hands out a bare FILE NAME for the failed turn (`audio_ref`, found by time -
`HHMMSS_mmm.wav` carries no turn id - and withheld when two clips are within 5 s of each other) and nothing reads, copies or moves the audio.

**Nothing applies itself.** The night job only creates / updates / withdraws `proposed` rows. `POST .../accept` records the decision and the
follow-up: `router_example` -> `{type: router_selftrain_queue, consumer: ZOE_ROUTER_SELFTRAIN, label}`; any other kind -> a task stub
(`title`, `body`, `labels`, a ready-to-paste `ledger_line` for `docs/knowledge/open-problems.md`), never a PR. Only `mark_applied` (the Phase 3
seam, an `accepted` proposal only) reaches `applied`.

**Surfaces.** Admin PERSONS only (a panel device token is refused): `GET /api/system/struggles/proposals?status=&kind=` (list, counts, mode),
`GET .../proposals/{id}` (with evidence event ids), `POST .../{id}/accept|reject` `{reason}` (a code - `not_a_problem | wrong_label | already_fixed | not_now | other` - never free text, so no utterance can be typed into a proposal); `scripts/maintenance/zoe-struggle-proposals.py`
(`--dry-run` counts only, `--list`, `--show`, `--accept ID`, `--reject ID`); a `STRUGGLE_PROPOSALS_SUMMARY` line in the night report
(`NIGHT_JOB struggle_proposals ...`, counts only); mode `on` only: one line in the admin's morning brief ("I have 2 ideas for getting better
at things I struggled with (1 router, 1 missing ability)").

| flag | default | meaning |
|---|---|---|
| `ZOE_STRUGGLE_PROPOSALS` | `shadow` | `off` = the night job is a no-op (stored rows stay readable / decidable); `shadow` = cluster + store, admin API / CLI only; `on` = shadow + the morning-brief line |
| `ZOE_STRUGGLE_PROPOSAL_DAYS` | 7 | lookback (1..30) |
| `ZOE_STRUGGLE_PROPOSAL_MIN_EPISODES` | 3 | the recurrence floor (2..50) |

`routers/chat._check_frustration` (an older in-process exact-repeat counter feeding `evolution_notice`) still exists beside this; folding it
into the proposals (one notice source, not two) is open.

**Evidence (2026-10-18).** The live detector had stored 4 events in 30 days (a shadow week), so the read-only dry run proposes nothing
(0 clusters; by kind: 2 answer_quality, 1 router_example, 1 stt_term events, all below the floor) - the loop is built ahead of its data.
The tests are synthetic (`tests/test_struggle_proposals.py`, real SQLite + the real migrations, negative controls recorded in its header).

## Phase 3 - prove + apply (DESIGN ONLY in this PR; code is the next PR)

**The closed loop.** Zoe fails a turn; the user's next attempt works; that success labels the failure; the labelled failures retrain the
router overnight; the new router is adopted only if it provably fixes real failures without breaking anything; the struggle detector then
watches whether the cluster shrank.

```
fail -> (P1) event + follow-up facts -> (P2) failed->success router_example proposals (label_source=followup) -> queue
     -> nightly: delta set (seam) -> oracle re-label -> warm-start continuation of the router -> candidate GGUF
     -> gate (a) held-out failures  AND  gate (b) golden eval + voice replay  -> ADOPT (or discard) -> record -> watch the cluster
```

**What may change: only the router** (FunctionGemma-270M stage 2, `functiongemma-router.service`). Gemma (the brain), Moonshine (STT) and
Kokoro (TTS) are rocks and never a fix target; stage 1 (the SetFit head) is hand-committed and outside the loop. `intent_gap`,
`answer_quality` and `stt_term` proposals stay human work (task stubs; code changes arrive as PRs).

**The feed.** A new flag `ZOE_ROUTER_STRUGGLE_FEED` (`off` default | `shadow` builds and scores but never adopts | `on`). In `on`, the
strong-label (`label_source = followup`) `router_example` proposals that cleared the Phase 2 thresholds are queued by POLICY
(`decided_by = policy:struggle-feed`) instead of waiting for a person: adoption is guarded by the gates below, so a manual accept adds no
safety and would stall the loop. An admin can still reject a proposal before the night (it leaves the queue and is suppressed for 30
days). `label_source = router` examples are not fed.

**Training (inside the existing machinery, not beside it).** The job extends `scripts/maintenance/router_selftrain.py` (its ratchet,
`decide_promotion`, `deploy`, `restore_lkg`, journal and scoreboard stay the only path to a live model); the delta set is a second candidate
source next to the miner's. Steps: (1) read `queued_router_examples()` (train + holdout split from `split_of`, a hash of the normalised failed text - an
example never moves between sets from one night to the next and duplicate phrasings never straddle the two); (2) re-label each example with the loop's independent grammar-constrained
oracle and keep only those where oracle and follow-up AGREE (two sources, as for mined candidates); (3) refuse any text that overlaps the
frozen 81-case corpus (the loop's existing held-out guard); (4) warm-start from `ZOE_ROUTER_WARM_START` (`runs/<gen>/merged`, **never
deleted**) on the train split plus a replay sample of the previous training set (against forgetting); (5) export a candidate GGUF.
*Time is the open constraint:* the window is capped at 65 minutes and must end before the 04:10 voice gate, CPU training on this Jetson takes
hours for a full set, and the scratch-rig eval needs a quiet box. The first Phase 3 task is therefore to MEASURE a continuation run on a
few hundred examples; the design then either fits the slice into the window's tail (after the 12B stops) or checkpoints and RESUMES across
nights, adopting when a generation completes. Nothing here assumes the measurement.

**Adoption gate. Both (a) and (b) must hold; any miss discards the candidate and the incumbent keeps serving.**

* **(a) Real improvement on held-out failures.** The holdout is the 20% of labelled failures never trained on. Both models are scored on it
  back-to-back on the same scratch rig (the loop's like-for-like rule). Requires: at least 10 holdout examples (fewer = "not enough evidence",
  no adoption); candidate `fixed - broken >= max(3, 25% of the items the incumbent got wrong)` where `fixed` = incumbent wrong and candidate
  right, `broken` = the reverse. Memorising the train split cannot pass this.
* **(b) No regression anywhere else.** (i) the full golden router eval set (the 81-case frozen corpus): **per case**, every case the incumbent
  answered correctly the candidate also answers correctly (stricter than the existing aggregate `>=`), chat false positives == 0, p50 under
  budget, same case count; (ii) the voice replay corpus `~/.zoe-voice-samples` through `voice_regression_probe.py` under
  `flock /tmp/zoe-voice-harness.lock`: said-vs-did must not regress (a previously-working command that now fails is a bug) and per-stage speed
  must not regress; a skipped replay is not a pass. Failed turns whose audio is in the corpus (`audio_ref`) are in the replay set by
  construction.
* **Instrument checks (run when the pipeline changes, not nightly):** a shuffled-label control must FAIL gate (a); an injected regression
  (one golden case flipped) must FAIL gate (b); the measurement-validity abort (incumbent live vs scratch within 10 points) stays.

**Rollback and record.** The previous generation stays serving until the new one passes; the adopted model keeps the loop's last-known-good
GGUF, post-deploy verification and auto-rollback; `runs/<gen>/merged` of every generation is kept (the lineage rule) and the last two GGUFs
stay on disk for an instant swap (`ZOE_ROUTER_HEAD=off` is the global kill switch). Everything is recorded: the journal and scoreboard rows
(candidate and incumbent scores, holdout ids by hash, fixed / broken lists as ids, gate verdicts, timings), the proposals moved to `applied`
with the generation id (`mark_applied`), and a counts-only line in the night report.

**Watching it work.** For 7 nights after an adoption the struggle detector's counts for the applied clusters (same fingerprints) are compared
with the 7 nights before: a cluster that does not shrink, or another signal that grows, raises an alarm and queues a rollback proposal
(the human decides; the loop never rolls back on a trend alone, only on the loop's own post-deploy check).

**Limits stated up front.** (1) A follow-up that succeeded is evidence, not proof - the user may have asked for something different; the
oracle-agreement step and the holdout exist for that. (2) Forgetting cannot reach into weights: a generation trained on a phrasing keeps
having learned it. Mitigation: the delta set is rebuilt from the seam each run (a forgotten example is never retrained), kept generations
are limited to the last two plus the lineage checkpoint, everything is local, and the next adopted generation supersedes. The A6
router-log redaction work in flight covers the shadow log, not this path. (3) With one household the volume of strong labels is small; the
gate's "not enough evidence" is the expected outcome for a while, and that is correct.

## Failure capture - per-turn outcomes and the owner's label check (built, flag-dark: `ZOE_STRUGGLE_OUTCOMES`)

**Why the loop had no fuel (measured 2026-10-19, counts only).** The detector went live 2026-10-14 ~12:00 (shadow) and saw ~2.5 days of
household use before the owner went away: the app log holds 51 real-panel voice turns after that (10-14 26, 10-15 21, 10-16 4, none
since), and the store took 4 events (3 voice, 1 chat). Four causes, in order of weight: (1) **little traffic** - no household turn after
10-16; (2) **69% of those turns were attributed to `guest`** (35 of 51; voice identity recognised nobody until the 10-18 flip), and a guest
row keeps no words and makes no router example, so even a caught struggle could not become a label; (3) **most use is conversation** (42
of 51 turns had no routed domain, 35 went to the brain), so router failures are rare by construction; (4) the heaviest burst (10-14
21:49-21:55) ran BEFORE the recall round (#2052, merged 23:01): replaying the same log with today's rules finds 10 events in that burst
where the live detector stored 2. Phase 2 then needs 3 episodes on 2 user-days, so 1 router-training event proposes nothing. Beside those:
no per-turn record existed (a turn that worked left no trace, so there was no denominator), the handler / tool that ran was never logged,
`chat_feedback` has 0 rows (a guest cannot write one, and the panel's voice surface has no thumbs-down), and 97% of brain turns are the
harness, which nothing tagged.

**What is captured now, per turn** (`turn_outcomes.py`, migration `0058`, written by the detector's ONE worker after the reply - never on
the reply path): the route (`route_domain` + `route_conf` + `route_source` two_stage | two_stage_failed | similarity, `head_top`); what
EXECUTED it (`handler` = `tier:<tier>/<intent>` from `fast_tiers.resolve`, `intent:<name>` from `intent_router.execute_intent`,
`skybridge:<domain>.<action>`; `tools` = the brain's tool NAMES from the Flue `__TOOL__` phase=start sentinels); the `outcome` (did |
cant_do | fell_through | brain | error | interrupted); `path_served`, `latency_ms`; and `traffic` (`traffic_class`: household | harness - a
harness panel, a harness session such as `replay-*`, a synthetic user, or a `ZOE_HARNESS=1` process). No audio, no reply text. In-turn
cost: the hooks are deque appends behind two env reads (`test_note_overhead_is_tiny`, Jetson 2026-10-19: p50 0.008 ms, p99 0.009 ms for one
handler + one tool note per turn); the row write is one INSERT (plus one UPDATE when a later turn reveals a failure) in the existing
background worker, whose CPU per turn grows ~0.15 ms (p50 1.73 -> 1.88 ms with in-process stores; the DB round trip is async).

**Failed turns and their labels.** A row is FAILED when its outcome is cant_do / fell_through / error or a struggle signal fires about it
(an over-long answer alone does not count) - at once for a single-turn signal, or when a later turn reveals it: a re-ask, rephrase,
correction, "that was wrong", or the chat **thumbs-down / correction button** (queued to the same worker, which marks the rated turn
when that is the session's newest answer - an older one is not guessed at - and keeps its words only while the turn is still in the
detector's 10-minute in-process history). A failed household member row keeps the member's own words under exactly the event `summary`
rules (PII-scrubbed raw, display form, <= 160 chars; never a guest's, off-the-record, distress or forgotten-entity turn; never across two
identities) and a PROPOSED route: the fast domain that answered the follow-up that worked (`followup`), else the router's own belief
(`router`). Successful turns and harness rows hold no words.

**Approve-before-train** (user-stated > confirmed > inferred). The owner reviews the week's failed turns - `GET /api/system/struggles/turns`
(admin persons, own turns and words only), `scripts/maintenance/zoe-struggle-labels.py --user ID`, and one morning-brief line when some
wait - and confirms the proposed route, gives the right one (a fast domain, or `chat` = the brain was right) or rejects it
(`POST .../turns/{id}/confirm {label?}` / `.../reject`). **Only a confirmed row with a fast-domain label reaches the router**:
`struggle_proposals.queued_router_examples` appends `turn_outcomes.confirmed_examples()` (`event_id` = `proposal_id` = `turn:<row id>`,
`status` accepted, `decided_by` `owner:<id>`, `label_source` followup when the owner kept a follow-up-proven label, else router with a
PERSON as decider - the shape the Phase 3 driver (#2128) treats as strong), and the seam's forget re-check applies. Phase 3's own policy
feed of follow-up proposals is unchanged; this path adds owner-confirmed pairs beside it.

**Walls** (`tests/test_turn_outcomes.py`, negative controls in its header): harness rows are text-free and can never be failed, reviewed,
confirmed or handed out; a guest row cannot be confirmed; a member decides only their own rows (another member's reads as "no such
turn"); a proposed or rejected label never reaches the seam; forget (`erase_entity`) and user delete reach the rows and fail closed, their
seam ids leaving the proposals / retrain ledger first (`struggle_proposals.forget_events(["turn:<id>"])`); retention is the detector's
`ZOE_STRUGGLE_RETENTION_DAYS` sweep.

| flag | default | meaning |
|---|---|---|
| `ZOE_STRUGGLE_OUTCOMES` | `off` | `off` = no notes, no rows, nothing in review or the seam; `on` = the record above (needs `ZOE_STRUGGLE_DETECT` != off) + the morning-brief label line |

With the flag off the detector skips exactly what it always skipped (a harness panel, a synthetic user); the wider traffic class
applies only when it is on. Known limit: `intent_router.execute_intent` knows the user but not the session, so its note is joined by
user and could land on the wrong row when one member has two turns in flight at once (voice and chat); a tier, Skybridge or tool note is
per session or per user as stated.

VOICE-PATH file touched: `fast_tiers.py` (one note in the `resolve` wrapper after a tier answered) - replay-gated. `zoe_flue_client.py`,
`intent_router.py`, `skybridge_service.py` and `routers/chat.py` carry one guarded note each.

## Known gaps (also in docs/knowledge/open-problems.md)

* No STT confidence: Moonshine's word confidences are off (`word_timestamps` not enabled), so a garbled transcript that
  is not repeated is invisible.
* ABANDON cannot tell a barge-in from a client disconnect (no server-side barge-in signal).
* Turn ids are detector-minted, not `chat_messages` ids (the voice save does not return its row id).
* Recall is deliberately traded for precision (REPHRASE 0.50 in-sample): rewordings with no shared words and no time
  anchor ("how are you" -> "I want to know how you are") are not caught.
