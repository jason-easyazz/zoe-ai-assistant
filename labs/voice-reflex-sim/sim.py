#!/usr/bin/env python3
"""Voice reflex projection lab: what share of turns would the panel reflex play on, why not on the rest, and what would the
first-sound distribution look like with it on, against today's?

Log-derived and synthetic inputs ONLY: per-turn (timestamp, endpoint close ms, panel TTFA) from the felt scorecard's allowlist
extract of the panel log (numbers and a timestamp, never text). It runs the REAL ``zoe_voice_reflex.decide()`` (the gate), so the
rules are the shipped rules, not a re-implementation. Reads no audio, loads no model, touches no device, writes nothing.

What the log CAN state per turn (exact here): the close and its time, the hour (night), the gap since the previous turn (the
30 s budget, the 4 h idle greeting), the endpoint rule. What it CANNOT state: the speaker verdict, the media state, the room
floor, the still-talking window, wake versus window turn, speech length. Those are ``Assumptions``: each unobservable rule passes
with a stated probability (default 1.0 = the CEILING), and the report prints a sensitivity grid. Nothing here is a measurement of
those rules; the shadow mode on the panel (``REFLEX_DECISION`` lines) is the measurement and ``--decisions`` reads it.

    python3 labs/voice-reflex-sim/sim.py --panel                  # read the Pi's log over ssh (allowlisted extract, read-only)
    python3 labs/voice-reflex-sim/sim.py --lines reduced.txt      # a saved extract
    python3 labs/voice-reflex-sim/sim.py --synthetic 400          # invented turns (a self-check, no log)
    python3 labs/voice-reflex-sim/sim.py --decisions decisions.txt   # summarise REFLEX_DECISION lines (shadow week)
    python3 labs/voice-reflex-sim/sim.py --decisions decisions.txt --markers markers.txt   # + would-play turns near a safety marker
"""
from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import math
import random
import re
import statistics
import subprocess
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterable, Optional, Sequence

REPO = Path(__file__).resolve().parents[2]


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


RX = _load("zoe_voice_reflex_for_sim", "scripts/setup/zoe_voice_reflex.py")

#: bars the owner's felt number is judged by: any sound inside 1 s of speech end; 0.6 s is the reflex bar
BANDS_MS = (1000, 600)
LINE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) (?:T (\d+(?:\.\d+)?)|C (\d+)|Q (deep|quiet)|(R)|E)$")
TAIL_FLOOR_MS = {"deep": 640, "quiet": 800}      # the panel's live floors, used only when a Recorded line has no close=


@dataclass(frozen=True)
class Turn:
    ts: dt.datetime
    close_ms: int          # speech end -> the committed close (the endpoint rule's wait)
    ttfa_ms: int           # POST -> first server audio on the panel (the scorecard's TTFA)


@dataclass(frozen=True)
class Assumptions:
    """Everything the log cannot state. Defaults are the CEILING (every unobservable guard passes); each is a probability that
    the guard does NOT silence the turn, applied independently per turn with a seeded RNG."""
    p_wake: float = 1.0            # the turn is a wake turn (S1 plays on wake turns only)
    p_speaker: float = 1.0         # the speaker clears the FAR 0.1 % bar and is a household member
    p_media_clear: float = 1.0     # nothing foreign is playing, and the watcher can say so
    p_quiet: float = 1.0           # the room floor is under the noisy bar
    p_still: float = 1.0           # nobody is still talking after the close, and the mic window could say so
    p_long: float = 1.0            # at least 480 ms of speech
    verdict_p50_ms: float = 70.0   # the at-close speaker score: p50 / p95 on the panel Pi (docs/knowledge/speaker-resnet34-switch.md)
    verdict_p95_ms: float = 215.0
    talk_ready_ms: float = 240.0   # earliest the post-close mic can state "nobody is talking": mic open + 160 ms. UNMEASURED
    start_ms: float = 100.0        # decision -> first sample out of the speaker (spawn + write + onset). 70-90 ms measured for
                                   # the player alone (the daemon's own comment), the rest unmeasured
    seed: int = 7


def _lognormal(p50: float, p95: float):
    mu = math.log(p50)
    sigma = max(1e-6, (math.log(p95) - mu) / 1.645)
    return mu, sigma


def parse_turns(reduced: Iterable[str], since: Optional[dt.datetime] = None, until: Optional[dt.datetime] = None,
                skipped: Optional[dict] = None) -> "list[Turn]":
    """Pair each TTFA with the closest preceding Recorded line within 60 s (the scorecard's pairing), keeping the timestamp.
    A row whose timestamp matches the shape but is not a real time (month 99, 30 February, hour 25) is skipped, never a crash;
    when ``skipped`` is a dict, ``skipped["bad_timestamp"]`` counts them."""
    out: "list[Turn]" = []
    last = None
    for line in reduced:
        m = LINE_RE.match(line.strip())
        if not m:
            continue
        try:
            ts = dt.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            if skipped is not None:
                skipped["bad_timestamp"] = skipped.get("bad_timestamp", 0) + 1
            continue
        ttfa, close, tail, rec = m.group(2), m.group(3), m.group(4), m.group(5)
        if close is not None:
            last = (ts, int(close))
        elif tail is not None:
            last = (ts, TAIL_FLOOR_MS[tail])
        elif rec is not None:
            last = None
        elif ttfa is not None:
            if last and 0 <= (ts - last[0]).total_seconds() <= 60 and (since is None or since <= last[0]) \
                    and (until is None or last[0] < until):
                out.append(Turn(last[0], last[1], int(round(float(ttfa) * 1000.0))))
            last = None
    return out


def synthetic_turns(n: int, seed: int = 1) -> "list[Turn]":
    """Invented turns: closes 640 / 800 ms, TTFA 1.3 to 5 s, spread over a day and a half, some in bursts."""
    rng = random.Random(seed)
    t = dt.datetime(2026, 1, 1, 6, 0, 0)
    out = []
    for _ in range(n):
        t += dt.timedelta(seconds=rng.choice([8, 15, 25, 40, 90, 600, 3600, 5 * 3600]) * rng.uniform(0.7, 1.3))
        out.append(Turn(t, rng.choice([640, 640, 640, 800]), int(rng.uniform(1300, 5000))))
    return out


def _percentile(xs: Sequence[float], q: float) -> float:
    s = sorted(xs)
    if not s:
        return float("nan")
    k = (len(s) - 1) * q
    lo, hi = int(math.floor(k)), int(math.ceil(k))
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def simulate(turns: "Sequence[Turn]", a: Assumptions = Assumptions(), *, deadline_ms: int = RX.REFLEX_SCORE_DEADLINE_MS) -> dict:
    """Run every turn through the REAL ``decide()``. Returns the per-rule counts and both first-sound distributions."""
    rng = random.Random(a.seed)
    mu, sigma = _lognormal(a.verdict_p50_ms, a.verdict_p95_ms)
    s = replace(RX.Settings(enabled=True), score_deadline_ms=deadline_ms)
    takes = ("mm_01", "mm_02", "mm_03", "mm_04")
    state: dict = {}
    skips: "dict[str, int]" = {}
    today, with_reflex, played_ms = [], [], []
    plays = 0
    prev_ts = None
    for t in sorted(turns, key=lambda x: x.ts):
        idle_h = (t.ts - prev_ts).total_seconds() / 3600.0 if prev_ts is not None else 0.0
        prev_ts = t.ts
        first_today = t.close_ms + t.ttfa_ms                      # as the scorecard counts it (excludes the pre-POST upload)
        draw = rng.random

        def ok(p: float) -> bool:
            return draw() < p

        verdict = math.exp(rng.gauss(mu, sigma))
        decided = max(verdict, a.talk_ready_ms)                   # the decision waits for the post-close mic window
        since = None if state.get("last_at") is None else max(0.0, t.ts.timestamp() - state["last_at"])
        ctx = RX.Ctx(
            rule="deep" if t.close_ms <= 640 else "quiet", speech_ms=1200 if ok(a.p_long) else 300,
            turn_kind="wake" if ok(a.p_wake) else "followup", local_hour=t.ts.hour, idle_h=idle_h, since_last_s=since,
            bank_voice="af_sky", current_voice="af_sky", takes=takes, committed=True,
            guest=False if ok(a.p_speaker) else True, speaker_accept=True, speaker_model=RX.KNOWN_SPEAKER_MODELS[0],
            verdict_ms=decided, media_active=False if ok(a.p_media_clear) else True, talking=False if ok(a.p_still) else True,
            noise_dbfs=-60.0 if ok(a.p_quiet) else -20.0)
        d = RX.decide(ctx, state, s=s, seed=rng.randrange(1 << 30))
        sound_ms = first_today
        if d.kind == "heard":
            onset = t.close_ms + decided + a.start_ms
            if onset < first_today:                                # the reflex only helps when it lands before the reply
                sound_ms = onset
                plays += 1
                played_ms.append(onset)
                state = RX.record(state, d, t.ts.timestamp())
            else:
                skips["reply_first"] = skips.get("reply_first", 0) + 1       # late-skip: the reply would have come first
        else:
            skips[d.skip] = skips.get(d.skip, 0) + 1
        today.append(first_today)
        with_reflex.append(sound_ms)
    n = len(today)

    def share(xs, bar):
        return sum(1 for x in xs if x < bar) / n if n else float("nan")

    return {"n": n, "plays": plays, "skips": dict(sorted(skips.items(), key=lambda kv: -kv[1])),
            "today": {"p50": _percentile(today, .5), "p90": _percentile(today, .9), **{f"under_{b}": share(today, b) for b in BANDS_MS}},
            "reflex": {"p50": _percentile(with_reflex, .5), "p90": _percentile(with_reflex, .9),
                       **{f"under_{b}": share(with_reflex, b) for b in BANDS_MS}},
            "reflex_onset_p50": statistics.median(played_ms) if played_ms else float("nan")}


def sensitivity(turns: "Sequence[Turn]", base: Assumptions = Assumptions()) -> "list[tuple[str, dict]]":
    """The ceiling, then the unobservable-rule pass probability dropped jointly, then timing variants."""
    rows = [("ceiling (every unobservable guard passes)", simulate(turns, base))]
    for f in (0.8, 0.6, 0.4):
        a = replace(base, p_speaker=f, p_quiet=f)
        rows.append((f"speaker and room each pass {f:.0%}", simulate(turns, a)))
    rows.append(("only 60% of turns are wake turns", simulate(turns, replace(base, p_wake=0.6))))
    for label, kw in (("decision ready at 160 ms (a fast mic)", dict(talk_ready_ms=160.0)),
                      ("decision ready at 320 ms (a slow mic, 250 ms deadline)", dict(talk_ready_ms=320.0)),
                      ("player start 200 ms", dict(start_ms=200.0)),
                      ("player start 300 ms", dict(start_ms=300.0))):
        rows.append((label, simulate(turns, replace(base, **kw))))
    return rows


def _pct(x: float) -> str:
    return "n/a" if x != x else f"{100 * x:.1f}%"


def report(turns: "Sequence[Turn]", label: str) -> str:
    out = [f"# Voice reflex projection ({label})", "",
           f"{len(turns)} turns" + (f" from {min(t.ts for t in turns):%Y-%m-%d %H:%M} to {max(t.ts for t in turns):%Y-%m-%d %H:%M}" if turns else "")
           + ". Log-derived timing, ASSUMED rules for what the log cannot state; see the module docstring.", ""]
    base = simulate(turns)
    t = base["today"]
    out += [f"today: first sound p50 {t['p50'] / 1000:.2f} s, p90 {t['p90'] / 1000:.2f} s, under 1 s {_pct(t['under_1000'])}, "
            f"under 0.6 s {_pct(t['under_600'])}", "",
            "| scenario | plays | share of turns | first sound p50 | under 1 s | under 0.6 s | reflex onset p50 |",
            "|---|---:|---:|---:|---:|---:|---:|"]
    for name, r in sensitivity(turns):
        out.append(f"| {name} | {r['plays']} | {_pct(r['plays'] / r['n']) if r['n'] else 'n/a'} | {r['reflex']['p50'] / 1000:.2f} s | "
                   f"{_pct(r['reflex']['under_1000'])} | {_pct(r['reflex']['under_600'])} | {r['reflex_onset_p50'] / 1000:.2f} s |")
    out += ["", "Why the reflex did not play (ceiling scenario; the real gate's own skip names, plus the lab's `reply_first`):", ""]
    for k, v in base["skips"].items():
        out.append(f"- `{k}`: {v} ({_pct(v / base['n'])})")
    out += ["", "Under 0.6 s is 0 by construction: the endpoint closes no sooner than the 640 ms floor, so no sound that follows "
            "the close can land inside 0.6 s."]
    return "\n".join(out)


DECISION_RE = re.compile(r"REFLEX_DECISION mode=(shadow|on) kind=(heard|none) skip=([a-z_-]+) take=\S+ decided_ms=(\d+) "
                         r"talk_obs_ms=(\d+) noise_db=(-?\d+) spk=(-1|0|1) media=(-1|0|1) win=([a-z]+) bank=\S+")


def summarise_decisions(lines: Iterable[str]) -> str:
    """The shadow week, from the panel's REFLEX_DECISION lines: the per-rule skip distribution and decision timing."""
    rows = [m for m in (DECISION_RE.search(x) for x in lines) if m]
    n = len(rows)
    if not n:
        return "no REFLEX_DECISION lines"
    skips: "dict[str, int]" = {}
    for m in rows:   # only the shipped skip enum is ever echoed: any other word (a forged or corrupt line) is "other"
        key = "would_play" if m.group(2) == "heard" else m.group(3) if m.group(3) in RX.LOG_SKIPS else "other"
        skips[key] = skips.get(key, 0) + 1
    dec = [int(m.group(4)) for m in rows]
    out = [f"{n} decisions; decided_ms p50 {_percentile(dec, .5):.0f} p95 {_percentile(dec, .95):.0f}", ""]
    out += [f"- {k}: {v} ({_pct(v / n)})" for k, v in sorted(skips.items(), key=lambda kv: -kv[1])]
    return "\n".join(out)


SAFETY_RE = re.compile(r"REFLEX_SAFETY mode=(shadow|on) would_play=([01]) class=(none|distress|medical|child_floor|refusal|unknown)$")
SAFETY_CLASSES = ("distress", "medical", "child_floor", "refusal")


def summarise_safety(lines: Iterable[str]) -> str:
    """The SHADOW'S OWN safety count, from the daemon's ``REFLEX_SAFETY`` lines: would-play turns per safety class, as the
    server classified them in the done frame. While every line is ``class=unknown`` (today: the server sends no ``safety``
    key) the count is UNAVAILABLE, and this says so rather than printing a zero."""
    rows = [m for m in (SAFETY_RE.search(x.rstrip()) for x in lines) if m]
    if not rows:
        return "safety count: no REFLEX_SAFETY lines"
    stated = [m for m in rows if m.group(3) != "unknown"]
    if not stated:
        return (f"safety count: UNAVAILABLE ({len(rows)} decided turns, every one class=unknown: the server sends no done-frame "
                "safety class yet; see --markers for the time-correlated lab measure, which cannot see refusals)")
    by = {c: sum(1 for m in stated if m.group(2) == "1" and m.group(3) == c) for c in SAFETY_CLASSES}
    out = [f"safety count (the shadow's own, from the server's done-frame class): {len(stated)} of {len(rows)} decided "
           f"turns classified; would-play turns before a safety reply: {sum(by.values())}"]
    out += [f"- {c}: {n}" for c, n in by.items()]
    return "\n".join(out)


TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
MARKER_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) (DISTRESS_HANDOFF|MEDICAL_SAFETY|CHILD_SAFETY_FLOOR)$")


def join_safety(decision_lines: Iterable[str], marker_lines: Iterable[str], window_s: float = 45.0) -> dict:
    """Would-play shadow decisions (``kind=heard``) that have a server safety marker (``DISTRESS_HANDOFF`` /
    ``MEDICAL_SAFETY`` / ``CHILD_SAFETY_FLOOR``) within ``window_s`` AFTER them. The panel cannot know a turn is a crisis,
    medical or child-floor turn at the close, so this is the MEASURE of the rule the design cannot enforce. Timestamps and the
    three marker names only: ``marker_lines`` is the allowlisted ``<timestamp> <NAME>`` extract, never a log line."""
    marks, bad = [], 0
    for x in marker_lines:
        m = MARKER_RE.match(x.strip())
        if m:
            try:
                marks.append((dt.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S"), m.group(2)))
            except ValueError:      # timestamp-shaped but not a real time (month 99): skipped and counted, never a crash
                bad += 1
    would, hits = 0, {}
    for x in decision_lines:
        if "REFLEX_DECISION" not in x or " kind=heard " not in x:
            continue
        t = TS_RE.match(x)
        if not t:
            continue
        try:
            ts = dt.datetime.strptime(t.group(1), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            bad += 1
            continue
        would += 1
        for mt, name in marks:
            if 0 <= (mt - ts).total_seconds() <= window_s:
                hits[name] = hits.get(name, 0) + 1
                break
    out = {"would_play": would, "near_marker": sum(hits.values()), "by_marker": hits}
    if bad:
        out["skipped_bad_timestamp"] = bad
    return out


def _positive_int(text: str) -> int:
    """argparse type: a whole number of at least 1 (0, a negative or a non-number is refused with a clear message)."""
    try:
        n = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected a whole number of 1 or more, got {text!r}") from None
    if n < 1:
        raise argparse.ArgumentTypeError(f"expected a whole number of 1 or more, got {n}")
    return n


def main(argv: "Optional[Sequence[str]]" = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--panel", action="store_true", help="read the allowlisted extract of the panel log over ssh (read-only)")
    g.add_argument("--lines", help="a saved allowlisted extract")
    g.add_argument("--synthetic", type=_positive_int, help="N invented turns (a self-check)")
    g.add_argument("--decisions", help="a file of REFLEX_DECISION lines: summarise the shadow week")
    ap.add_argument("--panel-host", default="zoe-pi")
    ap.add_argument("--markers", help="with --decisions: the allowlisted '<timestamp> <MARKER>' extract of the server log")
    a = ap.parse_args(argv)
    if a.decisions:
        dec = Path(a.decisions).read_text().splitlines()
        print(summarise_decisions(dec))
        print("\n" + summarise_safety(dec))
        if a.markers:
            print("\nLAB measure (a time correlation, not the shadow's count; blind to refusals): would-play turns with a "
                  "server safety marker within 45 s:", join_safety(dec, Path(a.markers).read_text().splitlines()))
        return 0
    if a.synthetic is not None:
        print(report(synthetic_turns(a.synthetic), "synthetic, invented turns"))
        return 0
    if a.panel:
        sc = _load("felt_scorecard_for_sim", "labs/felt-scorecard/scorecard.py")
        p = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", a.panel_host, sc.panel_command()],
                           capture_output=True, text=True, timeout=60)
        if p.returncode != 0:
            print("ssh to the panel failed", file=sys.stderr)
            return 2
        lines = p.stdout.splitlines()
        label = "panel log, allowlisted extract"
    else:
        lines = Path(a.lines).read_text().splitlines()
        label = "saved extract"
    skipped: dict = {}
    print(report(parse_turns(lines, skipped=skipped), label))
    if skipped:
        print(f"\nskipped rows (a timestamp that is not a real time): {skipped.get('bad_timestamp', 0)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
