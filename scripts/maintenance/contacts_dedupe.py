#!/usr/bin/env python3
"""Report likely-duplicate contacts (a first-name-only record next to a fuller
record of the same first name and relation). REPORT ONLY - it never deletes,
merges or writes anything; a person reads the output and decides.

Why (2026-10-04): the lookup "Who is <first name>" answered with two rows - a
bare "<First>" and "<First> <Last>", both the same relation - because nothing
ever merged them. The service now collapses them on lookup and on save behind
``ZOE_CONTACTS_CONVERSATIONAL``; this one-off finds the ones already stored.

The matching rule is the live one (``services/zoe-data/contacts_conversation.py``
``duplicate_kind``): same first name, one side first-name-only, relations equal or
one side blank. Two different surnames ("Caitlin Farrell" / "Caitlin Hale") are
NOT duplicates.

Usage (read-only; ``--dry-run`` is required so the intent is explicit)
  python3 scripts/maintenance/contacts_dedupe.py --dry-run
  python3 scripts/maintenance/contacts_dedupe.py --dry-run --json-file rows.json
  python3 scripts/maintenance/contacts_dedupe.py --dry-run --psql-cmd "docker exec zoe-database psql -U zoe -d zoe"

Rows come from ``psql`` (default: the zoe-database container) as a single JSON
document, or from ``--json-file`` (a list of people rows) for offline use. The
output names household contacts: keep it on the box, do not paste it into PRs.

Exit codes: 0 = report printed (duplicates or not), 2 = refused (no --dry-run) or
the rows could not be read.
"""
from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "services" / "zoe-data"))
from contacts_conversation import find_duplicate_groups  # noqa: E402

DEFAULT_PSQL = "docker exec zoe-database psql -U zoe -d zoe"
SQL = (
    "select coalesce(json_agg(t), '[]'::json) from ("
    "select id, user_id, name, relationship, phone, email, birthday, created_at "
    "from people where deleted = 0 or deleted is null order by user_id, name) t"
)


def load_rows(psql_cmd: str, json_file: str | None) -> list[dict]:
    if json_file:
        return json.loads(Path(json_file).read_text())
    out = subprocess.run(
        [*shlex.split(psql_cmd), "-Atc", SQL],
        capture_output=True, text=True, timeout=60, check=True,
    ).stdout.strip()
    return json.loads(out or "[]")


def _richness(r: dict) -> tuple:
    """Which record to suggest keeping: the fuller name, then the most filled-in."""
    filled = sum(1 for k in ("relationship", "phone", "email", "birthday") if r.get(k))
    return (len(str(r.get("name", "")).split()), filled)


def render(groups: list[list[dict]]) -> str:
    if not groups:
        return "No likely-duplicate contacts found."
    lines = [f"{len(groups)} likely-duplicate group(s) - REPORT ONLY, nothing was changed.", ""]
    for n, g in enumerate(groups, 1):
        keep = max(g, key=_richness)
        lines.append(f"[{n}] user={str(g[0].get('user_id'))[:24]}")
        for r in sorted(g, key=_richness, reverse=True):
            tag = "  <- suggest keeping" if r is keep else ""
            has = ",".join(k for k in ("phone", "email", "birthday") if r.get(k)) or "-"
            lines.append(f"    {r.get('id')}  {r.get('name')!s:<28} rel={r.get('relationship') or '-':<12} "
                         f"has={has} created={str(r.get('created_at') or '-')[:10]}{tag}")
        lines.append("")
    lines.append("To merge two records, use the People panel merge (ZOE_PERSON_MERGE_ENABLED) "
                 "after checking they are the same person.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="required: this tool only reports")
    ap.add_argument("--json-file", help="read people rows from a JSON file instead of psql")
    ap.add_argument("--psql-cmd", default=DEFAULT_PSQL, help="psql invocation (default: zoe-database container)")
    args = ap.parse_args(argv)
    if not args.dry_run:
        print("refused: this tool only reports - pass --dry-run to confirm (it never writes)",
              file=sys.stderr)
        return 2
    try:
        rows = load_rows(args.psql_cmd, args.json_file)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"could not read people rows: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(render(find_duplicate_groups(rows)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
