# Multica → Pi/Flue executor migration — scope

> **Decision of record (Jason, 2026-07-20): KEEP Multica.** Not for its data —
> "I don't care about the issues inside multica, they could all be erased" — but
> as **software**. What moves is *execution*, not the board.
>
> Companion to [`zoe-flue-integration.md`](zoe-flue-integration.md) §8.1/§8.2,
> whose stop-blocks already require a proven executor replacement *before* Hermes
> retires. This document is that replacement's scope.

---

## 1. What Multica actually is, verified

Not a Hermes component. A third-party product running **on Zoe**:

| | |
|---|---|
| Containers | `zoe-multica-backend`, `zoe-multica-web` (`ghcr.io/multica-ai/*`) |
| Storage | **its own Postgres DB** (`…/multica`), not Zoe's SoR |
| Zoe's client | `multica_client.py`, `multica_ticket_contract.py`, webhook emitter |

**Why it's worth keeping** — the parts you would otherwise build:

- **`multica-web`** — the human steering surface. Unattended engineering needs
  somewhere to see what is queued, blocked and in flight, and to intervene. That
  UI is weeks of work.
- **An integration that already works** — the client, contract and emitter are
  written and proven.
- **Agent-native primitives** — `agent_task_queue`, `agent_skill`,
  `autopilot_run` are first-class in its schema. Most trackers model humans and
  bolt agents on afterwards.
- **Two containers.** Near-zero operational cost, separate DB, no SoR coupling.

**Immediate risk, unrelated to this migration:** both images run `:latest`.
An unpinned third-party image on a load-bearing system means a bad upstream push
lands silently on the next pull. **Pin the digests** — do this regardless of
whether the migration proceeds.

---

## 2. The actual coupling — what leaves

```
TODAY:   Multica ──> kanban_adapter ──> `hermes kanban` CLI ──> Hermes gateway workers
                     └─ KEEP ────────┘   └────────── REPLACE ──────────────────┘

TARGET:  Multica ──> kanban_adapter ──> Zoe-native executor (Flue) ──┬─> local worker
                                                                     └─> Omnigent (heavy lane)
```

**Nothing about the board changes.** Multica keeps holding the work.

**`kanban_adapter.py` stays** — and this is the point most likely to be got
wrong. It encodes twelve PRs of *discovered* failure modes: stranded chains
(`#592`/`#597`), workers that finish and never ship (`#520`), PR-URL handoff
(`#601`), verify agents that block instead of verifying (`#607`/`#632`), review
agents that flake (`#672`/`#677`), closeout agents that *claim* success without
merging (`#679`/`#681`), zombie workers holding the lane (`#685`), no-op
implements (`#694`). **Rebuilding it means rediscovering all of that.** Its
Hermes coupling is a CLI call site (`hermes_bin`, `executors/kanban_adapter.py`
~`:839`) — a seam, not a rewrite.

---

## 3. Phases

### Phase 1 — the Zoe-native executor  ← the only hard part

Replaces the Hermes gateway's `kanban_watchers`. Contract:

- claim a ready task atomically (one lane, per today's `POLL_DISPATCH_LIMIT=1`)
- spawn a worker for its phase with the task's worktree + handoff
- report terminal state back, **with a reason on every transition**
- reap a worker whose process died (the `#685` behaviour — do not lose it)

Substrate: Flue. `labs/flue-harness-spike/` already has `scout` / `verifier`
`defineAgentProfile`s and `sandbox: local()`. The missing piece was the claim →
spawn → report loop, not the agent roles — **built and proven lab-first in
`labs/flue-executor/` (2026-07-21): synthetic end-to-end ticket, 18/18 asserts,
including the reason-on-every-transition write-through and the #685 reap.**
Evidence: `labs/flue-executor/FINDINGS.md`.

**The three unknowns — settled 2026-07-21** (full evidence in
`labs/flue-executor/FINDINGS.md`):
1. **`sandbox: local()` does not manage git state at all** — it binds an agent
   to an *existing* host directory (`packages/runtime/src/node/local.ts` is a
   thin fs+spawn wrapper; the spike's #864 dirty-tree bug proved the same live).
   **`worktree_bootstrap` stays authoritative**; the executor passes the task's
   worktree path through the handoff (`work_dir`) as the worker's `cwd`.
2. **Multica's `agent_task_queue`, no Zoe-side lease table.** The live schema
   already has a claim-candidates partial index keyed by `runtime_id`, the full
   status lifecycle, `attempt`/`max_attempts`/`failure_reason`, and a
   one-pending-task-per-issue guard — and `activity_log` is in the same DB, so
   the reason commits in the same transaction as the status flip. Claim =
   per-runtime advisory lock + `FOR UPDATE SKIP LOCKED` (SKIP LOCKED alone
   double-dispatches under concurrency — proven and closed in the lab). A
   Zoe-side lease table would hide queue state from `multica-web`.
3. **Routing is decided in the executor at spawn time** from the claimed task's
   context: local lane = a Flue per-agent model (config), heavy lane = an
   Omnigent kick (session + brief + `omnigent run -r` — not a model swap).
   Claim-time routing keeps the Phase-2 `kanban_adapter` change to the minimal
   seam swap and preserves "Omnigent down → local lane still runs".

### Phase 2 — re-point `kanban_adapter`

Swap the dispatch target from the `hermes kanban` CLI to the Phase-1 executor.
Keep every phase, gate and deterministic override untouched. Prove on ≥3 real
tickets end-to-end before Phase 3.

### Phase 3 — Omnigent as a second executor

`zoe-omnigent` (`:6767`) already has `/home/zoe/assistant` mounted rw and `gh`
credentials. Register it alongside the Flue executor and let Multica route by
task class — heavy/multi-file work to Omnigent, small work local. Additive: if
Omnigent is down, the local lane still runs.

### Phase 4 — retire Hermes

Only now, and **no part of the gate may be treated as pre-satisfied.**

`zoe-flue-integration.md` §8.2 states the live board is `~/.hermes/kanban.db`.
That wording was written before this document established the split, and is
imprecise rather than wrong: **Multica is the board** (issues, activity log,
inbox — its own DB), while **`~/.hermes/kanban.db` is the executor queue** that
`kanban_adapter` writes tasks into. Both exist today.

So the "durable board outside Hermes" gate is **NOT** ticked by Multica's mere
existence. What must be true before Hermes is retired:

- [ ] The **executor queue** no longer lives in Hermes (Phase 1) — this is the
      piece §8.2 is really about
- [ ] An executor runs phase workers without the Hermes gateway (Phase 1)
- [ ] Both proven on ≥3 real tickets end to end (Phase 2)
- [ ] Operator sign-off naming the `hermes-agent.service` row specifically

Retiring Hermes on the strength of "Multica already exists" would remove the
queue and workers while leaving the board — the exact failure §8.2's stop-block
was added to prevent.

---

## 4. Non-negotiables

- **Record a reason on every transition.** Multica's `activity_log` already does
  this (`actor_type`/`actor_id`/`action`/`details`); the executor must write
  through to it. Hermes's kanban recorded `blocker_reason` **zero times across
  128 blocked tickets**, which is why June's failure modes had to be found one at
  a time by hand.
- **Do not rebuild `kanban_adapter`.** See §2.
- **Prove before retiring.** Phase 1 runs alongside Hermes; nothing is removed
  until Phase 2 has landed real merged PRs.
- **Multica stays paused** (`~/.zoe/multica_dispatch_paused`) until Phase 2 is
  proven. It is paused deliberately, not broken — it reached 100% hands-off
  idea→merged-PR autonomy on 2026-06-17 (ZOE-5834 → PR #682).

---

## 5. Decisions of record (Jason, 2026-07-20)

1. **Phase 1 substrate is Flue** — no Python interim loop. (The lab executor
   reuses `worktree_bootstrap` anyway, per unknown 1: worktree lifecycle stays
   in Python regardless of the executor substrate.)
2. **Worker model** — the deterministic verify/review/closeout gates carry over
   unchanged; the local 4B's agentic-reliability limits are contained by the
   gates, not by the worker. Confirmed compatible with Flue's per-agent model
   binding (the spike's live runs + the lab executor's spawn design).
3. **Omnigent is a PRIMARY heavy executor lane from day one**, not a deferred
   Phase-3 add-on — the executor routes heavy/multi-file work to it at spawn
   time (see unknown 3). Additive: if Omnigent is down, the local lane runs.
4. **OpenClaw fully retires.** It is not a lane in this architecture.
5. **Hermes retires only via the Phase 4 gates** — no part of that gate is
   treated as pre-satisfied.
