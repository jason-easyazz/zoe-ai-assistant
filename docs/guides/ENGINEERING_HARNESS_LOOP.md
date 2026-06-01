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

## Critical pipeline patterns (non-zero exit)

Recorded in findings; **fail exit** when any of:

- `fingerprint_abort`
- `WORKTREE_NOT_READY` (or embedded in row)
- `block_reason` null while status is `blocked`
- Repeated `gate_blocked` (≥5) for same `task_ref` + phase in the tail window

Individual `gate_blocked` rows are counted in `gate_blocked_count` for triage but do not alone fail the run.

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
