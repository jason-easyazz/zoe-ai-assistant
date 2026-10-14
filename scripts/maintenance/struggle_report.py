#!/usr/bin/env python3
"""The struggles report for the operator (``struggle_digest.digest``): where Zoe visibly struggled, clustered.

    struggle_report.py [--days 7] [--top 20]            # everyone + guests: COUNTS and clusters only, no words
    struggle_report.py --user <member-id> [--days 7]    # that member's own struggles, with their example words
    struggle_report.py --guest                          # guest turns (categories only - guest rows hold no words)
    struggle_report.py --json                           # the raw report

Read-only (one SELECT on ``struggle_events``). Needs the service's ``POSTGRES_URL`` in the environment, e.g. run it with the
zoe-data env loaded. Example words are printed only with ``--user``, and only that member's own - the same rule as the
admin endpoint ``GET /api/system/struggles``. Exit 2 = the store could not be read.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "services" / "zoe-data"))


def _when(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%m-%d %H:%M")


def render(rep: dict) -> str:
    lines = [f"Struggles, last {rep['days']:g} day(s): {rep['total']} event(s)  (detector mode now: {rep['mode']})"]
    if rep["by_signal"]:
        lines.append("  by signal: " + ", ".join(f"{k} {v}" for k, v in rep["by_signal"].items()))
    for i, c in enumerate(rep["clusters"], 1):
        lines.append(f"{i:>2}. {c['signal']:<17} {c['domain']:<11} x{c['count']:<3} users={c['users']} "
                     f"conf={c['mean_confidence']:.2f}  {_when(c['first_seen'])} .. {_when(c['last_seen'])}")
        lines.append(f"      shape {c['pattern']}  paths {c['paths']}")
        lines.append(f"      fix: {c['suggested_fix']} - {c['fix_hint']}")
        for ex in c["examples"]:
            lines.append(f"      e.g. {ex}")
    return "\n".join(lines)


async def run(args) -> int:
    import struggle_digest as sd
    from db_pool import close_pool, init_pool

    try:
        await init_pool()
    except Exception as exc:  # noqa: BLE001
        print(f"cannot open the database ({type(exc).__name__})", file=sys.stderr)
        return 2
    try:
        user_ids = [args.user] if args.user else (["guest"] if args.guest else None)
        rep = await sd.digest(viewer_id=args.user, user_ids=user_ids, days=args.days, top=args.top)
    except Exception as exc:  # noqa: BLE001
        print(f"cannot read struggle_events ({type(exc).__name__}: {exc})", file=sys.stderr)
        return 2
    finally:
        await close_pool()
    print(json.dumps(rep, indent=2, sort_keys=True) if args.json else render(rep))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--days", type=float, default=7.0)
    ap.add_argument("--top", type=int, default=20)
    who = ap.add_mutually_exclusive_group()
    who.add_argument("--user", help="one member's own struggles, with their example words")
    who.add_argument("--guest", action="store_true", help="guest turns only (no words)")
    ap.add_argument("--json", action="store_true")
    return asyncio.run(run(ap.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
