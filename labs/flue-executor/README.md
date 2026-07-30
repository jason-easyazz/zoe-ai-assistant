# Flue Executor (lab-only) — Phase 1 of the Multica executor migration

> **STATUS: opt-in Phase-2 unit — installed inert, default-OFF.** `install-jetson.sh`
> ships `flue-executor.service` as an installed-but-never-enabled template behind
> three safety gates (kill switch armed, `ZOE_EXECUTOR_DISPATCH=dry`, single lane).
> It is not imported by any service and does nothing until an operator deliberately
> enables and unpauses it. **Go-live runbook (the only source of truth for enabling
> it): [`scripts/setup/systemd/README.md`](../../scripts/setup/systemd/README.md).**
> Design: [`docs/architecture/multica-executor-migration.md`](../../docs/architecture/multica-executor-migration.md).

## What this proves

The Phase-1 contract — the piece that replaces the Hermes gateway's
`kanban_watchers` (nothing here touches Hermes, `kanban_adapter`, or the live
Multica DB):

- **claim** a ready task atomically, single lane (`POLL_DISPATCH_LIMIT=1`
  semantics), via a per-runtime advisory lock + `FOR UPDATE SKIP LOCKED`
- **route** at spawn time from the claimed task's context (§5 decision 2):
  `context.lane === 'heavy'` → the **Omnigent lane** (session + staged brief +
  runner + the docker-exec kick REST cannot do; completion detected by a
  per-task nonce token in the session items); everything else → the local lane
- **spawn** a local worker as a real Flue workflow process
  (`flue run phase-worker`) bound to the task's work_dir (worktree handoff)
- **report** terminal state **with a reason on every transition**, written
  through to `activity_log` in the same transaction — an empty reason throws
- **reap** dead workers (the #685 zombie-lane behaviour): dead-pid `running`
  rows, age-stalled `dispatched` rows, and orphaned Omnigent rows (recovered by
  token evidence, failed by timeout) — always with a reason, requeued while
  attempts remain

The e2e's Omnigent scenarios need the live `zoe-omnigent` container (`:6767`)
— the lane test is a REAL claude-sdk session (tiny, no file work), and an
unreachable Omnigent is an honest test failure, not a skip.

The three migration-doc §3 unknowns are answered with evidence in
[`FINDINGS.md`](./FINDINGS.md).

## Where it runs

On the Jetson, against a **scratch database** (`multica_executor_lab`) in the
existing Postgres container — never the live `multica` DB (the config refuses
that name). The schema mirrors the live `agent_task_queue` + `activity_log`
DDL (minus FKs) so the claim semantics are proven on the real engine. The
synthetic phase worker **never opens a model session** — no model, no GPU, no
network egress; the registered provider is a dead end by design.

## Run it

```bash
cd labs/flue-executor
npm install
npm run typecheck
npm run test:unit  # pure unit tests: no DB, no network, no npm install needed
npm run e2e        # the synthetic end-to-end proof (5 scenarios, hard asserts)
npm run executor   # the loop itself, standalone (Ctrl-C to stop)
npm run live       # the supervised live runner (Ctrl-C to stop)
```

`test:unit` needs neither Postgres nor Omnigent nor `npm install` (every runtime
import is a node builtin or local) — `node --test` type-strips the `.ts` directly.
It covers the omnigent lane's **session-id shell guard**: the id is interpolated
into the docker-exec `sh -c` kick, so `assertSafeSessionId` refuses anything that
is not `^(?:conv_)?[A-Za-z0-9]+$` (both the `conv_<hex>` and the bare-hex 0.7.0
shape). The last test is a behavioural negative control — it drives the real
`spawnOmnigentWorker` with a hostile id over a stubbed fetch and goes red if the
guard call is deleted from the call site, not merely if the helper is.

Credentials are read at runtime from `LAB_DATABASE_URL`, or derived from
`POSTGRES_URL` in the live service env file (`ZOE_ENV_FILE`, default
`/home/zoe/assistant/services/zoe-data/.env`). Nothing secret is committed.

## What Phase 2 changes (not this lab)

`kanban_adapter` swaps its dispatch target from the `hermes kanban` CLI to this
executor's queue: enqueue = INSERT into Multica's real `agent_task_queue`
targeting this executor's registered `agent_runtime` row; worktree creation
stays with `worktree_bootstrap` (see FINDINGS unknown 1); worker-model routing
is decided by the executor at spawn time (FINDINGS unknown 3). Multica's
dispatch kill switch (`~/.zoe/multica_dispatch_paused`) stays until Phase 2 is
proven on ≥3 real tickets.
