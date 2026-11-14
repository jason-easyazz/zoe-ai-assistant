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

The member's memory opt-out (`user_prefs.MEMORY_OPT_OUT_SOURCES`) holds `external_source` exactly like every other automatic writer,
at the same three walls - `ingest`, `review(edit)` and the candidate write - matched on the lane `source` OR the real writer `origin`:
an opted-out member gets nothing stored from any feed (no row, no candidate, no pointer).

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

## The calendar feed fetcher contract (`calendar_feed.py`; the real fetcher is a lab module, unwired)

The engine never holds a subscription address; an injected fetcher hands it bytes. It enforces what it can see itself:

- a hard, engine-owned bound on every fetch (`FETCH_TIMEOUT_S`, 45 s). An async fetcher is cancelled at it. A sync fetcher runs
  in the module's own pool of `MAX_FETCH_THREADS` (2) threads; at the bound the run is recorded failed (class `network`, report
  detail `fetch_timeout`). A Python thread cannot be killed, so the stuck call keeps its worker until it returns: a feed whose fetch
  is still outstanding is refused (`fetch_outstanding`, the fetcher is not called), and at most two stuck calls can exist (more
  queue and are cancelled at their own bound);
- the size cap on the result: a body over `ZOE_CALENDAR_FEED_MAX_BYTES` is refused (`quota` / `too_large`) whatever status came
  with it, before anything is parsed or written;
- a fetcher's exception is logged by its type name only; its text reaches no log, row or report (a test plants a token in the
  address and in the exception and dumps every table, every log record and every report).

The real fetcher (it belongs to the credential broker; built in the lab, see below) MUST: stream the body and stop at `FetchRequest.max_bytes`;
use connect / read timeouts shorter than the engine's bound; follow at most 3 redirects and re-check every hop; refuse loopback,
link-local, private, multicast, reserved and cloud-metadata addresses AFTER DNS resolution and connect to the address it checked
(`agent_safety.assert_public_url` plus a pinned address), answering `blocked`; and never put the address, host or a token in an
exception, a log line or a result field.

### What exists and what does not (2026-11-14)

- **Exists, UNWIRED:** [`labs/calendar-fetcher/calendar_fetcher.py`](../../labs/calendar-fetcher/calendar_fetcher.py), a
  standard-library fetcher that implements the contract above, plus its tests (no real network: a fake resolver and an
  in-process socket pair) and a loopback lab (`lab_happy_path.py --lab`) that drives the engine's `sync_account` on a scratch
  store. It resolves once per hop, refuses every non-public answer (IPv4 and IPv6, v4-mapped / NAT64 / 6to4 judged as the
  IPv4 they carry, numeric host spellings and internal names refused before any lookup), connects to the checked address with
  the host in `Host` and SNI, follows at most 3 redirects with every check re-run and never https -> http, streams to
  `max_bytes`, bounds every phase inside 30 s, sends and returns validators, and answers with the engine's status list
  (pinned by a test). It does not use `agent_safety` (the broker must not import zoe-data) and it is not httpx (no public
  IP-pinning hook, and it reads proxy settings from the environment). `http://` exists only as a constructor argument for the
  lab, to loopback only.
- **Does not exist:** anything that calls it from zoe-data, any stored link, a broker verb that runs it, the unit it runs in,
  the scheduler tick, and the connect flow below. `account_secrets` still refuses every `put`.
- **Where it runs (the broker integration, after P0):** not in the `zoe-vaultd` core, whose sandbox allows loopback only
  ([credential-vault.md](../architecture/credential-vault.md) 5.1). The same split as the login browser: a small egress unit
  of its own (public addresses only, `IPAddressDeny=` over loopback, private and link-local ranges as a second wall) that the
  core hands ONE link per call, and that returns the result's fields only. zoe-data asks by account id and receives a
  `FetchResult`; it never sees the link, the host or the fetcher's reason word.

## Connecting a calendar subscription (design only; nothing built)

The general flow is [app-connection-handoff.md](app-connection-handoff.md) (QR on the panel, finish on the phone, VISION
principle 8). What is particular to a subscription link, because the link is the credential:

1. The member says "connect my calendar" or taps Settings > Connected accounts. The panel shows a QR (a new handoff kind); a
   linked member may get the Telegram "send to my phone" link instead. Nothing is typed on the panel.
2. The phone page shows the consent text, short "where to find the secret address" help per provider, ONE paste box and a
   visibility choice (default: only me).
3. **The pasted link goes to the broker's phone unit, not to zoe-data** (nginx routes that path straight to the broker's socket,
   as the vault's approve page does). The broker checks it, makes one test fetch, stores it, and tells zoe-data only "account
   created" plus the counts. zoe-data never receives the link, even once.
4. The panel card follows the handoff live and says "connected" with the count of things in the next two weeks; a refusal
   is said in plain words from the fixed error code (for example "That address is on a home network; I can't use those").
