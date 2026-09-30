#!/usr/bin/env python3
"""recall_evidence_probe.py — "when did I tell you about X?" against the LIVE API.

Deterministic probe for ZOE_RECALL_EVIDENCE: one throwaway ``demo_bar_<8 hex>``
user says a distinctive fact, then asks when they said it and what exactly they
said. Gates, the Live client and the ASSERTED teardown are ``samantha_bar``'s.
The server's mode is read from the packet, so run it flag off, then flag on:

    python3 scripts/perf/recall_evidence_probe.py --dry-run
    ZOE_PERF=1 flock /tmp/zoe-voice-harness.lock nice -n 5 \
        python3 scripts/perf/recall_evidence_probe.py

Verdicts, exit codes and artifacts: docs/knowledge/samantha-bar.md (companion probe).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import signal
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import samantha_bar as bar  # noqa: E402 — shared gates, Live client, teardown

SAY = "Just so you know, my sister Marisol is flying in from Lisbon on Thursday."
ASK_WHEN = "When did I tell you about Marisol flying in?"
ASK_SAID = "What exactly did I say about Marisol?"
EVIDENCE_MARKER = "never guess a date"  # recall_evidence.instruction_line
RESULTS = bar.CACHE / "recall_evidence_probe_last.json"
TREND = bar.CACHE / "recall_evidence_probe_trend.jsonl"
PENDING = bar.CACHE / "recall_evidence_probe_pending_teardown.json"
_DATED_TODAY = re.compile(r"\((?:Mon|Tue|Wed|Thu|Fri|Sat|Sun) \d{1,2} [A-Z][a-z]{2}, today\)")
_TODAY_WORDS = ("today", "earlier", "this morning", "this afternoon", "this evening",
                "tonight", "just now", "a moment ago", "a few minutes ago", "a little while ago")


def observed_mode(packet: str | None) -> str:
    if not packet:
        return "unknown"
    return "on" if EVIDENCE_MARKER in packet else "off"


def score_packet(packet: str | None) -> dict[str, Any]:
    line = next((ln for ln in (packet or "").splitlines() if "marisol" in ln.lower()
                 and "lisbon" in ln.lower()), "")
    quote = line.split('you said: "', 1)[1] if 'you said: "' in line else ""
    return {"bullet_found": bool(line), "dated_today": bool(_DATED_TODAY.search(line)),
            "any_date": bool(re.search(r"\((?:Mon|Tue|Wed|Thu|Fri|Sat|Sun) \d", packet or "")),
            "quoted": "lisbon" in quote.lower()}


def score_when(reply: str, weekday: str) -> bool:
    low = bar.normalize(reply)
    return any(w in low for w in _TODAY_WORDS) or weekday.lower() in low


def score_said(reply: str) -> bool:
    return bar.contains_all(reply, ("lisbon", "thursday"))


def verdict(mode: str, pkt: dict[str, Any], when_ok: bool) -> str:
    if mode == "on":
        return "PASS" if (pkt["dated_today"] and pkt["quoted"] and when_ok) else "FAIL"
    if mode == "off":
        return "ERROR" if pkt["any_date"] else "BASELINE"
    return "ERROR"


def run(live: bar.Live, user: str, log) -> dict[str, Any]:
    before = live.capture_status(user)
    seed = live.chat(user, "evseed", SAY)
    landed = live.wait_captured(user, before)
    landed_pkt = live.wait_landed(user, ASK_WHEN, ["marisol", "lisbon"])
    packet = live.packet(user, ASK_WHEN)
    mode, pkt = observed_mode(packet), score_packet(packet)
    weekday = dt.datetime.now().strftime("%A")  # the box's local day (ZOE_TIMEZONE on the Jetson)
    when = live.chat(user, "evask", ASK_WHEN)
    said = live.chat(user, "evsaid", ASK_SAID)
    when_ok, said_ok = score_when(when["reply"], weekday), score_said(said["reply"])
    v = verdict(mode, pkt, when_ok)
    if seed["error"] or when["error"] or said["error"] or not landed_pkt["landed"]:
        v = "ERROR"
    log(f"mode={mode} packet={pkt} when_ok={when_ok} said_ok={said_ok} -> {v}")
    return {"mode": mode, "verdict": v, "packet": pkt, "when_ok": when_ok, "said_ok": said_ok,
            "capture": landed, "landed": landed_pkt, "seed": live.evidence(seed),
            "when": live.evidence(when), "said": live.evidence(said)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="print the plan; no network")
    ap.add_argument("--keep-replies", action="store_true")
    ap.add_argument("--service-dir", default=None)
    args = ap.parse_args(argv)
    if args.dry_run:
        print(f"say {SAY!r} -> /for-prompt + ask {ASK_WHEN!r} -> ask {ASK_SAID!r} -> teardown")
        return 0
    if os.environ.get("ZOE_PERF") != "1":
        print("recall_evidence_probe: skipped — live runs require ZOE_PERF=1 (see --dry-run)")
        return 0
    log = lambda m: print(m, flush=True)  # noqa: E731
    service_dir = bar.resolve_service_dir(args.service_dir)
    revision = bar.service_revision(service_dir)
    bar.load_source_fallback_markers(service_dir)
    lock_fd = bar._acquire_lock()  # noqa: F841 — held for the process lifetime
    token = bar.env_file_value(service_dir, "ZOE_INTERNAL_TOKEN")
    dsn = bar.env_file_value(service_dir, "POSTGRES_URL")
    live = bar.Live(token, os.environ.get("ZOE_BAR_ADMIN_SESSION", "").strip(), dsn,
                    args.keep_replies)

    def refuse(reason: str) -> int:
        print(f"REFUSED: {reason}", file=sys.stderr)
        bar.write_json(RESULTS, {"status": "refused", "reason": reason, "revision": revision})
        return 2

    gates = (  # (passes?, why not) — evaluated in order, lazily
        lambda: (not bar.in_nightly_window(dt.datetime.now()), "inside/near the nightly window"),
        lambda: (lambda b: (b[0] is False, f"deploy check: {b[1]}"))(bar.deploy_in_progress()),
        lambda: (bool(token and dsn), "ZOE_INTERNAL_TOKEN / POSTGRES_URL not in the service .env"),
        lambda: bar._wait(bar._readyz, 600, "/readyz"),
        lambda: (live.forget_ok(), "memory-store teardown unavailable — the run must not write"),
        lambda: (bar.mem_available_mb() >= bar.MIN_MEM_MB, f"MemAvailable < {bar.MIN_MEM_MB} MB"),
    )
    for gate in gates:
        ok, why = gate()
        if not ok:
            return refuse(why)
    prior = bar._pending_teardown(live, PENDING, log)
    if prior is not None and not prior["proven"]:
        return refuse(f"a previous probe's teardown is unproven: {prior['problems']}")

    user, started = bar.new_demo_user(), dt.datetime.now(dt.timezone.utc)

    def _sigterm(signum, frame):  # route SIGTERM through the finally below
        raise SystemExit(128 + signum)
    signal.signal(signal.SIGTERM, _sigterm)
    result: dict[str, Any] = {"verdict": "ERROR"}
    run_error = None
    try:
        bar.write_json(PENDING, {"users": [user], "sessions": [], "started_at": started.isoformat()})
        result = run(live, user, log)
    except BaseException as exc:  # noqa: BLE001 — teardown must still run
        run_error = f"{type(exc).__name__}: {str(exc)[:200]}"
        log(f"run aborted: {run_error}")
    finally:
        sessions = list(live.sessions.get(user, []))
        bar.write_json(PENDING, {"users": [user], "sessions": sessions,
                                 "started_at": started.isoformat()})
        td = bar.teardown(live, [user], sessions)
        if td["proven"]:
            PENDING.unlink(missing_ok=True)
        log(f"teardown proven={td['proven']} {'' if td['proven'] else td['problems']}")
    payload = {"probe": "recall_evidence", "run_error": run_error, "revision": revision,
               "started_at": started.isoformat(timespec="seconds"),
               "finished_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
               **result, "teardown": td}
    bar.write_json(RESULTS, payload)
    TREND.parent.mkdir(parents=True, exist_ok=True)
    with open(TREND, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({k: payload.get(k) for k in ("finished_at", "mode", "verdict",
                                                         "when_ok", "said_ok", "packet")}
                            | {"commit": (revision or {}).get("commit"),
                               "teardown_proven": td["proven"]}, sort_keys=True) + "\n")
    if run_error or not td["proven"] or result["verdict"] == "ERROR":
        return 2
    return 1 if result["verdict"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
