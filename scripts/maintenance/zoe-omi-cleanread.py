#!/usr/bin/env python3
"""Run the pendant CLEAN READ (services/zoe-data/omi_repass_clean.py) as a standalone process: a lightly cleaned DISPLAY twin of each speaker turn, stored beside
the verbatim. The night window's job ``omi_cleanread`` runs this on the 12B before the day review:

    zoe-omi-cleanread.py --model-url http://127.0.0.1:11500/v1 --budget-s 540            # the night lane
    zoe-omi-cleanread.py --model-url http://127.0.0.1:11434/v1 --budget-s 300            # by hand on the 4B (the daytime server) if the 12B stage was skipped

Flags
  --model-url URL      the llama-server (default $GEMMA_SERVER_URL). Loopback only unless --allow-remote.
  --fallback-url URL   a second server tried for a turn when --model-url fails (e.g. the 4B); never required.
  --model-name NAME    the model name sent.
  --budget-s S         seconds this process may spend (the window passes what it has left); the rest carries to the next night.
  --limit N            at most N turns this run.
Off unless ZOE_OMI_CLEAN_READ=on. ONE T=0 request per turn of 6+ words; the answer is shown only if the code guard passes (an in-order subsequence of the verbatim,
<= 15% dropped, no dropped run > 4 words); otherwise the deterministic cleaner, otherwise the verbatim marked "(lightly cleaned)". Memory, extraction, the day review
and the ledger never read the clean twin.
Output: ONE NIGHT_JOB line, counts only - never what anyone said; it ends in ``ALERT=...`` when the guard-violation rate passes 20% (the morning report prints it).
Exit 0 always except a setup error (1): an unreachable model degrades to the deterministic cleaner, it does not fail the night.
"""
from __future__ import annotations

import argparse
import asyncio
import ipaddress
import os
import pathlib
import sys
import urllib.parse

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "services" / "zoe-data"))


def _loopback(url: str, allow_remote: bool) -> str:
    host = urllib.parse.urlparse(url).hostname or ""
    try:
        ok = host == "localhost" or ipaddress.ip_address(host).is_loopback
    except ValueError:
        ok = False
    if url and not ok and not allow_remote:
        raise SystemExit(f"refusing the non-loopback url {url!r}")
    return url


def line(summary: dict) -> str:
    s = summary
    out = (f"NIGHT_JOB omi_cleanread status={s['status']} turns={s.get('turns', 0)} llm={s.get('llm', 0)} short={s.get('short', 0)} violations={s.get('violations', 0)} "
           f"sent={s.get('sent', 0)} unreachable={s.get('unreachable', 0)} violation_rate={s.get('violation_rate', 0.0):.3f} left={s.get('left', 0)}")
    if s.get("alert"):
        out += f" ALERT=guard_violation_rate_{s['violation_rate'] * 100:.0f}pct_over_20pct_the_model_is_rewriting_turns"
    return out


async def amain(argv: "list[str] | None" = None) -> "tuple[int, dict]":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--model-url", default=os.environ.get("GEMMA_SERVER_URL", ""))
    ap.add_argument("--fallback-url", default="")
    ap.add_argument("--model-name", default=os.environ.get("MEMORY_DIGEST_MODEL", ""))
    ap.add_argument("--budget-s", type=float, default=0.0)
    ap.add_argument("--limit", type=int, default=2000)
    ap.add_argument("--allow-remote", action="store_true")
    args = ap.parse_args(argv)

    import omi_repass_clean as oc
    from typed_env import env_str

    if env_str("ZOE_OMI_CLEAN_READ", "off").strip().lower() != "on":
        return 0, {"status": "flag_off"}
    from db_pool import close_pool, init_pool
    try:
        await init_pool()
    except Exception as exc:  # noqa: BLE001
        return 1, {"status": "error", "setup_error": type(exc).__name__}
    primary = oc.llm_from_url(_loopback(args.model_url, args.allow_remote), args.model_name) if args.model_url else None
    second = oc.llm_from_url(_loopback(args.fallback_url, args.allow_remote), args.model_name) if args.fallback_url else None

    async def llm(text: str):
        if primary is None:
            return None
        try:
            return await primary(text)
        except Exception:  # noqa: BLE001 - the 12B did not answer: the second server, else the deterministic cleaner
            if second is None:
                raise
            return await second(text)

    try:
        summary = await oc.run(llm=llm if (primary or second) else None, budget_s=args.budget_s, limit=args.limit)
    finally:
        await close_pool()
    return 0, summary


def main() -> int:
    rc, summary = asyncio.run(amain())
    print(line(summary) if summary.get("status") != "error" else f"NIGHT_JOB omi_cleanread status=error {summary.get('setup_error', '')}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
