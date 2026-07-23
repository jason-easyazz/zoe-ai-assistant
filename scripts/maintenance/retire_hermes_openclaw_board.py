#!/usr/bin/env python3
"""Retire the Hermes + OpenClaw identity rows from the Multica board.

Both runtimes have been offline since 2026-06-19 and their agents hold zero
activity_log / agent_task_queue rows, but they still show on the board and still
own live references. A naive DELETE is unsafe in two distinct ways, which is why
this is a reviewed script and not an ad-hoc statement:

  * ``autopilot.assignee_id -> agent`` is **ON DELETE CASCADE** — deleting the
    Hermes agent would SILENTLY destroy the live "Platform Health Check"
    autopilot (status=active, last run 2026-07-23).
  * ``squad.leader_id -> agent`` and ``agent.runtime_id -> agent_runtime`` are
    **ON DELETE RESTRICT** — squads led by these agents, and the surviving
    ``Auto Research Engineer`` agent pinned to the ``Hermes (Zoe)`` runtime,
    block the delete outright.

So the order below is load-bearing: re-home the things worth keeping, remove the
blockers, then delete. Everything runs in ONE transaction — a failure at any
step rolls the whole retirement back.

Multica is third-party: this touches DATA only, never its schema.

Usage:
    python3 scripts/maintenance/retire_hermes_openclaw_board.py            # dry-run
    python3 scripts/maintenance/retire_hermes_openclaw_board.py --execute  # apply
"""
from __future__ import annotations

import argparse
import asyncio
import datetime
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "services" / "zoe-data"))

RETIRED_AGENTS = ("Hermes", "OpenClaw")
RETIRED_RUNTIMES = ("Hermes (Zoe)", "Openclaw (Zoe)")
AUTOPILOT_TITLE = "Platform Health Check"
NEW_ASSIGNEE = "Flue Executor"
REHOME_AGENT = "Auto Research Engineer"
REHOME_RUNTIME = "Zoe Home Server (Jetson Orin NX)"
BACKUP_DIR = Path.home() / ".zoe-backups"


def _ensure_postgres_url() -> None:
    """Derive the DSN from the service .env so a hand-run needs no exported secret.

    Checked in order: this checkout's .env, then the live checkout's — a git
    worktree has no .env (it is gitignored), so a run from one still resolves.
    """
    if os.environ.get("POSTGRES_URL") or os.environ.get("MULTICA_DATABASE_URL"):
        return
    override = os.environ.get("ZOE_ENV_FILE", "").strip()
    candidates = ([Path(override)] if override else
                  [REPO_ROOT / "services" / "zoe-data" / ".env",
                   Path("/home/zoe/assistant/services/zoe-data/.env")])
    for env_file in candidates:
        try:
            for line in env_file.read_text().splitlines():
                if line.startswith("POSTGRES_URL="):
                    val = line.split("=", 1)[1].strip()
                    if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
                        val = val[1:-1]
                    os.environ["POSTGRES_URL"] = val
                    return
        except OSError:
            continue


def rewrite_health_check(description: str) -> str:
    """Drop the self-referential Hermes/OpenClaw clauses, keep the contract."""
    return (description
            .replace("(daily, Hermes)", f"(daily, {NEW_ASSIGNEE})")
            .replace("zoe-data API, Hermes and OpenClaw are healthy",
                     "zoe-data API and the Flue executor lane are healthy"))


async def snapshot(conn) -> Path:
    """Back up every row this script touches, so the retirement is reversible."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    snap = {"taken_at": datetime.datetime.now(datetime.timezone.utc).isoformat()}
    agents, runtimes = list(RETIRED_AGENTS), list(RETIRED_RUNTIMES)
    queries = (
        ("agent", "SELECT * FROM agent WHERE name = ANY($1::text[])", (agents,)),
        ("agent_runtime", "SELECT * FROM agent_runtime WHERE name = ANY($1::text[])", (runtimes,)),
        ("squad", "SELECT s.* FROM squad s JOIN agent a ON a.id = s.leader_id "
                  "WHERE a.name = ANY($1::text[])", (agents,)),
        ("agent_skill", "SELECT sk.* FROM agent_skill sk JOIN agent a ON a.id = sk.agent_id "
                        "WHERE a.name = ANY($1::text[])", (agents,)),
        ("autopilot", "SELECT * FROM autopilot", ()),
    )
    for table, q, args in queries:
        rows = await conn.fetch(q, *args)
        snap[table] = [{k: str(v) for k, v in dict(r).items()} for r in rows]
    path = BACKUP_DIR / "hermes-openclaw-retire.json"
    path.write_text(json.dumps(snap, indent=2))
    return path


async def run(execute: bool) -> int:
    from executors.executor_queue_backend import get_pool

    pool = await get_pool()
    try:
        return await _run_with_pool(pool, execute)
    finally:
        # close OUTSIDE the acquire block — closing while a connection is still
        # checked out deadlocks (pool.close waits for a release that can't happen)
        await pool.close()


async def _run_with_pool(pool, execute: bool) -> int:
    async with pool.acquire() as conn:
        backup = await snapshot(conn)
        print(f"backup written: {backup}")

        print("\n--- plan ---")
        ap = await conn.fetchrow(
            "SELECT title, description, status FROM autopilot WHERE title = $1", AUTOPILOT_TITLE)
        if ap:
            print(f"  REASSIGN autopilot {ap['title']!r} ({ap['status']}) -> {NEW_ASSIGNEE}, rewrite description")
        squads = await conn.fetch(
            "SELECT s.name, a.name AS leader FROM squad s JOIN agent a ON a.id = s.leader_id "
            "WHERE a.name = ANY($1::text[])", list(RETIRED_AGENTS))
        for s in squads:
            print(f"  DELETE   squad {s['name']!r} (leader {s['leader']})")
        pinned = await conn.fetch(
            "SELECT a.name FROM agent a JOIN agent_runtime ar ON ar.id = a.runtime_id "
            "WHERE ar.name = ANY($1::text[]) AND a.name <> ALL($2::text[])",
            list(RETIRED_RUNTIMES), list(RETIRED_AGENTS))
        for p in pinned:
            print(f"  REHOME   agent {p['name']!r} -> runtime {REHOME_RUNTIME!r}")
        for nm in RETIRED_AGENTS:
            print(f"  DELETE   agent {nm!r}")
        for nm in RETIRED_RUNTIMES:
            print(f"  DELETE   runtime {nm!r}")

        if not execute:
            print("\nDRY RUN — nothing changed. Re-run with --execute to apply.")
            return 0

        async with conn.transaction():
            if ap:
                new_desc = rewrite_health_check(ap["description"])
                if new_desc == ap["description"]:
                    raise RuntimeError("health-check rewrite matched nothing — refusing to proceed")
                assignee = await conn.fetchval("SELECT id FROM agent WHERE name = $1", NEW_ASSIGNEE)
                if assignee is None:
                    raise RuntimeError(f"{NEW_ASSIGNEE!r} agent not found — refusing to orphan the autopilot")
                await conn.execute(
                    "UPDATE autopilot SET assignee_id = $1, description = $2, updated_at = now() WHERE title = $3",
                    assignee, new_desc, AUTOPILOT_TITLE)

            await conn.execute(
                "DELETE FROM squad WHERE leader_id IN (SELECT id FROM agent WHERE name = ANY($1::text[]))",
                list(RETIRED_AGENTS))

            rehome = await conn.fetchval("SELECT id FROM agent_runtime WHERE name = $1", REHOME_RUNTIME)
            if rehome is None:
                raise RuntimeError(f"{REHOME_RUNTIME!r} runtime not found — cannot re-home surviving agents")
            await conn.execute(
                "UPDATE agent SET runtime_id = $1, updated_at = now() "
                " WHERE runtime_id IN (SELECT id FROM agent_runtime WHERE name = ANY($2::text[])) "
                "   AND name <> ALL($3::text[])",
                rehome, list(RETIRED_RUNTIMES), list(RETIRED_AGENTS))

            await conn.execute("DELETE FROM agent WHERE name = ANY($1::text[])", list(RETIRED_AGENTS))
            await conn.execute("DELETE FROM agent_runtime WHERE name = ANY($1::text[])", list(RETIRED_RUNTIMES))

        print("\n--- applied. verifying ---")
        left = [r["name"] for r in await conn.fetch("SELECT name FROM agent_runtime ORDER BY created_at")]
        print("  runtimes:", left)
        agents = [r["name"] for r in await conn.fetch("SELECT name FROM agent ORDER BY created_at")]
        print("  agents:", agents)
        chk = await conn.fetchrow(
            "SELECT ap.title, a.name AS assignee, ap.status FROM autopilot ap "
            "LEFT JOIN agent a ON a.id = ap.assignee_id WHERE ap.title = $1", AUTOPILOT_TITLE)
        if chk:
            print(f"  autopilot: {chk['title']} -> {chk['assignee']} ({chk['status']})")
        for nm in RETIRED_AGENTS + RETIRED_RUNTIMES:
            assert nm not in agents and nm not in left, f"{nm} survived the retirement"
        print("  OK — Hermes/OpenClaw rows gone, survivors intact.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--execute", action="store_true", help="apply (default is a dry run)")
    args = ap.parse_args()
    _ensure_postgres_url()
    return asyncio.run(run(args.execute))


if __name__ == "__main__":
    raise SystemExit(main())
