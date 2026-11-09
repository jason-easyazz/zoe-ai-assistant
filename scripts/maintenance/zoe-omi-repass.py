#!/usr/bin/env python3
"""The pendant NIGHT RE-PASS (services/zoe-data/omi_repass.py) as a standalone process, plus the owner's wearer-print commands.

The night window's job ``omi_repass`` runs this BEFORE the 12B loads (the 4B brain and Kokoro are stopped; nothing else needs the RAM):

    zoe-omi-repass.py --budget-s 1500                 # the night lane: faster-whisper + ECAPA over the recordings since the last pass
    zoe-omi-repass.py --dry-run                       # what is waiting and which model the budget would pick; writes nothing
    zoe-omi-repass.py --probe-gpu                     # what the GPU path could do on this stack (one JSON line); changes nothing

The OWNER's side of "who is me" (run it yourself; the listing holds what was said, so it is never pasted into the repo, a PR or a comment):

    zoe-omi-repass.py --wearer-status --user ID       # confirmed windows vs needed, and the heuristic proposals waiting
    zoe-omi-repass.py --wearer-lines --user ID --proposal 2026-11-10    # what was said in a proposal's windows: was that you?
    zoe-omi-repass.py --confirm-wearer 2026-11-10 --user ID             # yes, that was me: those windows become your pendant print
    zoe-omi-repass.py --reject-wearer 2026-11-10 --user ID              # no: delete the proposal

Nothing makes a line "me" until windows are CONFIRMED (here, or by labelling a line "me" in the app). Flag ``ZOE_OMI_NIGHT_REPASS`` = off | shadow | on.
Output: ONE JSON line (counts only) on stdout and a ``NIGHT_JOB omi_repass ...`` line on stderr. Exit 0 for any data outcome, 3 a model is missing, 1 an error.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import subprocess
import sys
import time

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "services" / "zoe-data"))


async def amain(argv: "list[str] | None" = None) -> "tuple[int, dict]":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--budget-s", type=float, default=1500.0)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--probe-gpu", action="store_true")
    ap.add_argument("--requeue-failed", action="store_true", help="after a fix: put recordings the pass gave up on (WAV still held) back in the queue")
    ap.add_argument("--wearer-status", action="store_true")
    ap.add_argument("--wearer-lines", action="store_true")
    ap.add_argument("--proposal", default="")
    ap.add_argument("--confirm-wearer", default="")
    ap.add_argument("--reject-wearer", default="")
    ap.add_argument("--user", default="")
    a = ap.parse_args(argv)
    import omi_repass as rp

    if a.probe_gpu:
        t0 = time.time()
        p = subprocess.run([rp.venv_python(), str(rp.worker_path()), "--probe-gpu"], capture_output=True, text=True, timeout=120, check=False)
        try:
            out = json.loads(p.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            return 3, {"status": "model_missing", "rc": p.returncode}
        return 0, {"status": "ok", **out, "probe_s": round(time.time() - t0, 1)}
    from db_pool import close_pool, init_pool

    try:
        await init_pool()
    except Exception as exc:  # noqa: BLE001
        return 1, {"status": "error", "setup_error": type(exc).__name__}
    try:
        if a.requeue_failed:
            return 0, {"status": "ok", "requeued": await rp.requeue_failed(a.user or None)}
        owner_cmds = a.wearer_status or a.wearer_lines or a.confirm_wearer or a.reject_wearer
        if owner_cmds:
            if not a.user:
                raise SystemExit("--user ID is required for the wearer commands")
            from db_compat import get_compat_db
            async with get_compat_db() as db:
                if a.confirm_wearer:
                    return 0, {"status": "ok", "confirmed": await rp.confirm_wearer(db, a.user, a.confirm_wearer)}
                if a.reject_wearer:
                    return 0, {"status": "ok", "rejected": await rp.reject_wearer(db, a.user, a.reject_wearer)}
                if a.wearer_lines:
                    if not a.proposal:
                        raise SystemExit("--wearer-lines needs --proposal ID")
                    for ln in await rp.proposal_lines(db, a.user, a.proposal):
                        print(f"  [{ln['at_ms'] // 1000}s] {ln['text']}", file=sys.stderr)
                    return 0, {"status": "ok"}
                return 0, {"status": "ok", **await rp.wearer_status(db, a.user)}
        return 0, await rp.run_night(budget_s=a.budget_s, dry_run=a.dry_run)
    finally:
        await close_pool()


def main() -> int:
    rc, summary = asyncio.run(amain())
    keys = ("status", "mode", "model", "pending", "done", "replaced", "shadow", "carried", "failed", "agree", "fits", "device")
    print("NIGHT_JOB omi_repass " + " ".join(f"{k}={summary[k]}" for k in keys if k in summary), file=sys.stderr)
    print(json.dumps(summary, sort_keys=True))
    if summary.get("status") == "model_missing":
        return 3
    return rc


if __name__ == "__main__":
    sys.exit(main())
