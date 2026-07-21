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
`defineAgentProfile`s and `sandbox: local()`. The missing piece is the claim →
spawn → report loop, not the agent roles.

**Unknowns to settle before estimating** (this is why Phase 1 is not costed
here):
1. Does Flue's `sandbox: local()` give a worker a writable worktree on a branch,
   or does the harness still own `worktree_bootstrap`?
2. Atomic claim — Multica's `agent_task_queue`, or a Zoe-side lease table?
3. Worker model routing: local 4B vs Omnigent, decided where?

### Phase 2 — re-point `kanban_adapter`

Swap the dispatch target from the `hermes kanban` CLI to the Phase-1 executor.
Keep every phase, gate and deterministic override untouched. Prove on ≥3 real
tickets end-to-end before Phase 3.

### Phase 3 — (superseded by §5 decision 2: Omnigent is PRIMARY from day one)

Per the operator decision, Omnigent is not a deferred "second executor" — the
Phase-1 executor's worker-routing contract includes the Omnigent lane from the
start: heavy/multi-file implement work routes to `zoe-omnigent` (`:6767`, repo
mounted rw, `gh` creds), light work to the local 4B, and the deterministic
harness gates keep either worker honest. What remains as "Phase 3" is only
hardening the routing policy (task-class rules, Omnigent-down fallback to the
local lane) once Phase 2 has real tickets flowing. If Omnigent is down, the
local lane still runs — that property is non-negotiable.

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

## 5. Decisions — RESOLVED by operator (Jason, 2026-07-22)

1. **Phase 1 substrate = FLUE.** "Wouldn't using flue be smarter" — yes:
   durable run state, one engine (the brain already lives there), and the
   agent roles are already spiked in `labs/flue-harness-spike/`. No Python
   interim; build the claim → spawn → report loop on Flue directly.
2. **Omnigent is a PRIMARY executor lane from day one, not a deferred Phase 3.**
   "Omnigent is a beast, and should be used to build zoe until we get a box
   where local agents can do those tasks." Route heavy/multi-file implement
   work to Omnigent; the local 4B keeps the light lane and the deterministic
   harness gates keep either worker honest. Revisit the split only when new
   hardware lands.
3. **Hermes retires; Multica stays and pairs with Flue.** Confirmed. Keep what
   was built (skills/adapters already backed up in
   `docs/knowledge/operator-skills/`) as reference, and work toward this goal
   through the Phase 1-4 gates above — the gates themselves are unchanged.

**Related decisions of record (same date):**
- **OpenClaw: full retirement.** "Just bloody get rid of openclaw" — delete the
  runtime + builder intents with it (the intents name skills that never loaded;
  the ACP path's 2,338 runs were improvisation, not skill execution). Rebuild
  capabilities on Pi/Flue when actually needed, referencing the public
  Agent-Skills ecosystem ("internet of skills") rather than porting blind.
- **Hardware: PARKED, direction = DGX Spark.** No purchase now; "it will
  probably be a DGX Spark, i havent seen anything better yet" (Jason,
  2026-07-22). Coherent with the two-model direction: 128GB unified holds a
  larger Gemma brain AND a resident coding model simultaneously — a 24GB card
  cannot — plus local fine-tuning headroom, on the same CUDA stack Zoe already
  runs. The accepted trade: ~273GB/s bandwidth means big models generate
  steadily, not fast; capacity and training headroom are what the money buys.
