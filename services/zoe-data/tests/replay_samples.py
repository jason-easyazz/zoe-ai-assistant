#!/usr/bin/env python3
"""Replay saved room-audio WAVs through Zoe's REAL voice pipeline — offline.

Every fix used to need a manual re-test on the panel. The voice path saves the
actual captured utterances (ZOE_VOICE_SAVE_AUDIO=1 → /home/zoe/.zoe-voice-samples).
This harness feeds those exact WAVs back through the SAME stages the live panel
runs so a fix can be validated/regressed without speaking a word, and reports
**what was said (STT) vs what actually happens** (route → reply/action):

    STT (Moonshine v2) → semantic_router → fast_tiers.resolve(channel="voice")
                       → (Gemma brain with the user's memory, on fall-through)

It mirrors LIVE voice: the voice channel profile (run_tier0=False, margin check,
defer_domains={people,memory}) and the real user, so people/memory recall + chat
go to the brain exactly as they do on the panel — not the old raw expert path.

Each turn is classified so capability gaps are obvious:
  OK       fast path or brain answered
  CANT_DO  asked for something Zoe couldn't fulfil (extractor empty, "I don't
           have that on file", "I can't…") — these are bugs to fix
  EMPTY    STT heard nothing (silence / clipped capture)
  ERROR    a stage raised

By default DRY: reads/recall run (side-effect free), writes are only PLANNED
(allow_writes=False → deferred) so the DB isn't mutated. --execute fulfils writes.
--brain actually runs the Gemma brain on fall-through (slower; needed to test
recall end-to-end). Default user is 'jason' so memory recall has facts.

Usage:
    python3 tests/replay_samples.py                  # all samples, dry, no brain
    python3 tests/replay_samples.py --brain          # run the brain on fall-through
    python3 tests/replay_samples.py --last 30        # newest N
    python3 tests/replay_samples.py --since 0928     # files named >= 0928xx
    python3 tests/replay_samples.py --execute        # really run writes
    python3 tests/replay_samples.py --json out.json  # machine-readable dump
"""
import argparse
import asyncio
import glob
import json
import os
import re
import sys
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
os.chdir(ROOT)

# The canonical "brain unreachable" fallback: the flue client NEVER raises on a
# failed brain turn — it yields this text instead (transport error, non-2xx, or
# an HTTP-200-but-empty result all surface identically). Imported, not
# duplicated, so the harness can't drift from the client's actual fallback.
from zoe_flue_client import _FALLBACK_TEXT as _BRAIN_FALLBACK_TEXT  # noqa: E402

# Replies that mean "I couldn't do what you asked" — a capability gap to fix.
# Targeted at capability DISCLAIMERS (no access / not on file / can't act), NOT
# conversational hedges like "I don't have one perfect answer" (meaning-of-life).
_CANT_DO_RE = re.compile(
    r"don'?t (?:\w+\s+){0,2}have access to|can'?t access|no access to (?:your|the)|"
    r"\bnot on file\b|don'?t have (?:it|that|your \w+) on file|"
    r"can'?t (?:tell you|find|create|add|set|schedule|do that|help with that)|"
    r"couldn'?t (?:find|create|add|set|schedule)|\bunable to\b|"
    r"you'?ll have to (?:check|do).{0,30}?yourself",
    re.IGNORECASE,
)


def _load_env() -> None:
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


def _run_session_id() -> str:
    """Fresh brain/flue session id per harness RUN (all samples in one run share
    it, preserving the within-run conversational continuity the old fixed id
    gave).

    A hardcoded "replay" id accumulated history ACROSS runs in the flue
    sidecar's durable session store until prompt assembly overflowed the model
    context (8288 > 8192 tokens → HTTP 500 on every turn; 2026-07-07 incident).
    Nothing reads the session between runs, so a per-run id loses no continuity.

    Uses a random token (not a wall-clock second): two runs launched within the
    same second — e.g. parallel CI workers — must not collide into one shared
    sidecar session and re-create the very accumulation this guards against.
    """
    return f"replay-{uuid.uuid4().hex[:12]}"


def _classify(transcript: str, reply: str, outcome: str) -> str:
    if not transcript:
        return "EMPTY"
    if outcome.startswith("<"):
        return "ERROR"
    if reply and _BRAIN_FALLBACK_TEXT in reply:
        # HARD failure: the brain lane silently degraded. The flue client
        # swallows sidecar failures and yields this fallback text instead of
        # raising, so without this check a dead brain still counted as OK and
        # weakened replay-gate evidence (2026-07-07 incident).
        return "ERROR"
    if reply and _CANT_DO_RE.search(reply):
        return "CANT_DO"
    if "extractor empty" in outcome:
        return "CANT_DO"
    if outcome == "brain" and not reply:
        return "CANT_DO"  # brain ran but produced no spoken text → couldn't answer
    if outcome == "→ brain":  # fast path deferred; brain not run — not a confirmed OK
        return "DEFERRED"
    return "OK"


def _transcribe_remote(wav_path: str, base_url: str, token: str) -> str:
    """STT via the LIVE zoe-data /api/voice/transcribe (its Moonshine is already warm).

    Why this exists: in-process STT lazy-loads a SECOND Moonshine copy
    (`_ensure_moonshine`) next to the one the live service already holds — the
    single biggest slice of the harness's 1500MB memory requirement, on a box
    where the gate skipped for days because that much was never free. STT is a
    pure function of the audio, so it is the one stage that can safely go remote:
    router/fast_tiers stay in-process because ONLY the harness runs them with
    allow_writes=False — the live endpoints would actually execute the commands.

    Trade-offs, stated: stt_ms gains localhost-HTTP overhead (~ms, dwarfed by the
    1-2s of STT itself). panel_id "replay-harness" makes the endpoint SUPPRESS its
    UI broadcasts (_suppress_ui_broadcast in voice_tts.py) — the first cut claimed
    kiosks filter on panel_id and they do not (websocket-sync.js reacts to every
    voice event unconditionally; Copilot caught the unverified claim), so without
    the server-side guard a nightly gate run would poke the house panels 20 times.
    Auth follows the zoe_latency_probe convention: ZOE_DEVICE_TOKEN (with
    DEVICE_TOKEN as the fallback name, matching that probe) from the environment
    only, never a CLI flag.
    """
    import base64 as _b64
    import urllib.request as _rq

    with open(wav_path, "rb") as fh:
        payload = json.dumps({
            "audio_base64": _b64.b64encode(fh.read()).decode("ascii"),
            "panel_id": "replay-harness",
        }).encode("utf-8")
    req = _rq.Request(
        base_url.rstrip("/") + "/api/voice/transcribe",
        data=payload,
        headers={"Content-Type": "application/json", "X-Device-Token": token},
        method="POST",
    )
    with _rq.urlopen(req, timeout=60) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    # A backend failure raises above (non-2xx -> HTTPError), matching the
    # in-process contract: silence returns "", real failure raises.
    return str(body.get("text") or "")


async def _run(args) -> int:
    _load_env()
    os.environ.setdefault("ZOE_ROUTER_ENABLED", "1")

    from db_pool import init_pool
    await init_pool()

    import semantic_router as sr
    import fast_tiers as ft
    import intent_router as ir
    from routers.voice_tts import (
        _transcribe_audio_impl, _clean_for_speech, _voice_brain_memory,
        _voice_domain_context, _merge_brain_context,
    )

    sr.warm()

    _remote_token = ""
    if args.stt == "remote":
        # Resolve the base HERE, after _load_env() has merged the service .env —
        # an argparse-time default reads the environment before that merge, so a
        # ZOE_BASE_URL that lives only in the .env (the normal case for the live
        # service) would be silently ignored in favour of loopback (Bugbot, #1572).
        if not args.base_url:
            args.base_url = os.environ.get("ZOE_BASE_URL") or "http://127.0.0.1:8000"
        _remote_token = (os.environ.get("ZOE_DEVICE_TOKEN")
                         or os.environ.get("DEVICE_TOKEN") or "").strip()
        if not _remote_token:
            print("--stt remote needs ZOE_DEVICE_TOKEN in the environment "
                  "(same convention as zoe_latency_probe; never a CLI flag)", file=sys.stderr)
            return 1

    user = args.user
    session_id = _run_session_id()
    sample_dir = os.environ.get("ZOE_VOICE_SAMPLE_DIR") or "/home/zoe/.zoe-voice-samples"
    files = _select(sample_dir, args)
    if not files:
        print(f"no samples in {sample_dir}", file=sys.stderr)
        return 1

    print(f"Replaying {len(files)} sample(s) from {sample_dir} as user={user}"
          f" session={session_id}"
          f"{' [EXECUTE writes]' if args.execute else ' [dry]'}"
          f"{' [+brain]' if args.brain else ''}\n")

    rows: list[dict] = []
    counts: dict[str, int] = {}
    for f in files:
        name = os.path.basename(f)
        rec: dict = {"file": name}

        t = time.monotonic()
        try:
            if args.stt == "remote":
                transcript = (await asyncio.to_thread(
                    _transcribe_remote, f, args.base_url, _remote_token) or "").strip()
            else:
                transcript = (await _transcribe_audio_impl(f) or "").strip()
        except Exception as exc:
            transcript = ""
            rec["stt_error"] = str(exc)
        rec["stt_ms"] = int((time.monotonic() - t) * 1000)
        rec["transcript"] = transcript

        rr = sr.route(transcript) if transcript else {"domain": "chat", "score": 0.0, "scores": {}}
        rec["router"] = {"domain": rr.get("domain"), "score": rr.get("score")}
        try:
            det = ir.detect_intent(transcript, log_miss=False)
            rec["tier0"] = det.name if det else None
        except Exception:
            rec["tier0"] = None

        reply = None
        outcome = "→ brain"
        if transcript:
            t = time.monotonic()
            try:
                # Mirror live voice: channel="voice" (margin + defer people/memory),
                # real router decision, dry keeps allow_writes off so writes defer.
                res = await ft.resolve(
                    transcript, user, session_id, channel="voice",
                    router_decision=rr, allow_writes=bool(args.execute),
                    extra_ctx={"panel_id": "replay"},
                )
            except Exception as exc:
                res = None
                outcome = f"<resolve error: {exc}>"
            rec["resolve_ms"] = int((time.monotonic() - t) * 1000)
            if res is not None:
                reply = res.reply
                outcome = f"{res.tier or 'tier1.5'}:{res.domain}:{res.intent}"
            elif args.brain and not outcome.startswith("<"):
                t = time.monotonic()
                try:
                    from brain_dispatch import brain_oneshot
                    db_mem, portrait = await _voice_brain_memory(user)
                    # Mirror live: inject calendar/lists/reminders context on deferral
                    # (#760) so a calendar follow-up isn't wrongly answered "I don't
                    # have access". The harness must match the live brain turn exactly.
                    domain_ctx = await _voice_domain_context(rr, user)
                    db_mem = _merge_brain_context(db_mem, domain_ctx)
                    reply = (await brain_oneshot(
                        transcript, session_id, user_id=user, voice_mode=True,
                        db_memory_context=db_mem, portrait=portrait,
                    ) or "").strip()
                    outcome = "brain"
                except Exception as exc:
                    outcome = f"<brain error: {exc}>"
                rec["brain_ms"] = int((time.monotonic() - t) * 1000)

        rec["outcome"] = outcome
        if reply:
            rec["reply"] = reply
            rec["spoken"] = _clean_for_speech(reply)
        # An STT failure is an ERROR, never an EMPTY: EMPTY means "the recording is
        # silence" and is excluded from the gate's denominator, so collapsing HTTP/
        # auth/timeout failures into it would let a half-dead endpoint quietly
        # shrink the corpus instead of failing the gate (Codex P1 on #1572). ERROR
        # counts toward `fail`, so a dead remote = a loud red run.
        if rec.get("stt_error"):
            verdict = "ERROR"
        else:
            verdict = _classify(transcript, reply or "", outcome)
        rec["verdict"] = verdict
        counts[verdict] = counts.get(verdict, 0) + 1
        rows.append(rec)

        flag = {"OK": "  ", "CANT_DO": "✗ ", "EMPTY": "··", "ERROR": "!!"}.get(verdict, "  ")
        print(f"{flag}● {name}  [{verdict}]  stt={rec['stt_ms']}ms")
        print(f"    heard : {transcript!r}")
        print(f"    route : {rr.get('domain')} ({rr.get('score')})   tier0={rec['tier0']}")
        print(f"    => {outcome}")
        if reply:
            print(f"    spoken: {rec.get('spoken')!r}")
        print()

    total = len(rows)
    print("─" * 60)
    print(f"{total} samples:  " + "  ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    cant = [r for r in rows if r["verdict"] == "CANT_DO"]
    if cant:
        print(f"\n⚠ {len(cant)} couldn't-do (fix these):")
        for r in cant:
            print(f"    {r['file']}: {r['transcript']!r} → {r['outcome']}")

    if args.json:
        with open(args.json, "w") as fh:
            json.dump({"counts": counts, "rows": rows, "stt_mode": args.stt}, fh, indent=2)
        print(f"\nwrote {args.json}")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", help="only files whose name sorts >= this (e.g. 0928)")
    ap.add_argument("--last", type=int, help="only the newest N samples")
    ap.add_argument("--user", default="jason", help="user_id to replay as (memory recall)")
    ap.add_argument("--brain", action="store_true", help="run the Gemma brain on fall-through")
    ap.add_argument("--execute", action="store_true", help="actually fulfil writes (mutates DB)")
    ap.add_argument("--json", help="also dump machine-readable results here")
    ap.add_argument("--stt", choices=["inprocess", "remote"], default="inprocess",
                    help="'remote' = transcribe via the LIVE /api/voice/transcribe "
                         "(no second Moonshine load; needs ZOE_DEVICE_TOKEN in env)")
    ap.add_argument("--base-url", default=None,
                    help="live service base URL for --stt remote (default: ZOE_BASE_URL "
                         "resolved AFTER the service .env is loaded, else localhost:8000)")
    args = ap.parse_args()
    sys.exit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
