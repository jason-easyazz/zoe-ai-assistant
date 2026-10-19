---
type: Reference
title: Caregiver curation - a parent adds memories for a child (memories-space phase 7)
description: Flag ZOE_MEMORIES_CURATORS (default off). Every adult household admin is the implicit curator of every child (D7) and adds, fixes or removes notes about them from the phone or desktop Memories page ("Family memories"). Curated rows carry provenance and a new authority class above the child's own passing statements (D9); a contradicting remark is held for the curator. Health, medication and money need a per-child opt-in (D10). An adult is curated only in categories they opted in themself. Sensitive curated rows never reach the wall (D11). Security model, operator steps, rollback, what is unproven.
tags: [memory, caregiving, children, privacy, provenance, authority, forget, flag-dark]
timestamp: 2026-10-19T00:00:00+08:00
---

# Caregiver curation (`memory_curation.py`, `routers/memories_curation.py`, `js/memories-curation.js`, migration 0061)

Design: [memories-space.md](../architecture/memories-space.md) 2.3, 9 (P7 build status) and 11 (owner answers 2026-10-19).
Authority wall: [memory-authority.md](memory-authority.md).

## What a parent can do

On the phone or computer, Memories page, **Family memories** (shown only when the flag is on and the server offers it):

- pick a child; see ONLY the notes added for that child (by any adult admin), never the child's other memories;
- **add** a note: one sentence + a category (Routines, People, School, Important dates, Likes, Other; Health, Medication,
  Money once switched on). "Mia's teacher is Ms Lee" is stored on Mia's account as "User's teacher is Ms Lee";
- **fix** or **remove** a note (remove asks first; it is the audited forget, with the physical erase);
- switch **health / medication / money** on or off for a child (off by default);
- answer the **held notes**: when the child said something different ("my teacher is Mr Brown"), keep the note or use what
  they said.

An adult member can open the same page and choose which of their own categories an admin may look after ("Help with my
memories"). Turning one off gives those notes back to them: their own word wins again.

The child hears the notes when they ask Zoe (they are ordinary rows of the child, found by recall), sees them on their own
Memories list labelled "Added for you by <name>", and cannot forget or correct them.

## By voice at the panel (VOICE-PATH, replay-gated)

"Zoe, remember that Mia's swimming is on Tuesdays" from a curator in THEIR OWN panel identity (`extra_ctx['scope_user']`: a
verified voice session or a fresh PIN) writes the same curated note (`curated_via="voice"`) through `memory_curation.by_voice`,
called first in `memories_voice.tier` (inside `fast_tiers._provenance_tier`). The category is guessed from the words (health /
medication / money by the classifiers, refused aloud unless opted in; then school, dates, routines, likes, people, other). Zoe says
"Okay, I'll remember that for Mia." and never repeats the note. A guest, a child, a non-admin, a speaker the gate rejected
(`speaker_verified is False`), the chat lane, a dry turn, a name that is none of the curator's people, or "remember that I ..."
fall through untouched (the existing teach path). Two curatable people with the same first name: "Add it on your phone".
Pinned by `tests/test_memories_curation_voice.py`.

## Walls (each pinned in `services/zoe-data/tests/test_memory_curation.py`, with a control)

| Wall | Where |
|---|---|
| a curator is an ADULT ADMIN: session role AND `auth_users` role AND `manner_block._account_is_adult` | `memory_curation.require_curator` |
| no session header, a guest, a degraded auth check: refused | `memory_space.require_member` |
| a panel never reaches the routes (a device token or the kiosk cookie = 403) | `routers/memories_curation._guard` |
| an adult only in a category they opted in THEMSELF; an admin cannot opt an adult in | `memory_curation.policy`, `set_child_sensitive` |
| health / medication / money refused until opted in; health or money words filed elsewhere refused (422) | `memory_curation.check_text` |
| feelings, family conflict, grief never curated | `check_text` (`NEVER`) |
| a child sensitive category switched off again hides its notes from the curator (counted, not shown, not editable) until it is back on | `memory_curation.curated_rows` |
| class `curated` (rank 6): only a curator or an operator changes it - the child's passing remark becomes a `disputed` note, the review UI and the Memories screen refuse, a held note cannot be approved by the child | `memory_authority.may_override`, `MemoryService._authority_refuses`, `_operator_rows_sync`, `memory_space.curated_locked` |
| a passing DENIAL ("I'm not allergic to peanuts") is held too | `memory_authority.negation_flip` (curated targets only) |
| provenance on every row: `curated_by`, `curated_by_name`, `curated_at`, `curated_via`, `curated_category`, `curated_sensitive` | `memory_service.CURATED_KEYS` (first-class only for the `curator` writer) |
| only `memory_curation` writes as the `curator` writer | static test |
| every action audited BEFORE it runs (no audit row, no change) and when it finished; ids, category, a row-id hash, never text | `memory_curation_audit` |
| a sensitive curated row is private by its category, so the panel counts it and never shows or speaks it | `provenance_answers.sensitive_classes` |

## Operator steps

1. Run the migration (0061; the coordinator re-points `down_revision` onto the latest pending head at landing).
2. Set `ZOE_MEMORIES_CURATORS=1` in zoe-data's env and restart zoe-data (the flag is read per request; the routes are always
   mounted and answer 404 while it is off).
3. Make sure each child account has the `child` role (or a `kid` / minor `member_modes` row) in zoe-auth: an account that is
   neither positively adult nor positively a child is not curatable.

Rollback: unset the flag (404 everywhere). Curated rows already written stay ordinary rows of the child, still protected by the
`curated` class; an admin can remove them through the review UI as an operator.

## Not built / unproven

- **D8** (a carer acting for an adult who cannot consent) is a legal question; nothing acts for an adult without their own opt-in.
- Proven only by tests and the replay gate: no live run of the spoken curator intent yet.
- Recall phrasing: the brain sees a curated row as an ordinary fact; the "Mum added a note" lexicon line of design 2.3 is not built.
- Contradiction detection is the shared matcher (`conflict_kind`) plus the negation flip; a paraphrased contradiction it cannot
  match is stored beside the curated row, not held.
