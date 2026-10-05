---
type: Reference
title: Memory authority - who said it decides who may change it
description: Every memory row carries a ranked provenance class; a write below the user classes can never supersede, archive or contradict a row that outranks it - it becomes a disputed candidate. The class, the allow-list, the choke point, the anchoring rule, the 14 retire-capable paths (covered vs follow-up), the rollout flag, the backfill, and the tests with their negative controls.
tags: [memory, authority, provenance, incident, flags, mempalace, people-graph]
timestamp: 2026-10-05T14:00:00Z
---

# Memory authority

Directive (owner, 2026-10-05): *"The memory needs to be flawless ... Samantha doesn't have
dementia, neither can Zoe."* Rule: **model inference must never supersede or contradict a fact the
user stated themselves.** Audit that found every writer: `docs/research/memory-fidelity-audit-2026-10-05.md`
(PR #1867) - this change is its P1.1 and P1.2 (P1.3 and P2 are follow-ups, below). The identity half
(a nightly-digest pass wrote the wrong name) is PR #1866 and is kept as the special case.

## The incident class

The nightly digest read a day transcript holding a speech-to-text fragment that named a third person,
the extractor asserted a fact about the owner from it, and its contradiction pass called
`MemoryService.review(edit)`. That **superseded a row the owner had stated** and **copied the old row's
`source` / `session_id` / `user_turn_id` / `source_excerpt` onto the model's text** - so the polluted row
looked like a regex write from a real conversation. No path checked who wrote the row it retired.

## The classes (`memory_authority.py`)

| Rank | Class | Which writers (allow-list; anything not listed is rank 0) |
|---|---|---|
| 5 | `operator` | `operator`, `operator-cleanup`, `identity_audit`, `admin`, `system`, `samantha_live_cleanup` |
| 4 | `user_confirmed` | the review UI (`review_ui`), the account acting on its own rows (`actor == user_id`: REST edits, intent handlers, MCP), the user approving a candidate |
| 3 | `user_stated` | the person's words typed or dictated (`voice_fact`, `proposal`, `conversation_correction`, `chat`, `skybridge_action`, `note_*`, `journal_*`, `person_created/updated`), deterministic extractors over the user's turn (`chat_regex`, `chat_regex_fallback`, `voice_regex`, `conversation`, `voice`), and a model writer whose fact the user's OWN turn supports (below) |
| 2 | `user_unverified` | reserved for voice turns attributed only by panel binding - nothing writes it yet (P1.3) |
| 1 | `model_from_turn` | `turn_digest`, `voice_turn_digest`, `person_extractor_llm`, `brain_tool`, `mcp`, `zoe_agent`, `decay_sweep` - when the turn does not support the fact |
| 0 | `model_from_transcript` | `digest`, `idle_consolidation` (unsupported), `consolidation`, `synthesis`, `music_digest`, the emotional pass, `profile-analysis`, `hindsight_retain_candidate`, **any unknown writer** |

Each row also gets `authority` (the owner's three-way view: `user_stated` | `user_confirmed` |
`inferred`), `authority_class`, `authority_basis` (why), `origin` (the real writer - finer than the lane
`source`; `person_extractor_llm` writes under `source="conversation"`), `turn_ref`, and `model` for LLM
writers. Stamped in **every** mode.

## The rule - one choke point (`MemoryService`)

* **May a write override a row?** Yes if its class is a **user class (rank >= 3)** - a later statement of
  the person's always wins, even over a row they once approved (this is the one place the rule departs from
  the audit's literal `rank(writer) >= rank(target)`, so a typed correction can replace a `user_confirmed`
  row) - or if `rank(writer) >= rank(row)`. Below that it is held back.
* **Held back = a `disputed` candidate.** `ingest` stores the fact with `status=disputed`,
  `contradicts_id=<row>`, `authority_blocked=true`; `review(edit)` leaves the same candidate and returns
  `None` (every caller already treats `None` as "supersede refused"). `disputed` is a blocked-read status,
  so a candidate is never recalled. Log (labels only, never text):
  `AUTHORITY_BLOCKED writer=<name> kind=<attr> action=<ingest|edit|rewrite|archive|supersede|approve>`.
  `kind` is a closed vocabulary (`name birthday age home work relationship pet health preference other`).
* **Where:** `ingest` (a contradicting fact), `review` edit / archive / reject / approve (an automatic
  actor cannot approve a candidate raised against a row), `supersede_by` (the SUCCESSOR's class decides:
  the implicit-supersede passes), `archive_duplicate` (the weekly merge). `sweep_soft_archive` goes through
  `review(archive)` so the decay sweep (`decay_sweep`, rank 1) can no longer archive a never-recalled row
  the user said.
* **Contradiction** = same subject + same attribute + a different value (`memory_authority.conflict_kind`
  on the shared reconciler's attribute matcher and `memory_supersede`'s subject/home/change-cue matchers),
  or a stated END of it ("no longer works at X"). Pure restatements by a lower class are refused quietly
  (`action=rewrite`, no candidate).
* **A supersede records the NEW writer.** `review(edit)` no longer copies `source`, `session_id`,
  `user_turn_id`, or - for a writer below the user classes - `source_excerpt`; it stamps the editor
  (`source` is the editor's lane label when on the allow-list, else `review_ui`; `origin` is always the
  exact actor). Callers pass `session_id` / `turn_ref` of the new write.
* **The user resolves it.** A person approving a candidate (`review(approve)` by a user class) makes it
  `user_confirmed` and **retires the row it disputed**; rejecting it keeps the row and the candidate is
  not resurrected by the next extraction.

## How a model writer earns `user_stated` (`supports`)

The caller passes the user's OWN turn text as `anchor_text` (user turns only - never assistant text;
defaults to `source_excerpt`). The fact is supported when ONE user sentence (or two adjacent ones of the
same turn - never across turns) holds all its value tokens (names, numbers, places), the attribute cue it
names (name / where they live / work / age / birthday / a liking / an allergy, with synonyms), most of its
other words, and - for a fact about the user - the user speaking in the first person. So `uh Casey is
coming over, I think` does not support `User's name is Casey` (no name cue), `Casey moved to Perth` does
not support `User lives in Perth` (no first person), and `my dog is Teddy` / `uh Rex is coming` on two
turns does not support `User's dog is Rex`. Dates are normalised day-first before matching.

P1.2 (digest): `_EXTRACTION_PROMPT` now asks for `{"fact","type","quote"}` - the user's own words, verbatim.
`memory_digest.fact_anchor` uses the quote only if it is a verbatim span of the user turns (a hallucinated
span leaves the fact unanchored), and falls back to the whole user transcript only when the model returned
no `quote` field. Idle consolidation now sends **user turns only** to the extractor (it fed the assistant's
lines too) and anchors the same way. The weekly merge **archives identical duplicates** (keeper = strongest
class, then confidence) instead of rewriting the weaker row - `archive_duplicate` - and leaves a merely
similar row alone.

A third-person name that the identity wall drops (`User's name is <X>`) and that merely appears in the
user's text becomes a **pending PERSON candidate** ("<X> was mentioned in conversation ...",
`person_pending`), never a user attribute. If the user did say it is their name, nothing is created.

## The 14 retire-capable paths (audit section 2.2)

| Path | Covered here? | How |
|---|---|---|
| W1 regex extractor | yes (user class; may override) | `chat_regex` etc. = `user_stated` |
| W2 turn digest | yes | `model_from_turn`, `user_stated` only if the turn supports it |
| W3-edge relationship-graph supersede | yes | migration 0037 stamps edges (`authority`, `origin`); an inferred writer cannot close a user-stated or unstamped (legacy) edge - it leaves a candidate (`contradicts_id=edge:<id>`) |
| W4 LLM person extractor | yes | own `origin=person_extractor_llm` (was indistinguishable from the regex half); a blocked fact is not written to the structured people tables either |
| W6a nightly contradiction judge | yes | `review(edit)` with the verbatim quote as anchor |
| W6b nightly reconcile update | yes | same call |
| W8 idle consolidation | yes | user turns only + anchor |
| W9 weekly merge | yes | `archive_duplicate`, no rewrite |
| W10 weekly contradiction judge | yes | `consolidation` = rank 0 |
| W11 write-time implicit supersede | yes | `supersede_by`, successor's class decides |
| W12 nightly implicit-conflict pass | yes | same |
| W16 decay archive | yes | `decay_sweep` = rank 1 |
| W20 voice teach | yes (user class) | `voice_fact` |
| W22 MCP `memory_review` as the user | **no - follow-up** | the agent edits under the owner's id (`actor == user_id` => `user_confirmed`), indistinguishable from the owner by construction |

Not in this PR (follow-ups): **dispute -> question** (the proactive selector / `pending_suggestions` asking
"earlier you told me X; I heard Y - which is right?"; candidates already carry `contradicts_id`, and
`review(approve)` resolves them, so the asker is the only missing piece); **P1.3 speaker gate**
(`user_unverified` for panel-bound turns); the `used to love <city>` cue false positive (P2.1); durable
forgetting; REM/deep-sleep raw upserts; `brain_tool` / `mcp` carrying the user's turn as `anchor_text`
(today a conflicting `remember_fact` is held back and the brain is told to say so - the reply no longer
claims "Got it" over a held-back write).

## `ZOE_MEMORY_AUTHORITY` - `enforce` (default) | `shadow` | `off`

Read per call. `shadow` logs `AUTHORITY_WOULD_BLOCK` and changes nothing; `off` disables the wall.
Provenance stamping and the "an edit stamps the editor" fix are **unflagged** (they are pure correctness
and the wall depends on them). **Default `enforce`, argued:** the only thing the wall ever does is park a
MODEL's overwrite of something the user said as a `disputed` candidate - lossless (the text is kept),
reversible (approving it applies it) and invisible to recall - while a writer that has the user's turn earns
`user_stated` through `supports`, so what the user said is never refused. `shadow` default would leave the
incident class open for a week on a box whose only user is the owner. **Not measured:** the bar and the
replay gate were not run (the box is RAM-starved); the operator should run the Samantha bar (S2, S10,
S20-S22) and watch `AUTHORITY_BLOCKED` / `AUTHORITY_WOULD_BLOCK` for a night before relying on it, and flips
to `shadow` with one env line if a legitimate update is being parked. Voice-gate: none of the changed files
is in `voice_gate_check.VOICE_PATH_PATTERNS`.

## Existing rows (backfill)

The live code derives a class for unstamped rows (`legacy_class_basis`), so nothing needs migrating for
the wall to work. `scripts/maintenance/memory_authority_backfill.py --dry-run` reports the counts (class x
status, basis, writer, user, incident-shaped rows; never row text) and `--apply --i-have-reviewed
--i-stopped-zoe-data` persists the stamps (metadata only; idempotent). Rule: reviewed by a model -> model
class (the incident row: `source=chat_regex, reviewed_by=digest`); reviewed by a person -> `user_confirmed`;
source on the user allow-list -> `user_stated` (`review_ui` -> `user_confirmed`); a model writer that read
the user's turn -> `user_stated` only if the stored `source_excerpt` supports the text; anything else ->
rank 0. **Not run against the live palace.**

## Tests (all `ci_safe`, synthetic data, negative controls)

* `test_memory_authority.py` - provenance, store -> recall, the user's correction wins, an inferred
  contradiction cannot win (the digest incident replayed on a generic attribute), negation is
  `user_stated`, "I used to" keeps history, a third-person fragment creates no user fact (a PERSON
  candidate), isolation, anchoring table, the legacy rule, and the bar-shaped fixtures
  (`tests/fixtures/memory_authority_scenarios.json`, replayed at the store level; **not** wired into
  `scripts/perf/samantha_bar.py` - new scenario ids need a `--record-baseline` run on a healthy box).
* `test_memory_authority_matrix.py` - **writer x target-class** for every path above, with
  `ZOE_MEMORY_AUTHORITY=off` as the negative control on every cell (the S1 signature), shadow mode, the
  quote anchor, idle consolidation sending user turns only, the weekly merge.
* `test_memory_authority_people.py` - migration 0037, edge stamps, the edge wall + control.
* `test_memory_authority_backfill.py` - the dry-run report (read-only, no text) and the apply.
* `test_identity_facts.py` - the digest-replay control now removes BOTH walls; removing only #1866's
  leaves the genuine row standing (defence in depth).
