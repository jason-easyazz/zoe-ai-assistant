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
spawn → report loop, not the agent roles — **built and lab-proven in
`labs/flue-executor/`, BOTH lanes (Phase 1 contract complete 2026-07-22):
33/33 e2e asserts (2026-07-22; suite since grown to 35 — see FINDINGS) — the local Flue worker lane (2026-07-21) plus the Omnigent
heavy lane live against `zoe-omnigent` (session + staged brief + runner +
docker-exec kick; completion by nonce token — sessions settle to `idle`, never
`completed`), with the reason-on-every-transition write-through, the #685
reap, and Omnigent-down failing loudly while the local lane runs.** Evidence:
`labs/flue-executor/FINDINGS.md`. What remains before Phase 2: registering the
executor's `agent_runtime` row and pointing it at the REAL Multica tables
(the lab mirrors the DDL in a scratch DB).

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
3. The routing POLICY was already decided (§5: Omnigent = primary heavy lane,
   local 4B = light lane); the open question was only its implementation point.
   **Answer: a rule in the executor at claim/spawn time, keyed off the claimed
   task's context (task-class rides the handoff)** — local lane = a Flue
   per-agent model (config), heavy lane = an Omnigent kick (session + staged
   brief + `omnigent run -r` — not a model swap). Claim-time routing keeps the
   Phase-2 `kanban_adapter` change to the minimal seam swap and preserves
   "Omnigent down → local lane still runs".

### Phase 2 — re-point `kanban_adapter`  ← seam LANDED 2026-07-22, flag-dark

Swap the dispatch target from the `hermes kanban` CLI to the Phase-1 executor.
Keep every phase, gate and deterministic override untouched. Prove on ≥3 real
tickets end-to-end before Phase 3.

**Seam shipped (default OFF).** `kanban_adapter._run` — the single CLI call
site, exactly as §2 predicted — now dispatches on `ZOE_KANBAN_BACKEND`:

| value | behaviour |
|---|---|
| `hermes` (**default**) | today's `hermes kanban` CLI. Shipping this changed nothing. |
| `executor` | `executors/executor_queue_backend.py` serves the same six verbs (`list`/`show`/`create`/`block`/`archive`/`complete`) against Multica's own `agent_task_queue` + `activity_log`. |

The adapter's 2,394 lines of discovered failure modes are **untouched** — 279
of its existing tests pass unchanged. Revert path: unset the env var and
restart (no code change, no migration).

**Proven against the REAL Multica tables** (not a scratch DB) by
`scripts/maintenance/verify_executor_queue_backend.py` — 30/30 checks:
identity registration is idempotent; `create` dedupes on the idempotency key
under an advisory lock (no double dispatch); every row/detail field the
adapter reads is present and `_row_ref_key()` correlates; the status
vocabularies map (`queued→ready`, `failed→blocked`, `completed→done`); and
**every transition lands a non-empty reason in `activity_log`** — the §4
non-negotiable, now visible in `multica-web` where Hermes recorded
`blocker_reason` 0/128 times. The probe task is deleted afterwards; the
reason entries are kept as evidence.

**Registered in live Multica** (additive, no schema change): `agent_runtime`
"Flue Executor (Zoe)" + `agent` "Flue Executor" (`max_concurrent_tasks=1`,
mirroring the single-lane contract). Schema gotcha on record: `agent.status`
is `idle|working|blocked|error|offline` — NOT `agent_runtime`'s
`online|offline` — and `agent.description` is capped at 255 chars.

**Still to do before Phase 3:** flip the flag on a real ticket with the
executor process running, and land ≥3 real tickets end-to-end (≥1 heavy via
Omnigent). The dispatch kill switch (`~/.zoe/multica_dispatch_paused`) stays
until that holds.

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
- [ ] **The Omnigent lane proven too**: at least one heavy ticket routed to and
      completed via Omnigent under the Phase-1 routing rule — a local-only
      executor does NOT satisfy this gate (§5 decision 2)
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

## 6. Background-task engineering lane — NOT built (investigated + closed 2026-07-24)

A `background_runner.py` "engineering lane" (route `Implement evolution proposal`
tasks to the Omnigent `execute_issue_dict` lane) was specced (PR #1538) and built
flag-dark (PR #1547), then **both closed** — the premise was wrong.

**Finding 1 (why the background lane was wrong):** nothing enqueues a proposal
as a *background* task. The only builder of the `Implement evolution proposal
<id>` string is the proposal-approve endpoint (`routers/system.py`), which
dispatched via `dispatch_issue`, never `enqueue_background_task`. So a
`background_runner` engineering lane targets a task that never arrives there.
Corroborating: its pre-existing auto-deploy regex is dead code and matched
**dashed** UUIDs while real `evolution_proposals.id` is **32-hex non-dashed**.
`background_runner` stays general-only.

**Finding 2 (the real break — proposals reached NO live executor):** an early
draft of this note claimed proposals "already reach Omnigent via the board." That
was WRONG, and was disproved on real approved proposal `631f4b5e` (operator:
"prove it on a real proposal before flipping"). In truth:
- `dispatch_issue` routes to the **Kanban PHASE pipeline** (`kanban_adapter` →
  `pipeline_store`), whose consumer (Hermes / the Flue live-runner) is
  **retired / not running** — `agent_task_queue` is empty, the unit inactive.
- The proposal's `multica_issue_id` was a **phantom** — never persisted to the
  `issue` table (`update_multica_issue_on_proposal_status_change` only *updates*,
  never creates).
- The board **runner** (running, proven) claims `todo` issues from the `issue`
  table — a **different mechanism/table** than `dispatch_issue`. Proposals never
  reached it.

So approved proposals stranded, reaching no executor. *Lesson: verify the task
actually FLOWS to a running consumer — reading the dispatch code is not proof.*

**Fix (PR #1557):** `proposal_board_bridge` lands an approved proposal as a `todo`
issue directly in the board runner's workspace (body from the proposal's own DB
fields only), and the approve endpoint calls it instead of `dispatch_issue`. So
proposals now flow through the proven board lane (runner → `execute_issue_dict` →
gated PR → merge). **Proven end-to-end before wiring:** `631f4b5e` → issue #6112
→ PR #1555 (*fix(agent): stop clock fast-path hijacking business-hours
questions*) → merged.

**Remaining Hermes CLI users — the FULL scope (all `background_runner` callers).**
`background_runner._run_hermes_background_task` shells `hermes -p <profile> -z`,
and it is the general-task engine for **every** `enqueue_background_task` entry
point, not just escalation. To retire the Hermes CLI, ALL of these must move (else
they fail once the CLI is gone):

- `routers/chat.py` — Zoe-Agent background escalation **and** the
  `/api/chat/tasks/*` generic task API.
- `routers/voice_tts.py` — voice escalation (post-#1531 → background).
- `mcp_server.py` — the `hermes` agent MCP tool (A2A/agent-to-agent entry).
- the A2A **delegation-depth** path (`request_depth`/`_MAX_REQUEST_DEPTH`).
- `routers/system.py` — the background-task admin endpoints.

Most are **general/research** work that should route to the web-search/browse
tier (task #18); the A2A/MCP generic-task callers may instead want the Omnigent
general lane. The Hermes CLI can't be deleted (nor `HERMES_API_KEY` revoked)
until **every** caller above has a non-Hermes home — task #18 is necessary but
not by itself sufficient.
