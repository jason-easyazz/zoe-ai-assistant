---
type: Reference
title: Synthetic users, proactive recipients and kiosk presence (2026-09-27)
description: Who the nightly memory passes and the proactive triggers treat as a real user — the is_synthetic_user rule and its allowlist flag, the household recipient rule that replaced "created a chat session in 7 days", guest-owned kiosk presence, the flag-dark brief-on-arrival, the nightly purge of probe chat sessions, and why the spoken morning brief was silent from 08-16.
tags: [memory, dreaming, proactive, morning-brief, brief-on-arrival, presence, test-data, zoe-data]
timestamp: 2026-09-27T12:00:00Z
---

# Synthetic users, proactive recipients and kiosk presence

Tracker rows: B3.2, B2.1 and "Speaks first" in [the program](../architecture/beat-the-bar-2026-program.md).

## Synthetic users are filtered at read time

`services/zoe-data/user_filters.py::is_synthetic_user` is true for guest sentinels (`guest`,
`anonymous`, `voice-guest`, `voice-daemon`, empty) and ids matching
`^(test|probe|demo|ci|e2e|bench)[-_]` (case-insensitive; the separator keeps "Christine" or
"testa" real). `ZOE_SYNTHETIC_USER_ALLOWLIST` (comma list, default empty) opts pattern ids
back in for a lab, never a guest sentinel. It is applied in the dreaming, weekly
consolidation, music-taste and portrait passes and in the morning, evening,
evolution-digest and emotional follow-up triggers, each logging one `<pass>: users kept=N skipped_synthetic=M` line
(counts only). The 03:00 memory digest is deliberately **not** filtered: its selection must
match its independent "was there input" probe, or a probe turn raises a false
`no_eligible_users_despite_input` alert. The purge below covers it instead.

The dead `svc.list_users()` call was removed rather than implemented. MemoryService never
had it, so the chat-owner fallback query was already what ran every night.

## Proactive recipients and kiosk presence

`proactive/recipients.py::proactive_recipients`:
- **Active users** are owners of a user turn in `chat_messages` in the last 7 days. The
  evening wind-down and the emotional follow-up use only these.
- **Household**, for the morning brief and the evolution digest, adds the users named by a
  panel's `default` binding. The evolution digest then keeps admins only
  (`recipients.admins_only`, `auth.is_admin_role`): its approve/defer links are
  `require_admin`-gated.
- Synthetic ids are removed from both.

It replaced "created a `chat_sessions` row in 7 days". Telegram turns reach `chat_messages`
through `/api/chat`, but they reuse one session per chat.

`proactive/presence.py::panel_presence_tier` returns a tier. The kiosk reclaims the panel
row as `guest` 300 s after the owner goes quiet, so from 08-16 the brief was refused
`panel=none` even with the panel on. It was not created at all from 09-11.

| tier | condition | what is spoken |
|---|---|---|
| `owner` | a fresh foreground row owned by the member (written by sign-in or PIN, kept fresh by their own turns) | the full brief |
| `bound_guest` | a fresh `guest`-owned row on a panel whose `default` binding names the member | only the trigger's `spoken_guest_safe` line (first name, no calendar, loops or memories); the full brief stays in push and `proactive_pending` |
| `absent` | anything else | nothing |

Face-ID and voice-ID claims have no timestamped server-side record yet. They are accepted
per turn in `routers/voice_tts.py` and kept only as an in-memory session binding that
carries across rollovers. So they do not raise a panel to `owner`; wiring them in is a
voice-path change and needs the replay gate.

## Brief on arrival (B2.1, flag-dark)

`proactive/arrival.py` speaks a missed 07:30 brief once, when its member turns up.
It needs `ZOE_PROACTIVE_BRIEF_ON_ARRIVAL=1` and the master `ZOE_PROACTIVE_SPOKEN=1`;
both default off, and with either off nothing is read or queued.

- **Trigger:** a foreground `POST /api/ui/panel/bind` or `/api/ui/state/sync` from the
  member's own session (`routers/ui_actions.py::_note_owner_presence`). Guests and
  device tokens never count. The check runs as a background task, at most every 30 s
  per member. No voice-path file is involved; face and voice claims will join once
  they have a server-side record (see above).
- **Speaks** the stored `proactive_pending` text of today's brief, through the same
  two lanes as the 07:30 brief, on the panel where the member was seen.
- **Only if all hold:** 07:00–11:00 local; not quiet hours; tier `owner` on that
  panel; not a synthetic or guest id; a brief exists today and was not opened in
  chat; the full text was not delivered and is not still queued (a delivered
  `bound_guest` teaser does not count as heard); no user turn from the member in
  the last 2 minutes.
- **Once per member per local day:** the `proactive_responses` claim row
  (migration `0030`, `UNIQUE (user_id, trigger_type, local_date)`) decides it, not
  process memory. A failed speak is not retried.
- **Log:** `PROACTIVE_SPOKEN trigger=morning_checkin_arrival user= panel= outcome=
  daemon_queue= tier=owner missed=absent|guest_teaser|expired`.
- **Response signal (for B2.2):** the slow loop sets `outcome` on each claim row once
  its window has passed. `accepted` means a user turn within
  `ZOE_PROACTIVE_ARRIVAL_RESPONSE_S` (default 120 s) of the daemon playing it;
  otherwise `ignored`, or `undelivered` if it never played. Each is logged as
  `PROACTIVE_RESPONSE`.

## Probe chat rows are purged nightly

The leaked rows come from probes that call the live `/api/chat` as a fixed identity and do
not delete their sessions:
- `test-route-probe`: perf and route probes such as the brain-flags `chat_check.py` (22
  sessions and 64 turns on 2026-09-27).
- `test-sec-b-<6 hex>`: the Flue parity security gate (2026-07-07).

`scripts/maintenance/purge_orphaned_test_data.py` hard-deletes the `chat_sessions` those
exact ids own, together with their messages and AG-UI runs. It first locks the candidate
sessions, so no turn can land mid-purge. It then writes a re-parsed, count-verified JSON
backup to `~/.zoe/backups/purge/<stamp>-chat.json`. Only then does it delete exactly the
backed-up rows, checking the deleted count per table; any mismatch rolls back. Old backups
are removed only by hand, with `--prune-backups` (see `scripts/AGENTS.md`). It keeps any session with
a turn whose metadata names another owner. The nightly `self-hosted-tests` run (00:30 AWST)
executes it. On 2026-09-27, 130 sessions and 343 messages matched. A new probe identity
must be added there as an exact, anchored id, or tear its sessions down with
`DELETE /api/chat/sessions/{id}`.

## Verify

- **07:30:** `grep -E "T07:3.*(morning_checkin|PROACTIVE_SPOKEN)" ~/.zoe-logs/zoe-data.app.log`.
  Expect `morning_checkin: users kept=N skipped_synthetic=M`, then `fired for N user(s)` with
  the bound member included.
  - Panel off: `panel=none outcome=absent`, which still proves the brief was created.
  - Panel on (kiosk idle as guest): `panel=<id> outcome=enqueued` and a new
    `voice_announcements` row.
- **No wait:** `POST /api/proactive/trigger-morning` as the bound member with the panel on.
- **Arrival (flag on):** `grep -E "morning_checkin_arrival|PROACTIVE_RESPONSE" ~/.zoe-logs/zoe-data.app.log`
  and `SELECT local_date, missed, outcome, responded FROM proactive_responses ORDER BY created_at DESC`.
- **Skip counts** (read-only, live DB, 2026-09-27):

  | pass | kept | skipped |
  |---|---|---|
  | dreaming / consolidation | 4 | 21 (10 after the purge) |
  | portrait | 5 | 22 |
  | music | 2 | 1 (a guest sentinel) |
  | morning / evolution | 1 | 1 |
  | evening | 0 | 1 |
