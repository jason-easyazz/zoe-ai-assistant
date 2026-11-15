# labs/zoe-os: the Zoe OS kernel prototype

PR #2630 review regressions: `tests/test_privacy_review.py` covers text-free diagnostics, token-protected
HTTP handlers without sockets, cross-owner request erasure, and suspended replay snapshot refresh.
Diagnostic outputs retain IDs, closed codes and counts, never utterances, arguments, say strings or reasons.
Request-derived stores must purge erased sequences across all owners; `purge_key` opts a driver into
notice-bound deletion even after consent is revoked. Validation of this correction is pending: the builder
observed less than 800 MiB available memory and could not run Python under the builder brief's memory gate.

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
| `zoe_os/wire.py` | the socket protocol: frames, version 1, the closed table of nine ops and their arguments |
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
| `zoe_os/brain/bridge.py` | the brain's adapter: tool list derived from the kernel's `describe`, one tool call = one `act`, one sentence back |
| `zoe_os/brain/drivers.py` | kernel-side drivers for the brain's three actions (lists, reminders, alarms) and the keeper that erases them on forget |
| `zoe_os/brain/drive.py` | the scripted agent and the recorded-stream replay (no model) |
| `bench_brain.py` | latency of the kernel hop per brain action |
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
| per-person shields allow unrelated private reuse by B; forgetting grant/refusal records removes consent | `test_forget_owner.py` (verification pending) | old global text shield refuses B; omitting payload-reference indexing leaves grant/refusal rows naming A |
| the bytes are gone from disk | `test_forget.py`, `test_attack.py` | positive control: the sentinel is in the files before the forget |
| no action without the manifest and that person's grant | `test_receipts.py`, `test_attack.py` | `Dispatcher._authorize` always yes: the light is toggled for someone who never agreed |
| a consequential action needs the person's own unused yes | `test_receipts.py`, `test_skills.py` | (declined, missing, reused and someone else's yes are each refused) |
| no claim without a verified receipt | `test_upper_review.py`, `test_receipts.py` | disabling `Spine._claim_refusal` allows an unbacked claim |

### Across the process boundary

| Guarantee | Test | Control: remove this, and the bad thing happens |
|---|---|---|
| the malicious skill, from another process: six remaining attempts refused; forgotten cache entries unavailable | `test_boundary.py` | the same four controls, applied inside the kernel process |
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
| an unbacked claim is refused and remains absent after restart | `test_durable.py` | a trusting receipt check admits a false claim |
| a kill between the write and the receipt is reported `uncertain`, never done, never retried | `test_durable.py` | no write-ahead intent: no trace; a recovery that says "done": a false claim passes |
| review fixes: derived-only text and tuple-wrapped words or names stay forgotten; a child's grant opens only the child's events; one receipt backs one claim; a forget scrubs the intent rows and a yes stays spent; "the last action" is the newest | `test_review_findings.py` | each test failed on the code before its fix (the old code is the control) |

### Explain, undo, replay, reminders, retention

| Guarantee | Test | Control: remove this, and the bad thing happens |
|---|---|---|
| an explanation never shows another person's private event, not even its number | `test_explain.py` | no visibility check: the private words are in the answer |
| undo is for the person (or a child's guardian), once, and never faked | `test_undo.py` | no ownership check; no once rule; "the last reversible action" instead of "the last action" |
| the same recording gives the same report, byte for byte | `test_replay.py` | (checked across three processes with different hash seeds and time zone) |
| one person's yes cannot move another person's reminder | `test_reminders.py` | without the kernel's yes rule the rule-free store moves it |
| a forget still reaches what derived from an event that aged out | `test_retention.py` | expiry that drops the links, or retires a skeleton early: the copy survives |

### The brain bridge ([record](../../docs/architecture/zoe-os-brain-bridge.md))

| Guarantee | Test | Control: remove this, and the bad thing happens |
|---|---|---|
| an action's declared arguments are checked before its driver runs | `test_describe_params.py` | `check_params` always yes: the driver runs on malformed arguments |
| an action for a person must cite an event about that person | `test_cause_binding.py` | `Dispatcher._concerns` always yes: a deputy acts for one person on another's word |
| the brain's tools are derived from the kernel; the turn's own values are hidden from the model | `test_brain_bridge.py` | a bridge that shows every parameter, or accepts `asked` from the model |
| forgetting a person removes the effect in the stores, not only the events | `test_brain_bridge.py` | a keeper that ignores the notice |
| kernel down or lost mid-call is said honestly and writes nothing half-way | `test_brain_bridge.py` | (a kill between write and receipt is `uncertain`, never done) |

### The household twin

| Guarantee | Test | Control: remove this, and the bad thing happens |
|---|---|---|
| the same seed gives the same days, byte for byte | `test_twin_days.py` | (checked across processes with different hash seeds) |
| no baseline for someone who has not enabled the twin; none for a guest; none before ten working days | `test_twin_baseline.py` | (the kernel never delivers their events: its delivery controls are above) |
| a change that tells one person about another is reported first, though the kernel allowed it | `test_twin_rehearse.py` | no privacy pass: the change reads as "two more offers" |
| silent on an ordinary day; a line never contains a reason | `test_twin_unusual.py` | (five seeds, a whole ordinary month, a banned-word list over every line) |
| forgetting a person removes their baseline | `test_twin_forget.py` | a twin that ignores the notice still describes what was forgotten |
| a line about a person is shown to that person, and to a child's guardians, and to nobody else | `test_decisions.py` | without the rule one adult is shown another adult's day |

| a person's lines are shared only if that person chose it, one way, revocably | `test_sharing.py` | without the ownership rule an outsider shares someone's lines with themself |

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

The supported subscriber cache holds only sequence numbers and resolves bodies from the live authorized log:
forget removes source and derived text from it immediately, including the malicious helper's cache. A private
body copy outside that contract cannot be erased by Python. Forget does not catch reworded content, near spellings, part of
a name or a pronoun (`test_HONEST_LIMIT_reworded_content_...` asserts three of these get through).

"Durable" means against a killed process, not power loss. An `uncertain` receipt has no resolution step.
Grants, manifests and credentials are in memory: a restart denies everything until granted again.

A skill's arbitrary private memory is outside the kernel. A registered store is purged directly by
the kernel even if its skill ignores the notice (`test_kernel_purges_store_even_when_skill_ignores_the_notice`).
A forget by entity or topic cannot match an event
whose content has aged out (`test_HONEST_LIMIT_a_scoped_forget_...`). Retention has a chosen policy (record,
section 9) and no scheduler: nothing expires until something calls the sweep.

Whole-member forget erases consent records too. The kernel purges the reminder store and verifies it
before erasing consent; the skill then drops its cache without acting through the former grant.
The kernel-side brain keeper erases its stores directly without consulting a skill grant. Per-person shield
and legacy-store rules, including anonymous whole-member notices, are in the architecture record §2.3–2.4.
Trusted origins can no longer reassert forgotten text for the forgetting member.

When two people enable the same skill, the kernel lets it derive something for one from the other's private
event; the rehearsal reports that, the kernel does not stop it. A forget by name does not reach the twin's
reduced copy once the kernel's events have aged out (`test_HONEST_LIMIT_a_forget_by_name_after_...`). The suite
has shown one unexplained failure in about fifty full runs.

The guarded runner confines `--lock` and `--out` to `labs/zoe-os/.state/` (the default) or
`~/.zoe/agent-tools/zoe-os/`, resolving symlinks and refusing traversal. Real runs additionally require the
pre-existing shared voice harness lock, opened read-only. Only explicit-port HTTP loopback llama origins are
accepted; Python disables proxies and redirects and the Node fetch boundary refuses redirects. Quiet guards
retain only log prefixes and process identities, never log bodies or whole command lines.

Claims are checked at public publish time, including batches: one matching verified receipt backs one live
claim. `ClaimAuditor.scan()` is diagnostic only. Socket-free review regressions are in `tests/test_upper_review.py`;
the Node boundary's synthetic stub-fetch test is `node --test labs/zoe-os/brain-lab-app/src/local-network.test.mjs`.

One kernel process, one lock. No rate limit on failed handshakes. Presence, memory and
self-care are not prototyped. The socket latency in the record was read in a cloud sandbox, not on the target
hardware. README and results are records, not contracts.

## Offline load evidence [code]

`bench_kernel.py` owns latency (all nine verbs), 1/4/16-client concurrency, a seeded
20,000-operation soak (290 s cap), 50 SIGKILL/restart trials, and cold start to the
first authenticated answer. `bench_support.py` starts one scratch kernel child;
clients use threads, never additional worker processes. It uses the SAME `Client`,
`KernelServer`, wire, consent, dispatcher and record as the brain bridge. Effects
are **durable synthetic SQLite drivers**, not the brain's in-memory example stores.
No model is loaded or contacted. No service or environment setting is changed.
Scratch files stay inside this directory and are removed on exit.

When the host's memory/probe rules allow, on a host permitting Unix socket bind:

```sh
python3 -B labs/zoe-os/bench_kernel.py --mode all --seed 7 --n 100 --soak-n 20000 --crashes 50 --out labs/zoe-os/bench-results.json
python3 -B labs/zoe-os/bench_kernel.py --mode latency --seed 7 --n 100 --history-n 20000 --out labs/zoe-os/bench-history.json
python3 -B labs/zoe-os/bench_kernel.py --mode latency --seed 7 --n 100 --profile --history-n 20000 --out labs/zoe-os/bench-profile.json
python3 -B -m pytest -p no:cacheprovider -q labs/zoe-os/test_bench_kernel.py
```

Numbers and fixed statuses only leave the harness: never credentials or payloads.
A failure writes its exception CLASS, exits nonzero, and does not masquerade as a
completed measurement. CLI modes run independently so all failures are recorded.
Keep baseline/after JSON from identical parameters and a comparable host load.
Do not compare profiled timings to unprofiled timings. `--profile` writes a top-level
`profile` object (also under `latency`), with one extra call per verb and `phases_us`
for decode, consent, SQL, commit and receipt. Fixture setup precedes the counter reset.
`--history-n` preloads at least that many action-history events through real calls,
excluded from timing, so links and completed intents also grow. Counters are inclusive,
not additive: `receipt_inclusive` contains append/SQL timings; JSON decoding includes
wire parsing and DB payload decoding. SQL commit/autocommit include SQLite sync work;
**fsync is not separately timed**. Cursor stepping is included in SQL time;
supervisor commands are excluded from JSON decode counters. This is instrumentation,
not an fsync attribution. Zero is an unused phase; receipt overlaps SQL and commit.

Crash trials shuffle all six semantic crash points once per block, deterministically
for the seed. The supervisor waits for a hook INSIDE the selected phase before
SIGKILL; it does not hope a timer hits a write. It checks SQLite integrity, closed
intent/receipt correspondence, payload completeness, acknowledged receipts AND actual
effects, persistent survival sentinels, and undo after every restart. A volatile effect
store must fail that same oracle. A driver that writes to the wrong member must fail
the concurrency oracle even if its own read-back is unverified. The seeded workload
is repeatable; thread schedules and timings are not. Process kill is not power loss.

Forget timings cover a scoped isolated event, not deletion of a large member history
or asynchronous erasure from external stores. Setup calls are excluded from per-verb
latency; total concurrency wall time includes both successful writes and adversarial
cross-member calls. Tail latency describes successful writes. Soak counts mixed
operations separately from wire calls including fixture setup. RSS is the child process,
not the controller, and disk growth includes the SQLite database, WAL and SHM files.
Cold start includes Python imports, schema, synthetic grants, authentication and describe.

[lab, coordinator] Seed-7 baseline numbers and the failed 20,000-call soak are recorded
in the architecture document's “Load, soak and crash results”. New indexed lookups are
unmeasured until the coordinator reruns them. The 290 s cap remains: baseline medians
plus fixture setup imply about 75 s at steady cost. Timeout now retains partial
measurements with a failure status. Each requested-history quarter reports per-verb
percentiles and sequence boundaries, including any partially completed quarter.

The builder sandbox cannot bind AF_UNIX; wire, concurrency, crash and the original
oversized-frame scenario must be rerun by the coordinator. Socket-free complexity
guards live in `tests/test_history_cost.py` (200/5,000 events, fixed SQLite VM-step
budgets, deliberate scans as failing controls). VM steps measure SQL work, not storage
latency. Scoped ID forget is bounded by affected rows; broad/entity/topic forget and
pending-intent cause inspection still scale with their candidates. WAL truncation
remains synchronous. Additive `CREATE INDEX IF NOT EXISTS` statements apply on opening
an existing lab store; there is no production migration or new environment variable.

Owner-decision repair: forget counts all erased event rows (including grants,
refusals and named scoped notices). Shield controls restore explicit consent before
turning off the shield, so erasing consent cannot mask the control's attack. Text
reassertion and explicit entity release remain blocked pending the owner's decision
on deliberate relearning. Trusted bootstrap can register idempotent store purges and
independent read-back with the kernel; forget runs them before erasing consent.
The reminder store is purged synchronously even if the skill ignores its notice.
Store-purge registration is absent from Context and the wire/action catalog.
Context's replay delete is restricted to driver-opted-in IDs from a live forget notice;
it cannot erase arbitrary rows or supply a replacement grant. The architecture document
records the failure/retry boundary and the outstanding owner question.

Fresh scratch schemas use `WITHOUT ROWID` for cursors, metadata and retired counts,
eliminating three redundant primary-key B-trees. Person and cause links are sets
with composite primary keys, replacing each link table's separate lookup B-tree.
The named `people_seq` and `latest_receipt` indexes remain, as do the cause lookup
and all intent indexes; repeated links are ignored rather than stored twice.
The oversized-frame oracle checks that no 4096-byte run of the 200,000-byte body
is on disk and that DB/WAL/SHM total stays below twice its length, matching the
boundary test. SQL complexity budgets stay unchanged.
The socket-free disk oracle now includes the launcher's initial audit grant; the
former empty-spine fixture understated WAL growth. Existing scratch tables retain
their layout; these DDL changes compact fresh stores, without rewriting old ones.
Review correction: authorised explainer and twin display results retain household content;
tool reports and bridge logs retain codes, IDs and counts only. A whole-person forget
also clears the owner's reduced twin facts through its watermark after source retention,
while cross-owner cleanup matches explicit erased request sequences.
