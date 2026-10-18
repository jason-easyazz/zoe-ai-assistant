#!/usr/bin/env python3
"""Run the struggle PROPOSALS pass (services/zoe-data/struggle_proposals.py) as a standalone process, and decide proposals by hand.

The night window's job ``struggle_proposals`` runs this on the 12B after the digest, dreaming and the night mind (best effort):

    zoe-struggle-proposals.py --model-url http://127.0.0.1:11500/v1 --ctx-tokens 8192 [--decode-tok-s X]   # the night lane
    zoe-struggle-proposals.py --dry-run [--days 30] [--min-episodes 2]       # cluster + count, NOTHING written, no model call
    zoe-struggle-proposals.py --no-model                                     # store proposals with the template sentence
    zoe-struggle-proposals.py --list [--status proposed] [--kind intent_gap] # what is stored (counts and shapes, never words)
    zoe-struggle-proposals.py --accept ID [--reason CODE] | --reject ID [--reason CODE]

CODE does the grouping, counting and thresholds; the model only rewords the counts as one sentence, and it is shown nothing else
(no utterance, no name, no id). With ``ZOE_STRUGGLE_PROPOSALS=off`` the pass is a no-op (a ``--dry-run`` still counts). Accepting a
proposal applies NOTHING: a ``router_example`` goes to the router self-train queue (Phase 3 reads it), any other kind becomes a task
stub whose ledger line is printed here.

Output: a ``NIGHT_JOB struggle_proposals ...`` line (counts only) then ONE JSON line (counts / the proposal's public view). Exit 0 ok /
nothing to do / flag off, 1 error, 2 the database could not be read. Needs the zoe-data environment (POSTGRES_URL).
"""
from __future__ import annotations

import argparse
import asyncio
import ipaddress
import json
import os
import sys
import urllib.parse
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "services" / "zoe-data"))


def _loopback(url: str, allow_remote: bool) -> str:
    host = urllib.parse.urlparse(url).hostname or ""
    try:
        ok = host == "localhost" or ipaddress.ip_address(host).is_loopback
    except ValueError:
        ok = False
    if url and not ok and not allow_remote:
        raise SystemExit(f"refusing the non-loopback --model-url {url!r}")
    return url


def make_llm(base_url: str, model: str, decode_tok_s: float, prefill_tok_s: float):
    """``async (messages, max_tokens) -> str`` against a llama-server. A connection failure raises ``ConnectionError`` (the pass then stops asking)."""
    base = base_url.rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]

    async def llm(messages: list, max_tokens: int) -> str:
        import httpx

        prompt_tokens = sum(len(m["content"]) for m in messages) // 3 + 16
        timeout = 20.0 + max_tokens / max(0.5, decode_tok_s) * 1.5 + prompt_tokens / max(20.0, prefill_tok_s)
        payload = {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": 0.1, "stream": False}
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=8.0)) as client:
                resp = await client.post(f"{base}/v1/chat/completions", json=payload)
                resp.raise_for_status()
                return str(resp.json()["choices"][0]["message"]["content"] or "")
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise ConnectionError(type(exc).__name__) from exc
    return llm


async def amain(argv: "list[str] | None" = None) -> "tuple[int, dict]":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--model-url", default="")
    ap.add_argument("--model-name", default="")
    ap.add_argument("--ctx-tokens", type=int, default=8192)       # accepted for the window's contract; the prompts here are tiny
    ap.add_argument("--decode-tok-s", type=float, default=0.0)
    ap.add_argument("--prefill-tok-s", type=float, default=0.0)
    ap.add_argument("--no-model", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--days", type=float, default=0.0)
    ap.add_argument("--min-episodes", type=int, default=0)
    ap.add_argument("--budget-s", type=float, default=240.0)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--status", default="")
    ap.add_argument("--kind", default="")
    ap.add_argument("--show", default="")
    ap.add_argument("--accept", default="")
    ap.add_argument("--reject", default="")
    ap.add_argument("--reason", default="", help="not_a_problem | wrong_label | already_fixed | not_now | other (a code, never free text)")
    ap.add_argument("--allow-remote", action="store_true")
    args = ap.parse_args(argv)

    import struggle_proposals as sp
    from db_pool import close_pool, init_pool

    url = _loopback(args.model_url or os.environ.get("GEMMA_SERVER_URL", ""), args.allow_remote)
    try:
        await init_pool()
    except Exception as exc:  # noqa: BLE001
        return 2, {"status": "error", "setup_error": type(exc).__name__}
    try:
        try:
            if args.list:
                return 0, {"status": "ok", "mode": sp.mode(), "counts": await sp.counts(),
                           "proposals": await sp.list_proposals(status=args.status or None, kind=args.kind or None, limit=100)}
            if args.show:
                return 0, {"status": "ok", "proposal": await sp.get_proposal(args.show)}
            if args.accept or args.reject:
                p = await sp.decide(args.accept or args.reject, "accept" if args.accept else "reject", "cli", args.reason)
                return 0, {"status": "ok", "proposal": p}
        except (sp.ProposalError, ValueError) as exc:
            return 1, {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
        llm = None
        if not (args.no_model or args.dry_run) and url:
            llm = make_llm(url, args.model_name or os.environ.get("MEMORY_DIGEST_MODEL", ""), args.decode_tok_s or 5.0,
                           args.prefill_tok_s or 150.0)
        try:
            res = await sp.run_pass(llm=llm, dry_run=args.dry_run, days=args.days or None, min_ep=args.min_episodes or None,
                                    budget_s=args.budget_s)
        except Exception as exc:  # noqa: BLE001
            if sp._missing_table(exc):       # migration 0058 has not run yet (deploy order): nothing to do tonight, not a failed night
                return 0, {"status": "table_missing"}
            return 1, {"status": "error", "error": type(exc).__name__}
        return 0, res
    finally:
        await close_pool()


def main(argv: "list[str] | None" = None) -> int:
    rc, out = asyncio.run(amain(argv))
    import struggle_proposals as sp

    if "events" in out or out.get("status") in ("off", "error"):
        print(f"NIGHT_JOB struggle_proposals rc={rc} " + sp.summary_line(out).replace("STRUGGLE_PROPOSALS_SUMMARY ", ""))
    print(json.dumps(out, sort_keys=True, default=str))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
