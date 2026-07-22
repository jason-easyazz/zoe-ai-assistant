# FINDINGS — Flue Executor (Phase 1 of the Multica executor migration)

Run on the live Jetson Orin NX, 2026-07-21, in an isolated worktree. Node
v22.22.0, `@flue/runtime@1.0.0-beta.6` + `@flue/cli@1.0.0-beta.6`, Postgres =
the live `zoe-database` container (scratch DB `multica_executor_lab` only).

## The three migration-doc §3 unknowns — answered with evidence

### 1. Does `sandbox: local()` give a worker a writable worktree on a branch?

**No — and it doesn't try to. `worktree_bootstrap` stays authoritative.**

- Flue source (`packages/runtime/src/node/local.ts`): `local()` is a thin
  `SandboxFactory` over host fs + `child_process.spawn` with an env allowlist.
  The sandboxes guide says it is for agents operating "directly on the host
  filesystem … against an existing checkout" — `cwd` points at a directory
  that must already exist. There is no git, branch, or worktree management
  anywhere in the sandbox layer.
- Corroborated by the harness spike's #864 pollution bug: Flue happily ran in
  a dirty checkout; the spike had to add its own `git reset --hard` in
  deterministic code. Git state is the harness's job, not Flue's.

**Consequence:** the executor receives the task's worktree path in the handoff
(`work_dir` on the queue row) and passes it to the worker (`cwd` for a real
phase agent's `local()` sandbox). Python's `worktree_bootstrap.ensure_worktree`
/ `prepare_kanban_worktree` remain the single owner of worktree lifecycle.

### 2. Atomic claim — Multica's `agent_task_queue`, or a Zoe-side lease table?

**Multica's `agent_task_queue`. No new lease table.**

Evidence from the live schema (`\d agent_task_queue`, 2026-07-21):

- A purpose-built partial index exists for exactly this:
  `idx_agent_task_queue_claim_candidates (runtime_id, priority DESC, created_at)
  WHERE status = 'queued'`.
- The status lifecycle is enforced by CHECK:
  `queued → dispatched → running → completed/failed/cancelled`, with
  `dispatched_at/started_at/completed_at`, `attempt`/`max_attempts`,
  `failure_reason`, `result` jsonb, and a `context` jsonb for the handoff.
- Rows are partitioned by `runtime_id` (FK to `agent_runtime`) — the executor
  registers as its own runtime row and only ever claims its own rows, so it
  cannot race Multica's daemon push lanes (whose OpenClaw/Pi runtimes have
  been offline since 2026-06-19).
- A unique partial index (`one pending task per (issue_id, agent_id)`)
  already guards duplicate enqueues.
- `activity_log (actor_type/actor_id/action/details)` lives in the same DB, so
  the reason write-through commits **in the same transaction** as the status
  flip — reasons cannot be lost to a crash between two stores.
- A Zoe-side lease table would hide queue state from `multica-web`, defeating
  the entire keep-the-board rationale (§1 of the migration doc).

**Mechanics proven here:** claim = per-runtime `pg_advisory_xact_lock` + one
`UPDATE … WHERE id = (SELECT … FOR UPDATE SKIP LOCKED) RETURNING`, refusing to
claim while a dispatched/running row exists (single lane). SKIP LOCKED alone is
NOT sufficient — two concurrent claimers each skip the other's locked row and
both dispatch; the advisory lock closes that race (e2e scenario 2).

### 3. Worker-model routing (local 4B vs Omnigent) — decided where?

**In the executor, at spawn time, from the claimed task's context.** Two
mechanisms, one decision point:

- **Local Flue lane:** Flue binds the model **per agent** (`registerProvider`
  + a `provider/model` string in `defineAgent`) — the spike already proved
  per-agent models live. Which profile/model a phase gets is config the
  executor applies when it spawns the workflow.
- **Omnigent heavy lane (a PRIMARY lane per Jason's decision):** Omnigent
  (`:6767`, `/home/zoe/assistant` mounted rw, `gh` creds) is not a "model" —
  it is an alternative worker the executor kicks per the verified recipe
  (REST session + staged brief + `omnigent run -r <SID>` kick). Same claim,
  same reporting, different spawn path.

Routing at claim/spawn time (not enqueue time) keeps `kanban_adapter`'s Phase-2
change to the minimal seam swap the doc demands, and gives the fallback the doc
requires: Omnigent down → the local lane still runs. `runtime_id` partitioning
remains available later if the lanes ever need independent claim loops.

## The claim → spawn → report → reap loop — proven end-to-end

`npm run e2e` (synthetic ticket, real `flue run phase-worker` child processes,
scratch DB): **21/21 asserts PASS** —

```
== 1. reason enforcement (negative control) ==       2/2 PASS
== 2. atomic single-lane claim under concurrency ==  1/1 PASS
== 3. happy path: claim -> spawn -> report + CAS ==  6/6 PASS
== 4. reap: zombie running row, dead pid (#685) ==   4/4 PASS
== 4b. reap: stalled dispatched row ==               2/2 PASS
== 5. single lane + kill of a hung worker ==         6/6 PASS
E2E: ALL PASS
```

Review-hardening (Greptile, PR #1498), re-proven by the run above: transitions
are compare-and-swap (`WHERE id AND status=<expected from>`) so a racing
reporter (exit handler vs reaper) loses cleanly instead of overwriting a
terminal result (asserted in scenario 3); a worker's pid leaves the tracked set
at exit BEFORE the exit report, so a report that dies on a transient DB error
self-heals via the reaper instead of the tracked pid stalling the lane; the
scratch-DB guard is an allowlist (`multica_executor_lab` exactly), not a
`/multica` denylist. Second round: a `dispatched` row whose running transition
is lost (DB error mid-spawn, executor crash, instant-exit race) can no longer
wedge the lane — the reaper also fails-and-requeues dispatched rows older than
worker-timeout + grace (by then any real worker has been SIGKILLed by the spawn
timeout), the spawn path kills its child if the running CAS loses or throws so
no orphan outlives its row, and a failed tick no longer kills the loop
(scenario 4b).

- Every transition (`task_claimed`, `task_started`, `task_completed`,
  `task_failed`, `task_requeued`, reap) lands in `activity_log` with a
  **non-empty reason**, committed atomically with the status flip; an empty
  reason **throws**. This is the §4 non-negotiable (Hermes: `blocker_reason`
  0/128) made structural.
- The reaper failed-with-reason and requeued a seeded zombie `running` row
  whose pid was dead (the #685 behaviour), and the rerun completed.
- A hung worker held the single lane (second task provably waited), was
  killed, reported `failed` with the signal in the reason, and the lane freed.

**Harness honesty check (verify-your-instruments):** the first full run was
RED — 6 failures — because `flue run` rejected the app: `src/db.ts` is a
**reserved filename in Flue** (`[flue] db.ts must default-export a
PersistenceAdapter with a connect() method`). The executor machinery kept every
promise during that failure (transitions, reasons, requeue, lane handling); the
fix was renaming the lab module to `labdb.ts`. So the e2e demonstrably goes red
on a real defect, and a real Flue API gotcha is now on record: **do not name an
app-level module `src/db.ts` in a Flue project.**

## The Omnigent heavy lane — LIVE-PROVEN 2026-07-22 (Phase 1 complete)

Per §5 decision 2, the executor routes `context.lane === 'heavy'` to Omnigent
at spawn time. Full e2e (`npm run e2e`) after adding the lane: **29/29 asserts
PASS**, including a REAL heavy ticket end-to-end on the live `zoe-omnigent`
(`:6767`): session created for `polly` (claude-sdk), brief staged as a comment,
runner launched, the `docker exec … omnigent run -r <SID>` kick fired (REST
alone cannot start a claude-sdk run — re-confirmed), and the session's reply
carried the per-task nonce token; the executor reported `completed` with the
full `task_claimed → task_started → task_completed` reasoned chain and the
session id on the queue row.

Load-bearing findings for Phase 2:

- **Sessions never report `completed`** — they settle back to `idle` after
  replying. Completion detection MUST be by evidence (the nonce token in the
  session items), not by session status.
- **The anti-silence design proved itself on a real failure.** The first live
  run failed because the container's claude-sdk harness was logged out (its
  OAuth record had `expiresAt: 0`). The executor surfaced the ROOT CAUSE in
  `failure_reason` within seconds — `omnigent claude-sdk harness cannot run
  (fatal kick error): … Not logged in · Please run /login` — via fail-fast
  pattern-matching on the kick log, instead of a silent 10-minute timeout.
  Operator re-login (interactive `claude /login` in the container,
  2026-07-22) fixed it; the credential expires 2026-08-22, so this WILL
  recur — the executor's reasoned failure is the designed detection path.
- **Omnigent-down is loud and contained**: a heavy task fails with
  `omnigent lane unavailable …; local lane is unaffected` (scenario 6).
- Orphaned omnigent rows (executor restart) are recovered by the reaper on
  token EVIDENCE — completed work is never thrown away — and failed by age
  otherwise.

## Deliberate scope limits

- The local synthetic worker never opens a model session (dead-end provider);
  model choice per phase is config, proven separately by the spike's live runs.
- The live heavy ticket is a connectivity/contract proof (no file work by
  design); the §4 Hermes-retirement gate item "≥1 heavy ticket routed to and
  completed via Omnigent" refers to Phase-2 REAL tickets and stays unticked.
- The lab mirrors `agent_task_queue`/`activity_log` DDL minus FKs; Phase 2
  targets the real tables and must create the executor's `agent_runtime` row.
- Multica dispatch stays paused (`~/.zoe/multica_dispatch_paused`);
  `kanban_adapter` untouched; Hermes untouched.
