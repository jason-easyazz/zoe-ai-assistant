# Engineering Harness Loop

Deterministic operator gatekeeper for Zoe engineering quality. Runs focused tests,
structure validators, optional live health, Kanban dispatch checks, and scans recent
pipeline JSONL for fail-closed patterns. **Does not auto-fix code** — findings feed
greploop, Hermes, or human review.

## Script

`scripts/maintenance/engineering_harness_loop.py`

Reports append to `~/.zoe/harness_loop_report.jsonl` (one JSON object per line).

Pipeline scan reads `~/.zoe/engineering_pipeline_runs.jsonl` (override with
`ZOE_PIPELINE_STORE_PATH`).

## Modes

| Mode | What runs |
|------|-----------|
| `--mode smoke` | Focused pytest suite only |
| `--mode full` (default) | pytest + validators + `/health` + pipeline findings |
| `--mode kanban-dry` | full checks + `sync_multica_to_kanban.py --dry-run --limit 1` |
| `--mode kanban-live` | full + one live dispatch + 90s pipeline monitor |
| `--mode report` | Print latest report from `harness_loop_report.jsonl` |

Use `--skip-scout` with kanban modes to set `ZOE_KANBAN_SKIP_SCOUT=1` on dispatch.

## Focused pytest targets

- `test_kanban_adapter.py`
- `test_pipeline_handoff.py`, `test_pipeline_evidence.py`, `test_pipeline_store.py`
- `test_worktree_bootstrap.py`, `test_background_runner.py`
- `test_multica_poll_dispatch.py`, `test_greploop_guard.py`

## Worktree base provenance

Hermes task worktrees are created from `origin/main` by default to ensure fresh base branches. The worktree bootstrap process:

1. Attempts `git fetch origin <base_branch>` (default `main`)
2. If fetch fails, logs a warning: `"worktree_bootstrap: fetch origin/<base_branch> failed (rc=<N>); falling back to local tracking ref or branch: <stderr>"`
3. Creates worktree using the fetched remote reference when available

This prevents task worktrees from starting from stale local main branches. The fetch fallback warning indicates a possible stale base branch, which could affect task execution.

## Critical pipeline patterns (non-zero exit)

Recorded in findings; **fail exit** when any of:

- `fingerprint_abort`
- `scope_split_required`
- `WORKTREE_NOT_READY` (or embedded in row)
- `block_reason` null while status is `blocked`
- Repeated `gate_blocked` (≥5) for same `task_ref` + phase in the tail window

Individual `gate_blocked` rows are counted in `gate_blocked_count` for triage but do not alone fail the run.
`scope_split_required` means the parent Multica ticket is intentionally blocked with
a machine-readable `split_packet`; create a smaller child ticket or escalate to an
operator instead of redispatching the same broad implement phase.

## Usage

```bash
# Quick smoke (CI-friendly)
python3 scripts/maintenance/engineering_harness_loop.py --mode smoke

# Default operator iteration
python3 scripts/maintenance/engineering_harness_loop.py --mode full

# Kanban dispatch audit without side effects
python3 scripts/maintenance/engineering_harness_loop.py --mode kanban-dry --skip-scout

# One live issue + monitor (use sparingly)
python3 scripts/maintenance/engineering_harness_loop.py --mode kanban-live --skip-scout

# Read last report
python3 scripts/maintenance/engineering_harness_loop.py --mode report
```

Exit `0` = pass; `2` = test failure or critical pipeline finding.

After a failing run, use `scripts/maintenance/greploop_guard.py --packet-only --pr N`
for bounded repair packets — do not expect this script to patch code.

## Relation to Multica Hermes PR loop

See [MULTICA_HERMES_PR_LOOP.md](./MULTICA_HERMES_PR_LOOP.md). The harness loop is the
**verify-phase analogue for operators**: run before merging or after Kanban closeout
when pipeline JSONL shows repeated blocks.

## Learnings

- 2026-06-01: Stuck Multica issue `6dfb0e14` repeats scout `gate_blocked` (missing tool evidence) and implement `fingerprint_abort` — use `skip_scout` on audit-only issues or clear the chain before re-dispatch.
- 2026-06-01: `pipeline_store.sync_pipeline_from_chain` now passes `block_reason` into `transition()` history; older JSONL rows may still fail `block_reason_null` until archived.
- 2026-06-01: Pytest fixture refs (`multica:u`, `multica:uuid-*`) in operator JSONL are ignored by the harness scanner; isolate tests with `ZOE_PIPELINE_STORE_PATH`.

## Operator pause (stop Multica spend during manual work)

Before editing harness code, merging PRs, or running a controlled E2E, pause automated dispatch so Hermes workers and OpenRouter credits are not consumed in parallel:

```bash
# 1. Stop Zoe poll dispatch (host .env)
#    services/zoe-data/.env
ZOE_MULTICA_POLL_DISPATCH_LIMIT=0
systemctl --user restart zoe-data.service

# 2. Pause hourly board cron
hermes cron pause hourly-zoe-board-dispatch

# 3. Block or reclaim any active Kanban task (example)
hermes kanban block t_<task_id>
# or: hermes kanban reclaim t_<task_id>

# 4. Move large in-flight Multica issues back to backlog (optional)
#    Use multica_client.update_issue(issue_id, status='backlog') or Multica UI.

# 5. Verify no workers remain
pgrep -af 'kanban-worker' || echo 'no kanban workers'
```

After a controlled E2E completes, re-enable dispatch one issue at a time:

```bash
# Restore single-issue poll dispatch
ZOE_MULTICA_POLL_DISPATCH_LIMIT=1
systemctl --user restart zoe-data.service

hermes cron resume hourly-zoe-board-dispatch

python3 scripts/maintenance/sync_multica_to_kanban.py --limit 1
```

Monitor with `hermes kanban show t_<id> --json`, pipeline JSONL (`~/.zoe/engineering_pipeline_runs.jsonl`), and `engineering_harness_loop.py --mode report`. Pause again when done.
