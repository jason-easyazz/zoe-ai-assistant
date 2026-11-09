#!/usr/bin/env python3
"""The morning TRANSCRIPT CHECK (services/zoe-data/transcript_check.py): the owner's daytime rehearsal and status commands.

    zoe-transcript-check.py --status --user ID           # counts only: open / answered / skipped / shadow items, the ask lock, the contender ledger
    zoe-transcript-check.py --rehearse-lab [DIR]          # the NIGHT half on the lab's fusion output: contested slots -> ranked, capped queue; counts only
    zoe-transcript-check.py --shadow-report [--days N]    # what the shadow weeks WOULD have asked, from the app log's TRANSCRIPT_CHECK_SHADOW lines

Nothing here prints a word, a name or a sentence, and nothing writes: the rehearsal works in memory. Run it BY DAY, before ZOE_TRANSCRIPT_CHECK goes to
shadow (owner rule: rehearse a night job by day). Flag ``ZOE_TRANSCRIPT_CHECK`` = off | shadow | on. Output: ONE JSON line on stdout.
"""
from __future__ import annotations

import argparse
import asyncio
import difflib
import json
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "services" / "zoe-data"))

LAB = pathlib.Path.home() / ".zoe" / "lab" / "omi-retranscribe-r2"
LOWCONF_AWAY = 0.5          # every other model more than half different from the pivot = a low-confidence chunk


def _lab_chunks(lab: pathlib.Path):
    """Yield the transcript_check chunk dicts the lab's ROVER would hand the night pass. Words stay in memory."""
    sys.path.insert(0, str(lab / "fusion"))
    import rover as rv                                   # the lab prototype: the contract is its slot output
    from common import load
    for it in load():
        hyps = {k: v for k, v in it["hyp"].items() if k in rv.VOTERS}
        away = [1 - difflib.SequenceMatcher(None, hyps["qwen3"].split(), v.split()).ratio() for k, v in hyps.items() if k != "qwen3" and v.strip()]
        if away and all(a > LOWCONF_AWAY for a in away):
            yield {"t0_ms": int(it["t0"] * 1000), "t1_ms": int(it["t1"] * 1000), "mode": "lowconf", "hyps": hyps, "slots": [], "tokens": []}
            continue
        slots: list = []
        tokens, _ = rv.rover(it["hyp"], slots)
        yield {"t0_ms": int(it["t0"] * 1000), "t1_ms": int(it["t1"] * 1000), "mode": "rover", "hyps": hyps, "slots": slots, "tokens": tokens}


def rehearse_lab(lab: pathlib.Path) -> dict:
    import transcript_check as tc
    chunks = list(_lab_chunks(lab))
    cands, by_chunk = [], 0
    for ch in chunks:
        c = tc.candidates_from_chunk(ch)
        by_chunk += bool(c)
        cands += c
    ranked = []
    for c in cands:
        imp = tc.classify(c["before"], c["span"], c["after"], c["alts"], set(), c["kind"])
        ranked.append((tc.score(imp, c["alts"], False), imp, c["kind"]))
    ranked.sort(reverse=True)
    kept, chunk_n = [], 0
    for s, imp, kind in ranked:
        if len(kept) >= tc.max_items():
            break
        if kind == "chunk":
            if chunk_n >= tc.MAX_CHUNK_ITEMS:
                continue
            chunk_n += 1
        kept.append((imp, kind))
    classes: dict = {}
    for imp, _ in kept:
        classes[imp] = classes.get(imp, 0) + 1
    return {"chunks": len(chunks), "chunks_with_candidates": by_chunk, "candidates": len(cands),
            "slots": sum(c["kind"] == "slot" for c in cands), "lowconf_chunks": sum(c["kind"] == "chunk" for c in cands),
            "kept_for_the_day": len(kept), "cap": tc.max_items(), "kept_by_class": classes}


async def status(user: str) -> dict:
    import transcript_check as tc
    async with tc._db() as db:
        rows = await tc._fetch(db, "SELECT status, COUNT(*) AS n FROM omi_check_items WHERE user_id = ? GROUP BY status", (user,))
        asks = await tc._fetch(db, "SELECT day_local, outcome FROM omi_check_asks WHERE user_id = ? ORDER BY day_local DESC LIMIT 7", (user,))
    return {"mode": tc.mode(), "items": {r["status"]: int(r["n"]) for r in rows}, "asks": [{"day": a["day_local"], "outcome": a["outcome"]} for a in asks],
            "ledger": await tc.ledger(user)}


def shadow_report(log: pathlib.Path, days: int) -> dict:
    n = stored = 0
    classes: dict = {}
    if log.exists():
        for line in log.read_text(errors="replace").splitlines()[-200000:]:
            m = re.search(r"TRANSCRIPT_CHECK_SHADOW (\{.*\})", line)
            if not m:
                continue
            try:
                rec = json.loads(m.group(1))
            except ValueError:
                continue
            n += 1
            stored += int(rec.get("stored", 0))
            for k, v in (rec.get("classes") or {}).items():
                classes[k] = classes.get(k, 0) + int(v)
    return {"recordings": n, "would_ask": stored, "by_class": classes, "log": str(log)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--user", default="")
    ap.add_argument("--rehearse-lab", nargs="?", const=str(LAB), default=None)
    ap.add_argument("--shadow-report", action="store_true")
    ap.add_argument("--log", default=str(pathlib.Path.home() / ".zoe-logs" / "zoe-data.log"))
    ap.add_argument("--days", type=int, default=7)
    a = ap.parse_args(argv)
    if a.rehearse_lab is not None:
        out = rehearse_lab(pathlib.Path(a.rehearse_lab))
    elif a.status:
        if not a.user:
            ap.error("--status needs --user")
        out = asyncio.run(status(a.user))
    elif a.shadow_report:
        out = shadow_report(pathlib.Path(a.log), a.days)
    else:
        ap.error("give --status, --rehearse-lab or --shadow-report")
    print(json.dumps(out, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
