#!/usr/bin/env python3
"""Run the night-extract SHADOW pre-pass (services/zoe-data/night_extract.py) as a standalone process; read a night's proposals.

The night window's job ``extract_prepass`` runs this on the 12B's lane right after the digest (the 12B is idle during it; it only needs the CPU):

    zoe-night-extract.py --budget-s 300                      # the night lane: the real members' last 24 h, the model once over all of it
    zoe-night-extract.py --dry-run                           # read + extract + compare, WRITE NOTHING (a flag-off box still refuses: status flag_off)
    zoe-night-extract.py --jsonl a.jsonl b.jsonl --out-dir D # SYNTHETIC input only: each file is one persona's day (rows with an "in" field); no database, no ledger
    zoe-night-extract.py --show 2026-11-08                   # the owner: a night's proposals, re-checked against the forget ledger

Shadow: nothing is written to memory or the people graph, ever. The proposals go to ~/.zoe/night-extract/<night>.night-extract.jsonl (0600).
Flags: ZOE_NIGHT_EXTRACT=off|shadow|on (default off; ``on`` is not built and runs as shadow), ZOE_NIGHT_EXTRACT_THREADS / _BATCH / _MEM_MAX / _VENV / _DIR.
Output: one JSON line (counts only) on stdout, a ``NIGHT_JOB extract_prepass ...`` line on stderr. Exit 0 always for a data outcome (a shadow pass never fails
the night: a missing venv/model is ``status=model_missing``); 2 only for a usage error.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import functools
import json
import logging
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "services" / "zoe-data"))

WINDOW_H = 24
MIN_BUDGET_S = 120.0                    # under this the model load + a day does not fit: not started ("no_time")


def _epoch(iso: str) -> float:
    try:
        return dt.datetime.fromisoformat(str(iso).strip().replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _jsonl_turns(path: Path) -> list:
    out = []
    for i, ln in enumerate(path.read_text(encoding="utf-8").splitlines()):
        try:
            row = json.loads(ln)
        except ValueError:
            continue
        if isinstance(row, dict) and str(row.get("in") or "").strip():
            out.append((f"{path.stem}:{i}", str(row["in"]), 0.0))
    return out


async def amain(argv: "list[str] | None" = None) -> "tuple[int, dict]":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--night", default="")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--budget-s", type=float, default=0.0)
    ap.add_argument("--threads", type=int, default=0)
    ap.add_argument("--batch-size", type=int, default=0)
    ap.add_argument("--mem-max", default="")
    ap.add_argument("--jsonl", nargs="*", default=[], type=Path, help="synthetic persona files (no database, no ledger)")
    ap.add_argument("--out-dir", type=Path, default=None)
    ap.add_argument("--force-mode", default="", choices=("", "shadow"), help="run although the flag is off (synthetic / manual runs only)")
    ap.add_argument("--show", default="", metavar="NIGHT")
    args, _unknown = ap.parse_known_args(argv)        # the window appends model-url / tok-s flags every job gets; this job has no model server

    import night_extract as ne

    if args.show:
        from db_pool import close_pool, init_pool

        await init_pool()
        try:
            recs = await ne.load_facts(args.show)
        finally:
            await close_pool()
        for r in recs:
            print(json.dumps(r, sort_keys=True))
        return 0, {"status": "ok", "facts": len(recs)}
    run_mode = args.force_mode or ne.mode()
    if run_mode == "off":
        return 0, {"status": "flag_off", "mode": "off"}
    if args.budget_s and args.budget_s < MIN_BUDGET_S:
        return 0, {"status": "no_time", "budget_s": args.budget_s}
    threads = args.threads or int(ne._flag("ZOE_NIGHT_EXTRACT_THREADS", 4))
    batch = args.batch_size or int(ne._flag("ZOE_NIGHT_EXTRACT_BATCH", 8))
    mem_max = args.mem_max or str(ne._flag("ZOE_NIGHT_EXTRACT_MEM_MAX", "1600M"))
    night = args.night or dt.date.today().isoformat()
    extract = functools.partial(ne.run_worker, threads=threads, batch_size=batch, mem_max=mem_max,
                                timeout_s=max(60.0, (args.budget_s or 900.0) - 30.0))
    if args.jsonl:
        persona = {p.stem + ":" + p.parent.name: _jsonl_turns(p) for p in args.jsonl}

        async def load_turns(uid: str) -> list:
            return persona[uid]

        async def load_stored(_uid: str) -> list:
            return []

        s = await ne.run_night(night=night, members=list(persona), load_turns=load_turns, load_stored=load_stored, extract=extract,
                               dry_run=args.dry_run, d=args.out_dir, run_mode=run_mode, use_ledger=False)
        return 0, s
    from db_pool import close_pool, init_pool

    try:
        await init_pool()
    except Exception as exc:  # noqa: BLE001
        return 0, {"status": "error", "setup_error": type(exc).__name__}
    try:
        import memory_digest as md
        from memory_service import get_memory_service

        ids = await md._list_user_ids(md._message_owner_users_sql(today_only=False, lookback_hours=WINDOW_H), (WINDOW_H,))
        members = ne.real_members(ids)
        end = dt.datetime.now(dt.timezone.utc)
        start = end - dt.timedelta(hours=WINDOW_H)

        async def load_turns(uid: str) -> list:
            tr = await md.load_day_messages(uid, start.isoformat(), end.isoformat())       # own words, off-record + forgotten turns out (strict ledger)
            tr = md.without_rejected_speakers(tr)          # a voice the speaker gate rejected is not the member's: never extracted as theirs
            turns, times = list(getattr(tr, "turns", ())), list(getattr(tr, "times", ()))
            times += [""] * (len(turns) - len(times))
            return [(mid, line, _epoch(at)) for (mid, line), at in zip(turns, times)]

        async def load_stored(uid: str) -> list:
            refs = await get_memory_service().load_recent_for_prompt(uid, window_s=WINDOW_H * 3600, limit=300)
            return [r.text for r in refs or []]

        s = await ne.run_night(night=night, members=members, load_turns=load_turns, load_stored=load_stored, extract=extract,
                               dry_run=args.dry_run, d=args.out_dir, run_mode=run_mode)
    except Exception as exc:  # noqa: BLE001
        s = {"status": "error", "error": type(exc).__name__}
    finally:
        await close_pool()
    return 0, s


def main(argv: "list[str] | None" = None) -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(levelname)s %(name)s: %(message)s")
    t0 = time.monotonic()
    try:
        code, s = asyncio.run(amain(argv))
    except Exception as exc:  # noqa: BLE001 - a shadow pass never fails the night
        code, s = 0, {"status": "error", "error": type(exc).__name__}
    s.pop("rows", None)                                   # proposals are never printed: the stdout line is counts only
    members = s.get("members") or {}
    s["members"] = len(members) if isinstance(members, dict) else members
    s.setdefault("wall_s", round(time.monotonic() - t0, 1))
    print(json.dumps(s, separators=(",", ":"), sort_keys=True))
    print(f"NIGHT_JOB extract_prepass status={s.get('status')} members={s.get('members', 0)} turns={s.get('turns', 0)} facts={s.get('facts', 0)} "
          f"agree={s.get('agree', 0)} extractor_only={s.get('extractor_only', 0)} contradicts={s.get('contradicts', 0)} wall_s={s.get('wall_s')} "
          f"peak_mb={s.get('peak_rss_mb')}", file=sys.stderr)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
