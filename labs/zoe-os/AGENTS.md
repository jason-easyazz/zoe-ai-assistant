# labs/zoe-os — the Zoe OS kernel

## Purpose

One small process that owns the household's hard rules so that no feature carries its own copy: who may do what (consent), what
was done (receipts), why, undo, and forget. Features propose; the kernel decides and records. Design record:
`docs/architecture/zoe-os.md`; the brain's side: `docs/architecture/zoe-os-brain-bridge.md`; the programme, phases and owner
decisions: `docs/architecture/zoe-os-cutover-plan.md`.

It lives under `labs/` while production moves onto it one action at a time. It is not throwaway: treat every change here as a
change to something production will depend on.

## Ownership

The owner decides what the kernel may decide (the cut-over plan records each decision). Builders change the kernel through
reviewed pull requests. The landing session and the owner are the only ones who install its unit, set its flags or restart it.

## Local Contracts

These are the rules the kernel exists to guarantee. A change that weakens one is not a refactor; it needs an owner decision
recorded in the cut-over plan.

1. **The brain proposes, the kernel disposes.** Nothing outside the kernel process writes its record. Callers hold a socket and
   a credential, never the database.
2. **The wire is the only seam.** `services/` never imports this package. Wire changes are additive: an existing client keeps
   working unchanged.
3. **No claim without a receipt.** An action is recorded with a verified receipt or it did not happen. One receipt backs one
   claim, and a spent receipt stays spent across forget, retention and restart.
4. **Durable or not acknowledged, and honest about which kind.** An acknowledged write survives the kernel being killed at any
   point; an unacknowledged one is absent or marked uncertain, never half there. Surviving a POWER CUT is a stronger promise
   that depends on how the write is synced: it is given only for the writes that are declared to need it (permissions, forgets,
   receipts for consequential actions), and the record says which. Speed is never bought by quietly weakening either.
5. **Forgotten means forever.** A forget reaches every copy: events, derived text, undo snapshots, intents, grant and refusal
   records, subscriber and skill stores, and anything a later action or undo would write back. The shield is scoped to the
   person who forgot. The kernel orders a skill's store purge before the permission that would allow it is erased. When the
   kernel cannot tell what to erase, it refuses; it does not confirm.
6. **One member's data never reaches another** except through a grant the kernel holds.
7. **Authorised display is not a diagnostic.** What the kernel returns to a reader it has authorised may contain that reader's
   content. Anything a tool prints, logs or writes, and any endpoint without the token, carries codes, ids and counts only.
8. **An erased permission authorises nothing.** Consent is read from the live record every time it matters.
9. **Cost does not grow with history.** Everyday actions, undo and forget do bounded work; the step-budget tests pin it.
10. **It works alone.** No network, no cloud, no code-hosting site is needed to run or test it. Pinned in tests: `conftest.py`'s autouse `works_alone` fixture refuses (and fails the test on) any non-loopback socket connect/sendto in this process; `tests/test_works_alone.py` is its negative control.

## What the kernel owns in production

Nothing yet. This table is the single place that says which actions production must write through the kernel; a row is added
in the same change that makes the kernel the authority for that action, and never before.

| Action | State | Flag | Since |
|---|---|---|---|
| list changes (`list.create` / `rename` / `delete` / `share`, `item.add` / `remove` / `tick` / `edit`): the kernel DECIDES and records ids; the rows stay in production | built, default off (not live) | `ZOE_OS_LISTS=kernel` | built 2026-11-19 |
| everything else | lab only | n/a | n/a |

## How a feature moves onto the kernel

Owner direction 2026-11-15; phases, proofs and decisions are in `docs/architecture/zoe-os-cutover-plan.md`.

- **New features** that write a household action reuse the existing permission, visibility, quiet-hours and receipt code (one
  thing to move later) until the kernel owns that action; then they write through the kernel. A feature that is still dark
  and has no behaviour to preserve may be designed against the kernel's model from the start, in the lab.
- **Existing actions** move one at a time, in this order: shadow (the kernel is shown each real action and its verdict is
  compared; it controls nothing), the pass mark for that action, authority behind its own default-off flag with the old path as
  the fallback, then the duplicate rule code is removed.
- A kernel fault (down, slow, unsure) never fails or stalls a turn: the old path runs and the fallback is counted. A real
  refusal by the kernel is honoured and is FINAL: "no permission" and every forget-class refusal (the text or name was
  forgotten, or the request derives from something forgotten) are never treated as "kernel unavailable" and never fall back,
  because the old path would write the forgotten thing straight back. Every client of the kernel has a test that plants
  exactly that case.
- Forget and member delete reach the kernel before the kernel holds anything of a person.
- Flags, units and restarts are the owner's and the landing session's.

## Forbidden

- Importing this package from `services/`, or reaching its database from anywhere but the kernel process.
- Shipping a behaviour without a test that fails when the behaviour is removed (a negative control). A control that cannot fail
  is not a control.
- Marking a test's expectation "updated" to make it pass without saying which owner decision or which stronger guarantee
  justifies it.
- Loosening a refusal to make a test pass before establishing whether the refusal was right.
- Printing, logging or writing event text, arguments or reasons from a tool, harness or report.
- Real names, places, utterances, device ids or addresses in code, tests, fixtures or word lists. Invented content only.
- Process-environment names that start with the product's flag prefix; lab settings use their own prefix.
- Installing or enabling a unit, setting a flag, restarting a service, or sending traffic to the running assistant from here.
- Raising a step budget, a size bound or a timeout to make a change fit, without the measurement and the reason in the PR.

## Work Guidance

- Failing test first, then the fix, then the old-code control. Fix the class, not the instance: when a fault is found in one
  place that stores or echoes content, sweep every other place that does.
- Keep changes surgical; other branches are usually stacked on this one.
- Measure with `bench_kernel.py` before and after any change to a hot path, and put the numbers in the PR.
- The whole suite starts processes: it is a heavy run. On the box it runs in a memory window, one run at a time.
- A finding on the lab is fixed in place when it lets text or a credential leave the kernel process, breaks rule 5, or breaks
  rule 6; otherwise it may be deferred to a named follow-up that lists it.

## Verification

- `python3 -m pytest -p no:cacheprovider -q labs/zoe-os` (the second-OS-user tests need root and are skipped without it).
- `python3 labs/zoe-os/bench_kernel.py --mode all --seed 7 --out <file>` for latency, concurrency, soak, kill-and-restart and
  cold start (arrives with the load-harness change).

## Child DOX Index

None.

- `zoe_os/shadow_state.py` is the kernel side of the shadow state directory's SETTLE rule (a new directory is made empty, marked, and waits before it holds data; data with no trustworthy marker is refused). Its block between `# >>> settle` and `# <<< settle` is the SAME TEXT as the one in `services/zoe-data/os_shadow.py`: change both together (`services/zoe-data/tests/test_os_shadow_settle.py` fails otherwise). `shadow_daemon.py` must call `shadow_state.require_settled(state)` before it reads or writes anything in the state directory.

- `shadow_daemon.py`: a roster member whose whole record was forgotten gets their record-only grant back at their NEXT act (`ShadowServer.record_again`), never at the forget itself (nothing may name them in between) and never for someone off the roster. Pinned by `tests/test_shadow_daemon.py` and, from the client side, `services/zoe-data/tests/test_os_shadow_contract.py`.

- `shadow_daemon.py`: **the roster follows `people.json` with no restart** (`ShadowServer.sync_roster`, under the kernel lock, before each `list_ask` / `door_ask` and their reports (the watch-only `publish` and `act` keep the roster as loaded); one `stat`, and the file is opened only when its inode, size or modification time moved). `apply_roster` makes the kernel's people exactly what `build` would make them from the same file, so a restart and a re-read never disagree: a new member is known with the role STATED and gets the record-only grant; a changed role takes effect; `person_from_entry` is the only reader (an entry that does not fully say otherwise is least access); a member taken off the file has their grant revoked and is least access, known and forgettable while the kernel holds a record for them and nobody once it holds none; a member whose whole record was forgotten is not granted by a re-read (only by their next act, `record_again`); the spine's member index (`Spine._members`, `_backfill_nested`) takes in a new member, older rows included. A file that is missing or unreadable changes nothing. A new wire op that decides or records by person calls `sync_roster` first. Pinned by `tests/test_roster_sync.py`.

- `zoe_os/door.py` is THE door: one mechanism for every area (what is recorded, ids only, the order against a forget, the unknown-owner rule, ask and report in one transaction each). An area is an `Area` (name, verbs, its `decide` table, the record keys for the thing and its parts) handed to `Door` in `Kernel.__init__`; a verb belongs to exactly one area. On the wire: `door_ask` (`area`, `verb`, `person`, `target`, `parts`, `owner`, `visibility`, `turn`, `stamps`) and `door_report`; `list_ask` / `list_report` are the lists area's older spelling of the same two and stay served (same record). A new area adds a table module and a test per row, never a second door. Pinned by `tests/test_door_areas.py`.

- `zoe_os/remind_door.py` is the reminders AREA of the door (`door_ask` / `door_report` with `area="reminders"`): the permission table is in its module docstring and each row has a test in `tests/test_reminders_door.py`. A reminder belongs to the person it is for; `household` = addressed to the house. It stores ids only: never a title, a day or a time (the wire has no field for one). A new reminder verb goes in `remind_door.VERBS`, the table and a test together; the daemon's brain manifest names every door verb (`shadow_daemon.SHADOW_BRAIN`).

- `zoe_os/lists.py` is the lists AREA of the door (`list_ask` / `list_report` on the wire): the permission table is in its module docstring and each row has a test in `tests/test_lists_door.py`. It stores ids only: never an item's text, a list's name, a snapshot, or a row per existing item (the growth test pins rows per act and zero `snapshot_dependencies` rows). Its rules are household rules by role, not a skill's consent; a new list verb goes in `lists.VERBS`, the table and a test together.

- **A caller's late write never lands after a person's forget (`tests/test_lists_erase_order.py`).** An ask naming a list OWNER who is not on the roster is refused `unknown_person` and the owner's id is never written (only a known person can be forgotten or carry an erase mark). A caller whose writes and forgets travel on different connections sends an erase stamp `[boot, generation]` (`spine.erase_stamp`; compared as a pair) with each: `forget(stamp=)` on a WHOLE-person forget keeps the newest one as that person's erase mark (`erase_stamps`, keyed by the person's keyed hash `Spine._owner` like the forget ledger, written in the forget's own transaction, so it survives a restart and names nobody); `list_ask(stamps=)` / `list_report(stamps=)` give one per member named. Under the kernel lock, an ask whose stamp for the acting person or the list's owner is not STRICTLY newer than their mark (equal counts as before the forget: the caller moves its count on only once the forget is confirmed) writes nothing that names anyone: an allowed one is refused `erased` (a bare refusal event), a refused one keeps its reason, unnamed. Equal or newer is recorded (a forgotten member who acts again is recorded afresh). No stamp for a person who HAS a mark counts as older; a forget without a stamp sets no mark (an older caller behaves exactly as before). Scoped forgets (entity, topic, seqs) set no mark: these writes hold ids only. A report after a forget has nothing to close (the forget removes every open ask naming the person, as actor or as owner) and a stale stamp on it is refused the same way. Any NEW wire op that records something naming a person, sent on a connection other than the one forgets use, takes `stamps` and checks `Spine.erased_after` under the lock before it writes.
