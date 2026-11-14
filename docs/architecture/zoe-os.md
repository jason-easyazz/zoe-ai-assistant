---
type: architecture
title: Zoe OS - one spine every part plugs into
date: 2026-11-14
status: design record plus a lab prototype (labs/zoe-os/). Nothing here is wired to production; no flag exists yet.
prototype: labs/zoe-os/ (stdlib Python, SQLite in a temp dir, synthetic people only)
related: zoe-abilities.md, identity-presence-engine.md, communication-preferences.md, anticipation-loop.md, credential-vault.md
---

# Zoe OS: one spine every part plugs into

This record is written for a reader who has never seen the project. Zoe is a household assistant that runs on
a small computer in the home: a local language model, speech in and out, a memory, calendar, lists, reminders,
wall panels and safety rules. Every claim below carries a label: **[code]** means it was read in the repository
at the path given, **[lab]** means it was run in `labs/zoe-os/` on synthetic data, **[inferred]** means reasoned
and not checked, **[vision]** means not built and not proven.

## 0. Answer first

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

**What is not proven:** that it is fast enough inside the live voice turn, that it survives a process boundary
(the prototype is in-process, so a hostile skill in the same process could simply reach around it), that the
real modules can publish to it without regressions, and everything about presence, memory and self-care as
kernel services. Section 8 lists every place the vision outruns the proof.

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

**Missing.** A process boundary; schema versioning; retention (the lab log grows for ever); multi-process
writers; any production publisher.

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
access. A child's grant needs one of that child's guardians. A guest cannot grant. Revoking takes effect on the
next delivery.

**Prototype [lab]** (`capability.py`): the manifest format (unknown keys, kinds or levels are errors), a grant
store, and one `Enforcer` that the spine and the action dispatcher both call. A skill that asks for more than
its manifest is refused and the refusal is an event. Grant changes are events only an auditor can read.

**Exists [code].** Roles and a capability matrix (`guest_policy.py`), the authority table with coded floors
(`household_authority.py`), and a written design for ability manifests and connected accounts:
[zoe-abilities.md](zoe-abilities.md). The lab manifest is deliberately the same idea as that document's
"ability registry"; when the two meet, that document's fields win and the lab format is adjusted.

**Missing.** Consent is per role today, not per person per skill **[code]**. There is no revocation event, and
no single enforcement point: each feature checks for itself.

### 2.4 Forget, as a kernel guarantee

**Contract.** When a person asks to forget, their events are gone: from replay, from export, from every
subscriber's queue, from events that were derived from them, and from the bytes on disk. A subscriber that was
offline during the forget and replays later gets nothing. Nobody can bring the words back by re-publishing them.

**Prototype [lab]** (`spine.py` `forget`): only the person (or a child's guardian) may ask. The kernel
1. collects every event about the person, including events that only mention them;
2. follows the "derives from" links to everything built on those events, to any depth;
3. erases the payloads in place (SQLite `secure_delete`, then a write-ahead-log truncate) and marks the rows;
4. withdraws matching events from every live queue;
5. tells every subscriber, whatever its filters, with a notice that carries no content, so a well-behaved skill
   drops its own derived state;
6. keeps a keyed hash of the forgotten free text. A skill that later tries to publish the same words, even
   nested inside another payload, is refused. A trusted adapter recording the person saying the same thing
   again is new reality, is allowed, and lifts that hash.

**Exists [code].** The production forget is strong and hand-maintained: a ledger that cannot list what it
forgot (`memory_forgotten.py`), a cascade over derived stores (`memory_forget_cascade.py`), an erase gate that
wins races with background writers (`user_erase_gate.py`), and the inventory test (`forget_reach.py`).

**Missing, and the honest limits of the lab version.** A skill that already copied the words into its own
memory keeps them: the kernel can refuse to take them back and can tell the skill to forget, but cannot reach
into another program. The words reworded are not caught (the hash is of the exact normalised text). The lab
forgets by person or by named events; production forgets by entity and topic, which the spine would need.

**Migration.** The spine does not replace the 62 reached stores. It becomes store number 104 on day one
(a new row in `forget_reach.py`, reached by one call), and it shrinks the inventory only as features move
their state onto it.

### 2.5 Intent, plan, act, with receipts

**Contract.** An action runs only through the dispatcher. It needs the skill's manifest, that person's grant,
a cause, and, for any action marked consequential, the person's own accepted offer from this same skill, used
once. After the action the driver reads back what it did. The receipt records what, by which skill, under
which grant, with what proof. A claim ("I moved your alarm") must cite a verified receipt.

**Prototype [lab]** (`receipts.py`): the dispatcher, the read-back, and a detector that finds claims with no
receipt, an unverified receipt, or a receipt for a different action. A driver that reports success and did
nothing gets an unverified receipt, and the honest skill then makes no claim (tested).

**Exists [code].** This is the area closest to done in production. `write_receipts.py` and
`write_claim_guard.py` are exactly this idea for five kinds of write, enforced where every brain turn ends
(`brain_dispatch.py` `_claim_guarded`, line 915). `anticipation.py` already offers before acting.

**Missing.** A receipt today proves "a row was written", in process memory, for the claim guard's use
**[code: `write_receipts.py`]**. It does not record under whose consent, it is not durable, and device
actions and messages have no read-back. "Plan" is not prototyped at all: the lab goes from an accepted offer
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

## 4. Trust boundary: what the prototype does not defend

The prototype runs in one Python process. A skill there can call a private method or read the database file.
The tests show that the *rules* are complete and consistent when every call goes through the kernel; they do
not show isolation. In production the kernel must be its own process under its own user, skills must reach it
over a socket, and the database file must be readable by the kernel alone. The work to give each service its
own unprivileged user is already planned ([p0-deroot.md](p0-deroot.md)) and is a prerequisite, the same way it
is for the credential broker ([credential-vault.md](credential-vault.md)).

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

Tests **[lab]**: 84 tests in 7 files, all passing, run twice (local time zone and `TZ=UTC`), 1.3 s. Twelve of
them are negative controls that remove one guarantee and assert the specific bad outcome.

## 6. The bridge plan: five call sites, no rewrite

Stage 1 puts five publish calls into production code, behind one new flag (working name `ZOE_OS_SPINE`,
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
3. call site 4, with a restore-and-replay drill of the forget on a copy;
4. call sites 1 and 5 (voice path: replay-gated);
5. only then a first subscriber, in shadow: the claim auditor, which reads and reports and changes nothing.

## 7. What to build next, in order

1. **Process boundary.** Move the lab kernel behind a local socket with its own user and prove the malicious
   skill again from a separate process. Until this exists the consent model is a convention.
2. **Bridge step 1 and 2** above, flag off, and the measure: events per day and publish time at each call site.
3. **Forget by entity and topic**, matching what `memory_forgotten.py` does, so one forget covers both.
4. **Receipts made durable**: `write_receipts.record` publishes a receipt event; the claim guard reads it.
   This retires the in-memory receipt list rather than adding a second one.
5. **Attention as the one gate** for offers from `anticipation.py`, with the per-person setting.
6. **Presence engine** per its own design record, publishing to the spine; then the leaving catch for real.
7. **Skills as packages** with manifests, starting with one that exists (anticipation) rather than a new one.

`docs/ROADMAP.md` owns the order of all work. This list is the order *within* this design if it is taken up.

## 8. Where the vision outruns the proof

- **Isolation** is not proven at all (section 4).
- **Speed on the live path** is not measured. The lab numbers are for a quiet single process and say only that
  the design is not obviously too slow.
- **The real modules** have not published a single event. The call sites were read, not run.
- **Presence, memory and self-care** as kernel services are a map, not a build.
- **Forget** cannot reach a copy a skill already took, and does not catch reworded content.
- **Plans** (more than one action from one intent) are not designed here.
- **Retention, schema change and backup** of the spine are not designed. A log of every household event is
  itself sensitive, and "private by default" does not answer how long it is kept.
- **The manifest format** is a lab format. It has not met a real third-party skill.
- **One writer.** SQLite with one in-process lock. Several services publishing at once is untested.
- **"OS"** is a direction. What exists is a kernel of about a thousand lines in a lab directory.
