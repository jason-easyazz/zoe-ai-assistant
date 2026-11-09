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
import json
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "services" / "zoe-data"))

LAB = pathlib.Path.home() / ".zoe" / "lab" / "omi-retranscribe-r2"


def _lab_rows(lab: pathlib.Path):
    """Yield the stored-chunk rows (the shape ``omi_repass_eval.check_queue`` returns) the lab's ROVER makes of its pendant day. Words stay in memory."""
    sys.path.insert(0, str(lab / "fusion"))
    import omi_repass_eval as eu
    from common import load
    for i, it in enumerate(load()):
        names = {"qwen3": "qwen3-asr", "parakeet": "parakeet-tdt", "whisper": "whisper-gpu:large-v3"}      # the lab's short names -> the re-pass's backend ids
        hyps = {names[k]: v for k, v in it["hyp"].items() if k in names}
        fz = eu.rover(hyps)
        yield {"i": i, "t0": it["t0"], "t1": it["t1"], "hyps": hyps, "rover": fz["text"], "slots": fz["slots"], "low_conf": fz["low_conf"], "voters": fz["voters"]}


def _contested(r):
    import omi_repass_eval as eu
    return eu.is_contested(r)


def rehearse_lab(lab: pathlib.Path) -> dict:
    import transcript_check as tc
    chunks = [r for r in _lab_rows(lab) if _contested(r)]
    cands, by_chunk = [], 0
    for ch in chunks:
        c = tc.candidates_from_row(ch)
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
            "contender_ledger": tc.ledger()}


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
