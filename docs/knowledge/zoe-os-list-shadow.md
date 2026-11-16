---
type: Reference
title: Zoe OS list-add shadow - every real list add is also proposed to a kernel, off the turn
description: Flag-dark ZOE_OS_LIST_SHADOW=off|shadow. The live list add stays the only writer; a Zoe OS kernel records the event and a receipt in its own store and the two outcomes are compared as codes in one log line per add. Where list add happens, the socket boundary, the unit, forget reach, what "the same" means, and the owner's turn-on and rollback steps.
tags: [zoe-os, shadow, lists, kernel, forget, privacy, flag-dark]
timestamp: 2026-11-15T00:00:00Z
---

# Zoe OS list-add shadow

**Status 2026-11-15: built, flag-dark (`ZOE_OS_LIST_SHADOW` unset = `off`). Nothing in this page is live.** The owner asked for "list add
running through the kernel in shadow"; the brain-bridge lab record (`docs/architecture/zoe-os-brain-bridge.md` on the lab stack, PR #2609)
named lists as the first live candidate: "run it flag-dark in shadow mode first, with the old tool still authoritative and receipts compared
for a week". This is that, and no more: there is no `on`, the kernel never changes the household's list.

Labels: **[code]** read in the source, **[lab]** run against a fake or the lab kernel, **[inferred]** reasoned, **[not run]**.

## 1. Where a list add happens today (every path) [code]

One live write core is two functions and a REST route. Every path below ends in one of them; the shadow hook sits AFTER each commit.
**The hook call sites and their writer-coverage test arrive later in the stack (#2623-#2631)**; this PR is the client alone.

| Path | Entry | Reaches the write through | Hook |
|---|---|---|---|
| Voice / chat fast tier, brain tool (`/api/system/intent-dispatch`), guest-at-the-panel household add | `intent_router.py:5244` `_execute_list_add_direct` (`:3047`; guest table `:4198`); intent-dispatch is `routers/system.py:2342` | `list_service.add_items_to_list` (`list_service.py:669`) | `list_service.py:689`, `source=spoken` |
| MCP tool `list_add_item` (the brain's tool lane) | `mcp_server.py:1506` | `list_service.add_items_to_list` | same (`source=spoken`) |
| Pendant day review applying an approved list item | `omi_review_apply.py:148` | `list_service.add_item_to_list` (`list_service.py:534`), no receipts list | `list_service.py:609` / `:655`, `source=single` |
| Skybridge voice resolver ("add X to the shopping list" at the panel) | `routers/voice_tts.py:4623` `resolve_skybridge_request` -> `skybridge_service.py:3569` -> `_resolve_list_add_item` (`:4882`) and the "this list" path `:4626` | its own inline INSERT in `_add_spoken_items` (`skybridge_service.py:4786`) | `skybridge_service.py:4871`, `source=skybridge` |
| UI / REST "add an item" | `routers/lists.py` `POST /{list_type}/{list_id}/items` | `create_item_record` directly | `routers/lists.py:389`, `source=rest` |
| a suggestion the member accepted (`pending_suggestions._execute_action`, inside the transaction) | the member's yes | inline INSERT | **shadowed** by its only caller `execute_suggestion`, after the commit (`src=suggestion`; `HOOKED_BY_CALLER` in `tests/test_os_shadow_hook.py`) |
| the trip packing list (`anticipation._do`) | Zoe starts it from one offer, many items | `create_item_record` | **not shadowed**: system-generated, no member add to compare (`NOT_SHADOWED`) |

**The seam.** There is no single function all four share: `write_receipts.record(user, "list", row_id, why)` is called by all of them but
carries no item, no list and no outcome. So the hook is four one-line calls, and a static test
(`test_every_list_item_writer_is_shadowed_or_named_with_a_reason`) scans every non-test module for `INSERT INTO list_items` /
`create_item_record(`: a NEW writer must call `os_shadow.note_add` or be added to `NOT_SHADOWED` with a reason, so a path cannot silently
escape the shadow. The hook is given `(member, source, list type, list name, normalised item, landed)`; `landed` is False for an item that
was already open or a replayed duplicate (counted as `live_noop`, never proposed: the kernel would otherwise record an add that never
happened). The fast tier, the brain tool and MCP all arrive as `source=spoken`; the shadow cannot tell them apart [code].

## 2. The boundary [code]

`labs/AGENTS.md` forbids wiring lab code into `zoe-data`. So `services/zoe-data/os_shadow.py` imports nothing from `labs/` and does not
add it to `sys.path` (pinned: `test_zoe_data_never_imports_the_lab_kernel`). It speaks the kernel's **socket protocol v1**:

* frame = 4-byte big-endian length + UTF-8 JSON object (`labs/zoe-os/zoe_os/wire.py`);
* on connect the kernel sends `{"v":1,"nonce":hex}`; the client answers `{"v":1,"skill":name,"proof":HMAC-SHA256(credential, nonce)}`;
* requests `{"v":1,"id":n,"op":op,"args":{...}}`, replies `{"ok":true,"result":...}` or `{"ok":false,"error":{"code","reason"}}`;
* the client uses three identities and four ops: `voice` (publish an `utterance` for the member: the cause), `brain` (`act` `list.add`
  citing that event), `panel` (a trusted surface: `forget`). `describe` is not needed: the action name and arguments are fixed here.

**How the two cannot drift silently.** `tests/test_os_shadow.py` runs the client against an in-process FAKE kernel written from the spec.
`tests/test_os_shadow_contract.py` (later in the stack, with the kernel verbs: #2623-#2631 and the kernel branch) runs it against the REAL lab kernel (`labs/zoe-os/shadow_daemon.py` in a subprocess) and asserts
`WIRE_VERSION == wire.VERSION` and that every op and argument the client sends is in `wire.OPS`. It is **skipped with a named reason**
("the lab Zoe OS kernel is not in this tree: contract unproven") when `labs/zoe-os` is absent, so the zoe-data side can land on `main`
without the lab stack and the contract test starts running the day both are present. Until then the wire is pinned by the fake only [inferred].

## 3. The kernel in production [code, owner steps in section 7]

| Question | Answer |
|---|---|
| who starts it | the owner, once: `scripts/setup/systemd/zoe-os-shadow-kernel.service` (a user-unit TEMPLATE, **not installed by any PR, never auto-enabled**) |
| as which user | the same user as zoe-data (`zoe`); the kernel is a separate PROCESS and credential boundary, not a separate OS user. The brain-bridge record's own caveat applies: a process under the same OS user can read the kernel's files; the socket is not the whole boundary [lab record] |
| where | `~/.zoe/os-shadow/` (`ZOE_OS_SHADOW_DIR`), mode 0700, **not inside the checkout**: `k.sock`, `creds.json` (0600, re-issued at every start), `people.json` (the roster), `kernel/zoe-os.db` (the spine), `pending-forgets.json` |
| what runs | `labs/zoe-os/shadow_daemon.py`: the lab kernel + the lab `Lists` driver + the `Keeper` that erases the driver's rows when the kernel forgets. The driver's rows are **in memory**; the durable record is the spine (events + receipts) |
| when it is down | the shadow logs ONE line per add with `result=kernel_unavailable` and carries on; a 5 s circuit stops it reconnecting per add; the live add is untouched. `Restart=on-failure` brings it back; the client re-reads `creds.json` on every reconnect, so a re-issued credential needs no zoe-data restart |
| supervision | systemd only. There is no watchdog: a dead shadow is visible in the report (`kernel_unavailable` count), by design not an alarm |
| roster | the kernel is built with a fixed list of members (`people.json`, read at start). A member not in it is **never proposed** (`member_not_in_roster`), because the kernel could not forget what it recorded about a person it does not know. Add a member = regenerate `people.json` and restart the unit. A FORGET never consults the roster: it is sent by the member's id whatever `people.json` says now (a member taken off it may still have records); only the kernel's own "never knew them" refusal reads as nothing to forget, which is sound because the daemon keeps every id it was ever started with |

## 4. Privacy and forget [code; lab for the kernel's own forget]

The kernel record holds list items (household words) in the spine, in receipts (`proof.item`, the undo snapshot) and in the driver. So:

* **`MemoryService.delete_user`** calls `os_shadow.forget_user` (a whole-member kernel forget through the `panel` surface) in its fail-closed
  block: while the flag is `shadow`, a kernel that cannot confirm the forget raises `ShadowForgetUnavailable` and the member erase fails and is
  retried, exactly like the other erasers.
* **Entity forget ("forget Dana")**: the cascade has a new step `os_shadow` (`memory_forget_cascade._clear`) calling `os_shadow.erase_entity`
  (kernel `forget(entity=...)`, which also shields the name). A failure lands in `CascadeResult.failed`, the forget is not confirmed, and the
  durable retry marker re-runs it as a WHOLE-MEMBER kernel forget (the name is never stored: the safe over-forget of a measuring store).
* **Topic forget** (`forget_topic._forget`): `os_shadow.erase_topic` (kernel `forget(topic=...)`), failure = `store_down` + the same marker.
* A scope the kernel refuses to match falls back to a whole-member forget (a scope that matches nothing never widens, but this store may).
* **A forget that cannot be confirmed in the kernel is PARKED, whatever the flag** (`pending-forgets.json`, replayed before the next
  job): with the flag `shadow` the caller is still told it failed (`ShadowForgetUnavailable`), with it off it is deferred (0). The park
  is a locked (thread lock + `flock`), atomic and fsync'd read-modify-write; a replay re-reads under the lock, so a park made while it
  ran is kept; a park that cannot be made durable (disk, an unreadable file, which is never overwritten) FAILS the erase closed
  (`ShadowForgetUnavailable`) instead of letting it say done. **A missing `creds.json` is not proof of nothing to forget** (the kernel's
  store can outlive it): the ONLY no-op is the flag off AND no shadow state at all, decided by `os_shadow.shadow_state_exists()`: the
  state directory is absent, or holds none of `creds.json`, `people.json`, `known-people.json`, `kernel/`, `pending-forgets.json`; a
  directory that cannot be read, or a state path that is not a directory, counts as state. The check runs in a thread (`asyncio.to_thread`), never on
  the event loop, like the park and the worker start (`asyncio.to_thread(_ensure_worker)`: `Thread.start()` waits for the thread);
  a forget's only work on the loop is a flag read, a non-blocking queue put, the wait and its log line. An add never starts a thread.
* **A parked forget always reaches the kernel before any later add for that member** (`test_os_shadow_forget.py`, "forgotten means
  forever"): the worker replays the park file before EVERY job while anything is parked (an in-memory copy of the parked ids is kept in
  step with the file under its lock, so a known-empty park costs one global read per job and no file I/O; a new process reads the file
  before its first job, so a park survives a restart). While a member's forget is still parked (kernel down, refused, file unreadable)
  that member's adds are dropped as `dropped_erased reason=pending_forget`, never sent ahead of it. A replay ahead of an add respects the
  5 s circuit (no connect per add to a dead kernel); a forget job always tries.
* **An add stamped before a forget never reaches the kernel after it.** Queued forgets need nothing extra: one FIFO worker sends
  every add stamped (and queued) before the forget first, an add in flight finishes first, and the forget then removes them. A
  whole-member erase also moves `user_erase_gate`'s generation (`erase_span`), which drops queued adds as before. The hole was the
  PARKED forget (queue full): it is replayed before the next job, ahead of adds still queued that were stamped before it. Those are
  now dropped as `dropped_erased reason=forgotten_before` (the park records when it happened; each add carries its door time). An
  add made after the forget completed is shadowed as usual (`test_os_shadow_forget.py`).
* **Only the kernel's documented forget reply is "forgotten"**: exactly `{"count": int, "upto": int}`, both non-negative ints, never a
  bool (`labs/zoe-os/zoe_os/server.py` `_op_forget`, `spine.ForgetResult`; the same for a whole, entity, topic or seqs forget). Anything
  else in an `ok:true` envelope (null, `{}`, wrong keys, wrong types, extra keys) is `kernel_malformed`: the delete is NOT confirmed (with
  the flag on the caller is told it failed) and the forget STAYS parked. `os_shadow.forget_ack` is the one reader; every path that confirms
  a forget or drops its marker (`_do_forget`: delete, entity / topic erase, the cascade step and its retry; `_replay_pending`) goes
  through it. "Never knew them" is the kernel's refusal, read as nothing to forget.
* **A forget never blocks the event loop**: the job goes on the queue with `put_nowait`; a full queue parks the forget durably instead
  (the park's `flock` + fsync run in an executor thread). Honest to the caller: flag `shadow` and not confirmed (parked or not) raises
  `ShadowForgetUnavailable`; flag off and parked returns 0 (deferred); not parkable raises.
* Erase ordering: forget jobs and proposals share ONE FIFO queue and one worker, so a proposal queued before an erase is sent before the
  forget and removed by it; one queued after is dropped (`user_erase_gate` generation moved / erase in progress: `dropped_erased`).
* `forget_reach.py` lists the kernel store (`zoe_os_shadow_kernel`: reached, member + entity erasers); `docs/knowledge/forget-reach.md` is regenerated.
* Not covered: **backups** of `~/.zoe/os-shadow/` (the existing backup policy ages them out; the directory is not in the nightly jobs, [inferred] verify before the owner turns this on), and the live `list_items` table, which is itself a ledgered GAP (member-owned data, `forget_reach.py` `list_items`).

## 5. What "the same" means [code]

The comparison is made for each list add that really landed (`os_shadow.compare`):

| field | live | kernel |
|---|---|---|
| member | the acting member id | the receipt's `on_behalf_of` (the act is for that member or refused) |
| list | the list NAME the writer resolved, sent as `list` | echoed only if the kernel's driver reports it |
| item | the stored, normalised text (`normalize_item_text`) | `proof.item` of the read-back |
| outcome class | `added` | `verified` / `unverified` / `uncertain` / `refused` |

Result codes (the closed set; one per add): `match`, `kernel_refused_live_did` (with `reason=` `no_grant|manifest|params|cause|forgotten|other`),
`kernel_unverified_live_did`, `item_differs`, `list_differs`, `member_differs` (a verified receipt for ANOTHER member) and `member_unproven` (a receipt
that names no member), `kernel_malformed` (`reason=crossed` when the reply's `v` / `id` is not this request's: a stale or crossed reply is never the answer),
`kernel_unavailable`, `timeout`,
and not-compared codes `member_not_in_roster`, `dropped_erased`, `queue_full`, `live_noop` (`live=noop`), `worker_down`, `no_input` (no member
or no item) and `shadow_error` (the shadow's OWN failure: `reason=hook` at the door, `worker` in the worker, `code` for a code outside the set).
With the flag on, EVERY `note_add` call writes exactly one line, with the same eight fields, through the one emitter `_finish`; `RESULTS` in
`os_shadow.py` is exactly the set that can be written (both pinned in `tests/test_os_shadow.py`). `kernel_did_live_refused` exists in the comparator
and is tested, but **no call site feeds a live refusal yet** [code]: only landed adds are proposed, so the "kernel did it, live refused" row of
the week's table will read zero by construction, not by evidence. `list_differs` is likewise **not measurable with the lab driver**: it is told
the list, it does not resolve one, and its read-back is `{"item"}` only. Both become meaningful when a kernel driver owns list resolution.

The log line (INFO, logger `os_shadow`, in `~/.zoe-logs/zoe-data.*`) carries **no item, no list name, no person**:

    OS_SHADOW_LIST_ADD src=spoken cls=shopping live=added kernel=verified result=match reason=- hop_ms=0.9 q_ms=0.1

Every field is from a closed set (`os_shadow.SOURCES`, `CLASSES`, `LIVE`, `KERNEL_STATES`, `REASONS`, `RESULTS`) or a number; `_finish`
maps anything else to `other` / `none`, so no caller argument can put text in the line or a counter key. `src` is one of
`spoken|single|skybridge|rest|suggestion|service|other`; `cls` is `shopping|todo|personal|other`. `hop_ms` is the kernel round trip (publish + act);
`q_ms` the wait in the queue. `scripts/maintenance/os_shadow_report.py` turns a week of these lines into the table.

## 6. Speed and safety of the hook [lab]

The hook is a flag read and, with the flag on, one bounded `queue.put_nowait`. The socket work is on one daemon thread. Measured in
`tests/test_os_shadow.py::test_the_hook_costs_almost_nothing` (2000 adds, system python 3.10, box under test-lane load): **flag off p95
0.005 ms; flag on p95 0.011 ms** per add, against a target of under 5 ms. This is the time added on the caller; it does not include the
worker thread's GIL share while it talks to the socket [not measured] and was not measured under a real turn [not run].

Any unavailability (a failed connect, unreadable or missing credentials, a connection the kernel accepted then closed or timed out
mid-request) opens the 5 s circuit, so a broken kernel costs one attempt, not one per add. A slow kernel is cut off by ONE budget per add (1.5 s from when the worker takes it): every connect, handshake, publish, act and read draws
on what is left, each also capped at 0.5 s, so a stall at any stage ends the add as `timeout` within the budget
(`test_one_deadline_per_add_covers_every_socket_stage`); a full queue (64) drops and counts; nothing the worker does can raise
into a turn (`test_the_live_add_is_identical_with_the_shadow_off_dead_or_on`: the rows the live path writes and the outcome it returns are identical
with the flag off, with the kernel dead, refusing, closing the connection mid-call, or answering garbage).

## 7. Owner steps

(Lands with the last PRs of this stack. Nothing is turned on by any PR.)

## 8. Not done, not proven

* Not run against a real turn on the live box; not measured under real load. The kernel hop number the brain-bridge record gives (about 0.75 ms
  p50 in the lab) is the lab's, not this client's [inferred].
* The kernel driver's list is in memory, has no dedupe and no list resolution: a week of shadow proves the record, receipts, availability,
  latency and forget reach, not that the kernel could replace the live list logic (dedupe, household/personal visibility, list resolution).
* Children are treated as adults in the shadow roster (no guardian model in `users`); not a consent model for anything authoritative.
* `reminders` and `alarms` are not shadowed.
