#!/usr/bin/env python3
"""lane_parity.py - the SAME utterance through the typed chat path and the spoken voice path, side by side.

Why: Zoe's panel is the voice-first surface, and the typed lane kept getting tiers the spoken lane did not (register G11 / felt gap 7).
A ledger line said so; nothing measured it. This runs the REAL `routers.chat.chat_stream_generator` and the REAL
`routers.voice_tts.voice_command` (text-injected exactly as the Pi daemon posts it after STT) over a fake store and a stubbed model
transport, for each cell in `services/zoe-data/tests/lane_parity_cells.py`, and prints the table. A cell is PASS on a lane when the
household gets the capability (the stored fact changed, the contact exists, the claim was checked, the thumb exists), not when the two
reply strings are byte-equal. PARITY is both lanes PASS.

Isolation (nothing here can write to a live store): the database is an in-memory fake that RECORDS what it did not understand, the memory
palace is a throwaway directory, the model is a stub under the real Flue seam (so verify-on-challenge, the recall/offer/identity blocks
and the outbound message are real), TTS and broadcasts are no-ops, identities are `demo_lane_parity_*_<hex>`. No service is contacted,
no flag is written, no brain time is spent.

Usage (service interpreter or any python with the zoe-data deps; pytest is NOT needed):
    python3 scripts/perf/lane_parity.py                 # the table; exit 1 unless every cell has parity
    python3 scripts/perf/lane_parity.py --only correction,contact_list
    python3 scripts/perf/lane_parity.py --details       # why each cell passed or failed
    python3 scripts/perf/lane_parity.py --controls      # the instrument's own proof: a tier OFF must turn its cell red (exit 2 if not)
    python3 scripts/perf/lane_parity.py --json out.json

Exit: 0 every cell has parity (and, with --controls, every control went red) | 1 a cell lacks parity | 2 an instrument control stayed green.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SERVICE = REPO / "services" / "zoe-data"

#: flag controls: turning the tier off must turn these cells red (on BOTH lanes: the rows are lane-neutral)
CONTROLS = (
    ({"ZOE_CORRECTION_APPLY": "0"}, ("correction",)),
    ({"ZOE_VERIFY_ON_CHALLENGE": "0"}, ("verify_on_challenge",)),
    ({"ZOE_CONVERSATION_FEEDBACK": "0"}, ("feedback_down", "feedback_up")),
    ({"ZOE_MEMORY_PROVENANCE_ANSWERS": "0"}, ("provenance", "forget_it")),
)


def _pin_stores() -> str:
    """The conftest's pins, before any zoe-data module imports: no per-user LIVE store is reachable from this process."""
    root = tempfile.mkdtemp(prefix="zoe-lane-parity-")
    os.environ["MEMPALACE_DATA_DIR"] = os.path.join(root, "mempalace")
    os.environ["ZOE_VOICE_STT_LOG"] = os.path.join(root, "voice_stt.jsonl")
    os.environ["ZOE_MEMORY_REJECT_LEDGER"] = os.path.join(root, "memory-reject-ledger.json")
    os.environ["ZOE_STRUCTURAL_VERIFIER"] = "off"
    for p in (str(SERVICE / "tests"), str(SERVICE)):
        if p not in sys.path:
            sys.path.insert(0, p)
    import exact_words
    import night_store

    exact_words.set_backend(exact_words.MemoryBackend())
    night_store.set_backend(night_store.MemoryBackend())
    return root


async def _main(args) -> int:
    root = _pin_stores()
    try:
        import lane_parity_cells as lpc
        from lane_parity_rig import MiniPatch

        only = {c for c in (args.only or "").split(",") if c} or None
        known = {c.cid for c in lpc.CELLS}
        if only and not only <= known:
            print(f"unknown cell(s): {sorted(only - known)}; known: {sorted(known)}", file=sys.stderr)
            return 2
        results = await lpc.run_cells(MiniPatch, only=only)
        print(lpc.render(results))
        if args.details:
            for r in results:
                print(f"\n[{r.cid}] {r.title}\n  chat : {r.chat_detail}\n  voice: {r.voice_detail}")
        bad_controls = []
        if args.controls:
            print("\ninstrument controls (a tier turned OFF must turn its cells red):")
            for flags, cells in CONTROLS:
                res = await lpc.run_cells(MiniPatch, only=set(cells), flags=flags)
                red = all((r.chat_ok is False and r.voice_ok is False) for r in res)
                print(f"  {','.join(f'{k}={v}' for k, v in flags.items()):42} -> {','.join(cells):32} {'RED (good)' if red else 'STILL GREEN (instrument broken)'}")
                if not red:
                    bad_controls.append(flags)
        if args.json:
            Path(args.json).write_text(json.dumps([r.__dict__ | {"parity": r.parity} for r in results], indent=2))
        if bad_controls:
            return 2
        return 0 if all(r.parity for r in results) else 1
    finally:
        shutil.rmtree(root, ignore_errors=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--only", help="comma-separated cell ids")
    ap.add_argument("--details", action="store_true")
    ap.add_argument("--controls", action="store_true")
    ap.add_argument("--json")
    args = ap.parse_args()
    import logging

    logging.disable(logging.CRITICAL)
    sys.exit(asyncio.run(_main(args)))


if __name__ == "__main__":
    main()
