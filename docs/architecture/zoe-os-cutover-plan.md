# Zoe OS: the plan from the lab to the kernel in charge

**Status 2026-11-15: a plan. Nothing in it is live.** Owner direction, 2026-11-15: Zoe OS is fast-tracked and goes first in every
queue; new work that touches lists, reminders, alarms or consent is built kernel-first; the switch-over is staged, one action at a
time, never all at once.

This page is the whole programme in one place: what gets updated, upgraded, optimised and cut over, in what order, what each step
must prove first, and how it is undone. It does not replace the records it leans on:

- `docs/architecture/zoe-os.md`: what the kernel is and why (arrives with PR #2630).
- `docs/architecture/zoe-os-brain-bridge.md`: how the brain proposes and the kernel disposes (PR #2630).
- `docs/knowledge/zoe-os-integration.md`: the shadow stage in detail and the twelve things a production kernel lacks (PR #2631).
- `docs/ROADMAP.md` owns the order of all work; this page is the order *inside* Zoe OS.

Claims are labelled [lab] (run in the lab on the box), [live] (seen on the running system), [code] (read in the tree) or
[inferred]. A number without a label is a target, not a measurement.

## 1. Where it stands today

| Piece | State | Evidence |
|---|---|---|
| The lab kernel (events, consent, receipts, why, undo, forget, retention, sharing, twin, brain bridge) | One PR, #2630, in its final review | [lab] 447 tests |
| Real-brain run | Done twice with a synthetic member | [live read, lab process] list add 8/8, reminders 8/8, alarms 6/6, refusals 3/3, no false calls with the kernel's policy arm |
| Speed of one kernel call | Measured | [lab] about 1 ms for everyday actions; undo about 1 ms; forget about 2.6 ms |
| Crash safety of the record | Measured | [lab] 50 kills mid-write: nothing lost, nothing half-written |
| Forget scoped to the person; permission and refusal records forgotten | Built, in review (#2633, on #2634 and #2635) | [lab] 515 tests |
| Shadow hook for list add, list remove, reminders, alarms | Built, switched off, in review (#2622 to #2625, #2631, #2632) | [lab] 126 tests; nothing live |
| List add with the kernel in charge | Being built, switched off | none yet |
| Anything running in production | Nothing | n/a |

## 2. The rules every step follows

1. **One action at a time.** A verb moves only when its own numbers say so. No big-bang.
2. **Shadow before authority.** The kernel first watches a verb and its verdict is compared with what really happened. It controls
   nothing until the two agree.
3. **The old path stays as the fallback** until a verb has run under the kernel for a full release of the household's normal use.
   A dead, slow or confused kernel must never fail or stall a turn.
4. **A real refusal is honoured.** If the kernel says no for a policy reason (no permission, forgotten), that is the answer. The
   fallback is for faults, not for disagreeing with the rules.
5. **Forget reaches the kernel before the kernel holds anything.** No stage may leave a member's data somewhere "forget" and
   "delete this member" cannot reach.
6. **Measured before and after.** Each step names its number, takes it before, takes it after. Voice-path changes are
   replay-gated against the real-voice corpus.
7. **Proven, then switched on.** Built-but-off is not done. Flag flips, service units and restarts are the owner's and the
   landing session's; this plan says what must be true first.
8. **Night jobs are rehearsed by day** before any night setting changes.

## 3. The phases

Each phase lists what it delivers, what must be proven to start it, what proves it worked, and the one-line rollback.

### Phase 0. Land the lab (in progress)

- **Delivers:** the kernel and its docs on `main`, lab-only. Then the three stacked follow-ups: the load harness, the undo and
  forget speed-up, and the two forget decisions.
- **Proof to start:** done.
- **Proof it worked:** `pytest labs/zoe-os` green on `main`; nothing under `services/` imports it.
- **Rollback:** revert the merge. Nothing runs it.

### Phase 1. Shadow on, four verbs

- **Delivers:** every real list add, spoken list remove, reminder create and alarm create is also shown to a kernel process. The
  kernel records it and its verdict is compared with what production did. Production is still the only writer.
- **Needs first:** the shadow PRs landed; a kernel service unit installed; the member roster written; a voice replay (the hook sits
  on the voice path); the hook's cost on a turn re-measured (it must be a flag read and one queue put).
- **Owner steps:** install the unit, set the shadow switch and the verb list, restart. Exact commands arrive with the PR.
- **Proof it worked:** the per-verb report shows lines arriving, agreement near 100%, the kernel answering in under 10 ms at p90,
  and one deliberate "forget" and one demo-member delete seen to empty the kernel's record for that person.
- **Rollback:** remove the switch line and restart.

### Phase 2. Prove agreement quickly

Waiting two weeks for enough real traffic is slow. Compress it:

- **Synthetic day:** a scripted set of a few hundred varied list, reminder and alarm requests run through the live box with demo
  users, built from the same shapes the household uses (no real words). Includes the awkward ones: duplicates, a list that does
  not exist, a child speaker, a guest, a reminder in the past, two alarms.
- **Corpus replay:** the real-voice replay corpus run with shadow on, so real phrasings are compared too.
- **Twin rehearsal:** the household twin plays a week through the kernel as if it were in charge, to find rule conflicts before a
  person does.
- **Proposed pass mark for a verb (owner decision D1):** at least 200 compared actions, at least 99.5% agreement, every
  disagreement read and explained, kernel unavailable under 1%, p90 under 10 ms, and forget and member delete both seen to reach
  the kernel. Real household traffic still counts and keeps accumulating; the synthetic day is there so the proof does not wait on
  the calendar.

### Phase 3. First cut-over: list add

- **Delivers:** a list add is proposed to the kernel and the kernel's receipt decides. The existing list tables are still written,
  as the household's read model, so the panel, the app and every reader keep working unchanged.
- **Needs first (the gaps, for this one verb):**
  1. a durable list store in the kernel (today the lab's is in memory);
  2. the fallback, proven with a hung kernel, a dead kernel and a slow one;
  3. forget and member delete reaching the kernel, failing closed (a delete that cannot reach the kernel is retried, never
     reported as done);
  4. today's household rule for who may add to which list, kept exactly: nobody loses an ability they have now;
  5. a drift report comparing kernel state with the list tables;
  6. the service unit with memory limits that can never pressure the voice stack, a health check, and the kernel's database in the
     nightly backup;
  7. Phase 2's pass mark for list add; a voice replay.
- **Proof it worked:** a week of real use with zero drift, zero stalled turns, fallbacks counted and each one explained, list-add
  speed on a voice turn no worse than before (replay-gated), and "why did you add that?" and "undo that" answered from receipts.
- **Rollback:** the authority flag to `off`. The old path never stopped existing.

### Phase 4. Close the kernel's gaps for the next verbs

The kernel cannot yet say everything production can. Each of these is an upgrade to the kernel, built and tested in the lab, then
shadowed:

| Gap | Blocks | Upgrade |
|---|---|---|
| One alarm per person, no repeat rule | alarms | several alarms per person, repeat rules, snooze |
| No recurring reminders; no date-only; nothing past 7 days | reminders | recurrence, date-only at the household default time, long horizons |
| Reminder and alarm stores in memory | both | durable stores, crash-tested like the record |
| Children treated as adults in the shadow roster | everything authoritative | the guardian model for a child's permission (owner decision D7) |
| The kernel learns of members by hand | everything | member add and remove pushed to the kernel (owner decision D6) |
| Nothing calls the retention sweep | growth | a night job, rehearsed by day first (owner decision D5) |
| Kernel runs as the same system user as the skills | the trust boundary | its own system user, with the two file-permission tests repeated on the real box |

### Phase 5. The remaining cut-overs, in order

Each repeats Phase 1 to 3 for its own verb: shadow, pass mark, authority behind its own flag, a week clean, then the next.

1. **List remove** (smallest step after list add; shares its store).
2. **Reminders** (create, then snooze and done, then firing). The firing path is the first place the kernel decides *when to
   speak*, so quiet hours and the attention budget move with it.
3. **Alarms and timers.**
4. **Write receipts everywhere.** Today's receipts live in process memory. Every action that already records one also publishes it
   to the kernel, in shadow; after a clean week the "did I really do that?" guard reads the durable ones.
5. **Offers and anticipation.** Zoe's own suggestions ("want me to wake you earlier?") become kernel offers with a yes, a receipt
   and a per-person attention budget. This is built kernel-first: it is still dark, so there is no old behaviour to match.
6. **Notifications and the proactive engine:** one gate for anything Zoe says unprompted.
7. **Notes, journal, calendar publishing, people and contacts**, each mainly for the forget guarantee.
8. **Presence**, once the presence work it depends on exists.
9. **Memory, last and least.** It is the strongest part today. The kernel first only *observes* memory writes and forgets; it does
   not take them over without its own decision.

### Phase 6. Retire the duplicates

When a verb has run under the kernel for a clean release with the fallback never needed for a fault: remove the old permission
checks, visibility filters and receipt code for that verb. Retire by removing. This is where the simplification the whole idea
promised is actually collected, so it is tracked: lines and duplicate rule copies removed per verb.

## 4. Optimisation track (runs alongside, never blocks a phase)

| Target | Today | Goal | How |
|---|---|---|---|
| Everyday kernel call (list, reminder, alarm) | about 1.1 ms [lab] (was 0.7 ms before the forget work) | at or under 0.7 ms | trim the indexes added for forget; batch the receipt and consumption writes |
| A refused call | 0.85 ms [lab] (was 0.33 ms) | at or under 0.4 ms | refusal records written off the reply path |
| Forget, slowest 1% | about 73 ms [lab], cause unknown | under 10 ms | find it with the harness profile |
| Kernel hop seen from a voice turn | not measured | no measurable change in the replay gate | measure at Phase 1; the hook is one queue put |
| Tool-choice quality (plain brain) | list 7/8, reminders 7/8 [live read] | 8/8 without the policy arm | tool descriptions from the kernel's own schemas; the router learning from kernel receipts |
| Brain time per tool turn | about 0.9 s p50 [live read] | lower | fewer tools shown per turn, chosen by the kernel's permission view |
| Kernel memory | not measured over days | a fixed small ceiling in the unit | a week's resident size at Phase 1 |
| Database growth | about 16 MB per 20,000 calls [lab] | bounded | retention sweep; measure real volume at Phase 1 |

The load harness (`labs/zoe-os/bench_kernel.py`) is the instrument for the first three rows and is re-run on every kernel change.

## 5. What the household gets, and when

| After | New ability |
|---|---|
| Phase 1 | nothing visible; the owner gets a daily agreement report |
| Phase 3 | "why did you add that?", "undo that" and a receipt for every list add; a member's forget provably reaches lists |
| Phase 5.2 to 5.3 | the same for reminders and alarms; one place decides when Zoe may speak |
| Phase 5.4 | Zoe can never claim an action she did not take, across restarts |
| Phase 5.5 | suggestions that respect each person's attention, with a yes that is recorded |
| Phase 6 | fewer places a rule can be wrong; new skills plug into one set of rules instead of copying them |

## 6. Owner decisions (all nine decided 2026-11-15)

| # | Decision | Outcome |
|---|---|---|
| D1 | The pass mark for a verb | **Decided 2026-11-15 (owner accepted the recommendation):** the Phase 2 numbers, reached by synthetic day + corpus replay + real use, not by waiting 14 days |
| D2 | Kernel as its own service, or inside the main service | **Decided 2026-11-15:** its own service: a kernel fault must not take Zoe down, and it is the only way to a real trust boundary |
| D3 | Where the kernel's database lives | **Decided 2026-11-15 (owner: "option a"):** its own database file (never tables in the main database: only the kernel process opens it, which is what makes a receipt trustworthy), in a permanent location beside Zoe's other data rather than the experiment folder, and in the nightly backup. The exact path is settled when the service unit is written |
| D4 | Back it up | **Decided with D3:** yes, in the nightly backup from the day shadow goes on; it holds reminder notes, which are words |
| D5 | Who runs the retention sweep | **Decided 2026-11-15:** the night window, after a daytime rehearsal |
| D6 | How the kernel learns of members | **Decided 2026-11-15:** the main service pushes add and remove; the kernel never reads the member table itself |
| D7 | A child's permissions | **Decided 2026-11-15:** a guardian grants on the child's behalf; until decided, kernel authority falls back to today's rule for children |
| D8 | May "learn this again" lift a forget | **Decided 2026-11-15:** blocked in the code today (fail closed) until built; allow it only as a deliberate, recognised request from the same person, for phrases; never automatically |
| D9 | Sold boxes | **Decided 2026-11-15:** every phase must work with no GitHub and no cloud: the kernel, its tests and the harness run offline |

## 7. Risks, and what answers each

| Risk | Answer |
|---|---|
| The kernel slows or stalls a voice turn | strict timeout, fallback, replay gate at every cut-over |
| Two sources of truth drift apart | one read model, a drift report, zero drift as an exit proof |
| A forget misses the kernel | fail-closed erase reach; the all-tables "nothing names the person" check runs in tests and after a live demo-member delete |
| Memory pressure on a tight box | unit memory ceiling; a week's measurement before authority |
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
| Nightly backup and restore drill | The kernel's database is in no backup job (decided: add it) and no restore drill | Phase 1 |
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
| Routines (#2522, #2523, #2614) | a new store of household actions built straight on tables: it should publish receipts to the kernel from day one, and its "done / skip / snooze" taps are natural kernel actions with undo |
| Anticipation engine (#2588 to #2592) | still dark, so build its offers, answers and cooldown as kernel offers with the per-person attention budget rather than its own store |
| Presence (#2593 onward) | publish presence to the kernel's person-and-presence service instead of a parallel record |
| Notifications: web push (#2512) and phone delivery | "may Zoe speak or send now" should become one kernel gate, not a third copy of quiet hours |
| Named guests; lists private until shared; the fridge rule | the kernel has no guest and no household-owned list yet, so these rules must be modelled in the kernel before lists leave fallback for those cases |
| Recipe cards | "add the ingredients to the shopping list" goes through the kernel action; cards are views over receipts |
| Web lookup (#2527 to #2530) | the one door out of the house is a capability: its grant belongs in the kernel's consent model |
| Not affected | the reflex, the medical and distress floors, the speed loop, the scrubber |

## 9. What is in flight right now

- Final review of the lab PR (#2630); then the harness (#2634), the speed-up (#2635) and the forget decisions (#2633).
- The shadow stack in review (#2622 to #2625, #2631, #2632).
- List add under kernel authority being built, switched off (Phase 3 items 1 to 5).
- Next to start: the synthetic day for Phase 2; the kernel upgrades for alarms and reminders (Phase 4); the everyday-call speed
  regression (section 4).
