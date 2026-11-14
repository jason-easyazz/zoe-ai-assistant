# labs/zoe-os: the Zoe OS kernel prototype

A lab-only prototype of the kernel described in
[`docs/architecture/zoe-os.md`](../../docs/architecture/zoe-os.md): one event spine every part plugs into,
with consent, forgetting and receipts as properties of the spine rather than of each feature.

**Nothing here is wired to anything.** No production module imports it, there is no flag, no service, no
unit. Standard library only (SQLite in a temp dir). Every person and every word in the tests is invented.

## What is in it

| File | What it is |
|---|---|
| `zoe_os/events.py` | the event record: a closed set of kinds, a subject, a visibility (private by default), a source |
| `zoe_os/spine.py` | the bus: durable append, capability-filtered subscriptions, bounded queues, replay, forget |
| `zoe_os/capability.py` | skill manifests, per-person grants (a child needs a guardian), the one enforcer |
| `zoe_os/receipts.py` | the action dispatcher, read-back receipts, the claim-without-receipt detector |
| `zoe_os/attention.py` | per-person routing of offers: card by default, spoken only if that person turned it on |
| `zoe_os/kernel.py` | the services wired together and the loop that feeds events to skills |
| `zoe_os/skills/` | three toy skills: leaving catch, calendar heads-up, and a deliberately malicious one |
| `zoe_os/bridge.py` | thin adapters for the five production call sites of the bridge plan (not imported by production) |
| `bench.py` | one capped benchmark (60 s, 100k events) |

## Run it

The box has one shared test lane; follow the lane rules in force (memory floor, one process, the lock).

    python3 -m pytest -p no:cacheprovider -q labs/zoe-os/tests
    python3 labs/zoe-os/bench.py

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

Controls are monkeypatches in the tests, never switches in the kernel.

## Limits (the same list as the record's section 8, short)

In-process only, so no isolation: a skill in the same process can reach around the kernel. One writer.
No retention policy. Forget cannot reach a copy a skill already took and does not catch reworded content.
Presence, memory and self-care are not prototyped. README and results are records, not contracts.
