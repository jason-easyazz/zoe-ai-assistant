#!/usr/bin/env python3
"""Review the week's FAILED turns and confirm the route each should have taken (failure capture, ``turn_outcomes``).

Approve-before-train: a failed turn becomes a router training example ONLY after its own member confirms the label here (or through
``POST /api/system/struggles/turns/{id}/confirm``). Same rules as the admin API: one member, that member's own turns and words.

    zoe-struggle-labels.py --user <member-id> [--days 7]              # the member's failed turns, newest first, with the proposed route
    zoe-struggle-labels.py --user <member-id> --confirm ID [--label d] # confirm (or give the right route: a fast domain, or chat)
    zoe-struggle-labels.py --user <member-id> --reject ID              # not a routing failure: never learned from
    zoe-struggle-labels.py --user <member-id> --json

The listing PRINTS THE MEMBER'S OWN WORDS: run it for yourself, and never paste its output into the repository, a PR or a comment.
Needs the service's ``POSTGRES_URL`` in the environment (run it with the zoe-data env loaded). Exit 2 = the store could not be read,
3 = the decision was refused (the reason is printed).
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


def render(rep: dict) -> str:
    lines = [f"Failed turns, last {rep['days']:g} day(s): {len(rep['items'])} ({rep['pending']} waiting for your OK)"
             + ("" if rep.get("enabled") else "  [ZOE_STRUGGLE_OUTCOMES is off: nothing new is captured]")]
    for it in rep["items"]:
        when = datetime.fromtimestamp(it["ts"]).strftime("%m-%d %H:%M")
        prop = it["proposed_label"] or "?"
        lines.append(f"{it['id'][:12]}  {when}  {it['channel']:<5} {it['outcome']:<12} signals={it['signals'] or '-'}")
        lines.append(f"      routed {it['route_domain'] or '-'} ({it['route_source'] or '-'}) ran {it['handler'] or it['path_served']}"
                     f"{(' tools=' + it['tools']) if it['tools'] else ''}")
        lines.append(f"      proposed: {prop} ({it['label_source'] or '-'})  status: {it['label_status']}"
                     f"{(' -> ' + it['label']) if it['label'] else ''}")
        lines.append(f"      words: {it.get('words') or '(none kept)'}")
    return "\n".join(lines)


async def run(args) -> int:
    import turn_outcomes as to
    from db_pool import close_pool, init_pool

    try:
        await init_pool()
    except Exception as exc:  # noqa: BLE001
        print(f"cannot open the database ({type(exc).__name__})", file=sys.stderr)
        return 2
    try:
        if args.confirm or args.reject:
            row_id = await _full_id(to, args.user, args.confirm or args.reject)
            try:
                out = await to.decide(row_id, args.user, "confirm" if args.confirm else "reject", args.label)
            except to.ReviewError as exc:
                print(f"refused: {exc}", file=sys.stderr)
                return 3
            print(json.dumps(out, indent=2, sort_keys=True) if args.json else f"{out['label_status']}: {out['id']} {out['label'] or ''}")
            return 0
        rep = await to.review(args.user, days=args.days)
    except Exception as exc:  # noqa: BLE001
        print(f"cannot read turn_outcomes ({type(exc).__name__})", file=sys.stderr)
        return 2
    finally:
        await close_pool()
    print(json.dumps(rep, indent=2, sort_keys=True) if args.json else render(rep))
    return 0


async def _full_id(to, user_id: str, prefix: str) -> str:
    """The listing prints 12-character ids; accept one when it names exactly one of the member's failed turns."""
    rep = await to.review(user_id, days=90.0, limit=500)
    hits = [it["id"] for it in rep["items"] if it["id"].startswith(prefix)]
    return hits[0] if len(hits) == 1 else prefix


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--user", required=True, help="the member whose own failed turns to review")
    ap.add_argument("--days", type=float, default=7.0)
    act = ap.add_mutually_exclusive_group()
    act.add_argument("--confirm", metavar="ID")
    act.add_argument("--reject", metavar="ID")
    ap.add_argument("--label", help="with --confirm: the route it should have taken (a fast domain, or chat)")
    ap.add_argument("--json", action="store_true")
    return asyncio.run(run(ap.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
