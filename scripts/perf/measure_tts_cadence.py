#!/usr/bin/env python3
"""TTS-cadence probe — the instrument the replay gate can't be: it HEARS rhythm.

The replay voice gate is warm and stops before TTS (docs/knowledge/voice-pipeline.md),
so a prosody/rhythm regression is invisible to it by construction. This probe closes
that blind spot. It replays a brain turn against a Flue sidecar, feeds the streaming
deltas through the LIVE sentence emitter (routers/voice_tts._extract_first_unit /
_extract_complete_sentences — imported, not re-implemented), and records the SCHEDULE
of Kokoro /synthesize calls the turn would produce: for each spoken sentence, the
monotonic delivery time and its character length. That schedule is exactly what an
operator hears as rhythm.

It then scores the trace with services/zoe-data/voice_cadence_guard against the recorded
baseline band and prints a verdict — the same core the ci_safe unit test exercises on a
fixture, so a green here and a green in CI mean the same thing.

WHY IT RECORDS THE EMIT SCHEDULE, NOT LIVE KOKORO ROUND-TRIPS: the rhythm regression is
in the CADENCE at which sentences reach the synthesizer (the Flue-2.x delta bursts), not
in Kokoro itself (a rock, unchanged). Timing the emit schedule isolates that signal, needs
no second Kokoro load (two ~2.3GB loads OOM the box — see AGENTS.md), and stays read-only.

USAGE (on the live box):
  python3 scripts/perf/measure_tts_cadence.py --wire 2            # score the live 2.x wire
  python3 scripts/perf/measure_tts_cadence.py --wire 1 --wire 2   # compare both wires
  python3 scripts/perf/measure_tts_cadence.py --wire 1 --update-baseline   # re-record the band

Token: read from ZOE_BRAIN_TOKEN or ~/.hermes/.env / services/zoe-data/.env.
NOT a merge gate — no automated check verifies rhythm; this is an operator/diagnostic
instrument whose verdict is advisory and whose real corroboration is the ear.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_ZOE_DATA = _REPO / "services" / "zoe-data"
sys.path.insert(0, str(_ZOE_DATA))

import httpx  # noqa: E402

import voice_cadence_guard as vcg  # noqa: E402

_BASELINE = _ZOE_DATA / "tests" / "fixtures" / "voice_cadence_baseline.json"

_DEFAULT_PROMPTS = [
    "Tell me about the history of the Colosseum in three sentences.",
    "Give me a short summary of how photosynthesis works.",
]


def _import_emitter():
    """Import the LIVE sentence emitter from the router (no re-implementation)."""
    from routers.voice_tts import _extract_complete_sentences, _extract_first_unit

    return _extract_first_unit, _extract_complete_sentences


def _token() -> str:
    tok = (os.environ.get("ZOE_BRAIN_TOKEN") or "").strip()
    if tok:
        return tok
    for path in (Path.home() / ".hermes" / ".env", _ZOE_DATA / ".env"):
        try:
            for line in path.read_text().splitlines():
                if line.strip().startswith("ZOE_BRAIN_TOKEN="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
        except FileNotFoundError:
            continue
    return ""


def _endpoint(base: str, sid: str) -> str:
    return f"{base.rstrip('/')}/agents/zoe/{sid}"


def _payload(wire: int, msg: str) -> bytes:
    if wire >= 2:
        return json.dumps({"kind": "user", "body": msg}).encode()
    return json.dumps({"message": msg}).encode()


async def _capture_turn(wire: int, base: str, prompt: str, token: str) -> list[dict]:
    """Replay one turn; return the emit trace [{t_ms, chars, text}]."""
    first_unit_fn, complete_fn = _import_emitter()
    sid = f"cadence-{wire}-{int(time.time() * 1000)}"
    headers = {"Content-Type": "application/json", "Accept": "application/x-ndjson"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    token_buf = ""
    first_done = False
    emits: list[dict] = []
    t0: float | None = None
    async with httpx.AsyncClient(timeout=180.0) as client:
        async with client.stream(
            "POST", _endpoint(base, sid), content=_payload(wire, prompt), headers=headers
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                now = time.monotonic()
                line = (line or "").strip()
                if not line:
                    continue
                try:
                    chunk = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(chunk, str):
                    continue
                if chunk.startswith(("__TOOL__:", "__THINKING__:")):
                    continue
                if t0 is None:
                    t0 = now
                token_buf += chunk

                def _emit(text: str) -> None:
                    emits.append(
                        {"t_ms": (now - t0) * 1000.0, "chars": len(text.strip()), "text": text.strip()[:60]}
                    )

                if not first_done:
                    fu, token_buf = first_unit_fn(token_buf)
                    if fu:
                        _emit(fu)
                        first_done = True
                ready, token_buf = complete_fn(token_buf)
                for s in ready:
                    _emit(s)
    if token_buf.strip() and t0 is not None:
        emits.append({"t_ms": (now - t0) * 1000.0, "chars": len(token_buf.strip()), "text": token_buf.strip()[:60]})
    return emits


def _print_trace(wire: int, prompt: str, trace: list[dict], verdict: vcg.CadenceVerdict) -> None:
    print(f"\n--- wire {wire}: {prompt!r}")
    prev = 0.0
    for e in trace:
        print(f"    t={e['t_ms']:8.1f}ms  +{e['t_ms']-prev:7.1f}ms  [{e['chars']:3d}ch] {e['text']!r}")
        prev = e["t_ms"]
    m = verdict.metrics.as_dict()
    tag = "SKIP" if verdict.skipped else ("PASS" if verdict.ok else "FAIL")
    print(f"    => {tag}  rush_fraction={m['rush_fraction']} gap_cv={m['gap_cv']} "
          f"gap_max_ms={m['gap_max_ms']} n_emits={m['n_emits']}")
    for f in verdict.failures:
        print(f"       - {f}")


async def _run(args) -> int:
    token = _token()
    if not token:
        print("WARN: no ZOE_BRAIN_TOKEN found — sidecar may reject the turn", file=sys.stderr)
    band = vcg.load_baseline(_BASELINE)
    bases = {1: args.url1, 2: args.url2}
    all_metrics: list[vcg.CadenceMetrics] = []
    worst_ok = True
    for wire in args.wire:
        for prompt in args.prompt:
            trace = await _capture_turn(wire, bases[wire], prompt, token)
            verdict = vcg.evaluate_cadence(trace, band)
            _print_trace(wire, prompt, trace, verdict)
            if not verdict.skipped:
                all_metrics.append(verdict.metrics)
                worst_ok = worst_ok and verdict.ok

    if args.update_baseline:
        if not all_metrics:
            print("\nNothing scored (all traces too short) — baseline NOT updated.", file=sys.stderr)
            return 2
        rush = max(m.rush_fraction for m in all_metrics)
        cv = max(m.gap_cv for m in all_metrics)
        data = json.loads(_BASELINE.read_text())
        # Record the observed max plus a margin so the band admits the reference
        # cadence and nudges no tighter than what was actually seen.
        data["band"]["rush_fraction_max"] = round(min(0.5, rush + 0.15), 4)
        data["band"]["gap_cv_max"] = round(cv * 1.3 + 0.1, 4)
        data["recorded_utc"] = time.strftime("%Y-%m-%d")
        data["source"] = f"measure_tts_cadence.py --update-baseline wire(s)={args.wire}"
        _BASELINE.write_text(json.dumps(data, indent=2) + "\n")
        print(f"\nBaseline updated: rush_fraction_max={data['band']['rush_fraction_max']} "
              f"gap_cv_max={data['band']['gap_cv_max']}")
        return 0

    print(f"\nVERDICT: {'PASS' if worst_ok else 'FAIL'} (advisory — rhythm has no merge gate; confirm by ear)")
    return 0 if worst_ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--wire", type=int, action="append", choices=[1, 2],
                    help="wire(s) to probe (repeatable); default 2")
    ap.add_argument("--prompt", action="append", help="prompt(s) to replay (repeatable)")
    ap.add_argument("--url1", default="http://127.0.0.1:3578", help="Flue 1.x base URL")
    ap.add_argument("--url2", default="http://127.0.0.1:3579", help="Flue 2.x base URL")
    ap.add_argument("--update-baseline", action="store_true",
                    help="re-record the baseline band from the probed cadence")
    args = ap.parse_args()
    if not args.wire:
        args.wire = [2]
    if not args.prompt:
        args.prompt = _DEFAULT_PROMPTS
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
