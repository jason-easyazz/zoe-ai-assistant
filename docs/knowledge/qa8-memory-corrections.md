---
type: spec
title: QA round 8 memory fixes - a corrected date, amount or age replaces every stored copy, a caseless name reaches the record, an amount with "and" in it is stored
description: Acceptance spec + root causes for the three memory faults QA round 8 proved (2026-11-12). "the dentist appointment is the 27th not the 20th" retires every phrasing of the 20th and a later transcript pass does not file it again; "how old is eli" (no capitals) reaches the stored age; "ive got 13 and a half thousand saved now" is stored, and the quote retirement retires the OLD amount, never the new one.
tags: [qa, memory, corrections, recall, spec]
timestamp: 2026-11-12T00:00:00Z
---

# QA round 8 memory fixes

## Acceptance - true when done

Synthetic names: Noor is the speaker, Eli is her son. Each line is checkable by a typed QA chat (capture drained between sessions).

1. After "my dentist appointment is on the 20th of november" and, in a later session, "sorry the dentist appointment is the 27th of
   november not the 20th", every stored copy of the 20th ("on the 20th of November", "on the twentieth of November") is superseded,
   "when is my dentist appointment" says the 27th, and the idle consolidation of the FIRST session does not file the 20th again.
   The same for an exam date, an age with a trailing clause ("actually eli is 9 not 8, he had his birthday last month"), a month
   ("the 3rd of december, not the 20th of november") and an amount ("13 and a half thousand not 12").
2. "how old is eli" typed without capitals reaches the stored "Eli: 9" on the first turn of a new session.
3. "ive got 13 and a half thousand saved for the caravan now" leaves exactly one current amount: 13.5 thousand.

## Root causes

| item | cause | fix |
|---|---|---|
| 1a. the correction was read by nothing | `value_correction.statement` took a value made of letters or a bare 0-120 number, anchored at the end of the sentence. An ordinal / date / amount ("the 27th of november", "12 thousand") or a trailing clause (", he had his birthday last month") never formed a correction. The turn digest's own row for the new value was unanchored (no first-person word in "the dentist appointment is the 27th"), so its supersede was refused by the authority wall (`has_standing`, `AUTHORITY_BLOCKED ... action=supersede`), correctly: an unanchored model row has no standing. | `value_correction`: a QUANTITY pair (`_QPAIR_RE`, `quantity_value`, `quantities`) - a number in any spelling ("20", "20th", "twentieth", "twenty-seven", "13 and a half thousand", "$12,000", "12k"), with only article / preposition / month / currency / "years old" words beside it; a trailing clause after the old value is allowed. `rewrite` compares by VALUE (`_rewrite_quantity`): exactly one number after the subject (or, when the subject comes last, the row's only number) equal to the old value; an old value said without a scale takes the new one's; a month the correction names must not contradict the row's. The rows are rewritten by the existing user-class writer `conversation_correction` (`review(edit)`: old row superseded, never deleted). |
| 1b. the old value came back | The idle consolidation re-read the earlier session a minute after the correction and stored "User (Noor): has a dentist appointment on the 20th of November", approved. | `value_correction.superseded_by_correction`: a correction that rewrote rows is remembered in process for `REMEMBER_S` (6 h); `memory_idle_consolidation.consolidate_session` drops a fact that still holds the corrected-away value of that subject (`VALUE_CORRECTION_STALE`). |
| 2. "no information about Eli" | A caseless question names nobody the recall floor can look up: `memory_gate.person_candidate_names` reads capitalised runs only, so the person-fact entity `slug:eli` was never checked. The turn fell to the household fallback, whose packet fetch has a 0.4 s budget; on the first turn of the session it ran out (`SEAM_HOUSEHOLD budget_exceeded=0.40s`) and the brain got no memory block. Re-run 2026-11-12: "How old is Eli?" fired `RECALL_FLOOR ... people=1 linked=0` and "how old is eli" did not. | `memory_gate.person_caseless_candidates`: for a caseless question the content words (and adjacent pairs) are the lookup keys; `person_recall_floor._resolve` keeps only those that ARE one of this user's entities (one exact-key read). |
| 3a. the new amount was dropped | `fact_subject._clauses` split "ive got 13 and a half thousand saved ..." at the "and" inside the number, so the clause holding the amount had no "I" and the fact was dropped as someone else's (`dropped a fact about another person filed under the user`). | The "and" inside a number ("N and a half/quarter/third", "hundred/thousand/million and N") is not a clause break. |
| 3b. the old amount was retired anyway | The quote retirement (`ZOE_QUOTE_RETIRE` enforce) runs after the digest and offers the sentence's top rows; it retired the 12 thousand row, so no amount was left. With 3a fixed, the NEW row would be the closest match and could be the one retired. | `memory_digest` records `written_ids`; `_quote_retire_pass` passes them as `exclude_ids` to `memory_retire.distill_turn` -> `candidates`: a turn never retires a row it wrote itself. |

Tests: `services/zoe-data/tests/test_qa8_memory_corrections.py` (ci_safe, fakes only).

## Not in this change

- A restated amount with no "not <old>" ("13 and a half thousand now") is still retired by the quote judge (a model call) rather than by
  `value_correction`; the subject of such a sentence ("the caravan") is too loose for a deterministic rewrite.
- The household fallback's 0.4 s budget is unchanged: a caseless question that names no known person still depends on it.
- The registry in 1b is in process: a zoe-data restart between a correction and the idle pass loses it (cost: one stale near-duplicate).
- The nightly digest is not guarded by 1b; its LLM contradiction pass remains the backstop there.
