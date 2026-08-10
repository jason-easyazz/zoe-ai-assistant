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

A GREEN RESULT MUST MEAN "REPLIES WERE CHECKED", NOT "NOTHING WAS FOUND. The
sidecar creates its store at boot, so a run that never actually reached the 2.x
lane — ``ZOE_BRAIN_BACKEND`` not flipped, zoe-data pointed at the wrong sidecar, the harness
env not applied — leaves a valid, empty database. Counting zero length-stops in
it and printing PASS would green-light the flip on no evidence at all, which is
the precise failure this gate exists to prevent. So the gate also counts the
assistant replies it EXAMINED and fails when that is zero.

Use ``--since`` to scope a run. The store accumulates across sessions, so after a
failed attempt its old length-stops would fail every later run forever and push
an operator toward deleting the database — destroying the evidence. Pass the
timestamp the replay started instead.

READ-ONLY. Opens the store with SQLite's ``mode=ro`` URI and never writes.

Usage:
    python3 labs/flue-zoe-brain-2x/parity/count_length_stops.py \
        [--db PATH] [--since ISO8601] [--json]

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
    """Full serialized batch for one row, reassembling a spilled batch.

    Mirrors upstream's ``materializeBatchData`` validation exactly, including the
    per-row ``chunk_index``/``chunk_count`` checks — a laxer version would happily
    reassemble out-of-order chunks into corrupt JSON and could silently lose a
    length-stop record inside a spilled batch.
    """
    if not stored.startswith("{"):
        return stored
    chunk_count = json.loads(stored).get(_SPILL_KEY)
    rows = conn.execute(
        "SELECT chunk_index, chunk_count, data FROM flue_conversation_stream_batch_chunks "
        "WHERE path = ? AND seq = ? ORDER BY chunk_index",
        (path, seq),
    ).fetchall()
    if not isinstance(chunk_count, int) or chunk_count <= 0:
        raise SystemExit(f"FAIL: spilled batch {path}#{seq} has a malformed sentinel")
    if len(rows) != chunk_count:
        raise SystemExit(
            f"FAIL: spilled batch {path}#{seq} is incomplete "
            f"({len(rows)} chunk rows, sentinel says {chunk_count}) — store is damaged"
        )
    for index, (chunk_index, row_count, _data) in enumerate(rows):
        if chunk_index != index or row_count != chunk_count:
            raise SystemExit(
                f"FAIL: spilled batch {path}#{seq} chunk rows are not contiguous "
                f"(row {index} says index={chunk_index} count={row_count})"
            )
    return "".join(row[2] for row in rows)


def scan(db: Path, since: str | None = None) -> tuple[list[dict], int]:
    """Return (length-stop hits, assistant replies examined).

    The second value is what separates "checked and clean" from "there was
    nothing to check"; the caller fails on zero.
    """
    if not db.exists():
        raise SystemExit(f"FAIL: no store at {db} — nothing was replayed against it?")
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        hits: list[dict] = []
        examined = 0
        for path, seq, data in conn.execute(
            "SELECT path, seq, data FROM flue_conversation_stream_batches"
        ):
            try:
                records = json.loads(_materialize(conn, path, seq, data))
            except json.JSONDecodeError as err:
                # Fail closed with the module's own voice rather than a traceback.
                raise SystemExit(
                    f"FAIL: batch {path}#{seq} is not valid JSON ({err}) — store is damaged"
                ) from err
            for record in records:
                if record.get("type") != "assistant_message_completed":
                    continue
                timestamp = record.get("timestamp")
                # ISO-8601 UTC strings from the same producer: lexicographic
                # comparison is chronological. A record with no timestamp is
                # never skipped — dropping evidence is the failure mode here.
                if since and isinstance(timestamp, str) and timestamp < since:
                    continue
                examined += 1
                if record.get("stopReason") == "length":
                    usage = record.get("usage") or {}
                    hits.append(
                        {
                            "stream": path,
                            "seq": seq,
                            "timestamp": timestamp,
                            "submissionId": record.get("submissionId"),
                            "input": usage.get("input"),
                            "output": usage.get("output"),
                        }
                    )
        return sorted(hits, key=lambda h: (h["stream"], h["seq"])), examined
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="store path")
    parser.add_argument(
        "--since",
        help="only examine records at/after this ISO-8601 timestamp "
        "(scope the check to THIS replay run; the store accumulates)",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON")
    args = parser.parse_args()

    hits, examined = scan(args.db, args.since)
    failed = bool(hits) or examined == 0

    if args.json:
        print(
            json.dumps(
                {
                    "db": str(args.db),
                    "since": args.since,
                    "examined": examined,
                    "count": len(hits),
                    "hits": hits,
                    "ok": not failed,
                },
                indent=2,
            )
        )
        return 1 if failed else 0

    if examined == 0:
        scope = f" at/after {args.since}" if args.since else ""
        print(f"FAIL: 0 assistant replies{scope} in {args.db}")
        print("  Nothing was checked, so this is NOT a pass. The replay almost")
        print("  certainly never reached the 2.x sidecar — verify ZOE_FLUE_WIRE=2 and")
        print("  ZOE_FLUE_BRAIN_URL in services/zoe-data/.env, and that zoe-data was")
        print("  restarted after the edit.")
        return 1

    if not hits:
        print(f"PASS: 0 length-stopped replies out of {examined} examined in {args.db}")
        return 0

    print(f"FAIL: {len(hits)} length-stopped replies out of {examined} in {args.db}")
    print("  Replies were TRUNCATED. The replay verdict counts cannot see this.")
    for hit in hits:
        print(
            f"  {hit['stream']} seq={hit['seq']} "
            f"input={hit['input']} output={hit['output']} at {hit['timestamp']}"
        )
    print("  => ROLL THE FLIP BACK, then re-check the output-budget arithmetic")
    print("     (labs/flue-zoe-brain-2x/src/providers/capped-completions.ts).")
    print("  If these are stale hits from an EARLIER attempt, re-run with --since")
    print("  <the timestamp this replay started> rather than deleting the store.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
