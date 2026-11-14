# labs/zoe-os: the Zoe OS kernel prototype

A lab-only prototype of the kernel described in
[`docs/architecture/zoe-os.md`](../../docs/architecture/zoe-os.md): one event spine every part plugs into,
with consent, forgetting and receipts as properties of the spine rather than of each feature.

**Nothing here is wired to anything.** No production module imports it, there is no flag, no service, no
unit. Standard library only (SQLite in a temp dir, a Unix socket). Every person and every word in the tests
is invented.

## What is in it

| File | What it is |
|---|---|
| `zoe_os/events.py` | the event record: a closed set of kinds, a subject, a visibility (private by default), a source |
| `zoe_os/spine.py` | the bus: durable append, capability-filtered subscriptions, bounded queues, replay, forget (by person, named events, entity or topic) |
| `zoe_os/forget_match.py` | pure matching for an entity (whole words, in order) and a topic (every content word) |
| `zoe_os/capability.py` | skill manifests, per-person grants (a child needs a guardian), the one enforcer |
| `zoe_os/receipts.py` | the action dispatcher, read-back receipts, the write-ahead intent and crash recovery, the claim-without-receipt detector |
| `zoe_os/attention.py` | per-person routing of offers: card by default, spoken only if that person turned it on |
| `zoe_os/kernel.py` | the services wired together and the loop that feeds events to skills |
| `zoe_os/wire.py` | the socket protocol: frames, version 1, the closed table of eight ops and their arguments |
| `zoe_os/server.py` | the kernel behind a Unix socket: handshake, per-connection identity, size cap, frame deadline |
| `zoe_os/client.py` | the skill's side of the socket, and `RemoteKernel` so an in-process skill runs unchanged |
| `zoe_os/skills/` | three toy skills: leaving catch, calendar heads-up, and a deliberately malicious one |
| `zoe_os/bridge.py` | thin adapters for the five production call sites of the bridge plan (not imported by production) |
| `zoe_os/explain.py` | "why did you do that?": the chain behind an action, refusal or offer, and one sentence built by code |
| `zoe_os/replay.py` | the replay harness: a recorded day in, a deterministic report out, a diff between two skill versions |
| `zoe_os/retention.py` | full, then a skeleton, then a count. The owner chose the "balanced" policy (2026-11-14); nothing sweeps by itself |
| `zoe_os/skills/reminders.py` | one real feature as a skill (create, fire, snooze, forget) against a fake store |
| `zoe_os/twin/days.py` | the household twin's day generator: synthetic days for a seed, awkward days on purpose |
| `zoe_os/twin/baseline.py` | the twin skill: what is normal per person, as counts; "not enough days yet" below ten working days |
| `zoe_os/twin/rehearse.py` | a changed skill against generated and recorded days: privacy failures first, then every difference |
| `zoe_os/twin/unusual.py` | "this differs from the last N days in this way": never a reason |
| `bench.py` | one capped benchmark (60 s, 100k events) |
| `bench_socket.py` | per-request latency of the socket hop (one kernel process, one client) |
| `tests/kernel_proc.py`, `tests/skill_proc.py`, `tests/procs.py` | the kernel and a skill as separate processes, for the boundary and kill tests |

## Run it

The box has one shared test lane; follow the lane rules in force (memory floor, one process, the lock).

    python3 -m pytest -p no:cacheprovider -q labs/zoe-os/tests
    python3 labs/zoe-os/bench.py
    python3 labs/zoe-os/bench_socket.py 2000

The suite starts short-lived kernel and skill processes (about 12 s in all). Two tests need root to start a
skill as a second OS user and skip otherwise; that skip is the honest result for a one-user run.

`pytest.ini` `testpaths` and the CI lanes never collect `labs/`, so this suite is hand-run only.

## What the tests prove, and the control for each

A guarantee without a failing control proves nothing, so each one has a test that removes it and shows the
bad outcome.

| Guarantee | Test | Control: remove this, and the bad thing happens |
|---|---|---|
| a reader gets only what it was granted | `test_spine.py`, `test_capability.py` | `Spine._deliverable` / `Enforcer.may_read` always yes: another person's private words are read |
| a slow subscriber never blocks a publisher and loses nothing | `test_spine.py` | (property test: 50 events into a queue of 4, all 50 arrive in order) |
| a forgotten event never replays, even to a subscriber that was offline | `test_forget.py` | `Spine._tombstone` a no-op: the sentinel replays |
| a forgotten event is withdrawn from a live queue | `test_forget.py` | `Subscription._purge_forgotten` a no-op: the queued sentinel is delivered |
| the forget reaches everything derived | `test_forget.py` | `Spine._closure` returns only the seed: a derived copy survives |
| forgotten words cannot be re-published by a skill | `test_forget.py`, `test_attack.py` | `Spine._shielded` always no: the words are back on the spine |
| the bytes are gone from disk | `test_forget.py`, `test_attack.py` | positive control: the sentinel is in the files before the forget |
| no action without the manifest and that person's grant | `test_receipts.py`, `test_attack.py` | `Dispatcher._authorize` always yes: the light is toggled for someone who never agreed |
| a consequential action needs the person's own unused yes | `test_receipts.py`, `test_skills.py` | (declined, missing, reused and someone else's yes are each refused) |
| no claim without a verified receipt | `test_receipts.py` | `ClaimAuditor.backed` always yes: an unbacked claim is not found |

### Across the process boundary

| Guarantee | Test | Control: remove this, and the bad thing happens |
|---|---|---|
| the malicious skill, from another process: ten attempts, ten recorded refusals | `test_boundary.py` | the same four controls, applied inside the kernel process |
| a skill cannot be another skill, name its own source, or speak for a person | `test_boundary_attacks.py` | no credential check: it becomes the panel and reads everything |
| a captured handshake or a re-issued credential does not log in | `test_boundary_attacks.py` | a predictable challenge: the capture logs in |
| an oversized frame is refused before its body is read | `test_boundary_attacks.py` | no size cap: it is stored |
| a malformed frame is refused, recorded, and harms nothing (23 cases) | `test_boundary_attacks.py` | no validator: an unknown argument is accepted, nothing recorded |
| slow clients cannot hold every connection | `test_boundary_attacks.py` | no frame deadline: an honest client is starved |
| a dead client leaves nothing behind; a whole request runs once | `test_boundary_attacks.py` | no release: its subscriptions pile up |
| a second OS user cannot open the database file (root only, else skipped) | `test_boundary_attacks.py` | loose file modes: it reads the file |
| forget by entity and by topic keeps every forget guarantee | `test_forget_scope.py` | the cascade, the tombstone, the queue purge and the shields, each removed in turn |
| a forgotten name stays forgotten in new words | `test_forget_scope.py` | `Spine._names_forgotten` always no |
| a receipt and a used yes survive a killed kernel | `test_durable.py` | the used yes held in memory: one yes buys two actions |
| an unbacked claim is found after a restart, once | `test_durable.py` | a trusting auditor finds nothing |
| a kill between the write and the receipt is reported `uncertain`, never done, never retried | `test_durable.py` | no write-ahead intent: no trace; a recovery that says "done": a false claim passes |

### Explain, undo, replay, reminders, retention

| Guarantee | Test | Control: remove this, and the bad thing happens |
|---|---|---|
| an explanation never shows another person's private event, not even its number | `test_explain.py` | no visibility check: the private words are in the answer |
| undo is for the person (or a child's guardian), once, and never faked | `test_undo.py` | no ownership check; no once rule; "the last reversible action" instead of "the last action" |
| the same recording gives the same report, byte for byte | `test_replay.py` | (checked across three processes with different hash seeds and time zone) |
| one person's yes cannot move another person's reminder | `test_reminders.py` | without the kernel's yes rule the rule-free store moves it |
| a forget still reaches what derived from an event that aged out | `test_retention.py` | expiry that drops the links, or retires a skeleton early: the copy survives |

### The household twin

| Guarantee | Test | Control: remove this, and the bad thing happens |
|---|---|---|
| the same seed gives the same days, byte for byte | `test_twin_days.py` | (checked across processes with different hash seeds) |
| no baseline for someone who has not enabled the twin; none for a guest; none before ten working days | `test_twin_baseline.py` | (the kernel never delivers their events: its delivery controls are above) |
| a change that tells one person about another is reported first, though the kernel allowed it | `test_twin_rehearse.py` | no privacy pass: the change reads as "two more offers" |
| silent on an ordinary day; a line never contains a reason | `test_twin_unusual.py` | (five seeds, a whole ordinary month, a banned-word list over every line) |
| forgetting a person removes their baseline | `test_twin_forget.py` | a twin that ignores the notice still describes what was forgotten |
| a line about a person is shown to that person, and to a child's guardians, and to nobody else | `test_decisions.py` | without the rule one adult is shown another adult's day |

Three owner decisions (2026-11-14) are pinned by `test_decisions.py`: the retention policy, the twin's 28 days,
and who is shown a line. Change the record first, then the code.

**The twin's days are generated by its author.** These tests show the code does what it says, on synthetic days,
and nothing about a real household (record, section 12).

In-process controls are monkeypatches in the tests. Controls for the kernel process are named patches in
`tests/kernel_proc.py`, applied through the parent's pipe. Never switches in the kernel.

## Limits (the same list as the record's section 8, short)

**The socket is not the whole boundary.** A skill running as the same OS user as the kernel finds the database
through `/proc` and reads it; `test_HONEST_LIMIT_a_skill_running_as_the_same_user_...` asserts that it can.
File permissions and a second OS user stop it, shown only in a root sandbox with a throwaway user.

Forget cannot reach a copy a skill already took, and does not catch reworded content, near spellings, part of
a name or a pronoun (`test_HONEST_LIMIT_reworded_content_...` asserts three of these get through).

"Durable" means against a killed process, not power loss. An `uncertain` receipt has no resolution step.
Grants, manifests and credentials are in memory: a restart denies everything until granted again.

A skill's own store is outside the kernel: a skill that ignores a forget notice keeps the words
(`test_HONEST_LIMIT_a_skill_that_ignores_the_notice_...`). A forget by entity or topic cannot match an event
whose content has aged out (`test_HONEST_LIMIT_a_scoped_forget_...`). Retention has a chosen policy (record,
section 9) and no scheduler: nothing expires until something calls the sweep.

When two people enable the same skill, the kernel lets it derive something for one from the other's private
event; the rehearsal reports that, the kernel does not stop it. A forget by name does not reach the twin's
reduced copy once the kernel's events have aged out (`test_HONEST_LIMIT_a_forget_by_name_after_...`). The suite
has shown one unexplained failure in about fifty full runs.

One kernel process, one lock. No rate limit on failed handshakes. Presence, memory and
self-care are not prototyped. The socket latency in the record was read in a cloud sandbox, not on the target
hardware. README and results are records, not contracts.
