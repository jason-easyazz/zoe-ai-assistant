#!/usr/bin/env python3
"""One COLD first voice turn, the way the panel produces it: evict the slot, wait out the quiet window, wake-warm, ask.

The replay harness times warm turns only, so it cannot see the regression that shipped three times (the wake warm no
longer matching the real first turn: ~6 s cold vs ~1.2 s warm). This measures that one turn through production code:

  1. evict   -- one unrelated tiny request, so llama-server's single slot stops holding the conversation prefix
                (what the post-turn background passes do);
  2. wait    -- a fixed pause (``DEFAULT_WAIT_S``, 47 s), override ``--wait-s``. Not the slot guard's quiet window: this
                process never takes the guard, so the wait only simulates a pause, and tracking ``quiet_s()`` (180 s)
                would make every probe run 3 minutes slower;
  3. warm    -- ``zoe_flue_client.warm_kv_prefix(likely)``, the exact call the panel's wake prewarm makes, where ``likely`` comes
                ONLY from the persisted speaker memo read as a freshly restarted process would (``_restart_memo``), never
                from the ``--user`` argument directly;
  4. ask     -- ``_run_flue_brain_streaming_turn`` (the envelope/prompt path of every real turn, BELOW the wrapper that
                claims the member's day brief and proactive raise: those write household state and have no dry-run mode)
                as the same member, replay-isolated (tool writes reported, never committed), traffic class ``probe``
                (so it never reaches the household alarms or the owner alert).

Prints ONE JSON object on stdout: {status: ok|skip|error, reason, member, warm, memo_restart, wait_s, first_prompt_n, first_cache_n,
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

DEFAULT_WAIT_S = 47.0   # the pause the stage was built and baselined with (the old 45 s quiet window + 2)
UTTERANCE = "Good evening, how are you going tonight?"


def _is_reply_text(chunk: object) -> bool:
    """A real reply-text delta: not empty, not a __TOOL__ / __THINKING__ phase sentinel."""
    return (isinstance(chunk, str) and chunk.strip() != ""
            and not chunk.startswith(("__TOOL__:", "__THINKING__:")))


def _quiet_gate():
    """The shared harness quiet-hours gate (scripts/maintenance/harness_quiet.py), loaded by path (scripts/ is no package)."""
    import importlib.util

    mod = sys.modules.get("harness_quiet")
    if mod is None:
        from pathlib import Path

        spec = importlib.util.spec_from_file_location(
            "harness_quiet", Path(__file__).resolve().parents[1] / "maintenance" / "harness_quiet.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules["harness_quiet"] = mod
        spec.loader.exec_module(mod)
    return mod.check("measure_cold_first_turn")


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
    """Open the DB pool the live service has (the manner block's adult lookup and the persona snapshot FAIL CLOSED
    without it, dropping the member's blocks from the warm AND the turn), measure, close it."""
    import db_pool

    try:
        await db_pool.init_pool()
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "reason": f"db pool unavailable ({type(exc).__name__}): the member's prompt blocks "
                                              "would be dropped, so this is not the panel's prompt", "member": member}
    try:
        return await _measure(member, wait_s, gap_s)
    finally:
        try:
            await db_pool.close_pool()
        except Exception:  # noqa: BLE001
            pass


PROBE_PANEL = "probe-cold-panel"


def _restart_memo(member: str, zfc) -> "tuple[str, str]":
    """Record ``member`` as the probe panel's speaker in a THROWAWAY state file, then look the memo up the way a fresh process
    does (in-process memos emptied, only the file left). Returns (likely speaker, error). Never touches the live state file."""
    import tempfile

    import warm_speaker

    key = "ZOE_WARM_SPEAKER_STATE"
    saved = os.environ.get(key)
    with tempfile.TemporaryDirectory(prefix="zoe-cold-probe-") as d:
        os.environ[key] = os.path.join(d, "warm-speaker.json")
        try:
            warm_speaker.reset()
            warm_speaker.note(PROBE_PANEL, member, sync=True)
            warm_speaker.reset()                                   # the process restarts: only the file remains
            getattr(zfc, "_LAST_TURN", {}).clear()                 # ... and so does the turn memo the warm replays
            likely = warm_speaker.get(PROBE_PANEL)
        finally:
            warm_speaker.reset()
            if saved is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = saved
    if member and not likely:
        return "", ("the persisted speaker memo did not survive a simulated restart (warm_speaker.get returned nothing): the "
                    "wake warm would render the guest envelope, not the member's")
    return likely, ""


async def _measure(member: str, wait_s: float | None, gap_s: float) -> dict:
    import brain_dispatch
    import zoe_flue_client as zfc

    out: dict = {"status": "ok", "reason": "", "member": member}
    # The panel streams: sentence TTS starts on the first delta. Without the stream flag the turn yields the whole reply
    # at once and "time to first delta" would be the full generation time.
    os.environ["ZOE_FLUE_STREAM_ENABLED"] = "1"
    if not brain_dispatch.use_flue_brain():
        return {**out, "status": "skip", "reason": "ZOE_BRAIN_BACKEND is not flue: no Flue first turn to measure"}
    wait = DEFAULT_WAIT_S if wait_s is None else float(wait_s)
    out["wait_s"] = round(wait, 1)

    # 1. evict: a request that does not extend the conversation prompt replaces the slot's content
    try:
        # replay-isolated like every other request this script sends: even a tool call from the model would not commit
        evicted = await zfc._kv_warm_turn("probe-cold-evict", {}, zfc._wrap_message_with_replay("Reply with the single word: ok.", True))
    except Exception as exc:  # noqa: BLE001
        return {**out, "status": "error", "reason": f"evict request failed: {type(exc).__name__}: {exc}"}
    if not (isinstance(evicted, tuple) and evicted and evicted[0] == "warmed"):
        # an eviction that did not complete leaves the conversation in the slot: the next turn would be warm, not cold
        return {**out, "status": "error", "reason": f"the eviction request did not complete (outcome {evicted!r})"}
    # 2. wait out the quiet window
    await asyncio.sleep(max(0.0, wait))
    # 3. the panel's wake prewarm, then the gap between the wake word and the finished utterance. The warm is NOT told the
    #    member: like a freshly restarted zoe-data, it has only the persisted speaker memo (warm_speaker) to go on, and the
    #    in-process turn memo is empty. 2026-10-28: a memo that did not survive a restart warmed the guest envelope and the
    #    member's first reply re-prefilled ~1k tokens, which a probe that named the member could not see.
    likely, why = _restart_memo(member, zfc)
    if why:
        return {**out, "status": "error", "reason": why}
    out["memo_restart"] = True
    out["warm"] = await zfc.warm_kv_prefix(likely)
    await asyncio.sleep(max(0.0, gap_s))
    # 4. the first real turn
    seen: dict = {}
    orig = zfc._log_prompt_cache

    def _spy(session_id, terminal):
        try:
            rounds = terminal.get("prompt_cache")
            if isinstance(rounds, list) and rounds and isinstance(rounds[0], dict):
                if rounds[0].get("prompt_n") is not None and rounds[0].get("cache_n") is not None:   # absent != 0
                    seen["prompt_n"] = int(rounds[0]["prompt_n"])
                    seen["cache_n"] = int(rounds[0]["cache_n"])
        except Exception:  # noqa: BLE001
            pass
        return orig(session_id, terminal)

    # the member blocks the live prompt carries, computed through the same providers the turn calls
    import manner_block

    # Distinguish a CONFIRMED ineligible speaker (a guest, a child: an empty block is the panel's prompt) from a failed
    # lookup (which fails closed to the same empty block and must not be measured as if it were the panel's prompt).
    adult = False
    if manner_block.enabled() and not manner_block.is_guest(member):
        try:
            adult = bool(await manner_block._account_is_adult(member))
        except Exception as exc:  # noqa: BLE001
            return {**out, "status": "error", "reason": f"the adult lookup failed ({type(exc).__name__}): the manner "
                                                         "block cannot be told from an absent one"}
    expect_manner = bool(await zfc._manner_context_block(member, UTTERANCE))
    if adult and not expect_manner:
        return {**out, "status": "error", "reason": "manner blocks are on and this member is an adult, but none "
                                                     "rendered: the measured prompt is not the panel's"}
    expect_persona = bool(await zfc._persona_context_block(member))   # "" is legitimate (member held on the fixed persona)
    envelope: dict = {}
    orig_note = zfc._note_turn_envelope

    def _note(uid, session_id, outbound):
        envelope.update(zfc._leading_envelope_lines(outbound))
        return orig_note(uid, session_id, outbound)

    zfc._note_turn_envelope = _note
    zfc._log_prompt_cache = _spy
    t0 = time.monotonic()
    ttft = None
    try:
        async for chunk in zfc._run_flue_brain_streaming_turn(
                UTTERANCE, f"voice-panel-probe-cold-{int(t0)}", member, replay_isolation=True):
            if ttft is None and _is_reply_text(chunk):
                ttft = (time.monotonic() - t0) * 1000.0
    except Exception as exc:  # noqa: BLE001
        return {**out, "status": "error", "reason": f"first turn failed: {type(exc).__name__}: {exc}"}
    finally:
        zfc._log_prompt_cache = orig
        zfc._note_turn_envelope = orig_note
    if expect_manner and not envelope.get("manner"):
        return {**out, "status": "error", "reason": "the measured turn carried no manner block although one is enabled "
                                                     "for this member: not the panel's prompt"}
    if expect_persona and not envelope.get("persona"):
        return {**out, "status": "error", "reason": "the measured turn carried no persona block although one renders "
                                                     "for this member: not the panel's prompt"}
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
    ap.add_argument("--wait-s", type=float, default=None, help="wait before the cold first turn (default: 47 s fixed, independent of the slot guard quiet window)")
    ap.add_argument("--gap-s", type=float, default=1.5, help="wake word -> end of utterance gap before the turn")
    args = ap.parse_args()
    if os.environ.get("ZOE_PERF") != "1":   # like every scripts/perf harness: opt-in, it sends real brain turns
        print(json.dumps({"status": "error", "reason": "set ZOE_PERF=1 (this sends real brain requests)"}))
        return 0
    # Harness quiet hours (binding, scripts/AGENTS.md): this evicts the household's cached prefix and queues brain work, so
    # a DIRECT invocation obeys the shared gate too. Its landing exemption (ZOE_HARNESS_LANDING=1, inherited from the lander)
    # and the owner's ZOE_HARNESS_FORCE=1 keep their meaning; a refusal is an unmeasured run, i.e. an error, never a pass.
    verdict = _quiet_gate()
    if not verdict.allowed:
        print(json.dumps({"status": "error", "reason": f"refused by harness quiet hours: {verdict.reason}"}))
        return 0
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
