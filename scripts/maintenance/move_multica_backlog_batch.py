#!/usr/bin/env python3
"""Move Hermes-assigned Multica backlog issues to todo in small batches.

Phased workstreams (e.g. ``card-upgrade: Phase 1`` … ``Phase 8``, or
``Skybridge P1`` … ``P8``) are dispatched **one phase at a time**. This script
only promotes the lowest phase whose predecessors are ``done`` in Multica, and
at most **one issue per sequence key** per run so a batch cannot parallelize
Phase 2+ while Phase 1 is still open.

Non-phased backlog issues are still eligible; they sort after phased picks using
the usual identifier ordering within the remaining batch slots.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "zoe-data"))

# Title patterns for ordered multi-phase engineering tracks.
_CARD_UPGRADE_RE = re.compile(r"(?i)^(card-upgrade)\s*:\s*phase\s*(\d+)")
_SKYBRIDGE_RE = re.compile(r"(?i)^skybridge\s+p(\d+)")
_GENERIC_PHASE_RE = re.compile(r"(?i)^([\w][\w-]*)\s*:\s*phase\s*(\d+)")

def _load_dotenv() -> None:
    for path in (
        ROOT / "services" / "zoe-data" / ".env",
        ROOT / ".env",
        Path.home() / ".hermes" / ".env",
    ):
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


def parse_phased_title(title: str) -> tuple[str, int] | None:
    """Return (sequence_key, phase_number) when title encodes a phased track."""
    title = (title or "").strip()
    m = _CARD_UPGRADE_RE.match(title)
    if m:
        return m.group(1).lower(), int(m.group(2))
    m = _SKYBRIDGE_RE.match(title)
    if m:
        return "skybridge", int(m.group(1))
    m = _GENERIC_PHASE_RE.match(title)
    if m:
        return m.group(1).lower(), int(m.group(2))
    return None


def _issue_sort_key(issue: dict) -> tuple:
    ident = str(issue.get("identifier") or issue.get("number") or issue.get("id") or "")
    return (ident, str(issue.get("id") or ""))


def _predecessors_done(sequence: str, phase: int, by_sequence: dict[str, list[dict]]) -> bool:
    """True when every lower phase in the same sequence is ``done`` (or missing)."""
    siblings = by_sequence.get(sequence) or []
    for other in siblings:
        other_parsed = parse_phased_title(other.get("title") or "")
        if not other_parsed:
            continue
        other_seq, other_phase = other_parsed
        if other_seq != sequence or other_phase >= phase:
            continue
        # Predecessor must be explicitly done; cancelled/backlog/todo does not unblock.
        if other.get("status") != "done":
            return False
    return True


def select_batch(
    backlog: list[dict],
    all_issues: list[dict],
    count: int,
) -> tuple[list[dict], list[str]]:
    """Pick up to ``count`` backlog issues respecting phased ordering.

    ``all_issues`` is the full Multica board (any assignee) so predecessor phases
    are checked even when an earlier phase was completed by a non-Hermes assignee.

    Returns (batch, skip_reasons) where skip_reasons explains phased issues held back.
    """
    by_sequence: dict[str, list[dict]] = defaultdict(list)
    for issue in all_issues:
        parsed = parse_phased_title(issue.get("title") or "")
        if parsed:
            by_sequence[parsed[0]].append(issue)

    phased_picks: list[dict] = []
    skip_reasons: list[str] = []
    sequences_seen: set[str] = set()

    # Lowest backlog phase per sequence first (stable identifier order within phase).
    phased_backlog: list[tuple[str, int, dict]] = []
    for issue in backlog:
        parsed = parse_phased_title(issue.get("title") or "")
        if parsed:
            phased_backlog.append((parsed[0], parsed[1], issue))
    phased_backlog.sort(key=lambda row: (row[0], row[1], _issue_sort_key(row[2])))

    for sequence, phase, issue in phased_backlog:
        if sequence in sequences_seen:
            continue
        ident = issue.get("identifier") or issue.get("id")
        if not _predecessors_done(sequence, phase, by_sequence):
            skip_reasons.append(
                f"{ident}: {sequence} phase {phase} blocked — earlier phase not done in Multica"
            )
            continue
        phased_picks.append(issue)
        sequences_seen.add(sequence)

    unphased = [i for i in backlog if parse_phased_title(i.get("title") or "") is None]
    unphased.sort(key=_issue_sort_key)

    batch: list[dict] = []
    for issue in phased_picks + unphased:
        if len(batch) >= count:
            break
        batch.append(issue)
    return batch, skip_reasons


async def run(args: argparse.Namespace) -> int:
    from multica_client import get_engineering_multica_agent_id, get_multica_client

    client = get_multica_client()
    if not client.is_configured():
        print("Multica not configured", file=sys.stderr)
        return 1

    hermes_id = get_engineering_multica_agent_id()
    backlog = await client.list_issues(status="backlog")
    all_issues = await client.list_issues()
    hermes_backlog = [
        i
        for i in backlog or []
        if str(i.get("assignee_id") or "") == hermes_id
        and str(i.get("assignee_type") or "agent") in ("agent", "")
    ]
    hermes_backlog.sort(key=_issue_sort_key)

    batch, skip_reasons = select_batch(hermes_backlog, all_issues or [], args.count)

    print(f"Hermes backlog: {len(hermes_backlog)} issue(s); batch size {args.count}; moving {len(batch)}")
    if skip_reasons:
        print("Phased skips (predecessor not done):")
        for line in skip_reasons:
            print(f"  - {line}")
    if batch:
        print("Selected for todo:")
    for issue in batch:
        ident = issue.get("identifier") or issue.get("id")
        parsed = parse_phased_title(issue.get("title") or "")
        phase_note = f" [{parsed[0]} P{parsed[1]}]" if parsed else ""
        print(f"  - {ident}{phase_note}: {(issue.get('title') or '')[:70]}")

    if args.dry_run or not args.execute:
        if not args.execute:
            print("Dry-run (pass --execute to apply)")
        return 0

    moved = 0
    for issue in batch:
        issue_id = str(issue.get("id") or "")
        if not issue_id:
            continue
        result = await client.update_issue(issue_id, status="todo")
        ident = issue.get("identifier") or issue_id
        if result.get("error"):
            print(f"FAIL {ident}: {result['error']}", file=sys.stderr)
        else:
            moved += 1
            print(f"OK {ident} → todo")
        if args.sleep_ms > 0:
            await asyncio.sleep(args.sleep_ms / 1000.0)

    remaining = max(0, len(hermes_backlog) - moved)
    print(f"Moved {moved}/{len(batch)}; ~{remaining} Hermes backlog remaining")
    return 0 if moved == len(batch) else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--count", type=int, default=5, help="Issues to move (default 5)")
    parser.add_argument("--dry-run", action="store_true", help="List targets only")
    parser.add_argument("--execute", action="store_true", help="Apply status updates")
    parser.add_argument("--sleep-ms", type=int, default=100, help="Delay between updates")
    args = parser.parse_args()
    if args.execute and args.dry_run:
        print("Use either --dry-run or --execute, not both", file=sys.stderr)
        return 2
    if not args.execute:
        args.dry_run = True
    _load_dotenv()
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
