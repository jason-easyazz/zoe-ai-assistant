#!/usr/bin/env python3
"""Write a pendant day as a BOOK-format transcript (services/zoe-data/omi_transcript_book.py): the clean read as prose, speaker in bold, scene breaks instead of
timestamps, uncertain turns in italics, the verbatim with times as an appendix.

    zoe-omi-transcript.py --user USER_ID --date 2026-11-08 --out ~/.zoe/omi-transcripts

It holds what people said, so --out must be OUTSIDE the repository; the file is written 0600. Prints the path and a turn count - never the text.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import os
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "services" / "zoe-data"))


async def amain(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--user", required=True)
    ap.add_argument("--date", required=True, help="the household day, YYYY-MM-DD")
    ap.add_argument("--out", required=True, help="a directory OUTSIDE the repository")
    args = ap.parse_args(argv)
    out_dir = pathlib.Path(args.out).expanduser().resolve()
    if REPO == out_dir or REPO in out_dir.parents:
        raise SystemExit("a transcript holds what people said: write it OUTSIDE the repository")
    import omi_day_review as dr
    import omi_transcript_book as book
    from db_compat import get_compat_db
    from db_pool import close_pool, init_pool

    day, tz = dt.date.fromisoformat(args.date), dr._tz()
    await init_pool()
    try:
        async with get_compat_db() as db:
            turns = await book.load_day(db, args.user, day, tz)
    finally:
        await close_pool()
    if not turns:
        print("OMI_TRANSCRIPT turns=0 (nothing for that day)")
        return 0
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{day.isoformat()}-book.md"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(book.render(turns, title=f"{day:%A}, {day.day} {day:%B %Y}", tz=tz))
    print(f"OMI_TRANSCRIPT turns={len(turns)} path={path}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(amain()))
