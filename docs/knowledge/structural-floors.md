---
type: Reference
title: Structural, language-independent memory floors - the claim row, id-triple roles, lexicons as data
description: What ZOE_STRUCTURAL_CLAIMS (off | shadow | enforce, default shadow) changes in the memory floors and the role guard - the extractor's claim row (subject, predicate, value, polarity, modality, tense, the owner's verbatim quote), the language-independent checks that authorise, the per-language lexicons that only pre-filter, retirement by claim key, role claims supported only by people-graph id triples, the off-path 4B yes/no verifier, how to read the shadow logs, the enforce checklist, and the measured limits.
tags: [memory, authority, multilingual, floors, claim-row, role-guard, lexicons, verifier, samantha-bar, flags]
timestamp: 2026-10-09T12:00:00Z
---

# Structural memory floors (`ZOE_STRUCTURAL_CLAIMS`)

Why: `docs/research/structural-floors-2026-10-09.md`. The lexical floors (`memory_authority`, `role_guess_guard`, `people_roles`) decide polarity,
contrast, end-state, hedge and role by English word lists: 0.98 balanced accuracy on the phrasings they were tuned on, 0.59 on new English
(two false promotions: "My mum is moving to Bendigo next month", "...but not in the town itself" both promote "mum lives in Bendigo"), 0.50 on
es / fr / de / zh / ja (they never fire). The fix separates **reading** (the multilingual extractor) from **authority** (structure that does not
care about the language).

## The switch

`ZOE_STRUCTURAL_CLAIMS` - a per-call env read (the process sees a changed unit env after a restart; no code path caches it).

| mode | the extractor | the lexical floor | the structural floor |
|---|---|---|---|
| `off` | legacy prompt, 256 tokens | decides | not computed, nothing stored or logged |
| `shadow` (**default**) | the **legacy** call, byte for byte as `off` (same prompt, 256 tokens, same stored rows); the claim row is read afterwards by a SEPARATE bounded call (`structural_reader`, `ZOE_STRUCTURAL_READER`) over the facts that call stored | **decides** | computed from the reader's rows and only logged beside the lexical one (`STRUCTURAL_FLOOR lane=turn_digest_post`, `STRUCTURAL_READ`, the would-retire and verifier lines); nothing is stored on the row |
| `enforce` | asked for a claim row beside every fact in the extraction call (640 tokens, 30 s) | the fallback when a fact has no valid claim row | **decides** wherever a valid claim row exists; retires by claim key; role claims by id triples |

Only the word `enforce` enforces (`1` / `true` / `on` / a typo are `shadow`). A fact without a claim row, and every nightly-digest fact, keeps the lexical
decision in every mode.

## The claim row (written once, at extraction)

In `enforce` only, `memory_digest._turn_prompt_claims()` (the legacy prompt plus `structural_claims.CLAIM_FIELD_RULES`) adds one `claim` object per fact; in `shadow` the same field definitions are the post-turn reader's prompt: `subj` (`user` | `rel:<kin>` | `person:<Name>`), `pred` (closed list),
`obj`, `pol` (`affirm` | `negate` | `ended`), `mod` (`asserted` | `hedged` | `hypothetical` | `question` | `reported`), `tense` (`current` | `past` |
`future`), `quote` (the owner's own words, verbatim) and `lang`. A contrast ("my mum lives in Bendigo, not Ballarat") is TWO items: the new fact and the
old value (`pol negate`, quote "not Ballarat"); the second is never stored as a fact, it retires Ballarat's row by key. Polarity, modality and tense are
**decided once and stored**; nothing re-derives them from prose later. Malformed rows are "no claim". The quote and value pass the same PII scrub as the
evidence excerpt (a claim that would carry anything the scrub touches is not stored). `finish_reason`-style cuts keep every complete item
(`memory_digest._salvage_items`, claim mode only).

## What authorises, what only vetoes (`structural_claims.decide`)

Authorising (language-independent, `_check_*`): the quote is a substring of the owner's turn (NFKC + casefold + accent/whitespace fold; the numeric-date
normalised variant anchors but never promotes); the value is in the quote and in the fact (edit distance - none for 1-4 letters, 1 for 5-6, 2 for 7+ - or a
long shared stem; numbers are exact); the numbers of the fact are in the quote; modality `asserted` (promotion) / `asserted | hedged` (anchoring); the
subject is the speaker or one of their relatives; the speaker was not rejected.

Vetoing (the lexicon, `lexicons.py` + `lexicons_data/<lang>.json`, **pre-filter only**): the fact's wording and the quote must agree in polarity with the
claim row (a negation that governs the value, a denied end, an end-state verb, `no longer`); a hedge / wish / question / past marker in a quote the
row calls plain; a negation in the quote that no sibling claim accounts for (`unaccounted_negation` - the declined "not within the city limits" case is now
an explicit, logged ambiguity); a relative named in a quote claimed as the speaker's; no first person where the language needs one. A language with no
lexicon contributes no veto (it is never guessed from English); a denial or an end whose wording nobody can read is held (`polarity_uncorroborated`).

The English words were MOVED into `lexicons_data/en.json`, not rewritten: `tests/test_lexicon_english_identity.py` pins every regex byte for byte. The es / fr /
de / zh / ja files are author-written, NOT native-reviewed (`reviewed: false`, research E6); a language's `enabled` is true only while its labelled-set rows pass
(`tests/test_structural_floors_labelled.py`). Adding a language = one JSON file + fixture rows.

## Retirement by key

`memory_supersede.retire_by_claims`: a negate / ended claim retires an older approved row whose stored claim has the same subject + predicate + value; an
affirm on a one-valued slot (`residence`, `birthday`, `age`) retires the older value. Older rows without a claim fall back to `owner_key_match` (the #1925 text key). Only
the owner's word retires (a shadow-mode comparison uses the structural label). `supersede_by` keeps both timelines (`invalid_at`, `expired_at`); nothing is deleted.
`shadow` logs `STRUCTURAL_RETIRE ... would_retire=N` and changes nothing; `enforce` acts, independent of `ZOE_MEMORY_IMPLICIT_SUPERSEDE`.

## Roles: id triples only (`role_triples.py`, `role_guess_guard.neutralise(..., triples=, ids=)`)

A role claim is allowed iff the triple `(people.id, kin code, owner)` - or a more specific one that implies it - is in the user's people graph:
`person_relationships` current edges (both directions; an `inferred` edge counts for nothing) and `people.relationship` (the speaker has no node: this column is
their edge, owner = the account). A gender-neutral edge cannot license a gendered claim (`spouse` does not say `wife`). In `enforce` the packet and the owner's
turn are **not parsed**: an ownerless row cannot exist, a denial is the absence of a triple, a lowercase namesake has another id, "your friend's wife" is an owner
chain over triples, an ambiguous given name names nobody, a failed read (`complete=False`) fails closed. The reply-side claim reader stays (English patterns); a
role behind a possessive ("your mother's friend") is no longer read as a claim about the mother. `shadow` logs `STRUCTURAL_FLOOR floor=role ... lexical=allow
structural=deny` and keeps the lexical decision. The one `zoe_flue_client.py` change (voice-path file): one bounded read (0.4 s in enforce; a background task in shadow) of the triples on a turn that already
named a person, and two sink keys.

## The off-path verifier (`structural_verifier.py`, `ZOE_STRUCTURAL_VERIFIER` = shadow (default) | off)

After the digest has stored the turn, facts whose two floors disagreed (or that tripped a pre-filter) are put to the live 4B as a constrained
`grammar: root ::= "yes" | "no"` judge (2 tokens, temperature 0, ~280 ms p50 in the research) and the verdict is **logged**
(`STRUCTURAL_VERIFY verdict=.. lexical=.. structural=.. agrees_structural=..`). It decides nothing. Bounded: 2 calls per digest, 60 per hour per process, 3 s timeout,
serial, a breaker after 3 failures (5 min). It is awaited only from `memory_digest._structural_post` (post-turn); a test pins that no voice-path module imports it.
`scripts/perf/structural_verifier_live_check.py` is the bounded (<= 40 call, `flock /tmp/zoe-voice-harness.lock`) measurement.

## Reading the shadow logs (`~/.zoe-logs/`, labels only - never the fact or the owner's words)

* `STRUCTURAL_READ lane=.. facts=.. with_claim=.. ms=..` - the shadow reader's one post-turn call (at most 4 facts, 360 tokens, 12 s, 120 an hour, breaker after 3 failures; never on a turn that stored nothing);
* `STRUCTURAL_EXTRACT lane=.. ms=.. max_tokens=640` - the `enforce` extraction call; compare `ms` with the legacy digest call to size the brain-slot cost.
* `STRUCTURAL_CLAIMS rows=N with_claim=M` - the extractor's claim emission rate.
* `STRUCTURAL_FLOOR floor=support lane=.. lang=.. lexical=<promote|anchor|hold> structural=<promote|anchor|hold|no_claim> agree=0|1 reasons=.. ambiguous=..`
* `MEMORY_ROW_CLAIM lane=.. id=.. claim=<pred>/<pol>/<mod>/<tense>:<lexical>><structural>` per stored row (beside the unchanged `MEMORY_ROW` line).
* `STRUCTURAL_RETIRE mode=shadow would_retire=N keys=..`, `STRUCTURAL_VERIFY ...`, `STRUCTURAL_FLOOR floor=role|role_marker ...`.

## Before flipping `enforce` (operator checklist)

1. A day of shadow: `with_claim / rows` >= 0.9; `STRUCTURAL_EXTRACT ms` p95 within the digest's budget; per language, `structural=promote lexical!=promote` rows read by hand
   (100 rows, research M3); `lexical=promote structural!=promote` rows are the false promotions it would remove.
2. Role: `floor=role ... lexical=allow structural=deny` counts. Expect denials where a role lives only in memory TEXT (no `people.relationship` / edge yet) - the
   triple source is the people graph, so the next step is writing the structured row where the owner states a role.
3. Replay gate (voice-path file `zoe_flue_client.py`): the PR lands via `deploy.yml`'s fresh-artifact check.
4. Flip: `ZOE_STRUCTURAL_CLAIMS=enforce` in the unit env, restart zoe-data, poll `/health`; roll back by `shadow` / `off`.

## Measured, and what is NOT measured (2026-10-09; the claim rows in the fixture are author-written ORACLE readings)

Given a correct claim row, the structural floor scores (balanced accuracy, support rows): ledger 0.99, held-out English 0.95, es / fr / de / zh / ja 1.00 (each 18 rows),
against the lexical floor's 0.98 / 0.59 / 0.50. The extractor's own accuracy at emitting the claim row is **not measured live** (the shadow logs measure it); a sloppy
reading (subject = user, plain, current, whole sentence as the quote) still leaks 1-5 of 13-58 negatives per group through the pre-filters (bounded in
`test_structural_floors_labelled.py`). See `docs/knowledge/open-problems.md` for the open items.

## Why `shadow` does not touch the extraction (2026-10-09, the regression this section records)

The first `shadow` asked the per-turn extractor for the claim row INSIDE the extraction call. A 4B asked for a different JSON shape returns different
FACTS and words them differently, so "shadow" was not byte-identical for the stored text: on the live acceptance run (main `4bb903d2`) "My friend Dana
has two kids, Mika and Biscuit" began to store `Dana Whitfield has a child named Biscuit` (the 10-07 PASS stored only `User's friend is named Dana
Whitfield`) and the pet correction (#1860) lost its race to that row (bar S21 FAIL: the store still lists Biscuit as a child), and the digest's
own sentence for "I live in Hobart now" no longer differed from the regex fragment beside it. Rule: **a measurement mode never changes what the
model is asked to extract.** `tests/test_structural_claims_digest.py` pins it - the extraction payload and every stored row (text, type, class,
basis, status, every metadata key) are equal for `off` and `shadow` over a seed set of the acceptance turns, and the control (taking the fix out)
turns the test red. `enforce` still asks inline (the claim row must exist at write time to decide), so the first `enforce` flip needs its own bar run
(S21 and the digest's child/pet facts) before it stays.

Measured live on the 4B (14 calls, 2026-10-09 15:3x, `flock /tmp/zoe-voice-harness.lock`, the same turns and temperature 0.1): "My friend Dana Whitfield has two
kids, Mika and Biscuit." - legacy prompt `["User's friend is named Dana Whitfield", "Dana Whitfield has two kids"]` in 2.1 s; claim-row prompt adds `Dana Whitfield has
a child named Mika` / `... Biscuit` in 6.8 s. "Big news - I've moved. I live in Hobart now." - legacy `User has moved and now lives in Hobart` (a change-of-state fact
that is never deduped away) vs claim-row `User now lives in Hobart.` (an overlap-duplicate of the regex row, silently dropped). The post-turn reader over the legacy
facts returned a well-formed claim row for every fact (`pet_name`, `residence`, `name`, `kin:has`). The same session's place fragments (`you live in these days`,
`... Hobart now`, `... Dunedin, by the way`) came from the deterministic extractor's unbounded `(.{2,80})` capture, not the model - fixed in `memory_quality.clean_place_value`.
