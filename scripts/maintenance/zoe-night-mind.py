#!/usr/bin/env python3
"""Run Zoe's NIGHT MIND (the nightly reflection pass, services/zoe-data/night_mind.py) as a STANDALONE process.

The same code the nightly digest calls (``memory_digest.run_memory_digest`` -> ``night_mind.run_for_user``), runnable on its own so a bigger model can do
the night's reflection in a window of its own (the 12B at 16k / 32k on :11500) and so one member / one day can be re-run by hand.

    zoe-night-mind.py --model-url http://127.0.0.1:11500/v1 --ctx-tokens 16384 --all-members            # the night, enforce
    zoe-night-mind.py --model-url ... --ctx-tokens 16384 --user alice --date 2026-10-08 --dry-run        # calls + checks, NOTHING written
    zoe-night-mind.py --model-url ... --ctx-tokens 16384 --cells                                          # score the K cells (lab) on that model

CONTRACT (the 12B night-window agent codes against this; docs/knowledge/night-mind.md has the same text)
-------------------------------------------------------------------------------------------------------
Flags
  --model-url URL      the llama-server (``http://127.0.0.1:11500/v1`` or without /v1). Default: $ZOE_NIGHT_MIND_URL, else $GEMMA_SERVER_URL.
                       Loopback only unless --allow-remote.
  --model-name NAME    the model name sent in the request (default $ZOE_NIGHT_MIND_MODEL / $MEMORY_DIGEST_MODEL).
  --ctx-tokens N       the server's context size (default 8192). The chunk budget derives from it: 2,400 turn-tokens at 8k, proportionally more
                       (16k -> 4,800, 32k -> 9,600), capped so prompt + output stay inside N.
  --chunk-tokens N     override the derived chunk budget.   --max-calls N   model calls per member per night (default 7 = 6 MOMENTS + 1 THREADS).
  --decode-tok-s X     the server's measured decode rate (default 8.0; the HTTP timeout is prompt/650 + max_tokens/X + 20 s per call).
  --user ID | --all-members   one member, or every chat-turn owner minus synthetic ids (--allow-synthetic keeps the demo ids).
  --date YYYY-MM-DD    reflect on that Zoe-local day (00:00-24:00). Default: the digest's rolling lookback (the last ~30 h).
  --dry-run            run every call and check, write NOTHING (mode shadow). Without it: mode enforce. (--mode shadow|enforce overrides.)
  --transcript-file F  synthetic runs without Postgres: a JSON list of {"id","text","at"} (turns of ONE member, given by --user).
  --cells              do not touch any member: score the reflection cells K1-K12 (the lab, scratch stores) against --model-url and print the counts.
Output
  ONE JSON object on stdout (logs go to stderr):
    {"status": "ok"|"nothing_to_do"|"llm_unreachable"|"error", "mode", "dry_run", "model_url", "model", "ctx_tokens", "chunk_budget", "max_calls", "date",
     "members": [{"user_id", "status", "turns_in", "turns_dropped_routine", "turns_skipped_cap", "chunks", "calls", "calls_invalid", "moments_proposed",
                  "moments_verified", "moments_held", "observations_written", "observations_pending", "threads_created", "threads_updated",
                  "prompt_tokens", "completion_tokens", "wall_s", "written", "skipped_reason"?, "error"?}],
     "totals": {"members", "calls", "prompt_tokens", "completion_tokens", "wall_s", "observations_written", "observations_pending", "calls_invalid",
                "completion_tokens_per_wall_s"},
     "cells": {"K1": "PASS", ..., "pass", "fail", "skip", "error", "k1": {"judged", "true", "false"}}      (only with --cells)}
Exit codes: 0 ok / nothing to do, 1 error, 2 the model is unreachable (NOTHING was written: the pass computes the whole night in memory and commits at the end,
and probes the server first).
Never prints environment, tokens or the text of what the owner said (counts and ids only).
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import pathlib
import sys
import time

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "services" / "zoe-data"))


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Zoe's night mind as a standalone process (the contract is in this file's docstring)")
    ap.add_argument("--model-url", default="")
    ap.add_argument("--model-name", default="")
    ap.add_argument("--ctx-tokens", type=int, default=8192)
    ap.add_argument("--chunk-tokens", type=int, default=0)
    ap.add_argument("--max-calls", type=int, default=0)
    ap.add_argument("--decode-tok-s", type=float, default=0.0)
    who = ap.add_mutually_exclusive_group()
    who.add_argument("--user", default="")
    who.add_argument("--all-members", action="store_true")
    ap.add_argument("--allow-synthetic", action="store_true", help="include synthetic / demo ids (never in a real night)")
    ap.add_argument("--allow-remote", action="store_true", help="allow a non-loopback --model-url (the box's rule is: nothing leaves it)")
    ap.add_argument("--date", default="")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--mode", choices=("shadow", "enforce"), default="")
    ap.add_argument("--transcript-file", default="")
    ap.add_argument("--cells", action="store_true")
    ap.add_argument("--seed", default="zmb-v1", help="--cells: the corpus seed")
    return ap


def _loopback(url: str, allow_remote: bool) -> str:
    import ipaddress
    import urllib.parse

    host = urllib.parse.urlparse(url).hostname or ""
    try:
        ok = host == "localhost" or ipaddress.ip_address(host).is_loopback
    except ValueError:
        ok = False
    if not ok and not allow_remote:
        raise SystemExit(f"refusing the non-loopback --model-url {url!r} (use --allow-remote only if the owner said so)")
    return url


def _transcript_from_file(path: str):
    import memory_digest

    raw = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    turns = [(str(t["id"]), str(t["text"])) for t in raw]
    times = [str(t.get("at") or "") for t in raw]
    return memory_digest.Transcript("\n".join(t for _i, t in turns), turns, times)


def _day_bounds(day: str) -> "tuple[str, str]":
    from time_utils import zoe_timezone

    d = dt.date.fromisoformat(day)
    tz = zoe_timezone()
    start = dt.datetime(d.year, d.month, d.day, tzinfo=tz)
    return start.isoformat(), (start + dt.timedelta(days=1)).isoformat()


async def run_members(args, cfg, mode: str) -> dict:
    """The member loop. Returns the summary dict (without ``status``)."""
    import night_mind as nm

    members: "list[dict]" = []
    svc = None
    db_ctx = None
    try:
        if not args.transcript_file:
            from db_pool import close_pool, get_db_ctx, init_pool

            await init_pool()
            db_ctx = (get_db_ctx, close_pool)
        from memory_service import get_memory_service

        svc = get_memory_service()
        users: "list[str]"
        if args.transcript_file:
            users = [args.user or "demo_night_file"]
        elif args.user:
            users = [args.user]
        else:
            import memory_digest as md
            from user_filters import drop_synthetic_users

            async with db_ctx[0]() as db:
                users = await md._list_user_ids(md._message_owner_users_sql(today_only=False), db=db)
            if not args.allow_synthetic:
                users = drop_synthetic_users(users, pass_name="night_mind", log=None)
        for uid in users:
            if args.transcript_file:
                transcript = _transcript_from_file(args.transcript_file)
            else:
                import memory_digest as md

                async with db_ctx[0]() as db:
                    if args.date:
                        start, end = _day_bounds(args.date)
                        transcript = await md.load_day_messages(uid, start, end, db=db)
                    else:
                        transcript = await md._load_todays_messages(uid, db)
            night_date = args.date or ""
            res = await nm.run_for_user(uid, transcript, svc, force_mode=mode, cfg=cfg, night_date=night_date)
            members.append({k: v for k, v in res.items() if isinstance(v, (int, float, str, bool)) and k != "mood"})
            if res.get("status") == "llm_unreachable":
                break
    finally:
        if db_ctx is not None:
            await db_ctx[1]()
    return {"members": members}


def totals(members: "list[dict]") -> dict:
    keys = ("calls", "prompt_tokens", "completion_tokens", "wall_s", "observations_written", "observations_pending", "calls_invalid", "moments_verified")
    t = {k: round(sum(float(m.get(k) or 0) for m in members), 2) for k in keys}
    for k in keys:
        if k != "wall_s":
            t[k] = int(t[k])
    t["members"] = len(members)
    t["completion_tokens_per_wall_s"] = round(t["completion_tokens"] / t["wall_s"], 2) if t["wall_s"] else None
    return t


def run_cells(args, url: str) -> dict:
    """Score the reflection cells K1-K12 (the lab: scratch stores, synthetic household) against the model at ``url``. Touches no member."""
    sys.path.insert(0, str(REPO / "scripts" / "perf"))
    from zmb import cells as cellmod, spec, world
    from zmb.arms.z0 import Z0Arm

    base = url[:-3] if url.rstrip("/").endswith("/v1") else url
    arm = Z0Arm(name="Z0n", night=True, night_url=base.rstrip("/"), night_model=args.model_name, night_ctx=args.ctx_tokens)
    w = world.make_world(args.seed)
    out: dict = {}
    k1: dict = {}
    try:
        for c in (c for c in spec.load_cells() if c.axis == "reflection"):
            o = cellmod.run_cell(c.rendered(w), w, arm)
            out[c.id.split(".")[0] + ("f" if c.id.endswith("flat_week") else "")] = o.verdict
            if c.id.startswith("K1."):
                ev = ((o.evidence.get("probes") or [{}])[0]).get("observations_judged") or {}
                k1 = {"judged": ev.get("decidable", ev.get("n")), "true": ev.get("true"), "false": ev.get("false"), "observations": ev.get("observations")}
    finally:
        arm.close()
    verdicts = list(out.values())
    out.update({"pass": verdicts.count("PASS"), "fail": verdicts.count("FAIL"), "skip": verdicts.count("SKIP"), "error": verdicts.count("ERROR"), "k1": k1})
    return out


async def amain(argv: "list[str] | None" = None) -> "tuple[int, dict]":
    args = build_parser().parse_args(argv)
    if not args.cells and not (args.user or args.all_members):
        raise SystemExit("one of --user ID, --all-members or --cells is required")
    if args.cells or args.transcript_file:
        # the lab pins every per-user store (the palace, the reject ledger, the STT log) to a scratch directory BEFORE any service module is imported, so a
        # synthetic run (--cells, or a --transcript-file of demo turns) can never read or write the household's real stores
        sys.path.insert(0, str(REPO / "scripts" / "perf"))
        from zmb import lab_driver

        lab_driver.pin_scratch_stores()
    import night_mind as nm
    if args.transcript_file:
        import night_store

        night_store.set_backend(night_store.MemoryBackend())      # no Postgres in a synthetic run

    url = _loopback(args.model_url or "", args.allow_remote) if args.model_url else ""
    cfg = nm.config_from_env(url=url, model=args.model_name, ctx_tokens=args.ctx_tokens, max_calls=args.max_calls or None,
                             chunk_tokens=args.chunk_tokens or None, decode_tok_s=args.decode_tok_s or None)
    mode = args.mode or ("shadow" if args.dry_run else "enforce")
    summary: dict = {"status": "ok", "mode": mode, "dry_run": mode == "shadow", "model_url": cfg.url, "model": cfg.model, "ctx_tokens": cfg.ctx_tokens,
                     "chunk_budget": cfg.chunk_budget, "max_calls": cfg.max_calls, "date": args.date or None, "members": [], "totals": {}}
    ok, why = await nm.probe_model(cfg)
    if not ok:
        summary.update(status="llm_unreachable", reason=why, totals=totals([]))
        return 2, summary
    t0 = time.monotonic()
    if args.cells:
        summary["cells"] = await asyncio.to_thread(run_cells, args, cfg.url + "/v1")           # the lab arm drives its own event loop: not inside this one
        summary["totals"] = {**totals([]), "wall_s": round(time.monotonic() - t0, 2)}
        return 0, summary
    res = await run_members(args, cfg, mode)
    summary["members"] = res["members"]
    summary["totals"] = totals(res["members"])
    statuses = {m.get("status") for m in res["members"]}
    if "llm_unreachable" in statuses:
        summary["status"] = "llm_unreachable"
        return 2, summary
    if "error" in statuses:
        summary["status"] = "error"
        return 1, summary
    if not res["members"]:
        summary["status"] = "nothing_to_do"
    return 0, summary


def main(argv: "list[str] | None" = None) -> int:
    import logging

    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(message)s")
    code, summary = asyncio.run(amain(argv))
    print(json.dumps(summary, indent=2, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
