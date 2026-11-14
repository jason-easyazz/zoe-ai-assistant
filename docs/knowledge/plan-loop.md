---
type: Reference
title: The bounded plan-act-verify loop
description: How a request with several parts is run as one frozen plan of at most 6 tool steps in 90 seconds - every step judged by the household authority table and the safety floors BEFORE anything runs, a step done only when a receipt proves it, an uncertain write never retried and never claimed. Flag ZOE_PLAN_LOOP = off | shadow | on, default off. Not wired to any turn.
tags: [trust, plan-loop, tools, authority, receipts, samantha, zoe-data]
timestamp: 2026-11-14T00:00:00Z
---

# The bounded plan-act-verify loop (`plan_loop.py`)

Zoe handles one tool call per turn well. A request with two or three parts ("add floss to the shopping list, set a reminder
for the dentist on Friday, then tell me what else is on Friday") often gets one part done, or a part claimed that never
happened. This is the piece that runs such a request as a plan, proves each step, and tells the truth about what happened.

It replaces the `problem_loop.py` that was built 2026-11-05, never wired, and deleted in the 2026-11-13 gap audit. What is
different this time: the plan is frozen before any tool runs (no model in the loop between steps), the whole plan is refused
if one step is not allowed, an uncertain write is its own outcome, and the reply is rendered from receipts by code.

## True when done (the owner's words)

1. "I ask for two or three things in one breath and she does all of them, or tells me exactly which ones she did not do."
2. "She never says she did something she did not do. Not once." (the number: false claims = 0)
3. "If she is not sure a thing went through, she says so and does not try it again behind my back."
4. "A child, a guest or a voice she does not know cannot get a grown-up action done by hiding it inside a longer request."
5. "Nobody at the panel can delete a household list, however they ask."
6. "Something written on a list or in a calendar title cannot give her orders. She does what the person speaking asked."
7. "It never takes more than six steps or a minute and a half, and it never loops."
8. "A plain one-thing request works exactly as it does today, at the same speed."
9. "A medical or distress turn is answered by the safety floor first, always. The plan never runs on one."
10. "It is built and proven in the lab before it is switched on, and switching it on is my call."

## The core (`services/zoe-data/plan_loop.py`)

Input: an ordered plan of `Step(tool, args, part, needs)` and a `Speaker(user_id, role, verified, at_panel, confirmed)`.
`tool` must be an intent the authority table names (the same registry `POST /api/system/intent-dispatch` allows); `part` is
the piece of the speaker's own words the step supports. Output: `PlanResult(status, outcomes, refusal, elapsed_s)`.

**Gate first, all or nothing** (`judge`, pure, runs no tool). In order: the safety floors on the speaker's words
(`distress_handoff.detect`, `medical_safety.detect`; a floor that cannot be asked refuses), a valid table, the step bound, then
per step: argument shape, no self-widening, the fridge rule, a known tool, no repeat, and `household_authority.decide` for the
speaker's effective role (an unverified member and an unknown role are judged as an unrecognised voice). One refusal refuses
the whole plan; nothing has run. The closed list of reasons:

| reason | when |
|---|---|
| `safety_floor` | the speaker's words belong to the distress or medical floor |
| `table_invalid` | the authority table failed to load (every decision is then never) |
| `empty_plan` / `too_many_steps` | no steps, or more than 6 |
| `bad_arguments` | arguments are not a flat map of plain values, or `needs` points at itself or a later step |
| `widens_permissions` | an argument names who is acting, a role, a confirmation or a visibility |
| `fridge_rule` | a list would be deleted, emptied or renamed at the panel |
| `unknown_tool` | the tool is not in the registry |
| `repeated_step` | the same tool with the same arguments twice |
| `not_allowed` | the table says `never` for this role |
| `needs_confirmation` | the table says `ask_first` and the speaker has not said yes to that tool |

**Bounds.** 6 steps, 90 s wall clock, 20 s per step. A caller may pass smaller numbers, never larger. Each step is sent
exactly once. A step whose time has run out before it starts is `skipped` (`time_budget`); a step that `needs` a step that is
not done is `skipped` (`dependency`). One failed part does not stop the independent parts after it.

**Proof.** Each step runs in its own `write_receipts` turn. Outcomes and their codes (closed lists, `plan_loop.CODES`):

| status | code | meaning |
|---|---|---|
| `done` | `receipt` | a receipt of the tool's kind, stamped with this step's turn id, exists (the row id is the proof) |
| `done` | `read_ok` | a read returned text |
| `failed` | `tool_said_no`, `tool_refused` | the tool answered no, or raised a validation verdict (4xx / `StepRejected`) |
| `failed` | `read_timeout`, `read_error` | a read did not come back (nothing can have changed) |
| `uncertain` | `timeout`, `error_after_send` | a write may have been sent and no receipt exists |
| `uncertain` | `no_receipt` | the tool said ok and left no receipt |
| `uncertain` | `unprovable` | the tool has no receipt kind at all (journal, music, people) |
| `skipped` | `time_budget`, `dependency` | never sent |

A receipt wins over the call's own fate: a write whose row landed and whose call then timed out is `done`. An unstamped
receipt (another request's write for the same member) never proves a step. A proven write is re-recorded in the caller's turn
with source `plan_loop`, which is how the write-claim guard sees exactly the proven writes.

The table gained three receipt kinds the writers already record: `list_remove` -> `list_removed`, `memory_store` -> `memory`,
`memory_retire` -> `forget`.

## The flag

`ZOE_PLAN_LOOP` = `off` (default: `run` returns before touching anything) | `shadow` (judge the plan, log codes and counts,
run nothing) | `on`. One log line per plan, no user id, no words, no argument values:

```
PLAN_LOOP mode=shadow status=refused steps=2 done=0 failed=0 uncertain=0 skipped=0 refusal=not_allowed tool=memory_retire class=memory_forget role=child ms=0
```

## What the registry cannot do yet (found while building)

There is no tool that MOVES or CANCELS a reminder or a calendar entry: the registry has `reminder_create`, `reminder_list`,
`calendar_create`, `calendar_show` only. "Move my dentist reminder to Friday" therefore has no honest plan today. The loop
reports that part as one it has no way to do; it never makes a second reminder and calls it a move.

## Open

- Not wired. No turn calls the loop; `ZOE_PLAN_LOOP=on` changes nothing until a reviewed wiring PR.
- A tool whose writer records a receipt for a row that did not commit would be believed. Receipts are recorded after the
  commit today (`write_receipts` callers); the loop inherits that trust.
- English only where words are involved; the core itself has no words.
