---
type: Reference
title: Memory supersession — explicit corrections, implicit changes, validity metadata
description: Every path that retires a stored memory row in favour of a newer one (review edit, shared reconciler, nightly LLM contradiction judges, flag-dark implicit supersede), how each decides "same slot", the drawer metadata keys for validity, how reads hide superseded rows, and how to enable ZOE_MEMORY_IMPLICIT_SUPERSEDE.
tags: [memory, samantha, context-engineering, supersession, validity, flags]
timestamp: 2026-09-30T12:00:00Z
---

# Memory supersession

Context-manager gap #4 ([research §2 pattern 3, §4 item 4](../research/samantha-context-engineering-2026-09-29.md);
[audit gap table](../research/zoe-context-audit-2026-09-29.md)). A row is never deleted to
correct it: the old row gets `status=superseded` and every read hides it
(`memory_service._BLOCKED_READ_STATUSES`, the packet builder's drop-set, the card's
`_eligible`, `list_by_status("approved")`).

## Who supersedes, and how "same slot" is decided

| Path | When | Same-slot rule | Link written |
|---|---|---|---|
| `memory_quality.reconcile_for_ingest` → `classify_against_existing` | every conversational write (turn digest, extractor, person extractor, nightly digest, expert dispatch) | top-3 semantic hits, entity-guarded; UPDATE only for a `SequenceMatcher` ratio ≥ 0.92 with a different value, or a matching "my/X's ATTR is" key (ratio ≥ 0.45) with a different value | `review(edit)`: new row `supersedes_id`, old `superseded_by_id` |
| `memory_extractor` conversational correction | "wait no, I meant Saturday" against the previous user turn | the corrected-away value must occur in the prior message | `review(edit)` |
| `memory_digest.run_memory_digest` contradiction check | nightly, facts re-extracted from the day | top-3 semantic hits → LLM judge (`_CONTRADICTION_PROMPT`) | `review(edit)` |
| `memory_digest._resolve_contradictions` | Sunday consolidation | pairs with ≥ 0.25 word overlap → LLM judge, ≤ 50 pairs | `review(edit)` |
| **`memory_supersede`** (flag-dark) | write time in `run_turn_digest`, and nightly (dreaming phase 1.7) | deterministic: change cue + topic match + subject guard, or a different home | `MemoryService.supersede_by` (metadata-only) |

Measured gap (A/B run 2, 2026-09-30): "Change of plan: I've dropped the half-marathon. I'm
doing a 10k in May instead." gave "User dropped the half-marathon." (ratio 0.59 vs the old
row, no attribute key) and "User is doing a 10k in May instead." (0.43) — both ADD, so
"training for their first half-marathon in March" stayed approved. `looks_like_correction`
does not help: it recognises correction OPENERS ("no, …", "actually …", "I meant …"), not a
change of state. S2 ("moved to Hobart") passes in-day only because the recall packet
presents conflicting bullets newest-first; the Dunedin row is retired later by an LLM judge.

## Implicit supersede (`ZOE_MEMORY_IMPLICIT_SUPERSEDE`, default OFF, per-call read)

- **Cue table** (`memory_supersede.CUES`): end cues — dropped, no longer, not … anymore,
  stopped, quit, gave up, cancelled, used to; swap cues — change of plan (utterance only),
  moved from, moved to, instead, switched to, changed to, now … rather than, and
  **correction** (utterance only: "actually/wait/sorry/no … wrong/meant/not …", "I got
  that wrong", "I was wrong", "I meant", "correction") — a change of state with no change
  verb, added 2026-10-04 after the day-sim's ask 3 showed the corrected home town being
  dropped. Everyday
  senses are excluded in the regex and pinned by tests: dropped my keys / the kids off /
  by, stopped at the shops, got/am used to. "instead"/"rather than" with a one-off time
  ("this morning", "for lunch") is a substitution, not a change.
- **Gate:** the utterance must carry a cue AND the stored fact must either carry one
  itself OR replace an approved row by the same-topic / exclusive-home-slot match
  (`changes_existing`; a corrected fact — "User's mum lives in Bendigo" — has no cue word).
  An unrelated fact from the same turn (no cue, no matching row) still cannot retire
  anything, and a cue-less change acts as a swap in `supersede_for_turn`.
- **Tombstone:** a fact with an END cue is stored `memory_type=state_change`, tag
  `state_change`. The card never lists it; the recall packet prefixes it `(change)`.
- **Subject** (`same_subject`, 2026-10-06): equal owner and relation words AND compatible NAMED
  people (`subject_names` / `names_compatible`): a leading name, a possessive ("Dana's job"),
  the names after a relation noun ("User's friend Dana"). Names are read WHOLE with
  `named_relations`' token reader (the recall floor's discipline), never as substrings:
  "Ana" is not "Anabel"; "Dana" is "Dana Whitfield" but "Whitfield" is not. "User's friend
  Dana" is not "User's friend Leo" is not "User" (bake-off X1: the old key carried only
  "user" + "friend", so the nightly pass retired 5 of 20 friends' homes). One side naming
  nobody still matches when every relation is one-per-person (mum, dad, wife, boss ...:
  "User's mum lives in Bendigo" corrects "User's mum Ingrid lives in Ballarat"); for
  friend / sister / kids the name is what tells people apart.
- **Attribute** (`attributes_of` / `same_attribute`): a closed vocabulary (home, job,
  birthday, age, pet, school, health, status). When BOTH facts state a classified attribute
  and the sets are disjoint they are never the same topic: a move never retires a job
  (bake-off X2: "friend moved to Perth" retired "friend works at a bookbinder" on the seeds
  where the name + "friend" were enough overlap).
- **Topic match** (`same_topic`): `same_subject` and `same_attribute`, then content tokens
  MINUS the subject's own words (relation words, names) and the change frame and
  frequency words, plural folded; shared tokens must be ≥ ½ of the smaller set
  AND ≥ ½ of the old fact's set. Known miss by design: "gave up the cello" does not retire
  "plays the cello in a community orchestra" (one word of four) — the LLM judges and the
  newest-first packet remain the backstop. The same name guard sits in the per-turn
  reconciler (`memory_quality.classify_against_existing`: "Leo's birthday is ..." never
  UPDATEs "Dana's birthday is ...", the attribute key strips the subject) and in the weekly
  LLM contradiction pass (two different named people's facts are never put to the judge).
- **Exclusive slot:** home (`lives/based/settled in X`, `moved to X`); a different X
  supersedes.
- **Targets:** approved person-fact types only (`user_model_card.ALLOWED_TYPES`); never
  emotional moments, notes, open loops or earlier tombstones. ≤ 3 per new fact at write
  time; ≤ 10 per user per nightly run (`NIGHTLY_CAP`).
- **Word-overlap dedup:** a change fact skips the turn digest's legacy substring dedup —
  "User no longer lives in Dunedin." scores 0.83 against the row it retires and was
  dropped as a duplicate.
- **Link:** a tombstone's old row points at the turn's single swap fact when there is one
  ("…a 10k in May instead"), else at the tombstone. After a write-time supersede the card
  is rebuilt (no-op unless `ZOE_USER_MODEL_BLOCK`).
- **Logs:** `MEMORY_SUPERSEDE user=<id> cue=<cue> superseded=<n> new=<id8>` and
  `MEMORY_CONFLICT_PASS user=<id> pairs=<n> superseded=<n>` (zoe-data logs to
  `~/.zoe-logs/`, not journald).

### The owner's retraction / correction retires by KEY, not by words (2026-10-09)

Found by the agent of #1916: after "Good news: I no longer get the migraines since I switched to new glasses." the
retraction landed approved as `user_stated_derived`, but "User has been getting migraines most afternoons lately"
stayed approved beside it (`superseded=0` with the flag on); the day-sim passed only because the new row was served
too. Cause: `same_topic` asks the change to name at least half of the OLD sentence's content words, and "User no
longer gets migraines" names 1 of 3. Whether a pair formed depended on how much the 4B extractor wrote, not on whose
fact it was.

When the new row is the **owner's word** (`memory_supersede.is_owner_word`: authority rank >= `user_stated_derived`;
a model's guess and an unconfirmed panel voice never qualify) the pair is decided by `owner_key_match(new, old, cue)`
(subject, attribute), in the write-time pass and the nightly pass alike; `same_topic` / the home slot still apply
first and are unchanged:

- **slot** - a swap/correction on a one-valued attribute (`SLOT_ATTRIBUTES`: home, birthday, age, job) with the same
  attribute set on the same subject (`same_subject`, so another person's row is never touched). A job is replaced only
  by a change in the fact's own words or an explicit correction, not by any cue elsewhere in the turn.
- **retraction** - an "ended" fact with a predicate ("no longer GETS migraines", "has stopped GETTING migraines"): the
  older row states the same predicate (by stem) and everything the retraction names (its reason clause - "since
  switching to new glasses" - is not the thing that ended). A light verb ("gets", "has") needs its object; an end with
  no predicate ("dropped the half-marathon") is left to the overlap rule.

The old row is retired by id (`supersede_by`: `superseded_by_id`, `invalid_at`, `expired_at`; never deleted; the two
timelines of #1896) and the audit note says `key slot:home` / `key retraction`. An over-retirement is reversible
(`MemoryService.restore_superseded`). Pinned by `tests/test_owner_retraction_retires_old_row.py` (turn and nightly lanes,
break-the-fix controls, the day-sim 6 and 6n seed turns).

### Replaying an incident: the `MEMORY_ROW` line

Every row the turn digest (`lane=turn_digest`), the nightly digest (`lane=digest`) or the emotional pass
(`lane=emotional`) stores, parks, edits or holds logs ONE INFO line after the turn (never on the voice hot path):

`MEMORY_ROW lane=<lane> outcome=<stored|parked|edited|held> user=<id> id=<row id> class=<authority class> promoted=<yes|no> basis=<authority basis> status=<status> type=<type> wording='<row text>'`

`promoted=yes` = the owner's one verbatim sentence entailed the fact (`authority_basis=verbatim_user_span`); `no` is why
a retraction was parked as a dispute (`class=model_from_turn promoted=no status=disputed` is the 6n signature). The
wording is the row's own text - the extractor's sentence after the write boundary's scrub - never the owner's turn.
Pinned by `tests/test_memory_digest_row_log.py`. Writer: `memory_digest.log_row`.

## Validity metadata (Chroma drawer metadata, no migration)

| Key | Type | Written |
|---|---|---|
| `added_ts` | epoch float | every write (existing) |
| `valid_from` | epoch float (= `added_ts`) | new writes and `review(edit)` rows while the flag is on; the successor in `supersede_by` if missing |
| `invalid_at` | epoch float | the old row, by `supersede_by`, and by `review(edit)` while the flag is on |
| `supersedes_id` / `superseded_by_id` | row id | existing keys; `supersede_by` keeps the first `supersedes_id` |

`valid_from`/`invalid_at` are never carried forward by an edit. Rendering "(until 30 Sep)"
for a superseded row on past-tense questions is NOT built (`recall_evidence` would have to
read superseded rows, which every read path hides by design).

## Enabling

1. Inspect first, per member, from a zoe-data Python shell:
   `rows = await get_memory_service().list_by_status(user_id=uid, status="approved", limit=2000)`
   then `memory_supersede.conflict_pairs(rows)` lists every `(newer, older, reason)` the
   nightly pass would act on (it writes nothing; `nightly_conflict_pass(..., dry_run=True)`
   logs only the count).
2. Set `ZOE_MEMORY_IMPLICIT_SUPERSEDE=1`, restart zoe-data, re-run the user-model A/B
   (`scripts/perf/user_model_ab.py`, race guard) and the Samantha bar S2.
3. Rollback: unset the flag. A wrongly retired row is recoverable:
   `review(<id>, decision="approve", actor="operator")` (the row is kept).

Tests: `services/zoe-data/tests/test_memory_implicit_supersede.py` (ci_safe), `tests/test_owner_retraction_retires_old_row.py`, `tests/test_memory_digest_row_log.py`.
