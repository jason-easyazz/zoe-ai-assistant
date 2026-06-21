#!/usr/bin/env python3
"""Replay saved room-audio WAVs through Zoe's real voice pipeline — offline.

Why: every fix used to need a manual re-test on the panel. The voice path saves
the actual captured utterances (ZOE_VOICE_SAVE_AUDIO=1 →
/home/zoe/.zoe-voice-samples). This harness feeds those exact WAVs back through
the REAL stages so a fix can be validated/regressed without speaking a word:

    STT (Moonshine v2)  →  semantic_router  →  Tier-0 detect_intent
                        →  expert_dispatch plan  →  reply  →  _clean_for_speech

By default it runs DRY: reads (weather/time/recall) are executed (side-effect
free), writes (calendar/lists/reminders) are only PLANNED + slot-extracted so we
can see what WOULD be created without mutating the DB on every run. Pass
--execute to actually fulfil writes.

Usage:
    python3 tests/replay_samples.py                 # all samples, dry
    python3 tests/replay_samples.py --since 0928    # only files named >= 0928xx
    python3 tests/replay_samples.py --last 26       # newest N
    python3 tests/replay_samples.py --execute       # really run writes
    python3 tests/replay_samples.py --json out.json # machine-readable dump
"""
import argparse
import asyncio
import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
os.chdir(ROOT)


def _load_env() -> None:
    """Load .env the way the service does (no secrets printed)."""
    p = os.path.join(ROOT, ".env")
    if not os.path.exists(p):
        return
    for line in open(p):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k, v.strip().strip('"').strip("'"))


def _select(sample_dir: str, args) -> list[str]:
    files = sorted(glob.glob(os.path.join(sample_dir, "*.wav")))
    if args.since:
        files = [f for f in files if os.path.basename(f) >= args.since]
    if args.last:
        files = files[-args.last:]
    return files


async def _run(args) -> int:
    _load_env()
    # Router/expert must be loadable; plan needs no "active" mode, but be explicit.
    os.environ.setdefault("ZOE_ROUTER_ENABLED", "1")

    from db_pool import init_pool
    await init_pool()

    import semantic_router as sr
    import expert_dispatch as xd
    import intent_router as ir
    from routers.voice_tts import _transcribe_audio_impl, _clean_for_speech
    from nlu_extractor import extract_slots_for_intent

    sr.warm()

    sample_dir = os.environ.get("ZOE_VOICE_SAMPLE_DIR") or "/home/zoe/.zoe-voice-samples"
    files = _select(sample_dir, args)
    if not files:
        print(f"no samples in {sample_dir}", file=sys.stderr)
        return 1

    print(f"Replaying {len(files)} sample(s) from {sample_dir}"
          f"{' [EXECUTE writes]' if args.execute else ' [dry]'}\n")
    rows = []
    for f in files:
        name = os.path.basename(f)
        rec: dict = {"file": name}
        try:
            transcript = (await _transcribe_audio_impl(f) or "").strip()
        except Exception as exc:
            transcript = f"<STT error: {exc}>"
        rec["transcript"] = transcript

        rr = sr.route(transcript) if transcript else {"domain": "chat", "score": 0.0, "routed": "chat"}
        rec["router"] = {"domain": rr.get("domain"), "score": rr.get("score"), "routed": rr.get("routed")}
        try:
            det = ir.detect_intent(transcript, log_miss=False)
            rec["tier0"] = det.name if det else None
        except Exception:
            rec["tier0"] = None

        domain = rr.get("domain")
        reply = None
        outcome = "→ brain"
        try:
            if domain and domain != "chat":
                plan = xd._plan(domain, transcript)
                if plan:
                    intent_name, _slots, kind = plan
                    rec["plan"] = {"intent": intent_name, "kind": kind}
                    if kind == "read":
                        ctx = {"user_id": "guest", "session_id": "replay", "score": rr.get("score", 0.0)}
                        res = await xd.dispatch(domain, transcript, ctx)
                        if res:
                            reply, outcome = res.reply, f"expert:{domain}:{res.intent}"
                    elif kind == "expert":
                        ctx = {"user_id": "guest", "session_id": "replay", "score": rr.get("score", 0.0)}
                        res = await xd.dispatch(domain, transcript, ctx)
                        if res:
                            reply, outcome = res.reply, f"expert:{domain}:{res.intent}"
                        else:
                            outcome = f"expert:{domain}:{intent_name} (store/none)"
                    else:  # write
                        if args.execute:
                            ctx = {"user_id": "guest", "session_id": "replay", "score": rr.get("score", 0.0)}
                            res = await xd.dispatch(domain, transcript, ctx)
                            if res:
                                reply, outcome = res.reply, f"WROTE {domain}:{res.intent}"
                            else:
                                outcome = f"write {intent_name} → extractor empty → brain"
                        else:
                            ex = await extract_slots_for_intent(intent_name, transcript)
                            rec["would_create"] = ex
                            outcome = f"would-create {intent_name} {ex}" if ex else \
                                      f"write {intent_name} → extractor empty → brain"
        except Exception as exc:
            outcome = f"<dispatch error: {exc}>"
        rec["outcome"] = outcome
        if reply:
            rec["reply"] = reply
            rec["spoken"] = _clean_for_speech(reply)
        rows.append(rec)

        # Human-readable line block
        print(f"● {name}")
        print(f"    heard : {transcript!r}")
        print(f"    route : {rr.get('domain')} ({rr.get('score')})   tier0={rec['tier0']}")
        if "plan" in rec:
            print(f"    plan  : {rec['plan']['intent']} [{rec['plan']['kind']}]")
        print(f"    => {outcome}")
        if reply:
            print(f"    spoken: {rec['spoken']!r}")
        print()

    if args.json:
        json.dump(rows, open(args.json, "w"), indent=2)
        print(f"wrote {args.json}")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", help="only files whose name sorts >= this (e.g. 0928)")
    ap.add_argument("--last", type=int, help="only the newest N samples")
    ap.add_argument("--execute", action="store_true", help="actually fulfil writes (mutates DB)")
    ap.add_argument("--json", help="also dump machine-readable results here")
    args = ap.parse_args()
    sys.exit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
