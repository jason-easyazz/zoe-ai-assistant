# Multica Hermes PR Loop

This workflow lets Zoe track engineering work from a Multica issue through Hermes implementation, GitHub PR creation, Greptile review, and human merge readiness.

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

1. A user, API caller, authenticated Multica webhook (`issue.assigned`), board approval, or the Zoe poll bridge creates a Hermes Kanban chain (**scout → implement → verify → review → closeout → retro**) on OpenRouter-routed worker profiles. Set `ZOE_KANBAN_SKIP_SCOUT=1` or issue `skip_scout` metadata to omit scout on well-scoped tasks. Legacy in-flight chains may still be on older paths; poll treats them as complete when closeout (or retro, when present) finishes.
2. `zoe-planner` **scout** gathers Graphify/opensrc/Multica context read-only (no code changes).
3. `zoe-coder` **implement** opens a small PR and hands off with `PR_URL=`, `BLOCKER=`, `TESTS=`, `TOOLS_USED=`, `SUMMARY=`.
4. `zoe-reviewer` **verify** runs the objective evidence gate (structure validators, focused tests, live health) before review spends tokens.
5. `zoe-reviewer` **review** checks diff scope and verify-phase evidence; blocks or requests changes when evidence is missing.
6. `zoe-planner` **closeout** runs the Greptile grep loop (`github-greptile-loop`), squash-merges when Greptile + CI are green (`greploop_guard.py --merge-when-ready`), then updates the Multica issue.
7. `zoe-planner` **retro** captures learnings and optional harness improvements (no silent production changes).
8. The Zoe poll loop advances Multica `in_progress` issues to `done` when retro completes (or closeout for legacy/v2 chains without retro). Structured evidence uses `pipeline_evidence` + JSONL `pipeline_store`; fail-closed gates block phase advancement when required evidence is absent.

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
| **Zoe poll bridge** | Always (default today) | `multica_webhook_emitter.py` in the 30s poll loop POSTs `issue.assigned` for Hermes **todo** issues and **backfills** Hermes **in_progress** issues that have no Kanban chain yet (`chain_needs_dispatch` in `multica_poll_dispatch.py`) |
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

Expected Kanban chain progression (per Multica issue):

- `scout` (`zoe-planner`) — Graphify/opensrc/Multica context only; `TOOLS_USED=` + `SCOUT_SUMMARY=` handoff.
- `implement` (`zoe-coder`) — graphify/opensrc first, smallest reviewable change, small PR on a worktree at `~/.worktrees/<kanban_task_id>` (override root with `ZOE_WORKTREE_ROOT`). Zoe pins this path on the Kanban row at dispatch so workers do not default to `<repo>/.worktrees/`. Terminal protocol: `kanban_complete` or `kanban_block` on the last turn. Handoff metadata must include `PR_URL`, `TESTS`, `TOOLS_USED`, `SUMMARY`. Pure audit/doc tasks: `AUDIT_ONLY=1` with blank `PR_URL`.
- `verify` (`zoe-reviewer`) — objective test/evidence gate before review; records validator + test outcomes. Fail-closed: missing evidence blocks advancement.
- `review` (`zoe-reviewer`) — diff/scope/architecture check against verify evidence; may loop back to implement via revision metadata.
- `closeout` (`zoe-planner`) — Greptile grep loop, squash merge when ready, Multica status update.
- `retro` (`zoe-planner`) — learnings + optional follow-up issue; pipeline completes when retro finishes.
- Engineering mode: `ZOE_ENGINEERING_MODE=interactive|overnight|quality-escalation` (or issue metadata) adjusts worker runtime and cost preference.

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

5. **Dispatcher:** Hermes cron `hourly-zoe-board-dispatch` (`dispatch-hermes-board.sh` → `sync_multica_to_kanban.py`) or the 30s Zoe poll webhook bridge:

   ```bash
   python3 scripts/maintenance/sync_multica_to_kanban.py --dry-run --limit 1
   ```

Keep `ZOE_BOARD_REVIEW_AUTOPILOT_ENABLED=false` (the Zoe poll loop and cron own dispatch).

**Operator cron (outside repo):** disable Board Fix Progress Watcher; ensure Graphify refresh script path is valid under `~/.hermes/scripts/`.

## Contributor quick reference (ZOE-5378)

Use this when dispatching or reviewing a Hermes Kanban chain on Multica:

| Control | Where to set | Effect |
|---------|--------------|--------|
| `skip_scout: true` | Issue description or `metadata.skip_scout` | Omits scout phase; chain starts at implement |
| `evidence_profile: audit` | Issue description or `metadata.evidence_profile` | Verify gate accepts audit-only handoffs (no PR required) |
| `ZOE_KANBAN_SKIP_SCOUT=1` | `services/zoe-data/.env` | Global skip-scout for all new chains |
| `ZOE_MULTICA_POLL_DISPATCH_LIMIT=1` | `services/zoe-data/.env` | Poll bridge dispatches one Hermes todo issue per cycle (default when unset; use `0` to disable dispatch) |

**Idempotency:** Kanban tasks are keyed `multica:{issue_uuid}:{phase}`. Re-dispatch is safe when poll reports `not_found` or `partial`; active `running`/`blocked` chains are left alone.

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
