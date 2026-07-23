"""Multica board runner — single-lane autonomous processing of the board.

Picks ONE ready (`todo`) Multica issue at a time and runs it through the
Omnigent executor (implement -> PR -> gates -> hardened greploop close). Reports
progress back to Multica so `multica-web` and the review card show it:

    todo  --claim-->  in_progress  --+--merged-->     done
                                     +--PR, gated -->  in_review   (no-merge / CI pending)
                                     +--blocked   -->  blocked     (needs feedback)

THREE guards, all on:
  * KILL SWITCH  — idles while `~/.zoe/multica_dispatch_paused` exists.
  * SINGLE LANE  — never claims a new issue while one is `in_progress`
    (advisory lock + a NOT EXISTS guard), so the board can't fan out into
    concurrent Omnigent sessions (the usage guard).
  * FLAG         — the executor itself is `ZOE_USE_OMNIGENT_EXECUTOR`-gated.

Hand-run:  python3 services/zoe-data/multica_board_runner.py            # one issue
           python3 services/zoe-data/multica_board_runner.py --loop     # continuous
           python3 services/zoe-data/multica_board_runner.py --issue N  # a specific one
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import time
from pathlib import Path

import asyncpg

from executors.executor_queue_backend import ensure_executor_identity, get_pool, close_pool
from omnigent_issue_executor import execute_issue_dict, omnigent_executor_enabled

logger = logging.getLogger(__name__)

KILL_SWITCH = Path(os.environ.get("ZOE_MULTICA_KILL_SWITCH", str(Path.home() / ".zoe" / "multica_dispatch_paused")))


def kill_switch_present() -> bool:
    return KILL_SWITCH.exists()


def _ensure_postgres_url() -> None:
    """Hand-run robustness: as a systemd unit POSTGRES_URL is in the environment,
    but a manual invocation may not have it exported — derive it from the live
    service env file so the Multica pool can connect. No secret is committed."""
    if os.environ.get("POSTGRES_URL") or os.environ.get("MULTICA_DATABASE_URL"):
        return
    env_file = Path(os.environ.get("ZOE_ENV_FILE", "/home/zoe/assistant/services/zoe-data/.env"))
    try:
        for line in env_file.read_text().splitlines():
            if line.startswith("POSTGRES_URL="):
                val = line.split("=", 1)[1].strip()
                # A .env value may be wrapped in quotes — strip a matching pair
                # so the DSN isn't passed with literal surrounding quotes.
                if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
                    val = val[1:-1]
                os.environ["POSTGRES_URL"] = val
                return
    except OSError:
        pass


def build_issue_body(row: asyncpg.Record | dict) -> str:
    """Compose the task body the agent sees: description + acceptance criteria."""
    desc = str((row["description"] if row["description"] is not None else "") or "").strip()
    ac = row["acceptance_criteria"]
    if isinstance(ac, str):
        try:
            ac = json.loads(ac)
        except json.JSONDecodeError:
            ac = []
    parts = [desc]
    if ac:
        parts.append("\n\nAcceptance criteria:")
        parts.extend(f"- {item}" for item in ac)
    return "\n".join(p for p in parts if p).strip()


async def _log_issue_activity(
    conn: asyncpg.Connection, identity: dict, issue_id: str, action: str, reason: str,
    extra: dict | None = None,
) -> None:
    if not (reason or "").strip():
        raise ValueError("issue activity requires a reason")
    await conn.execute(
        """INSERT INTO activity_log (workspace_id, issue_id, actor_type, actor_id, action, details)
           VALUES ($1::uuid, $2::uuid, 'agent', $3::uuid, $4, $5::jsonb)""",
        identity["workspace_id"], issue_id, identity["agent_id"], action,
        json.dumps({"reason": reason, **(extra or {})}),
    )


async def claim_next_issue(
    conn: asyncpg.Connection, identity: dict, *, issue_number: int | None = None,
) -> asyncpg.Record | None:
    """Flip exactly one `todo` issue to `in_progress`, single-lane. Returns the
    claimed row, or None if the lane is busy / nothing ready."""
    async with conn.transaction():
        await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", "multica-board-runner")
        # Refuse to start new work while an issue is already in flight — this is
        # the SINGLE-LANE guard, and it holds for `--issue N` too (targeting a
        # specific item must not open a second concurrent lane). The one
        # exception is re-targeting the item that is itself already in_progress.
        busy = await conn.fetchval(
            """SELECT count(*) FROM issue
                WHERE workspace_id=$1::uuid AND status='in_progress'
                  AND ($2::int IS NULL OR number <> $2)""",
            identity["workspace_id"], issue_number,
        )
        if busy:
            return None
        if issue_number is not None:
            row = await conn.fetchrow(
                """UPDATE issue SET status='in_progress',
                       first_executed_at=coalesce(first_executed_at, now()), updated_at=now()
                   WHERE workspace_id=$1::uuid AND number=$2 AND status IN ('todo','backlog','blocked')
                   RETURNING id::text, number, title, description, acceptance_criteria""",
                identity["workspace_id"], issue_number,
            )
        else:
            row = await conn.fetchrow(
                """UPDATE issue SET status='in_progress',
                       first_executed_at=coalesce(first_executed_at, now()), updated_at=now()
                   WHERE id = (
                     SELECT id FROM issue
                      WHERE workspace_id=$1::uuid AND status='todo'
                      ORDER BY position, created_at
                      LIMIT 1 FOR UPDATE SKIP LOCKED)
                   RETURNING id::text, number, title, description, acceptance_criteria""",
                identity["workspace_id"],
            )
        if row is not None:
            await _log_issue_activity(
                conn, identity, row["id"], "issue_claimed",
                f"claimed board item #{row['number']} for autonomous implementation",
            )
        return row


async def report_result(conn: asyncpg.Connection, identity: dict, issue: asyncpg.Record, result) -> None:
    """Map the executor result onto Multica issue status + an activity entry."""
    if result.merged:
        status, action = "done", "issue_completed"
    elif result.ok and result.stage == "review":
        status, action = "in_review", "issue_in_review"
    else:
        status, action = "blocked", "issue_blocked"
    async with conn.transaction():
        await conn.execute(
            "UPDATE issue SET status=$2, updated_at=now() WHERE id=$1::uuid", issue["id"], status,
        )
        await _log_issue_activity(
            conn, identity, issue["id"], action,
            f"{result.stage}: {result.detail}",
            {"pr_url": result.pr_url, "merged": result.merged, "session_id": result.session_id,
             "merge_sha": result.merge_sha},
        )


async def run_one(*, issue_number: int | None = None) -> dict:
    if kill_switch_present():
        return {"status": "paused", "detail": "kill switch present"}
    if not omnigent_executor_enabled():
        return {"status": "disabled", "detail": "ZOE_USE_OMNIGENT_EXECUTOR is off"}
    pool = await get_pool()
    async with pool.acquire() as conn:
        identity = await ensure_executor_identity(conn)
        issue = await claim_next_issue(conn, identity, issue_number=issue_number)
    if issue is None:
        return {"status": "empty", "detail": "no ready issue (or lane busy)"}

    issue_dict = {"number": issue["number"], "title": issue["title"], "body": build_issue_body(issue)}
    logger.info("board runner: implementing #%s %r", issue["number"], issue["title"][:60])
    # The issue is now committed `in_progress`. Anything that raises before the
    # final status write would strand it there forever (the single-lane guard
    # would then wedge the whole board). Catch every failure and turn it into a
    # blocked result so report_result always writes a terminal status.
    try:
        # Blocking Omnigent run off the event loop so nothing else stalls.
        result = await asyncio.get_running_loop().run_in_executor(
            None, lambda: execute_issue_dict(issue_dict))
    except Exception as exc:  # noqa: BLE001
        from omnigent_issue_executor import OmnigentResult
        logger.exception("board runner: #%s raised", issue["number"])
        result = OmnigentResult(False, "error", f"runner exception: {exc}")

    try:
        async with pool.acquire() as conn:
            identity = await ensure_executor_identity(conn)
            await report_result(conn, identity, issue, result)
    except Exception:  # noqa: BLE001 - last resort: don't leave it in_progress
        logger.exception("board runner: could not write final status for #%s", issue["number"])
        try:
            async with pool.acquire() as conn:
                await conn.execute(
                    "UPDATE issue SET status='blocked', updated_at=now() WHERE id=$1::uuid", issue["id"])
        except Exception:  # noqa: BLE001
            logger.error("board runner: #%s left in_progress — Multica unreachable", issue["number"])
    return {
        "status": "done" if result.merged else ("in_review" if result.ok else "blocked"),
        "issue": issue["number"], "stage": result.stage, "pr": result.pr_url,
        "merged": result.merged, "detail": result.detail,
    }


async def _amain(loop_forever: bool, issue_number: int | None) -> int:
    try:
        while True:
            out = await run_one(issue_number=issue_number)
            print(json.dumps(out))
            if not loop_forever or out["status"] in ("paused", "disabled"):
                return 0
            if out["status"] == "empty":
                await asyncio.sleep(float(os.environ.get("ZOE_BOARD_POLL_S", "60")))
            issue_number = None  # only the first --issue is targeted
    finally:
        await close_pool()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--loop", action="store_true", help="keep processing, one issue at a time")
    ap.add_argument("--issue", type=int, default=None, help="target a specific board item number")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    _ensure_postgres_url()
    return asyncio.run(_amain(args.loop, args.issue))


if __name__ == "__main__":
    raise SystemExit(main())
