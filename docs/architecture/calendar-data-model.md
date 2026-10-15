---
type: Architecture / design
title: "The calendar data model: where synced (and local) calendar data lives (alembic 0052, 2026-10-14)"
status: BUILT (schema + API read/write of the new fields + .ics import/export). NO sync engine - that is the connectors work (zoe-abilities A2). Owner decisions in section 8; the defaults below ship until they are answered.
date: 2026-10-14
description: One storage model for every calendar a household member keeps in Zoe - local events, Home Assistant shared calendars, and personal CalDAV / Google / Microsoft / .ics calendars - aligned to RFC 5545 iCalendar and RFC 8984 JSCalendar, with field-by-field mappings for the Google Calendar API and Microsoft Graph. Adds a calendars table (per member, no credentials), description, IANA timezone + UTC instants, status, links, attendees with an optional people-graph link and its provenance, alarms, recurrence with overridden instances, source tracking for sync, busy-only visibility, tombstones, and the forget cascade. Additive only; every pre-0052 reader and client works unchanged.
evidence_labels: "[src] file read in this worktree (origin/main 8c144cc7e) | [lib] the adopted library's source via opensrc (pypi:icalendar@7.3.0) | [rfc] the standard | [docs] the provider's official API reference | [branch] an unmerged PR branch | [decision] a choice made here, listed for the owner"
---

# The calendar data model (alembic 0052)

Owner, 2026-10-14: "consider the fact that eventually when people sync their calendars with Zoe, all that
information will have to live somewhere." This is that somewhere. It is built now, before any sync engine, so
the touch event-detail sheet (title, time, date, who it is with, category, notes, location) and the connectors
(zoe-abilities A2, [branch] `docs/architecture/zoe-abilities.md` on PR #2029) both write into one shape.

## 1. What existed (0001) and what was missing

[src] `services/zoe-data/alembic/versions/0001_initial_schema.py` `events`: `title, start_date, start_time,
end_date, end_time` (TEXT), `duration`, `category`, `location`, `all_day`, `recurring` (free TEXT), `metadata`
(JSON TEXT), `visibility` (`family|personal`), `deleted`. Every reader filters
`(visibility = 'family' OR user_id = me) AND deleted = 0` ([src] `routers/calendar.py`, `mcp_server.py`,
`intent_router.py`, `skybridge_service.py`, `proactive/`).

Missing for sync and for the detail sheet: notes, a time zone, an instant, status, attendees, organizer,
links, reminders, real recurrence, any idea of *which calendar* or *which provider* a row came from, and a way to
show "busy" without showing what. The desktop calendar worked around part of it by stuffing `description`,
`attendees` and `reminders` into `metadata` ([src] `services/zoe-ui/dist/calendar.html` `saveEvent`).

## 2. The model

Standards first: the vocabulary is RFC 5545's, lower-cased (RFC 8984 JSCalendar uses the same values). Provider
shapes are mapped onto it by the connector, never stored raw.

### 2.1 `calendars` (new) - one row per calendar a member owns

| column | meaning |
|---|---|
| `id`, `user_id` | the calendar and its OWNER (a member). Per-member isolation keys on `user_id`. |
| `name`, `colour`, `description`, `timezone` | display; `timezone` is the calendar default (IANA) |
| `source` | `local` \| `ha` \| `caldav` \| `google` \| `microsoft` \| `ics` |
| `source_account_id` | the connected account (zoe-abilities `connected_accounts.id`, future); NOT a credential |
| `source_calendar_id` | the provider's id: CalDAV collection href, Google `calendarId`, Graph calendar `id`, HA `calendar.*` entity id, .ics URL hash |
| `default_visibility` | `family` \| `personal` \| `busy` - what an imported event gets when the provider says nothing |
| `writable` | Zoe may write back (Google `accessRole` owner/writer, Graph `canEdit`, CalDAV privileges) |
| `is_default` | the member's default calendar for new events |
| `household_shared` | an HA shared household calendar (abilities doc: HA for shared, in-process for personal) |
| `sync_enabled`, `sync_cursor`, `last_synced_at`, `last_sync_status` | the change cursor (CalDAV `sync-token`, Google `nextSyncToken`, Graph `@odata.deltaLink`) and health |
| `conflict_policy` | `remote_wins` (default) \| `local_wins` \| `ask` |
| `deleted`, `deleted_at` | soft delete |

**Never here:** OAuth tokens, refresh tokens, app passwords, cookies. They belong in the abilities vault
(`account_secrets`, envelope-encrypted, per member; [branch] zoe-abilities section 5). A sync cursor is not a
credential: a Graph deltaLink is a URL containing a `$deltatoken`, useless without the account's access token.

### 2.2 `events` (0001 columns unchanged + new nullable / defaulted columns)

| new column | meaning |
|---|---|
| `description` | the notes (RFC 5545 DESCRIPTION). Plain text. **Untrusted** when imported: never put in a prompt unfenced (abilities principle 2) |
| `timezone`, `end_timezone` | IANA zone of DTSTART / DTEND. NULL = **floating** (RFC 5545 3.3.5) - every pre-0052 row |
| `start_at`, `end_at` | derived UTC instants, fixed-width `YYYY-MM-DDTHH:MM:SSZ` TEXT (sorts as text; matches the schema's TEXT timestamps). NULL for floating and all-day. Never client-written |
| `status` | `confirmed` (default) \| `tentative` \| `cancelled` |
| `transparency` | `opaque` (default, counts as busy) \| `transparent` |
| `url` | the event's page (http/https only - surfaces render it as a link) |
| `conference_url` | the join link (RFC 7986 CONFERENCE; http/https/tel/sip/sips) |
| `calendar_id` | FK `calendars.id`; NULL = the member's implicit local calendar (every pre-0052 row) |
| `source` | as `calendars.source`, denormalised so readers can filter without a join; `local` default |
| `external_uid` | iCalendar UID (Google `iCalUID`, Graph `iCalUId`) - the cross-provider identity |
| `external_id` | the provider resource id (Google event `id`, Graph event `id`, CalDAV object href) |
| `etag`, `sequence`, `last_synced_at` | change detection: CalDAV ETag / Google `etag` / Graph `changeKey`; RFC SEQUENCE |
| `read_only` | 1 = externally owned; the API refuses update/delete with 409 |
| `rrule` | one RFC 5545 RRULE value (`FREQ=WEEKLY;BYDAY=TU;COUNT=10`) |
| `rdates`, `exdates` | JSON arrays of ISO values: `YYYY-MM-DD` (date), `...Z` (instant), or naive (floating) |
| `recurrence_id`, `series_id` | an overridden instance: its original start (same ISO forms) and the master's `events.id` |
| `deleted_at` | the tombstone time (soft delete already exists: `deleted = 1`) |

Indexes: `(user_id, start_at)`; `calendar_id`; `series_id`; and the sync identity, UNIQUE
`(calendar_id, external_uid, COALESCE(recurrence_id, ''))` where both are set - one row per calendar per UID per
instance, so a re-sync updates instead of duplicating.

The legacy `recurring` TEXT stays as-is (written by the voice path); `rrule` is the structured one. `category`
stays Zoe's single category (CATEGORIES' first value on import).

### 2.3 `event_attendees` (new) - attendees AND the organizer

`event_id` (FK, cascade), `user_id` (the event OWNER - whose forget reaches it), `display_name`, `email`,
`person_id` (FK `people.id`, `ON DELETE SET NULL`), `person_link_source` (`user` \| `email_match` \| `inferred`),
`role` (`chair` \| `req-participant` \| `opt-participant` \| `non-participant`), `partstat` (`needs-action` \|
`accepted` \| `declined` \| `tentative` \| `delegated`), `kind` (CUTYPE: `individual` \| `group` \| `resource` \|
`room` \| `unknown`), `is_organizer`, `rsvp`, `position`.

One row per person: an organizer who also attends is ONE row (`is_organizer = 1`, `role = chair`); an organizer
who only organises is `role = non-participant`. This is how Google lists the organizer inside `attendees[]`
with `organizer: true`, and it keeps "who is it with" a single list.

**Provenance (memory must be flawless):** `person_link_source` records how a contact link was made. The API
only ever writes `user` (the member picked the contact). A connector may later write `email_match` (the
attendee's email equals a `people.email`) and must never overwrite a `user` link; `inferred` (name match) is
reserved and is never auto-written in this PR. Import never links.

### 2.4 `event_alarms` (new)

`action` (`display` \| `audio` \| `email`), `offset_minutes` (minutes BEFORE the anchor, so RFC `TRIGGER:-PT30M`
= 30), `related` (`start` \| `end`), or an absolute `trigger_at`, `description`. **Stored only: nothing fires
them.** The API exposes them read-only; the touch sheet must not offer "remind me" on top of them until a firing
path exists (that is `reminder_service`'s job, not this table's).

## 3. Field mappings

Sources: [rfc] RFC 5545 section 3.6.1 / 3.8, RFC 7986 (CONFERENCE, COLOR), RFC 8984 section 4-5; [docs] Google
Calendar API v3 Events resource (developers.google.com/calendar/api/v3/reference/events) and CalendarList
(…/calendarList); Microsoft Graph v1.0 `event` resource (learn.microsoft.com/graph/api/resources/event) and
`calendar`; [lib] `icalendar` 7.3.0 `src/icalendar/cal/event.py`, `timezone/tzp.py` (Windows zone names),
`cal/calendar.py::add_missing_timezones`.

| Zoe | RFC 5545 | RFC 8984 JSCalendar | Google Calendar v3 | Microsoft Graph |
|---|---|---|---|---|
| `title` | SUMMARY | `title` | `summary` | `subject` |
| `description` | DESCRIPTION | `description` | `description` (may be HTML) | `body.content` (`contentType` html/text; convert html to text) |
| `location` | LOCATION | `locations{}.name` | `location` | `location.displayName` |
| `start_date` / `start_time` / `timezone` | DTSTART (+TZID; VALUE=DATE for all-day) | `start` (LocalDateTime) + `timeZone` (+`showWithoutTime`) | `start.dateTime` + `start.timeZone`, or `start.date` | `start.dateTime` + `start.timeZone` (often a **Windows** name: map via icalendar's `WINDOWS_TO_OLSON`, or send `Prefer: outlook.timezone="<IANA>"`); `isAllDay` |
| `end_*`, `end_timezone`, `duration` | DTEND / DURATION | `duration` (+ `locations` timeZone for a different end zone) | `end.*` | `end.*` |
| `start_at` / `end_at` | (derived) | (derived) | (derived; `dateTime` carries an offset) | (derived) |
| `all_day` | DTSTART;VALUE=DATE | `showWithoutTime: true` | `start.date` present | `isAllDay` |
| `status` | STATUS | `status` | `status` | `isCancelled` → `cancelled`, else `confirmed`; `showAs: tentative` → `tentative` |
| `transparency` | TRANSP | `freeBusyStatus` (`free`→transparent) | `transparency` | `showAs` (`free`→transparent, else opaque) |
| `visibility` | CLASS (PUBLIC/PRIVATE/CONFIDENTIAL) | `privacy` (public/private/secret) | `visibility` (default/public/private/confidential) | `sensitivity` (normal/personal/private/confidential) |
| `url` | URL | `links{}` rel `alternate` | `htmlLink` | `webLink` |
| `conference_url` | CONFERENCE (RFC 7986) | `virtualLocations{}.uri` | `hangoutLink` / `conferenceData.entryPoints[type=video].uri` | `onlineMeeting.joinUrl` |
| `category` | CATEGORIES (first) | `keywords` / `categories` | (none; `colorId` is colour) | `categories[0]` |
| `external_uid` | UID | `uid` | `iCalUID` | `iCalUId` |
| `external_id` | (CalDAV href) | (none) | `id` | `id` |
| `etag` | (CalDAV ETag) | (none) | `etag` | `changeKey` / `@odata.etag` |
| `sequence` | SEQUENCE | `sequence` | `sequence` | (none) |
| `rrule` | RRULE | `recurrenceRules` | `recurrence[]` "RRULE:" line | `recurrence` (PatternedRecurrence: **not** RRULE - the connector converts pattern+range to an RRULE) |
| `rdates` / `exdates` | RDATE / EXDATE | `recurrenceOverrides` (`{}` / `excluded: true`) | `recurrence[]` "RDATE:" / "EXDATE:" lines | (instances via `exception` / deleted occurrences) |
| `recurrence_id`, `series_id` | RECURRENCE-ID (+ same UID) | `recurrenceId` (+ `recurrenceIdTimeZone`) | `originalStartTime` + `recurringEventId` | `originalStart` + `seriesMasterId`, `type: exception` |
| attendee `display_name` / `email` | ATTENDEE CN / cal-address | `participants{}.name` / `sendTo.imip` | `attendees[].displayName` / `email` | `attendees[].emailAddress.name` / `address` |
| attendee `role` | ROLE | `roles` (chair/attendee/optional/informational) | `optional: true` → opt-participant | `type` required/optional/resource |
| attendee `partstat` | PARTSTAT | `participationStatus` | `responseStatus` (needsAction/accepted/declined/tentative) | `status.response` (none/notResponded→needs-action, tentativelyAccepted→tentative) |
| attendee `kind` | CUTYPE | `kind` (individual/group/location/resource) | `resource: true` → resource | `type: resource` → resource |
| attendee `is_organizer` | ORGANIZER | `roles.owner` | `organizer` / `attendees[].organizer` | `organizer` |
| attendee `rsvp` | RSVP | `expectReply` | (none) | `responseRequested` (event level) |
| alarm | VALARM (ACTION, TRIGGER, RELATED) | `alerts{}` (OffsetTrigger `offset`+`relativeTo`, AbsoluteTrigger `when`) | `reminders.overrides[]` (`method` email/popup, `minutes`) + `useDefault` | `isReminderOn` + `reminderMinutesBeforeStart` (one alarm) |
| `calendars.sync_cursor` | CalDAV `sync-token` (RFC 6578) | (JMAP state string) | `nextSyncToken` (410 Gone → full resync) | `@odata.deltaLink` |
| `calendars.colour` | COLOR (RFC 7986) | `color` | CalendarList `backgroundColor` | calendar `hexColor` |
| `calendars.writable` | CalDAV current-user-privilege-set | (JMAP `myRights`) | CalendarList `accessRole` owner/writer | calendar `canEdit` |

Home Assistant shared calendars: HA's `calendar.get_events` returns `summary, description, location, start,
end` only (no UID, attendees or recurrence). An `ha` calendar row therefore carries title/notes/place/time and
nothing else, keyed by `(calendar_id, start, summary)` until HA exposes a UID; it is `household_shared = 1`,
`default_visibility = family`, and Zoe treats HA as the owner of the data (`read_only = 1` unless the HA entity
supports `CREATE_EVENT`).

## 4. Visibility and per-member ownership

* Stored values keep the 0001 names every reader already filters on: `family` (household) and `personal`
  (private), plus the new `busy` (busy-only). The API accepts `household` / `private` / `busy_only` as aliases;
  anything else is a 422 (it used to be stored verbatim, which hid a misspelt value from everyone but the owner).
* `busy`: another member sees `id`, owner, the times and status as **"Busy"** - never title, notes, place,
  category, attendees, links or metadata (`calendar_service.busy_projection`). A category filter never reaches
  someone else's busy event. They cannot edit or delete it (404). The owner sees everything.
* Fail closed: every reader outside `routers/calendar.py` still filters `visibility = 'family' OR user_id = me`,
  so to them another member's `busy` event is simply absent - nothing leaks through a reader that does not know
  the new value.
* Ownership: `events.user_id` is the owner. An event a member did not create but is invited to (an attendee
  who is also a member) is NOT shared with them by this PR; that waits for the identity engine's directory
  (`person_directory`, [src] `docs/architecture/identity-presence-engine.md` section 5.5).
* An attendee link may point at a household (`family`) contact, or at the owner's own private contact when the
  owner writes it - never at another member's private contact (422), and never at a forgotten one.

## 5. Conventions (each one is a default the owner can overturn, section 8)

1. **Wall clock in the event's own zone.** `start_date` / `start_time` are DTSTART's wall clock in `timezone`
   (RFC semantics). A UTC-only instant (`...Z`, how airlines export) is stored in the household zone
   (`ZOE_TIMEZONE`), because it has no wall clock of its own and every pre-0052 reader assumes household time.
2. **Floating stays floating.** No `timezone` = the same wall clock anywhere; no instant is invented for it. The
   API derives `start_at` / `end_at` only when the client states a zone.
3. **All-day end is inclusive** in Zoe (how people say it); iCalendar DTEND is exclusive (+1 day on export,
   -1 on import).
4. **Cancelled is hidden by default** from `/api/calendar/events` (`include_cancelled=true` shows it);
   `/events/today` never shows it.
5. **Notes have one home.** The `description` column is authoritative; the desktop calendar's legacy
   `metadata.description` is promoted into it on write and mirrored back into `metadata` on read, so the old
   page and the new sheet see the same text.
6. **Who it's with, for old events.** An event with no attendee rows returns the desktop's `metadata.attendees`
   in the structured shape, marked `legacy: true` (not rows; a PUT of `attendees` makes them rows). Every event
   from a calendar also carries `calendar_name`. PUT stays a partial update: only the keys sent are written (`attendees` absent or null = unchanged, `[]` = cleared).

## 6. Forget, delete, tombstones

* **"Forget <person>"** (`memory_forget_cascade`, new step `calendar_attendees`, a DB step with the same retry
  and pending-marker contract as the others): on the member's OWN events, an attendee linked to the forgotten
  person or whose name / email names them loses the link, the name and the email (the row stays, anonymous, so
  the event keeps its shape); the desktop's legacy `metadata.attendees` entries are dropped the same way. The
  "keep the contact" undo restores the contact, not the attendee links. The step is scoped to the forgetting
  member: a household contact that member A forgets stays linked from member B's own attendee rows (B did not
  forget them; per-member forgetting is the memory layer's rule too).
* Not in this PR, owner decision 8.4: event TITLES and DESCRIPTIONS that name the person ("Lunch with Robin")
  are not redacted - today's cascade never touched `events` either.
* **Delete** stays a soft delete (`deleted = 1`) and now stamps `deleted_at`: the tombstone a sync engine needs
  to push a local delete upstream and to ignore a stale re-add.
* A synced (read-only) event's row is the provider's copy: the API refuses to edit or delete it (409).
* There is no hard erase of a member's `events` rows today (only soft delete). When one is written, the
  `event_attendees` / `event_alarms` rows cascade from `events` on their own; `calendars` must be deleted by it
  explicitly (its FK from `events.calendar_id` has no cascade, on purpose: disconnecting an account deletes that
  calendar's events first, it never orphans them into the local calendar).

## 7. Migration and back-compat

* Revision **0052**, `down_revision = 0051` (#2024's speaker `model_id`). One head; pinned by the head tests.
* Additive only: three new tables, 23 new `events` columns, all NULLable or defaulted (PostgreSQL 11+ adds a
  defaulted column without a rewrite). No existing value is rewritten; no backfill runs in the migration. Every
  statement is guarded (`IF NOT EXISTS` on PostgreSQL, a column probe on SQLite), so a rerun is a no-op and
  `alembic upgrade head --sql` still renders offline. The downgrade drops only what 0052 added.
* API: a pre-0052 payload behaves exactly as before; every response gains the new keys (`attendees` / `alarms`
  as lists). The voice / MCP writer (`create_event_record`) issues the identical 15-column INSERT unless a caller
  passes `extra=`.
* Not migrated: the desktop's `metadata.attendees` / `metadata.reminders`. They stay where they are (read by the
  desktop page); a one-off backfill into the child tables is decision 8.5.
* `.ics`: `calendar_ical.py` (parse / store / export) on the adopted `icalendar` 7.3.0 (BSD-2, pure Python;
  its deps `python-dateutil`, `tzdata`, `typing-extensions` are already on the box). It is imported by nothing on
  the request path, so the box does not need it until the connectors land. It is pinned in CI's `validate.yml`
  only (so the round-trip test runs) and NOT yet in `requirements.txt`: that manifest is a voice-gate path, and a
  declaration there would make this schema PR a replay-gated voice deploy for a package nothing loads. The
  connectors PR declares it (and installs it on the box first, per services/zoe-data/AGENTS.md).

### Before a sync engine writes a row (prerequisites, not done here)

1. The voice / MCP / skybridge / proactive readers must filter `status <> 'cancelled'` (today only the router does).
2. The MCP `calendar_update_event` / `calendar_delete_event` and the skybridge writers must refuse `read_only` rows,
   re-derive `start_at` / `end_at` when they move a time, and stamp `deleted_at` on delete (best: one shared
   update / delete in `calendar_service`)
   (today only the router does).
3. Inbound sync must consult the forget ledger (`memory_forgotten`) so a forgotten attendee name is not re-imported.
4. Inbound text (description, title, attendee names) stays fenced untrusted input for any brain call.
5. Never push a forget-driven attendee change upstream: it would uninvite a real person from someone else's meeting.
6. `calendar_ical.store_parsed` always INSERTs: a second import of the same file into the same calendar hits
   `uq_events_external_instance`. The connector needs an upsert on `(calendar_id, external_uid, recurrence_id)`
   that respects `sequence` / `etag`.
7. A PUT of `attendees` re-asserts every `person_id` it carries as `person_link_source = user` (the member
   confirmed the list); a connector-written `email_match` link survives only until the member edits the list.

## 8. Decisions for the owner

1. **Wall clock (5.1):** keep the event's own zone (default; a Sydney 9:00 flight reads "9:00"), or always
   store household wall clock (voice would say "6:00" for the same flight, in Perth)?
2. **Visibility names:** keep storing `family` / `personal` / `busy` (default, zero reader changes) or migrate to
   `household` / `private` / `busy` everywhere?
3. **Default visibility of a synced personal calendar:** `busy` (default proposed: the household sees you are
   busy, not why) or `personal`?
4. **Forget and event text:** should "forget Robin" also redact the name from event titles / notes the member
   owns? (Default: no - a calendar is a plan record and may be shared; the attendee link and name ARE removed.)
5. **Backfill** the desktop's `metadata.attendees` / `metadata.reminders` into the child tables (one audited
   script), or leave them until the desktop calendar is rewritten?
6. **Conflict policy default:** `remote_wins` (proposed: the provider is the owner of a synced event) or `ask`?
7. **Invited members:** when an attendee's email is another member's, should the event appear on that member's
   calendar (as an attendee copy) - and at what visibility?

## 9. Verification

`services/zoe-data/tests/test_calendar_data_model.py` (ci_safe, invented data): single head, upgrade
additive + rerun + downgrade on SQLite, PostgreSQL offline render; pre-0052 payloads; new-field round trip;
422 vocabulary; private-contact link refusal; busy-only and personal for another member; forget cascade;
read-only refusal; `.ics` import → store → export → re-import equality on `tests/fixtures/calendar_sample.ics`.
