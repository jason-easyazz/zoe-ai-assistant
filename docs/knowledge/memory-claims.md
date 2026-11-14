---
type: Reference
title: Memory claims - the model proposes, code validates, one guarded writer applies
description: What ZOE_MEMORY_CLAIMS (off | shadow | on, default off) does - a model reads a correction as a typed claim, memory_claims_validate decides it against the people graph and the stored rows, and memory_claims_apply is the one writer; how to read the two log lines; what must be decided before on.
tags: [memory, corrections, claims, validator, guarded-writer, shadow, flags, forget]
timestamp: 2026-11-14T12:00:00Z
---

# Memory claims (`ZOE_MEMORY_CLAIMS`)

The structural replacement for the regex correction path (`value_correction`). A model reads a correction-shaped turn as a
typed claim; code decides whether the claim may change anything; anything ambiguous is left as mined.

| piece | file | what it is |
|---|---|---|
| claim shapes, normalisers | `services/zoe-data/memory_claims.py` | pure; a value is `Norm` or abstain |
| validator, rules D.1-D.20 | `services/zoe-data/memory_claims_validate.py` | returns a `Verdict` (values, never text) |
| new sentence | `services/zoe-data/memory_claims_render.py` | splices the member's words into the stored span, read back by the normalisers |
| the one writer | `services/zoe-data/memory_claims_apply.py` | `apply(verdict, claim, job)` |
| golden set | `services/zoe-data/tests/fixtures/memory_claims_golden.json` | the review contract; scores in `labs/memory-claims-lab/` |

## The flag

`ZOE_MEMORY_CLAIMS`: `off` (default; nothing runs), `shadow` (the claim and what WOULD be written are computed and one
counts-only line is logged against the old regex path; nothing is written and the old path still decides), `on` (corrections
are applied by the guarded writer; a health event is still not filed unless the caller names the class).

## What the writer guarantees

1. It writes only for a validator `apply` verdict. Abstain, hold, refuse and agree write nothing.
2. Every row of a claim is planned first; if one row cannot be rewritten, none is.
3. The new text loses nothing (`value_correction.drops_value`) and reads back as the planned value.
4. Forget walls fail closed: a retracted turn, the forgotten ledger (an unreadable ledger refuses), the erase generation.
5. Authority is never lowered: a `user_confirmed`, operator or curated row is not replaced by a claim. This is STRICTER than
   the regex path, where a spoken correction may replace a `user_confirmed` row.
6. New row first, then the old one retired (`MemoryService.review`), then both rows are read back; only then are the
   superseded value and the structured birthday recorded.

## Reading the logs (codes and counts only)

- `MEMORY_CLAIM_APPLY class= mode= code= rows= registry=`: one per claim the writer saw. `code`: `would_write`, `written`,
  `not_validated`, `nothing_to_write`, `render`, `drops_value`, `stale_row`, `retracted`, `forgotten`, `ledger_unreadable`,
  `authority`, `erased`, `write_refused`, `verify_failed`, `error`.
- `MEMORY_CLAIM_SHADOW class= lane= verdict= rule= rows_old= rows_claim= ms= model=`: `verdict` is `agree`, `claim_only`,
  `old_only`, `disagree` or `abstain`; `rule` is the validator rule that stopped the claim, or `-`.
- `MEMORY_CLAIM_INVALID class= verdict= rule= lane=`: a claim that changed nothing, by rule number.

## Known limits

- The superseded-value registry is reached through a seam. It keys a subject by `self` or a person id, so a correction about
  a thing ("book club") records nothing there.
- Stored rows carry no attributes until a claim touches them; reading them is the pass's second model call.
- The read-back happens after `review(edit)` has already retired the old row: a failed read-back is reported
  (`verify_failed`) and stops the registry write, it does not undo the edit.
