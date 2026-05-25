# Multica Hermes PR Loop

This workflow lets Zoe track engineering work from a Multica issue through Hermes implementation, GitHub PR creation, Greptile review, and human merge readiness.

## Required Setup

- `ZOE_MULTICA=true`
- `MULTICA_BASE_URL`, `MULTICA_API_TOKEN`, and `MULTICA_WORKSPACE_ID`
- `MULTICA_WEBHOOK_SECRET` for any webhook path that starts Hermes
- `HERMES_MULTICA_AGENT_ID` set to the Hermes agent ID in Multica
- Hermes running on `HERMES_API_URL` or `http://127.0.0.1:8642`
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

Expected state changes after approval/start:

- `queued` after task creation.
- `hermes_running` once the background task is enqueued.
- `pr_open` after Hermes returns a `PR_URL=...`.
- `ready_for_human` when Greptile reports target confidence and no actionable findings.
- `blocked` if Hermes reports `BLOCKER=...` or completes without a PR URL.

## Local Verification

```bash
python3 -m pytest services/zoe-data/tests/test_engineering_workflow.py -q
python3 -m py_compile services/zoe-data/engineering_workflow.py services/zoe-data/greptile_client.py
python3 tools/audit/validate_structure.py
python3 tools/audit/validate_critical_files.py
```
