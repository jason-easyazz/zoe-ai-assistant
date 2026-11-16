---
type: Plan
title: Zoe OS integration - the staged path from the lab kernel to the kernel as the authority, each stage with its proof and its rollback
description: Four stages (lab lands; shadow for four verbs until they agree; the kernel the authority for list add behind a flag with the old path as fallback; the rest), what each stage must prove before it starts and how it is undone, and the open items a kernel process in production needs that do not exist yet (service unit, database location, backup, erase reach), each with an owner decision where one is needed.
tags: [zoe-os, shadow, kernel, integration, flag-dark, forget, rollout]
timestamp: 2026-11-15T00:00:00Z
---

# Zoe OS integration: from the lab to the authority

**Status 2026-11-15: stage (a) is in review, stages (b) and (c) are built and flag-dark, (d) is not built. Nothing here is live.**
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
fires it at the household default time), a reminder more than 7 days out (`remind.set` takes 1..10080 minutes), an alarm time that is
not `HH:MM`, and a REPEATING alarm (`alarm.set` is one time, once: `not_expressible reason=recurring`) [code]. The kernel's alarm is
one time per person with no repeat rule; that is a known gap to close before (d), not a pass.

**What "agree" means, per verb** (`os_shadow.VERBS`, `compare`) [code]: a `match` needs the receipt to be for the member
(`on_behalf_of`, else `member_differs`) AND every read-back field that says WHAT and WHEN to equal what was sent:

| verb | WHAT | WHEN | a field the read-back lacks |
|---|---|---|---|
| `list_add`, `list_remove` | `item` (`item_differs`); `list` when the driver reports it (`list_differs`) | none: a list item does not fire | `item` missing = `kernel_malformed`; `list` optional |
| `reminder_create` | `note` (`item_differs`) | `due_at`, the ABSOLUTE due instant (UTC epoch seconds), within 60 s (`schedule_differs`) | `due_at` missing = `schedule_unproven` (a relative `in_minutes` read-back is no proof) |
| `alarm_create` | the time is the whole value | `alarm` == `time`, an absolute wall-clock `HH:MM` already (no queue-delay weakness) (`schedule_differs`) | `kernel_malformed` |

For whom, every verb: `on_behalf_of` must be the member (`member_differs`), and a receipt without it is `member_unproven`
(`os_shadow.for_whom`, the one check).

**Reminders are compared absolute to absolute** [code]: the due instant is computed once from the live row and the commit time; the
wire still carries `in_minutes` (the kernel's `remind.set` takes only that and refuses an unknown argument,
`labs/zoe-os/zoe_os/receipts.py check_params`), recomputed from the instant at SEND time, so a job that waited in the queue still asks
for the same instant. **What the kernel side must add before reminders can be compared:** accept `due_at` (integer, UTC epoch
seconds) on `remind.set` (alongside or instead of `in_minutes`), and return `proof.due_at` (integer, UTC epoch seconds: the stored due)
in its read-back (`Reminders.added`). Then drop `due_at` from `os_shadow.LOCAL_ONLY` so it is sent.

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

**What exists now (built, flag-dark, not live).** Flag `ZOE_OS_LIST_ADD_AUTHORITY=off|kernel` (default `off`), client
`services/zoe-data/os_authority.py`, kernel `labs/zoe-os/authority.py` (kernel branch `feature/os-authority-1-kernel`) [code].

- **Where it decides.** `list_service.add_items_to_list` (the voice/chat fast path in `intent_router` and the brain's
  `list_add_item` tool in `mcp_server`) asks the kernel INSIDE the list's write transaction, after production's own dedupe and
  "already on the list" checks and before the row is inserted, so the kernel is only ever asked about a row that is about to exist.
  NOT under authority in this stage (they stay shadowed only): the skybridge spoken add (`skybridge_service._add_spoken_items`),
  the REST/panel add (`routers/lists.py`), the Omi day-review add (`omi_review_apply`) and Zoe's own offers. Each moves in its own
  change [code].
- **Source of truth in kernel mode.** The kernel's receipt is the authority on WHETHER a spoken add by a roster member happens.
  Production's `list_items` stays the household's list: every reader (panel, REST, the brain's list read, the reconcile) reads it,
  and every other verb (remove, tick off, rename, delete) still writes only it. The kernel's list store is therefore a record of the
  adds it decided, not a second list: it must be a SUBSET of production's rows (production's delete is a soft delete, so a decided
  row is always there in some state).
- **How they are kept from diverging.** One order (kernel receipt, then production insert, in production's transaction) and one
  check: `scripts/maintenance/os_authority_reconcile.py` (read-only, codes and counts) compares the kernel's row digests with every
  `list_items` row. `kernel_only` (a kernel-decided add production never got: the production write failed after the receipt) must
  be 0, exit 1 otherwise; `production_only_recent` is the expected count of adds the kernel did not decide (fallbacks, guests, the
  paths above, erased rows) and is read beside the `OS_AUTHORITY` fallback counts it also prints.
- **All or none (a spoken add of several items).** `add_items_to_list` runs its one transaction inside `os_authority.all_or_none()`:
  every item the kernel verifies is noted, and if the transaction rolls back (a later item refused, a failed insert, a failed
  commit) each is TAKEN BACK in the kernel with a `list.remove` act by the brain, newest first, so neither side keeps part of the add
  [code; lab: `test_first_items_verified_then_one_refused_leaves_neither_side_holding_any`, and on the real kernel
  `test_a_later_refusal_leaves_the_real_kernel_holding_none_of_the_add`]. Not `undo`: the kernel's `undo` reverses only a person's
  LAST action and refuses a second undo in a row ("the last action was itself an undo"), so it cannot take back two items, and the
  kernel has no batch verb [code: `labs/zoe-os/zoe_os/receipts.py` `undo`]. A take-back that does not verify is logged
  (`result=rollback code=incomplete`), recorded in `authority-rollback-pending.json` beside `creds.json` (0600; member id, count,
  time, never an item or a list name) for the reconcile to report, and the add is never reported done (the add's own error stands).
  The kernel is never asked outside such a unit (`fallback no_unit`).
- **Kernel durability.** The kernel's list rows are rebuilt from its own receipts at start and after every forget: an acknowledged add
  survives `kill -9`; an add killed before its receipt committed is absent and its intent gets an `uncertain` receipt; one killed
  after the commit but before the reply is present and verified (the client fell back, so production has it once too) [lab:
  `labs/zoe-os/tests/test_authority.py`].

**The fallback (the load-bearing part).** One log line per decision, codes only:
`OS_AUTHORITY verb=list_add result=<kernel|refused|fallback> code=<code> ms=<n>` [code].

| condition | what happens | logged |
|---|---|---|
| flag off | today's path, no socket | nothing |
| verified receipt naming the item | production inserts the row; the shadow does not propose it again | `kernel verified` |
| kernel refuses `no grant` (the member withdrew the brain's grant) or `forgotten` (the text was forgotten) | nothing written; the caller says so (`os_authority.refusal_reply`) | `refused no_grant` / `refused forgotten` |
| kernel refuses for any other reason (`params`, `manifest`, `cause`, `other`) | old path adds it | `fallback refused_<code>` |
| unverified / uncertain receipt, or a proof that names another item | old path | `fallback unverified` |
| no answer within `TIMEOUT_S` = 150 ms (hanging kernel) | old path; the circuit skips the kernel for 5 s | `fallback timeout` |
| inside the circuit window | old path, no socket | `fallback circuit` |
| kernel down / socket missing / connection refused or closed | old path; circuit | `fallback unavailable` |
| garbage reply / other socket error | old path; circuit | `fallback protocol` / `socket` |
| health (read at most every 5 s): not alive, schema not 1, or its write probe failed | old path; circuit | `fallback unhealthy` / `write_failed` |
| member not in the kernel's roster (a guest, the household, a member added after the roster was written) | old path, no socket | `fallback not_in_roster` |
| member being erased | old path, no socket | `fallback erasing` |
| any unexpected error in the client | old path | `fallback error` |
| asked outside an `all_or_none` unit (a grant nothing could take back) | old path, no socket | `fallback no_unit` |
| the kernel verified the act after the turn's wait ran out | old path; the worker takes the grant back (`list.remove`), or records it | `fallback late` (and `late:taken_back` / `late:recorded`) |

Refusal codes treated as REAL: `no_grant` and `forgotten` (`os_authority.REAL_REFUSALS`).

**The turn's budget is ONE deadline, and nothing blocks the loop.** `propose_add` does only in-memory checks on the event loop (the
flag, empty input, the erase gate, the unit, the circuit); the roster file, every credential file and the kernel calls run on the
authority's one worker thread, against the same deadline the turn waits on (health, publish and act together, never 150 ms each). The
worker starts no `act` with less than `ACT_MIN_S` (20 ms) left. A grant is handed over under a lock: either the turn is still waiting
(the grant joins its ledger) or the worker sees it abandoned and takes it back with `list.remove`, recording it ids-only
(`authority-rollback-pending.json`, `why=late`) when that does not verify. **The window that remains**: an `act` already sent when the
deadline passes mid-reply. The kernel may commit it and the client cannot know, so the worker records it (`why=uncertain`, member and
count only) and the reconcile's `kernel_only` / `rollback_pending` show it; production has written the row by the old path, so the
two sides hold the same item and only the kernel's record of who decided it differs [lab:
`test_an_act_answered_past_the_deadline_is_recorded_and_the_old_path_result_stands`]. The timeout: the healthy hop is about 1.4 ms p50
[live, shadow record] and about 1 ms in the lab [lab]; 150 ms is about 100 times that, and it is the most a kernel that accepts but
never answers can add to one voice turn, once per 5 s circuit window [inferred: no voice-turn measurement was taken for this stage].

**Children and guests.** Today's rule is the fridge rule (`services/zoe-data/AGENTS.md`): any member, and the panel as a guest,
works the ITEMS of a family list. The kernel's consent model has a person, an adult/child role and a guardian-signed grant, and NO
guest and no household-owned list: it cannot express "the panel as a guest adds to a family list". So a guest or household id is
never proposed (`not_in_roster` fallback): nobody loses an ability. A child is an adult to this kernel (the daemon grants every roster
member the brain's acts at start, without a guardian) [code]; with D7 undecided that keeps every child's ability; when D7 adds a
guardian ceremony, a child without a guardian grant must FALL BACK, not be refused, until the owner decides otherwise.

**Who may ask (the kernel's speaker rule).** The kernel refuses a person-bound request (forget, erase, grant, revoke, undo, explain for
a person) from a surface unless it carries a SPEAKER ASSERTION minted with the kernel's per-start identity secret
(`identity-creds.json`, 0600). zoe-data is the component that verifies people, so it mints them itself: `os_speaker.py` re-implements
the token from the kernel README's spec (stdlib HMAC, no import from `labs/`), reads the secret and the kernel run's `generation` (every token names it) fresh for every mint (a kernel
restart writes new ones), mints ONE per request for the person zoe-data already verified with the audience the request needs, and
never logs it; a `speaker unproven` answer is retried once with the secret read again, then it is the kernel's real answer (never
"unavailable", and a scoped forget is never widened into a whole-member one on it, nor on a `not theirs to forget` refusal). The member-delete path connects as the kernel's
`eraser` instead (`erase-creds.json`): the shadow's whole-member forget and `erase_member` both use it when it is provisioned. The
reconcile reads `list_digest` as the `reconciler` (`reconcile-creds.json`: credential plus the per-start digest key). Contract
against the real lab code: `services/zoe-data/tests/test_os_speaker_contract.py` and `test_os_authority_contract.py`.

**Erase reach.** `MemoryService.delete_user` calls `os_authority.erase_member` (as the `eraser`) after the shadow's forget: with the flag `kernel` the
kernel's read-back must find nothing of the member left (events, list rows, reminder/alarm rows; their grant is revoked so a restart
does not re-grant them), else `delete_user` raises and the delete is retried (an unprovisioned kernel, a missing eraser credential and
a refusal fail it too); with the flag off the shadow's `forget_user` still
forgets the member and the rows are rebuilt from what is left [code; lab]. The entity and topic forgets reach the rows through the
rebuild [lab: `test_an_entity_forget_through_the_old_op_also_reaches_the_durable_rows`]. `os_authority.erase_item(member, item)` ("forget that" on
one item of ONE member, by digest, with that member's `forget.scoped` assertion) exists and is proven against the real kernel, but **no production verb calls it**: production
has no per-item forget, and its item delete is a soft delete that keeps the words in `list_items` too, so it deliberately does not
erase the kernel's copy either.

**Entry proof (to turn it on).** Stage (b)'s exit proof for list add (D1); the kernel PR and this one merged; the authority unit
installed in place of the shadow unit (D2, D3); the reconcile at `kernel_only=0` on the box; a replay-gated voice run with the flag on
(list add is on the voice path); a twin rehearsal of the household's week with the kernel as authority.

**Rollback.** Remove the `ZOE_OS_LIST_ADD_AUTHORITY` line and restart zoe-data: the old path is the only path again.

**Still open for (c).** Applying the nightly-backup block and the one-time database move (D3/D4 decided; `labs/zoe-os/README.md`); D5 retention (the rows are rebuilt from receipts, so a retention sweep would silently drop list rows: it must exempt
list receipts that back a live row, or write a checkpoint, before anything calls it); D6 the roster (a new member falls back until it
is re-written); D7 children; the skybridge and REST adds; power loss (SQLite WAL with `synchronous=NORMAL`: a kill is survived, a power
cut may lose the last acknowledged adds, which production still has).

## Stage (d): the rest

Reminder create, alarm create and list remove move one at a time, each with its own (b) exit proof and its own flag. Before alarms
can move the kernel's alarm model must hold several alarms per person with a repeat rule; before reminders, recurrence and date-only
reminders. The skills that today are lab toys (`calendar_headsup`, `leaving_catch`) come after, behind the attention budget.

## What a kernel process in production needs that does not exist yet

Every item is open. None is done by any PR in this stack.

| # | item | state today | needed before | owner decision |
|---|---|---|---|---|
| 1 | **a service unit** | shadow: `scripts/setup/systemd/zoe-os-shadow-kernel.service`; authority: `labs/zoe-os/zoe-os-authority-kernel.service` (replaces it, `Conflicts=`): both TEMPLATES, never installed [code] | (b) / (c) | **D2**: a user unit beside zoe-data, or the kernel inside zoe-data's process |
| 2 | **memory protection** | the authority template sets `MemoryHigh=64M`, `MemoryMax=96M`, `MemorySwapMax=0`, no `MemoryLow`, `Nice=5`, sized from a measured ~20 MB resident [code; lab] | (c), done in the template | none beyond D2 |
| 3 | **its database location** | DECIDED (owner, 2026-11-15): the kernel's OWN SQLite file, never tables in the main database, in a permanent home beside Zoe's other stores, proposed `~/.zoe/zoe-os/zoe-os.db`; ONE setting (`ZOE_OS_KERNEL_DB` in the unit template, passed as `--db` to either daemon; the same registered flag in zoe-data, which only names it); the daemon refuses to start if the old `~/.zoe/os-shadow/kernel/zoe-os.db` was not moved [code; lab] | (b) | owner step: the one-time move (`labs/zoe-os/README.md`); confirm the proposed path |
| 4 | **backup** | DECIDED (owner, 2026-11-15): in the nightly backup from the day shadow is on. Proposed concrete addition to `scripts/maintenance/mempalace-nightly-backup.sh` (Python online-backup API from a read-only connection, integrity check, gzip, keep 7; restore check: integrity, `health`, reconcile `kernel_only=0`, re-apply later forgets), written out in `labs/zoe-os/README.md`, not applied [code] | (b) | owner: apply the block to the nightly script (a separate PR) |
| 5 | **erase reach: member delete** | `MemoryService.delete_user` calls `os_shadow.forget_user` and, with the authority flag `kernel`, `os_authority.erase_member` (verified read-back, fail-closed) [code; lab] | (b) / (c), done in code | none; live-verify once after turn-on; re-join PR #2598's delete-everything structure when it lands |
| 6 | **erase reach: forget** | entity forget (`memory_forget_cascade` step `os_shadow`) and topic forget (`forget_topic`) reach it; `forget_reach.py` lists the store [code] | (b), done in code | none; the store description must say it now holds reminder notes and alarm times too |
| 7 | **retention** | nothing calls the sweep; the authority's list rows are REBUILT from receipts, so a sweep as written would drop live list rows [code] | (c) | **D5**: who calls the sweep, and it must exempt list receipts backing a live row (or checkpoint) first |
| 8 | **the roster** | written by hand once (`os_shadow.py --write-roster`); a new member is unknown to the kernel until it is re-run and the unit restarted [code] | (c) | **D6**: zoe-data pushes member add/remove to the kernel, or the kernel reads `users` itself |
| 9 | **consent model** | the shadow daemon grants every roster member the brain's acts with no ceremony; children are treated as adults [code] | (c) | **D7**: the guardian model for a child's grant before anything is authoritative |
| 10 | **the in-memory drivers** | the authority daemon's list driver is durable (rebuilt from the log, kill-tested); reminders and alarms stay in memory (not authoritative) [lab] | (c) for lists, done; (d) for the rest | none |
| 11 | **health and alarm** | the `health` op (alive, schema, a write probe) drives the fallback; nothing alarms on a run of fallbacks yet [code] | (c) | whether ground truth names the unit and alarms on the fallback rate |
| 12 | **the credentials** | `creds.json` (0600) is rewritten on every kernel start; zoe-data reads it on first use [code] | (c) | rotation rule, and whether it moves into the credential vault |

## Open owner decisions, in one list

- **D1** the exit proof numbers for stage (b) (proposed above).
- **D2** a user unit beside zoe-data, or the kernel inside zoe-data's process.
- **D3** decided 2026-11-15: its own file in a permanent home (proposed `~/.zoe/zoe-os/zoe-os.db`); the move is an owner step.
- **D4** decided 2026-11-15: nightly backup from the day shadow is on; the script change is proposed, not applied.
- **D5** who runs the retention sweep.
- **D6** how the kernel learns about a new or removed member.
- **D7** the guardian model for a child's grant.
- And the one that is already the owner's: when to set `ZOE_OS_LIST_SHADOW=shadow` and which verbs to list.
- **D8** when to set `ZOE_OS_LIST_ADD_AUTHORITY=kernel` (after stage (c)'s entry proof), and whether a run of fallbacks alarms.
