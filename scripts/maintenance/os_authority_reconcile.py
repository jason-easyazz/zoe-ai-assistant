#!/usr/bin/env python3
"""Does the Zoe OS kernel's list store agree with production's list tables? (docs/knowledge/zoe-os-integration.md, stage c)

READ-ONLY. Prints CODES AND COUNTS ONLY: no item, no list name, no member, never the digest key. For the owner's flip decision, and
after it.

    python3 scripts/maintenance/os_authority_reconcile.py [--since-days 14] [--logs ~/.zoe-logs/zoe-data.*] [--json]

IDENTITY. A row is (member, list, item) with its duplicate count (two "milk" rows on one list are two). The kernel answers
``list_digest`` only to the ``reconciler`` credential (``reconcile-creds.json``, read at every run: its digest key is fresh at every
kernel start) with one KEYED digest per row (``os_speaker.keyed_digest``: member, list, item). Production's rows (``list_items``
joined to ``lists``, every row in any state: production's delete is a soft delete) are digested the same way, with the member and
the list name exactly as ``os_authority.propose_add`` sends them (the member id as ``on_behalf_of``, ``os_authority.kernel_list_name``
for the list). Production keeps no adder per row, so the member is the list's OWNER for a personal list, and any roster member for a
shared one (``visibility = family`` or the household's): a personal row can match only its owner's kernel row, a shared row any
member's of the same list name and item. One box is one household, so there is no household field. Compared:

* ``kernel_only``  - kernel rows with NO production row of the same identity left to match. THE drift that matters (a kernel-decided
  add production never got, or one whose member or list the two sides name differently). Must be 0; exit 1 otherwise.
* ``production_only_recent`` - production rows created in the window that no kernel row matched: the adds the kernel did not decide
  (fallbacks, guests, the REST and skybridge adds, Zoe's own offers) and rows the kernel erased. Expected; read it beside the
  fallback counts.
* ``rollback_pending`` - take-backs os_authority recorded as NOT verified (``authority-rollback-pending.json``, ids only).
* ``decisions`` - with ``--logs``: the ``OS_AUTHORITY`` lines by ``result:code`` (os_authority.py writes codes only).
* ``status`` - ``OK``, ``DRIFT``, or ``INCOMPLETE`` when any input could not be read (a log file, the roster, the rollback record):
  the counts then do not cover everything, and ``skipped`` names WHAT kind of input was missing (codes, never a path).

Exit 0 = OK, 1 = drift, 2 = cannot read the kernel or production, 3 = INCOMPLETE with no drift found.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from collections import Counter
from typing import Callable, Iterable, NamedTuple

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "services", "zoe-data"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

LINE = re.compile(r"OS_AUTHORITY verb=list_add result=(off|kernel|refused|fallback) code=(\S{1,40}) ms=")
ROLLBACK = re.compile(r"OS_AUTHORITY verb=list_add result=rollback code=(done|incomplete) n=\d+ left=\d+")
#: os_authority.py's closed set of codes; anything else is ``unknown`` (never echoed)
CODES = frozenset({"verified", "no_grant", "forgotten", "empty", "not_in_roster", "erasing", "circuit", "no_unit", "late", "timeout",
                   "unavailable", "socket", "protocol", "unhealthy", "write_failed", "unverified", "error", "refused_params",
                   "refused_manifest", "refused_cause", "refused_forgotten", "refused_other", "-"})
SHARED_VISIBILITY = frozenset({"family"})


class Row(NamedTuple):
    """One production row as the kernel would name it."""
    owner: str          # lists.user_id
    list_name: str      # os_authority.kernel_list_name(list_type, name)
    item: str
    shared: bool        # any roster member may have added it
    recent: bool


def compare(kernel: Iterable[str], rows: list, digest: Callable[[str, str, str], str], roster: Iterable[str]) -> dict:
    """``kernel``: the kernel's keyed digests (a multiset). ``rows``: production's ``Row``s. ``digest(member, list, item)``: the
    kernel's keyed digest. Each kernel row consumes at most one production row of its identity: personal rows first (only their
    owner's digest can use them), then shared rows (any roster member's; rows of one list name and item are interchangeable, so the
    greedy match is the best one)."""
    kernel = list(kernel)
    want = Counter(kernel)
    members = sorted(set(roster))
    used = [False] * len(rows)
    for i, r in enumerate(rows):
        if not r.shared:
            d = digest(r.owner, r.list_name, r.item)
            if want[d] > 0:
                want[d] -= 1
                used[i] = True
    for i, r in enumerate(rows):
        if r.shared:
            for m in members:
                d = digest(m, r.list_name, r.item)
                if want[d] > 0:
                    want[d] -= 1
                    used[i] = True
                    break
    left = sum(n for n in want.values() if n > 0)
    return {"kernel_rows": len(kernel), "production_rows": len(rows), "kernel_only": left, "matched": len(kernel) - left,
            "production_only_recent": sum(1 for r, u in zip(rows, used) if r.recent and not u)}


def decisions(lines) -> dict:
    """``result:code`` -> count over the OS_AUTHORITY lines; anything else on a line is never read."""
    out: Counter = Counter()
    for raw in lines:
        m = LINE.search(raw)
        if m:
            out[f"{m.group(1)}:{m.group(2) if m.group(2) in CODES else 'unknown'}"] += 1
            continue
        m = ROLLBACK.search(raw)
        if m:
            out[f"rollback:{m.group(1)}"] += 1
    return dict(sorted(out.items()))


def read_logs(paths: Iterable[str]) -> tuple[list, int]:
    """Every line of every readable log, and how many named inputs could NOT be read (missing, a directory, no permission). A
    skipped input is never silent: it makes the run INCOMPLETE."""
    lines, skipped = [], 0
    for path in paths:
        try:
            with open(path, errors="replace") as fh:
                lines.extend(fh)
        except OSError:
            skipped += 1
    return lines, skipped


def production_rows(records: Iterable, kernel_list_name: Callable, household_owner: str) -> list:
    """``records``: mappings with text, recent, owner, name, list_type, visibility (``SQL``)."""
    out = []
    for r in records:
        owner = str(r["owner"] or "")
        shared = str(r["visibility"] or "") in SHARED_VISIBILITY or owner == household_owner
        out.append(Row(owner, kernel_list_name(r["list_type"], r["name"]), str(r["text"] or ""), shared, bool(r["recent"])))
    return out


SQL = ("SELECT li.text, (li.created_at::timestamptz > now() - make_interval(days => $1)) AS recent, l.user_id AS owner, l.name,"
       " l.list_type, l.visibility FROM list_items li JOIN lists l ON l.id = li.list_id")


async def _production(since_days: int) -> list:
    import asyncpg
    from purge_orphaned_test_data import _resolve_dsn

    dsn = _resolve_dsn()
    if not dsn:
        raise RuntimeError("POSTGRES_URL not found")
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
        return list(await conn.fetch(SQL, since_days))
    finally:
        await conn.close()


def summarise(out: dict, skipped: list) -> dict:
    out["skipped"] = sorted(skipped)
    out["status"] = "INCOMPLETE" if skipped else ("DRIFT" if out["kernel_only"] else "OK")
    return out


def exit_code(out: dict) -> int:
    return 1 if out["kernel_only"] else (3 if out["skipped"] else 0)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--since-days", type=int, default=14)
    ap.add_argument("--logs", nargs="*", default=[])
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    import os_authority
    import os_shadow
    import panel_principal

    try:
        kernel = os_authority.kernel_digests()          # as the reconciler; the key stays inside this object
    except Exception as exc:  # noqa: BLE001
        print(f"kernel: unreadable ({type(exc).__name__})", file=sys.stderr)
        return 2
    try:
        records = asyncio.run(_production(a.since_days))
    except Exception as exc:  # noqa: BLE001
        print(f"production: unreadable ({type(exc).__name__})", file=sys.stderr)
        return 2
    skipped = []
    roster = os_shadow.roster()
    if not roster:
        skipped.append("roster")                         # shared rows cannot be matched to any member
    rows = production_rows(records, os_authority.kernel_list_name, panel_principal.HOUSEHOLD_OWNER_ID)
    out = compare(kernel.digests, rows, kernel.digest, roster)
    try:
        out["rollback_pending"] = sum(int(r.get("n", 0)) for r in os_authority.pending_rollbacks())
    except (OSError, ValueError, TypeError, AttributeError):
        out["rollback_pending"] = None
        skipped.append("rollback_record")
    lines, bad_logs = read_logs(a.logs)
    if bad_logs:
        skipped.append("log")
    out["logs_skipped"] = bad_logs
    out["decisions"] = decisions(lines)
    summarise(out, skipped)
    print(json.dumps(out, sort_keys=True) if a.json else "\n".join(f"{k}={v}" for k, v in sorted(out.items())))
    return exit_code(out)


if __name__ == "__main__":
    raise SystemExit(main())
