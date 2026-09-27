---
type: Reference
title: Synthetic users, proactive recipients and kiosk presence (2026-09-27)
description: Who the nightly memory passes and the proactive triggers treat as a real user — the is_synthetic_user rule and its allowlist flag, the household recipient rule that replaced "created a chat session in 7 days", guest-owned kiosk presence, the nightly purge of probe chat sessions, and why the spoken morning brief was silent from 08-16.
tags: [memory, dreaming, proactive, morning-brief, presence, test-data, zoe-data]
timestamp: 2026-09-27T12:00:00Z
---

# Synthetic users, proactive recipients and kiosk presence

Tracker rows: B3.2 and "Speaks first" in [the program](../architecture/beat-the-bar-2026-program.md).

## Synthetic users are filtered at read time

`services/zoe-data/user_filters.py::is_synthetic_user` is true for guest sentinels (`guest`,
`anonymous`, `voice-guest`, `voice-daemon`, empty) and ids matching
`^(test|probe|demo|ci|e2e|bench)[-_]` (case-insensitive; the separator keeps "Christine" or
"testa" real). `ZOE_SYNTHETIC_USER_ALLOWLIST` (comma list, default empty) opts pattern ids
back in for a lab, never a guest sentinel. It is applied in the dreaming, weekly
consolidation, music-taste and portrait passes and in the morning, evening and
evolution-digest triggers, each logging one `<pass>: users kept=N skipped_synthetic=M` line
(counts only). The 03:00 memory digest is deliberately **not** filtered: its selection must
match its independent "was there input" probe, or a probe turn raises a false
`no_eligible_users_despite_input` alert. The purge below covers it instead.

The dead `svc.list_users()` call was removed rather than implemented. MemoryService never
had it, so the chat-owner fallback query was already what ran every night.

## Proactive recipients and kiosk presence

`proactive/recipients.py::proactive_recipients`:
- **Active users** are owners of a user turn in `chat_messages` in the last 7 days. The
  evening wind-down uses only these.
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
- **Skip counts** (read-only, live DB, 2026-09-27):

  | pass | kept | skipped |
  |---|---|---|
  | dreaming / consolidation | 4 | 21 (10 after the purge) |
  | portrait | 5 | 22 |
  | music | 2 | 1 (a guest sentinel) |
  | morning / evolution | 1 | 1 |
  | evening | 0 | 1 |
