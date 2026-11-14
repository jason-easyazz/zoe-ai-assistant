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
(`distress_handoff.detect`, `medical_safety.detect`; a floor that cannot be asked refuses) and the child safety floor (`child_safety_floor.check`, asked read-only: a hazard request passes only for a speaker who is positively an adult and has not said this session that they are a child), a valid table, the step bound, then
per step: argument shape, no self-widening, the fridge rule, a known tool, no repeat, and `household_authority.decide` for the
speaker's effective role (an unverified member and an unknown role are judged as an unrecognised voice). One refusal refuses
the whole plan; nothing has run. The closed list of reasons:

| reason | when |
|---|---|
| `safety_floor` | the speaker's words belong to the distress or medical floor, or the child safety floor would answer this speaker |
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

## The planner seam (`services/zoe-data/plan_loop_planner.py`)

`maybe_run(utterance, speaker, started=...)` is the one entry a turn would call. It returns the reply for a multi-part ask, or
`None`, which means "not mine, carry on exactly as today". In order:

1. `ZOE_PLAN_LOOP` off: `None`, before anything else.
2. `looks_multi_part` (deterministic, no model): two DIFFERENT action words and something joining them. "Add milk and eggs to
   the list" is one action. A turn that fails this returns `None` at once, so a single-tool turn never reaches the planner.
3. A turn the distress / medical floor owns returns `None`: it is never planned.
4. The planner is asked ONCE, with the speaker's words and the tool list and nothing else: no history, no memory, no list
   contents, no calendar titles, no tool output. The real call (`brain_planner`) is one JSON-schema-constrained request to the
   Gemma brain inside `brain_scheduler.declare("turn:plan_loop")`, 8 s at most; a failure or timeout returns `None`.
5. `parse_plan` validates the JSON in code. A step is DROPPED, never repaired and never guessed, when:

   | code | when |
   |---|---|
   | `not_json` | the reply is not the schema at all (no plan) |
   | `bad_shape` | a step is not `{tool, args, part}` |
   | `unknown_tool` | the tool is not one of the 16 plannable registry tools (`TOOL_ARGS`) |
   | `bad_args` | an argument is outside the tool's schema, missing, or the wrong type |
   | `ungrounded` | a content word of the step's `part` or of a text argument is not a word the speaker said |
   | `needs_dropped` | the step needs a step that was dropped |

   A dropped step whose words ARE the speaker's is reported back as a part there is no way to do yet. An ungrounded step is
   not reported at all: it was not the person's ask. Steps with a permission-shaped argument or a list deletion are passed on
   so the gate refuses the WHOLE plan (stronger than dropping one step).
6. A plan with fewer than two parts left returns `None` (a single-tool turn).
7. `shadow`: the gate judges and logs, nothing runs, `None`. `on`: the plan runs and the reply is rendered and guarded.

Once a tool may have run, the turn belongs to the loop: a failure after that point returns a fixed "something went wrong part
way through, please check" line and never `None`, because handing the turn back to the brain could do the writes again.

**Why text in a list item or a calendar title cannot give orders.** Three independent reasons: the planner never sees tool
output (the plan is frozen before the first tool runs and there is no second planner call); every step's words must be the
speaker's own (`ungrounded`); and what a read returns is only ever spoken, never parsed.

`intent_executor` is the default executor: the same in-process fulfilment `POST /api/system/intent-dispatch` uses
(`write_intent_gate.check`, then `intent_router.execute_intent`), so receipts are written in the step's own context.

Shadow log, one line per multi-part turn, codes and counts only:

```
PLAN_LOOP_PLAN mode=shadow outcome=planned steps=3 unplanned=0 dropped=ungrounded tools=list_add,reminder_create,calendar_show ms=412
```

## The truthful reply (`services/zoe-data/plan_loop_reply.py`)

`render(result)` writes one sentence per part, in plan order, from the outcomes. No model writes a word of it.

| outcome | what is said |
|---|---|
| `done` (write) | the TOOL's own sentence with the step's arguments: "I added floss to the shopping list." |
| `done` (read) | what the read returned |
| `failed` | "I couldn't add floss to the shopping list." |
| `uncertain` (timeout / error) | "I tried to ..., but I can't confirm it went through, so please check before asking again." |
| `uncertain` (no receipt) | "I asked to ..., but I can't confirm it happened." |
| `skipped` | "I ran out of time before I could ..." / "I didn't ..., because the step before it didn't work." |
| a part with no tool | "I don't have a way to do this yet: “move my dentist reminder to friday”." |
| a refused plan | "I haven't done any of that." plus one line: needs a grown-up / needs a household member / needs someone I recognise / needs a yes first / the fridge rule / too many at once |

The words of a done sentence come from the tool that ran, never from how the person phrased the ask, so a new reminder is
"I set a reminder", never "I moved it". Only the `done` branch can produce a sentence that says something happened. Argument
text is cut to one short clause (no sentence breaks), so a list item cannot carry a second sentence into the reply.

`speak(result, speaker, started)` then passes the text through the existing write-claim guard
(`write_claim_guard.guard_text`, lane `plan_loop`) with the caller's turn start. It is integrated, not replaced: every done
phrase is one the guard reads as a claim of that tool's receipt kind (pinned by a test over the whole table), and the guard
finds the receipt because the core re-recorded each proven write in the caller's turn. If the renderer ever claimed an
unproven write, the guard would replace the sentence (negative control: a forged `done` outcome).

A plan refused by a safety floor renders as nothing: the distress / medical floor owns that turn's reply.

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
- English only: the reply phrases and the multi-part pre-check (`looks_multi_part`) are English word lists.
- `looks_multi_part` is a word test. A single-tool turn with two action words ("remind me to take the bins out and feed the cat") reaches the planner and is handed back as a one-step plan: correct, but it has paid one planner call. How often that happens is the shadow number `outcome=single`.
- The real Gemma planner has not been run: every plan in the tests and the lab is scripted. Whether the 4B brain copies the speaker's words closely enough to pass the grounding check is unmeasured (shadow `dropped=ungrounded`).
- A read's text is spoken as returned. A list item that is itself written like a claim ("I have added ...") would be judged by the write-claim guard like any sentence.
