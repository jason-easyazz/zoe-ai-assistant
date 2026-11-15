#!/usr/bin/env python3
"""Does the Zoe OS kernel's list store agree with production's list tables? (docs/knowledge/zoe-os-integration.md, stage c)

READ-ONLY. Prints CODES AND COUNTS ONLY: no item, no list name, no member. For the owner's flip decision, and after it.

    python3 scripts/maintenance/os_authority_reconcile.py [--since-days 14] [--logs ~/.zoe-logs/zoe-data.*] [--json]

The kernel answers with a digest per list row (``list_digest``, the panel credential, over its socket); production's rows are read
from ``list_items`` (every row, deleted or done included: production's delete is a soft delete, so a row the kernel decided is
always there unless the production write itself failed). Compared:

* ``kernel_only``  - kernel rows with NO production row of the same text, in any state. THE drift that matters: the kernel decided an
  add the household's list never got (a production write that failed after the receipt). Must be 0; exit 1 otherwise.
* ``production_only_recent`` - production rows created in the window with no kernel row: the adds the kernel did not decide (counted
  fallbacks, guests, the REST and skybridge adds, Zoe's own offers) and rows the kernel erased (a member delete, a forget). Expected;
  read it next to the fallback counts.
* ``decisions`` - with ``--logs``: the ``OS_AUTHORITY`` lines by ``result:code`` (os_authority.py writes codes only).

Exit 0 = no kernel-only drift, 1 = drift, 2 = cannot read one side.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "services", "zoe-data"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

LINE = re.compile(r"OS_AUTHORITY verb=list_add result=(off|kernel|refused|fallback) code=(\S{1,40}) ms=")
#: os_authority.py's closed set of codes; anything else is ``unknown`` (never echoed)
CODES = frozenset({"verified", "no_grant", "empty", "not_in_roster", "erasing", "circuit", "timeout", "unavailable", "socket", "protocol",
                   "unhealthy", "write_failed", "unverified", "error", "refused_params", "refused_manifest", "refused_cause",
                   "refused_forgotten", "refused_other", "-"})


def compare(kernel: list, production_all: list, production_recent: list) -> dict:
    """``kernel``: digests. ``production_all`` / ``production_recent``: digests of every row / of the rows created in the window."""
    have = set(production_all)
    kset = Counter(kernel)
    return {"kernel_rows": len(kernel), "production_rows": len(production_all),
            "kernel_only": sum(n for d, n in kset.items() if d not in have),
            "production_only_recent": sum(1 for d in production_recent if d not in kset),
            "matched": sum(n for d, n in kset.items() if d in have)}


def decisions(lines) -> dict:
    """``result:code`` -> count over the OS_AUTHORITY lines; anything else on a line is never read."""
    out: Counter = Counter()
    for raw in lines:
        m = LINE.search(raw)
        if m:
            out[f"{m.group(1)}:{m.group(2) if m.group(2) in CODES else 'unknown'}"] += 1
    return dict(sorted(out.items()))


async def _production(since_days: int) -> tuple[list, list]:
    import asyncpg
    from purge_orphaned_test_data import _resolve_dsn

    import os_authority

    dsn = _resolve_dsn()
    if not dsn:
        raise RuntimeError("POSTGRES_URL not found")
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
        rows = await conn.fetch("SELECT text, (created_at::timestamptz > now() - make_interval(days => $1)) AS recent FROM list_items",
                                since_days)
    finally:
        await conn.close()
    every = [os_authority.digest(str(r["text"] or "")) for r in rows]
    return every, [d for d, r in zip(every, rows) if r["recent"]]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--since-days", type=int, default=14)
    ap.add_argument("--logs", nargs="*", default=[])
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        import os_authority

        kernel = os_authority.kernel_digests()
    except Exception as exc:  # noqa: BLE001
        print(f"kernel: unreadable ({type(exc).__name__})", file=sys.stderr)
        return 2
    try:
        every, recent = asyncio.run(_production(a.since_days))
    except Exception as exc:  # noqa: BLE001
        print(f"production: unreadable ({type(exc).__name__})", file=sys.stderr)
        return 2
    out = compare(kernel, every, recent)
    lines = []
    for path in a.logs:
        try:
            with open(path, errors="replace") as fh:
                lines.extend(fh)
        except OSError:
            continue
    out["decisions"] = decisions(lines)
    print(json.dumps(out, sort_keys=True) if a.json else "\n".join(f"{k}={v}" for k, v in sorted(out.items())))
    return 1 if out["kernel_only"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
