# Multica-First Engineering Driver

Multica is the operator-visible source of truth. Zoe owns deterministic phase
control and the append-only journal; Hermes executes one bounded phase at a time.

The Hermes worker that runs the `implement` phase loads the `zoe-engineering`
skill (`~/.hermes/skills/zoe-engineering/SKILL.md` plus profile copies). That
skill's **Harness Operating Model** section mirrors this guide: stay in the task
worktree, terminal protocol (`kanban_complete`/`kanban_block`), one small PR per
implement task. Keep the two in sync.

> **Dispatch architecture.** The Hermes gateway (`hermes gateway run`, systemd
> `hermes-agent.service`) is the *executor*: it spawns `work kanban task <id>`
> worker subprocesses for **ready, assigned** tasks already on the Hermes Kanban
> board (`kanban.dispatch_in_gateway: true` — required; disabling it stops all
> worker execution). The gateway has **no Multica integration** — it never pulls
> issues directly. The Zoe adapter (`services/zoe-data/executors/kanban_adapter.py`,
> `created_by=zoe-bridge`) is the **only** authorized creator of engineering tasks
> and emits worktree-isolated phase tasks. Gateway `kanban.auto_decompose` is kept
> **false** so the gateway never auto-creates/fans-out tickets out of band.

## Required Setup

- `ZOE_MULTICA=true`
- `MULTICA_BASE_URL`, `MULTICA_API_TOKEN`, and `MULTICA_WORKSPACE_ID`
- `MULTICA_WEBHOOK_SECRET` for any webhook path that starts Hermes (run `python3 scripts/maintenance/setup_multica_webhooks.py` once to generate)
- Optional `MULTICA_WEBHOOK_TARGET_URL` (default `http://127.0.0.1:8000/api/agent/board/webhook`) for the Zoe-side emitter
- `HERMES_MULTICA_AGENT_ID` set to the Hermes agent ID in Multica (or rely on [`agents_registry.yml`](../../services/zoe-data/agents_registry.yml))
- `HERMES_API_KEY` or `API_SERVER_KEY` matching the Hermes gateway (`runtime_env.bootstrap_runtime_env()` loads service `.env`)
- Hermes running on `HERMES_API_URL` or `http://127.0.0.1:8642`
- Run `python3 scripts/maintenance/sync_hermes_env_from_zoe.py` once so `~/.hermes/.env` includes `MULTICA_*` for Hermes MCP subprocesses
- GitHub auth available to Hermes through `gh` or `GITHUB_TOKEN`
- Greptile auth through `GREPTILE_API_KEY` or `~/.config/zoe/greptile.env`

## Flow

1. A user, API caller, authenticated Multica webhook (`issue.assigned`), board approval, or the Zoe poll bridge asks Zoe to dispatch a Hermes-assigned issue. For new `zoe-chain: v4` runs, Zoe creates only the current ready phase from the JSONL pipeline journal. It does **not** pre-create the full chain.
2. Zoe bootstraps or replays `pipeline_store` state and emits one Hermes Kanban task for the current phase (`scout`, or `implement` when scout is skipped).
3. Hermes executes that single bounded phase and must end with `kanban_complete` or `kanban_block`.
4. Zoe polls the phase row, parses evidence, and advances the journal only when `pipeline_evidence` requirements pass.
5. When the journal moves to a new `todo` phase and no Kanban row exists for that phase, the poll bridge or compatibility script may dispatch exactly that next phase.
6. `zoe-planner` **closeout** runs the Greptile grep loop (`github-greptile-loop`) and squash-merges when Greptile + CI are green (`greploop_guard.py --merge-when-ready`).
7. `zoe-planner` **retro** captures learnings and optional harness improvements (no silent production changes).
8. Zoe marks the Multica issue done only after retro completes, then admits the next explicitly approved backlog ticket.
9. Legacy v2/v3 chains are still recognized while in-flight board work drains; poll treats them as complete when closeout (or retro, when present) finishes.

## Model Routing Policy (Phase 0 cost control)

Worker-chain routing is intentionally different from main chat routing:

- **Main Hermes** (`~/.hermes/config.yaml`):
  - Primary: `openai-codex / gpt-5.4`
  - Fallback: `openrouter / openrouter/free`
- **Kanban workers** (`zoe-planner`, `zoe-coder`, `zoe-reviewer`):
  - Primary/fallbacks are OpenRouter-only (no direct `gemini` or `openai-api` provider entries)
  - Planner/reviewer primary: `minimax/minimax-m3`; coder primary: `deepseek/deepseek-chat-v3.1`
  - Fallback order: `minimax/minimax-m3` → `google/gemini-2.5-flash` → `openrouter/free`

This keeps board execution off Codex usage while preserving a deterministic low-cost fallback path.

### Webhooks (how Multica talks to Zoe)

Zoe exposes `POST /api/agent/board/webhook` for `issue.assigned`, `issue.status_changed`, and `issue.created`.

Stock Multica (ghcr backend) does **not** have a `/api/webhooks` registry for outbound issue events. Two paths feed the same receiver:

| Path | When | How |
|------|------|-----|
| **Zoe poll bridge** | Always (default today) | `multica_webhook_emitter.py` in the 30s poll loop POSTs `issue.assigned` for Hermes **todo** issues and backfills an **in_progress** issue whose current journal phase has no Kanban task (`chain_needs_dispatch` in `multica_poll_dispatch.py`) |
| **Multica push** | After rebuilding backend with `zoe_webhook_listener.go` | Multica event bus POSTs to `ZOE_BOARD_WEBHOOK_URL` from the container |

Set the same secret in both places:

- Zoe: `MULTICA_WEBHOOK_SECRET` in `.env` / `services/zoe-data/.env`
- Multica container: `ZOE_BOARD_WEBHOOK_SECRET` (see `docker-compose.modules.yml`)

Proposal status sync via `issue.status_changed` does not require the secret. **Starting Kanban work** requires either `Authorization: Bearer $MULTICA_WEBHOOK_SECRET` or `X-Multica-Webhook-Token: $MULTICA_WEBHOOK_SECRET`.

## API Smoke Procedure

Run these on the Zoe host after migrations are applied:

```bash
curl -sf http://127.0.0.1:8000/health
curl -sf http://127.0.0.1:8000/api/system/status
```

Draft a dry engineering workflow through chat:

```text
offload hermes to make a docs-only test PR that changes no production behavior
```

This should create a queued workflow that still needs approval before Hermes changes code. Start one through the admin API, board approval, or a Hermes-assigned Multica issue, then check progress:

```text
what's the hermes engineering status
```

Expected journal phase progression (one Kanban task exists at a time):

- `scout` (`zoe-planner`) — Graphify/opensrc/Multica context only; `TOOLS_USED=` + `SCOUT_SUMMARY=` handoff.
- `implement` (`zoe-coder`) — graphify/opensrc first, smallest reviewable change, small PR on a worktree at `~/.worktrees/<kanban_task_id>` (override root with `ZOE_WORKTREE_ROOT`). Zoe pins this path on the Kanban row at dispatch so workers do not default to `<repo>/.worktrees/`. Terminal protocol: `kanban_complete` or `kanban_block` on the last turn. Handoff metadata must include `PR_URL`, `TESTS`, `TOOLS_USED`, `SUMMARY`. Pure audit/doc tasks: `AUDIT_ONLY=1` with blank `PR_URL`.
- `verify` (`zoe-reviewer`) — objective test/evidence gate before review; records validator + test outcomes. Fail-closed: missing evidence blocks advancement.
- `review` (`zoe-reviewer`) — diff/scope/architecture check against verify evidence; may loop back to implement via revision metadata.
- `closeout` (`zoe-planner`) — Greptile grep loop, squash merge when ready, Multica status update.
- `retro` (`zoe-planner`) — learnings + optional follow-up issue; pipeline completes when retro finishes.
- Engineering mode: `ZOE_ENGINEERING_MODE=interactive|overnight|quality-escalation` (or issue metadata) adjusts worker runtime and cost preference.

### Worktree isolation (every task runs in its own checkout)

Each phase task runs in an **isolated git worktree**, never the live checkout
(`/home/zoe/assistant`):

- The Zoe adapter pins `workspace_kind=worktree` and a `workspace_path` of
  `~/.worktrees/<kanban_task_id>` on the Kanban row at dispatch (override root with
  `ZOE_WORKTREE_ROOT`). `worktree_bootstrap.py` creates the worktree from
  `origin/main` (fresh base), on branch `wt/<task_id>`.
- The only phase that runs in the live checkout is `retro` (`workspace_kind=dir`,
  `dir:<repo_root>`) — it is read-only orchestration/learning work, so it does not
  spawn a phantom worktree after closeout has already merged.
- Workers must run **all** git/test/validator/patch/PR commands from their
  `workspace_path`. A single command that references `/home/zoe/assistant` trips the
  `WORKTREE_PATH_VIOLATION` guard and ends the run. The worker prompts and the
  `zoe-engineering` skill enforce this.

### Budget and safety guards (kanban_adapter.py)

The implement/verify/review/closeout prompts and `poll()` enforce fail-closed guards.
A worker blocks (`kanban_block`) with a `BLOCKER=` fingerprint rather than drifting:

| Guard / `BLOCKER` | Phase | What it prevents |
|-------------------|-------|------------------|
| `IMPLEMENT_HANDOFF_DRIFT` | implement | Burning the pre-edit explore budget hunting context instead of using the scout handoff; classified with `IMPLEMENT_BUDGET`. |
| `WORKTREE_PATH_VIOLATION` | all | Any command run against the live checkout (`/home/zoe/assistant`) instead of the task `workspace_path`. |
| `IMPLEMENT_EDIT_SAFETY` | implement | Committing/continuing with a malformed or unsafe edit; the worker blocks instead. |
| `ITERATION_BUDGET` | all | Exceeding the per-phase turn/iteration budget; the run blocks instead of looping. |

### Evidence gate (fail-closed advancement)

`poll()` advances the journal only when `pipeline_evidence` requirements pass.
Crucially, **implement must record a `pr` evidence item** (a `PR_URL=` on a pushed
branch) before verify/review can run. Missing the `pr` evidence blocks advancement.
Audit/doc-only tickets opt out with `AUDIT_ONLY=1` (blank `PR_URL`) or
`evidence_profile: audit`.

### Capture-on-exit (unshipped-diff salvage)

If an implement worker finishes a complete diff but runs out of turns before
`git commit`/`push`/`gh pr create`, the adapter's
`_maybe_recover_unshipped_diff` salvages it: it commits the worktree diff,
pushes branch `wt/<task_id>`, and opens a PR so the chain advances to verify
instead of a fresh-worktree resume discarding the work and re-spending. Hard
safety gates: it operates **only** inside the task's own `wt/<task_id>` branch
(never `main`/`master`, never the live checkout, never `--force`) and never opens
an empty PR. A sibling recovery, `_maybe_recover_pushed_pr`, handles the case
where the worker pushed but lost the `PR_URL` handoff. Workers should still
push + open the PR themselves; salvage is a backstop, not the plan.

### Hard-ticket split policy

Hard or broad tickets should fail cleanly instead of burning retries. If `implement`
cannot fit the work into one small PR, hits repeated protocol/turn-budget failures,
or needs a product/architecture split, it must block with:

```text
NEEDS_SPLIT=1
SPLIT_PACKET={"child_issue_template":{"title":"<parent>: <small deliverable>","description":"Scope + acceptance criteria + evidence"},"reason":"<why split is required>"}
```

The pipeline records `block_classification=scope_split_required`, persists the
`split_packet` in `~/.zoe/engineering_pipeline_runs.jsonl`, and suppresses
redispatch. Downstream verify/review/closeout phases must not be treated as ready
until a smaller child issue is created or an operator deliberately clears the block.
Repeated implement `PROTOCOL_VIOLATION`, `TURN_BUDGET`, `CONTEXT_LIMIT`, or
`TOKEN_LIMIT` fingerprints are classified the same way even if the worker did not
write an explicit packet.

## Board rollout

After deploying this PR on the Zoe host:

1. `systemctl --user daemon-reload && systemctl --user restart zoe-data.service`
2. `python3 scripts/maintenance/sync_hermes_env_from_zoe.py`
3. `python3 scripts/maintenance/setup_multica_webhooks.py --probe`
4. Reassign open issues to Hermes:

   ```bash
   python3 scripts/maintenance/multica_reassign_open_to_hermes.py --dry-run
   python3 scripts/maintenance/multica_reassign_open_to_hermes.py --execute
   ```

5. **Dispatcher:** the 30-second Zoe poll bridge is authoritative. The compatibility CLI calls the same decision gate:

   ```bash
   python3 scripts/maintenance/sync_multica_to_kanban.py --dry-run --limit 1
   ```

Keep `ZOE_BOARD_REVIEW_AUTOPILOT_ENABLED=false` (the Zoe poll loop and cron own dispatch).

**Operator cron (outside repo):** disable Board Fix Progress Watcher; ensure Graphify refresh script path is valid under `~/.hermes/scripts/`.

## Contributor quick reference (ZOE-5378)

Use this when dispatching or reviewing a Multica ticket:

| Control | Where to set | Effect |
|---------|--------------|--------|
| `skip_scout: true` | Issue description or `metadata.skip_scout` | Omits scout phase; chain starts at implement |
| `evidence_profile: audit` | Issue description or `metadata.evidence_profile` | Verify gate accepts audit-only handoffs (no PR required) |
| `ZOE_KANBAN_SKIP_SCOUT=1` | `services/zoe-data/.env` | Global skip-scout for all new chains |
| `ZOE_MULTICA_POLL_DISPATCH_LIMIT=1` | `services/zoe-data/.env` | Poll bridge dispatches one Hermes todo issue per cycle (default when unset; use `0` to disable dispatch) |

**Idempotency:** Kanban tasks are keyed `multica:{issue_uuid}:{phase}`. Re-dispatch is safe when poll reports `not_found` or `partial`; active `running`/`blocked` chains are left alone.

**Runtime pause:** `pause engineering dispatch` creates
`~/.zoe/multica_dispatch_paused`; `resume engineering dispatch` removes it.
Both the poll bridge and compatibility CLI obey the same sentinel.

**Greptile closeout:** Closeout runs `github-greptile-loop` / `greploop_guard.py --merge-when-ready` only when implement recorded `PR_URL=` on a pushed branch. Audit-only issues should hand off with `AUDIT_ONLY=1` and blank `PR_URL`.

**Terminal protocol:** Every worker phase must end with `kanban_complete` or `kanban_block`. Silent exits trigger dispatcher retries and may auto-block after two protocol violations (`ZOE_KANBAN_PROTOCOL_VIOLATION_LIMIT`, default 2).

## Local Verification

```bash
python3 scripts/maintenance/engineering_harness_loop.py --mode full
python3 scripts/maintenance/engineering_harness_loop.py --mode kanban-dry --skip-scout
```

See [ENGINEERING_HARNESS_LOOP.md](./ENGINEERING_HARNESS_LOOP.md) for modes, exit codes, and pipeline JSONL findings.

Legacy one-liners (subset of the harness):

```bash
python3 -m pytest services/zoe-data/tests/test_kanban_adapter.py services/zoe-data/tests/test_pipeline_evidence.py services/zoe-data/tests/test_executor_registry.py services/zoe-data/tests/test_multica_webhook_emitter.py services/zoe-data/tests/test_multica_client.py services/zoe-data/tests/test_multica_poll_dispatch.py services/zoe-data/tests/test_runtime_env.py -q
python3 -m py_compile services/zoe-data/executor_registry.py services/zoe-data/executors/kanban_adapter.py services/zoe-data/multica_webhook_emitter.py services/zoe-data/multica_client.py services/zoe-data/runtime_env.py
python3 tools/audit/validate_structure.py
python3 tools/audit/validate_critical_files.py
```
