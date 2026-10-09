---
type: Reference
title: Distress hand-off (ZOE_DISTRESS_HANDOFF) - a deterministic floor for self-harm, abuse and danger language
description: Governance note section 7 built. A per-language lexicon detector (hand-off / gentle / none, negation and quotation guards) that runs before every tier and before the brain; on hand-off Zoe speaks a fixed two-sentence pointer to a human and the local crisis line, the turn is kept out of every memory writer, the night mind and the digest, and the household contact is told once. Flag off|shadow|enforce, default enforce for the hand-off tier. Measured per tier; known limits; the operator block.
tags: [safety, governance, voice-path, children, zoe-data, lexicon, samantha]
timestamp: 2026-10-09T00:00:00Z
---

# Distress hand-off (`distress_handoff.py`)

Spec: [emotional-safety note](../governance/emotional-safety-note.md) section 7; [blueprint](../architecture/samantha-brain-blueprint-2026-10-09.md) 2.4 and 2.9. Measured as
[conversation-quality-classes](conversation-quality-classes.md) measures a class: a labelled set, a negative control per guard, a live path, a rollback.

## What it does
A turn that says someone wants to die, is hurting themself, is being hurt, or is in danger gets **no model**: one fixed, warm, two-sentence pointer (the household contact and the local crisis
line; a child gets the kids' line; an immediate-danger cue leads with the emergency number), the turn is **not stored, digested or reflected on**, and the household contact gets one plain
notification with **no words from the turn**. No humour, no diagnosis, no persona: the text is data in `lexicons_data/<lang>.json`.

## The detector
`detect(text) -> Verdict(tier, cls, lang, why)`; pure, stdlib, a few compiled regexes per language. **Reads no word of any language** (a test scans the module's string constants).
Cues, guards and reply texts live in `lexicons_data/<lang>.json` under `distress` (English and Spanish; a language with no section never fires; adding German is a data file, pinned by a test).
Every language with a section is scanned on every turn (code-switching works); the reply is in the language of the cue that fired.

* A **cue** is a closed-vocabulary sequence (`seq`: named `groups` joined by a closed `fillers` list, so "I want my mum to die" is not "I want to die") or a regex. Text and patterns are folded alike
  (NFKC, casefold, accents off Latin letters, apostrophes dropped).
* **Tiers.** `handoff`: a first-person cue stands in its own clause (clauses end at punctuation and the lexicon's `clause_breaks`). `gentle`: the same cue behind a **negation** ("I don't want to die") or a
  **story/quote marker** ("in the film he says..."), a bare topical word ("the film was about suicide"), a third party at risk ("my friend said she wanted to die"), ordinary low mood.
  `none`: nothing, or an **idiom** ("dying to see it", "kill for a coffee", "I can't live without my phone").
* **Over-triggering is preferred to missing**: a guard only ever lowers handoff to gentle, never to none; only the idiom list voids a cue; a marker AFTER a cue never demotes it.

## Where it sits (voice-path change: the replay gate applies)
1. `fast_tiers.resolve` wrapper, **before** the provenance tier and every other tier (chat fast path, voice, LiveKit, Telegram-through-chat): `DispatchResult(tier="distress_handoff")`.
2. `zoe_flue_client.run_flue_brain_streaming`, **first** line of a brain turn: the brain, recall, day brief, `[RAISE]` and persona never see the turn (a test makes each raise if called).
3. `memory_provenance.claim_turn` / `is_off_record` / `reply_is_off_record` / `blocks_write` carry the "off the record" tag for the turn and the reply (works with `ZOE_MEMORY_PROVENANCE_ANSWERS` off).
4. Readers re-check the TEXT: `memory_digest._transcript_from_rows` (nightly digest + night mind loader), `_extract_open_loops`, `night_mind.turns_of`. The chat rows are kept (history) flagged `off_record`.

## Flags and config (read per call)
`ZOE_DISTRESS_HANDOFF` off | shadow | **enforce (default; a typo enforces)**; `ZOE_DISTRESS_GENTLE` off | **shadow (default)** | enforce (a fixed "that sounds heavy, how are you?"). Shadow logs
`DISTRESS_HANDOFF user= tier= lang= mode=shadow` and changes nothing. Enforced log: the same line, `mode=enforce`, never a word of the turn.
Rollback: `ZOE_DISTRESS_HANDOFF=off` (restart); the lexicon section can be deleted per language.

### Operator block (nothing is set by this PR)
| Setting | Why |
|---|---|
| `ZOE_HOUSEHOLD_COUNTRY=AU` (ISO code; falls back to `ZOE_LOCATION_COUNTRY`) | picks the numbers from `distress_data/crisis_lines.json`. **Unset = no number is guessed**: Zoe says "your local crisis line / emergency number" and logs a warning. Verify the numbers; or `ZOE_DISTRESS_LINES_FILE=/path.json` (same shape) overrides. |
| `ZOE_DISTRESS_CONTACT_NAME=<name>` | the human Zoe names ("Please talk to <name> now"). Unset = "someone you trust" / "a grown-up you trust". |
| `ZOE_DISTRESS_CONTACT_USER=<account id>` | the adult who is notified (a `notifications` row + the bell). Unset = nobody is notified. |
| `ZOE_DISTRESS_NOTIFY=all` (default) \| `minors` \| `off` | owner decision: **`all`** tells the contact about any member (the brief); **`minors`** keeps an adult's disclosure private. Never the contact about themself, never a guest. Once per member per 6 h (in-process: a restart inside the window can repeat it once). |
| Mark each child as a minor (persona `member_modes.minor`) | the only child record; unflagged, a child gets the adult wording and, under `minors`, no notification. |

## Measured (208 synthetic phrases, English + Spanish, `tests/fixtures/distress_labelled.json`)
Three sets: **dev** (written with the lexicon, tuned on); **held_out_1** and **blind_2** (written after the lexicon froze, scored once, THEN used to fix whole classes of cue).
First-run (the honest out-of-sample estimate of a hand-written lexicon) vs after the class fixes (a regression set now):

| tier (n over the two blind sets) | blind first run: precision / recall | after fixes, all 208: precision / recall |
|---|---|---|
| handoff (56 -> 89 all) | 0.93 / 0.66 | 1.00 / 0.99 (en 1.00 / 0.98, es 1.00 / 1.00) |
| gentle (27 -> 48) | 0.76 / 0.59 | 1.00 / 0.98 |
| none (45 -> 71) | 0.61 / 0.91 (misses fall into none) | 0.97 / 1.00 |

**Read the left column as the real expectation: about two thirds of unseen hand-off phrasings were caught the first time.** The right column is not a promise on new phrasings. The failure
that matters (a hand-off turn scored `none`) is pinned: the only one left is "I want to go to sleep and never wake up"; the other pinned miss is a grief statement. The test fails if the set changes.

## No-store map (`tests/test_distress_no_store.py`, real code, control for each: flag off stores it)
extractor + ingest choke point (`blocks_write`), per-turn digest (`is_off_record`), exact-words index, person extractors, latent-intent detector (all via `is_off_record`, text test, stateless);
saved transcript rows (turn + Zoe's reply, `off_record`, also for a guest, also with provenance off); nightly digest + night mind (text filter, so a row saved before the flag still never reaches a prompt);
open loops; idle consolidation + exact-words catch-up (SQL `off_record`; idle consolidation does not re-check the text, so a row saved before the flag is not covered there). The brain, brief and raise are not run, so nothing proactive can quote it.

## Break-each-fix log
A manual mutation of each fix turned the tests red (18 of 18: both tier call sites, the claim / stateless / reply / choke-point tags, the digest, open-loops and night-mind filters, `mark_turn`, the notify cooldown and
self-skip, the negation / quote / idiom guards, opened fillers, AU hardcoded, default flipped to shadow). The first run found two green (the `blocks_write` gate with provenance off, `mark_turn`): both are tests now.

## Known limits (also in `open-problems.md`)
* Recall on unseen phrasing is the left column above. Not reviewed by a native speaker or a clinician (`reviewed: false` in both files); Spanish is a pending-decision pure data file.
* `routers/voice_tts.voice_command` has early exits (pending yes/no, form fill, Skybridge) before `fast_tiers.resolve`; a distress phrase that is ALSO one of those shapes is answered by them, then the next turn is caught.
* After the pointer the NEXT turn goes to the brain with the history in context; there is no sticky "stay gentle" window.
* The persona gate `MINORS_GET_PERSONA` is **not** flipped here (kid mode is not built; owner decision). The governance note's section 7 now says the path exists.
* Whether any spoken pointer reached a real child is not measurable offline: replay gate + the operator's live test (say a synthetic phrase to a test panel account).
