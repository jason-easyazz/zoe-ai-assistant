"""Read-only parsing of the wall panel's ``voice.log`` (zoe_voice_daemon.py on the Pi) and the corpus join.

Shared by ``scripts/perf/measure_wake.py`` (wake FAR/FRR) and ``scripts/maintenance/speaker_fa_review.py`` (speaker
false-accept review). Stdlib only. Nothing here writes anywhere: the log is fetched with ``ssh <host> cat <file>``
(bounded) or read from a local copy, and the corpus is listed, never copied.

The log lines it understands are the daemon's own ``log.info`` formats (``scripts/setup/zoe_voice_daemon.py``); an
unknown line is ignored, so a new daemon message never breaks a parse. A turn is:

    trigger (``Wake word detected!`` | ``Orb-tap activation received`` | ``Follow-up speech detected``)
      -> ``Recorded command|follow-up: duration=X.XXs``      (X = the clip's length, pre-roll included)
      -> speaker lines (``Speaker claim: u (s)``, ``Speaker ID (dual, ResNet34): u score=s accept=b ...``,
         ``Speaker ID (shadow): u (s)``)
      -> outcome (``turn_stream TTFA=... transcript='...'`` | ``Empty transcript with no audio`` | ``Command too short``
         | ``Ignoring junk`` | ``turn_stream: not addressed`` | ``turn_stream: held``)

Transcripts are household data: callers that write files keep them OUTSIDE the repo (``~/.zoe/voice-gate/``).
"""
from __future__ import annotations

import datetime as dt
import os
import re
import bisect
import subprocess
import wave
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional

PANEL_HOST = "zoe-pi"
PANEL_LOG = "/home/pi/.zoe-voice/voice.log"
CORPUS_DIR = os.path.expanduser("~/.zoe-voice-samples")

_LINE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),(\d{3}) \[[^\]]*\] (\w+) (.*)$")
_NEAR = re.compile(r"wakeword near-miss: max_score=([0-9.]+) \(need ([0-9.]+)\)")
_RECORDED = re.compile(r"Recorded (command|follow-up): duration=([0-9.]+)s")
_CLAIM = re.compile(r"Speaker claim: (\S+) \((-?[0-9.]+)\)")
_DUAL = re.compile(r"Speaker ID \(dual, ResNet34\): (\S+) score=(-?[0-9.]+|None) accept=(\w+) source=(\w+)")
_SHADOW = re.compile(r"Speaker ID \(shadow\): (\S+) \((-?[0-9.]+)\)")
_TURN = re.compile(r"turn_stream TTFA=(-?[0-9.]+)s transcript=(.*)$")
_TRANSCRIPT_OLD = re.compile(r"^Transcript: (.*)$")


@dataclass
class Event:
    ts: dt.datetime
    kind: str
    data: dict = field(default_factory=dict)


@dataclass
class Turn:
    """One recorded utterance on the panel and what became of it."""
    trigger: str                      # wake | orb | followup
    trigger_ts: dt.datetime
    recorded_ts: Optional[dt.datetime] = None
    duration_s: Optional[float] = None
    claim_user: Optional[str] = None  # the ACTING model's claim (``Speaker claim:``)
    claim_score: Optional[float] = None
    dual_user: Optional[str] = None   # ResNet34 logged beside an acting Resemblyzer (``dual``)
    dual_score: Optional[float] = None
    dual_accept: Optional[bool] = None
    shadow_user: Optional[str] = None  # Resemblyzer shadow line
    shadow_score: Optional[float] = None
    transcript: Optional[str] = None
    outcome: str = "none"             # turn | empty | too_short | junk | not_addressed | held | none


def parse_ts(day: str, ms: str) -> dt.datetime:
    return dt.datetime.strptime(day, "%Y-%m-%d %H:%M:%S").replace(microsecond=int(ms) * 1000)


def _unquote(raw: str) -> str:
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "'\"":
        return raw[1:-1]
    return raw


def events(lines: Iterable[str]) -> list:
    """Every line the turn model cares about, in log order."""
    out: list = []
    for line in lines:
        m = _LINE.match(line.rstrip("\n"))
        if not m:
            continue
        ts = parse_ts(m.group(1), m.group(2))
        msg = m.group(4)
        if msg.startswith("Wake word detected"):
            out.append(Event(ts, "wake"))
        elif msg.startswith("Orb-tap activation received"):
            out.append(Event(ts, "orb"))
        elif msg.startswith("Follow-up speech detected"):
            out.append(Event(ts, "followup"))
        elif msg.startswith("Loading wake word model"):
            out.append(Event(ts, "daemon_start"))
        elif (n := _NEAR.search(msg)):
            out.append(Event(ts, "near_miss", dict(score=float(n.group(1)), need=float(n.group(2)))))
        elif (r := _RECORDED.search(msg)):
            out.append(Event(ts, "recorded", dict(which=r.group(1), duration=float(r.group(2)))))
        elif (c := _CLAIM.search(msg)):
            out.append(Event(ts, "claim", dict(user=c.group(1), score=float(c.group(2)))))
        elif (d := _DUAL.search(msg)):
            score = None if d.group(2) == "None" else float(d.group(2))
            out.append(Event(ts, "dual", dict(user=d.group(1), score=score, accept=d.group(3) == "True",
                                              source=d.group(4))))
        elif (s := _SHADOW.search(msg)):
            out.append(Event(ts, "shadow", dict(user=s.group(1), score=float(s.group(2)))))
        elif (t := _TURN.search(msg)):
            out.append(Event(ts, "turn", dict(transcript=_unquote(t.group(2)))))
        elif (o := _TRANSCRIPT_OLD.match(msg)):
            out.append(Event(ts, "turn", dict(transcript=_unquote(o.group(1)))))
        elif msg.startswith("Empty transcript with no audio") or msg.startswith("Empty follow-up transcript"):
            out.append(Event(ts, "empty"))
        elif msg.startswith("Command too short"):
            out.append(Event(ts, "too_short"))
        elif msg.startswith("Ignoring junk"):
            out.append(Event(ts, "junk"))
        elif msg.startswith("turn_stream: not addressed"):
            out.append(Event(ts, "not_addressed"))
        elif msg.startswith("turn_stream: held"):
            out.append(Event(ts, "held"))
    return out


ORB_WAKE_COALESCE_S = 1.0
_OUTCOMES = ("turn", "empty", "too_short", "junk", "not_addressed", "held")


def turns(evs: Iterable[Event]) -> list:
    """Group events into turns. A trigger opens a turn; the next trigger (or a daemon restart) closes it. The first
    outcome line wins."""
    out: list = []
    cur: Optional[Turn] = None
    for e in evs:
        if e.kind in ("wake", "orb", "followup"):
            # An orb tap makes the daemon call on_wake(), which logs "Wake word detected!" ~instantly: that line belongs
            # to the orb turn, it is not an acoustic wake-phrase positive.
            if (e.kind == "wake" and cur is not None and cur.trigger == "orb"
                    and 0 <= (e.ts - cur.trigger_ts).total_seconds() <= ORB_WAKE_COALESCE_S):
                continue
            cur = Turn(trigger=e.kind, trigger_ts=e.ts)
            out.append(cur)
            continue
        if e.kind == "daemon_start":
            cur = None
            continue
        if cur is None:
            continue
        if e.kind == "recorded" and cur.recorded_ts is None:
            cur.recorded_ts, cur.duration_s = e.ts, e.data["duration"]
        elif e.kind == "claim" and cur.claim_score is None:
            cur.claim_user, cur.claim_score = e.data["user"], e.data["score"]
        elif e.kind == "dual" and cur.dual_user is None:
            cur.dual_user, cur.dual_score, cur.dual_accept = e.data["user"], e.data["score"], e.data["accept"]
        elif e.kind == "shadow" and cur.shadow_score is None:
            cur.shadow_user, cur.shadow_score = e.data["user"], e.data["score"]
        elif e.kind in _OUTCOMES and cur.outcome == "none":
            cur.outcome = e.kind
            if e.kind == "turn":
                cur.transcript = e.data.get("transcript")
    return out


def fetch_lines(local_path: Optional[str] = None, *, host: str = PANEL_HOST, remote_path: str = PANEL_LOG,
                timeout_s: int = 30, runner: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> list:
    """The panel log's lines: a local copy when ``local_path`` is given, else read over ssh (read-only, BatchMode,
    bounded). Raises ``RuntimeError`` when the Pi cannot be read - a caller must not report on nothing."""
    if local_path:
        with open(os.path.expanduser(local_path), "r", encoding="utf-8", errors="replace") as fh:
            return fh.read().splitlines()
    cmd = ["timeout", str(timeout_s), "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", host,
           "cat " + remote_path]
    res = runner(cmd, capture_output=True, text=True, errors="replace", timeout=timeout_s + 5, check=False)
    if res.returncode != 0 or not (res.stdout or "").strip():
        raise RuntimeError(f"could not read {host}:{remote_path} (exit {res.returncode})")
    return res.stdout.splitlines()


@dataclass
class Clip:
    path: str
    ts: dt.datetime        # capture time (local): the file's mtime DATE + the HHMMSS_mmm in its name
    duration_s: float


_NAME = re.compile(r"^(\d{2})(\d{2})(\d{2})_(\d{3})\.wav$")


def clip_time(name: str, mtime: float) -> Optional[dt.datetime]:
    """Capture time of a corpus clip ``HHMMSS_mmm.wav`` (voice_tts._maybe_capture_stt). The name has no date, so the
    mtime supplies it; a name more than 120 s from its mtime's clock (a copied / restored file) is unusable -> None."""
    m = _NAME.match(name)
    if not m:
        return None
    mt = dt.datetime.fromtimestamp(mtime)
    try:
        t = mt.replace(hour=int(m.group(1)), minute=int(m.group(2)), second=int(m.group(3)),
                       microsecond=int(m.group(4)) * 1000)
    except ValueError:
        return None
    for cand in (t, t - dt.timedelta(days=1), t + dt.timedelta(days=1)):  # a capture straddling midnight
        if abs((cand - mt).total_seconds()) <= 120:
            return cand
    return None


def wav_seconds(path: str) -> Optional[float]:
    try:
        with wave.open(path, "rb") as w:
            return w.getnframes() / float(w.getframerate())
    except (OSError, EOFError, wave.Error):
        return None


def corpus_index(directory: str = CORPUS_DIR) -> list:
    """Top-level ``*.wav`` of the corpus (quarantine sub-folders excluded), each with its capture time and length."""
    out: list = []
    d = os.path.expanduser(directory)
    if not os.path.isdir(d):
        return out
    for name in sorted(os.listdir(d)):
        p = os.path.join(d, name)
        if not name.endswith(".wav") or not os.path.isfile(p):
            continue
        ts = clip_time(name, os.path.getmtime(p))
        secs = wav_seconds(p)
        if ts is None or secs is None:
            continue
        out.append(Clip(p, ts, secs))
    return out


def match_clips(tns: list, clips: list, *, max_lag_s: float = 30.0, early_s: float = 3.0,
                dur_tol_s: float = 0.06) -> dict:
    """turn index -> Clip. A clip belongs to the turn whose ``Recorded ...`` line precedes its capture by at most
    ``max_lag_s`` (or follows it by at most ``early_s``: two clocks), with the SAME length (the daemon logs the clip's
    exact duration, pre-roll included). Each clip is used once; the closest in time wins."""
    out: dict = {}
    used: set = set()
    order = sorted(range(len(clips)), key=lambda i: clips[i].ts)
    stamps = [clips[i].ts for i in order]
    for ti, t in enumerate(tns):
        if t.recorded_ts is None or t.duration_s is None:
            continue
        lo = bisect.bisect_left(stamps, t.recorded_ts - dt.timedelta(seconds=early_s))
        best, best_gap = None, None
        for k in range(lo, len(order)):
            ci = order[k]
            c = clips[ci]
            gap = (c.ts - t.recorded_ts).total_seconds()
            if gap > max_lag_s:
                break
            if ci in used or abs(c.duration_s - t.duration_s) > dur_tol_s:
                continue
            if best_gap is None or abs(gap) < best_gap:
                best, best_gap = ci, abs(gap)
        if best is not None:
            used.add(best)
            out[ti] = clips[best]
    return out
