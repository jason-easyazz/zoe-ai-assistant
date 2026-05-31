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

1. A user, API caller, authenticated Multica webhook (`issue.assigned`), board approval, or the Zoe poll bridge creates a Hermes Kanban chain (implement → review → closeout) on DeepSeek worker profiles.
2. `zoe-coder` implements on a worktree, opens a small PR, and hands off with `PR_URL=`, `BLOCKER=`, `TESTS=`, `SUMMARY=`.
3. `zoe-reviewer` runs verification-first checks (structure validators, focused tests, live health).
4. `zoe-planner` closeout runs the Greptile grep loop (`github-greptile-loop`) and updates the Multica issue when merge-ready.
5. The Zoe poll loop advances Multica `in_progress` issues to `done` when the Kanban chain completes.

### Webhooks (how Multica talks to Zoe)

Zoe exposes `POST /api/agent/board/webhook` for `issue.assigned`, `issue.status_changed`, and `issue.created`.

Stock Multica (ghcr backend) does **not** have a `/api/webhooks` registry for outbound issue events. Two paths feed the same receiver:

| Path | When | How |
|------|------|-----|
| **Zoe poll bridge** | Always (default today) | `multica_webhook_emitter.py` in the 30s poll loop POSTs `issue.assigned` for Hermes todos |
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
