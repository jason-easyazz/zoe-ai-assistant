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

**Nothing here is wired to anything.** No production module imports it. (The one exception is a socket: `shadow_daemon.py` is
what zoe-data's flag-dark list-add shadow talks to once the owner installs its unit; zoe-data never imports this code.) There is no
service or unit that runs by default. Standard library only (SQLite in a temp dir, a Unix socket). Every person and every word in the tests
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
| `zoe_os/wire.py` | the socket protocol: frames, version 1, the closed table of ten ops and their arguments, plus the authority daemon's four (`health`, `erase_member`, `erase_item`, `list_digest`) |
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
| `shadow_daemon.py` | the kernel as a long-running SHADOW process for zoe-data's shadow (list add, list remove, reminder create, alarm create) (`docs/knowledge/zoe-os-list-shadow.md`): roster in, credentials + socket out; owner-installed unit only |
| `bench_brain.py` | latency of the kernel hop per brain action |
| `bench.py` | one capped benchmark (60 s, 100k events) |
| `bench_socket.py` | per-request latency of the socket hop (one kernel process, one client) |
| `tests/kernel_proc.py`, `tests/skill_proc.py`, `tests/procs.py` | the kernel and a skill as separate processes, for the boundary and kill tests |
| `authority.py` | the kernel as the AUTHORITY for list add (stage c, `docs/knowledge/zoe-os-integration.md`): durable list rows rebuilt from the receipts, the `erase_member` / `erase_item` / `health` / `list_digest` ops, revokes remembered; unit template `zoe-os-authority-kernel.service` beside it (never installed) |
| `tests/authority_proc.py`, `tests/test_authority.py` | the authority daemon as its own process with one SIGKILL point or one control armed, and its tests |

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
| action arguments and undo state cannot restore shielded words or names (validation pending) | `test_forget_action_arguments.py`, `test_forget_receipts_notices.py` | disabling `Dispatcher._check_arguments` makes the resurrection regression fail; disabling snapshot scrub AND this guard permits restoration |
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

## Authority kernel (`authority.py`)

**Why the rows are rebuilt from the log.** A row exists exactly when a live, verified `list.add` receipt says so (less the verified
`list.remove` / `undo` receipts after it). The receipt is already durable and committed in one transaction with its write-ahead
intent, and every forget already reaches it, so there is ONE copy of the words to erase and nothing here changes the spine's or the
dispatcher's transaction code. The cost is a scan of the receipts at start (measured below) and after each forget (one person's).

**Kill points** (`tests/test_authority.py`, real SIGKILL): after the intent, after the driver ran but before the receipt (absent after
the restart, an `uncertain` receipt), after the receipt but before the reply (present and verified). Controls: `no_rebuild` (an
acknowledged add is lost and the test sees it), `no_member_forget` (the erase's read-back says `verified: false`).

**Measured** (this box, one client, list.add over the socket = publish + act): see the PR for the numbers of the run that landed it.

**Where the database lives (owner decision 2026-11-15).** The kernel keeps its OWN SQLite file, never tables in the main database,
in a permanent home beside Zoe's other stores (`~/.zoe/music-assistant/`, `~/.zoe/night-extract/`, `~/.zoe/omi-recordings/`, ...):
**proposed `~/.zoe/zoe-os/zoe-os.db`** (directory 0700, file 0600; `authority.PERMANENT_DB`). ONE setting names it:
`ZOE_OS_KERNEL_DB` in the unit template, passed to the daemon as `--db` (both daemons take it), and the same registered flag in zoe-data
(`os_authority.kernel_db_path()`, used only to name the store; zoe-data never opens the file). Without `--db` the lab default is
`STATE/kernel/zoe-os.db`. A daemon given `--db` refuses to start while that file is missing but the old `~/.zoe/os-shadow/kernel/zoe-os.db`
still exists, so a skipped move can never start an empty kernel beside an unmoved record.

**One-time move (owner step, before installing either unit with `--db`; nothing here does it).** Stop the kernel, copy consistently,
check, then remove the old copy (a second copy would be a store no forget reaches):

    systemctl --user stop zoe-os-shadow-kernel.service zoe-os-authority-kernel.service 2>/dev/null
    mkdir -p -m 700 ~/.zoe/zoe-os
    python3 -c "import sqlite3,sys; s=sqlite3.connect(sys.argv[1]); d=sqlite3.connect(sys.argv[2]); s.backup(d); d.close(); s.close(); c=sqlite3.connect(sys.argv[2]); assert c.execute('PRAGMA integrity_check').fetchone()[0]=='ok'; print('events', c.execute('SELECT COUNT(*) FROM events').fetchone()[0])" ~/.zoe/os-shadow/kernel/zoe-os.db ~/.zoe/zoe-os/zoe-os.db \
      && chmod 600 ~/.zoe/zoe-os/zoe-os.db \
      && rm -f ~/.zoe/os-shadow/kernel/zoe-os.db ~/.zoe/os-shadow/kernel/zoe-os.db-wal ~/.zoe/os-shadow/kernel/zoe-os.db-shm

**Nightly backup (from the day shadow is on).** A concrete addition to `scripts/maintenance/mempalace-nightly-backup.sh`, the nightly
job that already snapshots a live SQLite file with Python's online backup API (the `sqlite3` CLI is not installed on the Jetson); not
`postgres-nightly-backup.sh`, which is `pg_dump` only. Proposed block, after the MemPalace rotation (not applied by this change):

    ZOE_OS_DB="${ZOE_OS_KERNEL_DB:-$HOME/.zoe/zoe-os/zoe-os.db}"
    ZOE_OS_ROOT="${ZOE_BACKUP_DIR:-$HOME/.zoe-backups}/zoe-os"
    if [[ -f "$ZOE_OS_DB" ]]; then
      mkdir -p -m 700 "$ZOE_OS_ROOT"
      out="$ZOE_OS_ROOT/zoe-os-$(date +%Y%m%d-%H%M%S).db"
      python3 - "$ZOE_OS_DB" "$out" <<'PY'
    import sqlite3, sys
    src = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)   # WAL: a reader beside the live kernel, never a file copy
    dst = sqlite3.connect(sys.argv[2])
    src.backup(dst)                                                  # one consistent snapshot, committed WAL frames included
    dst.close(); src.close()
    chk = sqlite3.connect(f"file:{sys.argv[2]}?mode=ro", uri=True)
    assert chk.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert chk.execute("SELECT value FROM meta WHERE key = 'shield_key'").fetchone()   # a kernel database, not an empty file
    PY
      chmod 600 "$out" && gzip -9 "$out"
      ls -1t "$ZOE_OS_ROOT"/zoe-os-*.db.gz | tail -n +8 | xargs -r rm -f       # keep 7, like the palace
    fi

Never `cp` the live `.db` (its `-wal` holds committed adds the main file does not have yet). `creds.json` and `k.sock` are re-made at
every start and are never backed up; `people.json` is re-written from `users`. **Restore check** (run on the copy, kernel stopped):
`gunzip` it to the `--db` path, then the integrity check above, then start the daemon and read `health` (`schema` 1, `last_write_ok`
true) and `scripts/maintenance/os_authority_reconcile.py` (`kernel_only=0`). A restore older than a forget must re-apply the forget
(the repo-wide backup rule in `services/zoe-data/forget_reach.py`): re-run `delete_user` / the entity forget for anything forgotten
after the backup's date. The backup holds the same words the kernel does, so it ages out with the 35-day rule.

**Not done here.** Retention (a sweep as written would drop list rows: their receipts must be exempt first, D5), power loss (WAL with
`synchronous=NORMAL` survives a kill, not necessarily a power cut), the roster push (D6), the guardian model (D7).

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

A skill's own store is outside the kernel: a skill that ignores a forget notice keeps the words
(`test_HONEST_LIMIT_a_skill_that_ignores_the_notice_...`). A forget by entity or topic cannot match an event
whose content has aged out (`test_HONEST_LIMIT_a_scoped_forget_...`). Retention has a chosen policy (record,
section 9) and no scheduler: nothing expires until something calls the sweep.

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

Review correction: authorised explainer and twin display results retain household content;
tool reports and bridge logs retain codes, IDs and counts only. A whole-person forget
also clears the owner's reduced twin facts through its watermark after source retention,
while cross-owner cleanup matches explicit erased request sequences.
