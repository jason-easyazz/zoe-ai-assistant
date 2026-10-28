#!/usr/bin/env python3
"""One COLD first voice turn, the way the panel produces it: evict the slot, wait out the quiet window, wake-warm, ask.

The replay harness times warm turns only, so it cannot see the regression that shipped three times (the wake warm no
longer matching the real first turn: ~6 s cold vs ~1.2 s warm). This measures that one turn through production code:

  1. evict   -- one unrelated tiny request, so llama-server's single slot stops holding the conversation prefix
                (what the post-turn background passes do);
  2. wait    -- the slot guard's quiet window (``brain_slot_guard.quiet_s()``), override ``--wait-s``;
  3. warm    -- ``zoe_flue_client.warm_kv_prefix(member)``, the exact call the panel's wake prewarm makes;
  4. ask     -- ``run_flue_brain_streaming`` as the same member, replay-isolated (writes reported, never committed),
                traffic class ``probe`` (so it never reaches the household alarms or the owner alert).

Prints ONE JSON object on stdout: {status: ok|skip|error, reason, member, warm, wait_s, first_prompt_n, first_cache_n,
cache_frac, ttft_ms}. ``prompt_n`` is the tokens RE-PREFILLED and ``cache_n`` the tokens reused, so cache_frac is
cache_n / (cache_n + prompt_n). One extra brain turn plus ~2 tiny requests; run it under the voice harness lock
(``scripts/maintenance/voice_regression_probe.py`` does). Needs the live service env and the Flue brain.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time

UTTERANCE = "Good evening, how are you going tonight?"


def _load_env(service_dir: str) -> None:
    path = os.path.join(service_dir, ".env")
    if not os.path.exists(path):
        return
    for line in open(path):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k, v.strip().strip('"').strip("'"))


async def measure(member: str, wait_s: float | None, gap_s: float) -> dict:
    import brain_dispatch
    import brain_slot_guard
    import zoe_flue_client as zfc

    out: dict = {"status": "ok", "reason": "", "member": member}
    if not brain_dispatch.use_flue_brain():
        return {**out, "status": "skip", "reason": "ZOE_BRAIN_BACKEND is not flue: no Flue first turn to measure"}
    wait = brain_slot_guard.quiet_s() + 2.0 if wait_s is None else float(wait_s)
    out["wait_s"] = round(wait, 1)

    # 1. evict: a request that does not extend the conversation prompt replaces the slot's content
    try:
        await zfc._kv_warm_turn("probe-cold-evict", {}, "Reply with the single word: ok.")
    except Exception as exc:  # noqa: BLE001
        return {**out, "status": "error", "reason": f"evict request failed: {type(exc).__name__}: {exc}"}
    # 2. wait out the quiet window
    await asyncio.sleep(max(0.0, wait))
    # 3. the panel's wake prewarm, then the gap between the wake word and the finished utterance
    out["warm"] = await zfc.warm_kv_prefix(member)
    await asyncio.sleep(max(0.0, gap_s))
    # 4. the first real turn
    seen: dict = {}
    orig = zfc._log_prompt_cache

    def _spy(session_id, terminal):
        try:
            rounds = terminal.get("prompt_cache")
            if isinstance(rounds, list) and rounds and isinstance(rounds[0], dict):
                seen["prompt_n"] = int(rounds[0].get("prompt_n") or 0)
                seen["cache_n"] = int(rounds[0].get("cache_n") or 0)
        except Exception:  # noqa: BLE001
            pass
        return orig(session_id, terminal)

    zfc._log_prompt_cache = _spy
    t0 = time.monotonic()
    ttft = None
    try:
        async for chunk in zfc.run_flue_brain_streaming(
                UTTERANCE, f"voice-panel-probe-cold-{int(t0)}", member, replay_isolation=True):
            if ttft is None and chunk:
                ttft = (time.monotonic() - t0) * 1000.0
    except Exception as exc:  # noqa: BLE001
        return {**out, "status": "error", "reason": f"first turn failed: {type(exc).__name__}: {exc}"}
    finally:
        zfc._log_prompt_cache = orig
    if "prompt_n" not in seen or ttft is None:
        return {**out, "status": "error",
                "reason": "the sidecar reported no prompt_cache for the first turn (older sidecar, or no reply)"}
    total = seen["prompt_n"] + seen["cache_n"]
    return {**out, "first_prompt_n": seen["prompt_n"], "first_cache_n": seen["cache_n"],
            "cache_frac": round(seen["cache_n"] / total, 4) if total > 0 else 0.0, "ttft_ms": round(ttft, 1)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--service-dir", required=True)
    ap.add_argument("--user", default="jason", help="the panel's likely speaker (member id)")
    ap.add_argument("--wait-s", type=float, default=None, help="quiet window to wait (default: slot guard quiet_s + 2)")
    ap.add_argument("--gap-s", type=float, default=1.5, help="wake word -> end of utterance gap before the turn")
    args = ap.parse_args()
    os.environ["ZOE_TRAFFIC_CLASS"] = "probe"
    sys.path.insert(0, args.service_dir)
    os.chdir(args.service_dir)
    _load_env(args.service_dir)
    try:
        result = asyncio.run(measure(args.user, args.wait_s, args.gap_s))
    except Exception as exc:  # noqa: BLE001
        result = {"status": "error", "reason": f"{type(exc).__name__}: {exc}"}
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
