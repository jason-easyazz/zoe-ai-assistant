---
type: architecture
title: Zoe OS - one spine every part plugs into
date: 2026-11-14
status: design record plus a lab prototype (labs/zoe-os/). Nothing here is wired to production; no flag exists yet.
prototype: labs/zoe-os/ (stdlib Python, SQLite in a temp dir, a kernel process behind a Unix socket, synthetic people only)
related: zoe-abilities.md, identity-presence-engine.md, communication-preferences.md, anticipation-loop.md, credential-vault.md
---

# Zoe OS: one spine every part plugs into

This record is written for a reader who has never seen the project. Zoe is a household assistant that runs on
a small computer in the home: a local language model, speech in and out, a memory, calendar, lists, reminders,
wall panels and safety rules. Every claim below carries a label: **[code]** means it was read in the repository
at the path given, **[lab]** means it was run in `labs/zoe-os/` on synthetic data, **[inferred]** means reasoned
and not checked, **[vision]** means not built and not proven.

## 0. Answer first

**Forget merge [code; coordinator verification pending]:** Named sequences use `people_seq`;
snapshot copies use `snapshot_dependencies` and per-person `opaque_snapshots`. Selection,
complete notice batches, snapshot scrubbing, erasure and bookkeeping commit together.
Erasing an action receipt fingerprints its own arguments and content, excluding copied
`undo.before` and `proof.restored` state: those copies belong to their source provenance.
Otherwise erasing A would shield a surviving earlier row copied into A's before-state,
and refuse a clean undo receipt restoring that row. Kernel append checks remain enabled.

**What "an OS" means for a household.** Today each feature of Zoe carries its own copy of the hard rules: who
may see this, whether she may speak now, how to forget, how to prove a thing was really done. The copies are
good, and they are separate, so every new feature must remember all of them, and the price of one miss is a
child hearing a parent's reminder or a forgotten name coming back. An operating system moves those rules out of
the features and into one small core, the **kernel**, that every feature must pass through. A feature
(a "skill") then cannot break a rule it never held: it asks the kernel, and the kernel says yes or no.

**The kernel is seven services.** Each gives the rest one contract.

| # | Kernel service | The contract it gives everything else | Lab prototype | In production today |
|---|---|---|---|---|
| 1 | **Event spine** | Everything that happens is one typed record, private by default, in one ordered durable log. You receive only what you were granted. | built, tested **[lab]** | no bus; a websocket broadcaster and many direct calls **[code]** |
| 2 | **Person and presence** | "Who is here, and how sure am I" is one answer with a confidence, not a guess per feature. | events carry a confidence; no engine **[lab]** | several real parts, not unified **[code]** |
| 3 | **Capability and consent** | A skill declares what it reads and does. Each person grants it or not. Default deny; a child needs a guardian; revocable at once. | built, tested **[lab]** | role matrix and an authority table, per feature **[code]** |
| 4 | **Forget** (part of 1 and 3) | "Forgotten means forever" is a property of the log: gone from replay, export, queues, derived events and the file on disk. | built, tested with negative controls **[lab]** | a ledger plus a 62-store inventory, reached store by store **[code]** |
| 5 | **Act with receipts** | Nothing is done without a grant and (for a consequential action) the person's own yes. Nothing is claimed done without a read-back proof. | built, tested **[lab]** | write receipts and a claim guard exist for writes **[code]** |
| 6 | **Attention budget** | Whether an offer is spoken, shown as a card or held for a digest is decided per person by the kernel, never by the skill. Unprompted speech is off unless that person turned it on. | minimal, tested **[lab]** | one global flag, household quiet hours, per-member preferences for notices **[code]** |
| 7 | **Memory with authority** and **self-care** | What a person said outranks what was inferred; health, self-repair and measured improvement are services, not scripts. | not prototyped **[vision]** | the strongest existing parts: keep them, plug them in **[code]** |

**What was proven in the lab, and only that** (section 5 has the numbers): the spine, the consent model, the
forget guarantee and receipts work together as one small program; three toy skills run on it; a deliberately
malicious skill makes ten attempts across three attacks and all ten are refused and recorded; and each
guarantee has a negative control that shows the attack succeeding when that one guarantee is removed.

**Proven since, in the lab** (sections 4 and 5, all **[lab]**): the kernel runs in its own process behind a
local Unix socket with a closed, versioned protocol; the same malicious skill, run from another process, is
refused ten times out of ten, and six further attacks that a socket makes possible are refused with a negative
control each; forget works by entity and by topic as well as by person; receipts, the used-yes register and the
auditor's place survive a killed kernel, and a kill between a write and its receipt is reported as uncertain.

**What is not proven:** that it is fast enough inside the live voice turn; that the database file is out of
reach of a skill on the real box (the socket does not stop a process running as the same OS user: section 4);
that the real modules can publish to it without regressions; and everything about presence, memory and
self-care as kernel services. Section 8 lists every place the vision outruns the proof.

**The migration never needs a rewrite.** Stage 1 is five one-line publish calls behind a flag that defaults
off, carrying ids only (section 6). Nothing reads the spine until it has been shown to be boring.

## 1. Why now: the same rule, written many times

These are real, working mechanisms in the repository. Each is a local answer to a kernel question.

| The kernel question | Where it is answered today **[code]** |
|---|---|
| Who may hear or see this? | `services/zoe-data/push.py` `PushBroadcaster.broadcast` scopes by `user_id` per connection (line 126); `guest_policy.py` holds a role capability matrix; `panel_principal.py` decides "anyone at the panel"; `voice_scope.py` classifies a spoken turn public or user-scoped; `calendar_service.py` has per-event visibility. |
| May she do this for this person? | `household_authority.py` `decide(action_class, role, confirmed=...)` (line 240) over `authority_table.json`, with floors in code; `write_intent_gate.py` (a write needs an ask); the intent-dispatch endpoint `routers/system.py` `intent_dispatch` (line 2343) is the one door for brain tools. |
| Was it really done? | `write_receipts.py` `record` (line 120), called by about twenty writers; `write_claim_guard.py` blocks a spoken "I've added it" with no receipt; `commitments.py` tracks timed promises. |
| May she speak now? | `proactive/engine.py`: `_is_in_quiet_hours` (line 70), `_spoken_enabled` reading one global flag `ZOE_PROACTIVE_SPOKEN` (line 218); `comm_prefs.py` holds per-member presets, quiet hours and a daily cap for change notices. |
| How do we forget? | `memory_forgotten.py` (a ledger that stores a keyed hash, never the name); `memory_forget_cascade.py` `cascade_forget` (line 251); `MemoryService.delete_user` (`memory_service.py` line 2155); `forget_reach.py`, an inventory of 103 stores of which 62 are marked reached and 2 are known gaps, held true by a test. |
| Who is here? | `voice_session.py` `observe_turn` (line 380: a verified voice opens a panel session); `voice_speaker_id.py`; `routers/face_id.py`; `presence/ha_presence.py` `poll_once` (line 110) writing `presence_observations`; `proactive/presence.py` `panel_presence`. |
| Who said so? | `memory_authority.py` `RANK` (line 111) and `may_override` (line 514): user-stated outranks confirmed outranks inferred, at one choke point. |

Two things follow. First, the pieces are good: the kernel should **adopt** them, not replace them (repository
rule: adopt, do not rebuild). Second, the forget inventory is the clearest sign of the cost: 103 stores had to be
listed by hand because there is no one place data flows through **[code: `forget_reach.py`]**. A new feature
adds a store, and a person's "forget that" silently misses it until a test catches the gap.

There is no event bus in production today **[code]**: `push.py` is a websocket fan-out to screens with a
counter, not a log (`catchup` at line 311 replies "full state refresh recommended"); features call each other
directly (`fire_notification` has six callers, `broadcaster.broadcast` about 145 call sites).

## 2. The seven services

For each: the contract, what the prototype does, what exists, what is missing, how to get there.

### 2.1 Event spine

**Contract.** Every household event is one record: a `kind` from a closed list (someone spoke, a turn ended,
presence, a calendar change, a timer or reminder fired, a sensor, a routine step, an offer, an answer to an
offer, a claim, and five kinds only the kernel may write), one `subject` person, a `visibility`
(`private` to the subject by default, `household`, or `kernel` for the audit trail), a `source`, the events it
`derives from`, and a sequence number that only goes up.

**Prototype [lab]** (`labs/zoe-os/zoe_os/events.py`, `spine.py`):
- durable append to SQLite; the sequence is the row id and survives a restart;
- default deny: with no policy attached, publish, subscribe and replay are all refused;
- a subscriber has a bounded queue. A publisher never waits for a slow subscriber: when the queue is full the
  subscriber is marked lagging and catches up from the log, in order and without loss. This is the
  backpressure rule and it exists so that a slow skill can never slow a voice turn;
- every delivery is checked again at the moment it is handed over, so revoking a grant stops events already
  queued;
- a skill must say which events its event derives from, and may only cite events it was allowed to read.
  Trusted adapters (voice, calendar) record first-hand reality and need no cause;
- a refusal is itself an event (who, what was tried, why) and never carries content.

**Missing.** Schema versioning of the event record (the socket protocol is versioned, the stored events are
not); retention (the lab log grows for ever); more than one kernel process; any production publisher.

**Migration.** Section 6: publish ids from five call sites, flag off by default, nothing subscribes.

### 2.2 Person and presence

**Contract [vision].** One service answers "who is at this panel, in this room, at home" with a confidence and
the evidence behind it, and publishes changes as `presence` events. A skill never decides identity for itself.

**Prototype [lab].** Only the shape: a `presence` event must carry `state` and `confidence`, and the leaving
catch skill stays silent below 0.8 (tested). There is no engine.

**Exists [code].** Voice verification and panel sessions (`voice_session.py`), face identification
(`routers/face_id.py`), Home Assistant presence in shadow (`presence/ha_presence.py`, flag `ZOE_HA_PRESENCE`),
named guests (`named_guests.py`), child age handling (`person_age.py`). The design that unifies them is already
written: [identity-presence-engine.md](identity-presence-engine.md). The kernel adopts that design; this record
adds only that its output goes on the spine.

**Missing.** The engine itself; arrival and departure as facts with confidence (today "left" is not an event at
all **[code]**: `voice_session` closes a session on a timer or an unverified voice, which is not the same as
leaving).

### 2.3 Capability and consent

**Contract.** A skill ships a manifest: the event kinds it reads and how private, the kinds it emits, the named
actions it performs. That is a ceiling. Each person then grants the skill all or part of it. No grant, no
access. A child's grant needs one of that child's guardians, and opens only that child's own events: a
household-visible event of anyone else, or a household event about nobody in particular, needs an adult's
grant. A guest cannot grant. Revoking takes effect on the next delivery.

**Prototype [lab]** (`capability.py`): the manifest format (unknown keys, kinds or levels are errors), a grant
store, and one `Enforcer` that the spine and the action dispatcher both call. A skill that asks for more than
its manifest is refused and the refusal is an event. Grant changes are events only an auditor can read.

**Erased consent [code, lab verification pending].** In the kernel, a grant authorises only while its
grant-change record is live. `GrantStore.of` and `all_for` check that record on every read, so the enforcer,
action dispatcher, skills, surface descriptions and brain bridge all see an erased grant as no grant.
The explainer reads only live grant-change events and therefore cannot explain an erased grant.
Whole-member forget reaches grant records naming the member as person, guardian or sharing recipient, and refusal records
whose `who` is that member. An event-list forget reaches only selected records belonging to the person,
plus their descendants. Entity/topic forget reaches records only if their payload matches that scope.
Unrelated grants survive a scoped forget. A fresh explicit grant is required to authorise later actions.
The twin's sharing grants also require their recorded event to remain live; erased sharing never opens a line.
`ForgetResult.count` and wire `forget.count` count every erased event row, including grant/refusal records
and earlier scoped notices that name the member. The new forget notice is excluded. Thus one grant,
one utterance and its action receipt return 3, not 2. This follows the owner's decision that grant and
refusal records "should be forgotten too"; they are events, not uncounted ancillary rows. Whole-member
forget also removes an earlier named scoped notice; the aged-content retention example therefore counts
three rows (skeleton, derived copy, notice) while still honestly matching zero aged words by entity.

**Exists [code].** Roles and a capability matrix (`guest_policy.py`), the authority table with coded floors
(`household_authority.py`), and a written design for ability manifests and connected accounts:
[zoe-abilities.md](zoe-abilities.md). The lab manifest is deliberately the same idea as that document's
"ability registry"; when the two meet, that document's fields win and the lab format is adjusted.

**Missing.** Consent is per role today, not per person per skill **[code]**. There is no revocation event, and
no single enforcement point: each feature checks for itself.

### 2.4 Forget, as a kernel guarantee

**Contract.** When a person asks to forget, their events are gone: from replay, from export, from every
subscriber's queue, from events that were derived from them, and from the bytes on disk. A subscriber that was
offline during the forget and replays later gets nothing. Nobody can bring those words back into an event
concerning the forgetting person. Another member may use the identical sentence privately elsewhere.

**Prototype [lab]** (`spine.py` `forget`): only the person (or a child's guardian) may ask. The kernel
1. collects every event about the person, including events that only mention them;
2. follows the "derives from" links to everything built on those events, to any depth;
3. erases the payloads in place (SQLite `secure_delete`, then a write-ahead-log truncate) and marks the rows;
4. withdraws matching events from every live queue;
5. tells every subscriber, whatever its filters, with a notice that carries no content, so a well-behaved skill
   drops its own derived state. The supported subscriber cache (`Subscription.cache`, `Context.cache`, and the
   socket client's cache) stores sequence numbers only and reads bodies from the authorized live log. Forgotten
   source and derived events disappear from that view immediately, without waiting for a notice or `pump()`;
6. keeps a keyed hash of the forgotten free text, first-hand and derived alike (text that only ever lived in a
   skill's derived event is forgotten too). A skill that later tries to publish the same words, even nested
   inside another payload (a list, a tuple or a dictionary), is refused when the event concerns that person.
   The hash belongs to the forgetting person, including text that lived only in a derived event about
   someone else. Trusted adapters also obey the shield and cannot lift it by recording the words again;
7. reaches the action bookkeeping: an intent row tied to an erased event keeps no person, skill, action,
   grant or cause. A still-live sequence number stays as a bare marker, so forgetting only a receipt does not
   make the person's yes usable again. Whole-member erase also deletes all that member's intent rows,
   including rows with no event links, and erases indexed grant and refusal records.

**Per-person publication rule [code, lab verification pending].** For a private event, concerning A means
that A is its subject, a declared mention, its source identity, or a name appearing as whole words in a
payload string or dictionary key, at any depth (including tuples). The person identifier is the lab's
name; aliases and pronouns are not resolved. Household and kernel visibility are checked against every
person's shields even when B is the subject: A could receive them. A private event solely for B may carry
A's forgotten sentence or third-party name if it neither names nor mentions A. This applies to individual
publishes, batches, trusted origins and kernel-service appends. A refusal whose metadata would itself
repeat forgotten content is stored as an anonymous marker. Existing cause checks still reject any
reference to an erased event.

Whole-member erase removes all indexed events concerning A and their descendants. It shields their old
free text for A; it does not ban A's identity or prevent fresh events with different text or new consent.
The durable notice and ledger use a keyed owner hash instead of A's identifier. In-process skill contexts
resolve the hash against the already-known roster; consumers unable to resolve it must discard cached state
conservatively. Scoped notices retain their subject and erased-event list; a later whole-member erase also
erases those notices. The roster is consent configuration in memory, not a table erased by forget.

**Lab store upgrade.** Existing `shield` rows remain household-wide forever; they cannot safely be
attributed to one owner. Existing `entity_shield` rows also remain household-wide. New rows use indexed
`(fingerprint, owner-hash)` tables, so publication work depends on the incoming payload, not log or shield
history. Old grant/refusal/forget payload references are indexed once on opening the store, using the
live-kind index. If a whole-member erase removes the owner metadata needed to evaluate a legacy entity
hash, the store retains a permanent fail-closed marker: it refuses all named/string payloads globally.
This deliberately over-blocks legacy stores rather than forgetting how to recognise an old forbidden name;
new stores never need that fallback. Legacy global shields are an explicit exception to B's new freedom.

**Scratch schema footprint [code; coordinator measurement].** Fresh stores use `WITHOUT ROWID` for
cursors, metadata and retired counts, avoiding separate primary-key B-trees. Person links have primary
key `(person, seq)` and cause links `(seq, cause)`; both are sets, inserted with `OR IGNORE`. Their primary
keys replace `people_person` and `causes_seq`; `people_seq`, `causes_cause`, `latest_receipt` and every
intent index remain. Existing stores keep their old table layout/indexes. The coordinator measured
**209,288 bytes including DB/WAL/SHM** after bootstrap and two refusals; this is not the DB schema
alone. Relative to the branch's HEAD schema, the merge adds `snapshot_dependencies`,
`snapshot_receipt`, `opaque_snapshots` and `opaque_receipt` (four additional B-trees).
Relative to MERGE_HEAD it also adds `scoped_shield`, `scoped_entities`, `forget_subject` and
`intent_person`, while WITHOUT ROWID layouts remove redundant primary-key trees. Per-object byte
attribution is unmeasured. The oracle matches the boundary test: no 4096-byte run of the 200,000-byte
body on disk, and DB/WAL/SHM total below twice its length. Complexity budgets and both index pins stay.

**Conflicting prior guarantees [code; coordinator verification required].** First-hand reassertion does
not lift a shield. Explicit `release` is also refused, even for the member: "forgotten means forever"
wins until the owner defines an exception. A member may give new consent and say different things;
deliberately repeating the old exact sentence or forgotten entity remains blocked for that member.
**Owner question:** should deliberate relearning ever override "forgotten means forever"? If yes, what
explicit authenticated instruction permits it, and does it lift text, entity, or both? Pending an answer,
all remain blocked; another member's private use still follows "per-person scoping is required".

**Kernel-driven external cleanup [code; lab tests added, verification pending].** Trusted bootstrap
registers a store's idempotent purge and independent read-back with `Kernel.register_store_purge`.
After validating the person's forget authority and computing the erased rows, the spine invokes the
kernel under its lock and SQLite transaction, BEFORE tombstoning source/grant records. Whole-member
cleanup purges every row owned by the member, even if the skill lost its cache or ignores the notice.
Scoped cleanup receives the erased events while their request/receipt keys still exist and purges only
matching request rows across all owners. The reminder fake-store adapter implements both paths; the
skill's notice handler drops local state and replays idempotent notice-bound deletes. No ordinary
action or receipt is emitted for registered cleanup, and no grant is resurrected. Context exposes only
the reviewed ID-restricted replay delete, never registration or arbitrary store-purge callbacks.
The closed wire operation table exposes no purge; ordinary actions still require live consent.

A purge exception or failed read-back aborts forget and leaves consent/source records live for retry.
External stores cannot commit atomically with SQLite: if a later step fails or the kernel crashes, an
external purge may already have happened while log erasure rolls back. Purges must be idempotent; retry
finishes erasure. A successful forget promises verified cleanup of registered stores. Unregistered stores,
lying drivers and unsupported private process-memory copies remain outside this prototype's guarantee.
No production store or live service is changed.

**Forget scope and undo copies [code; regression runs pending].** Every notice carries `scope`
(`person`, `seqs`, `entity`, or `topic`) and `seqs`, including an explicit empty array when nothing
matched. It carries identifiers and counts, never the matched words. Selected sequences include the
derived closure. They are sorted and split into batches of at most 256; all batches, the tombstones,
snapshot scrubs, shields and ledger entry commit in one SQLite transaction before delivery. Each batch
is independently idempotent. The kernel acknowledges a skill's cursor only after its handler returns;
a restart resumes remaining batches, and an interrupted handler can receive its batch again. External
stores must survive or be restored separately; this does not make arbitrary external writes transactional.
Reminder request IDs derive from sequences, so notice replay can purge its surviving external table even
when the restarted skill has lost its in-memory item cache, including after consent erasure.
Whole-person notices apply through `upto`, so replay does not erase newer rows. Missing, unknown, or
malformed legacy scopes purge that person's subscriber state: over-erasing a store is safer than retaining
possibly forgotten content. Keeper, reminders, twin, leaving-catch and calendar-heads-up share this rule.

List, reminder and alarm driver snapshots now declare versioned per-row request provenance (`asked`).
The kernel sets provenance: after authorising the action and its cause, the dispatcher overwrites
`asked` with the single validated `caused_by` sequence (and fills a declared `asked` if omitted).
Actions using this scalar provenance refuse multiple causes before any driver write. Drivers use
that value; reminder skills learn it from kernel receipts. A refusal never stores caller text:
unknown or unauthorised actions use `unknown_action`, unverified identities use `unverified-peer`,
and errors use fixed reasons. Audit findings cite source sequences rather than copying claim action
text; kernel appends check both content and name shields, including argument keys, and derived
records retain subject/cause links so forget reaches them. These changes are statically reviewed only;
the new boundary regressions have not been run.

Forget scrubs affected rows from surviving undo snapshots and from undo read-back proofs. Undo then
restores only survivors; it does not depend causally on every old row (which would erase unrelated later
actions). Entity/topic matching excludes these before-state copies and handles them through the same scrub.
For older or opaque snapshots without complete provenance, a nonempty forget concerning the receipt's
person removes the saved before-state and refuses undo explicitly. The surviving action's arguments
remain available to reminder restoration. Empty selections invalidate nothing.
Queued receipts refresh their bodies after forget, so they cannot deliver the pre-scrub snapshot.

**Action argument shields [code; tests not run].** The dispatcher checks every action's arguments
before writing an intent or running a driver, using the same `_payload_refusal` as publish:
`_shielded` and `_names_forgotten` visit string values and dictionary keys through nested
dictionaries, lists and tuples. Shield scope is unchanged (word hashes remain household-wide).
Undo checks both saved arguments and the complete before-state before opening its intent or restoring
rows, in addition to the provenance scrub above. Refusals carry only the fixed code
`forgotten_action_arguments`, never argument text. Receipts for actions using `asked` also expose
the kernel-bound sequence directly; driver proofs keep their existing shape.
Socket-free regressions live in `tests/test_forget_action_arguments.py`; removing the argument guard
makes the list resurrection regression fail (a planted negative control, execution pending).

| Caller-text write path | Verdict |
|---|---|
| Action arguments to list/reminder/alarm drivers and receipts | Fixed here: shared publish shields before intent/driver. |
| Undo arguments and restored snapshot rows | Fixed here: shared shields before intent/reverse; provenance scrub remains in place. |
| Offers, offer responses and claims | Shielded through `Spine.publish` / `publish_many`; claims also require a matching unused receipt. |
| Brain bridge proposals (including prevalidation disabled), socket `act`, skill `Context.act` | Fixed here through their common `Dispatcher.act` destination. |
| Grant-change records | No free text: registered person/skill/action identifiers, grant IDs, visibility/kind enums and guardian IDs only. |
| Stage-one production bridge events | No free text by contract: IDs and labels only; trusted origins record first-hand reality through publish. |
| Purge driver calls | No free text: one exact request ID derived from a live forget notice, checked before driver invocation. |
| Attention, recovery and audit records | No caller free-text fields: fixed reasons/outcomes, registered IDs and sequence numbers; driver read-back is trusted kernel output. |

Forget-copy and bounded-list sweep (lab implementation, not production):

| Store or bound | Verdict |
|---|---|
| Event payloads, derived events, people/causes links; intent bookkeeping | Reached by tombstone closure and intent scrub; retention skeletons retain links for person/sequence forget. |
| Receipt undo before-state and undo `proof.restored` | Fixed here: provenance scrub; legacy undo invalidation and removal of restored proof. |
| Subscription queues | Fixed here: surviving receipt bodies refresh as well as withdrawing erased events. |
| Subscription/Context/client caches; malicious skill's supported cache | Reached: sequence-only views reread the authorized log; no stored body copy. |
| Brain list/reminder/alarm stores, reminders skill `_items`/`_offered` and fake reminder table | Fixed scoped notice handling; purge by request/receipt sequence. External stores still require their handler and restore procedure. |
| Twin days, room/step labels and computed baseline | Scoped handler fixed. Baselines recompute from surviving facts. Known limit: entity/topic matching after source content expiry cannot select reduced facts; person/sequence forget still reaches them. |
| Leaving-catch item labels; calendar-heads-up pending proposals | Fixed here: select request sequence rather than clearing unrelated state. |
| Attention preferences/budget counters; grants; consumed-receipt markers; shield hashes | Not content copies: settings, counts, consent or IDs/hashes only; not a free-text resurrection path. |
| Replay recordings, generated days, rehearsal/brain driver fixtures and reports | Not runtime stores: synthetic input/output artifacts; arbitrary exported files are outside the kernel erase boundary. |
| Notice 256-sequence bound | Fixed here: complete ordered batches; 260/1,000-event regressions plus old truncated-notice negative control. |
| SQL fetch/newest limits, closure chunks, queue bound, client poll limit | Pagination/replay loops consume all pages; chunk sizes do not cap forget coverage. |
| Topic 12-event and entity six-token bounds | Topic refuses before erasing. Forget/release now refuse oversized entities instead of silently matching a truncated prefix. |
| Explanation ancestor `MAX_CHAIN=64`, pump round budget, twin window | Not erase selectors: bounded explanation, resumable pump and explicit retention; none limits the tombstone closure. |
| Wire frame/reply/subscription limits; argument string limits; digest prefixes | Explicit transport/validation limits or hashes, not silently truncated erase lists. |

Socket-free regression file: `labs/zoe-os/tests/test_forget_receipts_notices.py`. Run it with the
existing forget, undo, reminders, twin-forget and review tests under the builder memory/quiet-box rules.
No result is claimed until those runs, including old-code negative controls, have executed.

**Exists [code].** The production forget is strong and hand-maintained: a ledger that cannot list what it
forgot (`memory_forgotten.py`), a cascade over derived stores (`memory_forget_cascade.py`), an erase gate that
wins races with background writers (`user_erase_gate.py`), and the inventory test (`forget_reach.py`).

**Entity and topic [lab]** (`forget_match.py`, `Spine.forget(entity=... | topic=...)`). The shapes were read in
`memory_forgotten.py` and `forget_topic.py` **[code]** and rebuilt, not imported. An entity is a name of one to
six whole words, case and punctuation blind; the person's events that name it are erased with everything
derived, and a keyed hash of the name is kept per person (never the name), so a skill that writes the name about
that person again is refused, in new words, nested, or as a dictionary key. B can mention that third party
in private events that do not concern A; a household event cannot resurrect it for A. First-hand mentions
also obey the shield; explicit entity release is blocked pending the owner question above.
A topic is its content words, and an event is about it
only when it holds every one; more than twelve matching events is refused whole. A scope with no words is
refused and never widens to "everything".

**Missing, and the honest limits of the lab version.** The supported cache is kernel-owned; a private body copy
outside that cache violates its contract. Python cannot prevent arbitrary copying in the same process or erase
another program's memory. Separate derived stores need registered kernel purge drivers (the reminder fake store)
or still depend on cooperative forget handlers (the twin); arbitrary stores are not automatically kernel-owned.
**Reworded content is not caught**:
a paraphrase that avoids the name and the exact old
sentences, a near spelling, part of a name, a pronoun or a role ("my sister"). A test asserts that the first
three get through, so the limit is pinned. Production has near-spelling probes and asks "did you also
mean ...?" **[code]**; the lab does not.

**Migration.** The spine does not replace the 62 reached stores. It becomes store number 104 on day one
(a new row in `forget_reach.py`, reached by one call), and it shrinks the inventory only as features move
their state onto it.

### 2.5 Intent, plan, act, with receipts

**Contract.** An action runs only through the dispatcher. It needs the skill's manifest, that person's grant,
a cause, and, for any action marked consequential, the person's own accepted offer from this same skill, used
once. After the action the driver reads back what it did. The receipt records what, by which skill, under
which grant, with what proof. A claim ("I moved your alarm") must cite a verified receipt, and one receipt
backs one claim.

**Prototype [code]** (`spine.py`, `receipts.py`): public publishing refuses a claim before append or delivery
unless it cites a verified receipt matching its skill, subject and action, unused by an earlier claim.
That receipt must also be a causal parent, so forgetting the proof withdraws the derived claim.
Batch publishing also refuses two claims spending the same receipt; one invalid claim rejects the whole batch.
Consumption is an indexed pair of receipt/claim sequence IDs, committed with the claim. No person or content
is stored there; forgetting or retiring the claim cannot make the receipt usable again. Forgetting the receipt
still withdraws its derived claim. Startup backfills matching claims whose payloads still exist; consumption
already erased before this bookkeeping was introduced cannot be reconstructed. These review fixes have
regressions in `test_upper_review.py`; their execution is pending the builder memory floor **[code]**.
Lock and result opens in `real_turn.py` walk directories through descriptors with `O_NOFOLLOW`, then open the
leaf relative to the pinned directory. Temporary stack files still use path-based APIs: a hostile same-user
directory rename remains outside the runner's isolation guarantee **[code]**.
Reconstructing the auditor does not reset receipt use. This is a synchronous
publish check; `pump()` need not race subscribers with an asynchronous audit. `ClaimAuditor.scan()` remains a
diagnostic for legacy or corrupt rows that bypassed public publishing, not the enforcement mechanism.
A driver that reports success and did nothing gets an unverified receipt and cannot back a published claim.

**Exists [code].** This is the area closest to done in production. `write_receipts.py` and
`write_claim_guard.py` are exactly this idea for five kinds of write, enforced where every brain turn ends
(`brain_dispatch.py` `_claim_guarded`, line 915). `anticipation.py` already offers before acting.

**Durable [lab].** Before the driver is called the dispatcher writes an intent row; the receipt closes it in the
same transaction. An intent still open when the kernel starts gets a receipt with outcome `uncertain` and an
audit finding; no driver is called and nothing is run again. The used-yes register and the auditor's cursor are
in the database. Tested by killing the kernel process before the write, and after the write but before the
receipt. "Durable" means against a killed process: SQLite runs with `synchronous=NORMAL`, so power loss was not
tested and is not claimed.

**Missing.** A receipt in production today proves "a row was written", in process memory, for the claim guard's
use **[code: `write_receipts.py`]**. It does not record under whose consent, it is not durable, and device
actions and messages have no read-back. In the lab an `uncertain` receipt is where the kernel stops: resolving
it (a read-back, or asking the person) is not built. "Plan" is not prototyped at all: the lab goes from an accepted offer
straight to one action **[vision]** for multi-step plans.

### 2.6 Attention budget

**Contract.** A skill publishes an offer. It does not choose how the offer reaches the person. The surface asks
the kernel, which answers `card` (silent, the default), `spoken` (only if this person turned unprompted speech
on, outside their quiet hours), or `digest` (budget spent or quiet hours). Only the person, or a child's
guardian, can change the setting. Each decision is recorded with its reason.

**Prototype [lab]** (`attention.py`): the above, with a per-day count. Deliberately small.

**Exists [code].** The owner's standing decision is that Zoe does not speak unprompted
(`docs/VISION.md` principle 10; flag `ZOE_PROACTIVE_SPOKEN`, off). It is one global switch
(`proactive/engine.py` line 218). Per-member preferences exist for change notices (`comm_prefs.py`), and
`restraint.py` withholds sensitive material. The kernel service generalises the per-member model to every
offer and makes "off" the per-person default rather than a household flag.

**Missing.** One budget across all sources; a per-person spoken setting; a record of why something was said.

### 2.7 Memory with authority, and self-care

Not prototyped. Named here so the map is complete, and because they are the parts to change least.

**Memory [code].** `memory_authority.py` already enforces "who said a fact decides who may change it" at one
choke point, and `MemoryService` (`ingest` line 1516, `search` line 2002) is already a service with one way in.
As a kernel service its change is small: memory writes and retirements become events with the authority rank
as a field, so that the spine's forget and memory's forget are one operation instead of two that must agree.

**Self-care [code].** Health checks (`scripts/maintenance/zoe_health.py`, `platform_health_check.sh`), the
felt scorecard (`labs/felt-scorecard/`), the struggle detector (`struggle_detector.py`, `turn_outcomes.py`)
and the self-repair work in the roadmap. As a kernel service **[vision]**: health findings and audit findings
are events (the lab already writes `audit_finding` and `refusal` events), so "what went wrong today" is one
replay rather than a search across logs.

## 3. The three toy skills [lab]

Written against the kernel with invented people and words, to prove the shape end to end.

- **Leaving catch** (`skills/leaving_catch.py`). An undone "take with you" routine step, then a presence
  event "left" with confidence 0.8 or more, produces an *offer* naming the item. Its manifest declares no
  actions, so it could not act if it tried. It stays silent when nothing is undone, when someone else left,
  when the confidence is low, and after the person forgot the routine.
- **Calendar heads-up** (`skills/calendar_headsup.py`). A new event early tomorrow produces an offer to move
  the alarm. Only an accepted answer leads to `alarm.move`, and the kernel checks that itself: the action is
  refused unless its cause is that person's unused yes to this skill's own offer. Then a verified receipt,
  then a claim that cites it.
- **A malicious skill** (`skills/malicious.py`) that one person enabled. It tries to read another person's
  private events (three ways), act without a grant (two ways), and bring back a forgotten subject (five ways:
  citing the forgotten event, citing something else, nesting the words, posing as the voice adapter, and
  writing a kernel-only kind). All ten are refused and all ten refusals are events with no content in them.

## 4. Trust boundary: what the socket defends, and what it does not

The first version ran in one Python process, where a skill could call a private method or read the database
file. That is no longer the only shape **[lab]**: `zoe_os/server.py` serves the kernel over a local Unix socket
from its own process, and a skill is another process that is handed a socket path and one credential.

**The protocol** (`zoe_os/wire.py`). A frame is a four-byte length and one JSON object. Version 1. Eight
operations and nothing else: `publish`, `subscribe`, `poll`, `forget`, `grant`, `revoke`, `act`, `explain`,
each with a fixed list of arguments. No request names its caller: the kernel proves who is on a connection once
(a challenge and a keyed answer, so the credential never crosses the socket) and uses that identity for every
request. `forget`, `grant`, `revoke`, `undo`, `explain` for a person (and the authority's `erase_member` /
`erase_item`) are a person's decisions and are accepted only from a credential issued to a trusted surface.

**A surface cannot speak for a person [lab].** The surface's word for WHO decided is not enough: until
2026-11-16 any holder of the panel credential could `forget(subject=X, actor=X)` for every member, and grant,
revoke, undo or explain for them. Now each person-bound request carries a **speaker assertion**
(`zoe_os/speaker.py`): a token `{person, issued_at, nonce, audience}` with an HMAC-SHA256 under the secret of
the `speaker-verifier` identity, minted by the component that actually verified the person (in production,
zoe-data after a voice match or a panel session) and relayed by the surface, which cannot mint one. The kernel
checks it in ONE place (`KernelServer._prove_speaker`, at the dispatch, so the shadow and authority daemons
inherit it): the MAC (constant time), the person (the request's speaking field: `actor` for forget and undo,
the guardian for a child's grant, else the named person; the kernel's own rule then links that speaker to the
subject), the audience (`forget.person`, `forget.scoped`, `grant`, `revoke`, `undo`, `explain`: "forget that"
cannot become "forget me"), age under 60 s (2 s skew), minted for THIS kernel run's generation (a random id
fresh at every start, published with the secret), and a nonce used once
(kept until its token's own issue time + 60 s + 2 s, so it outlives the token's acceptance window). The authority's
`list_digest` (every member's list rows, for the reconcile tool) answers only a dedicated `reconciler` credential, with
digests keyed by a per-start secret over member, list and item, so no surface can confirm a guess at anyone's list.
Every failure is one refusal, `speaker unproven`, counted by a fixed reason name; the token is never logged.
The member-delete path's `eraser` credential needs no assertion, for the forget family only. The secret is in
`identity-creds.json` (0600, apart from `creds.json` and `erase-creds.json`), fresh at every start. Used
nonces live in memory (bounded at 1024, a full store refuses); a restart loses them, which is safe because a
new run has a new generation and refuses every token minted for an earlier one, whatever the clocks did and
even under a provisioned secret. Within a run, verification time never goes back (`max(last seen, wall)`), and
a wall clock seen to step back past the skew rolls the generation mid-run (republished at once), so an
accepted token is never accepted again for any sequence of wall-clock values. A guardian link grants reach (forget, undo, seeing private events, signing a
grant) only while the kernel's own roster says the subject is a child. Organising never includes forget, undo or explain;
forgetting on someone's behalf is a separate named power, today offered only to a child's guardian, to be replaced by
memory care (not built yet). A person with no stated or an unknown role is LEAST access, never adult. There is no setting
that turns the check off. What it does not defend: a process holding the identity secret can mint for anyone,
so that secret must live only with the code that verifies people.

**What was attacked [lab]**, each refused and recorded, each with a control that removes one mechanism and
shows the attack working:

| Attack from another process | Control that makes it succeed |
|---|---|
| the malicious skill (read, act, resurrect); the kernel-owned cache loses forgotten text | the same four as in-process |
| forged identity: another skill's name, a `source` field, a skill granting itself | no credential check |
| replayed credential: a captured handshake, a credential that was re-issued | a predictable challenge |
| oversized frame (refused before its body is read) | no size cap |
| 23 malformed frames | no validator |
| slow clients holding every connection | no frame deadline |
| a client that dies mid-frame, after a whole request, or by a kill | subscriptions not released |

**What the socket does not defend.** A skill running as the **same OS user** as the kernel does not need the
socket: a lab test has the skill ask the socket for the kernel's process id, read `/proc`, open the database file
and find another person's private words, and the test asserts that this **succeeds**. What stops it is file
permissions and a separate OS user. Two tests show that (a skill started as a second user still speaks the
protocol and is denied the file) with a control (loosen the modes, it reads the file). They run only as root and
skip otherwise, because a sandbox with one user cannot prove it. They were run in a root sandbox with a
throwaway user, not with the service users of [p0-deroot.md](p0-deroot.md), which remains the prerequisite,
the same way it is for the credential broker ([credential-vault.md](credential-vault.md)).

Also not defended: a flood of failed handshakes (each writes a refusal event, so a local process that can reach
the socket can grow the log; no rate limit); a credential stolen from the skill it was issued to (it is not
bound to the peer's OS user); anything that needs more than one kernel process.

## 5. Measures

Read on the development box (a Jetson Orin that was also running the live assistant and other builds at the
time), single process, synthetic events of about 60 bytes of text. A lab reading of a prototype, not a
service level.

| Measure | Value | How |
|---|---|---|
| Publish, one durable commit per event | 7,436 events/s **[lab]** | 10,000 events through the real enforcer |
| Publish, batches of 1,000 in one transaction | 12,474 events/s **[lab]** | 90,000 events |
| Delivery to a subscriber that fell behind | 44,739 events/s **[lab]** | 10,000 events caught up from the log, queue of 256, lagged once, none lost |
| Replay of 100,000 events through the capability check | 3.08 s **[lab]** | one reader granted by all 20 synthetic people |
| Forget latency | 368 ms **[lab]** | one person's 5,000 events out of 100,000, including the on-disk erase; replay afterwards returned 95,000 events plus the notice |
| Log size | 15.3 MB for 100,000 events **[lab]** | about 150 bytes per event with indexes |
| Refusals in the attack test | 10 of 10 attempts refused, 10 refusal events, 0 carrying content **[lab]** | `tests/test_attack.py` |

One run, 14.4 s wall clock, 2026-11-14 (`python3 labs/zoe-os/bench.py`). One run is one reading: no spread was
measured. For scale **[inferred]**: a household produces thousands of events a day, not thousands a second, so
throughput is not the constraint; the number that matters for the bridge is the added time at a call site on
the live voice path, which is **not measured**.

Tests of the first version **[lab]**: 84 tests in 7 files, all passing, run twice (local time zone and
`TZ=UTC`), 1.3 s on that box. Twelve of them are negative controls.

### 5.1 The socket hop

Read in a **cloud sandbox, NOT the target hardware**: 4 virtual cores of an Intel Xeon at 2.1 GHz, 15 GB of
memory, Linux, Python 3.13.18, otherwise idle (load average 0.5). The Jetson was not available to this work, so
these say only that the hop is not obviously too slow. One kernel process, one client, no concurrency, one
request at a time, each timed from send to reply (`python3 labs/zoe-os/bench_socket.py 2000`). Three runs; the
table gives, for each row, the run whose p50 was the **middle of the three**, and the range of p50 across the
three runs. Microseconds.

| Request | n per run | p50 | p95 | p99 | max | p50 across 3 runs |
|---|---|---|---|---|---|---|
| `publish` (one durable commit) | 2,000 | 138 | 222 | 615 | 10,313 | 135 to 174 |
| the same publish in process, no socket | 2,000 | 29 | 61 | 119 | 10,242 | 28 to 31 |
| `poll`, one event waiting | 2,000 | 90 | 136 | 251 | 2,583 | 89 to 95 |
| `poll`, nothing waiting | 2,000 | 81 | 112 | 177 | 414 | 67 to 82 |
| a refused `publish` (also writes the refusal event) | 2,000 | 133 | 181 | 304 | 13,261 | 128 to 134 |
| `explain` (own standing) | 2,000 | 208 | 337 | 448 | 805 | 193 to 232 |
| `act` (includes the test device's own `fsync`) | 200 | 933 | 1,554 | 9,293 | 9,805 | 872 to 1,097 |
| connect and handshake | 200 | 434 | 855 | 1,525 | 2,438 | 392 to 455 |

Reading it **[inferred]**: the hop costs about 0.1 ms at the median on this machine (138 against 29 for the same
publish), and the tail is the database commit, not the socket (the in-process maximum is the same 10 ms).
`explain` scans every refusal on the log, so its time grows with the number of refusals; it was measured with
almost none on the log, which is its best case. The `act` row is dominated by the fake device syncing a file and says little about the kernel.
Not measured: concurrent clients, a busy box, the Jetson's storage, a cold start, memory.

The older table above was re-read once in this sandbox for scale, not as a replacement: 12,206 single publishes
per second, 54,558 batched, forget 195 ms (one run, `python3 labs/zoe-os/bench.py`).

Tests now **[lab]**: 181 tests in 12 files, all passing as root (three runs plus one with `TZ=UTC`); 179 passed
and 2 skipped as an ordinary user (the two second-user tests). Thirty-six are negative controls that remove one mechanism and assert the specific bad outcome.

## 6. The bridge plan: five call sites, no rewrite

Stage 1 puts five publish calls into production code, behind one new flag (working name `OSLAB_SPINE`,
default `off`, to be registered when the first call lands). Nothing subscribes. The adapters are written and
tested in the lab (`labs/zoe-os/zoe_os/bridge.py`); **no production file is changed by this record.**

Two rules: the bridge carries **ids and labels only** (no utterance, reply, title or message text), and it is
**never in the caller's way** (`Bridge.publish` cannot raise and cannot wait).

| # | Event | Exact call site **[code]** | In scope there | Wiring note |
|---|---|---|---|---|
| 1 | `turn_end` | `services/zoe-data/brain_dispatch.py`, `_tracked_stream`, the `finally` block, beside `_provenance_commit(...)` (about line 912) | `user_id`, `session_id`, `tools` (tool names from `reply_ledger.tool_names`), `replay` | One call after the provenance commit. Skip when `replay` is true: a replay-gate turn is not a household event. This is the single place every brain turn ends (voice, chat, Telegram), which is why the claim guard also lives here. It is on the voice path, so it is replay-gated like any other change there. |
| 2 | `reminder_fired` | `services/zoe-data/proactive/triggers/reminders.py`, after `await fire_notification(...)` (about line 176) | `user_id`, `item_id`, `pending_id` | Publish after delivery returns, so the event means "fired", not "was due". Alarms and timers follow the same shape at their own fire sites in a later step. |
| 3 | `calendar_change` | `services/zoe-data/calendar_service.py`, `create_event_record`, beside `write_receipts.record(user_id, "calendar", event_id, ...)` (line 141); then the update and delete handlers in `routers/calendar.py` (lines 335 and 439) | `user_id`, `event_id`, the event's visibility and start | Start with create: it is the one shared writer. The receipt call beside it shows the pattern is already accepted at this exact line. |
| 4 | forget | `services/zoe-data/memory_service.py`, `MemoryService.delete_user` (line 2155), inside the member's erase gate; and `memory_forgotten.py` `add` (about line 421) for an entity forget | `user_id`, `actor` | **Not best effort.** A forget that cannot reach the spine must fail loudly, like every other reached store. Register the spine in `forget_reach.py` in the same change, or the inventory test fails, which is the intended guard. |
| 5 | `presence` | `services/zoe-data/voice_session.py`, `publish(events)` (line 528), which already walks the open, renewed and closed frames for each panel | `member_id`, `panel_id`, `state` | Map open and renewed to "present at this panel" with the speaker gate's confidence; closed is "session ended", **not** "left". A true "left" needs the presence engine (2.2). |

Order of work, each step its own small change behind the flag:
1. the spine as a module inside the service with no publisher (the registry row for forget goes in here);
2. call sites 2 and 3 (off the voice path), then read the log for a week: volumes, no errors, no latency;
3. call site 4, with a restore drill on a copy;
4. call sites 1 and 5 (voice path: replay-gated);
5. only then a first subscriber, in shadow: the claim auditor, which reads and reports and changes nothing.

## 7. What to build next, in order

Done in the lab since the first version: the process boundary (section 4), forget by entity and topic (2.4),
durable receipts (2.5). What follows is the order from here.

1. **Wire one production call site, flag off.** The exact step is 7.1 below. It is described, not made.
2. **The second OS user on the real box.** Run the kernel under its own user, per
   [p0-deroot.md](p0-deroot.md), and repeat the two second-user tests there. Until then the file is one
   `open()` away from any skill.
3. **Bridge steps 2 to 4** of section 6, each behind the flag, with the measure at each call site.
4. **Durable grants and manifests.** They are in memory: after a restart everything is denied until granted
   again. That fails closed and it is not durability.
5. **Attention as the one gate** for offers from `anticipation.py`, with the per-person setting.
6. **Presence engine** per its own design record, publishing to the spine; then the leaving catch for real.
7. **Skills as packages** with manifests, starting with one that exists (anticipation) rather than a new one.

`docs/ROADMAP.md` owns the order of all work. This list is the order *within* this design if it is taken up.

### 7.1 The exact next step: `reminder_fired`, one call, flag off

Call site 2 of section 6, chosen because it is off the voice path and already has the ids in scope
**[code: `services/zoe-data/proactive/triggers/reminders.py`, after `await fire_notification(...)`, about line
176]**. Nothing below has been done.

1. **A kernel unit.** A systemd unit that starts the lab kernel's server as its own process, with its database
   in a directory only that unit's user can read and its socket in a runtime directory the zoe-data user can
   reach. Its manifest list has one entry: an origin adapter `bridge.reminders` that may emit `reminder_fired`.
   The unit hands zoe-data that one credential (a file readable by the zoe-data user only).
2. **A flag.** `OSLAB_SPINE`, default `off`, registered in the flag registry in the same change
   (`docs/knowledge/flag-registry.md`).
3. **A bridge module in zoe-data** that owns one client connection and a small bounded queue with one worker
   thread. The lab client is blocking, so the call site must never talk to the socket itself.
4. **The one call.** After `fire_notification` returns: build the event with the lab adapter's shape
   (`bridge.reminder_fired(user_id=..., item_id=..., pending_id=...)`, ids only) and put it on the queue without
   waiting. Queue full, kernel down or refused: drop it and count it. The reminder is never slower or less
   reliable because of the spine.
5. **Nothing subscribes.** No skill, no reader. The forget registry row (`forget_reach.py`) goes in with the
   first change that can store a person's events, which is this one.
6. **What to measure before turning the flag to `on` anywhere**, on the Jetson, with the live assistant
   running: the time the call adds at the call site (it should be an enqueue: microseconds); publish p50, p99
   and maximum through the socket under a voice turn; events per day; dropped events; the kernel's memory and
   its database growth over a week; and that a reminder still fires with the kernel unit stopped.

## 8. Where the vision outruns the proof

- **Isolation** is proven for the protocol and not for the file (section 4): a skill running as the same OS
  user reads the database around the kernel. The second-user result is from a sandbox, not the real box.
- **Grants, manifests and credentials** are in memory. A restart denies everything until granted again.
- **Power loss.** Durable means against a killed process. `synchronous=NORMAL` was not tested against a crash
  of the machine.
- **An `uncertain` receipt** has no resolution step.
- **Speed on the live path** is not measured. The lab numbers are for a quiet machine, and the socket numbers
  are from a cloud sandbox, not the Jetson. They say only that the design is not obviously too slow.
- **The real modules** have not published a single event. The call sites were read, not run.
- **Presence, memory and self-care** as kernel services are a map, not a build.
- **Forget** cannot reach a copy a skill already took, and does not catch reworded content, near spellings,
  part of a name, or a pronoun.
- **Plans** (more than one action from one intent) are not designed here.
- **"Undo that"** means the person's last action (owner decision 2026-11-14). If that action cannot be reversed
  the answer says so; nothing older is reversed in its place.
- **Retention** has a lab mechanism and a chosen policy (section 9, option B) and **no scheduler**: nothing
  expires until something calls the sweep. **Schema change and backup** of the spine are not designed; a backup taken before
  an event expired or was forgotten still holds it.
- **The manifest format** is a lab format. It has not met a real third-party skill.
- **One writer.** One kernel process with one lock. Several clients were tested for correctness (a slow one
  does not block an honest one), not for throughput.
- **The household twin** (sections 11 and 12) is tested only against days its own author generated. Nothing it
  shows is evidence about a real household, and who may be shown a line about a person is not decided.
- **A consent gap the rehearsal found:** when two people enable the same skill, the kernel lets it derive
  something for one of them from the other's private event. The rehearsal reports it; the kernel does not stop it.
- **"OS"** is a direction. What exists is a kernel and a twin of about three thousand five hundred lines in a
  lab directory.

## 9. Owner decision: how long the log is kept

**Status: decided by the owner on 2026-11-14: option B ("balanced"), as recommended below.** The lab kernel
holds it (`retention.POLICY`, `kernel.retention`) and a test pins the numbers. Presence at 14 days was flagged as
the least certain number and stays the first one to revisit once real volumes are measured. There is still no
scheduler: events age only when a sweep is called. The options that were not chosen stay here as the record.

A log of every household event is itself sensitive, and "private by default" says who may read an event, not
how long it exists. The mechanism is built and tested in the lab (`labs/zoe-os/zoe_os/retention.py`) **[lab]**.

**The mechanism.** Each kind of event has two ages and passes through three stages:

| Stage | What exists | What it still allows |
|---|---|---|
| 1. Full | the event as published | everything |
| 2. Skeleton | sequence number, time, kind, and the links to whom it concerned and what derived from it. The content and the subject are erased from the row and from the disk | a forget still reaches everything built on it; counts per person; "something happened here" |
| 3. Count only | the row is deleted. One number per day per kind, about no one | "42 utterances that day" |

**How a forget interacts [lab, each tested with a planted sentinel]:**
- A forget still reaches what was derived from an event whose content has aged out. That is why stage 2
  exists, and why a skeleton is not reduced to a count while anything derived from it is on the log. Two negative
  controls show the forget silently missing a copy when either rule is removed.
- A forgotten event is later deleted without being counted. The forget notice never expires, so a subscriber
  that was offline for years is still told. The keyed hashes of forgotten words and names are kept: they are
  what stops the words coming back.
- Expiry is not a forget: nothing is shielded, and the same words may be said or derived again.
- **Limit:** a forget by entity or topic matches content, so it cannot match an event whose content has aged
  out. A forget of the person still reaches it. A test pins this.

**The options.** Days kept in full, then further days as a skeleton. Forget notices and grant changes are kept
in every option (they carry no content, and the second is the record of consent).

| What | Kinds | A. Short | B. Balanced | C. Long |
|---|---|---|---|---|
| What was said | utterance | 7, then 23 | 30, then 335 | 365, then 365 |
| Where people were | presence, sensor | 3, then 27 | 14, then 351 | 90, then 640 |
| What happened | routine step, calendar change, timer, reminder, turn end | 30, then 60 | 90, then 275 | 365, then 365 |
| What was proposed | offer, answer, routing, claim | 30, then 60 | 90, then 275 | 365, then 365 |
| Proof | action receipt, refusal, audit finding | 90, then 275 | 365, then 365 | 730, then 730 |

**What is lost by shortening it.** Each of these stops working for anything older than the "full" age:
- **"Why did you do that?"** An explanation is built from the chain of events. Expired links become "events you
  may not see"; with the receipt gone there is nothing to explain.
- **"Undo that."** The receipt holds what an undo needs. Expired, the action cannot be undone.
- **Rehearsal and the baseline.** The replay harness and anything that learns "what is normal" only see what is
  still in full. Option A gives a week of words and three days of presence, which is too little to tell an
  unusual Tuesday from an ordinary one.
- **A forget by name or topic** of older events (the limit above).
- **The audit trail.** After the proof age nobody can show that an action was done under a grant.
- **A reader that was offline** longer than the full age never sees what it missed.
- **Debugging.** "It did something odd last month" has no record under option A.

What is *gained* by shortening it is the point of the question: less to leak, less to subpoena, less to be
wrong about, and a smaller answer to "what does it know about me".

**Recommendation: option B, with one change to consider.** Words for 30 days and presence for 14 are long
enough to rehearse a change against a few ordinary weeks and short enough that last season's conversations are
not on the box; proof for a year matches how long a person might reasonably ask "did you really do that". The
change to consider: presence at 14 days is the least certain number, because where people were is both the most
sensitive kind and the one a baseline needs most. This is a recommendation from reading the design
**[inferred]**, not from a measurement: event volumes per day, and how many days a useful baseline needs, have
not been measured on a real household.

**Not decided by this section, and not built:** who may change the policy later and whether shortening it
applies to events already kept; whether each person may choose a shorter age for their own events; backups (a
backup taken before an expiry still holds the content, exactly as `docs/knowledge/forget-reach.md` says of a
forget); the action-intent rows and the forget ledger, which are not events and are not aged; and a scheduler
(the sweep runs only when called).

## 10. Migration ranking: which features to move first

Zoe's existing features, ranked by how easy and how valuable each would be to move onto the kernel. Every fact
in the "evidence" column was **read in code** under `services/zoe-data/` at the path and line given (line
numbers as of this record's base commit); **nothing was run**. The two scores (1 low to 5 high) and the order
are judgement **[inferred]**: they are an argument to check, not a measurement. "Move" means the feature's
store sits behind kernel actions and its rules come from the kernel, as the lab reminders skill shows; it does
not mean rewriting the feature.

| # | Feature | Ease | Value | Evidence **[code]**: files read and the rules it re-implements today | Why this rank **[inferred]** |
|---|---|---|---|---|---|
| 1 | **Reminders** | 5 | 4 | `routers/reminders.py` (498 lines), `reminder_service.py` (282), `proactive/triggers/reminders.py` (346). Its own visibility filter (`routers/reminders.py:88`, used in six handlers); eight `require_feature_access` calls; owner-only snooze (`:334`); household quiet hours via `proactive/engine.py:99`; a receipt at `reminder_service.py:264`. **The `reminders` table is a known forget gap** (`forget_reach.py:150`). | Already written as a lab skill with 14 tests. Off the voice path for firing. Closes a real gap. The exactly-once claim (`proactive/triggers/reminders.py:43`) stays the feature's job. |
| 2 | **Anticipation offers** | 4 | 4 | `anticipation.py` (726 lines), flag-dark `ZOE_ANTICIPATION=off\|shadow\|on` (docstring, line 1). It is already offer, then yes, then the real tool, then a reply built from what the tool returned. Its own member check (`:268`), adult check (`:287`), 24 h cooldown, and its own offer store in `pending_suggestions`. `proactive_candidates` is a forget gap (`forget_reach.py:118`). | The closest thing in production to the kernel's shape, by code only (no model), and already dark. The kernel would replace its private offer store, consent check and cooldown with offers, grants and the attention budget. It surfaces on a chat brain turn, so that part is gated. |
| 3 | **Write receipts, in shadow** | 4 | 5 | `write_receipts.py` (167 lines): receipts live in a process-memory dict (`_STORE`, `:62`), written by one function (`record`, `:120`) that 22 call sites in 11 files use (`skybridge_service.py` 5, `list_service.py` 4, `intent_router.py` 3, `alarm_voice.py` 3, and seven files with one each). | One function is one call site: `record` can also publish a receipt event, so every writer gains a durable, per-person record with no change to the 22 callers. Shadow only: the claim guard keeps reading its own memory. |
| 4 | **Alarms and timers** | 3 | 3 | `alarms.py` (739 lines) with its own `can_see` (`:214`) and `can_edit` (`:218`); receipts in `alarm_voice.py:532`, `:653`, `:673`. Forget already reaches it (`alarms.forget_user`, `:451`; `forget_reach.py:180`). | A natural fit for reversible actions and "undo that". Less to gain (forget already works) and it is on the voice path, so every step is replay-gated. |
| 5 | **Notifications and the proactive engine** | 2 | 5 | `proactive/engine.py` (796 lines): one household-wide quiet window (`_is_in_quiet_hours`, `:70`), one global speech flag (`_spoken_enabled`, `:218`), `fire_notification` (`:80`) with 10 call sites in 5 files. Per-member preferences exist separately in `comm_prefs.py` (`in_quiet_hours`, `:181`). `proactive_pending` holds message text and is a forget gap (`forget_reach.py:169`). | The largest prize: one attention budget per person instead of two mechanisms. Hard because it owns delivery to panels and phones, catch-up and reconcile; a mistake here is a missed or doubled alert. |
| 6 | **Lists** | 2 | 4 | `list_service.py` (909 lines), `routers/lists.py` (570): seven feature checks, 21 lines that mention visibility, four receipt sites. `lists` and `list_items` are forget gaps (`forget_reach.py:146`, `:147`). | Valuable for the forget alone. Household-shared lists need the kernel's household visibility to mean exactly what "family" means today, and list writes are on the voice path. |
| 7 | **Notes, journal, transactions** | 3 | 2 | `routers/notes.py` (269), `routers/journal.py` (434), `routers/transactions.py` (359): each has the visibility filter and 4 to 9 feature checks; all three tables are forget gaps (`forget_reach.py:144`, `:145`, `:151`). | Simple stores, so moving them is mechanical, and the gain is forget reach only: they make no offers and take no actions for anyone. |
| 8 | **Calendar** | 2 | 3 | `calendar_service.py` (449), `routers/calendar.py` (468): two feature checks, per-event visibility, a receipt at `calendar_service.py:141`; `events` is a partial forget gap (`forget_reach.py:127`). | Easy as a *publisher* (bridge call site 3). Hard as a skill: external account sync and recurrence are most of the code. |
| 9 | **People and contacts** | 1 | 3 | `routers/people.py` (1,222 lines, 27 feature checks). | The entity forget cascade runs through it. Large, and tied to memory. |
| 10 | **Presence** | 1 | 4 | `voice_session.py` (540 lines). | Needs the presence engine first (section 2.2). On the voice path. |
| 11 | **Memory** | 1 | 5 | `memory_authority.py`, `memory_service.py` (section 2.7). | The strongest existing part. Change it last and least. |

**Recommended first three, in this order.**
1. **Reminders**, starting with the one call in section 7.1 (publish only), then the store behind kernel actions.
2. **Anticipation offers**, while it is still dark: its offers, answers and cooldown become kernel offers, grants
   and the attention budget, compared in shadow against what it does today using the replay harness.
3. **Write receipts in shadow**: `write_receipts.record` also publishes a receipt event. Nothing reads it. After
   a week of agreement between the two, the claim guard can be pointed at the durable one.

They share three properties: each has a single seam, each can run beside what exists with a flag off, and none
needs the presence engine or a change to memory.

**What must be measured on the real hardware before any of them is wired.** None of this has been measured; the
lab's numbers are from a cloud sandbox (section 5.1).
- **The cost at the call site** with the live assistant running: it must be an enqueue, in microseconds, and
  must not grow when the kernel is slow, stopped or refusing.
- **Publish latency through the socket on the Jetson**, p50, p99 and maximum, idle and during a voice turn, on
  the box's real storage. The sandbox tail (10 ms) was a database commit; the Jetson's is unknown.
- **Memory.** The kernel process's resident size over a week. The box has 15.6 GB shared with the voice brain
  and has run out before (`AGENTS.md`, the Serena section), so this is a gate, not a curiosity.
- **Volume.** Events per day by kind and database growth per week. Section 9's retention numbers depend on it.
- **For reminders:** lateness (due time to delivered) before and after, and that a reminder still fires with the
  kernel unit stopped.
- **For anything on the voice path** (anticipation's surfacing, alarms, list writes, receipts on a brain turn):
  the voice replay gate, said against did and per-stage speed, as `AGENTS.md` requires for every voice change.
- **The second OS user** (section 4): the two file-permission tests, repeated with the real service users.
- **The night window** (`scripts/AGENTS.md`): that a sweep, a forget or a restart of the kernel does not land in
  the brain-window quiet hours.

## 11. The household twin: rehearsing before trusting

**What it is.** A private simulation of a household that the system can rehearse against before it changes
itself or makes an offer. All of it is in the lab (`labs/zoe-os/zoe_os/twin/`), on synthetic days with invented
placeholder people **[lab]**. Section 12 says why none of it is yet evidence about a real household.

| Part | What it does **[lab]** |
|---|---|
| Day generator (`days.py`) | A small household description (people, roles, usual wake, leave, back, dinner and bed times with spread, weekly events) becomes synthetic days of kernel events, deterministic for a seed. Every third working day is awkward on purpose: a forgotten phone, a sick child at home, a guest, a late night, a flight, a missed routine step. |
| Baseline (`baseline.py`) | A kernel skill that reduces a person's private presence and routine events to a few facts a day, and reports what is normal as counts and ranges. It says "not enough days yet" until it has ten working days (six weekend days) for that person. |
| Rehearsal (`rehearse.py`) | Runs the current and a changed version of a skill over generated and recorded days and reports, in this order: privacy failures, totals, and every difference day by day. |
| Unusual today (`unusual.py`) | Lists departures from a person's own baseline as "this differs from the last N days in this way". Fixed sentences filled with times and counts: it cannot give a reason. |

**Consent.** There is no separate "tracking" switch. The twin is a skill, so a person who has not enabled it is
never delivered to it: no baseline, no line about them. It reads private events only, so a guest the household
can see is tracked by no one.

**Forget [lab, tested with a planted sentinel and a negative control].** The baseline is derived from a person's
events, so a forget must change or remove it:
- forgetting a person removes their baseline and leaves everyone else's unchanged;
- forgetting one thing (by name) removes what was derived from those events and keeps the rest;
- a twin that ignores the forget notice still knows what was forgotten. That is the control, and it is also the
  standing limit of any skill: the twin's memory is outside the kernel, and it forgets because it obeys.
- a forgotten person is not banned: new days start a new baseline, from "not enough days yet".

### Owner decision: what the twin keeps, and for how long

**Status: decided by the owner on 2026-11-14: option B, reduced facts for 28 days, as recommended below.** It
is held as `retention.TWIN_WINDOW_DAYS`, in the retention policy and not inside the skill, and a test pins it.
The accepted cost is the one in the table: a reduced copy two weeks older than the kernel's presence events, and
a forget by name that does not reach those two weeks. The twin's reduced facts are a
second copy of what the log held: clock times, rooms, and which routine steps were done, per person per day. No
free text, but a routine item's or a room's label is kept as the name of a count.

One thing makes this a real decision **[lab]**: **the twin's copy can outlive the kernel's**. Under retention
option B (section 9) presence is kept in full for 14 days, and a twin with a longer window still holds what it
derived from days the kernel has already reduced to a skeleton. A forget of the person still reaches the twin
then (tested). A forget by name does **not**: the kernel has no content left to match, so it erases nothing and
the twin keeps the label (a test pins this).

| Option | What is kept | What it allows | What it costs |
|---|---|---|---|
| A. Nothing stored | no twin memory; the baseline is recomputed from the log each time | the simplest rule: nothing outlives the log, and every forget is complete | under retention B, 14 days of presence is exactly ten working days: a working-day baseline that is always on the edge of "not enough", and never a weekend one (it needs six weekend days). Not built: the lab twin keeps its facts in memory |
| B. Reduced facts for 28 days | times, rooms and step counts for four weeks | 20 working days and 8 weekend days: both baselines, with some room | a reduced copy two weeks older than the kernel's presence events; the name-forget limit above applies to those two weeks |
| C. Reduced facts for 56 days (the lab placeholder) | the same, eight weeks | a steadier baseline; a one-off odd day weighs less | six weeks beyond the kernel's copy; the same limit for longer |

In every option: **generated days are never stored** (a seed regenerates them). Replay and rehearsal reports
contain codes, IDs and counts, never offer text, arguments or reasons. A recording of real days written to a file is itself a copy no forget can reach. The lab's
`record` writes such a file because its days are synthetic. For real days the replay should read the log
directly. That is not built.

**Recommendation: B**, and state the twin's window as its own line in the retention policy rather than leaving
it inside a skill. It is the shortest window that gives both baselines. If the owner prefers the simple rule
that nothing outlives the log, choose A and lengthen presence in section 9 to 28 days instead, which moves the
same cost into the log where a forget by name still works. Both are **[inferred]** from the lab's arithmetic
(ten working days, six weekend days), which is itself a judgement and not a measurement.

## 12. The twin's honest limits

**The generator and the things tested against it were written by the same hand.** That is the limit that
contains the others. A day the generator produces is plausible to its author; it is not evidence about any real
household. So, of the twin's lab results **[lab]**:

| What the lab shows | What it does not show |
|---|---|
| The baseline recovers the habits written into the generator (20 working days, the phone step done on 19) | that a real person's days have a "usual" this arithmetic can find |
| "Unusual today" is silent on the generator's ordinary days, for five seeds and a whole month | that it is quiet, or useful, on real ordinary days. Real days are not a habit with a symmetric spread |
| A rehearsal finds the privacy failures planted in three changed skills | that a change which rehearses clean is safe. It did nothing wrong *on those days* |
| A forget removes or changes the baseline | anything about a copy of the baseline made by something else |

**What the generator leaves out, on purpose or by ignorance.** People do not affect each other (nobody is late
because someone else was). Days do not carry over (a late night changes nothing the next morning; an illness
lasts one day; the flight has no return). There is one kind of week: no shift work, shared custody, school
holidays, seasons, or a person whose Tuesday looks nothing like their Thursday. Presence is perfect: every
"left" is true and has the same confidence, whereas real presence is a phone left on a table, a child with no
phone, a sensor that misses. Nothing arrives late, twice or out of order. Clock times are treated as UTC. The six
awkward days are six I thought of.

**What would have to be measured on a real household before any of this is trusted.** Each needs that
household's informed choice, person by person, and none has been done.

1. **Whether the events exist.** The twin needs "up", "left", "arrived", "bed" and rooms. Production has no
   presence engine yet (section 2.2), so today most of these would simply be missing.
2. **How wrong presence is.** For some weeks, compare what the system recorded with a diary the person keeps:
   how often a "left" or "arrived" is wrong or absent. The baseline is at best as good as that.
3. **Whether a person's times have a usual at all.** Look at the real distribution of each time. If it has two
   humps (two work patterns), a median and a range describe neither.
4. **How many days until the baseline stops moving.** This replaces the lab's judgement of ten working days and
   six weekend days with a measured number, per person.
5. **How often "unusual today" is noise.** Each line shown only to the person it is about, who marks it "yes,
   that was unusual" or "no". Decide the acceptable rate before looking.
6. **What it misses.** Things the person says were unusual that produced no line.
7. **Whether generated days predict real ones.** Rehearse the same change on generated days and on recorded
   real days and compare: every kind of line that appears only on real days is something the generator cannot
   rehearse.
8. **Whether anyone wants it.** Asked of each person separately, with "no" as the default and no cost to
   saying no.
9. **The cost on the box.** Time and memory to learn a baseline and to rehearse K days on the Jetson, beside
   the voice brain. The lab's one timing (25 days, two versions, 0.6 s) is from a cloud sandbox.
10. **A forget drill.** On a copy: forget a person, then search the twin, the log and the disk for a planted
    marker, as the lab tests do.

**What must never be inferred.** The twin sees when people are up, in, out and in which room. From that a
system, or a reader, could guess at things nobody agreed to share. These are out of bounds, as conclusions and
as wording:

- **health**, physical or mental: illness, sleep quality, tiredness, pain, medication, pregnancy, anything a
  clinician would name;
- **mood** or state of mind;
- **relationships**: who is with whom, who is avoiding whom, conflict, a partner's absence, a visitor's meaning;
- drinking or any other use of substances; religious practice; money; work performance or whether someone has
  a job;
- a child's behaviour, development or honesty;
- whether someone is safe, or telling the truth about where they were.

And, as rules of use rather than of wording: no comparison between people and no score or ranking of a person;
no "trend" framed as getting better or worse; no decision about a person (a permission, a level of trust) made
from a departure; no offer triggered by a departure ("you were up late, shall I ...") until the owner has
decided that separately; and no line about one person shown to another without that person's own choice.

**What enforces this today, and what does not.** In the lab **[lab]** the departure sentences are fixed
templates of times and counts, there is no code path that produces a reason, a test checks every line from every
awkward day against a list of words, and the awkward-day labels (`sick_child` and the rest) exist only in the
generator's metadata, which the twin never reads. That stops the *system* from saying it. It does not stop a
*reader*: "the child did not leave today" invites the guess. So the rule that matters most is about who is shown
a line.

**Who is shown a line: decided by the owner on 2026-11-14.** The owner asked for the recommendations to be
taken. For this question the record held a rule and no options, so the narrowest reading of that rule was
applied, and it should be confirmed rather than assumed:

- a line about a person is shown **to that person**;
- a line about a **child** is also shown to that child's **guardians**, the same people who may forget, explain
  and undo for the child elsewhere in the kernel;
- to **nobody else**: not another adult of the household, not a guest, not a skill.

In the lab **[lab]** this is `twin/unusual.py` `shown_to`, with a test (one adult is not shown the other adult's
day; a guardian is shown the child's) and a negative control.

**Sharing is the person's choice: decided by the owner on 2026-11-14** ("you can choose to share"). An adult may
share the lines about themself with a named member of the household. It is off by default, one way (sharing
with someone shows you nothing of theirs), revocable at once, and only that person can turn it on or off: not a
partner, not a guardian, not a skill. Each change is a kernel event with names and no content, which the person
can ask about. In the lab **[lab]** this is `Sharing` in the same module, with a negative control (without the
ownership rule an outsider switches it on for themself). The owner's words were about "memories"; what is built
covers the twin's lines about a person and nothing wider, and whether the same choice should govern anything
else a person has stored is a separate question that this does not answer.

Still open and unbuilt: whether an older child's lines should stop going to a guardian, and whether a child's
lines may be shared beyond their guardians at all (today they cannot be, by anyone). The choice is kept in
memory, like grants, so a restart returns everyone to "not shared". And the limit stands: the rule governs what a surface that calls `shown_to` displays. Nothing yet
forces a surface to call it.

## Load, soak and crash results

[code] `labs/zoe-os/bench_kernel.py` is the offline evidence harness, with
`bench_support.py` providing a scratch kernel process and durable synthetic effects.
It uses the brain bridge's real authenticated Unix-socket wire. The driver fixture
is explicitly different from the brain's in-memory stores; success here would not
prove those stores survive restart. Workload seed defaults to 4721.

[lab, coordinator baseline, 2026-11-15, seed 7] Measurements on the real box,
before the indexed-lookup changes below. All latencies are microseconds.

| Verb | p50 | p90 | p99 |
|---|---|---|---|
| list.add | 721 | not supplied | not supplied |
| list.remove | 713 | not supplied | not supplied |
| remind.set | 713 | not supplied | not supplied |
| alarm.set | 716 | not supplied | not supplied |
| why | 896 | not supplied | not supplied |
| undo | 7,726 | 8,231 | not supplied |
| forget | 12,261 | 38,555 | 49,851 |
| denied | 348 | not supplied | not supplied |
| malformed | 289 | not supplied | not supplied |

Undo was previously 27,774 p50 before newest-first paging; forget was 3,995 before
intent scrubbing and derived-event text hashing. These protections remain required.
Concurrency: 1/4/16 clients achieved 1,533 / 1,772 / 1,677 wire calls/s; 16-client
p99 was 17 ms, with cross-member isolation holding. Fifty SIGKILL trials across six
stages passed integrity, no half receipt, acknowledged-write survival and undo after
restart. Cold-start p50 was 129 ms. The 20,000-operation soak exceeded its 290 s budget;
there are no completed soak growth/drift measurements. Profiling was reported null.

[code] Root causes and changes:

- Undo used `_newest`'s 200-event decode and a member-wide `people` subquery;
  `_undone` scanned intents. `Dispatcher.last_action` now selects exactly one live
  receipt by subject using a partial index; `intents.undoes` is indexed.
- Scoped forget read every member event before intersecting `seqs`. Deletes by
  `people.seq` / `causes.seq`, and the three intent references, lacked indexes.
  Selection now includes the requested IDs in SQL; all these predicates are indexed.
  Reads, closure, hashes, erasure, intent scrub, ledger and notice share one transaction.
  Secure deletion, derived text/tuple shields, spent-yes/undo markers and the WAL
  truncation checkpoint remain. No durability setting or claim rule was weakened.
- `why` finds its grant through `_newest`; without a kind index this traversed
  intervening event history. A partial live `(kind, seq)` index removes that work.

[inferred] The nine baseline medians sum to 24.383 ms; their uniform mix costs
about 54.2 s for 20,000 timed operations. Fixture setup adds roughly 20 s at the
measured ordinary-call cost. Thus 290 s is already ample for steady costs (and is
under five minutes); raising it would hide history drift. It remains 290 s. Soak
now retains partial results on timeout, exits nonzero, and reports overall AND
per-verb p50/p90/p99 in each requested-history quarter. Mix changes cannot masquerade
as a slow verb when per-verb quarters are inspected.

[code] A bounded isolated forget and undo no longer scan unrelated completed history.
Broad person/entity/topic forget still visits candidate/erased events and descendants;
open intent cause checks scale with pending intents, not completed history. Checkpoint
I/O depends on WAL state and storage; indexed lookups do not bound physical I/O latency.
Grants and example brain driver effects remain memory-only. SQLite remains WAL with
`synchronous=NORMAL`: process-kill evidence is not power-loss evidence.

[code] `--profile` writes top-level `profile[verb]` and `latency.profile[verb]` for
one extra call of EVERY verb, with setup excluded. Each includes `phases_us` for
decode, consent, SQL (including cursor stepping), commit and receipt, plus raw
counters and total wire time. Zero means no work in that phase. Receipt is inclusive
of SQL/commit; phases overlap and must not be summed. Decode includes wire and stored
JSON; supervisor parsing is excluded. Autocommit timing includes the SQL statement
and SQLite sync; native fsync is not separately observable. No measured attribution
is claimed until the coordinator reruns it.

| Coordinator rerun evidence | After (fill from result JSON) |
|---|---|
| Undo on a 20,000-event history, target p50 < 2,000 us | **PENDING COORDINATOR:** `latency.undo` with `--history-n 20000` |
| Scoped forget on that history, target p50 < 5,000 us | **PENDING COORDINATOR:** `latency.forget` with `--history-n 20000` |
| Completed soak | **PENDING COORDINATOR:** completed calls, seconds, RSS/DB growth, quarter timings |
| Profile | **PENDING COORDINATOR:** per-verb phase timings from `profile` |
| Regression checks | **PENDING COORDINATOR:** test counts/exits, concurrency, crash and cold results |

[code] The small tests include deliberate wrong-member routing and a volatile effect
store. The crash oracle checks actual effects as well as acknowledged receipts; the
concurrency oracle compares stored member/key pairs against controller-owned expectations.
The fixture pauses inside one of six shuffled crash phases before SIGKILL, including
inside an effect transaction and inside a receipt transaction. It never treats an
unacknowledged interrupted write as known done. See the lab README for commands and
measurement boundaries. Full runs and all existing tests green remain unproven.

[code] Socket-free complexity guards in `tests/test_history_cost.py` build 200 and
5,000 events plus completed intents and links. SQLite progress callbacks count VM
steps, including work hidden inside a single statement; fixed budgets cover full
undo, absent-member lookup and isolated/receipt forget. Negative controls restore
the old paged undo or issue an unindexed scan and must trip the same budget.
Rollback, open-intent scrubbing and body absence with a loose 400,000-byte DB/WAL/SHM
bound after two refusals are checked. Startup truncates schema-creation WAL page images; the seven
additive indexes are installed idempotently on opening existing lab stores.
The existing oversized-frame scenario and all socket tests still need the coordinator.
## Snapshot dependency index (lab)

Receipt appends index both undo-before rows and restored read-back proofs in
`snapshot_dependencies(source_seq, receipt_seq)`, a WITHOUT ROWID table whose
primary key serves chunked source lookups. The receipt index supports deletion.
Forget visits only dependent receipts, removes their old edges, and indexes the
surviving rows again, in the erase transaction. Tombstones remove incoming and
outgoing edges. Opaque snapshots containing text use a compact per-person
fallback index; opaque numeric state contains no forgotten text.

Existing stores get a one-time atomic backfill at open, with 256 receipt bodies
per batch and a durable completion marker. This bounds memory, not total upgrade
work: first open is proportional to existing receipts; subsequent forgets never
scan receipt history. New stores use the indexed path immediately.

The history-cost regression retains the 4000-step fixed budget; its 1/50-dependent
case additionally allows 150 steps per payload rewrite. It includes a negative
control that deliberately scans surviving receipts. Restart/privacy, rollback,
remaining-edge and whole-member cleanup tests cover the index lifecycle.

The benchmark's `--profile` now exposes `checkpoint` separately, timing the
existing `PRAGMA wal_checkpoint(TRUNCATE)` call through its database wrapper.
It measures synchronous checkpoint call latency, including SQLite/I/O work,
without changing checkpoint policy. No checkpoint timing has been measured here.
Authorised display and diagnostics have different boundaries: returned to an authorised reader through the kernel's API = may contain that reader's content; printed, logged or written by a tool = codes, ids and counts only. The explainer retains visibility and grant checks and returns event payloads and human-readable explanations; twin display results retain routine and room labels and remain subject to display authorisation checks. Replay and rehearsal reports, harness diagnostics and bridge logs omit content. Any CLI printing twin or explainer display results must reduce them to codes, ids and counts unless an explicit `--show-content` flag is supplied (default off).

## 13. Reminders and alarms in the kernel: what production can do, and what still does not map

The kernel's reminder model (`labs/zoe-os/zoe_os/reminder_rows.py`, served by the authority daemon `authority.py`) was
written from production's code (`services/zoe-data/reminder_service.py`, `routers/reminders.py`,
`proactive/triggers/reminder_scan.py`, `reminder_recurrence.py`), read and never imported. **[code]**

**The row.** `owner` (who it is for), `by` (who set it), `note`, ONE schedule form, `visibility` (personal | family | private),
`active`, `done`, `snoozed_until`, `after` (nothing at or before it fires). The forms are production's, with its meaning:
an instant (`in_minutes`, today's argument, still 1..10080); a day and a time, any horizon; a day only, which fires at
the household default time (a daemon setting, `--default-time`, production `ZOE_REMINDER_DEFAULT_TIME`), or two minutes
after the write when set for today after that time had passed (production's same-day fallback); a time only, which is
DAILY until done (production `build_run_at` with no date); a repeat (production's RRULE subset exactly: DAILY, WEEKLY
with days, MONTHLY by day of month or one ordinal weekday, YEARLY, INTERVAL 1..52), anchored at its first occurrence.
When it fires is one pure function, `schedule.reminder_next_fire`; the recurrence maths is production's mirrored, and
`tests/test_schedule.py` runs the mirror and production's own module on one grid (three zones, both daylight changes,
month ends, the leap day) with a negative control that shows a wrong rule disagrees. Wall times are read in the
household zone (a daemon setting, `--tz`, production `ZOE_TIMEZONE`): a time in a spring-forward gap is shifted, never
skipped; an ambiguous fall-back time fires once, at its first instance.

**Actions**, each read back and each with a real undo: `remind.set`, `remind.snooze` (production default 15 minutes),
`remind.done` (a repeat is done for this occurrence only, anything else for good), `remind.cancel`, `remind.edit`
(note, day, time, on/off, visibility; a new day re-anchors a repeat, a re-sent anchor keeps its phase). Refusals are
decided before anything is written: a past day, a repeat outside the subset, a repeat with no upcoming time.

**Who may**, production's rules (`routers/reminders.py`): a person sees their own and every family reminder; any member
may edit or cancel a family one; only the owner snoozes or marks it done, and only the owner changes who sees it (a
member's visibility change is dropped, as production drops it). A CARER (owner rule 2026-11-16: a guardian named on the
roster of a child OR of an adult in care) may also see, set, edit, snooze, mark done and cancel the person's reminders,
except a `private` one. That reach is for managing rows only: organising never includes forget, undo or explain;
forgetting on someone's behalf is a separate named power, today offered only to a child's guardian (`capability.guards`), to
be replaced by memory care. Who SET a reminder is never standing authority: a carer reaches it only through the CURRENT care link, checked at every use, so a removed carer loses reading, editing, snoozing, done, cancel, and undoing or explaining their own past action over it at once, and regains them with the link (`rows.DurableRows.may_reach`). The `reminders` read op is person-bound: it answers only for the person a speaker assertion proves.

**Durable.** Rows are rebuilt from the kernel's own verified receipts (`rows.DurableRows`, the list store's rule): each
receipt's proof carries its row, and a row exists only while the receipt that created it is live. Every later action over a
row names the request that created it (`source` in its snapshot): forgetting the reminder takes its edits, snoozes, done
marks and cancel (and their requests); every action over a row copies its whole state, so forgetting any one of them (an
edit's new words) also takes every later action over the row; a cancel is a REVERSAL, so forgetting it takes the reminder
too, and no rebuild can bring it back (`Spine._row_source`). A `private` mark shuts out the carer who set it, as well. Kill tests at each
stage of a write are in `tests/test_authority.py`; in-process restart, undo and forget tests in
`tests/test_reminder_rows.py`.

**Where the kernel says MORE than production** (production cannot send these yet, and the kernel does not pretend it can):
an END to a repeat (`until`; production's parser refuses UNTIL); a reminder FOR someone else (`for`): only a guardian, for
their child, and only when the asking event names the child, so the child's forget reaches it (production has one
`user_id` and no "set by"); a day and time already past is REFUSED (production stores it and it never fires).

**What does not map, precisely.**
- Spoken forms ("10am", "tomorrow", "every weekday"): production resolves them before it stores; the kernel takes the
  stored forms (`YYYY-MM-DD`, `HH:MM` 24-hour, the RRULE). A stored "10:25 PM" must be converted by the caller.
- `category`, `priority`, `reminder_type`, the notification rows and the broadcast: not modelled (they decide nothing).
- Quiet hours, delivery, the claim and generation guards of the fire path: the kernel computes WHEN; delivering stays
  production's.
- A snooze length: production takes any integer; the kernel takes 1..1440.
- A time-only reminder in a fall-back hour: production compares wall clocks, the kernel instants (an hour's difference,
  once a year, at most).
- An entity or topic forget by the OWNER does not reach another member's receipt of an edit of a shared reminder whose
  new words no longer hold the entity (the row still goes: its create is gone). A whole-member erase does reach it.
- An undo is refused once the row has changed since the action it reverses (another member's edit or cancel, the owner
  marking it private): it never silently overwrites a later change (`rows.DurableRows.undo_check`).

**What the shadow hook can now send instead of `not_expressible`** (`services/zoe-data/os_shadow.py` `kernel_args`, on
the shadow-widen branch): `recurring` (send the stored RRULE as `repeat` with `date` and `time`), `no_time` (send `date`
alone: the default time is the kernel's setting), and `range` (send `date` and `time`: no 7-day limit). It must convert a
12-hour stored time to `HH:MM` first (`reminder_scan._parse_due_time`). The authority daemon must be started with the
household's `--tz` and `--default-time` for the comparison to mean anything.

### 13.1 Alarms

The alarm model (`labs/zoe-os/zoe_os/alarm_rows.py`, the same durable rows) was written from
`services/zoe-data/alarms.py`, `routers/alarms.py` and `alarm_voice.py`. **[code]**

**The row.** `owner`, `label` (60 characters), `time` (HH:MM), `repeat` (production's: once | daily | weekdays |
weekends | `days:1,3,5`, Monday 1), `date` (a one-off's day; none given = the next such time, today if still ahead),
`snooze_minutes` (1..30, default 9), `enabled`, and the ring bookkeeping. Several per person (30 at most, as production).
Whether one is ringing NOW is a pure function, `schedule.alarm_state`, where production's ring engine writes a `state`
column: an occurrence rings for 10 minutes and then stops by itself (one found later is skipped, production's
`MISSED_GRACE_S`); a snooze rings again when it ends; a one-off that has rung is off.

**Actions**, each read back and undoable: `alarm.set` (today's `{time}` still works, but now ADDS an alarm, as production
does, where the lab driver kept one per person and replaced it), `alarm.edit` (a changed time re-picks a one-off's day; a
spent one-off turned back on rings at the next such time; any edit stops a ring), `alarm.toggle`, `alarm.delete`,
`alarm.snooze` (only while ringing, 8 per occurrence, production's numbers), `alarm.dismiss` (this occurrence; an idle
alarm is left alone).

**Who may.** A member's own alarms (production's `can_see` and `can_edit` for a personal alarm), and a CARER's reach (a
guardian on the roster of a child or of an adult in care) over the person's alarms, except one its owner made `private`.
Forget, undo and explain stay child-only. The `alarms` read is person-bound (a speaker assertion). The forget rule is the
reminders' (`Spine._row_source`): forgetting an alarm takes every later record of it; a delete is a reversal, so forgetting
it takes the alarm too; an undo is refused once the alarm changed since.

**What does not map, precisely.**
- Alarms of a DEVICE or SHARED principal (production's owner `__household__`, set at the wall panel by whoever is there) and the panel's right to SILENCE
  any ringing alarm (`can_silence`): the kernel acts only for a person, and a guest at the panel is nobody. They need a
  device or shared principal in the consent model; until then they stay production's.
- One-day exceptions (`alarm_overrides`: "an hour earlier next Friday", or skip a day): not modelled.
- Sound (tone or music), the volume ramp and the panel's masked view: not modelled (they decide nothing the kernel owns).
- Timers are a separate, in-memory store in production (`skybridge_service._TimerStore`, "set a timer", pause, resume,
  adjust): not modelled; a follow-up.

**What the shadow hook can now send.** `alarm_create` with its repeat rule (`cls` "repeat" today) can send `repeat` and
`label`; the proof still carries `alarm` (the time) for `compare`. Two alarms set in a row are now two rows in the kernel,
as in production.

## Combined forget transaction and store boundary (lab)

[code] Under the spine lock and `BEGIN IMMEDIATE`, forget selects the complete
causal closure, purges each registered store and independently reads back that
its selected rows are absent, scrubs snapshots and intents and tombstones source
rows, writes person-scoped shields, erases selected grant/refusal records, writes
the opaque ledger and appends every ordered notice batch, then commits. Whole-person
cleanup also removes that member's intents, cursor and legacy named bookkeeping.
The append callback interface remains `then(seq)` with `before()` preparation;
forget does its work in preparation so notices are inserted last.

All SQLite erasure, dependency-index changes, shields, consent erasure, ledger
and notices commit or roll back together. Failure reloads the in-memory legacy
shield caches. External stores do not share that commit: their purge runs while
consent is still live, and a failed callback/read-back aborts log erasure. Earlier
external deletes can remain after rollback or a crash; callbacks must be idempotent
and retry must complete them. There is no distributed atomicity or compensation
that restores forgotten rows.

Every notice has `scope` (`person`, `entity`, `topic`, `seqs`) and a complete,
ordered sequence batch of at most 256 IDs, including an explicit empty batch.
A forget that erases an earlier notice (a scoped notice names its subject in
clear) carries that notice's listed IDs into its own batches, so a subscriber
that had not read it still receives the duty; `count` stays the rows erased.
Whole-person notices retain an opaque `subject_hash`, resolved against the known
roster only in memory. Every owner hash (notice subject, scoped shields, forget
ledger, nested-index markers) is a keyed hash of the EXACT person id; name
normalisation is for entity forgets only, so two ids that fold alike stay two
people. Stores written with the former normalised owner hash do not match after
this change (a lab clean break, no migration); scoped notices also identify the subject. This preserves
whole-person erasure of names without truncating the reviewed notice protocol.
Explicit erased request IDs match every owner's stores and caches. Whole-person
purge additionally sweeps orphaned owner rows; the twin clears reduced owner facts
through the notice watermark even when source retention has removed their events.
Every subscriber cache reads a notice through ONE helper, `provenance.erased`
(`subject_of` is the only reader of a notice's subject; a structural test pins it).
It resolves an anonymous notice through the context: in process the resolved
member's state goes up to the watermark, including state whose source retention
already retired. A remote skill (`Client`) holds no key to resolve `subject_hash`,
so an unresolved whole-person notice means "possibly any member", never "nobody":
only state whose source event the skill can still read is kept (`still_live`, one
replay of its own reads from its oldest held source); the Keeper, which cannot
vouch for any row from its reads, refuses and retries instead. State the unresolved
rule drops for another member can no longer act: a reminder or a leaving catch must
cite its live source to publish, and the twin relearns. On a whole-person notice the
reminder skill never deletes through the forgotten member's (erased) consent; the
kernel's whole-member sweep removed their rows. Another member's state with a live source
survives; their state whose source had also left the log is dropped and relearned.

Whole-person selection also follows `nested_people`: a known member id that an
ordinary payload holds as a whole string value or a dict key, at any depth, other
than the subject and mentions. Ids inside longer free text are not identities here
(the name shield covers those). Receipt before-state copies are excluded, as in
entity matching; the snapshot scrub reaches them through their sources. Rows written
before the index, or before a member was known, are backfilled once per member at
open (marked by a keyed hash, never the id). Scoped, entity and topic forgets do not
use this index.

Registered store purges and their read-backs receive every erased seq: a request
already expired to its retention skeleton arrives as a body-less `Skeleton`
(seq and kind), read by primary key, so rows keyed by `asked` are reached.

Trusted bootstrap registers synchronous purges for brain lists/reminders/alarms
and the reminder fixture. The reminder skill retains the reviewed replay delete:
`Context.purge` can invoke only an opted-in action for IDs derived from a live
notice, with independent read-back. It conveys no general action permission and
cannot replace an erased grant. Other skill caches remain cooperative in-process
memory; notices are replayed after restart and acknowledged only after handling.
First-hand text reassertion and explicit entity release do not lift shields.

[code] Both index pins and SQL-step budgets in `test_history_cost.py` are unchanged.
The arbitrary schema-size ceiling is replaced by the boundary test's content oracle and loose bound;
its negative control plants the oversized body on disk. Snapshot controls disable scrubbing and
either retain the shield (dirty receipt refused) or disable it (resurrection demonstrated).
These changes and controls remain unrun by this builder: available memory was 662 MB, below 1200 MB.
The coordinator must verify them in a test window. No live code or configuration changes.
