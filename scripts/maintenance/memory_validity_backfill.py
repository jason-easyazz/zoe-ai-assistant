#!/usr/bin/env python3
"""Report what a validity backfill WOULD write to memory rows written before the two timelines (DRY-RUN ONLY).

Why (docs/knowledge/memory-authority.md, "Two timelines"): every NEW row now carries ``valid_from`` (the event time the
person stated, else the capture time) and every retired row ``invalid_at`` + ``expired_at``. Rows written before that have
``valid_from`` only if the implicit-supersede flag was on when they were written (12 of 82 approved owner rows on
2026-10-05) and ``invalid_at`` on 3 of 17 retired ones. The runtime copes (``memory_temporal.row_start`` falls back to
``added_ts``; an as-of read bounds an unstamped superseded row by its successor) so NOTHING here is required; this tool
shows the counts so an operator can decide whether to persist them.

The plan is ``memory_temporal.backfill_plan`` (one pure function, tested): metadata only, text and embeddings untouched,
nothing deleted, idempotent.

  1. a row without ``valid_from`` gets its learned time (``valid_from_basis`` = ``backfill``);
  2. a superseded row without ``invalid_at`` ends where its successor began;
  3. an archived row without ``invalid_at`` ends when it was archived (``reviewed_at``);
  ``restated`` counts the user-class rows whose own source excerpt states an earlier event time ("since 2018"): a
  separate REVIEWED step, never part of the automatic plan.

Usage (read-only; safe while zoe-data runs, the palace sqlite is opened mode=ro; counts only, NO row text)
  python3 scripts/maintenance/memory_validity_backfill.py --dry-run [--user member-a] [--json]

There is deliberately NO --apply: persisting is an operator step to be added and reviewed on its own (it would write the
palace in-process, which needs zoe-data stopped - see memory_authority_backfill.py for the shape). Exit 0 = report
printed, 2 = refused.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2].joinpath("services", "zoe-data")))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import memory_temporal as mt  # noqa: E402
from memory_authority_backfill import DEFAULT_PALACE, load_rows  # noqa: E402


def report(rows: list[dict[str, Any]], only_user: Optional[str] = None) -> dict[str, Any]:
    """Counts only: the backfill plan's counters plus the status mix. Never row text."""
    chosen = [r for r in rows if not only_user or r["user_id"] == only_user]
    plan = mt.backfill_plan([(r["id"], r["text"], r["meta"]) for r in chosen])
    by_status: dict[str, int] = {}
    for r in chosen:
        status = str(r["meta"].get("status") or "-")
        by_status[status] = by_status.get(status, 0) + 1
    return dict(plan["counts"], by_status=dict(sorted(by_status.items())), would_update=len(plan["updates"]))


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="required: this tool has no other mode")
    ap.add_argument("--user", help="limit to one user_id")
    ap.add_argument("--palace", default=DEFAULT_PALACE, help="the palace directory (default: MEMPALACE_DATA_DIR)")
    ap.add_argument("--json", action="store_true", help="machine-readable output (counts only)")
    args = ap.parse_args(argv)
    if not args.dry_run:
        print("memory validity backfill: --dry-run is required (there is no --apply)", file=sys.stderr)
        return 2
    out = report(load_rows(args.palace), args.user)
    if args.json:
        print(json.dumps(out, sort_keys=True))
    else:
        for key, value in out.items():
            print(f"{key:<24} {value}")
        print("No row text is printed. Nothing was written (--dry-run only).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
