#!/usr/bin/env python3
"""Retire ONE night of the night mind: every observation, thread and run row a night wrote, by its run id (the night date).

    night_mind_retire_run.py --night-date 2026-10-10                 # DRY RUN: counts only, changes nothing
    night_mind_retire_run.py --night-date 2026-10-10 --apply         # do it
    night_mind_retire_run.py --night-date 2026-10-10 --user ID ...   # one member only

What "the run id" is: the pass stamps every observation it writes with ``run_id = night_date`` (the Zoe-local date of the run; ``night_mind._run``), opens a thread with
``opened_run = night_date`` and writes one ``night_runs`` row per member-night (``id = nr-<hash>``, ``night_date``). The 12B window's first real night is the first writer of these
tables, so retiring its date undoes the night completely. Rows are RETRACTED, not deleted: ``state = 'retracted'`` (observations: the readers serve ``current`` only, and a
thread with no current observation is not served), ``status = 'retracted'`` (threads, runs). Nothing is removed, so it can be undone by hand and the audit trail stays.
Limit, stated plainly: a LATER night's pass can also rewrite an EARLIER night's rows (a change moment turns older observations into ``history``; a thread resolves). Retiring
night N does not undo what night N+1 did to night N's rows, and does not undo what night N did to the rows of nights before it. For the first night there is nothing before.

Output is COUNTS ONLY (never a quote, a title or an id). Exit 0 ok, 1 error, 2 refused (bad date).
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import re
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "services" / "zoe-data"))

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


async def _scalar(db, sql: str, params: tuple) -> int:
    cur = await db.execute(sql, params)
    row = await cur.fetchone()
    return int(row[0] or 0) if row else 0


def _who(user_id: str) -> "tuple[str, tuple]":
    return (" AND user_id = ?", (user_id,)) if user_id else ("", ())


async def retire(db, night_date: str, *, user_id: str = "", apply: bool = False, now: "float | None" = None) -> "dict[str, int | bool | str]":
    """Count (and with ``apply`` retract) the rows of ``night_date``. ``db`` is anything with ``await db.execute(sql, params)`` (the pool's connection, or a test shim);
    portable ``?`` SQL, exactly the dialect ``night_store`` uses. Idempotent: rows already retracted are not counted again."""
    if not _DATE.match(night_date or ""):
        raise ValueError(f"night date must be YYYY-MM-DD, got {night_date!r}")
    dt.date.fromisoformat(night_date)
    who, wp = _who(user_id)
    obs_sql = f"FROM night_observations WHERE run_id = ? AND state <> 'retracted'{who}"
    thr_sql = f"FROM night_threads WHERE opened_run = ? AND status <> 'retracted'{who}"
    run_sql = f"FROM night_runs WHERE night_date = ? AND status <> 'retracted'{who}"
    out: "dict[str, int | bool | str]" = {
        "night_date": night_date, "applied": bool(apply),
        "observations": await _scalar(db, "SELECT COUNT(*) " + obs_sql, (night_date, *wp)),
        "threads": await _scalar(db, "SELECT COUNT(*) " + thr_sql, (night_date, *wp)),
        "runs": await _scalar(db, "SELECT COUNT(*) " + run_sql, (night_date, *wp)),
    }
    if apply:
        ts = float(now if now is not None else time.time())
        await db.execute(f"UPDATE night_observations SET state = 'retracted', valid_to = COALESCE(valid_to, ?) WHERE run_id = ? AND state <> 'retracted'{who}", (ts, night_date, *wp))
        await db.execute(f"UPDATE night_threads SET status = 'retracted', raise_policy = 'leave', leave_reason = 'retracted' WHERE opened_run = ? AND status <> 'retracted'{who}", (night_date, *wp))
        await db.execute(f"UPDATE night_runs SET status = 'retracted' WHERE night_date = ? AND status <> 'retracted'{who}", (night_date, *wp))
        commit = getattr(db, "commit", None)
        if callable(commit):
            await commit()
    return out


async def amain(args: argparse.Namespace) -> int:
    try:
        from db_pool import close_pool, get_db_ctx, init_pool
    except Exception as exc:  # noqa: BLE001
        print(f"cannot import the database pool: {type(exc).__name__}", file=sys.stderr)
        return 1
    try:
        await init_pool()
        async with get_db_ctx() as db:
            res = await retire(db, args.night_date, user_id=args.user, apply=args.apply)
    except ValueError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        print(f"failed: {type(exc).__name__}", file=sys.stderr)
        return 1
    finally:
        try:
            await close_pool()
        except Exception:  # noqa: BLE001
            pass
    print(json.dumps(res, sort_keys=True) + ("" if args.apply else "   (dry run: nothing changed; add --apply)"))
    return 0


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--night-date", required=True, help="the Zoe-local date of the night to retire, YYYY-MM-DD (the report's file name: ~/.zoe/night-reports/<date>.md)")
    ap.add_argument("--user", default="", help="one member only (default: everyone that night wrote)")
    ap.add_argument("--apply", action="store_true", help="retract the rows (default: count only)")
    return asyncio.run(amain(ap.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
