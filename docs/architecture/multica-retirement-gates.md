# Multica retirement gates — the §8.1 executable packet

> ## ⛔ STATUS 2026-07-20 — DO NOT EXECUTE THIS PACKET AS WRITTEN
>
> This document was written 2026-07-06 against `main @ 6891c93f`, **before** the
> outcome was known. Since then:
>
> - **Multica achieved true 100% hands-off idea→merged-PR autonomy** on
>   2026-06-17 (e2e8 / ZOE-5834 → PR #682 merged, `merge_sha 2d3edaa9`, zero
>   operator action). Twelve harness PRs are merged in `main`.
> - **It is PAUSED, not failed** — kill switch `~/.zoe/multica_dispatch_paused`
>   present since 2026-06-18 20:29, board at 0 dispatchable.
> - **Its board and workers live inside Hermes** (`~/.hermes/kanban.db`; gateway
>   `kanban_watchers.py`), so retiring Hermes silently removes the queue and
>   executor this packet assumes will still exist.
>
> The capability inventory below is still accurate and useful — it is a good map
> of what Multica does. What is **not** valid is the framing that these
> capabilities are awaiting retirement. Nothing here should be switched off until
> a replacement board + executor exists and is proven, and that replacement is
> not yet built (`labs/flue-harness-spike/` has the agent roles; the durable
> state layer is missing).
>
> See the stop blocks in [`zoe-flue-integration.md`](zoe-flue-integration.md)
> §8.1 and §8.2.

> Executable retire-gate spec for
> [`zoe-flue-integration.md`](zoe-flue-integration.md) **§8.1** (Multica —
> board-driven engineering orchestration). Rule of the land (§6 Phase 4 / §8):
> **retire by recreation** — a Multica capability is switched off only after its
> Flue recreation demonstrably owns the job, its proof gate holds, and an
> operator signs off. Nothing here is deleted because it's old.
>
> Every capability below is verified against the code as of 2026-07-06
> (`main` @ `6891c93f`); anchors are `file:line`. Design-only: this document
> changes no runtime behaviour.

---

## 1. Verified capability inventory — what Multica does TODAY

The "Multica surface" inside zoe-data is one poll loop plus five subsystems it
drives. Everything is gated on `ZOE_MULTICA=true`
(`services/zoe-data/main.py:1645-1646` starts the loop;
`main.py:1038-1045` syncs autopilot schedules at startup).

### C1 — Board poll loop: reconcile board ⇄ engineering-chain state

The 30s async loop `_multica_poll_loop` (`services/zoe-data/main.py:1056-1647`)
is Multica's heartbeat. Per cycle it:

- Reads board statuses `todo / in_progress / in_review / blocked`, tolerating
  partial failures and distinguishing outage from empty board
  (`_read_multica_board_statuses`, `main.py:539`).
- Polls each tracked issue's engineering chain with a timeout guard so one dead
  executor ref can't wedge the loop (`_poll_chain_guarded`, `main.py:328`;
  20s default via `ZOE_MULTICA_POLL_REF_TIMEOUT_S`).
- Reconciles terminal states back onto the board: done chains
  (`_record_completed_multica_chain`, `main.py:471`), blocked chains with
  operator-visible blocker text (`_record_blocked_multica_chain`,
  `main.py:711`), running-progress sync (`main.py:422`), and diverged
  board-status repair (`chain_needs_reconcile` branch, `main.py:1599-1607`).
- Broadcasts `multica_task_done / _blocked / _progress` WebSocket events to
  connected UIs (`main.py:1541-1596`).
- Recovers stale `in_progress` zombies after `ZOE_MULTICA_STALE_IN_PROGRESS_HOURS`
  (default 6; `_recover_stale_in_progress_issues`, `main.py:366`).
- Bounds blocked-chain resume probing with a rotating budget so it can't starve
  admission or OOM the 16 GB host (`_bounded_blocked_resume_window`,
  `main.py:288`; `ZOE_MULTICA_BLOCKED_RESUME_BUDGET` default 4).
- Throttles to `ZOE_MULTICA_PAUSED_POLL_S` (default 300s) while dispatch is
  paused (`_multica_poll_interval_s`, `main.py:312`).

### C2 — Single-lane admission + dispatch of ticket work

- **Admission:** when the lane is idle and `ZOE_MULTICA_AUTO_ADMIT=true`, one
  backlog issue is promoted to `todo` through the contract/priority gate
  `multica_admission.select_next_approved_issue`
  (`services/zoe-data/multica_admission.py`; call site `main.py:1259-1288`).
- **Dispatch:** the loop calls `executor_registry.dispatch_issue`
  (`services/zoe-data/executor_registry.py:35`) **in-process** — routed to the
  single `KanbanAdapter` (`executor_registry.py:13-31`) — capped at
  `ZOE_MULTICA_POLL_DISPATCH_LIMIT` (default 1) new chains per cycle, backfilling
  existing in-progress chains before starting fresh todos (`main.py:1290-1462`).
- **Operator pause:** a file flag `~/.zoe/multica_dispatch_paused`
  (`services/zoe-data/multica_dispatch_control.py:9-31`) halts dispatch;
  `multica_operator.py` provides operator-safe ticket actions from chat.

### C3 — Phase-chain orchestration (the engineering pipeline)

`KanbanAdapter` (`services/zoe-data/executors/kanban_adapter.py:828`) drives the
fixed six-phase chain `scout → implement → verify → review → closeout → retro`
(`_CHAIN`, `kanban_adapter.py:61-73`; typed as `PipelinePhase`,
`services/zoe-data/pipeline_evidence.py:17`):

- `dispatch()` (`kanban_adapter.py:1785`) idempotently creates only the single
  current ready phase, keyed `multica:{issue_id}:{phase}`.
- `poll()` (`kanban_adapter.py:2062`) aggregates chain state
  (`running | blocked | done | partial | not_found`).
- Durable state is an append-only JSONL journal at
  `~/.zoe/engineering_pipeline_runs.jsonl` (`pipeline_store.store_path`,
  `services/zoe-data/pipeline_store.py:112-116`), with evidence-gated phase
  transitions (`pipeline_evidence.transition`) and worker handoffs parsed into
  evidence items (`services/zoe-data/pipeline_handoff.py`).
- Stall recovery is self-healing: auto-block on protocol violation / dead worker
  / phase budget (`kanban_adapter.py:1272,1310,1350`), recovery of pushed
  branches that never got a PR (`_maybe_recover_pushed_pr`,
  `kanban_adapter.py:1439`), unshipped-diff rescue
  (`kanban_adapter.py:1563`), and no-op-implement convergence
  (`kanban_adapter.py:1683`).

### C4 — PR pipeline closeout (Greptile merge gate)

Harness-side deterministic validators appended per phase
(`pipeline_store.py:472,505,570`): focused tests (verify), review approval
(review), and the closeout merge. Closeout runs
`scripts/maintenance/run_greploop_guard.sh` — the Greptile fix-packet loop —
and only records `done` once the PR is actually merged
(`services/zoe-data/pipeline_closeout.py:27,84-113`;
`pipeline_review.py:65` explicitly defers Greptile thread resolution to this
gate). Externally-merged PRs complete the pipeline via
`complete_pipeline_after_external_merge` (`pipeline_store.py:344`).

### C5 — Worktree lifecycle

`services/zoe-data/worktree_bootstrap.py` owns task isolation:

- Create: `ensure_worktree` / `prepare_kanban_worktree`
  (`worktree_bootstrap.py:181,237`) — one worktree per task under
  `~/.worktrees/<task_id>` on branch `wt/<task_id>`
  (`worktree_bootstrap.py:36-48`), never the live checkout.
- Remove on completion: `remove_task_worktree` (`worktree_bootstrap.py:319`) —
  only when registered, clean, and merged (true ancestor **or** squash-merged PR
  via `gh pr view`, `_pr_is_merged`, `worktree_bootstrap.py:262`); called from
  `KanbanAdapter._cleanup_chain_worktrees` on chain completion
  (`kanban_adapter.py:2333-2358`).
- Daily safety-net sweep: `prune_merged_worktrees` (`worktree_bootstrap.py:388`)
  from the poll loop (`main.py:1148-1160`, `ZOE_WORKTREE_PRUNE_INTERVAL_S`
  default 86400) — skips the live checkout, dirty, locked, and unmerged trees.
- Manual fallback: `scripts/maintenance/prune_worktrees.sh` (dry-run by default;
  `scripts/AGENTS.md`).

### C6 — Autopilots (scheduled jobs)

`multica_autopilot_sync.sync_autopilots_from_multica`
(`services/zoe-data/multica_autopilot_sync.py`) registers Multica-defined cron
schedules into APScheduler at startup (`main.py:1038-1045`). Three live
autopilots (contracts in
[`../knowledge/autopilots/index.md`](../knowledge/autopilots/index.md)):
Platform Health Check (daily), Evolution Weekly Digest, Evolution Nightly
Notice. Tracker-issue wrappers are off by default; the poll loop closes stale
`Autopilot:` rows (`main.py:1138-1142,1494-1518,1620-1631`).

**Gap that C6's recreation must close FIRST (verified `main.py:1038`):** the
autopilot sync is gated by the same single `ZOE_MULTICA` flag as everything
else — there is NO independent kill for the scheduled autopilots today.
Because C6 is sequenced first while C1–C5 stay live, step 0 of C6 is adding a
dedicated `ZOE_MULTICA_AUTOPILOTS` env check to the sync block
(`main.py:1036-1043`, default `true` for today's behavior) so the trial can
run Flue schedules with Multica autopilots OFF while dispatch/poll (C1–C5)
remain untouched. Without this switch, the "never both live" Forbidden rule
and the C6-first sequencing are contradictory.

---

## 2. Per-capability: Flue recreation → proof gate → rollback

Common rollback rule for every row: **Multica stays installed-but-idle**
(`ZOE_MULTICA=false`, or dispatch paused via
`~/.zoe/multica_dispatch_paused`) until its gate holds. Re-enabling is a single
env flip + restart — no code restore needed, which is why no code is deleted
before a gate passes.

| # | Capability | Flue recreation | Proof gate (must ALL hold before switch-off) | Rollback |
|---|---|---|---|---|
| C6 | Autopilots | Flue **schedules** (cron-triggered durable submissions), one per live autopilot, honouring the existing Loop-Engineering contracts (Job/Forbidden fields become agent instructions). | Each of the 3 autopilots runs on Flue for **14 consecutive days** at its cadence with zero missed fires (vs. APScheduler log) and produces the same output class (health issue on failure / digest / nightly notice). Operator sign-off per autopilot. | Autopilot stays registered in Multica; re-sync via `sync_autopilots_from_multica` restores APScheduler jobs on restart. Run the two schedulers only with the *Flue* copy in dry-run/log-only until cutover — never both live (duplicate issues). |
| C5 | Worktree lifecycle | **Reuse the module as-is first**: `worktree_bootstrap.py` is harness-agnostic Python, exposed to the Flue lane as an exec-helper tool (create / remove / prune). Port into Flue workflow helpers only after the ticket lane (C3) has moved. | For **10 consecutive Flue-lane tasks**: worktree created under `~/.worktrees/`, never the live checkout; on merge, worktree + `wt/` branch removed with zero orphans left for the daily sweep; the sweep reports 0 `removed` rows attributable to Flue tasks over **14 days**. | The poll-loop daily sweep (`main.py:1148`) and `prune_worktrees.sh` remain active as the safety net throughout; they are the last Multica pieces to go. |
| C4 | PR closeout / Greptile gate | A Flue **workflow step/subagent** wrapping the same guard: invoke `run_greploop_guard.sh --pr N --merge-when-ready` (port `greploop_guard.py` mechanics, don't rewrite) and verify merge via REST. | **3 consecutive PRs** taken by the Flue step from open → every Greptile thread resolved (GraphQL `resolveReviewThread`) → squash-merged, with zero manual rescue and zero `--admin`/`--force`. Merge verified via REST `pulls/N.merged`. | `pipeline_closeout.py` keeps owning closeout for Multica-dispatched chains until the gate passes; the guard script itself is shared and is not retired at all. |
| C3 | Phase-chain orchestration | A **durable Flue submission per ticket**: bound agent delegates scout → implement → verify → openPR → closeout to subagents (the proven #858 Phase-0 harness pattern). Phase/evidence state lives in Flue run durability; the JSONL journal is retired with the adapter — retired = CLOSED to new writes, not deleted: `~/.zoe/engineering_pipeline_runs.jsonl` is the evidence trail for every past run and is archived read-only at retirement (move under `~/.zoe/archive/`; Flue run durability is the go-forward record; deleting the historical file is NOT part of the gate). zoe-data stays SoR for anything user-visible. | **5 consecutive real tickets** end-to-end (branch → PR → merged) on the Flue lane over **≥14 days** with zero manual rescue and zero stalls needing the C1-style recovery paths; every worktree cleaned (C5 gate held throughout); evidence per phase inspectable in Flue run history at parity with `pipeline_summary` (`pipeline_store.py:708`). Operator sign-off. | `KanbanAdapter` + `pipeline_store` stay importable and dispatch-able; flipping `ZOE_MULTICA=true` + assigning a ticket restores the old lane. During the trial, route tickets to exactly ONE lane (see Forbidden — collision rule). |
| C2 | Admission + dispatch | Flue lane intake: tickets enter as durable submissions; the single-lane cap and contract gate (`select_next_approved_issue` logic) port as the lane's admission policy. | Subsumed by C3's gate **plus**: during the 5-ticket run, at no point do two engineering chains run concurrently (the single-lane invariant observable in Flue run history). | Same env flip as C3; `multica_dispatch_control` pause file keeps working as the manual brake for the legacy lane. |
| C1 | Poll loop / board reconcile | Mostly **disappears**: durable Flue runs don't need a 30s reconcile heartbeat. What remains user-visible (task done/blocked/progress pushes to the UI) is re-pointed at Flue run events. Board reads, if the board outlives Multica, become a thin status view over Flue runs. | Last to go, automatically: passes only when C2–C6 gates have ALL passed and the loop has had nothing to reconcile for **14 days** (poll log `~/.zoe/zoe-data-poll.log` shows zero dispatches, zero recoveries, zero prune removals attributable to Multica chains). | `ZOE_MULTICA=false` already makes the loop a no-op (`main.py:1645`); switch-off is just leaving the flag false. |

---

## 3. Sequencing — what moves first and why

Order: **C6 autopilots → C4 PR closeout → C3+C2 ticket lane (with C5 reused
in place) → C5 port → C1 switch-off.**

1. **C6 first (lowest risk, most observed).** Autopilots are cron-fired,
   idempotent, low-frequency, and their outputs (health issues, digests) are
   the most operator-visible artifacts in the whole system — a missed or
   duplicated fire is noticed within a day. They touch no worktrees, no PRs,
   no git state. Flue schedules are also the most primitive Flue feature being
   leaned on, so this doubles as the cheapest soak test of Flue durability.
2. **C4 second.** The greploop guard is already a standalone script with its
   own `--packet-only`/`--once` bounded modes; wrapping it in a Flue step is
   small, so evidence accumulates fast. **Scope during the trial (collision
   rule):** the Flue C4 step takes ONLY PRs that are not owned by a
   Multica-dispatched chain — Multica chains keep `pipeline_closeout.py` for
   their own PRs until C3's gate passes (per the C4 rollback row). One PR,
   one closer, always; the 3-PR gate counts only Flue-owned PRs.
3. **C3+C2 third — the big one.** The durable-submission ticket lane replaces
   the adapter + journal + recovery machinery. It reuses `worktree_bootstrap`
   (C5) and the already-proven C4 step, so by the time it runs, its two
   riskiest dependencies are gate-proven.
4. **C5 port, then C1 lapse.** Only after the Flue lane owns tickets does
   porting worktree helpers make sense; the poll loop then has nothing left to
   reconcile and its gate passes by observation, not by construction.

---

## 4. Forbidden

- **Never delete Multica code, tests, or services before the owning
  capability's gate passes** and an operator signs off. Deletion PRs come only
  from a green gate row (§8.5 of the integration doc); until then Multica is
  installed-but-idle, one env flip from live.
- **Never run two orchestrators against the same ticket or PR simultaneously**
  (collision rule). During any trial, a ticket/PR belongs to exactly one lane:
  either the Multica chain or the Flue submission. Concretely: don't enable
  `ZOE_MULTICA_AUTO_ADMIT` dispatch and Flue-lane intake on the same board
  pool, and never point two greploop drivers at one PR.
- **The live checkout (`/home/zoe/assistant`) is never a task worktree** — for
  Multica *or* Flue. All task work happens under `~/.worktrees/<task_id>`
  (`worktree_bootstrap.py:36-48`); cleanup code must keep skipping the live
  checkout (`prune_merged_worktrees`, `worktree_bootstrap.py:388`).
- **Never bypass merge discipline while proving a gate**: no `--admin`, no
  `--force`, squash-only, Greptile threads resolved — a gate "passed" by
  bypassing the gate's own rules is void.
- **Never let the recreation weaken a guard the original had**: worktree
  removal keeps the merged-and-clean checks; dispatch keeps the single-lane
  cap; closeout keeps merge-verified-via-REST. Parity includes the safety
  rails, not just the happy path.
- **Voice path untouched throughout** — none of this work may touch the
  STT→brain→TTS path; any shared-resource change still owes the §4 latency
  gate of the integration doc.
