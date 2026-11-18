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
  job), and the caller is told it FAILED (`ShadowForgetUnavailable`) with the flag on OR off: a durable retry marker is not an erase (a
  shadow turned off with its kernel stopped still holds what it recorded), so a member delete, entity or topic forget fails closed and is
  retried until the kernel confirms. The park
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
  `ShadowForgetUnavailable`, flag on or off (parked, or not parkable).
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
With the switch on and the verb listed, EVERY `note_*` call writes exactly one line, with the same nine fields, through the one emitter `_finish`; `RESULTS` in
`os_shadow.py` is exactly the set that can be written (both pinned in `tests/test_os_shadow.py`). `kernel_did_live_refused` exists in the comparator
and is tested, but **no call site feeds a live refusal yet** [code]: only landed adds are proposed, so the "kernel did it, live refused" row of
the week's table will read zero by construction, not by evidence. `list_differs` is likewise **not measurable with the lab driver**: it is told
the list, it does not resolve one, and its read-back is `{"item"}` only. Both become meaningful when a kernel driver owns list resolution.

The log line (INFO, logger `os_shadow`, in `~/.zoe-logs/zoe-data.*`) carries **no item, no list name, no person**:

    OS_SHADOW_ACT verb=list_add src=spoken cls=shopping live=added kernel=verified result=match reason=- hop_ms=0.9 q_ms=0.1

Every field is from a closed set (`os_shadow.SOURCES`, `CLASSES`, `LIVE`, `KERNEL_STATES`, `REASONS`, `RESULTS`) or a number; `_finish`
maps anything else to `other` / `none`, so no caller argument can put text in the line or a counter key. `src` is one of
`spoken|single|skybridge|rest|suggestion|service|other`; `cls` is `shopping|todo|personal|other`. `hop_ms` is the kernel round trip (publish + act);
`q_ms` the wait in the queue. `scripts/maintenance/os_shadow_report.py` turns a week of these lines into the table. It drops nothing: a result code it does not know is
counted as `unknown` (a compared add that is not a match), a marker line it cannot read as `rejected` (a repeated key, no result, or a
`hop_ms` / `q_ms` that is missing, empty, not a number, nan / inf, negative or over a day), an unknown KEY is counted by an 8-hex tag of
its name (never the name: it is caller text in principle), and the totals always add up to the lines read (`tests/unit/test_os_shadow_report.py::test_nothing_is_dropped_unknown_codes_and_unreadable_lines_are_counted`).

## 6. Speed and safety of the hook [lab]

The hook is a flag read and, with the flag on, one bounded `queue.put_nowait`. The socket work is on one daemon thread. Measured in
`tests/test_os_shadow.py::test_the_hook_costs_almost_nothing` (2000 adds, system python 3.10, box under test-lane load): **flag off p95
0.005 ms; flag on p95 0.011 ms** per add, against a target of under 5 ms. This is the time added on the caller; it does not include the
worker thread's GIL share while it talks to the socket [not measured] and was not measured under a real turn [not run].

With the REAL lab kernel (`labs/zoe-os/shadow_daemon.py`, one process, 200 adds paced 10 ms apart, system python 3.10, box under normal builder load; a one-off scratch script, not committed) [lab, 2026-11-15, before the worker moved to import time]: the hook cost on the caller was **p50 0.06 ms, p95 0.14 ms** (max 7 ms: the very first add started the worker thread; the worker now starts at import while the switch is `shadow`, not re-measured), and the kernel round trip (publish + act, on the worker thread) was **p50 1.6 ms, p95 2.2 ms, max 11 ms**, all 200 `match`. The hop is paid by the worker, never by the turn. A burst of 300 adds in a tight loop overflowed the 64-slot queue (236 `queue_full`, nothing blocked).

Any unavailability (a failed connect, unreadable or missing credentials, a connection the kernel accepted then closed or timed out
mid-request) opens the 5 s circuit, so a broken kernel costs one attempt, not one per write. A slow kernel is cut off by ONE budget per shadowed write (1.5 s from its first kernel call): every connect, handshake, publish, act and read draws
on what is left, each also capped at 0.5 s, so a stall at any stage ends the add as `timeout` within the budget
(`test_one_deadline_per_add_covers_every_socket_stage`, `test_every_verb_runs_on_one_deadline`); a full queue (64) drops and counts; nothing the worker does can raise
into a turn (`test_the_live_add_is_identical_with_the_shadow_off_dead_or_on`: the rows the live path writes and the outcome it returns are identical
with the flag off, with the kernel dead, refusing, closing the connection mid-call, or answering garbage).

## 7. Owner steps (nothing is done by the PRs)

**2026-11-18 first live run:** The kernel ran from main under an active systemd unit. Three demo list adds each logged `kernel=verified result=match`, with 3 to 12 ms round trips. It was turned off for the train deploy. The rehearsal matched 300 of 300 adds: round trip p50 5.9 ms, p95 11.0 ms; added caller cost p50 0.010 ms; kernel memory about 22 MB.

A state folder made by hand has no settle marker and the train kernel refuses it. Always use `os_shadow.py --init`. This page describes watch-only mode; that mode ends when lists move behind `ZOE_OS_LISTS`.

**Merge order.** (1) the zoe-data PRs (hook, forget reach, report) can land on `main` alone: with the flag off they do nothing. (2) The lab
stack up to the brain bridge (PR #2609 and the ~28 draft PRs under it) must be merged before the kernel exists on `main`: the daemon needs
`zoe_os/kernel.py`, `server.py`, `wire.py`, `capability.py`, `receipts.py`, `spine.py` and `brain/drivers.py`. (3) then the kernel PR
(daemon, unit template, contract test).

**Turn on** (after 1-3 are merged and deployed):

    # the state directory: made EMPTY, marked, and settled (about 4 s) before anything is written into it. Never mkdir / copy it by hand.
    python3 ~/assistant/services/zoe-data/os_shadow.py --init
    # the roster: every member id, once (and again when a member is added). Ids only; the count is all it prints.
    docker exec zoe-database psql -U zoe -d zoe -tAc "SELECT id FROM users ORDER BY id" | xargs python3 ~/assistant/services/zoe-data/os_shadow.py --write-roster
    cp ~/assistant/scripts/setup/systemd/zoe-os-shadow-kernel.service ~/.config/systemd/user/
    systemctl --user daemon-reload && systemctl --user enable --now zoe-os-shadow-kernel.service
    # verify the kernel is up and the credentials were written (names only, never print them)
    systemctl --user is-active zoe-os-shadow-kernel.service && ls -l ~/.zoe/os-shadow/
    # the flag, then a zoe-data restart
    echo 'ZOE_OS_LIST_SHADOW=shadow' >> ~/assistant/services/zoe-data/.env && systemctl --user restart zoe-data

**The state directory settles before it holds anything (owner decision 2026-11-17: "redesign the setup step").** With no kernel
installed a forget must cost a spoken turn nothing, so zoe-data answers "no shadow state" from memory: no thread, no stat. That is
only sound if shadow state cannot exist before every process has seen its directory. The rule, in `os_shadow.py` and (the same text,
pinned equal by `tests/test_os_shadow_settle.py`) `labs/zoe-os/zoe_os/shadow_state.py`:

* whoever makes the directory makes it EMPTY, writes `settled.json` (a time, the boot id and the directory's own device / inode: no
  personal data) and waits `SETTLE_S` (4 s) before any data file appears. Every zoe-data writer goes through `ensure_state_dir`
  (a parked forget or erase, `--write-roster`, `--init`, `--restore`); the kernel goes through `shadow_state.require_settled`;
* a zoe-data process looks for the directory at start-up and then every `POLL_S` (1 s), and trusts a "no directory" answer for
  `TRUST_S` (3 s) only. `SETTLE_S > TRUST_S`, so by the time anything can be recorded every process has seen the directory or
  stopped trusting its answer and looks at the disk (in a thread). Once the directory exists every forget looks: data means the
  kernel path (fail closed), no data yet means there is provably nothing to forget;
* the settle wait never runs on a turn's event loop (it refuses to): a park takes it in the executor thread, once, when the
  directory is first made; the owner's tool and the kernel take it at start.

**Restore rule: install and restore go through the tool; never copy the directory by hand.**

    python3 ~/assistant/services/zoe-data/os_shadow.py --restore /path/to/a/backup/copy/of/os-shadow

It makes and settles the directory, then merges in ONLY the parked forgets and parked erases (ids) and, if there is none, the roster.
The kernel's record (`kernel/`) is NOT restored: it is a disposable copy and a backup of it can hold what a member has had forgotten
since; credentials are re-issued at every kernel start. The off-box backup leaves `~/.zoe/os-shadow` out (`zoe_offbox.py`
`ZOE_TREE_EXCLUDES`), so no job copies it.

What happens to a directory copied in by hand (`cp -r`, an rsync, a restore outside the tool): its marker is missing, unreadable,
from the future, too young for the data beside it, or another directory's (a copy has a different inode). **The kernel refuses to
start and says why** (`refusing to start: ... Remove the state directory, or restore it with os_shadow.py --restore`); it deletes
nothing. **zoe-data treats the data as state PRESENT**: every forget goes to the kernel, nothing answers, the forget is parked and
FAILS (the member is told it is not forgotten) until the owner removes the directory or restores through the tool. One honest limit:
a hand copy appears with its data at once, so a process that looked less than `TRUST_S` ago can answer one forget from memory in
those seconds; that data is never served, and the owner's only way forward is to remove it.

**After a forget the member is recorded afresh.** Forgetting a member's data must not lock them out. Two things made it do so,
found the first time the forget half of `tests/test_os_shadow_contract.py` ran against the real kernel: (1) the client published
ONE constant cause text for every write of a verb, and the kernel shields, per member, the free text of whatever it forgets, so the
first forget made every later write of theirs "carries forgotten content"; the cause text now ends in a random tag
(`os_shadow.cause_text`). (2) A whole-person forget erases the member's record-only grant with the rest, and only the next daemon
start gave it back; `shadow_daemon.py` now installs it again at the member's next act (`ShadowServer.record_again`), not before, so
between the forget and that act nothing names them. What stays refused, by the kernel's own rule: the very item, note or title the
member had forgotten (`CONTENT_KEYS`), said again by them, is `kernel_refused_live_did reason=forgotten`. Because a row- or
turn-scoped forget in zoe-data forgets the member's WHOLE shadow record, every item they had added before it is refused if added
again: read `reason=forgotten` lines in the week's report as that rule, not as the kernel disagreeing with production.

**Verify**: add one item by voice, then `grep 'OS_SHADOW_ACT verb=list_add' ~/.zoe-logs/zoe-data.*  | tail -3` shows `result=match`.
**Read a week**: `python3 scripts/maintenance/os_shadow_report.py ~/.zoe-logs/zoe-data.*`.
**Roll back**: remove the `ZOE_OS_LIST_SHADOW` line and restart zoe-data (the hook becomes a flag read again); optionally
`systemctl --user disable --now zoe-os-shadow-kernel.service`. Leave `~/.zoe/os-shadow/` in place so a later member erase can still reach the kernel
(a forget while the unit is stopped is parked in `pending-forgets.json` and replayed), or delete the directory to erase the whole shadow record.

**Retiring the shadow for good** (the only way its record is removed without the kernel): with the flag `off`, a forget the kernel cannot
confirm FAILS (member delete included) for as long as shadow state exists, by design. zoe-data cannot prove the kernel process is stopped,
so it never deletes the store itself. The owner stops the kernel unit, then removes the whole shadow directory (a shadow copy is disposable;
this drops its credentials, roster and parked forgets together, which is correct once nothing is recorded any more):

    systemctl --user stop zoe-os-shadow-kernel.service
    rm -rf ~/.zoe/os-shadow

After that a forget is the flag-off no-op again (no shadow state).

## 8. Not done, not proven

* Not measured under real household load. The kernel hop number the brain-bridge record gives (about 0.75 ms
  p50 in the lab) is the lab's, not this client's [inferred].
* The kernel driver's list is in memory, has no dedupe and no list resolution: shadow checks exercise the record, receipts, availability,
  latency and forget reach, not that the kernel could replace the live list logic (dedupe, household/personal visibility, list resolution).
* Children are treated as adults in the shadow roster (no guardian model in `users`); not a consent model for anything authoritative.
* Reminder create, alarm create and the spoken list remove are shadowed too since the widening, each named in `ZOE_OS_SHADOW_VERBS`
  (default `list_add` only): see [zoe-os-integration.md](zoe-os-integration.md), which also carries the staged path to the authority.
