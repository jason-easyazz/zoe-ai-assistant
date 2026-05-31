# Multica Hermes PR Loop

This workflow lets Zoe track engineering work from a Multica issue through Hermes implementation, GitHub PR creation, Greptile review, and human merge readiness.

## Required Setup

- `ZOE_MULTICA=true`
- `MULTICA_BASE_URL`, `MULTICA_API_TOKEN`, and `MULTICA_WORKSPACE_ID`
- `MULTICA_WEBHOOK_SECRET` for any webhook path that starts Hermes
- `HERMES_MULTICA_AGENT_ID` set to the Hermes agent ID in Multica (or rely on [`agents_registry.yml`](../../services/zoe-data/agents_registry.yml))
- `HERMES_API_KEY` or `API_SERVER_KEY` matching the Hermes gateway (`runtime_env.bootstrap_runtime_env()` loads service `.env`)
- Hermes running on `HERMES_API_URL` or `http://127.0.0.1:8642`
- Run `python3 scripts/maintenance/sync_hermes_env_from_zoe.py` once so `~/.hermes/.env` includes `MULTICA_*` for Hermes MCP subprocesses
- GitHub auth available to Hermes through `gh` or `GITHUB_TOKEN`
- Greptile auth through `GREPTILE_API_KEY` or `~/.config/zoe/greptile.env`

## Flow

1. A user, API caller, Multica webhook, board approval, or Board Review autopilot creates an `engineering_tasks` row.
2. Zoe queues Hermes with a structured prompt requiring `PR_URL=`, `BLOCKER=`, `TESTS=`, and `SUMMARY=`.
3. Hermes implements on a feature branch, opens a PR, and uses `github-greptile-loop`.
4. Zoe reconciles the linked `background_tasks` row and records the PR URL.
5. Zoe checks Greptile through MCP-compatible tools and updates workflow phase plus Multica issue status.
6. The workflow stops at `ready_for_human`, `blocked`, `cancelled`, or `done`.

Multica webhooks can sync proposal status without the secret, but they cannot start Hermes unless they send either `Authorization: Bearer $MULTICA_WEBHOOK_SECRET` or `X-Multica-Webhook-Token: $MULTICA_WEBHOOK_SECRET`.

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

- `implement` (`zoe-coder`) — small PR opened on a worktree.
- `review` (`zoe-reviewer`) — verification-first checks; blocks merge if they fail.
- `closeout` (`zoe-planner`) — Greptile grep loop, then Multica issue set to `done`.
- The Zoe poll loop advances the Multica issue to `done` when the chain completes.

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

## Local Verification

```bash
python3 -m pytest services/zoe-data/tests/test_kanban_adapter.py services/zoe-data/tests/test_executor_registry.py services/zoe-data/tests/test_multica_webhook_emitter.py services/zoe-data/tests/test_multica_client.py services/zoe-data/tests/test_runtime_env.py -q
python3 -m py_compile services/zoe-data/executor_registry.py services/zoe-data/executors/kanban_adapter.py services/zoe-data/multica_webhook_emitter.py services/zoe-data/multica_client.py services/zoe-data/runtime_env.py
python3 tools/audit/validate_structure.py
python3 tools/audit/validate_critical_files.py
```
