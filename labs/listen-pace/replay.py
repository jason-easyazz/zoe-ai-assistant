#!/usr/bin/env python3
"""Listen-pace SHADOW replayer: what level would Zoe have chosen per turn, and what would it have bought?

Reads the panel daemon's ``FIRST_SOUND`` lines (PR 2495's instrument; a file or stdin; every other line is ignored), runs the
REAL controller (``services/zoe-data/listen_pace.py``, loaded by path) as if ``ZOE_LISTEN_PACE=on``, and prints the two numbers
the owner cares about:

* **cut-offs**: per 100 eligible turns, observed (the live config) against predicted (at the level the controller would have
  used), and how many observed cut-offs a longer level would have AVOIDED;
* **waiting**: the mean wait after speech end, observed against predicted, and ms of waiting saved per 100 eligible turns.

How a turn observed at the live level U is predicted at level L (``PROFILES`` is monotone, so every tail at L <= U is <= U's):

* L < U: the close comes no later; a clean observation stays clean (the quiet run was unbroken and nobody spoke for the window
  after U's close), a cut stays a cut. The wait reaches the clean gate only when the fall was clean: an UPPER bound on the
  saving (the gate also needs <= 2 decay chunks and >= 480 ms of speech, neither on the line).
* any L: the wait is the observed one moved by L's table wait minus U's table wait for the close KIND (quiet / clean fall /
  hesitating deep), so the U table is undone before L's applies (an L4 deep close replayed at L0 gets L0's deep tail; an L-3
  gate close replayed at L0, which has no gate, gets L0's deep tail plus an assumed one-chunk decay, ``GATE_DECAY_MS``).
* L > U: an observed cut is avoided only when the line says
  when the talk resumed (``talk_first_ms``, not on PR 2495's line yet) and it is inside the extra wait; otherwise it is still
  counted as a cut (conservative) and reported as "possibly avoided".

Read-only, no network, no live reads: give it a file. Prints numbers and enums only, never a log line.

    python3 labs/listen-pace/replay.py panel-voice.log [--turns-per-day 10] [--observed-level 0] [--per-turn] [--json]
"""
from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Iterable, Optional

_LP_PATH = Path(__file__).resolve().parents[2] / "services" / "zoe-data" / "listen_pace.py"


def load_controller(path: Path = _LP_PATH):
    spec = importlib.util.spec_from_file_location("listen_pace_replay", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


LP = load_controller()

_KV = re.compile(r"(\w+)=(\S+)")
_CLOSE = re.compile(r"^([a-z_]+)=(\d+)ms$")
_TS = re.compile(r"^\s*(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})")


def parse_line(line: str) -> "Optional[tuple[Optional[float], dict]]":
    """(epoch or None, fields) for a FIRST_SOUND line; None for any other line. ``close=deep=640ms`` becomes rule/close_ms."""
    i = line.find("FIRST_SOUND ")
    if i < 0:
        return None
    fields = {}
    for k, v in _KV.findall(line[i + len("FIRST_SOUND "):]):
        fields.setdefault(k, v)
    m = _CLOSE.match(fields.get("close", ""))
    if m:
        fields["rule"], fields["close_ms"] = m.group(1), m.group(2)
    t = None
    ts = _TS.match(line)
    if ts:
        try:
            t = dt.datetime.fromisoformat(f"{ts.group(1)} {ts.group(2)}").timestamp()
        except ValueError:
            t = None
    return t, fields


def _int(fields: dict, key: str) -> Optional[int]:
    try:
        return int(fields[key])
    except (KeyError, ValueError):
        return None


GATE_DECAY_MS = 80   # a clean-gate close does not say when its deep run began: assume one decay chunk (the gate allows <= 2)


def _kind(o, fields: dict) -> str:
    """The close KIND, which decides the tail at any level: ``quiet`` (quiet / hesitation), ``clean`` (a clean fall: the gate
    can close it where a level has one), ``deep`` (a hesitating fall closed by the deep tail)."""
    if o.rule in ("quiet", "hesitation"):
        return "quiet"
    return "clean" if o.rule == "clean" or fields.get("fall") == "clean" else "deep"


def _table_wait(p, kind: str, ov: int) -> int:
    """The wait after speech end that profile ``p`` defines for ``kind``; ``ov`` = the decay before the deep / quiet run."""
    if kind == "quiet":
        return p.quiet_ms + ov
    deep = p.deep_ms + ov
    return min(deep, p.clean_ms) if kind == "clean" and p.clean_ms > 0 else deep


def predict(fields: dict, o, level: int, observed: int) -> "tuple[bool, Optional[int], str]":
    """(cut at ``level``, wait at ``level`` or None, avoided: yes | maybe | no | -).

    The wait is the observed wait moved by (the replayed level's table wait - the OBSERVED level's table wait) for this close
    kind, so a close seen at any level replays to the replayed level's own tail (an L4 deep close at L0 is L0's deep tail; an
    L-3 gate close at L0, which has no gate, is L0's deep tail) and observed == replayed changes nothing."""
    cut_obs = bool(o.still_talking or o.held)
    q = _int(fields, "quiet_ms")
    wait = q if q is not None and q >= 0 else None
    pl, pu = LP.profile(level), LP.profile(observed)
    kind = _kind(o, fields)
    if o.rule == "clean":
        ov = GATE_DECAY_MS
    else:
        ov = (q - (pu.quiet_ms if kind == "quiet" else pu.deep_ms)) if wait is not None else 0
    extra = _table_wait(pl, kind, ov) - _table_wait(pu, kind, ov) if o.rule in LP.CLOSE_RULES else 0
    if wait is not None:
        wait += extra
    if level <= observed:
        return cut_obs, wait, "-"
    if not cut_obs:
        return False, wait, "-"
    tf = _int(fields, "talk_first_ms")
    if tf is not None and 0 <= tf < extra:
        return False, wait, "yes"
    return True, wait, ("no" if tf is not None else "maybe")


def replay(lines: Iterable[str], *, turns_per_day: float = 10.0, observed: int = 0, child: bool = False) -> dict:
    state = LP.new_state(child=child)
    t_synth = t_last = 1_800_000_000.0
    rows, ignored = [], 0
    for line in lines:
        p = parse_line(line)
        if p is None:
            continue
        t, fields = p
        if t is None:
            t_synth += 86400.0 / max(0.1, turns_per_day)
            t = t_synth
        t_last = t
        o = LP.outcome_from_fields(fields)
        verdict, _half = LP.classify(o)
        level = LP.effective_level(state, t)
        if verdict.startswith("skip"):
            ignored += 1
            rows.append({"level": level, "verdict": verdict})
            continue
        cut, wait, avoided = predict(fields, o, level, observed)
        q = _int(fields, "quiet_ms")
        rows.append({"level": level, "verdict": verdict, "cut_obs": verdict == "cut", "cut_pred": cut,
                     "wait_obs": q if q is not None and q >= 0 else None, "wait_pred": wait, "avoided": avoided})
        pred = LP.Outcome(rule=o.rule, win=o.win, still_talking=cut and o.still_talking, talk_chunks=o.talk_chunks,
                          obs_ms=o.obs_ms, muted_ms=o.muted_ms, frozen="", reflex=o.reflex, held=cut and o.held and not o.still_talking,
                          speech_ms=o.speech_ms, level_used=level)
        state, _ = LP.step(state, pred, t)
    ev = [r for r in rows if not r["verdict"].startswith("skip")]
    n = len(ev)
    waits = [(r["wait_obs"], r["wait_pred"]) for r in ev if r["wait_obs"] is not None and r["wait_pred"] is not None]
    hist: dict = {}
    for r in ev:
        hist[r["level"]] = hist.get(r["level"], 0) + 1
    per100 = (lambda k: round(100.0 * sum(1 for r in ev if r[k]) / n, 2)) if n else (lambda k: None)
    mean_obs = round(sum(a for a, _ in waits) / len(waits), 1) if waits else None
    mean_pred = round(sum(b for _, b in waits) / len(waits), 1) if waits else None
    return {
        "turns": len(rows), "eligible": n, "skipped": ignored,
        "cuts_per_100_observed": per100("cut_obs"), "cuts_per_100_predicted": per100("cut_pred"),
        "cutoffs_avoided": sum(1 for r in ev if r.get("avoided") == "yes"),
        "cutoffs_possibly_avoided": sum(1 for r in ev if r.get("avoided") == "maybe"),
        "mean_wait_ms_observed": mean_obs, "mean_wait_ms_predicted": mean_pred,
        "wait_saved_ms_per_100_turns": round(100.0 * (mean_obs - mean_pred), 1) if waits else None,
        "wait_turns": len(waits), "level_histogram": dict(sorted(hist.items())), "final_level": LP.effective_level(state, t_last),
        "rows": rows,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("log", nargs="?", help="a file of daemon log lines (default stdin)")
    ap.add_argument("--turns-per-day", type=float, default=10.0, help="synthetic clock for lines without a timestamp")
    ap.add_argument("--observed-level", type=int, default=0, help="the level the log was recorded at (live = 0)")
    ap.add_argument("--child", action="store_true", help="replay as a child subject (floor L0, start L+1)")
    ap.add_argument("--per-turn", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    fh = open(a.log, encoding="utf-8", errors="replace") if a.log else sys.stdin
    with fh:
        rep = replay(fh, turns_per_day=a.turns_per_day, observed=a.observed_level, child=a.child)
    rows = rep.pop("rows")
    if a.json:
        out = dict(rep)
        if a.per_turn:
            out["rows"] = rows
        print(json.dumps(out, indent=1))
        return 0
    if a.per_turn:
        for i, r in enumerate(rows, 1):
            print(f"{i:5d} level={r['level']:+d} {r['verdict']:<18} cut_obs={int(r.get('cut_obs', 0))} "
                  f"cut_pred={int(r.get('cut_pred', 0))} wait_obs={r.get('wait_obs')} wait_pred={r.get('wait_pred')} "
                  f"avoided={r.get('avoided', '-')}")
    for k, v in rep.items():
        print(f"{k:<28} {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
