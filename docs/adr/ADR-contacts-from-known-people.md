# ADR: Contacts from known people — bridge the narrative layer to structured contacts

## Status

Accepted (2026-07-08). Phase 1 in progress.

## Context

Zoe has two memory layers that never meet:

1. **Narrative / vector** — conversation → MemPalace facts → an LLM-synthesized **portrait**
   (`user_portrait.py`). This is how Zoe *knows* people. It is prose: rich, but you cannot attach a
   birthday to a sentence.
2. **Structured contacts** — the `people` table (`name, relationship, birthday, phone, email,
   circle, …`), which the recall **dossier** (PRs #1169/#1170) and the contacts UI read.

Structured `people` rows are created by only two things:

- **Manual creation** — `routers/people.py:258` (full row, birthday-capable).
- **The relationship regex extractor** — `person_extractor.py:474`, which fires on narrow patterns
  ("X is my mother") and inserts a **bare `is_partial=1` stub** (`name, circle, context, visibility`
  only — *no* birthday/phone/email), which recall excludes.

**There is no bridge from layer 1 to layer 2.** So people Zoe clearly knows from natural
conversation (e.g. Jason's family — Janice, Niel, Karen, Julie — present in his portrait) never
become editable contacts. Confirmed live 2026-07-08: `for-prompt` for user `jason` renders the
portrait naming his family but contributes **zero** structured `[people]` refs — the dossier has
nothing to show because the structured layer is empty. (`family-admin` is the legacy
unauthenticated identity, `auth.py:30`; the real account is `jason`.)

Related dead-end: a mentioned birthday is written to `person_important_dates` **only if the person
already has a row**, so birthdays for not-yet-contacts have nowhere to land.

## Decision

Build the bridge as **one confirmable write path** fed by two sources, so nothing silently creates
junk contacts. Everything routes through the existing `pending_suggestions` pipeline (store →
`list_active` → `ui_components_for_suggestions` → `execute_suggestion`), which already backs
`list_add`/`reminder_create`/`calendar_create`/`note_create` — it just lacks a contact action.

Every new contact is **user-confirmed**; the `_looks_like_person_name` precision guard (#1168) is
reused; every phase is **flag-gated + demo-user-lab-proved before prod**; hot-path changes are
**replay-gated**.

### Phase 1 — `person_create` suggestion executor *(foundation)*

Add a `person_create` action to `pending_suggestions._execute_action` that inserts a **full**
`people` row (`is_partial=0`; `name` + optional `relationship`/`circle`), deduped against the user's
existing non-deleted people, guarded by `_looks_like_person_name`, behind
`ZOE_PERSON_SUGGEST_ENABLED` (default OFF). **Private by default** — `visibility='personal'` (the
owner still sees it; not auto-shared with the whole family, since a proposed contact may be
personal), and `circle` is left NULL unless a real category is supplied; both overridable via slots.
Surfaces through the existing UI-component path. No schema change. Inert until a source (Phase 2)
emits `person_create` suggestions.

### Phase 2 — two sources feed the executor

- **2a Propose-on-mention (go-forward):** extend `latent_intent_detector` to emit a `person_create`
  suggestion when a message names a person who isn't a contact (slots `{name, relationship}`), same
  flag + precision guard.
- **2b Backfill-as-proposals (clears the backlog — the Janice/Niel/Karen/Julie fix):** a one-shot
  admin pass reads the user's `person`-type MemPalace memories (+ existing stubs), extracts distinct
  `name`+`relationship`, dedups (`_resolve_person_uuid` + `merge_person`, #1036), and emits a batch
  of `person_create` proposals to accept. Flag `ZOE_CONTACT_BACKFILL_ENABLED`; demo-user lab first.

### Phase 3 — promote-on-confirm + birthday capture *(safe "enrich", not global un-stub)*

Do **not** flip `is_partial` globally (that would flood contacts with every mentioned name). Instead
promote a stub → full on confirmation (accepted suggestion) or a mention-count threshold, and let a
mentioned birthday land on the person once it is a real contact. Touches the hot
`person_extractor` write-path → replay-gated.

## Consequences

- The dossier gains real content: known people become editable contacts you can add birthdays to.
- No silent junk: creation is always user-confirmed and precision-guarded.
- Reuses existing rails (suggestions pipeline, person-merge, precision guard) — small increments,
  no schema change for Phases 1–2a.
- Contacts must be owned by the authed identity (`jason`), not legacy `family-admin`; this work
  assumes the identity migration and writes under the acting user.

## Acceptance criteria

- Accepting a `person_create` suggestion creates a full, editable `people` row under the acting
  user; duplicates are not created; pronoun/junk names are rejected.
- Backfill turns the family Zoe already knows into a batch of accept-able contact proposals.
- Every phase flag-gated, byte-for-byte no-op when off, lab-proved before prod; Phase 3 replay-gated.
