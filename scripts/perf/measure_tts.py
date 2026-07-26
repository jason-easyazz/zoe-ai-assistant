#!/usr/bin/env python3
"""Kokoro TTS **time-to-first-audio** probe — the one voice stage we never timed.

We already measure STT (Moonshine, ~700ms median), brain (Gemma, ~2.95s) and the
pre-TTS end-to-end (~3.8s) via ``measure_voice.py``. What stayed unmeasured is the
gap that decides how fast a reply *starts speaking*: from the moment the FIRST
speakable unit of the reply is ready to the moment Kokoro hands back the first
audio bytes. This probe measures exactly that, on the LIVE voice path.

It mirrors the live streaming emitter in ``routers/voice_tts._generate_voice_stream``:
the first audio chunk is the FIRST clause pulled out of the brain's token stream by
``_extract_first_unit`` (not a whole sentence), synthesized through
``_synthesize_kokoro_sidecar`` (the warm PyTorch sidecar on :10201, primary leg of
the TTS waterfall). We import those exact live functions — no re-implementation —
and time the sidecar call on the real first unit of each replayed reply.

Two cohorts per sample, because the sidecar has a phrase cache + startup warmup:
  * **first_unit_ms** — synth latency of the live first clause (what the user waits
    through before audio starts). Reports the sidecar ``X-Cache`` header so a
    cache HIT (instant) is not mistaken for real synthesis latency.
  * **full_reply_ms** — synth latency of the whole spoken reply, as an upper bound
    and a sanity check on how much the first-unit split actually buys.

INPUT: reply text comes from the canonical replay corpus. Either pass a replay JSON
(``--replay-json`` produced by ``replay_samples.py --json``), or let this probe shell
out to the replay harness itself (``--run-replay``, needs a reachable brain). It then
feeds each reply's ``spoken`` text through the live TTS first-unit path.

SAFETY: read-only w.r.t. Zoe state. It hits the already-running Kokoro sidecar over
HTTP (the same call the live path makes), never loads a second Kokoro model in-process,
never restarts the sidecar, never mutates the DB. Honour the harness lock when running
(``flock /tmp/zoe-voice-harness.lock``) so it can't run concurrently with a sibling
replay and OOM the box.

CI gate: requires ``ZOE_PERF=1`` and a reachable sidecar; otherwise exits 0 with a skip.

Usage (run on the Jetson host, under the harness lock). ``--service-dir`` auto-
resolves to the live env — including from a git WORKTREE — so it needs no flag:
    # measure TTS on an existing replay dump:
    ZOE_PERF=1 python3 scripts/perf/measure_tts.py --replay-json voice.json

    # or run the replay first, then measure TTS on its replies:
    flock /tmp/zoe-voice-harness.lock -c \
      'ZOE_PERF=1 python3 scripts/perf/measure_tts.py --run-replay --last 10 \
         --json tts.json'
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from service_dir import (  # noqa: E402 — sibling-import convention, scripts/ is not a package
    resolve_service_dir,
    SERVICE_DIR_HELP,
)


def _stats(values: list[float]) -> dict:
    if not values:
        return {}
    vals = sorted(values)
    n = len(vals)

    def pct(p: float) -> float:
        if n == 1:
            return vals[0]
        return vals[min(n - 1, max(0, int(round(p * (n - 1)))))]

    return {
        "n": n,
        "median": round(statistics.median(vals), 1),
        "p10": round(pct(0.10), 1),
        "p90": round(pct(0.90), 1),
        "min": round(vals[0], 1),
        "max": round(vals[-1], 1),
    }


def _load_env(service_dir: str) -> None:
    """Mirror replay_samples._load_env so the live router imports resolve."""
    p = os.path.join(service_dir, ".env")
    if not os.path.exists(p):
        return
    for line in open(p):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k, v.strip().strip('"').strip("'"))


def _run_replay(service_dir: str, args) -> list[dict]:
    """Shell out to the canonical replay harness and return its rows (with replies)."""
    replay = os.path.join(service_dir, "tests", "replay_samples.py")
    if not os.path.exists(replay):
        print(f"replay harness not found at {replay}", file=sys.stderr)
        return []
    with tempfile.NamedTemporaryFile("r", suffix=".json", delete=False) as tf:
        replay_json = tf.name
    cmd = [sys.executable, "tests/replay_samples.py", "--brain",
           "--user", args.user, "--json", replay_json]
    if args.since:
        cmd += ["--since", args.since]
    else:
        cmd += ["--last", str(args.last)]
    print(f"Running replay to source reply text: {' '.join(cmd)}\n")
    try:
        proc = subprocess.run(cmd, cwd=service_dir, timeout=args.timeout,
                              capture_output=True, text=True)
        sys.stdout.write(proc.stdout)
        if proc.returncode not in (0, 1):  # 1 = some CANT_DO turns; rows still valid
            sys.stderr.write(proc.stderr)
        with open(replay_json) as fh:
            return json.load(fh).get("rows", [])
    except Exception as exc:
        print(f"replay run failed: {exc}", file=sys.stderr)
        return []
    finally:
        try:
            os.unlink(replay_json)
        except OSError:
            pass


def _replies_from_json(path: str) -> list[dict]:
    with open(path) as fh:
        data = json.load(fh)
    return data.get("rows", [])


async def _measure(rows: list[dict], service_dir: str, args) -> int:
    sys.path.insert(0, service_dir)
    os.chdir(service_dir)
    # The live TTS first-chunk emitter and the sidecar synth call — imported, not
    # reimplemented, so we time exactly what production runs.
    # Import the live first-chunk split + text-clean used by the streaming emitter,
    # so we measure exactly the unit production sees in routers/voice_tts.
    from routers.voice_tts import (
        _extract_first_unit, _clean_for_speech, _fast_first_audio_enabled,
    )
    import httpx

    sidecar_url = os.environ.get("ZOE_KOKORO_SIDECAR_URL", "http://127.0.0.1:10201").rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=3.0) as c:
            h = await c.get(f"{sidecar_url}/health")
            health = h.json()
        if not health.get("pipeline_loaded"):
            print(f"sidecar at {sidecar_url} has no pipeline loaded — skipping.", file=sys.stderr)
            return 0
        print(f"Kokoro sidecar live at {sidecar_url} (device={health.get('device')}, "
              f"voice={health.get('voice')})  fast_first_audio={_fast_first_audio_enabled()}\n")
    except Exception as exc:
        print(f"sidecar at {sidecar_url} unreachable ({exc}) — skipping.", file=sys.stderr)
        return 0

    _voice = os.environ.get("ZOE_KOKORO_VOICE", "af_sky")

    async def _timed_synth(client, base_text: str, cache_bust: str = "") -> dict:
        """Time a SINGLE live sidecar synth and read its X-Cache verdict.

        This is the exact request `_synthesize_kokoro_sidecar` issues (same URL,
        same body shape: text + voice, speed omitted → 1.0). We post it directly
        so we can read the X-Cache header the live helper discards, and we time
        only this one call — no warm-up call beforehand, so a fresh first unit is
        measured COLD, as the live path experiences it for a new brain reply.

        `cache_bust` is appended AFTER `_clean_for_speech` so the cache-busting
        token isn't passed through speech-cleaning and synthesized as extra spoken
        words (which would inflate short-clause timing). Keep it a single tiny
        token so its synthesis cost is negligible vs the clause under test."""
        cleaned = _clean_for_speech(base_text) + cache_bust
        t = time.monotonic()
        try:
            r = await client.post(
                f"{sidecar_url}/synthesize",
                json={"text": cleaned, "voice": _voice},
            )
        except Exception as exc:
            return {"ms": 0.0, "bytes": 0, "ok": False, "cache": "?", "err": str(exc)}
        ms = (time.monotonic() - t) * 1000.0
        ok = r.status_code < 400 and bool(r.content)
        return {"ms": round(ms, 1), "bytes": len(r.content) if ok else 0,
                "ok": ok, "cache": r.headers.get("X-Cache", "?")}

    out_rows: list[dict] = []
    first_unit_ms: list[float] = []       # cold first-unit synth (cache miss only)
    first_unit_all_ms: list[float] = []   # first-unit synth incl. cache hits (live mix)
    full_reply_ms: list[float] = []

    # ONE pooled client, reused like the live `_kokoro_http_client`, so we time the
    # synthesis cost — not a fresh TCP/connection setup — per call. `async with`
    # guarantees the keepalive connections close even if the loop raises.
    async with httpx.AsyncClient(
        timeout=20.0,
        limits=httpx.Limits(max_keepalive_connections=4, keepalive_expiry=60.0),
    ) as _client:
      for idx, r in enumerate(rows):
        reply = (r.get("spoken") or r.get("reply") or "").strip()
        if not reply:
            continue
        name = r.get("file", "?")

        # Reproduce the live first-chunk split exactly: brain streams tokens, the
        # emitter snaps the FIRST clause via _extract_first_unit and synthesizes it.
        first_unit, _rest = _extract_first_unit(reply)
        if not first_unit:
            # Reply shorter than the min-unit threshold: the live path would emit it
            # whole as the first chunk.
            first_unit = reply

        # The sidecar phrase-caches af_sky / speed-1.0 / <=240ch text, so a corpus
        # phrase replayed twice serves from cache (~few ms) and masks the real synth
        # cost a NEW brain reply pays. --cold appends a tiny per-run cache-bust token
        # (after speech-cleaning) so each text is a guaranteed cache MISS = true cold
        # synthesis latency. A 2-char alpha token keeps the added spoken cost
        # negligible (digits would be read aloud as multi-word numbers). Default
        # (no --cold) measures the live mix incl. legitimate cache hits.
        bust = f" zq{idx}" if args.cold else ""
        fu = await _timed_synth(_client, first_unit, bust)
        # Whole-reply synth as the upper bound the first-unit split avoids.
        full = await _timed_synth(_client, reply, bust)

        cache_verdict = fu["cache"]
        if fu["ok"]:
            first_unit_all_ms.append(fu["ms"])
            if cache_verdict != "hit":
                first_unit_ms.append(fu["ms"])
        if full["ok"]:
            full_reply_ms.append(full["ms"])

        row = {
            "file": name,
            "verdict": r.get("verdict"),
            "first_unit_chars": len(first_unit),
            "reply_chars": len(reply),
            "first_unit_ms": fu["ms"],
            "first_unit_cache": cache_verdict,
            "full_reply_ms": full["ms"],
            "full_reply_cache": full["cache"],
            "first_unit_text": first_unit[:60],
        }
        out_rows.append(row)
        print(f"● {name}  [{r.get('verdict')}]")
        print(f"    first unit ({len(first_unit)}ch): {first_unit[:60]!r}")
        print(f"    first_unit_ms={fu['ms']} (cache={cache_verdict})  "
              f"full_reply_ms={full['ms']} ({full['bytes']}B, cache={full['cache']})")
        print()

    n_hit = sum(1 for r in out_rows if r["first_unit_cache"] == "hit")
    print("═" * 64)
    print("KOKORO TTS TIME-TO-FIRST-AUDIO (ms)")
    print(f"  device: {health.get('device')}   "
          f"cache hits: {n_hit}/{len(out_rows)} first units")
    print("  first_unit COLD (cache miss only) :", _stats(first_unit_ms))
    print("  first_unit live mix (incl. hits)  :", _stats(first_unit_all_ms))
    print("  full_reply (whole utterance)      :", _stats(full_reply_ms))

    report = {
        "kind": "tts_first_chunk",
        "service_dir": service_dir,
        "sidecar": sidecar_url,
        "device": health.get("device"),
        "n_samples": len(out_rows),
        "first_unit_cold_ms": _stats(first_unit_ms),
        "first_unit_live_mix_ms": _stats(first_unit_all_ms),
        "full_reply_ms": _stats(full_reply_ms),
        "cache_hits": n_hit,
        "rows": out_rows,
    }
    if args.json:
        with open(args.json, "w") as fh:
            json.dump(report, fh, indent=2)
        print(f"\nwrote {args.json}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--replay-json", help="replay_samples.py --json output to source replies from")
    ap.add_argument("--replies-file", help="plain text file, one Zoe reply per line (brain-free)")
    ap.add_argument("--run-replay", action="store_true",
                    help="run replay_samples.py to source replies (needs brain)")
    ap.add_argument("--cold", action="store_true",
                    help="append a per-run nonce so every synth is a true cache MISS (cold cost)")
    ap.add_argument("--last", type=int, default=10, help="newest N samples (with --run-replay)")
    ap.add_argument("--since", help="only samples whose filename sorts >= this")
    ap.add_argument("--user", default="jason", help="user_id for the replay brain turn")
    ap.add_argument("--service-dir", default=None, help=SERVICE_DIR_HELP)
    ap.add_argument("--json", help="write machine-readable results here")
    ap.add_argument("--timeout", type=int, default=600, help="replay subprocess timeout (s)")
    args = ap.parse_args()

    if os.environ.get("ZOE_PERF") != "1":
        print("ZOE_PERF != 1 — skipping TTS first-chunk probe (set ZOE_PERF=1 to run).")
        return 0

    # Same ladder as measure_voice.py / the probe (scripts/lib/service_dir.py) —
    # a direct run from a git worktree resolves the live env with no flag. The
    # loud skip below is unchanged: the ladder fixes the DEFAULT, not the failure.
    service_dir = str(resolve_service_dir(args.service_dir))
    if not os.path.exists(os.path.join(service_dir, ".env")):
        print(f"no .env in {service_dir} (live service env required) — skipping.", file=sys.stderr)
        return 0
    _load_env(service_dir)

    if args.replies_file:
        with open(args.replies_file) as fh:
            rows = [{"file": f"line{i+1}", "verdict": "OK", "spoken": ln.strip()}
                    for i, ln in enumerate(fh) if ln.strip()]
    elif args.replay_json:
        rows = _replies_from_json(args.replay_json)
    elif args.run_replay:
        rows = _run_replay(service_dir, args)
    else:
        print("provide --replies-file <f>, --replay-json <f>, or --run-replay", file=sys.stderr)
        return 2

    rows = [r for r in rows if (r.get("spoken") or r.get("reply"))]
    if not rows:
        print("no replies with spoken text to synthesize — nothing to measure.", file=sys.stderr)
        return 0

    return asyncio.run(_measure(rows, service_dir, args))


if __name__ == "__main__":
    sys.exit(main())
