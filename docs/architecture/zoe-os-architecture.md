---
type: architecture
title: Zoe OS - where the kernel sits, what goes through it, and what never does
date: 2026-11-15
status: decision record from an architecture study. Nothing here is built or live. It decides the open engineering questions of
  the cut-over plan and leaves the owner five household choices (section H).
related: zoe-os-cutover-plan.md, zoe-os-implementation-map.md, zoe-os.md, zoe-os-brain-bridge.md, p0-deroot.md, credential-vault.md,
  ../research/zoe-os-prior-art-2026-11-15.md
---

> **Status 2026-11-15: a proposal, reviewed, partly adopted.** An independent review (a different vendor's model, reading the code)
> returned "adopt with changes; do not authorise the proposed rework as written". What is ADOPTED: the boundary (the kernel decides
> actions and records; data streams never pass through it; reads follow its rules), its own service, its own system user, a home
> under `services/`, standard-library Python with a size budget, only the main service calling it, and the owner's five choices
> (recorded in `zoe-os-cutover-plan.md`). What is NOT adopted yet: **"the kernel keeps decisions, not content"**. It stays the
> intended direction, but the stores already built (rows rebuilt from the kernel's own verified receipts) give guarantees the
> replacement has not specified, so they are kept for the first cut-over. Before any rework, a complete action protocol must be
> written and reviewed: the caller allocates the action id; one transaction in the main database records the change, the id, the
> version acted on and the before-state reference; settlement is deduplicated; a stale approval cannot cross a forget or a consent
> change; historical "why" and undo work from versions, never from today's row. Other corrections taken from the review: a
> "forgotten" refusal is a real refusal and never falls back (fixed in the authority client); cached read rules need a revocation
> protocol with a stated staleness bound; text the model merely read needs provenance carried with the data, not a label on the
> turn; account removal keeps an item only on an EXPLICIT acceptance ("keep it", or an accepted invitation), a contribution keeps
> only the contributor's own material, missing evidence means delete, UNREADABLE evidence means stop and retry; the removal job
> persists a barrier before deactivation and keeps it afterwards; reminders and alarms already set still fire while the kernel is
> down (only changes are refused); the kernel's file is NOT low-sensitivity (schedules and relationships are in it) and is backed
> up and protected like any other household data; the one-millisecond figure is withdrawn for any design with a second call until
> measured.

# Zoe OS: the architecture decision

The owner asked for a software architect's deep dive on where the kernel should be, and asked: "shouldn't most things go
through the kernel?" This page answers both, decides every engineering question the plan and the implementation map left open,
and leaves the owner only the choices that are about his household (section H).

Labels: **[code]** read in the tree (path given), **[lab]** measured in the lab by earlier work and reported in the records named,
**[web]** read on the page cited, **[inferred]** my reasoning, **[unverified]** believed but not confirmed. Nothing was run for this
page and nothing live was read or changed.

## The two-minute summary

**The short answer to "shouldn't most things go through the kernel?" Yes: most *decisions* do. No *data stream* does.** Every
time Zoe changes something in the house, shares something, speaks or sends without being asked, lets something leave the house,
or forgets, the kernel decides and keeps the receipt. The sound of voices, the model's words as they are generated, the screens,
and the bulk of reading memories do not pass through it. They follow the kernel's rules without asking it each time. A kernel in
those streams would add delay to every word, make Zoe deaf and mute whenever it hiccups, and turn a small trusted program into a
second copy of everything. That is how a "kernel" stops being trustworthy.

| Question | Decision | Why, in one line |
|---|---|---|
| What goes through the kernel | Anything that changes the household's state, shares, speaks or sends unprompted, leaves the house, changes consent or membership, or forgets: the kernel decides (about 1 ms). Memory writes, timers, settings changes and kernel upgrades are reported to it as a record. Audio, the model's words, screens and memory reads never pass through it | decisions are rare and cheap to check; streams are constant and gain nothing from a check |
| Reads of a person's data | Decided by the kernel's rules, enforced by ONE function in the main service that a test forbids bypassing; no per-read call | a check on every read would make the kernel a data pipe; one rule copy is what we actually need |
| What the kernel keeps | Decisions, consent, who lives here, receipts, and fingerprints of words, never the words themselves. Lists, reminders and the rest stay in the main database | one home per fact; a forgotten word cannot hide in a second copy; the kernel's file stays small and low-risk |
| Where it runs | Its own small service, as decided, started before the main service and never required by it | a kernel fault must never take Zoe down |
| Where its code lives | `services/zoe-os/`, beside the credential broker, moved out of the experiment folder before it decides anything for real | the experiment folder is by rule "never part of the running system" |
| Who it runs as | Its own system user before it decides anything; the main service on its own user before the old rule copies are deleted | today every coding agent runs as the same user as Zoe and could open the kernel's file |
| Language | Stay in Python, standard library only, with a size and dependency budget enforced by a test | the cost is the disk sync, not the language; 500+ proven tests would be thrown away |
| Who may talk to it | Only the main service (proved by the operating system, not a password), plus a narrow maintenance key for backups and the night sweep. Not the brain app, not the panel, not coding agents | the component that reads web pages and messages should hold the fewest keys |
| Text the model read | Anything that came from a web page, a message, a pendant recording or an imported calendar can only make a suggestion, never act, unless that person turned on "act on it" for that source | stops a web page from adding to your list or sending your data out |
| Removing an account | Put up the barrier, deactivate, erase, then delete the sign-in. Keep only items that sit in someone else's list or calendar, or that another member explicitly accepted (tapped "keep" or replied yes); a member who only added to it keeps their own additions. With no record, delete; if the record cannot be read, stop and retry. The administrator is asked whether to delete shared content | matches the owner's rule, and nothing is kept on a guess |
| Who lives here | Read automatically from the sign-in accounts and the child settings Zoe already has, every minute and at once on changes Zoe makes. A missing account suspends; it never erases | the owner keeps no second list, and erasure is never triggered by an absence |
| "Remove milk from the list" | Deletes, on every path. "Tick off milk" stays its own action and ticks | the owner's decision, applied to the two paths that tick today |
| Timers | Reported to the kernel, not decided by it | a cooking timer must work even if the kernel is restarting; it shares nothing and is gone in minutes |
| Children and guests | A child is anyone marked child in either place; guardians manage a child's permissions; guests and "the household at the panel" are their own kinds of principal with narrow, fixed rights | deny wins; unknown means least privilege |
| Upgrades | Additive wire changes; a schema number and expand-then-contract migrations; every upgrade replays recent decisions on a copy and must agree before it is deployed | the household's rules must not change by accident during an upgrade |

**What changes in the plan, first three steps:** (1) fix account removal now: one "remove member" action that erases before it
deletes the sign-in, with author and acceptance records added to the database; (2) finish joining the scattered write paths into
one function per action (list add, list remove = delete), because every kernel step depends on it; (3) make the kernel fit the charter (A.7: people, care and sharing, with household words in a settings file) and move it to
`services/zoe-os/` under its own user, before any action goes under its authority. Re-cutting it to keep decisions and
fingerprints rather than words is the intended direction but is DEFERRED until its action protocol has been written and reviewed
(status note above); it is not a prerequisite for the first cut-over.

---

## A. What goes through the kernel

### A.1 The principle, and why "every access" does not mean "every byte"

The standard rule is Saltzer and Schroeder's **complete mediation**: "Every access to every object must be checked for
authority" [web: https://www.cs.virginia.edu/~evans/cs551/saltzer/, section I.A.3]. The reference-monitor idea that grew from it
(Anderson, 1972) adds two more properties: the check is always invoked, tamperproof, and "small enough to be subject to analysis and
tests, the completeness of which can be assured" [web: https://en.wikipedia.org/wiki/Reference_monitor; NIST SP 800-53 AC-25,
https://www.upguard.com/compliance/nist-sp-800-53/ac/ac-25]. The same paper's **economy of mechanism** ("keep the design as simple
and small as possible") pulls the other way. A kernel that every byte flows through is not small.

Every working system reconciles the two the same way: **the decision is made in one place; the enforcement sits where the data
is, and is not allowed to be skipped.**

- Android's privileged services run in one `system_server` process; each call arrives over Binder carrying the caller's
  kernel-assigned Linux user id (`Binder.getCallingUid`: "This UID can be used ... to determine its identity and check
  permissions") and each service checks the permission at its own entry point [web:
  https://developer.android.com/reference/android/os/Binder#getCallingUid()]. The check is central in rule, local in placement.
  Audio and video buffers do not route through the permission service; a permission is checked when the stream is opened
  [inferred from the API shape; not verified in AOSP source].
- Open Policy Agent describes itself as a decision engine that services "offload policy decisions to ... by executing queries",
  deployable "as a sidecar, host-level daemon, or library" [web: https://www.openpolicyagent.org/docs/philosophy]. The service
  enforces. OPA's own measurements put a precomputed decision at about 50 microseconds and note that the network hop to the
  engine, not the engine, is the cost [web: https://openpolicyagent.org/blog/partial-evaluation-162750eaf422].
- Home Assistant's core keeps a state machine, an event bus and an action registry that "don't know about each other" [web:
  https://contributors.home-assistant.io/architecture/core/]. Its permission page warns that "Permissions only work if your code
  checks them" [web, via the prior-art record section 8]. That is the failure mode Zoe has today: 74 copies of the rule
  ([code] implementation map, section 1).

So the boundary for Zoe is drawn by kind of thing, with three answers:

- **Decide**: the kernel is the authority. The caller asks before acting and obeys the answer. The kernel writes a receipt.
- **Record**: the caller acts on its own rules and reports what it did. The kernel keeps a receipt. It can answer "why" and help
  forget; it cannot say no.
- **Not through**: the kernel is not called. Where a rule applies, the caller applies the kernel's rule through one function that
  a test forbids bypassing.

### A.2 The rule a builder applies without asking

Walk these in order; the first "yes" wins.

1. **Does it change consent, membership or memory of a person** (grant, revoke, share, forget, add or remove a member, a guardian
   change, a household setting such as quiet hours or who may do what)? **Decide**, and it must come from a person's own action on
   a trusted surface, never from the model.
2. **Does it change the household's state on someone's behalf** (a list, a reminder, an alarm, a calendar event, a note, a
   contact, a device in the house, a routine step), or **send or say something nobody asked for right now**, or **send anything
   out of the house**? **Decide.**
3. **Does it store something about a person that someone may later ask "why", "undo" or "forget" about, but the kernel has no say
   over** (a memory write, a timer, a flag flip)? **Record.**
4. **Is it a stream** (audio, model tokens, screen updates) **or a bulk read used to build a reply**? **Not through.** Apply the
   kernel's visibility rule through the one shared function.
5. **Is it a change to Zoe's own code?** Not through the kernel at run time: the review pipeline is that authority (A.5).

If a new thing does not fit, it is the kernel builder's to classify, recorded in the "what the kernel owns" table in the same
change (E.4).

### A.3 Every kind of thing Zoe does

"Kernel down" describes the end state, after a verb's old rule copy has been removed (plan Phase 6). During the cut-over every
"decide" verb still has its old path as a fallback, so a kernel outage changes nothing a person can notice.

| Thing Zoe does | Answer | Reason | Kernel down (end state) |
|---|---|---|---|
| An action that changes household state (list, reminder, alarm, calendar, note, contact, routine step) | **Decide** | consent and receipts are the point; one decision of about 1 ms per action | refused honestly ("I can't change your lists right now, nothing changed"); see owner choice H1 |
| A read of a person's data (a list shown, "what's on my calendar") | **Not through**, kernel's rule | a read happens dozens of times per screen and per turn; the check is the visibility rule, applied by one function | reads go on: the rule is cached, and it cannot change while the kernel is down, because only the kernel changes it |
| Something Zoe says or sends unprompted (reminder firing, offer, notice, push) | **Decide** (the "may Zoe speak or send now" gate) | the owner's principle 10 and the attention budget live in one place; not on a reply's path | unprompted speech and sends are held (silence is the safe failure); requested speech is unaffected |
| A memory write | **Record** | memory is Zoe's strongest system with its own authority layer; the kernel learns of it so one forget covers both | memory works; the record catches up from a bounded queue |
| A memory read used to build a reply | **Not through** | a recall runs every turn over many rows; a hop per read would put private memory into a second process for no new safety | unaffected |
| A smart-home command | **Decide** | a lock or a heater is the most consequential thing Zoe touches | consequential devices refused; lights and media fall back (owner choice H2 draws the line) |
| A web request leaving the house | **Decide** (one decision per lookup; the page never passes through the kernel) | the one door out of the house; the "lethal trifecta" rule needs a single gate (C.5) | nothing leaves the house |
| A model call | **Not through** | one inference slot, streaming tokens; the kernel shapes it only by deciding which tools the model is offered for this person | unaffected (actions it proposes are refused as above) |
| Speech-to-text and text-to-speech audio | **Not through, ever** | continuous streams; no consent question per frame; a fault would make Zoe deaf and mute | unaffected |
| Panel rendering | **Not through** | screens read through the main service with the kernel's rule; the panel never talks to the kernel | unaffected |
| A background job | **Decide** if it acts for a person (an anticipation offer, a routine tick), with its cause marked as a job; nothing if it only computes | a job is a caller like any other; it gets no exemption | its actions are held and retried |
| A night job | Same as a background job; the kernel's own sweep and backup are kernel operations run by a maintenance key | rehearsed by day first (plan rule 8) | the night job's kernel steps are skipped and reported |
| A configuration change: household settings (quiet hours, sharing, who may do what) | **Decide** | these are consent | refused |
| A configuration change: the owner's switches (flags, units, model settings) | **Record** (the kernel notes the values of its own switches at start) | the kernel must never control its own off switch; the owner's operating controls sit above it | n/a |
| An agent editing Zoe's own code | **Not through** at run time; the review pipeline is the authority; changes to the kernel need the owner's approval (owner choice H4) | code changes are judged before they run, not while they run; coding agents get no kernel key at all | n/a |

Count: of the fifteen kinds, nine are decided by the kernel, three are recorded, and the rest are streams, screens and model
calls. That is the owner's "most things", measured in decisions.

### A.4 The arithmetic behind each "not through"

**Speed.** [lab] One kernel decision costs about 0.75 ms at the median and 2.2 ms at the slowest 1%, end to end over the socket,
measured on the Jetson under normal load (`zoe-os-brain-bridge.md`, "Latency of the hop"); a list add under authority about
1.1 ms (cut-over plan, section 4). The production client caps a call at 150 ms and then skips the kernel for 5 s
(`services/zoe-data/os_authority.py:37-38`, os-authority-prod worktree, [code]).

| Path | Rate | Cost if routed through the kernel | Verdict |
|---|---|---|---|
| A spoken action | one or two per turn | about 1-2 ms against a brain tool turn of about 0.9 s at the median ([live read] cut-over plan section 4): about 0.2% | through |
| Memory recall for a reply | tens of row reads per turn [inferred] | tens of ms per turn, plus a copy of private memory in a second process | not through |
| Audio | a continuous stream, tens of frames a second [inferred] | a hop per frame, and the kernel's failure becomes Zoe's deafness | never |
| Model tokens | tens per second while streaming [inferred] | a hop per token; no consent question per token | never |
| Screen updates | about 145 broadcast call sites in the main service ([code] `zoe-os.md` section 1) | the kernel becomes a hard dependency of every screen | not through |

**Availability.** The kernel runs as its own small service. With a decide answer and the old path kept (the cut-over), a kernel
outage is invisible. After the old path is removed, an outage refuses only the decide actions for its duration, and the drafted
unit restarts it after 10 s (`scripts/setup/systemd/zoe-os-authority-kernel.service`, os-kernel-production worktree, `RestartSec=10`,
[code]). I would cut that to 1 s with a watchdog (E.3). If the kernel were in the audio or token path instead, the same outage would
silence every turn in the house. That asymmetry is the whole argument.

**Blast radius.** A wrong decision by the kernel affects one action and leaves a receipt that can be undone. A kernel that held
content would add a second place every forget, backup and restore must reach (A.6).

### A.5 Agents editing Zoe's code

Self-evolution is in the owner's vision. The authority for a code change is the review pipeline (`validate`, cross-review, the
voice replay gate), not the kernel. The kernel has two rules about it:

- **Coding agents hold no kernel key and cannot read the kernel's file.** They run as the shared user today (`p0-deroot.md` 4.1,
  [code]); the kernel must not (B.3).
- **Changes to the kernel's code or to its "what it owns" table are load-bearing**: always cross-reviewed, always on the
  whole-repo review list, never landed by an autonomous loop without an approval. Whether that approval is the owner's own tap is
  owner choice H4.

### A.6 What the kernel must never become, and what stops the drift

**Never a pipe.** No audio, no model tokens, no screen frames, no bulk reads. Enforced by the wire itself: a closed list of
operations with fixed arguments and a frame size cap ([lab] `zoe_os/wire.py`), and by a pinned rule that no operation returns
another person's content in bulk.

**Never a second database for everything.** This is the decision that changes the lab's current direction. Today the authority
kernel and the reminders-and-alarms branch rebuild durable list, reminder and alarm rows inside the kernel, including the item
words, reminder notes and alarm labels ([code] `labs/zoe-os/zoe_os/reminder_rows.py`, `alarm_rows.py` on
`feature/os-kernel-3-reminder-rows` and `-4-alarms`; `zoe-os.md` section 13), while the main database still holds the same rows as
the household's read model. Two copies of the same words means two places forget must reach, two backups holding words, and a drift
report forever.

**Decision: the kernel keeps decisions, not content.** For a production verb it keeps: who, for whom, which action, under which
permission, the cause, the time, the row's id in the main database, a fingerprint of the words (a keyed hash), and every field a
decision needs (a reminder's schedule, an alarm's time and repeat rule, a list's owner and sharing). It never keeps the item text,
the reminder note or the alarm label. The words may pass through the kernel inside a proposal, so it can check them against a
person's forget shield; they are not stored.

- "Why did you add that?" is answered by joining the receipt (who, when, what caused it) with the row in the main database.
- "Undo that" is decided and recorded by the kernel and carried out by the main service from its own before-state.
- The drift report compares fingerprints (`os_authority.digest`, `kernel_digests`, [code], already works this way).
- The kernel's database becomes low-sensitivity: ids, times and fingerprints. Backing it up (D4) stays right; it stops being a
  second store of household words.

What it costs: the reminder and alarm row stores keep their schedule maths and rules (the valuable part) and drop their text
fields; the lab's undo-snapshot scrubbing gets simpler. [inferred] About one package of kernel work (G-rework in section G).

**Stopping the drift:** a test in the kernel suite asserts that no kernel table has a column that holds free text other than
fingerprints (a schema allowlist), and the size and dependency budget (E.5).

### A.7 The kernel knows people, care and sharing; "household" is a setting

**Decision (owner, 2026-11-16, after a code audit and a prior-art study):** the charter's rule stands, and the kernel is neither
left household-shaped nor turned into a general policy engine. It takes the middle path the evidence supports.

**What the kernel knows, as fixed kinds with fixed meaning:**

| Kind | What it is | Replaces |
|---|---|---|
| Principal | a person or a device, with an id | member, guest, "the household at the panel" |
| Attributes of a principal | plain flags the settings supply: decides for themselves or needs a co-signer; may read shared things; may grant; counts toward "someone always keeps full control"; expires at | the roles adult / child / guest, compared by name in code today |
| Care link | one principal acts for another: a typed edge with a stated set of actions, an optional expiry, and who may withdraw it (always including the person cared for, where they decide for themselves) | `guardians`, valid today only when the other person is a child |
| Scope | private to one principal, shared with a named group, or the kernel's own | the stored visibility value `household`, reminder `family` |
| Grant | a principal's yes to a skill, narrowed, with a parent and an expiry | today's grant, which has neither parent nor expiry |

**What stays in code, because a mistake here must be impossible to configure:**

1. A "no" beats a "yes"; an unknown principal, attribute or scope gets the least access. (Today an unlabelled person defaults
   to a full adult, [code] `zoe_os/capability.py:30`; that is fixed with this.)
2. Organising and memory care are two separate powers of a care link, and neither is ever implied by the other. **Organising**
   (reminders, alarms, lists, routines, calendar) never includes forgetting, undoing or explaining someone's data. **Memory
   care** (owner decision 2026-11-16) lets a carer correct or remove what Zoe has learned about the person they care for, and
   fix a fact in place so the person's later statements do not replace it: a small child's story should not become a fact,
   and someone unwell may say things their carer has to put right. Memory care is a named action set on the link, never a
   default of every link; it never opens private notes or a journal; every use is recorded with who did it and why; and it is
   never model-proposable (C.5). The settings loader refuses a file that gives memory care over a principal with no care link.
3. An item marked private shuts out every care link.
4. At least one principal who decides for themselves and may grant always exists; the last one cannot be removed or narrowed.
5. A grant never exceeds its parent, and withdrawing a parent withdraws its children.

**What moves out of the kernel:** the words `household`, `family`, `child`, `adult`, `guest`, `shopping`, the kinship word list
and the default list name. They live in one settings file (the household pack) that ships in the repository, is loaded at start,
and is checked against rules 1 to 5 before the kernel will serve. It is data with executable tests (allow and deny cases run in
CI), not a policy language: no conditions, no loops, no expressions. A different kind of buyer gets a different file.

**Why this and not the alternatives** (sources in `Sources` below and in the recommendation the owner read):

- The owner's carer rule (a carer organises reminders, alarms, lists, routines and calendar for a child or an adult in care,
  unless the item is private) cannot be expressed by the household model: every guardian path goes through one child-only check
  ([code] `zoe_os/capability.py:34-37`), and the reminders branch adds adults in care for reminders alone
  ([code] `zoe_os/reminder_rows.py:59-64`). Each further verb would repeat that.
- Consumer products that fixed parent, child and organiser roles in their core are the ones with documented trouble serving
  shared custody, adult carers and larger adult households; systems that must handle carers model a named delegate, a subject,
  a scope and a lifetime.
- Fully general permission engines need their own test and analysis tooling to be trusted, integrator-written policy is
  measured to drift looser, and their users still keep lock-out rules in code. A general engine with no product rules on top
  shipped nothing usable in the one home system that tried it.
- The audit found no rule that needs the kernel to know what a household is: 39 rule lines in the core and 16 in the two
  daemons branch on a household idea, plus 12 added by reminders; none is irreducible. [inferred] sizing: 300 to 450 changed
  lines, a settings module of 150 to 300 lines, and the tests that build a household fixture re-pointed. The kernel has no
  stored data on the box, so nothing is converted.

**Timing.** After the lab stack in review lands, as the next kernel package, and before B.2's move. Every verb added before it
adds rules to unpick, so alarms, routines and calendar are built on the care link, not on a guardian-of-a-child check.

---

## B. Where it lives

### B.1 Process: its own service (D2, confirmed)

| Option | Verdict | Why |
|---|---|---|
| **Its own service** | **chosen** (owner decision D2) | a fault in it cannot take Zoe down; it is the only shape with a real file boundary |
| Inside the main service | rejected | the main service has a history of event-loop stalls and is restarted on every deploy (architecture deep dive 2026-10-18, section 3.1); a library in-process shares its user and its memory, so a bug anywhere in 600 flags' worth of code can write the record |
| Inside the brain app | rejected | the brain reads untrusted text all day; it should hold the fewest powers, not the most; and it is Node, while every kernel test is Python |
| A library plus a guardian process | rejected | the library side would open the database file, so the trust boundary would be the library's honesty |

Ordering: the kernel starts before the main service (`Before=`), never `Requires=`; the main service never waits for it.

### B.2 Repository: `services/zoe-os/`

| Option | Verdict | Why |
|---|---|---|
| `labs/zoe-os/` (today; the os-kernel-production draft proposes keeping it, with one setting naming the source folder) | rejected as the end state | `labs/AGENTS.md` says lab code is never wired to a unit; the brain's exception is named temporary and scheduled for promotion (architecture deep dive, task B1). A trust kernel deployed from an experiment folder contradicts D2's reason for existing |
| **`services/zoe-os/`** | **chosen** | the precedent fits exactly: `services/zoe-vaultd/` is a small, stdlib-only, own-user, Unix-socket trusted daemon living under `services/` while still lab-only ([code] `services/AGENTS.md`, `credential-vault.md`) |
| A top-level package | rejected | the repo's structure validator and manifest would need a new root; nothing is gained |
| A separate repository | rejected | a sold box with no code-hosting account gets one checkout; two repositories mean version skew between the wire client and the kernel, two CI pipelines and two review queues |

**Migration.** The other worker's draft is the right mechanism with the wrong destination: keep its one setting
(`KERNEL_SOURCE_REL` in `scripts/maintenance/zoe_os_kernel.conf`, [code], os-kernel-production worktree) so launch and deploy read
the folder from one place. Then:

1. WP-03 moves the trusted core and its tests to `services/zoe-os/` with its own `AGENTS.md`. The twin, the benches and the brain
   lab app stay in `labs/zoe-os/` and use the service package through its socket or its public module.
2. The setting changes in the same commit. Shadow may start from `labs/` if it is ready first; **authority may not** (Phase 3
   requires the move).
3. **The charter is not amended; the kernel is made to fit it** (owner decision 2026-11-16, section A.7). Section 6 of
   `docs/governance/ZOE_DESIGN_PRINCIPLES.md` says no `home` / `family` / `household` concepts are baked into the kernel, and
   `services/AGENTS.md` repeats it. Neither file changes and Zoe OS gets no exception. So the prerequisite for the move is A.7's
   rework (package G-generic in section G): until the kernel's code speaks only of people, care and sharing, it stays in `labs/`
   and takes no authority.

### B.3 Runtime identity: its own system user, in two steps

The socket proves nothing against a process running as the same user: the lab shows a same-user skill reading the database
through `/proc`, and that test asserts the attack succeeds ([lab] `zoe-os.md` section 4). Today the main service, the brain, every
coding agent, Omnigent and the night window all run as the same user (`p0-deroot.md` 4.1, [code]).

- **Step 1, before Phase 3 (any authority):** the kernel runs as its own `zoe-os` system user, state directory mode 0700, socket in
  a directory group-readable only by the main service's group. Its database is then out of reach of coding agents. This needs a
  one-time owner step with administrator rights; it does not need the rest of the de-root programme.
- **Step 2, before Phase 6 (deleting any old rule copy):** the main service runs as its own user (`zoe-svc` in the de-root plan).
  Until then a coding agent running as the shared user can read the main service's kernel credential and speak to the kernel as
  the main service. Since the old rules still exist until Phase 6, nothing is lost by waiting; deleting them before step 2 would
  make the kernel's guarantees rest on agent good behaviour.
- **Identity on the socket:** the kernel reads the caller's operating-system user from the socket itself (`SO_PEERCRED`, "the
  credentials of the peer process ... in effect at the time of the call to connect" [web:
  https://man7.org/linux/man-pages/man7/unix.7.html]) and requires it to match the credential's owner. The challenge-and-answer
  credential stays as the second factor. Credentials become stable per install and rotate only on a deliberate act (map G20,
  prior-art 10.5): a deploy that restarts the kernel must not break the main service's next call.

### B.4 Data: one home per fact

The owner decided the kernel's own file (D3). This study adds which facts live where. Each fact has exactly one source of truth;
the other side holds a projection that a drift report checks.

| Fact | Source of truth | Projection |
|---|---|---|
| Who lives here, their role, guardians | the sign-in accounts plus child settings (D.2) | the kernel's roster |
| Permissions, sharing, household settings, attention settings | **kernel** | the main database's `visibility` columns, written from kernel decisions so reads stay one SQL filter |
| What was done, by whom, why; receipts; undo eligibility; refusals | **kernel** | none |
| Forget shields (fingerprints), the forget ledger | **kernel**, plus the small ledger outside it replayed after a restore (adopted from prior art) | none |
| When a reminder or alarm fires next | **kernel** (the schedule maths) | the main service's scheduler is told |
| Item words, notes, labels, event titles, contacts, memories | **main database** | the kernel holds fingerprints only (A.6) |

### B.5 Language and runtime: stay in Python, standard library only

Rejected: rewriting the trusted core in Rust or Go. The size argument for a trusted base is about how much logic must be right, not
which language holds it; the measured cost is the SQLite commit and its disk sync, not the interpreter ([lab] the in-process publish
took 29 microseconds against 138 over the socket, `zoe-os.md` 5.1); and a rewrite would discard 515 tests and their negative
controls ([lab] cut-over plan section 1). Python and `sqlite3` are on every box, including a Raspberry Pi 5.

**Revisit trigger, written into the budget:** if the kernel's own work, excluding the disk sync, exceeds 0.5 ms at the median, or its
resident memory exceeds the unit's 64 MB soft limit for a week, the language question reopens with a measurement.

### B.6 The sold box

Everything above works with no code-hosting account and no network:

- one checkout carries the kernel, its tests and its unit;
- first boot creates the user, the state directory, the database and the roster from the accounts already on the box (map 4.13),
  offline;
- the kernel's tests and the twin run offline (guarantee 10);
- updates arrive as a new checkout or bundle; the kernel migrates its own database at start, after an automatic backup (E.2);
- backups are local, and the forget ledger replays after a restore.

---

## C. The trust boundary

### C.1 Who may talk to the kernel, and as whom

| Caller | May talk to the kernel? | Proves identity by | May do |
|---|---|---|---|
| The main service (`zoe-data`) | **yes** | operating-system user on the socket plus a stable credential | record what a person said (on behalf of the voice and panel surfaces it hosts); propose actions for the verified person of a turn; relay a person's own consent, forget and membership decisions; settle receipts; read receipts and explanations for display |
| Maintenance (backup, night sweep, health) | **yes**, narrow | its own credential, usable only for `health`, `backup`, `sweep` | nothing about a person |
| The brain app (Flue/Pi) | **no** in production | n/a | it proposes through the main service's existing tool door (`/api/system/intent-dispatch`); the kernel gives the main service the tool list for this person (`describe`) |
| The panel and phones | **no** | n/a | they talk to the main service; the panel's "household at the panel" principal is asserted by the main service from the panel's signed-in device |
| Skills (today in-process modules of the main service) | not separately | n/a | they act through the main service's seam; future out-of-process skills get their own credential, manifest and user |
| Night jobs | only through maintenance or the main service | as above | a job that acts for a person proposes through the main service with its cause marked "job" |
| Coding agents | **never** | n/a | they change code through review; they hold no kernel key and cannot read its file once B.3 step 1 is done |

**Why the brain gets no direct line.** The lab bridge gives the brain process its own credential ([lab] `zoe-os-brain-bridge.md`).
That proves the kernel can be the brain's only door. In production the brain already has exactly one door, the main service's
intent dispatch, which knows the verified speaker. Giving the brain a second, direct key adds a credential to the component most
exposed to hostile text, and gains nothing: the person's identity must come from the voice surface in the main service either way.
The lab bridge stays as the test harness for the kernel's tool schemas.

**Honest limit.** The main service records what a person said and proposes actions for them, so the kernel's "cause" rule protects
against the main service's bugs, not against a compromised main service. That is unavoidable while the main service hosts the
voice surface and holds the household's database password: for the household's content, the main service is inside the trusted
base whatever the kernel does. What the kernel adds is one set of rules, a record nobody else can write, and a boundary against
everything that is not the main service.

### C.2 What a buggy or compromised caller can and cannot do

| Caller state | Can | Cannot |
|---|---|---|
| Main service, buggy | propose a wrong action for the turn's person; record a wrong utterance | act for a person who did not grant the permission; act with no cause; spend a receipt twice; bring back forgotten words; write the record directly; change consent without a person-surface call |
| Main service, compromised | everything the main service can already do to the main database; propose actions for any member using fabricated utterances | erase the record's history silently (append-only, and the file is another user's); grant itself new kinds of action beyond the manifest ceiling; forge a forget for a person without leaving a forget record |
| Brain, any state | propose wrong tools through the dispatch door | name the person (the turn does), touch a store, grant or forget, reach the kernel |
| Coding agent | open a pull request | read the kernel's file (after B.3 step 1); hold a kernel key; land a kernel change without approval (H4) |
| Maintenance key, stolen | trigger a backup or a sweep early | read or change anything about a person |

### C.3 Where a person's approval is required

- **Consent changes** (grant, revoke, share), by the person, or a guardian for a child, from a verified voice or a signed-in
  screen. Never from a model proposal.
- **Membership changes** by an adult administrator.
- **Forget** by the person or a guardian for a child.
- **Consequential actions** need the person's own yes to that offer, used once ([lab] guarantee in `zoe-os.md` 2.5): unlocking or
  opening, anything that heats, sending anything outside the house, spending money, and any action whose cause is untrusted text
  (C.5).
- **The kernel's own rules** (the "what it owns" table, budgets): a reviewed change, approval per H4.

There is no exempt principal (prior-art 8.1): the owner acting under a grant leaves a receipt like anyone.

### C.4 Who the turn is for

Identity comes from the surface, never from the model: the verified voice, the signed-in screen, or, when nobody is verified, the
least-privileged principal for that place (an anonymous guest at the panel; "nobody" on a phone that is not signed in). A tool has
no "member" argument to be talked into ([lab] `zoe-os-brain-bridge.md`). The kernel resolves list, reminder and alarm names to ids
from its own records and checks that the person owns or may use each id; a wrong id is refused as a bad id, not as a permission
refusal (adopted from prior art, plan section 6).

### C.5 Stopping the model from being a confused deputy for text it read

The model's arguments can come from a web page, a message, a pendant transcript, an imported calendar or another person's words in
the room. Following the prior-art review (section 7, OWASP LLM06 and the "lethal trifecta" it cites), the kernel takes four rules:

1. **Every proposal carries a cause class**, set by the main service, never the model: spoken by a verified person; spoken,
   unverified; typed by a signed-in person; untrusted text (web, message, transcript, imported feed); a job or timer. The receipt
   records it.
2. **Untrusted text can only make an offer.** An action whose cause is untrusted text becomes a suggestion that waits for the
   person's yes, unless that person turned on "act on it" for that source (owner choice H5 sets the default).
3. **A turn that has read untrusted text cannot send anything out of the house in the same turn without a yes.** The main service
   marks the turn as having read untrusted content; the kernel refuses an outbound action on a marked turn without an accepted
   offer. This is the one place the three ingredients (private data, untrusted input, a way out) meet.
4. **Consent, membership and forget are never proposable by the model.** They are accepted only from the person-surface call.

---

## D. The data-model questions, decided

### D.1 Removing an account: what "shared" and "accepted" mean

Today nothing records acceptance, and three of the five stores do not record who wrote an item ([code], verified for this study:
no `created_by` column in any migration under `services/zoe-data/alembic/versions/`; `event_attendees.partstat` exists in
`0052_calendar_data_model.py:138`; the list heads-up rides `pending_suggestions` with `action_type='list_shared'` in
`list_sharing.py:8,29`). The Settings delete button calls the sign-in service, which without `permanent=true` only deactivates the
account and never reaches the main service's erase (`services/zoe-ui/dist/settings.html` `deleteUser`,
`services/zoe-auth/api/admin.py:249-283`, [code]).

**The rule, in one line.** An item of the removed person is kept only if it sits in someone else's container (another member's or
the household's list or calendar), or it sits in the removed person's own container and another member EXPLICITLY accepted that
container ("keep it", or a yes to an invitation). What a remaining member added to the removed person's container is that
member's own material and stays theirs; it does not keep the rest of the container. Everything else goes. No record, no keep.
**The administrator is asked at removal** (owner decision 2026-11-16): "Do you want to delete all shared content?" Keeping is an
option he chooses, never something inferred.

**Words used:**

- **Container**: a list, a calendar event, a contact card, a note. Items live in containers (list items in lists; fields in a
  contact card).
- **Shared**: the container's owner made it visible to others, either the household or named members. Today that is
  `visibility = family`; named-member sharing becomes a kernel permission (B.4).
- **Accepted**: a specific remaining member did one of two EXPLICIT things with that container, recorded with who and when:
  1. tapped or said "keep it" / "add it to mine" on the heads-up;
  2. answered yes to a calendar invitation (`partstat = accepted`).

  Delivery is not acceptance. Viewing is not acceptance. **Contributing is not acceptance of the container either:** a member
  who added, edited, ticked or completed something in it keeps only their own material (their items, their edits as they wrote
  them), moved to a container of theirs; the removed person's items in it still go unless the container was accepted.

**Per type:**

| Type | Shared means | Accepted means | Removed person's items in OTHERS' containers | Removed person's own containers |
|---|---|---|---|---|
| Lists and list items | list visibility family, or shared with named members | "keep it" on the heads-up (a contribution keeps only that member's own items) | kept (the container is theirs) | kept if accepted by at least one remaining member, then owned by that member (one) or by the household (more than one); otherwise deleted with every item |
| Reminders set for someone else | set by the author for another member (needs a new author field) | the recipient had granted "may remind me" (a guardian's standing permission for a child counts), or acknowledged, snoozed or completed it | a reminder the removed person set FOR a remaining member stays with that member | the removed person's own reminders, including ones others set for them, are deleted |
| Calendar events | visibility family, or invitees | an invitee's RSVP accepted (an edit by a remaining member keeps only that member's change, not the event) | kept | kept if accepted; otherwise deleted. **This changes today's behaviour**: today the erase keeps every family event, whose title is the removed person's words |
| Contacts | visibility family | "keep" (a remaining member's own edits or links stay theirs; they do not keep the card) | n/a | the card and its fields kept if accepted; the removed person's private notes about the contact (rows with their author id) deleted |
| Notes | visibility family | "keep" (a remaining member's own edit stays theirs) | n/a | kept if accepted, otherwise deleted |
| Journal, money records | never shared (always personal, owner only) | n/a | n/a | deleted |

**What makes it true: the minimal migration** (one migration, next free number after the reserved ones):

1. `created_by` (nullable) on `list_items`, `reminders`, `notes` and `people_field_values`; set by every insert path through the
   converged write functions (WP-20, WP-26). `events` already has an owner and `event_attendees` already has per-attendee rows.
2. One table, `share_acceptances(object_type, object_id, member_id, accepted_at, how)`, `how` in `keep`, `rsvp`. Written by the
   heads-up's "keep" action and by an RSVP of accepted, and by nothing else. A contribution writes no acceptance: `created_by`
   (item 1) is what identifies a remaining member's own material in the removed person's container.
3. The list heads-up gains a "keep it" action. `pending_suggestions` delivery state stays what it is: delivery.

**Old rows.** Rows written before the migration have `created_by` empty. An old item in someone else's container is kept: where it
lives is a recorded fact about whose data it is part of, not a guess about who wrote it. An old item in the removed person's own
container follows that container's rule. Acceptance is never inferred for old rows: an old shared list of the removed person with
no acceptance record is deleted. The removal reports these counts to the administrator, so it is visible.

**The removed person's name on kept items.** The author field is replaced by a fixed "former member" marker, never the name; a
receipt for a kept item keeps the marker too. Text a person typed that happens to contain a name is content, and is not rewritten.
The kernel's whole-member erase already removes the person's own records and leaves no name ([lab] `zoe-os.md` 2.4).

**Missing record means delete; unreadable evidence means stop and retry.** No acceptance row (or no table yet) is a clear
answer: not accepted, so it is deleted with the rest. A row or table that exists but cannot be READ (a failed query, a corrupt
row, a timeout) is not an answer: the removal stops there, deletes nothing on that evidence, keeps its barrier up and retries.
It never treats "could not read" as "not accepted", and it never reports done.

**The button: four steps, in this order, run by the main service.** One "remove member" action in the main service replaces the
direct call to the sign-in service (this replaces WP-24):

1. **Persist the barrier first:** the durable erase mark and the erase gate for that member (`erase_watermark`, `user_erase_gate`,
   [code]) are written and confirmed before anything else, so no writer in any process adds data for them from this moment, and
   a restart finds the removal already in force. The barrier is **kept afterwards**, not cleared when the job ends.
2. **Deactivate the sign-in account** (the sign-in service's existing non-permanent delete). If the barrier could not be
   persisted, this step does not run and the removal reports failure.
3. **Erase:** the full erase in the main service, including the kernel's whole-member erase, failing closed. A failure, or
   evidence that cannot be read, leaves the member deactivated, the barrier up, the job pending, and the administrator told
   "not finished, retrying"; it never says done.
4. **Delete the sign-in:** the sign-in service's permanent delete, only after step 3 confirms.

Why this order: a barrier written after deactivation leaves a window in which a background writer, or a turn already running,
adds data the erase then misses; erasing after the account is gone leaves data with no account to retry against. The job is a
durable row, so a restart resumes it.

### D.2 How the kernel learns who lives in the house

**Source of truth:** the sign-in accounts (`auth_users`, the sign-in service's table, which lives in the same Postgres instance the
main service already reads: `services/zoe-auth/models/database.py:149`, [code]) for who exists and their role; `member_modes` (main
service, `0035_persona_layer.py`) for whether someone is a minor; named guests from `named_guests` once that ships. The owner keeps
no other list.

**How changes reach the kernel:** a roster reconciler in the main service (WP-23b, decided):

- at start, every 60 seconds, and at once after any change the main service itself makes (member registration, erase, a child
  setting);
- it computes the roster (id, kind, guardians) and sends the kernel a numbered snapshot (`member_upsert` for each change);
- it never reads the kernel's file; the kernel never reads Postgres (D6 holds).

**A person who disappears** from the accounts without going through "remove member" is **suspended** in the kernel (no new actions,
data kept) and an audit finding is raised. Erasure is irreversible; it is only ever triggered by the explicit remove, never by an
absence or a failed read.

**A person the kernel has not been told about:** during the cut-over the old path runs and the fallback is counted
(`not_in_roster`, already in `os_authority.py:190`, [code]), and a reconcile is triggered at once. After the cut-over the kernel
treats them as the least-privileged principal for that place, and Zoe says "I don't know you yet". It never creates a member on
its own.

### D.3 "Remove X from the list" deletes

- The two paths that tick today, the keyword intent (`intent_router.py:3200`) and the MCP tool (`mcp_server.py:1582`), are changed
  to call one converged delete function in `list_service` (WP-27), the same one skybridge and REST use. Delete is a soft delete
  kept for the undo window, then purged; forget purges at once.
- **"Tick off" stays its own spoken action** and ticks: "tick off milk", "I got the milk", "cross off milk" (crossing off a paper
  list marks it done). "Remove", "take off" and "delete" delete. One table of phrases in the intent layer, tested both ways.
- Shadow agreement for list remove is only meaningful after this lands (map R8); WP-28 moves the shadow hook to that one function.

### D.4 Timers: reported, not decided

Timers stay out of the kernel's authority. They are minutes long, they belong to whoever is at the panel, they share nothing, and a
cooking timer must ring even while the kernel restarts. Each create and cancel is **recorded** (so "did you set a timer?" has a
receipt across a restart and a forget reaches the label), and the timer's labels join the forget inventory (WP-53). The ring is
the direct consequence of a person's request, so it is not "unprompted" and does not wait on the speak gate, but it still honours
the one quiet-hours function for its volume (WP-44). This drops map item G11.

### D.5 Guests, named guests, children, guardians, and the household at the panel

**Kinds of principal** (deny wins; unknown means least privilege):

| Kind | Who | May |
|---|---|---|
| Adult member | account role admin, user or family member, and not marked minor | everything their permissions allow |
| Child member | account role child, **or** marked minor in `member_modes` (either makes a child) | what a guardian has granted on their behalf; until the guardian ceremony exists, today's rule (D7) |
| Guardian | an explicit link; until the owner sets them, every adult administrator, written as explicit links by the reconciler | manage the child's permissions, forget for the child, set, snooze and complete the child's reminders; reading the child's private items is owner choice H3 |
| Named guest | a guest person in the roster with an expiry | add to household lists, set timers, be remembered as themselves; their own forget; their items follow D.1's rule when they are removed |
| Anonymous guest | an unverified voice or the sentinel guest ids | household items only, timers, nothing personal, cannot grant |
| **The household at the panel** | a principal the main service asserts for a signed-in panel device when no one is verified | owns household lists and household alarms; carries the fridge rule ("anyone at the panel may edit items on household lists"); may silence any ringing alarm in its room; cannot read anyone's personal data; cannot grant, forget or share |

Permissions are small objects with a parent, an expiry and a narrowing; revoking a parent revokes its children; the last adult can
never be locked out (adopted in the plan from prior art). A guardian's grant for a child is a child of the guardian's own grant.

**The coordinator's four points from the reminders-and-alarms branch**, decided:

- (a) **A reminder for a time already past is refused, and Zoe says so** ("that time has already passed; did you mean tomorrow?").
  Agreed: production stores one that never fires, which is a silent failure.
- (b) **A guardian may snooze or complete their child's reminder.** Agreed, as a guardian right. It adds an ability, so the "nobody
  loses an ability" rule holds.
- (c) **The household principal**: defined above, the same one for household lists, household alarms, the fridge rule and the
  panel's power to silence a ringing alarm.
- (d) **Timers**: D.4.

**What does not map yet, and where each belongs:**

| Item | Kernel or feature | Why |
|---|---|---|
| One-day alarm exceptions ("an hour earlier next Friday", skip a day) | **kernel** | they change when an alarm rings, which is the kernel's schedule maths, and need a receipt and an undo |
| Alarm sound and volume ramp | feature | presentation; decides nothing about consent or timing |
| Reminder category and type | feature | labels for display |
| Reminder priority | feature sets it; **the kernel's speak gate** reads it as an input | "urgent overrides quiet hours" is a rule, so it lives in the gate, once |
| Quiet hours | **kernel's speak gate**, after WP-44 first merges today's three copies into one function | one rule for "may Zoe speak now" |
| Spoken time forms ("10am", "every weekday") | feature | language parsing is not policy; the kernel takes the stored forms |
| Snooze length (kernel 1..1440 minutes, production any number) | kernel's limit | the main service clamps and says so |

Following A.6, the kernel's reminder and alarm rows keep the schedule fields and drop the note and label text in favour of
fingerprints.

---

## E. How it evolves safely

### E.1 The wire and the record

- **Wire.** Version 1 stays; changes within a major version are additive only (new operations, new optional fields), as guarantee
  2 already requires. A `hello` reply lists the operations the kernel supports; the client turns off a feature whose operation is
  missing rather than failing. A breaking change is a new major version served beside the old one for one release.
- **Record.** Every stored event gets a schema version and an origin (the box's id) now (adopted in the plan). Old versions are
  read through small upgrade functions; stored events are never rewritten except by forget's erasure.

### E.2 The kernel's own database

- A `meta.schema` number. At start the kernel takes an online backup to `pre-migrate-<from>-<to>` (SQLite's backup interface,
  never a file copy of a write-ahead-log database: map R12), migrates in one transaction, checks integrity, and refuses to start on
  any failure. The main service then runs on its fallback and the health check is red.
- **Expand, then contract.** A release that changes the schema first adds (new code reads old and new); the next release removes.
  So rolling back the code by one release never meets a schema it cannot read. Rolling back a schema means restoring the
  pre-migration backup and replaying the forget ledger, an owner step.

### E.3 Rehearse, roll out, roll back

1. **Kernel suite** in CI (WP-02) on every change.
2. **Decision replay.** The new kernel is started on a copy of the live kernel database and replays the last 14 days of proposals
   from the record; every decision must equal the one recorded, or the difference is listed and explained in the pull request. This
   is the strongest guard against an upgrade changing the household's rules by accident. The copy is household data, not a harmless file: the kernel's database holds schedules, relationships and, for now, the
   words in its rows (status note at the top). So the rehearsal is protected: a scratch copy made on the box, readable only by
   the kernel's own user, used for this replay alone, deleted when it finishes whether it passes or fails, never written to a
   backup, a log or an artefact, and never leaving the box. A forget that lands while the copy exists is applied to the copy
   or the copy is discarded.
3. **The household twin** plays a week through the new kernel (plan Phase 2).
4. **Voice replay** with a scratch kernel for any kernel change on the voice path (WP-07).
5. **Rollout:** deploy restarts the kernel before the main service (additive wire, so the old client keeps working); a health
   probe within 60 s; on failure the deploy reverts the kernel's code to the previous commit and restarts it. `RestartSec=1` and a
   watchdog ping replace the drafted 10 s.

### E.4 Keeping builders honest

- **The "what the kernel owns" table becomes machine-readable** (a block in `services/zoe-os/AGENTS.md`). For each owned action a
  test scans the main service and fails on any write to that action's table outside the one allowed function, in the style of the
  existing "exactly one list writer" pin (`services/AGENTS.md`). Adding a row without the seam fails; adding a seam without a row
  fails.
- The existing import ban (the main service never imports the kernel package) stays pinned.
- The scorecard tracks "duplicate rule copies removed" per action (prior-art 8.5), starting from the map's count of 74.

### E.5 Size and dependency budget

- **Trusted core** = the files under `services/zoe-os/` except tests: today about 2,800 lines in the lab core plus about 460 in the
  authority daemon and its shadow daemon ([code] line counts of `zoe_os/*.py` without twin, skills and brain, and
  `authority.py`, `shadow_daemon.py`). **Budget: 6,000 non-blank lines.**
- **Zero third-party runtime imports.** A test parses every import in the core against the standard library list. One pinned
  cryptography library may be added only if the owner approves per-person encryption keys (prior-art, idea 4).
- **No free-text columns** except fingerprints (A.6).
- **Memory:** 64 MB soft and 96 MB hard ceiling in the unit, no swap, as drafted.
- Raising any budget needs the measurement and the reason in the pull request (already a lab rule) and is on the H4 approval
  path.

---

## F. The target picture, a year out

```
                     PEOPLE: voice at the panel, touch, phones, Telegram
                                      |
                                      v
   +--------------------------- MAIN SERVICE (zoe-data, own user) ---------------------------+
   |  voice surface (who is speaking) ---- turn = (person, cause class) ----.                 |
   |  STT/TTS audio  ... bypass ...  (never leaves this box of arrows)       |                 |
   |  panel / screens ... bypass ... read with ONE visibility function <-----+---- policy     |
   |  intent dispatch (the brain's one door)                                 |     cache      |
   |  memory service (own authority) ---- record -----------------------.    |       ^        |
   |  write seams: one function per action ----- DECIDE (propose) ---.   |    |       |        |
   |  speak/send gate wrapper -------------------- DECIDE ------------+   |    |       |        |
   |  web lookup door ---------------------------- DECIDE ------------+   |    |       |        |
   |  smart-home door ---------------------------- DECIDE ------------+   |    |       |        |
   |  timers ------------------------------------- record -----------+---+    |       |        |
   |  roster reconciler (accounts + child settings) -- push roster --+         |       |        |
   +-----------------------------|-----------------------------------|---------|-------|--------+
                                 | Postgres (content)                | Unix socket, peer user checked
                                 v                                   v
                      +--------------------+        +-------------------------------------+
                      | MAIN DATABASE      |        | ZOE OS KERNEL (zoe-os user)          |
                      | lists, reminders,  |        | consent, roster, record, receipts,   |
                      | events, notes,     |        | schedules, attention, forget shields |
                      | contacts, memories |        | own SQLite file, no words, ~1 ms     |
                      +--------------------+        +-------------------------------------+
                                 ^                                   ^
                                 | settle (row id + fingerprint)     | maintenance key:
                                 +-----------------------------------+ backup, sweep, health
                                                                       (night window)

   BRAIN APP (Flue/Pi on the one model slot) --tool calls--> main service intent dispatch only
        model tokens: bypass (straight back to the voice surface)
   SKILLS: inside the main service's seams today; later their own process, credential and manifest
   CODING AGENTS: pull requests only; no kernel key; cannot read the kernel's file
```

Arrow key: **DECIDE** = the caller asks first and obeys; **record** = the caller acts and reports; **bypass** = never touches the
kernel.

---

## G. What changes in the plan

**Edits to the cut-over plan and the work packages:**

1. **DEFERRED (see the status note at the top): Phase 3 item 1 would change** from "a durable list store in the kernel" to "a
   durable decision record keyed by the main database's row id and a fingerprint", once the action protocol is written and
   reviewed. The first cut-over keeps the stores already built. The intended shape, for that later rework: a `settle` operation: propose (decide, about 1 ms) → the main service writes the row → commit →
   settle with the row id and fingerprint. An unsettled receipt is "uncertain" and is resolved by the reconcile (closes map R3 and
   G18). The idempotency key (adopted) is the proposal's id, carried by settle and by the fallback.
2. **DEFERRED, not a prerequisite for landing:** the rework package for the authority kernel and the reminders-and-alarms
   branch (drop item text, reminder notes and alarm labels from kernel rows; keep the schedule maths and rules; add the "no
   free-text column" schema test). It waits for the reviewed action protocol; the lab stack lands with its current stores, and
   the kernel's file is treated and backed up as household data meanwhile.
3. **WP-03 is required before Phase 3, not optional.** Keep the os-kernel-production draft's single source-location setting; point
   it at `services/zoe-os/`. Replace that draft's section 13 ("retain labs") with B.2.
4. **Add "kernel on its own user" (B.3 step 1) before Phase 3;** move "the main service on its own user" (de-root) before Phase 6.
   This takes the kernel's user out of Phase 4.
5. **Replace WP-24 with "remove member"** (D.1's three steps, a durable job, failing closed) and **extend WP-26** to the D.1
   migration (`created_by` in four tables, `share_acceptances`, the "keep it" action). Both can start now; neither needs the kernel.
6. **WP-23b decided:** the reconciler reads accounts and child settings every 60 s and on own changes; suspends, never erases, on
   absence (D.2). Q3 closed.
7. **WP-27 decided:** remove deletes on every path; "tick off" stays; one phrase table (D.3). Q4 closed.
8. **Drop map G11** (timers as kernel objects); keep WP-53 (timer labels in the forget inventory) and add a timer receipt.
9. **Add a read-side package:** the kernel's sharing decisions written as the `visibility` projection, and the 22 copies of the
   list visibility filter replaced by one function, with a test forbidding a raw filter on owned tables. This is the read half of
   "through the kernel", and it starts collecting Phase 6's simplification early.
10. **Brain bridge in production:** no kernel credential for the brain app; the main service serves the tool list from the kernel's
    `describe`. The lab bridge stays as the schema test harness.
11. **Add cause classes and the untrusted-text rule** (C.5) to the proposal format before the first authority flag; add the
    outbound-on-a-marked-turn refusal with web lookup.
12. **Add package G-generic (A.7), before WP-03's move:** principal attributes, the care link, scopes and grant parents with
    expiry replace role names in kernel code; the five rules of A.7 are pinned by tests; the household pack and its allow/deny
    cases ship with it; the wire and record vocabulary (`household` visibility, the `guardian` argument, `erase_member`, the
    `shopping` default) is renamed while there is still no stored data. The main service's roster writer is where a household's
    members, children and carers are translated into attributes and care links.
12. **Add decision replay** (E.3 step 2) to the kernel's upgrade checklist, and the machine-readable "owns" table with its seam test
    (E.4).
13. **Phase 5 order gains two entries:** web lookup's grant goes in when web lookup ships (it is new, so it is built on the kernel
    from day one); consequential smart-home devices go after alarms.
14. **Q5 closed** (`services/zoe-os/`, with the scoped household-concepts rule); **Q6 closed** (D.4).

**Do these three first:**

1. **Remove member, properly.** The D.1 migration and the three-step button. It closes a live privacy hole today (the button
   only deactivates), it is needed before the kernel holds anything of a person (plan rule 5), and it needs no kernel.
2. **One write function per action.** Converge list insert (WP-20) and list remove-as-delete (WP-27). Every kernel step measures
   the wrong thing until this is done: today authority would cover a minority of list adds (map R1).
3. **Make the kernel fit the charter (A.7), move it to `services/zoe-os/`, and give it its own user.** After this, list add can
   go through Phase 1 to 3 on a kernel that is out of reach of everything but the main service. Re-cutting it to
   decisions-not-content (with settle and the idempotency key) is deferred until its action protocol is written and reviewed
   (status note at the top, G.1 and G.2); it is not one of the first three.

---

## H. The owner's real choices

Everything else on this page is decided. These five are about the household, risk or privacy, and each has a recommendation.

**H1. When the kernel is briefly down, after the old duplicate rules are removed, should Zoe refuse to change lists, reminders and
alarms until it is back, or keep an old back-up path forever?**
Recommendation: **refuse, and say so** ("I can't change that right now; nothing changed"). Reading lists and hearing reminders keep
working. Cost of refusing: a few seconds of "can't" during a restart, rarely. Cost of keeping a back-up path: the duplicated rules
the whole project exists to remove stay forever, with the bugs that come from two copies disagreeing.

**H2. Which smart-home devices should need your spoken yes every time, and do nothing at all if Zoe's rule-keeper is down?**
Recommendation: **door locks, garage doors, the security alarm, and anything that heats** (oven, heaters, kettle). Lights, blinds,
media and fans act on the request and keep working. Cost: one extra "yes" for the listed devices; benefit: a web page, a message or
a misheard phrase can never unlock the door.

**H3. What may a guardian do? DECIDED (owner, 2026-11-16).** A guardian is a carer, for a child **or an adult in care**.
- **Organising:** they can see and manage that person's reminders, alarms, lists, routines and calendar, unless an item is marked
  private. They do not read private notes or a journal.
- **Memory care:** they can correct or remove what Zoe has remembered about that person, and fix certain facts in place. The
  owner's reasons: a small child telling Zoe a story that is not true, and a person who is unwell saying things their carer needs
  to put right. A fact a carer has fixed outranks what the person in care says later.
- **An unrecognised voice** is treated as a guest and let through the child-safety floor: Zoe cannot tell it is a child, so it is
  not refused (owner, same day).

In the kernel both are the care link of A.7, not a parent-of-a-child rule. **Still to settle (proposed, not yet the owner's
words):** memory care comes with a child's guardian link by default and is switched on explicitly for an adult in care; the
person in care can see that a carer changed something unless the carer marks why it should not be shown.

**H4. Should changes to the rule-keeper's own code need your approval, even when the reviews are clean?**
Recommendation: **yes, a one-tap approval** for changes to the kernel's code, its "what it owns" table or its budgets; everything
else keeps landing as it does now. Cost: perhaps a few taps a week while it is being built, fewer later. Benefit: no autonomous
agent can quietly change who may do what in the house.

**H5. When Zoe finds something in text she read (a pendant recording, a message, an imported calendar, a web page), should she
act on it or only suggest it?**
Recommendation: **suggest by default** ("I heard you mention a dentist on Tuesday, want me to add it?"), with a per-person switch
"act on my pendant recordings without asking" for people who want it. Never automatic for web pages. Cost: an extra yes for
things found in recordings; benefit: text written by someone else can never make Zoe act on its own.

---

## What I could not verify

- Whether AOSP's audio and camera paths check permission only when a stream is opened (stated as inferred from the Binder API; I
  did not read the AOSP source).
- OPA sidecar overheads quoted by third parties (1-3 ms, a throughput ceiling): not used in the argument; only OPA's own 50
  microsecond figure is cited.
- The seL4 kernel's size, often quoted as about ten thousand lines [unverified]: not used in the argument.
- Rates for audio frames, model tokens and per-turn memory reads are [inferred] orders of magnitude, not measurements on this box.
- The kernel's cold-start time and restart time on the Jetson: not measured here; E.3's 1 s restart and 60 s health window are
  targets.
- I did not run any kernel test or benchmark; every [lab] number is from the records named beside it.
- The reminders-and-alarms branch is untested by its author; I read its design section and file sizes, not its behaviour.
- Whether `auth_users` sits in the same database (not only the same server) as the main service's tables: the sign-in service's
  default connection string points at the same database name ([code] `services/zoe-auth/models/database.py:149`); the live setting
  was not read.

## Sources

- Saltzer and Schroeder, "The Protection of Information in Computer Systems": https://www.cs.virginia.edu/~evans/cs551/saltzer/
- Reference monitor: https://en.wikipedia.org/wiki/Reference_monitor; NIST SP 800-53 AC-25:
  https://www.upguard.com/compliance/nist-sp-800-53/ac/ac-25
- Android Binder caller identity: https://developer.android.com/reference/android/os/Binder#getCallingUid()
- Open Policy Agent philosophy: https://www.openpolicyagent.org/docs/philosophy; partial evaluation:
  https://openpolicyagent.org/blog/partial-evaluation-162750eaf422
- Home Assistant core architecture: https://contributors.home-assistant.io/architecture/core/
- Linux Unix sockets and `SO_PEERCRED`: https://man7.org/linux/man-pages/man7/unix.7.html
- Everything else from prior art (capability systems, consumer permissions, SQLite durability, erasure, sagas, MCP and agent
  permissions, OWASP LLM06, the lethal trifecta, Solid, local-first software, systemd hardening) is cited with its URLs in
  `docs/research/zoe-os-prior-art-2026-11-15.md`.
