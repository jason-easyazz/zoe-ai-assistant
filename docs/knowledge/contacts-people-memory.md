---
type: reference
title: Contacts / people-memory — operational reference
description: How the contacts-from-known-people loop works end to end, the live flag state, the write-path FK bug class that bit three times, and the E2E test-harness traps. Read before touching a people/suggestion write path.
---

# Contacts / people-memory — operational reference

Operational SSOT for the **contacts-from-known-people** loop: turning people Zoe
already knows about into full, private, editable contacts. Design records:
[ADR-contacts-from-known-people](../adr/ADR-contacts-from-known-people.md) (original)
and [ADR-contacts-production-hardening](../adr/ADR-contacts-production-hardening.md)
(researched hardening, P1–P5). This doc is the *how it behaves live + how not to
re-break it* companion.

## The loop (verified live, 2026-07-09)

```
mention "my brother Daniel"
  → detect_and_store: _deterministic_person_proposals (regex) ∪ person_extractor_llm
  → person_create pending suggestion (deduped, user-scoped)
  → surfaces two ways:
       • flue brain: /api/memories/for-prompt folds the offer in (P1) ✅ live
       • touch panel: ui_components_for_suggestions → action_card ⚠️ emitted, not yet rendered (see gap below)
  → user says "yes" / taps Add
  → execute_suggestion → intent_router._execute_people_create_direct
  → full private contact (is_partial=0, visibility=personal, circle=circle)
  → zoe_memory_compose dossier renders it
```

Two halves, kept complementary: **this = create/surface** people as structured,
confirmable contacts; **recall-quality** (graph adjacency, linkage — #1197/#1198/#1199,
`docs/architecture/memory-system-audit-2026.md`) = make recall of them sharper.

## Live flag state (services/zoe-data/.env, 2026-07-09)

| Flag | State | Effect |
|---|---|---|
| `ZOE_MEMORY_COMPOSE_ENABLED` | **ON** | recall packet composes structured sections |
| `ZOE_PERSON_DOSSIER_ENABLED` | **ON** | compact per-person dossier line in the packet |
| `ZOE_PERSON_SUGGEST_ENABLED` | **ON** | propose-on-mention + person_create tool + P1 surfacing |
| `ZOE_CONTACT_BACKFILL_ENABLED` | **ON** | backfill known people → proposals + `/pending-contacts` |
| `ZOE_PERSON_LLM_CONFIDENCE_GATE` | **OFF (dark)** | P2 confidence gate; replay-gate before flipping |
| `ZOE_PERSON_LLM_CONFIDENCE_MIN` | 0.4 | discard threshold when the gate is on |

Every flag is byte-for-byte a no-op when OFF. Enable/rollback procedure (incl. the
recall-dossier flags): [relationship-memory-flag-enable runbook](relationship-memory-flag-enable.md).

## ⚠️ The write-path FK bug class (bit 3× — read before adding any people/suggestion write)

`people.user_id` and `pending_suggestions.user_id` both **FK → `users(id)`**. The
**chat** path ensures the acting user exists (`_ensure_user_and_chat_session`); the
**voice / tool / intent-dispatch** paths do **not**. So a write for an authed identity
that only has *memories* (no `users` row — e.g. a fresh flue/voice user, or `jason`
before he'd ever chatted) **silently FK-fails**.

Rule: **any code that INSERTs a row keyed on `user_id` from a non-chat path must
upsert the users row first** —
`INSERT INTO users (id, name, role) VALUES ($1,$1,'member') ON CONFLICT DO NOTHING`.

Instances found + fixed this session:
- **#1200** — `_execute_people_create_direct` INSERTed `people` with no users row →
  FK reject → handler swallowed it, returned None, fell back to mcporter (persists
  nothing). The live *"I tried to add her, nothing happened"* bug.
- **#1215** — `store_suggestions` had the identical FK gap and **swallowed the error
  silently**; propose-on-mention stored 0 rows. Fix = same upsert + the swallowed
  `except` elevated to **WARNING** (a silent DB except hid this for a long time).
- **#1216** — `people.circle` is **`TEXT NOT NULL DEFAULT 'acquaintance'`**, and
  `'circle'` is a **valid tier** (`inner | circle | public`), *not* a bogus literal.
  A well-meaning `circle = ... or None` broke the NOT NULL constraint on accept
  (*"null value in column circle"*). Default to `'circle'`, never NULL. Test schemas
  now declare the column NOT NULL so a regression fails in CI, not in prod.

Meta-lesson: two of the three were **swallowed exceptions**. A `try/except` around a
DB write on a capture path must log at WARNING+ or the failure is invisible.

`AsyncpgCompat.commit()` is a **no-op** (asyncpg autocommits outside an explicit
transaction) and `_release_safely` does **not** rollback — don't rely on either for
atomicity.

## E2E test-harness traps (for anyone testing this live)

- **Chat/voice identity override is token-gated.** `X-Zoe-User-Id` is honoured **only**
  with a valid `X-Internal-Token` (from `.env`); loopback alone is denied (#1054/#1090).
  Omit the token and your turn silently runs as **guest** → false negatives.
- **The accept endpoint needs a real session.** `POST /api/proactive/suggestions/{id}/accept`
  uses full `get_current_user` session auth — the internal-token override does **not**
  satisfy it. The **touch panel** provides real auth; a synthetic script can't. To test
  the accept path from a script, call `execute_suggestion` / `_execute_action` directly.
- **Standalone scripts must `await db_pool.init_pool()`** or every DB op fails and (per
  the class above) may be swallowed → looks like "stored 0", is actually "never connected".
- **Background capture is async + LLM.** `person_extractor_llm` + `detect_and_store` run
  post-turn via `asyncio.ensure_future`; wait **~30s**, not 20s, before asserting a
  proposal exists. E2E false negatives here cost real debugging time.
- **Deploy ≠ merge.** A merged PR is live only after `systemctl --user restart
  zoe-data.service` (operator-run; the classifier blocks agents from the restart). The
  driver's checkout-sync does not reliably restart.

## ⚠️ Known gap: the touch panel doesn't render `action_card` yet

`ui_components_for_suggestions` emits a proper `person_create` confirm card (#1222:
`{"type":"action_card","title":"Add {name} as your {relationship}?","actions":[Add,Dismiss]}`),
but the panel **does not display it**. `chat.html`'s `renderChatComponent` switches on
`data.component`; these cards carry `type:"action_card"` with **no `component` field**, so
they fall through to `[Unknown component: undefined]`. This is **pre-existing** — it drops
the old generic Save card too, not just person_create.

Complicating a fix: the panel has a *second*, working action-card path
(`renderActionCards`/`renderCardActions`, triggered by the plural `action_cards` event) whose
action shape is `{type, label, data}` → `executeCardAction`, which **does not match** the
suggestion card's `{label, action, suggestion_id}` shape. So closing the gap is net-new panel
work (add an `action_card` case that renders the actions and POSTs to
`/api/proactive/suggestions/{id}/accept|dismiss`), not a one-line switch case — and it needs
live-panel verification (kiosk @192.168.1.61), not just a headless test. The confirm card is
functional on the **flue/voice** surface today; the **panel** surface is this gap.

## Cleanup

Test contacts accumulate in the live `people` table under synthetic `zoe-*` user_ids.
Soft-delete them (reversible) — a bulk prod `UPDATE people SET deleted=1 …` is a
classifier-gated action the operator runs outside auto mode; only ever match
synthetic test identities, never a real user.

## PR ledger (this workstream)

| PR | What |
|---|---|
| #1200 | people_create ensure-user FK fix (the "nothing happened" bug) |
| #1203 | people_create private-by-default (was leaking `visibility=family`) |
| #1205 | P1 — fold pending offers into `/api/memories/for-prompt` (flue surfacing) |
| #1208 | P2 — confidence gate on LLM person extraction (dark) |
| #1214 | P5 — deterministic regex propose-on-mention (belt-and-suspenders) |
| #1215 | store_suggestions ensure-user FK fix + swallowed-except → WARNING |
| #1216 | circle NOT NULL regression fix (`'circle'` is a valid tier) |
| #1222 | P4 — person_create confirm card (`ui_components_for_suggestions`); backend only, panel render is the known gap above |
