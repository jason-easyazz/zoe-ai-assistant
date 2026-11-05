---
type: Reference
title: The household authority table and the bounded problem-solving loop
description: What Zoe may do, for whom, without asking - one data file (action class x member role -> allow / ask_first / never) with code-enforced floors, a pure decision function, a log-only check at the brain's tool dispatch, and a 6-step / 90-second loop that ends in an escalation card and counts a step done only on a receipt. Flag ZOE_PROBLEM_LOOP = off | shadow | on, default off.
tags: [trust, authority, problem-solving, samantha, zoe-data]
timestamp: 2026-11-05T00:00:00Z
---

# The household authority table (`household_authority.py`, `authority_table.json`, `problem_loop.py`)

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
unrecognised voice; nobody is `allow` on an irreversible class; `self_modify` is never for everyone; guests never read private
data or write memory; `unknown` allows nobody; the floor classes keep a minimum risk; the memory, settings and home intents keep
their class. A table that breaks a floor does not load, and every decision is then `never` (`table_invalid`).

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

## The loop (`problem_loop.run`, `ZOE_PROBLEM_LOOP=on` only)

A library, not yet called by any live turn. The caller supplies `plan_next(history)` and `execute(step)`. Per step: authority
decision first (`ask_first` unconfirmed or `never` = stop before running); each step runs in its own receipt turn; success is
code-checked: a write counts as done only when `write_receipts` holds a receipt of its kind for this member from this step's
turn, a read when its result is ok; a write with no receipt kind (lights, music, memory) is `unverified`, never done. Stops with
an `EscalationCard` (`"Here's what I tried: ... Here's what I need from you: ..."`, addressed to the member, a guardian for a
child, or the owner for self-modify) on: 6 steps, 90 seconds (also enforced per call), the same tool with the same arguments
twice, an unverified write, a planner failure.

## Evidence plan

1. Owner sets `ZOE_PROBLEM_LOOP=shadow` and restarts zoe-data.
2. After two weeks: `grep AUTHORITY_SHADOW ~/.zoe-logs/*.log | grep -o 'class=[a-z_]* role=[a-z_]* .*would=[a-z]*' | sort | uniq -c`
   - any `would=refuse` or `would=ask` on a call that really ran is a case to review (a table row too tight, or a real gap).
3. Only then: enforce at dispatch (a separate, reviewed PR) and wire the loop behind a turn.

## Open

- Receipts from a brain tool call reach zoe-data as a separate HTTP request with no turn id, so they are unstamped and count for
  any concurrent turn of that member (the write-claim guard has the same residual; ledgered in `open-problems.md`).
- No writer records a receipt for lights, music, journal or memory writes; the loop reports those as unverified until one does.
