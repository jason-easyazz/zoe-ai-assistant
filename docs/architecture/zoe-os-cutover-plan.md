# Zoe OS: the plan from the lab to the kernel in charge

**Changed 2026-11-18 (owner decisions):** After train #2631, areas move directly to the kernel behind one fall-back flag each. There are no further shadow stages or week-clean gates. The kernel decides and records; existing stores keep the data. Lists come first, followed by reminders, alarms and timers, routines, calendar and the remaining tools. Clean each area as it moves.

**Status 2026-11-18: a plan.** The first watch-only run ended for the train deploy. Move one area at a time as described below.

This page is the whole programme in one place: what gets updated, upgraded, optimised and cut over, in what order, what each step
must prove first, and how it is undone. It does not replace the records it leans on:

- `docs/architecture/zoe-os.md`: what the kernel is and why (arrives with PR #2630).
- `docs/architecture/zoe-os-brain-bridge.md`: how the brain proposes and the kernel disposes (PR #2630).
- `docs/knowledge/zoe-os-integration.md`: the shadow stage in detail and the twelve things a production kernel lacks (PR #2631).
- `docs/architecture/zoe-os-implementation-map.md`: the engineering companion: every production seam, the kernel's gaps, what
  around it must change, and the work packages.
- `docs/ROADMAP.md` owns the order of all work; this page is the order *inside* Zoe OS.

Claims are labelled [lab] (run in the lab on the box), [live] (seen on the running system), [code] (read in the tree) or
[inferred]. A number without a label is a target, not a measurement.

## 1. Where it stands today

On 2026-11-18 the kernel first ran from main in watch-only mode. Three demo list adds matched in 3 to 12 ms. It was turned off for the train deploy. The rehearsal matched 300 of 300 adds; see [the run record](../knowledge/zoe-os-list-shadow.md).

## 2. The rules every step follows

1. Move one area at a time, directly behind one fall-back flag. After train #2631 there are no more shadow stages or week-clean gates.
2. The kernel decides and records. Data stays in its existing store. Each area has one door in zoe-data: it asks the kernel, which answers and records who and which turn; zoe-data changes the store and reports the outcome.
3. A kernel refusal is final. If unreachable, the change proceeds and is recorded as not checked for later. Decide separately for any area where a wrong yes can do harm.
4. Measure added delay and replay-gate voice changes. The coordinating agent runs switch-overs; landing and merging follow their existing gates.
5. A new local store for real household data must stay on this box with no network path. Forget reach is not a precondition on this development box. Never commit personal data.

This design needs no import, leaves readers unchanged and loses no data when the flag falls back. The lists piece is hundreds of lines, compared with an estimated 1,350 to 2,400 before tests to move its data into the kernel.

## 3. The phases

1. Finish train #2631 and its live checks. Fix the database growth and restart recovery faults in [open problems](../knowledge/open-problems.md).
2. Move lists: one door in `list_service`, kernel verbs and rules, `ZOE_OS_LISTS=kernel`, live checks, forget by record and cleanup. Name the service `zoe-os-kernel` and its state folder `~/.zoe/os`; remove the shadow-named unit, flag and folder.
3. Move reminders, including the fixes from parked #2653; then alarms and timers.
4. Build routines on the kernel from the start. The old-style routines store PRs are parked.
5. Move calendar, then remaining tools one at a time: notes, journal, people and contacts, memory, music, home control, weather and web lookup, and messages.

An area is done when every change uses one door; every ability passes on the live box through the kernel with a demo user; forget of a turn and a person works from the kernel record; added delay is measured; and side doors are deleted. Remove dead code, duplicate paths, stale flags, unused tables and stale docs in the same work, and tick the area's rows in the [clean-house register](zoe-os-clean-house.md).

Rollback is the area's flag. Its data remains in the existing store.

## 4. Optimisation track (runs alongside, never blocks a phase)

| Target | Today | Goal | How |
|---|---|---|---|
| Everyday kernel call (list, reminder, alarm) | about 1.1 ms [lab] (was 0.7 ms before the forget work) | at or under 0.7 ms | trim the indexes added for forget; batch the receipt and consumption writes |
| A refused call | 0.85 ms [lab] (was 0.33 ms) | at or under 0.4 ms | refusal records written off the reply path |
| Forget, slowest 1% | about 73 ms [lab], cause unknown | under 10 ms | find it with the harness profile |
| Kernel hop seen from a voice turn | not measured | no measurable change in the replay gate | measure at each area on the live path |
| Tool-choice quality (plain brain) | list 7/8, reminders 7/8 [live read] | 8/8 without the policy arm | tool descriptions from the kernel's own schemas; the router learning from kernel receipts |
| Brain time per tool turn | about 0.9 s p50 [live read] | lower | fewer tools shown per turn, chosen by the kernel's permission view |
| Kernel memory | not measured over days | a fixed small ceiling in the unit | measure resident size |
| Database growth | about 16 MB per 20,000 calls [lab] | bounded | retention sweep; measure real volume |

The load harness (`labs/zoe-os/bench_kernel.py`) is the instrument for the first three rows and is re-run on every kernel change.

## 5. What the household gets, and when

Each finished area has one decision door and a record of its outcomes. The area order and completion checks are in section 3.

## 6. Owner decisions (all nine decided 2026-11-15)

| # | Decision | Outcome |
|---|---|---|
| D1 | The pass mark for a verb | **Superseded 2026-11-18:** no shadow pass mark or week-clean gate after the train; use the area completion checks in section 3 |
| D2 | Kernel as its own service, or inside the main service | **Decided 2026-11-15:** its own service: a kernel fault must not take Zoe down, and it is the only way to a real trust boundary |
| D3 | Where the kernel's database lives | **Decided 2026-11-15 (owner: "option a"):** its own database file (never tables in the main database: only the kernel process opens it, which is what makes a receipt trustworthy), in a permanent location beside Zoe's other data rather than the experiment folder, and in the nightly backup. The exact path is settled when the service unit is written |
| D4 | Back it up | **Decided with D3:** yes, in the nightly backup from the day shadow goes on; it holds reminder notes, which are words. Backups keep their data and a restore brings back everything, including things forgotten since (owner, 2026-11-16): backups and restores are the stated exception to permanent forgetting, so no backup-safe forget is owed |
| D5 | Who runs the retention sweep | **Decided 2026-11-15:** the night window, after a daytime rehearsal |
| D6 | How the kernel learns of members | **Decided 2026-11-15:** the main service pushes add and remove; the kernel never reads the member table itself |
| D7 | A child's permissions | **Decided 2026-11-15:** a guardian grants on the child's behalf; until decided, kernel authority falls back to today's rule for children |
| D8 | May "learn this again" lift a forget | **Decided 2026-11-15:** blocked in the code today (fail closed) until built; allow it only as a deliberate, recognised request from the same person, for phrases; never automatically |
| D9 | Sold boxes | **Decided 2026-11-15:** every phase must work with no GitHub and no cloud: the kernel, its tests and the harness run offline |

### Further owner decisions (2026-11-15, from the implementation map's questions)

| Question | Decision |
|---|---|
| Does removing an account erase that person's data | Yes: everything, **except** what another member explicitly kept (a "keep it", or a yes to an invitation); someone who only added to a shared list or event keeps their own additions. The administrator is asked at removal: "Do you want to delete all shared content?" (owner, 2026-11-16). No record means delete; a record that cannot be read means stop and retry. The Settings delete button must run the full erase, not only remove the sign-in account. The author and kept-by records have to exist in the data model before anything can be kept; until then nothing is kept on a guess |
| Does a spoken "remove X from the list" tick the item or delete it | Delete it. The paths that tick it are wrong for that request |
| How does the kernel learn who lives in the house | Automatically, from the accounts Zoe already has. The owner keeps no second list |
| Where should the kernel live, and how much should go through it | Open: an architecture study is under way. The owner's expectation is that most things go through the kernel; the study must state the boundary and justify every exception |

### Owner choices from the architecture study (decided 2026-11-15: the recommendations were accepted)

Record: `docs/architecture/zoe-os-architecture.md` (its section on the owner's choices).

| Choice | Decision |
|---|---|
| The kernel is briefly down after an action's old rule code has been removed | Superseded 2026-11-18: the change proceeds and is recorded as not checked; decide separately where a wrong yes can do harm |
| Devices that need a spoken yes every time, and do nothing while the kernel is down | Door locks, garage doors, the security alarm, and anything that heats |
| What a guardian can see and manage | A guardian is a carer, for a child or an adult in care (owner, 2026-11-16). **Organising:** that person's reminders, alarms, lists, routines and calendar, unless an item is marked private; not their private notes or journal; organising never includes forgetting, undoing or explaining that person's data. **Memory care**, a separate power: correcting or removing what Zoe has remembered about that person, and fixing a fact in place. Its limits: it is a named action set on the care link and never implied by organising; every use is recorded with who did it and why; the model can never propose it (only the carer, verified, can ask); it never opens private notes or a journal |
| Changes to the kernel's own code, its "what the kernel owns" table and its budgets | Need a one-tap approval from the owner, even after clean reviews |
| Things found in pendant recordings, messages, calendars or web pages | Suggestions by default; a per-person "act on my pendant" switch; never automatic for web pages. The pendant itself needs new firmware and a phone app before it is useful: later work, not part of this programme |

### Decisions taken from the prior-art review (2026-11-15; engineering decisions, not owner questions)

Record: `docs/research/zoe-os-prior-art-2026-11-15.md`. These are adopted and become work before the first authority flag goes live.

| Decision | Why | When |
|---|---|---|
| Two durability levels, stated: everyday actions survive a killed process; permissions, forgets and receipts for consequential actions are also synced to survive a power cut, after measuring the cost on this box | the database's normal sync level can lose a committed write on power loss; the one-line rule over-promised | before Phase 3 |
| Every proposal carries an idempotency key, and the fallback path carries the same key | after a timeout the old path writes; if the kernel then commits late, the same item could be written twice | before Phase 3 |
| The kernel resolves names to ids and checks ownership itself; it never trusts the model's arguments or matches patterns over free text | the model's arguments can come from text it read | Phase 3 onward |
| Permissions are small objects with a parent, an expiry and a narrowing; revoking a parent revokes its children; the last adult can never be locked out | guardians, guests and "just for tonight" need it | with the children and guests model |
| Every driver declares whether it is safe to repeat, readable, or fire-and-forget; actions are absolute ("set to 21"), never relative | makes "uncertain" resolvable by reading back | Phase 4 |
| Add a schema version and an origin field to stored events now | cheap now, expensive later | with the next kernel change |
| No tamper-evidence chain over readable text | a hash chain over text would fight forget | noted |

## 7. Risks, and what answers each

| Risk | Answer |
|---|---|
| The kernel slows or stalls a voice turn | strict timeout, fallback, replay gate at every cut-over |
| Two sources of truth drift apart | No longer applies: area data stays in one existing store |
| A forget misses the kernel | fail-closed erase reach; the all-tables "nothing names the person" check runs in tests and after a live demo-member delete |
| Memory pressure on a tight box | unit memory ceiling and measured memory use |
| The plan outruns review | small stacked PRs; one cut-over in flight at a time |
| The brain picks the wrong tool | the kernel refuses what is not permitted; quality tracked per verb in the report |

## 8. What else has to change around the kernel

The kernel does not land in a vacuum. These existing tools and open changes need adjusting; each is tied to the phase that needs it.

**Tools and pipelines**

| Tool | What is wrong today | Needed by |
|---|---|---|
| CI (`validate`) | [code] The kernel's own tests are not run by CI: `pytest.ini` collects only `services/zoe-data/tests`, the lab tests carry no `ci_safe` marker, and no workflow job names `labs/zoe-os`. They run only when someone runs them by hand | before Phase 1 |
| Where the kernel lives | It is under `labs/`, which the repo defines as isolated from the runtime. A process production depends on must move to `services/` (or be deployed from the lab by an explicit, reviewed step, as the brain app already is) | before Phase 3 |
| Deploy | [code] `deploy.yml` restarts the main service only; it does not install, update or restart a kernel, so a kernel would keep running old code after a deploy | before Phase 1 |
| Voice replay gate | The regression probe has no kernel. With the authority flag on it would only ever exercise the fallback. It needs a kernel in the probe's environment and a said-versus-did check for a kernel-decided add | before Phase 3 |
| Ground truth script | [code] `zoe_ground_truth.sh` reports nothing about the kernel: unit state, shadow and authority flags, last agreement figures | Phase 1 |
| Nightly backup and restore drill | The kernel's database is in no backup job and no restore drill. Decided: add it (D4) | Phase 1 |
| Night window | The orchestrator does not know the kernel: whether it stops, when its retention sweep runs, and that neither collides with the brain window | Phase 4 |
| Memory protection | The kernel unit needs a memory ceiling and must never pressure the voice stack | Phase 1 |
| Forget and text inventories | The kernel's stores must be listed with their reach (the shadow PR lists its store; the authority and later stores must follow) | each phase |
| Felt scorecard | No rows yet for agreement, fallbacks, receipts, undo and forget through the kernel | Phase 1 |
| Self-heal and health checks | Nothing watches the kernel or knows how to restart it | Phase 3 |
| Review cost controller | Kernel paths are not in the load-bearing path list that decides which changes get the whole-repo review | Phase 3 |
| First-boot and sold-box setup | A new box must create the kernel's user, database, roster and unit, offline | before a box is sold |
| De-root programme | The kernel's own system user belongs in the same programme as the other services' | Phase 4 |

**Open changes that should be reshaped**

| Change | Adjustment |
|---|---|
| Member delete everything (#2598) | must call the kernel's member erase, failing closed, beside the shadow forget |
| Routines (#2522, #2523, #2614) | Old-style store PRs are parked; build routines on the kernel from the start |
| Anticipation engine (#2588 to #2592) | still dark, so build its offers, answers and cooldown as kernel offers with the per-person attention budget rather than its own store |
| Presence (#2593 onward) | publish presence to the kernel's person-and-presence service instead of a parallel record |
| Notifications: web push (#2512) and phone delivery | "may Zoe speak or send now" should become one kernel gate, not a third copy of quiet hours |
| Named guests; lists private until shared; the fridge rule | the kernel has no guest and no household-owned list yet, so these rules must be modelled in the kernel before lists leave fallback for those cases |
| Recipe cards | "add the ingredients to the shopping list" goes through the kernel action; cards are views over receipts |
| Web lookup (#2527 to #2530) | the one door out of the house is a capability: its grant belongs in the kernel's consent model |
| Not affected | the reflex, the medical and distress floors, the speed loop, the scrubber |

## 9. What is in flight right now

See `docs/ROADMAP.md` for the current order and `docs/PLANS.md` for status.
