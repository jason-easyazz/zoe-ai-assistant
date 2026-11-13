---
type: record
title: Connected accounts - the feed spine (Stage 0)
description: The tables, the account-secret seam, the external_source memory class and the forget reach that every external feed (calendar, contacts, mail) hangs off; built 2026-11-13 from the gap audit's lane D, nothing connects to a provider yet.
tags: [feeds, calendar, contacts, mail, connected-accounts, privacy, forget, memory-authority]
timestamp: 2026-11-13T00:00:00Z
---

# Connected accounts - the feed spine (Stage 0)

Source: gap audit 2026-11-13, lane D (data model for external feeds), findings X1, X2, X5, X8, X10. Design it follows:
[zoe-abilities.md](../architecture/zoe-abilities.md) section 5 (credentials, generation fence), section 4.2 (QR on the panel, finish on
the phone). Nothing in Stage 0 talks to a provider, adds a route, a flag or a dependency.

## Tables (alembic 0078)

| table | one row per | notes |
|---|---|---|
| `connected_accounts` | member x provider x external account | `subject_hash` = sha256 of the provider subject (never the subject itself); `label` is member-facing and personal; `status` pending / active / error / revoked; `last_error_class` from a fixed list (`auth network rate_limited quota provider config internal`), never a provider message; `account_generation` bumped on disconnect |
| `sync_runs` | sync attempt | outcome running / ok / partial / error / fenced, counts seen / created / updated / deleted, opaque `cursor_before` / `cursor_after`, `backoff_until`, the generation it started under |
| `member_identities` | member's own email or phone | normalised `value_norm`, unique per (kind, value): one value is one member's; `verified`, `source` member / admin / account |

- Times are TEXT ISO-8601 UTC (`...Z`), the convention of 0052 / 0053 and 0071-0077.
- `calendars.source_account_id` (0052) and `contact_sources.source_account_id` (0053) are a documented SOFT reference to
  `connected_accounts.id` (indexed on calendars): no FK, because the contacts column is `NOT NULL DEFAULT ''` and removal is the
  disconnect / forget routine's job.
- No credential column exists in any of them (a test pins it).

## The account-secret seam (`account_secrets.py`)

`put` / `get` / `remove` / `forget_user` by account id, bound to (member, purpose `oauth_refresh` | `app_password`). The only backend
shipped is `RefusingBackend`: `put` and `get` raise `SecretsUnavailable` (fail closed), `remove` / `forget_user` return 0 (nothing
was ever stored). A secret never reaches the database, a log line, an exception message or a prompt.

**Owner decision needed before Stage 1 can store a token:** where the key-encryption key lives (a 0600 key file under `~/.zoe/keys/`, a
systemd encrypted credential, or the `zoe-vaultd` broker per credential-vault decision O9), and whether the key file is backed up
(losing it means every member reconnects).

## Generation fence and sync log (`connected_accounts.py`)

- `disconnect` bumps `account_generation`, marks the row `revoked`, deletes its secrets. It takes the account row lock
  (`SELECT ... FOR UPDATE` on PostgreSQL) inside its transaction.
- `start_run` records the generation; `finish_run` records counts and the cursor only if the account is still at that generation.
  The check and the write are one step: `still_current` locks the row for the rest of the transaction, and the account UPDATE is
  itself conditional (`WHERE id = ? AND account_generation = ? AND status <> 'revoked'`); zero rows = the run is `fenced` and
  nothing about the account changes. A sync never outlives a disconnect.
- Reconnecting (`create` on an existing member + provider + subject) returns the same id. A `revoked` account starts over as a fresh
  link does: `pending` (active after its first good sync), under a NEW generation, so a run from the old connection stays fenced.
- `upsert_by_external_id(db, table, match=, values=, version_col=)` is the idempotent feed write (`created` / `updated` /
  `unchanged`), on the caller's connection so it sits inside the caller's transaction and fence.
- Every member write snapshots `user_erase_gate.generation` when scheduled and writes inside `erase_gate.writing`: a write scheduled
  before "forget me" is dropped.

## Memory authority: `external_source`

A fact a provider gave (contact card, calendar, mail) is class `external_source`, rank 1, authority `inferred`
([memory-authority.md](memory-authority.md)). It never supersedes a `user_stated` / `user_confirmed` row and is superseded by them; it
becomes `user_confirmed` only when the member accepts it. Writer name `external_source`; the row must carry
`memory_authority.external_source_ref(kind, account_id, external_id)` (kind `calendar_event` | `contact_card` | `mail_item`): a pointer,
never words. The pointer is REQUIRED at every write path: `provenance()` raises without it, and `MemoryService.ingest(...,
source_ref=)` / `review(edit, source_ref=)` refuse (`MemoryServiceError`) before writing anything; a blocked provider edit's disputed
candidate keeps the class and the pointer; caller metadata cannot plant one. An edit by a provider must RE-SUPPLY the pointer of the
item the new words came from (the edited row's pointer is never carried forward: only the writer knows which item said the new words);
a member's own edit of a provider row is theirs and carries none, while the retired provider row keeps its pointer. No feed writes such
rows yet.

## Forget reach

`MemoryService.delete_user` (fail closed) runs `connected_accounts.forget_user` (secrets, sync runs, accounts),
`member_identities.forget_user` and `calendar_forget.forget_user` (the member's calendars, their OWN events in them - another member's event there is detached and kept - every synced event of the
member, their attendees and alarms). Registered in [forget-reach.md](forget-reach.md). Still open (owner decision): the member's
calendar-less local events, and their reminders, lists, notes, journal and transactions, which default to household visibility.

## What Stage 1 (read-only calendar) builds on this

Connect via `auth_handoff` (QR on the panel, finish on the phone) into `connected_accounts` + the chosen secret backend; one sync worker
per account using `start_run` / `finish_run`; `calendar_ical.store_parsed` and the connector moved onto `upsert_by_external_id` keyed by
`(calendar_id, external_uid, recurrence_id)` with `etag` / `sequence`; the shared calendar read path with recurrence expansion (audit X3,
X4); disconnect deleting the account's calendars and events after the fence.
