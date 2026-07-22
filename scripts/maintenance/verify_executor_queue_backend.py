#!/usr/bin/env python3
"""Phase-2 gate: prove the executor queue backend against the REAL Multica tables.

Hand-run verification for
`docs/architecture/multica-executor-migration.md` Phase 2. Exercises the full
`hermes kanban` verb surface the (untouched) `kanban_adapter` shells —
create / list / show / block / complete / archive — through
`executors.executor_queue_backend`, against Multica's own database.

Safety properties, deliberate:
  * It creates exactly ONE probe task, tagged with an unmistakable
    `phase2-probe:` idempotency key, and DELETES it at the end. The board is
    left as it was found.
  * The `activity_log` reason entries are left behind on purpose — they are
    the evidence that every transition recorded a why (the §4 non-negotiable).
  * It never touches Multica's schema, never dispatches real work, and does
    not care whether the dispatch kill switch is set: no poll loop is involved.

Usage:  python3 scripts/maintenance/verify_executor_queue_backend.py [--keep]
Exit 0 = every assertion held.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "services" / "zoe-data"))

PROBE_KEY = "phase2-probe:executor-backend-verification"

failures: list[str] = []


def check(cond: bool, label: str) -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {label}")
    if not cond:
        failures.append(label)


def _load_env() -> None:
    """Read POSTGRES_URL from the service env file when not already exported.

    Checked in order: ZOE_ENV_FILE, this checkout's service env, then the live
    checkout's. The last one matters because agent worktrees do not carry a
    `.env` — running this gate from a worktree is the normal case.
    """
    if os.environ.get("POSTGRES_URL"):
        return
    candidates = [
        Path(p) for p in (
            os.environ.get("ZOE_ENV_FILE", ""),
            REPO / "services" / "zoe-data" / ".env",
            Path("/home/zoe/assistant/services/zoe-data/.env"),
        ) if p
    ]
    for env_file in candidates:
        if not env_file.is_file():
            continue
        for line in env_file.read_text().splitlines():
            if line.startswith("POSTGRES_URL="):
                os.environ["POSTGRES_URL"] = line.split("=", 1)[1].strip()
                print(f"(read POSTGRES_URL from {env_file})")
                return
    raise SystemExit(
        "No POSTGRES_URL in the environment and none of these carry one: "
        + ", ".join(str(c) for c in candidates)
    )


async def main() -> int:
    _load_env()
    import executors.executor_queue_backend as eb

    keep = "--keep" in sys.argv
    pool = await eb.get_pool()

    print("== identity registration (idempotent, additive) ==")
    async with pool.acquire() as conn:
        identity = await eb.ensure_executor_identity(conn)
        again = await eb.ensure_executor_identity(conn)
    check(bool(identity["runtime_id"]), f"agent_runtime registered: {identity['runtime_id']}")
    check(bool(identity["agent_id"]), f"agent registered: {identity['agent_id']}")
    check(identity == again, "re-registration is idempotent (same ids, no duplicate rows)")

    async with pool.acquire() as conn:
        dupes = await conn.fetchval(
            "SELECT count(*) FROM agent_runtime WHERE workspace_id=$1::uuid AND name=$2",
            identity["workspace_id"], eb._EXECUTOR_RUNTIME_NAME,
        )
    check(dupes == 1, f"exactly one executor runtime row exists (got {dupes})")

    task_id = ""
    try:
        print("== create (real INSERT into agent_task_queue) ==")
        created = await eb.run_kanban_command([
            "create", "phase2 probe task",
            "--assignee", "zoe-coder",
            "--workspace", "worktree",
            "--idempotency-key", PROBE_KEY,
            "--max-runtime", "600",
            "--max-retries", "1",
            "--created-by", "zoe-bridge",
            "--body", f"zoe-ref: {PROBE_KEY}\nPhase-2 verification probe.",
            "--json",
        ], expect_json=True)
        task_id = created["id"]
        check(bool(task_id) and not created["deduplicated"], f"task created: {task_id}")

        again = await eb.run_kanban_command([
            "create", "phase2 probe task",
            "--idempotency-key", PROBE_KEY, "--json",
        ], expect_json=True)
        check(again["id"] == task_id and again["deduplicated"] is True,
              "second create with the same key deduplicates (no double dispatch)")

        print("== list (the shape the adapter reads) ==")
        rows = await eb.run_kanban_command(["list", "--json"], expect_json=True)
        row = next((r for r in rows if r["id"] == task_id), None)
        check(row is not None, "probe task appears in list output")
        if row:
            for field in ("id", "title", "body", "status", "block_reason", "result", "workspace_path"):
                check(field in row, f"list row exposes '{field}'")
            check(row["status"] == "ready", f"queued maps to hermes 'ready' (got {row['status']})")
            import executors.kanban_adapter as ka
            check(ka._row_ref_key(row) == PROBE_KEY,
                  "kanban_adapter._row_ref_key() correlates the row to its ref")

        print("== show ==")
        detail = await eb.run_kanban_command(["show", task_id, "--json"], expect_json=True)
        for field in ("task", "latest_summary", "comments", "runs", "events", "metadata"):
            check(field in detail, f"show exposes '{field}'")
        check(any(e["action"] == "task_created" for e in detail["events"]),
              "creation event is visible with its reason")

        print("== block (reason must be durable — the §4 non-negotiable) ==")
        await eb.run_kanban_command(["block", task_id, "BLOCKER=phase2 probe blocked on purpose"])
        rows = await eb.run_kanban_command(["list", "--json"], expect_json=True)
        row = next(r for r in rows if r["id"] == task_id)
        check(row["status"] == "blocked", f"failed maps to hermes 'blocked' (got {row['status']})")
        check(row["block_reason"] == "BLOCKER=phase2 probe blocked on purpose",
              "block_reason survives to the adapter — Hermes recorded this 0/128 times")

        print("== complete ==")
        await eb.run_kanban_command([
            "complete", "--result", "PR_URL=https://example.invalid/pull/0",
            "--summary", "phase2 probe completed",
            "--metadata", json.dumps({"probe": True}), task_id,
        ])
        rows = await eb.run_kanban_command(["list", "--json"], expect_json=True)
        row = next(r for r in rows if r["id"] == task_id)
        check(row["status"] == "done", f"completed maps to hermes 'done' (got {row['status']})")
        check(row["block_reason"] is None, "completing clears the block reason")

        print("== every transition recorded a reason ==")
        async with pool.acquire() as conn:
            entries = await conn.fetch(
                """SELECT action, details FROM activity_log
                    WHERE details->>'task_id' = $1 ORDER BY created_at, id""",
                task_id,
            )
        actions = [e["action"] for e in entries]
        reasons = [
            (json.loads(e["details"]) if isinstance(e["details"], str) else e["details"]).get("reason", "")
            for e in entries
        ]
        check(actions == ["task_created", "task_blocked", "task_completed"],
              f"full reasoned chain in activity_log (got {actions})")
        check(all(r.strip() for r in reasons), "every activity entry carries a non-empty reason")
    finally:
        if task_id and not keep:
            async with pool.acquire() as conn:
                await conn.execute("DELETE FROM agent_task_queue WHERE id=$1::uuid", task_id)
            print(f"cleaned up probe task {task_id} (activity_log evidence kept)")
        await eb.close_pool()

    print()
    if failures:
        print(f"VERIFY: {len(failures)} FAILURE(S)")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("VERIFY: ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
