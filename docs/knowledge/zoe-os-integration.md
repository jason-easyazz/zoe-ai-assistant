---
type: Plan
title: Zoe OS integration - the staged path from the lab kernel to the kernel as the authority, each stage with its proof and its rollback
description: Four stages (lab lands; shadow for four verbs until they agree; the kernel the authority for list add behind a flag with the old path as fallback; the rest), what each stage must prove before it starts and how it is undone, and the open items a kernel process in production needs that do not exist yet (service unit, database location, backup, erase reach), each with an owner decision where one is needed.
tags: [zoe-os, shadow, kernel, integration, flag-dark, forget, rollout]
timestamp: 2026-11-15T00:00:00Z
---

# Zoe OS integration: from the lab to the authority

**Status 2026-11-15: stage (a) is in review, stage (b) is built and flag-dark, (c) and (d) are not built. Nothing here is live.**
The kernel itself is described in `docs/architecture/zoe-os.md` and the brain bridge in `docs/architecture/zoe-os-brain-bridge.md`
(both on the lab branch `labs/zoe-os-combined`, PR #2630). The list-add shadow, its wire boundary and its owner steps are in
[zoe-os-list-shadow.md](zoe-os-list-shadow.md). This page is the order and the gates; `docs/ROADMAP.md` owns where it sits among
everything else.

Claims are labelled [code] (read in the tree), [lab] (run in the lab), [live] (seen on the box) or [inferred].

## The number the flip is judged by

Per verb, from `scripts/maintenance/os_shadow_report.py` over the zoe-data logs:

    python3 scripts/maintenance/os_shadow_report.py ~/.zoe-logs/zoe-data.*

| column | meaning |
|---|---|
| seen | shadowed live writes of that verb that logged a line |
| agree | `match`: the kernel did it and its read-back names the value the live write stored |
| disagree | refused, unverified, a different value, garbage, or a write the kernel's verb cannot express (`not_expressible`) |
| unavailable | `kernel_unavailable` / `timeout`: no verdict; never counted in the rate |
| agree rate | agree / (agree + disagree) |
| hop p50 / p90 | publish + act round trip, over writes the kernel answered |
| (not compared) `worker_down` | a turn found no live worker: dropped and counted, never a thread started on the turn |

[code] Every logged write lands in exactly one bucket, and a result code the reader does not know is counted as disagree.
[lab] The negative control `test_a_kernel_that_disagrees_on_purpose_is_disagree_for_every_verb_in_the_report` runs the same traffic
against a fake kernel that disagrees on purpose (a different value, a refusal, an unverified receipt): every verb reads 0 agree,
3 disagree; its other arm, with an agreeing kernel, reads 3 agree, 0 disagree.

## Stage (a): the lab lands

**What it is.** `labs/zoe-os-combined` (PR #2630) merged to `main`: the kernel, its spine, receipts, capability and forget, the
skills, the twin and the brain bridge. Lab-only: nothing in `services/` imports it (pinned by
`test_zoe_data_never_imports_the_lab_kernel`) [code].

**Entry proof.** `pytest labs/zoe-os` green on the branch [lab]; the deterministic gate green; a cross-vendor review.

**Rollback.** Revert the merge. Nothing runs it.

## Stage (b): shadow for four verbs

**What it is.** Every live list add, spoken list-item remove, reminder create and alarm create is also proposed to a kernel process
over its Unix socket; the kernel records an event and a receipt in its own store; the two outcomes are compared and logged as one
codes-only line. The live write is the only writer [code]. One switch, `ZOE_OS_LIST_SHADOW=off|shadow` (default off; there is no
`on`), and one verb list, `ZOE_OS_SHADOW_VERBS` (default `list_add`). The switch keeps its name so a box already set to `shadow`
keeps exactly today's behaviour; the verb list widens one verb at a time; one line turns all of it off.

Where each verb is hooked (after the commit, never raises, one bounded queue put; one daemon thread does the socket work) [code]:

| verb | kernel action | live call site | not shadowed (named in the writer scan, with the reason) |
|---|---|---|---|
| `list_add` | `list.add` | `list_service.add_item_to_list` / `add_items_to_list`, `skybridge_service._add_spoken_items`, `routers/lists.py` REST add, an accepted suggestion (`pending_suggestions.execute_suggestion`, after its transaction) | `anticipation` (a packing list Zoe makes itself) |
| `list_remove` | `list.remove` | `skybridge_service` spoken remove | REST delete (reads no item text), `rename_item`'s merge (a correction) |
| `reminder_create` | `remind.set` | `reminder_service.create_reminder_record`, an accepted suggestion (`execute_suggestion`; it has no due time, so it reads `not_expressible:no_time`) | none |
| `alarm_create` | `alarm.set` | `alarms.create_alarm` (every caller: REST, voice, anticipation) | none |

What the kernel's verbs cannot express is counted as a disagreement, never sent: a recurring reminder, a date-only reminder (live
fires it at the household default time), a reminder more than 7 days out only for an OLDER kernel (its `in_minutes` takes 1..10080), an alarm time that is
not `HH:MM`, and a REPEATING alarm (`alarm.set` is one time, once: `not_expressible reason=recurring`) [code]. The kernel's alarm is
one time per person with no repeat rule; that is a known gap to close before (d), not a pass.

**What "agree" means, per verb** (`os_shadow.VERBS`, `compare`) [code]: a `match` needs the receipt to be for the member
(`on_behalf_of`, else `member_differs`) AND every read-back field that says WHAT and WHEN to equal what was sent:

| verb | WHAT | WHEN | a field the read-back lacks |
|---|---|---|---|
| `list_add`, `list_remove` | `item` (`item_differs`); `list` when the driver reports it (`list_differs`) | none: a list item does not fire | `item` missing = `kernel_malformed`; `list` optional |
| `reminder_create` | `note` (`item_differs`) | `due_at`, the ABSOLUTE due instant (UTC epoch seconds) sent and read back (`schedule_differs`) | `due_at` missing = `schedule_unproven` (an older kernel; a relative read-back is no proof) |
| `alarm_create` | the time is the whole value | `alarm` == `time`, an absolute wall-clock `HH:MM` already (no queue-delay weakness) (`schedule_differs`) | `kernel_malformed` |

Every list remove is proposed against the RESOLVED list (the row the card or the name found, `skybridge_service._find_list_for_remove(...,
with_name=True)`), not the words. For whom, every verb: `on_behalf_of` must be the member (`member_differs`), and a receipt without it is `member_unproven`
(`os_shadow.for_whom`, the one check).

**Reminders are sent and compared absolute to absolute** [code]: the due instant (`due_at`, integer UTC epoch seconds) is computed
once from the live row and the commit time and SENT (never with `in_minutes`: the kernel takes exactly one), so a job that waited in
the queue still asks for the same instant; the kernel's read-back `proof.due_at` is the stored instant and must equal it (within 60 s,
the rounding of the fallback below). A due instant that is not a time (NaN, inf, a string, a bool, past 2^40) is `kernel_malformed`,
and the decoder refuses NaN / Infinity on the wire. **An older kernel** (no `due_at`) refuses it as an unknown argument: the act is
retried ONCE with `in_minutes` counted from the SEND, the line carries `reason=old_kernel`, and since its read-back has no `due_at` it
reads `schedule_unproven` (the report's NOT COMPARABLE YET line names how many writes went to an older kernel).

**The lab driver's `remind.set` read-back is `{"note"}` only** (`labs/zoe-os/zoe_os/brain/drivers.py` `Reminders.added`) [code], so
against today's lab kernel every reminder reads `schedule_unproven` (a disagreement), never `match`: a reminder at the wrong time can
no longer pass. The driver must report its schedule (`in_minutes`, or the due instant) before reminders can meet the pass mark.

**The report judges each verb on its own** (`scripts/maintenance/os_shadow_report.py`): one row per verb (an unknown verb or code is
its own counted row) and a `PASS` / `NOT YET (...)` line per verb from exactly the five numbers below, naming each one it fails; the
list-add totals are over list-add lines only. A verb whose disagreements are ALL `schedule_unproven` reads `NOT COMPARABLE YET
(schedule_unproven: n): cannot meet the pass mark yet: the kernel does not report when a reminder is due`, its agree rate not shown as
a failure (the counts stay in the table); one disagreement of any other code makes it `NOT YET` again.

**What the real run proved, and what it did not.** The 29-turn real run of 2026-11-15 drove list add (8/8), reminders (8/8) and
alarms (6/6) through the kernel with the policy arm, refusals 3/3, zero false calls, hop about 1.4 ms p50 (brain-bridge record)
[live]. **List remove was not in that run**: `list.remove` is a new kernel action added for the shadow (`labs/zoe-os/shadow_daemon.py`,
kernel-side branch), proven only against the lab kernel in the contract test [lab]. That run proves the brain can choose the action;
the shadow proves something different: that the kernel, given the live write, records and verifies the same thing.

**Entry proof (to turn it on).** The lab and the kernel PR are merged; the owner installs the unit and writes the roster
([zoe-os-list-shadow.md](zoe-os-list-shadow.md) section 7); `ZOE_OS_SHADOW_VERBS` is set to the verbs to watch.

**Exit proof (to go to (c)) - owner decision D1.** Proposed: for list add, at least 14 days and at least 200 judged writes, agree
rate at least 99.5%, every disagreement read and explained by its reason code (the report checks the machine part: none with an
unknown code or an `other` reason), unavailable under 1% of the writes the kernel was asked about, hop p90 under 10 ms,
and one member erase and one entity forget during the window that reached the kernel (checked by the kernel's receipt count for the
member going to zero). These numbers are a proposal, not a measurement.

**Rollback.** Remove the `ZOE_OS_LIST_SHADOW` line and restart zoe-data: the hook becomes a flag read again. Narrow a single verb by
editing `ZOE_OS_SHADOW_VERBS`. Leave `~/.zoe/os-shadow/` so a later forget still reaches the record (a miss is parked in
`pending-forgets.json` and replayed), or delete it to erase the whole shadow record.

## Stage (c): the kernel becomes the authority for ONE verb, list add

**What it is (not built).** A third value for a NEW flag (proposed `ZOE_OS_LIST_ADD_AUTHORITY=off|kernel`, default off): the spoken
list add asks the kernel to act, and the kernel's driver performs the live write through the same `list_service` path (the driver
becomes the caller, so dedupe, visibility and list resolution stay production's). The receipt is the write receipt. If the kernel
is unavailable, refuses for a reason that is not a policy refusal, or times out, the old path runs unchanged (fallback) and the
fallback is counted. A policy refusal (no grant, forgotten) is honoured: that is the point of the authority.

**Entry proof.** Stage (b)'s exit proof for list add; the open items below that are marked "before (c)" closed; a replay-gated voice
run (the list add is on the voice path); a twin rehearsal of the household's week with the kernel as authority.

**Rollback.** Flag to `off`: the old path is the only path again (it never stopped existing). The kernel's records stay and stay
reachable by forget.

## Stage (d): the rest

Reminder create, alarm create and list remove move one at a time, each with its own (b) exit proof and its own flag. Before alarms
can move the kernel's alarm model must hold several alarms per person with a repeat rule; before reminders, recurrence and date-only
reminders. The skills that today are lab toys (`calendar_headsup`, `leaving_catch`) come after, behind the attention budget.

## What a kernel process in production needs that does not exist yet

Every item is open. None is done by any PR in this stack.

| # | item | state today | needed before | owner decision |
|---|---|---|---|---|
| 1 | **a service unit** | `scripts/setup/systemd/zoe-os-shadow-kernel.service` is a user-unit TEMPLATE on the kernel branch, never installed or enabled by anything [code] | (b) | **D2**: install it as a user unit beside zoe-data, or fold the kernel into zoe-data's process (no unit, no socket, but then a kernel crash is a zoe-data crash) |
| 2 | **memory protection** | the unit sets nothing about swap or memory priority [inferred] | (c) | whether it joins the voice stack's no-swap drop-in (it would be on the list-add path once authoritative) |
| 3 | **its database location** | `~/.zoe/os-shadow/kernel/zoe-os.db`, SQLite, 0600, outside any checkout [code] | (b) | **D3**: keep it there, or move it beside the other stores under one Zoe data root |
| 4 | **backup** | not in any backup job [inferred: no backup script names it] | (c) | **D4**: include it in the nightly backup (it holds items and, with the widened verbs, reminder notes: words), or treat the shadow record as disposable and back up only from (c) |
| 5 | **erase reach: member delete** | `MemoryService.delete_user` calls `os_shadow.forget_user`, fail-closed while the switch is on; with it off a once-installed kernel is still tried and a miss is parked and replayed [code] | (b), done in code | none; must be live-verified once after turn-on |
| 6 | **erase reach: forget** | entity forget (`memory_forget_cascade` step `os_shadow`) and topic forget (`forget_topic`) reach it; `forget_reach.py` lists the store [code] | (b), done in code | none; the store description must say it now holds reminder notes and alarm times too |
| 7 | **retention** | the kernel's retention policy is decided (option B) but nothing calls the sweep: events never age [lab] | (c) | **D5**: who calls the sweep and when (a night-window job, rehearsed by day first) |
| 8 | **the roster** | CLOSED for lists (2026-11-19): with `ZOE_OS_LISTS=kernel` zoe-data rewrites `people.json` from the accounts when the door meets an unknown member, and the kernel re-reads the file when it changes, no restart ([zoe-os-list-shadow.md](zoe-os-list-shadow.md) section 4b). Watch-only shadow with lists off: still written by hand [code] | (c) | **D6**: zoe-data pushes member add/remove to the kernel, or the kernel reads `users` itself |
| 9 | **consent model** | the shadow daemon grants every roster member the brain's acts with no ceremony; children are treated as adults [code] | (c) | **D7**: the guardian model for a child's grant before anything is authoritative |
| 10 | **the in-memory drivers** | the shadow daemon's list, reminder and alarm stores are in memory: lost on restart, so a remove after a restart verifies trivially [code] | (c) | none: (c) replaces them with drivers that call production's write path |
| 11 | **health and alarm** | nothing watches the unit; an unavailable kernel is only a count in the log [code] | (c) | whether ground truth / health checks name it |
| 12 | **the credentials** | `creds.json` (0600) is rewritten on every kernel start; zoe-data reads it on first use [code] | (c) | rotation rule, and whether it moves into the credential vault |

## Open owner decisions, in one list

- **D1** the exit proof numbers for stage (b) (proposed above).
- **D2** a user unit beside zoe-data, or the kernel inside zoe-data's process.
- **D3** where the kernel's database lives.
- **D4** whether the shadow record is backed up.
- **D5** who runs the retention sweep.
- **D6** how the kernel learns about a new or removed member.
- **D7** the guardian model for a child's grant.
- And the one that is already the owner's: when to set `ZOE_OS_LIST_SHADOW=shadow` and which verbs to list.
