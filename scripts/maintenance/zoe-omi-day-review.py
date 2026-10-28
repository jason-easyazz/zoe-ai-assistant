#!/usr/bin/env python3
"""Run the pendant DAY REVIEW (services/zoe-data/omi_day_review.py) as a standalone process.

The night window's job ``omi_day_review`` runs this on the 12B after the digest, dreaming and the night mind:

    zoe-omi-day-review.py --model-url http://127.0.0.1:11500/v1 --ctx-tokens 8192 --all-members           # the night lane
    zoe-omi-day-review.py --model-url http://127.0.0.1:11434/v1 --user alice --date 2026-10-15 --dry-run   # calls + checks, NOTHING written

Flags
  --model-url URL        the llama-server (default $GEMMA_SERVER_URL). Loopback only unless --allow-remote.
  --model-name NAME      the model name sent (default $MEMORY_DIGEST_MODEL).
  --ctx-tokens N         the server's context (default 8192); the chunk budget derives from it.
  --decode-tok-s X / --prefill-tok-s X   the server's measured rates (the window passes them); the per-call HTTP budget follows.
  --user ID | --all-members   one member, or every member with unanalysed pendant recordings (synthetic ids dropped).
  --date YYYY-MM-DD      only that household day (repeatable). Default: every past day not analysed yet (<= ZOE_OMI_DAY_REVIEW_MAX_AGE_DAYS).
  --as-of YYYY-MM-DD     judge "already past" against the END of that day instead of now (the lab: re-reading old days as the night after).
  --budget-s S           the seconds this process may spend (the night window passes the time it has left): a day whose estimated cost
                         at the measured rates does not fit what remains is not started (status ``no_time``; the next night retries it).
  --dry-run              run every call and check, write NOTHING (works with the flag off). Without it the flag must be on.
  --dump FILE            with --dry-run only: the proposed candidates WITH their quotes, as JSON, for a private quality review. Refused
                         inside the repository (it holds what people said).
Output: ONE JSON line on stdout, counts and ids only - never what anyone said (except the --dump file). Exit 0 ok / nothing to do /
flag off, 1 error, 2 the model is unreachable.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import os
import pathlib
import sys
import time

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "services" / "zoe-data"))


def _loopback(url: str, allow_remote: bool) -> str:
    import ipaddress
    import urllib.parse

    host = urllib.parse.urlparse(url).hostname or ""
    try:
        ok = host == "localhost" or ipaddress.ip_address(host).is_loopback
    except ValueError:
        ok = False
    if url and not ok and not allow_remote:
        raise SystemExit(f"refusing the non-loopback --model-url {url!r}")
    return url


def _voice_log_watch(path: pathlib.Path):
    """A cancel check: '' until ``path`` grows a ``voice/turn`` line after this process started."""
    try:
        start = path.stat().st_size
    except OSError:
        start = 0

    def check() -> str:
        try:
            with path.open("rb") as f:
                f.seek(start)
                return "voice_turn" if b"voice/turn" in f.read() else ""
        except OSError:
            return ""
    return check


async def amain(argv: "list[str] | None" = None) -> "tuple[int, dict]":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--model-url", default="")
    ap.add_argument("--model-name", default="")
    ap.add_argument("--ctx-tokens", type=int, default=8192)
    ap.add_argument("--decode-tok-s", type=float, default=0.0)
    ap.add_argument("--prefill-tok-s", type=float, default=0.0)
    who = ap.add_mutually_exclusive_group(required=True)
    who.add_argument("--user", default="")
    who.add_argument("--all-members", action="store_true")
    ap.add_argument("--date", action="append", default=[])
    ap.add_argument("--as-of", default="")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--dump", default="")
    ap.add_argument("--allow-remote", action="store_true")
    ap.add_argument("--yield-to-log", default="")
    ap.add_argument("--chunk-tokens", type=int, default=0)
    ap.add_argument("--budget-s", type=float, default=0.0)
    args = ap.parse_args(argv)
    started = time.monotonic()

    import omi_day_review as dr

    if args.dump:
        if not args.dry_run:
            raise SystemExit("--dump is for --dry-run only")
        p = pathlib.Path(args.dump).expanduser().resolve()
        if REPO in p.parents:
            raise SystemExit("--dump holds what people said: write it OUTSIDE the repository")
    if not args.dry_run and not dr.enabled():
        return 0, {"status": "flag_off", "mode": dr.mode()}
    if not args.dry_run and dr.lane() == "day":
        return 0, {"status": "lane_day_only"}
    cfg = dr.config_from_env(url=_loopback(args.model_url, args.allow_remote), model=args.model_name, ctx_tokens=args.ctx_tokens,
                             decode_tok_s=args.decode_tok_s or None, prefill_tok_s=args.prefill_tok_s or None, lane_name="night")
    if args.chunk_tokens:
        cfg.chunk_tokens = max(300, args.chunk_tokens)
    if args.budget_s > 0:
        cfg.deadline = started + args.budget_s
    if args.yield_to_log:
        cfg.cancel = _voice_log_watch(pathlib.Path(args.yield_to_log).expanduser())
    as_of = None
    if args.as_of:
        d = dt.date.fromisoformat(args.as_of)
        as_of = dt.datetime(d.year, d.month, d.day, 23, 59, tzinfo=dr._tz())
    summary: dict = {"status": "ok", "dry_run": args.dry_run, "model": cfg.model, "ctx_tokens": cfg.ctx_tokens, "chunk_tokens": cfg.chunk_tokens,
                     "max_calls": cfg.max_calls, "prompt_sha": dr.prompt_sha(), "days": []}
    from db_pool import close_pool, init_pool

    try:
        await init_pool()
    except Exception as exc:  # noqa: BLE001
        return 1, {"status": "error", "setup_error": type(exc).__name__}
    dump: list = []
    try:
        users = [args.user] if args.user else await dr.members_with_recordings()
        for uid in users:
            for r in await dr.review_member(uid, cfg, days=args.date or None, as_of=as_of, dry_run=args.dry_run):
                if "candidates" in r:
                    dump.extend({"day": r["day"], **c} for c in r.pop("candidates"))
                for raw in r.pop("replies", []):
                    dump.append({"day": r["day"], "reply": raw})
                summary["days"].append(r)
                if r["status"] == "llm_unreachable":
                    summary["status"] = "llm_unreachable"
                    break
    finally:
        await close_pool()
    if args.dump:
        p = pathlib.Path(args.dump).expanduser()
        p.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.fchmod(fd, 0o600)          # O_TRUNC keeps an existing file's mode: a reused path must not stay world-readable
        with os.fdopen(fd, "w") as f:
            json.dump(dump, f, indent=1)
    if not summary["days"]:
        summary["status"] = "nothing_to_do"
    code = 2 if summary["status"] == "llm_unreachable" else 1 if any(d["status"] == "error" for d in summary["days"]) else 0
    return code, summary


def main(argv: "list[str] | None" = None) -> int:
    import logging

    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(levelname)s %(name)s: %(message)s")
    code, summary = asyncio.run(amain(argv))
    print(json.dumps(summary, separators=(",", ":")))
    tot = summary.get("days") or []
    print(f"NIGHT_JOB omi_day_review status={summary['status']} days={len(tot)} calls={sum(d.get('calls', 0) for d in tot)} "
          f"stored={sum(d.get('stored', 0) for d in tot)} no_time={sum(1 for d in tot if d.get('status') == 'no_time')} "
          f"skipped={sum(1 for d in tot if not d.get('calls') and d.get('status') not in ('no_time', 'error', 'llm_unreachable'))}", file=sys.stderr)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
