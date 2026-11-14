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

## Stage 1 core: the calendar subscription engine (`calendar_feed.py`, alembic 0079)

Built 2026-11-14, flag `ZOE_CALENDAR_FEED` = off (default) | shadow | on. Lab proof: `labs/calendar-feed/` (README holds the counts).

**It stores no link and no secret.** A subscription address is a bearer credential, and Zoe stores none until the credential vault
lands (owner decision). The engine is handed the feed BYTES by an injected fetcher: `sync_account(account_id, fetcher)` calls
`fetcher(FetchRequest(account_id, etag, last_modified, max_bytes))` and gets a `FetchResult` (status class `ok` / `not_modified` /
`unauthorized` / `not_found` / `rate_limited` / `too_large` / `network` / `server_error` / `blocked`, the bytes, new validators). The
fetcher will be a separate broker process; `account_secrets` still refuses every put and get, no purpose was added, no URL column
exists (0079's own test pins that), and `calendars.source_calendar_id` is the constant `feed`, never the address or its hash.

| step | what happens |
|---|---|
| fetch | conditional (ETag / Last-Modified travel back to the fetcher); 304, or a body whose sha256 equals the last good one = everything unchanged, no parse; over `ZOE_CALENDAR_FEED_MAX_BYTES` (10 MB) = `quota` / `too_large`; an HTML or non-VCALENDAR body = `provider` / `not_calendar`; 401/403 = `auth` / `revoked_link`, 404 = `auth` / `http_404`, SSRF refusal = `config` / `ssrf_blocked`; backoff 30 min doubling to 6 h, never sooner than a 429's Retry-After (`sync_runs.backoff_until`, `calendars.consecutive_failures`) |
| parse | `calendar_ical.parse_ics` in `proactive.scheduler.run_blocking`; masters and RECURRENCE-ID overrides stay stored rows; kept = overlaps [today - 7 d, today + 400 d] (a master by any occurrence, an override also by its original date); a rule `calendar_read` cannot read is kept and counted `rrule_unsupported` |
| reconcile | one transaction per batch of 200, each inside `erase_gate.writing` and `still_current` (false = stop, that batch unwritten, the run `fenced` or dropped); `upsert_by_external_id(version_col="content_hash")` keyed (calendar, UID, RECURRENCE-ID); `content_hash` = sha256 of the normalised event + attendees + alarms (DTSTAMP is never read); attendees / alarms replaced only on created / updated, `event_attendees.member_user_id` set from `member_identities`; overrides linked to their master (`series_id`); no UID = skipped; a duplicate key keeps the highest SEQUENCE; rows are `source='subscription'` (read-only provider source: "That one comes from a subscribed calendar; change it there."), `read_only=1`, `account_generation`, `last_synced_at` |
| visibility | the MORE PRIVATE of the calendar's `default_visibility` and the event's CLASS (`parse_ics` maps CLASS:PUBLIC to `family`): a provider never widens a private calendar; a calendar the engine creates is `personal` and not writable |
| remote delete | a stored key absent from a complete fetch is hard-deleted with its attendees, alarms and anticipation offers; one that left the window is counted `pruned`. MASS-DELETE GUARD (`mass_delete_suspect`): 0 events while rows exist, or > 50 % and > 20 rows to delete, deletes nothing, records `partial` / `provider` / `suspect_empty`, and only a second consecutive IDENTICAL body confirms it |
| forget shield | `forget_feed_event(user, event_id)` writes `memory_forgotten.add_feed_item` (scope `feed_item`, HMAC of `feed|<uid>|<recurrence id>`, no near probes, permanent) BEFORE it deletes; forgetting a master shields its whole series; the key survives disconnect and reconnect. Every batch re-reads the shield (strict) under the erase-gate lock and counts hits `suppressed`. FAIL CLOSED: an unconfigured ledger refuses the sync (`config` / `ledger_unavailable`) and the forget (`FeedForgetRefused`, nothing deleted), and an unreadable one refuses the sync too |
| shadow | fetch, parse and count against the stored rows; only `sync_runs` is written |
| disconnect | `disconnect_feed(account_id)`: `connected_accounts.disconnect` (the fence) first, then the account's calendars, events, attendees, alarms and offers. Forget-me reaches the same rows (`calendar_forget.forget_user` now also withdraws the anticipation offers on the erased events) |

0079 also adds `sync_runs` `n_unchanged` / `n_skipped` / `n_pruned` / `n_suppressed` / `n_rrule_unsupported` / `bytes` / `duration_ms` /
`error_code`, `connected_accounts.last_error_code` (both CHECKed against `connected_accounts.ERROR_CODES`: `http_404 too_large
not_calendar ssrf_blocked parse_failed revoked_link suspect_empty ledger_unavailable`), `calendars.last_success_at` and the
`(calendar_id, account_generation)` index. `finish_run` writes the 0079 fields only when given. Logs carry the account id, counts,
outcome, class and code only (a caplog sweep plants titles, places, people and a URL and finds none).

**Not built yet:** the scheduler tick (`is_due` is ready; night-window aware, one account at a time), the connect flow (handoff kind,
phone page, status route), the broker that holds the address and fetches it, `icalendar` in `requirements.txt` (voice-gated), and moving
anticipation onto `events_in_window` (a sibling PR). Before the flag goes on: the vault and broker, a confirmed `ZOE_FORGET_LEDGER_SALT`,
a shadow week, and the QA round in the design note.

## The calendar feed fetcher contract (`calendar_feed.py`; the real fetcher is not built yet)

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

The real fetcher (it belongs to the credential broker, a later change) MUST: stream the body and stop at `FetchRequest.max_bytes`;
use connect / read timeouts shorter than the engine's bound; follow at most 3 redirects and re-check every hop; refuse loopback,
link-local, private, multicast, reserved and cloud-metadata addresses AFTER DNS resolution and connect to the address it checked
(`agent_safety.assert_public_url` plus a pinned address), answering `blocked`; and never put the address, host or a token in an
exception, a log line or a result field.
