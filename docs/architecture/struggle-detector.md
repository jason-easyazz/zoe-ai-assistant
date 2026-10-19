---
type: architecture
title: Struggle detector - notice where Zoe struggled, propose how to fix it, retrain the router on it (Phases 1-3 of the self-correction loop)
status: phase-1 built (ZOE_STRUGGLE_DETECT default shadow); phase-2 built (ZOE_STRUGGLE_PROPOSALS default shadow); phase-3 built flag-dark (ZOE_ROUTER_STRUGGLE_FEED default off)
owner: services/zoe-data (struggle_signals.py, struggle_detector.py, struggle_digest.py, struggle_proposals.py, router_retrain_ledger.py, routers/struggles.py) + scripts/maintenance/router_struggle_retrain.py
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
| 4. prove | a retrained router is scored on held-out failures AND the golden eval + voice replay before it ships | 3 | **built** (flag-dark) |
| 5. apply | router retraining adopted automatically **within its gates**; code changes only as PRs; **rocks never change** | 3 | **built** (flag-dark; `on` only) |

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
`HHMMSS_mmm.wav` carries no turn id - and withheld whenever another clip shares the turn's window; Phase 3 -> *Audio*) and nothing reads, copies or moves the audio.

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

## Phase 3 - prove + apply (built, flag-dark; `scripts/maintenance/router_struggle_retrain.py`, `router_retrain_ledger.py`, flag `ZOE_ROUTER_STRUGGLE_FEED`)

**The closed loop** (owner, 2026-10-18: "save users' voice turns, like the ones that fail, update the router, test against these fails;
if the success rate improves then it adopts it"). Zoe fails a turn; the user's next attempt works; that success labels the failure; the
labelled failures retrain the router; the new router is adopted only if it provably fixes real failures without breaking anything.

```
fail -> (P1) event + follow-up facts -> (P2) failed->success router_example proposals -> seam queued_router_examples
     -> COLLECT (strong labels, forget tombstones out, frozen corpus out, one row per wording, TIME split)
     -> ORACLE (the brain labels blind; kept on agreement) -> TRAIN (warm-start continuation, measured) -> candidate GGUF
     -> GATE (a) held-out failures  (b) frozen corpus per case  (c) voice replay OF THE CANDIDATE  (d) forget-clean
     -> ADOPT (`on` only: router_selftrain.deploy) | record "would adopt" (`shadow`) | record why not
```

**What may change: only the router** (FunctionGemma-270M stage 2, `functiongemma-router.service`). Gemma, Moonshine and Kokoro are rocks;
stage 1 (the SetFit head) is hand-committed and outside the loop. `intent_gap`, `answer_quality` and `stt_term` proposals stay human work.

| flag / knob | default | meaning |
|---|---|---|
| `ZOE_ROUTER_STRUGGLE_FEED` | `off` | `off` = the scheduled run is a no-op line; `shadow` = collect, train, gate and record, NEVER swap; `on` = shadow + adopt a candidate that passes every gate. Read per run. `--shadow` forces a shadow pass whatever the flag; nothing on the command line can make a run adopt (no force flag; a test pins it) |
| `ZOE_ROUTER_RETRAIN_DIR` | `~/.zoe/router_retrain` | ledger, journals, scratch work, generations (0700 / 0600, outside git) |
| `ZOE_ROUTER_RETRAIN_STOP_BRAIN` | unset | `1` lets a run stop the brain for the TRAINING step only, when memory needs it, no voice turn for 30 min and the night window is not running; always restarted (`--recover` / the unit's `ExecStopPost` retry it) |
| `ZOE_ROUTER_TRAIN_PYTHON` | `/usr/bin/python3` | the trainer's interpreter (the py312 service venv has no peft / datasets) - shared with `router_selftrain.py` |
| `ZOE_ROUTER_RETRAIN_HOLDOUT_FRAC` / `_REPLAY` / `_DELTA_REPEAT` / `_EPOCHS` / `_LR` | 0.2 / 300 / 2 / 1 / 1e-4 | the split and the continuation recipe |

**Collect (the data).** The seam `queued_router_examples(statuses=SEAM_STATUSES)` resolves each example's words at read time from the
member's own `struggle_events.summary` (forget-gated, member-only, the failed turn only) - never the redacted router shadow log. Kept:
`label_source = followup` examples of `proposed` / `accepted` / `applied` proposals (a `rejected` one never), and a `label_source = router`
example only when a PERSON accepted it (an owner correction; the loop's own policy accept never upgrades a weak label). Dropped and
counted: forget tombstones, frozen-corpus texts, bad labels. One row per normalised wording (a wording with two labels is dropped). **Time
split, no leakage:** the newest 20% of wordings the incumbent cannot have learned are the held-out "known failures"; every older wording -
and any wording the incumbent MAY have learned: the served and previous generations' WHOLE warm-start lineage (`parent` links; a generation
holds what every ancestor learned) and the committed corpus every generation descends from - is train, so every held-out failure happened
after every new trained one and never shares a wording with it. The training rows are rebuilt with every held-out wording removed from
EVERY source (the train split and the committed-corpus replay sample), and the run aborts if one is still there (`prepare_training`,
`leaked`). Fewer than 10 held-out (about 50 labelled wordings) or 10 train = "not enough evidence": nothing is trained.

**Train.** The brain re-labels every example blind (the miner's grammar-constrained oracle, full tool menu, no hint) and only agreement
survives; the train split (x2) plus a 300-row replay sample of the committed corpus (against forgetting) is a warm-start continuation of
the SERVED generation's checkpoint (`ZOE_ROUTER_WARM_START`, gen1, before the first adoption), 1 epoch at lr 1e-4, `nice` + OOM-first. Every
run MEASURES itself (wall minutes, the trainer's own peak RSS via `wait4`, the lowest MemAvailable) into the ledger, and the next run's
plan uses the measured peak x 1.15 (4000 MB assumed until then). A watchdog terminates the trainer if MemAvailable falls under 700 MB.

**Adoption gate (`decide_adoption`; deterministic, no model-judged score; every check must hold).** Both models on the SAME scratch rig,
back to back, after the incumbent's live-vs-scratch validity check (> 10 points apart = inconclusive):

* **(a) the held-out failures:** >= 10 cases, scored by domain through the real production router (`prod_path_eval.py --corpus`);
  `fixed` = incumbent wrong and candidate right, `broken` = the reverse; requires the incumbent to miss at least one and
  `fixed - broken >= max(3, ceil(25% of the incumbent's misses))`. A shuffled-label control fails this (a test pins it).
* **(b) no regression elsewhere:** on the frozen 81-case corpus no case the incumbent answers may break (`GOLDEN_MAX_BROKEN = 0`; one case
  is 1.2 points), the aggregate holds (>=), chat false positives 0, p50 < 600 ms, the same case count.
* **(c) the voice replay** of the CANDIDATE (`router_selftrain.replay_gate(candidate_gguf=...)` serves it on the scratch port and points
  the probe at it, under `flock /tmp/zoe-voice-harness.lock`, into the loop's own results file - never the shared voice-gate artifact):
  it must RUN and pass; a skip is not a pass.
* **(d) forget-clean:** no example the run used was tombstoned while it ran.

Any miss leaves the incumbent serving and records the reasons. **Adopt** (`on` + every check) is one transaction: (1) RESERVE - under
the ledger lock, re-check the tombstones and insert the run as `adopting` (a forget from then on flags it); (2) a durable
`adopt_in_progress.json` marker; (3) `router_selftrain.deploy` (LKG archived first, atomic swap, restart, `/props` identity, post-deploy LIVE
eval, auto-rollback); (4) PUBLISH - the checkpoint moves to `generations/<gen>/merged` (**never deleted** - the lineage rule; it becomes the
next warm start) and, under the lock, the record commits as `adopted` with `served` / `previous` updated - unless a forget flagged it during
the swap. Any failure or flag after the swap restores the outgoing model (`restore_lkg`), removes the half-made generation and records
`rolled_back_at_publish`; a process killed mid-publication leaves the marker, and `--recover` (the unit's `ExecStopPost`) finishes the
rollback unless the commit had landed. Then the proposals it learned from move to `applied` (a still-proposed one is accepted by `policy:struggle-feed` first, so the
record says who decided). `--rollback` puts the previous model back instantly (`restore_lkg`, validated, archive-only). A candidate that
did not become a generation is scratch: its checkpoint is deleted at the end of the run; the newest shadow candidate GGUF is kept.

**Forget.** `struggle_proposals.forget_events` / `forget_user` (the forget / delete cascade, fail closed) call
`router_retrain_ledger.forget` first, with the failed turns' WORDING keys read from the struggle rows while they still exist: the example
leaves every run record, its wording key, event id (and, for a deleted user, the user key) become tombstones - also when no run has
recorded it yet - so it never re-enters even if the same words are said again, any leftover run text is deleted, and a served (or
being-swapped-in) generation that LEARNED it - itself or through any ancestor - is flagged `needs_retrain` (`--status` shows it). A pytest
session can never reach the live ledger (`root()` refuses it; both conftests pin `ZOE_ROUTER_RETRAIN_DIR`). Weights cannot unlearn: the next adopted
generation, trained without it, is the remedy. The ledger holds no words (event ids and truncated hashes); the words exist only for one
run in `work/<stamp>/*.texts.jsonl` (0600) and are deleted when it ends.

**Audio (the #2109 blocker, closed).** `audio_ref` is now bound to the turn by time and withheld whenever that is ambiguous: with the
failed turn's latency known, the clip must sit in [route start - 20 s, route start + 1 s] and be the ONLY clip from there to 3 s after the
turn finished (another panel capturing while the turn was answered withholds it); without the latency, the only clip in the 90 s before
it finished. It carries `mtime` / `size` (the names repeat daily). An exact binding still needs a turn id in the sample name (a
replay-gated voice change; open-problems).

**Where and when.** The run refuses when the night window unit is running, when it would overlap 02:45-03:15 or 04:00-04:55 (estimated from
the last measured run, 120 min until then), when a voice turn happened in the last 30 minutes (the panel is in use), or when training would
leave MemAvailable under 700 MB with the voice stack up and stopping the brain is not allowed. Template units
`scripts/setup/systemd/zoe-router-retrain.{service,timer}` (Saturday 00:30; installing them is an owner step).

**Evidence (2026-10-19).** The live store holds 4 struggle events in 30 days (the last on 2026-10-15) and 0 router_example proposals, so
the real shadow run (`--shadow`, recorded in the ledger) stopped at COLLECT: 0 seam rows, "not enough evidence: 0 held-out < 10 (~50
labelled wordings needed)". Training was NOT measured: MemAvailable was 1.5-2.1 GB with the voice stack up and the owner at the panel,
under the 4000 MB assumed peak + 700 MB floor, and the brain was not to be stopped. The first shadow run in a training window measures it.
Tests: `tests/unit/test_router_struggle_retrain.py` (gate, split, forget, shadow, rollback, window, memory; negative controls in its header)
and the Phase 3 cases in `services/zoe-data/tests/test_struggle_proposals.py`.

**Limits stated up front.** (1) A follow-up that succeeded is evidence, not proof; the oracle agreement and the holdout exist for that.
(2) Weights cannot unlearn (above). (3) With one household, labelled failures arrive slowly; "not enough evidence" is the expected outcome
for a while, and that is correct. (4) Watching an adopted cluster shrink over the next 7 nights (and queueing a rollback proposal if it
does not) is not built yet.

## Known gaps (also in docs/knowledge/open-problems.md)

* No STT confidence: Moonshine's word confidences are off (`word_timestamps` not enabled), so a garbled transcript that
  is not repeated is invisible.
* ABANDON cannot tell a barge-in from a client disconnect (no server-side barge-in signal).
* Turn ids are detector-minted, not `chat_messages` ids (the voice save does not return its row id).
* Recall is deliberately traded for precision (REPHRASE 0.50 in-sample): rewordings with no shared words and no time
  anchor ("how are you" -> "I want to know how you are") are not caught.
