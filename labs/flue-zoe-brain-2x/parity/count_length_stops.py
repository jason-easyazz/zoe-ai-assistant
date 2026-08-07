#!/usr/bin/env python3
"""Post-flip assertion: ZERO ``stopReason: "length"`` records in the 2.x store.

WHY THIS GATE EXISTS. The 2026-08-06 flip attempt scored ``OK=18 CANT_DO=1
EMPTY=1`` on the replay harness and was reported as a near-pass. It was not: the
lane was silently truncating replies mid-sentence on turns the harness scored
``OK`` ("I'm Zoe." became "I", "Which room needs the light turned on?" became
"Which room are"), because pi-ai 0.83.0's ``clampMaxTokensToContext`` had cut the
output budget to single digits. A truncated-but-non-empty reply matches no
``_CANT_DO_RE`` pattern, so verdict counts alone cannot see it — and the same
truncation is what produced the one real ``CANT_DO`` (a length-stopped tool call
poisons the canonical conversation stream and kills the turn).

So: after any replay run against the 2.x sidecar, count the length stops. On this
deployment ANY of them is a bug, not a tuning knob — llama-server has an
8192-token slot and ``src/context-window.ts`` windows every prompt to fit inside
it with a reply reserve, so a reply that runs out of room means the budget
arithmetic is wrong somewhere. Exit code 1 on any hit; roll the flip back.

READ-ONLY. Opens the store with SQLite's ``mode=ro`` URI and never writes.

Usage:
    python3 labs/flue-zoe-brain-2x/parity/count_length_stops.py [--db PATH] [--json]

Default DB is the sidecar's own store (``labs/flue-zoe-brain-2x/data/zoe-brain.db``
— the ``ZOE_BRAIN_DB`` default), resolved relative to this file so the command
works from any cwd. Note the box has no ``sqlite3`` CLI; this is stdlib Python on
purpose.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

DEFAULT_DB = Path(__file__).resolve().parent.parent / "data" / "zoe-brain.db"

# Flue spills a serialized batch over ~1MB into `flue_conversation_stream_batch_chunks`
# and leaves this sentinel in the batch row's `data` column instead. Real batch data
# is a JSON array and always starts with "[", so a stored value starting with "{" is
# always the sentinel (@flue/runtime conversation-stream-store, `materializeBatchData`).
_SPILL_KEY = "$flueChunkCount"


def _materialize(conn: sqlite3.Connection, path: str, seq: int, stored: str) -> str:
    """Full serialized batch for one row, reassembling a spilled batch."""
    if not stored.startswith("{"):
        return stored
    chunk_count = json.loads(stored).get(_SPILL_KEY)
    rows = conn.execute(
        "SELECT data FROM flue_conversation_stream_batch_chunks "
        "WHERE path = ? AND seq = ? ORDER BY chunk_index",
        (path, seq),
    ).fetchall()
    if not isinstance(chunk_count, int) or len(rows) != chunk_count:
        raise SystemExit(
            f"FAIL: spilled batch {path}#{seq} is incomplete "
            f"({len(rows)} chunk rows, sentinel says {chunk_count}) — store is damaged"
        )
    return "".join(row[0] for row in rows)


def find_length_stops(db: Path) -> list[dict]:
    """Every `assistant_message_completed` record whose stopReason is "length"."""
    if not db.exists():
        raise SystemExit(f"FAIL: no store at {db} — nothing was replayed against it?")
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        hits: list[dict] = []
        for path, seq, data in conn.execute(
            "SELECT path, seq, data FROM flue_conversation_stream_batches"
        ):
            for record in json.loads(_materialize(conn, path, seq, data)):
                if (
                    record.get("type") == "assistant_message_completed"
                    and record.get("stopReason") == "length"
                ):
                    usage = record.get("usage") or {}
                    hits.append(
                        {
                            "stream": path,
                            "seq": seq,
                            "timestamp": record.get("timestamp"),
                            "submissionId": record.get("submissionId"),
                            "input": usage.get("input"),
                            "output": usage.get("output"),
                        }
                    )
        return sorted(hits, key=lambda h: (h["stream"], h["seq"]))
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="store path")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    args = parser.parse_args()

    hits = find_length_stops(args.db)
    if args.json:
        print(json.dumps({"db": str(args.db), "count": len(hits), "hits": hits}, indent=2))
    elif not hits:
        print(f"PASS: 0 length-stopped replies in {args.db}")
    else:
        print(f"FAIL: {len(hits)} length-stopped replies in {args.db}")
        print("  Replies were TRUNCATED. The replay verdict counts cannot see this.")
        for hit in hits:
            print(
                f"  {hit['stream']} seq={hit['seq']} "
                f"input={hit['input']} output={hit['output']} at {hit['timestamp']}"
            )
        print("  => ROLL THE FLIP BACK, then re-check the output-budget arithmetic")
        print("     (labs/flue-zoe-brain-2x/src/providers/capped-completions.ts).")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
