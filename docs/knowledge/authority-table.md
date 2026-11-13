---
type: Reference
title: The household authority table
description: What Zoe may do, for whom, without asking - one data file (action class x member role -> allow / ask_first / never) with code-enforced floors, a pure decision function, and a log-only check at the brain's tool dispatch. Flag ZOE_PROBLEM_LOOP = off | shadow | on (on = shadow), default off.
tags: [trust, authority, problem-solving, samantha, zoe-data]
timestamp: 2026-11-05T00:00:00Z
---

# The household authority table (`household_authority.py`, `authority_table.json`)

Samantha initiative roadmap item 17 / lane F E7 ([research](../research/samantha-initiative-2026-11-03/lane-F-personality-proactive.md), section 4).
Independent problem-solving on a household box is mostly POLICY around tools that already exist: what may be done without asking,
a budget, and a way to stop that is a card rather than a failure.

## The table

`services/zoe-data/authority_table.json` is the single data file. Roles: `owner` (an admin account that is positively an adult),
`adult`, `child` (any member not positively an adult - fail closed), `guest` (kiosk / guest sessions), `unrecognised_voice`
(`voice-guest`, anonymous). Each action class has a `risk` (`low` / `high` / `irreversible`) and one verdict per role.

| class | owner | adult | child | guest | unrecognised voice |
|---|---|---|---|---|---|
| read_public (weather, time, sums) | allow | allow | allow | allow | allow |
| read_private (own calendar, reminders, notes, people) | allow | allow | allow | never | never |
| household_list (items on family lists: fridge rule) | allow | allow | allow | allow | allow |
| panel_utility (timer, volume, music at the panel) | allow | allow | allow | allow | allow |
| personal_write (own reminder, note, alarm, event) | allow | allow | allow | never | never |
| home_control (lights, normal room use) | allow | allow | allow | never | never |
| memory_write_stated | allow | allow | ask_first (guardian) | never | never |
| memory_write_inferred | ask_first | ask_first | never | never | never |
| calendar_shared, home_security, settings, unknown | ask_first | ask_first | never | never | never |
| messaging, purchase, memory_forget, irreversible_other | ask_first | ask_first | never | never | never |
| self_modify | never | never | never | never | never |

The `intents` block maps every dispatchable intent (`routers/system.py` `_DISPATCHABLE_INTENTS` + the alarm intents) to a class
and, where a writer records one, the `write_receipts` kind that proves it landed. A test fails if the two sets drift.

**Floors live in code, not data** (`household_authority.validate`): a high/irreversible class is `never` for child, guest and
unrecognised voice; nobody is `allow` on an irreversible class; `self_modify` is never for everyone; guests and unrecognised
voices never read private data, make a personal write, drive the house or write memory; `unknown` allows nobody; the floor
classes keep a minimum risk; every intent has a baseline class in code and the data may re-map it only to a class at least as
strict for every role (an intent with no baseline is judged against `unknown`). A table that breaks a floor does not load, and every decision is then `never` (`table_invalid`).

`decide(class, role, confirmed=...)` is pure. `confirmed` (the member said yes to this action) turns `ask_first` into allow and
nothing else: **`never` cannot be overridden**.

Consistent with the standing rules: voice is a password (no private read without an account), household lists fridge rule
(items yes, the list itself no), auth flows QR-on-panel (connecting an account is `settings`: ask first, grown-ups only), guests
never get memory (affect included), a guest id may not drive the house (2026-10-18), forgotten means forever (forget is
irreversible), a child's profile needs a guardian.

## Wiring today: log-only

`POST /api/system/intent-dispatch` (every brain tool call) schedules `household_authority.shadow_check` fire-and-forget when
`ZOE_PROBLEM_LOOP` is `shadow` or `on`. It logs one line per call and changes nothing:

```
AUTHORITY_SHADOW intent=smart_home class=home_control role=guest verdict=never would=refuse
```

No user id and no slot values reach the log. Role lookup reads `auth_users.role` + `manner_block.is_adult` under a 0.5 s budget;
a failure judges the member a child. zoe-data logs to `~/.zoe-logs/`, not journald.

## The problem-solving loop: deleted, never wired

`problem_loop.py` (a 6-step / 90-second multi-step loop ending in an escalation card, a step done only on a receipt) was built
2026-11-05 but no turn ever called it, so `ZOE_PROBLEM_LOOP=shadow` live did nothing for it. It was deleted in the 2026-11-13 gap
audit (git history keeps it; the idea is pinned in `docs/IDEAS.md`). `ZOE_PROBLEM_LOOP=on` now behaves as `shadow`.

## Evidence plan

1. Owner sets `ZOE_PROBLEM_LOOP=shadow` and restarts zoe-data.
2. After two weeks: `grep AUTHORITY_SHADOW ~/.zoe-logs/*.log | grep -o 'class=[a-z_]* role=[a-z_]* .*would=[a-z]*' | sort | uniq -c`
   - any `would=refuse` or `would=ask` on a call that really ran is a case to review (a table row too tight, or a real gap).
3. Only then: enforce at dispatch (a separate, reviewed PR).

## Open

- Receipts from a brain tool call reach zoe-data as a separate HTTP request with no turn id, so they are unstamped and count for
  any concurrent turn of that member (the write-claim guard has the same residual; ledgered in `open-problems.md`).
- No writer records a receipt for lights, music, journal or memory writes.
