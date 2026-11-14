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

**Entity and topic [lab]** (`forget_match.py`, `Spine.forget(entity=... | topic=...)`). The shapes were read in
`memory_forgotten.py` and `forget_topic.py` **[code]** and rebuilt, not imported. An entity is a name of one to
six whole words, case and punctuation blind; the person's events that name it are erased with everything
derived, and a keyed hash of the name is kept per person (never the name), so a skill that writes the name about
that person again is refused, in new words, nested, or as a dictionary key. A first-hand mention is recorded and
lifts nothing; only the person's explicit release does. A topic is its content words, and an event is about it
only when it holds every one; more than twelve matching events is refused whole. A scope with no words is
refused and never widens to "everything".

**Missing, and the honest limits of the lab version.** A skill that already copied the words into its own
memory keeps them: the kernel can refuse to take them back and can tell the skill to forget, but cannot reach
into another program. **Reworded content is not caught**: a paraphrase that avoids the name and the exact old
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
which grant, with what proof. A claim ("I moved your alarm") must cite a verified receipt.

**Prototype [lab]** (`receipts.py`): the dispatcher, the read-back, and a detector that finds claims with no
receipt, an unverified receipt, or a receipt for a different action. A driver that reports success and did
nothing gets an unverified receipt, and the honest skill then makes no claim (tested).

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
request. `forget`, `grant` and `revoke` are a person's decisions and are accepted only from a credential issued
to a trusted surface.

**What was attacked [lab]**, each refused and recorded, each with a control that removes one mechanism and
shows the attack working:

| Attack from another process | Control that makes it succeed |
|---|---|
| the ten attempts of the malicious skill (read, act, resurrect) | the same four as in-process |
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
2. **A flag.** `ZOE_OS_SPINE`, default `off`, registered in the flag registry in the same change
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
- **Retention** has a lab mechanism and **no chosen policy**: section 9 is an open owner decision, and nothing
  expires until it is made. **Schema change and backup** of the spine are not designed; a backup taken before
  an event expired or was forgotten still holds it.
- **The manifest format** is a lab format. It has not met a real third-party skill.
- **One writer.** One kernel process with one lock. Several clients were tested for correctness (a slow one
  does not block an honest one), not for throughput.
- **"OS"** is a direction. What exists is a kernel of about two thousand lines in a lab directory.

## 9. Owner decision: how long the log is kept

**Status: open. Nothing in the lab expires until a policy is chosen.** A log of every household event is itself
sensitive, and "private by default" says who may read an event, not how long it exists. The mechanism is built
and tested in the lab (`labs/zoe-os/zoe_os/retention.py`) **[lab]**; the numbers below are proposals. The code
has no default: it refuses to run without a complete policy, and no option is applied unless someone asks.

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

**Status: open.** The lab uses 56 days as a placeholder, and the code says so. The twin's reduced facts are a
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

In every option: **generated days are never stored** (a seed regenerates them); a rehearsal report is text that
quotes offers, so a report made from a real recording is a copy outside the kernel and should not outlive that
recording; and a recording of real days written to a file is itself a copy no forget can reach. The lab's
`record` writes such a file because its days are synthetic. For real days the replay should read the log
directly. That is not built.

**Recommendation: B**, and state the twin's window as its own line in the retention policy rather than leaving
it inside a skill. It is the shortest window that gives both baselines. If the owner prefers the simple rule
that nothing outlives the log, choose A and lengthen presence in section 9 to 28 days instead, which moves the
same cost into the log where a forget by name still works. Both are **[inferred]** from the lab's arithmetic
(ten working days, six weekend days), which is itself a judgement and not a measurement.
