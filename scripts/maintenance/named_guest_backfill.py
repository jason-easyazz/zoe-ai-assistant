#!/usr/bin/env python3
"""Owner/operator BACKFILL: make a named guest's panel voice print from saved panel clips (``named_guest_backfill``).

For a guest who talked to the panel before named guests were switched on: pick their clips from the replay corpus
(``~/.zoe-voice-samples``) and this embeds each with the panel's own speaker model, refuses any clip that sounds like a member, drops
outliers, and stores the print exactly as the panel's "learn Quinn's voice" enrolment would (the owner's own person,
``channel='panel'``, guest scope only). Runbook: ``docs/knowledge/named-guests.md`` "Backfill from saved clips".

    # score only, write nothing (always do this first)
    python3 scripts/maintenance/named_guest_backfill.py --owner <member user id> --name Quinn --dry-run CLIP.wav [CLIP.wav ...]
    # store (needs ZOE_NAMED_GUESTS=on in this process's environment, as in zoe-data's)
    python3 scripts/maintenance/named_guest_backfill.py --owner <member user id> --name Quinn CLIP.wav [CLIP.wav ...]

Run ON THE JETSON from the live checkout after the PR lands (the pinned model is in ``~/.cache/zoe-speaker-resnet34``, override with
``ZOE_OMI_TAG_MODEL_DIR``); the database comes from zoe-data's ``.env`` (``runtime_env``). Prints one JSON report: per clip
``kept`` | ``unreadable`` | ``too_short`` | ``member`` | ``outlier`` with its scores. Exit 0 = stored (or dry run with enough clips),
2 = not enough clips kept, 3 = a refusal / setup error. Clips are read, never copied, uploaded or sent anywhere.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "services" / "zoe-data"))


async def _main(args) -> int:
    import runtime_env

    runtime_env.bootstrap_runtime_env()
    # The forget ledger's salt is not a bootstrap key: without it ``memory_forgotten.matches`` answers "not forgotten" and a
    # forgotten guest would be backfilled over the shield. Load it the way the service gets it, and refuse a real run without it.
    import memory_forgotten as mf

    runtime_env.load_env_keys((mf.SALT_ENV,))
    if not args.dry_run and not mf.configured():
        print(json.dumps({"status": "error", "error": f"the forget ledger is not configured ({mf.SALT_ENV} missing or too short): nothing was stored"}))
        return 3
    import named_guest_backfill as ngb
    import named_guest_enrol as nge
    import omi_diarize
    from db_pool import close_pool, init_pool

    embedder = omi_diarize.load_embedder()
    if embedder is None:
        print(json.dumps({"status": "error", "error": "the panel speaker model would not load (see OMI_TAG_UNAVAILABLE)"}))
        return 3
    try:
        await init_pool()
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"status": "error", "error": f"database: {type(exc).__name__}"}))
        return 3
    try:
        report = await ngb.run(args.owner, args.name, args.clips, embedder=embedder, dry_run=args.dry_run, min_kept=args.min_kept)
    except nge.GuestError as exc:
        print(json.dumps({"status": "refused", "error": str(exc)}))
        return 3
    finally:
        await close_pool()
    print(json.dumps({"status": "ok", **report}, indent=2))
    return 0 if report["enough"] else 2


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--owner", required=True, help="the member user id whose layer the guest lives in (an adult member)")
    ap.add_argument("--name", required=True, help="the guest's name as Zoe should say it")
    ap.add_argument("--min-kept", type=int, default=3, help="refuse with fewer kept clips than this (default 3)")
    ap.add_argument("--dry-run", action="store_true", help="score and report only; write nothing")
    ap.add_argument("clips", nargs="+", help="saved panel clips (16-bit PCM WAV)")
    return asyncio.run(_main(ap.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
