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
  moved from, moved to, instead, switched to, changed to, now … rather than. Everyday
  senses are excluded in the regex and pinned by tests: dropped my keys / the kids off /
  by, stopped at the shops, got/am used to. "instead"/"rather than" with a one-off time
  ("this morning", "for lunch") is a substitution, not a change.
- **Gate:** the utterance must carry a cue AND the stored fact must carry one itself, so
  an unrelated fact from the same turn cannot retire anything.
- **Tombstone:** a fact with an END cue is stored `memory_type=state_change`, tag
  `state_change`. The card never lists it; the recall packet prefixes it `(change)`.
- **Topic match** (`same_topic`): equal subject key (owner, relation words, possessive
  names — "User's sister …" never matches "User …"), then content tokens minus the change
  frame and frequency words, plural folded; shared tokens must be ≥ ½ of the smaller set
  AND ≥ ½ of the old fact's set. Known miss by design: "gave up the cello" does not retire
  "plays the cello in a community orchestra" (one word of four) — the LLM judges and the
  newest-first packet remain the backstop.
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

Tests: `services/zoe-data/tests/test_memory_implicit_supersede.py` (ci_safe).
