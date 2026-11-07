#!/usr/bin/env python3
"""Run Zoe's nightly SELF-REVIEW (services/zoe-data/self_review.py) as a standalone process; read a night's private review.

The night window's job ``self_review`` runs this on the 12B (port 11500) after the struggle proposals, before the day review:

    zoe-self-review.py --model-url http://127.0.0.1:11500/v1 --ctx-tokens 8192 --budget-s 540   # the night lane
    zoe-self-review.py --dry-run --no-model          # gather + flag, NOTHING written, no model (any hour; works with the flag off)
    zoe-self-review.py --show 2026-11-08             # the owner: a night's records, re-checked against the forget ledger

Flags
  --model-url URL        the llama-server. Loopback only, no override (household words never leave the box); the live 4B's port
                         (11434) is REFUSED: the review never runs on the daytime brain (owner policy: the 12B night lane only).
                         --no-model runs the deterministic checks alone.
  --budget-s S           the seconds this process may spend (the window passes what it has left); a review the remaining time cannot
                         hold is not started (``no_time``). Token and call caps: ZOE_SELF_REVIEW_MAX_TOKENS / _MAX_CALLS.
  --night YYYY-MM-DD     the night label (default: today, Zoe's timezone). The window reviewed is since the last run (<= 36 h).
  --dry-run              no file, no row, no candidate is written (the flag may be off).
Output: a ``NIGHT_JOB self_review ...`` line on stderr, then ONE JSON line (counts only) on stdout. Exit 0 ok / nothing to do /
flag off, 1 error, 2 the model is unreachable.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import ipaddress
import json
import os
import sys
import time
import urllib.parse
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "services" / "zoe-data"))

LIVE_4B_PORT = 11434
DEFAULT_APP_LOG = Path.home() / ".zoe-logs" / "zoe-data.app.log"
DEFAULT_STT = Path.home() / ".zoe-voice" / "voice_stt.jsonl"
DEFAULT_FLUE_DB = Path(os.environ.get("ZOE_SELF_REVIEW_FLUE_DB", "") or REPO / "labs" / "flue-zoe-brain-2x" / "data" / "zoe-brain.db")


def check_model_url(url: str) -> str:
    """Loopback only (there is deliberately no override), and never the live daytime 4B on any host."""
    p = urllib.parse.urlparse(url)
    host = p.hostname or ""
    try:
        ok = host == "localhost" or ipaddress.ip_address(host).is_loopback
    except ValueError:
        ok = False
    if (p.port or 80) == LIVE_4B_PORT:
        raise SystemExit("refusing the live 4B (port 11434): the self-review runs on the 12B night lane only")
    if not ok:
        raise SystemExit(f"refusing the non-loopback --model-url {url!r}: household words never leave the box")
    return url


def make_llm(base_url: str, model: str, decode_tok_s: float, prefill_tok_s: float):
    base = base_url.rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]

    async def llm(messages: list, max_tokens: int) -> str:
        import httpx

        prompt_tokens = sum(len(m["content"]) for m in messages) // 3 + 16
        timeout = 20.0 + max_tokens / max(0.5, decode_tok_s) * 1.5 + prompt_tokens / max(20.0, prefill_tok_s)
        payload = {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": 0.2, "stream": False}
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=8.0)) as client:
                resp = await client.post(f"{base}/v1/chat/completions", json=payload)
                resp.raise_for_status()
                return str(resp.json()["choices"][0]["message"]["content"] or "")
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise ConnectionError(type(exc).__name__) from exc
    return llm


def _lines(path: Path) -> list:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.readlines()
    except OSError:
        return []


async def amain(argv: "list[str] | None" = None) -> "tuple[int, dict]":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--model-url", default="")
    ap.add_argument("--model-name", default="")
    ap.add_argument("--ctx-tokens", type=int, default=8192)          # the window's contract; the prompts here are small
    ap.add_argument("--decode-tok-s", type=float, default=0.0)
    ap.add_argument("--prefill-tok-s", type=float, default=0.0)
    ap.add_argument("--budget-s", type=float, default=0.0)
    ap.add_argument("--night", default="")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-model", action="store_true")
    ap.add_argument("--show", default="", metavar="NIGHT")
    ap.add_argument("--app-log", type=Path, default=DEFAULT_APP_LOG)
    ap.add_argument("--stt-log", type=Path, default=DEFAULT_STT)
    ap.add_argument("--flue-db", type=Path, default=DEFAULT_FLUE_DB)
    args = ap.parse_args(argv)
    started = time.monotonic()

    import self_review as sr

    if args.show:
        from db_pool import close_pool, init_pool

        await init_pool()
        try:
            recs = await sr.load_reviews(args.show)
        finally:
            await close_pool()
        for r in recs:
            print(json.dumps(r, sort_keys=True))
        return 0, {"status": "ok", "records": len(recs)}
    run_mode = sr.mode()
    if run_mode == "off" and not args.dry_run:
        return 0, {"status": "flag_off", "mode": run_mode}
    llm = None
    if not args.no_model:
        if not args.model_url:
            raise SystemExit("--model-url is required (or --no-model)")
        llm = make_llm(check_model_url(args.model_url), args.model_name or "night", args.decode_tok_s or 6.5,
                       args.prefill_tok_s or 180.0)
    budget = sr.Budget(deadline=(started + args.budget_s) if args.budget_s > 0 else 0.0, calls_left=sr.max_calls(),
                       tokens_left=sr.max_tokens(), decode_tok_s=args.decode_tok_s or 6.5, prefill_tok_s=args.prefill_tok_s or 180.0)
    now = time.time()
    night = args.night or dt.datetime.now(sr._tz()).date().isoformat()
    since = sr.window_start(now)
    from db_pool import close_pool, init_pool

    try:
        await init_pool()
    except Exception as exc:  # noqa: BLE001
        return 1, {"status": "error", "setup_error": type(exc).__name__}
    try:
        rows = await sr.load_chat_rows(since, now)
        struggles = await sr.load_struggles(since)
        inp = sr.NightInputs(rows=rows, log_lines=sr.read_log_segments(args.app_log), stt_lines=_lines(args.stt_log), struggles=struggles,
                             flue=await asyncio.to_thread(sr.flue_replies, args.flue_db, since, now), since=since, until=now)
        summary = await sr.run_night(inp, llm=llm, budget=budget, night=night, run_mode=run_mode if not args.dry_run else "shadow",
                                     dry_run=args.dry_run)
    except Exception as exc:  # noqa: BLE001
        return 1, {"status": "error", "error": type(exc).__name__}
    finally:
        await close_pool()
    summary.pop("events", None)                 # the stdout line is counts only (the events live in the 0600 summary file)
    summary["dry_run"] = args.dry_run
    if not summary.get("turns_household") and summary.get("status") == "ok":
        summary["status"] = "nothing_to_do"
    return (2 if summary.get("status") == "llm_unreachable" else 0), summary


def main(argv: "list[str] | None" = None) -> int:
    import logging

    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(levelname)s %(name)s: %(message)s")
    code, s = asyncio.run(amain(argv))
    print(json.dumps(s, separators=(",", ":"), sort_keys=True))
    print(f"NIGHT_JOB self_review status={s.get('status')} household={s.get('turns_household', 0)} flagged={s.get('flagged', 0)} "
          f"reviewed={s.get('reviewed', 0)} no_time={s.get('no_time', 0)} calls={s.get('calls', 0)}", file=sys.stderr)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
