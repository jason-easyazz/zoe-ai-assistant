#!/usr/bin/env python3
"""Felt scorecard: is Zoe's memory circuit closed on real household turns?

Read-only, counts and timings only. Reproduces the hand measurement of
2026-11-11 (docs/research/samantha-frontier-2026-11-11.md sections 1 and 3;
methods from lanes 1, 4 and 7 of that bundle) as one command.

Sources, all opened read-only:
  - zoe-data app logs (~/.zoe-logs/zoe-data.app.log*, plain and .gz rotations):
    marker lines, parsed for numeric fields only. No log line is ever printed.
    A window the retained rotations do not provably cover (starts before the
    earliest line, a damaged rotation, or a missing one) is `unmeasured`.
  - Postgres (POSTGRES_URL, resolved exactly as scripts/maintenance/
    purge_orphaned_test_data.py does): a read-only session and a read-only
    transaction; every query goes through _agg(), which refuses anything but one
    SELECT and any non-numeric result. Member classification of log lines sends
    the candidate ids/sessions INTO a query and gets back only ordinals.
  - MemPalace chroma.sqlite3, mode=ro; one GROUP BY over metadata keys
    user_id / source / status (see durable_rows()). Never the document text.
  - night-window.log: NIGHT_WINDOW_SUMMARY outcome per run.
  - ALERTS rows: crash-loop-watch.log, night-reports/*.json, backup-verify.log (+ the drill state file), health.log and
    two app-log markers; counts and fixed (allowlisted) labels only. See the README table.
  - Optional (--panel): the panel daemon log on the Pi over ssh. The Pi side
    runs an allowlist sed program that emits only a timestamp, a one-letter tag
    and a number or enum per line; nothing else can cross the host boundary.

Anything that cannot be taken read-only, or for the full window, prints as
`unmeasured` with the reason. Unmeasured is never shown as zero.

Usage:
  python3 labs/felt-scorecard/scorecard.py                # last 7 / 30 days
  python3 labs/felt-scorecard/scorecard.py --json
  python3 labs/felt-scorecard/scorecard.py --since 2026-11-05T00:00 --until 2026-11-11T22:30 \
      --long-since 2026-10-12T08:00                       # the 11-11 windows
"""
from __future__ import annotations

import argparse
import asyncio
import bisect
import datetime as dt
import glob
import gzip
import json
import os
import re
import shlex
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Iterable

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "services", "zoe-data"))
sys.path.insert(0, os.path.join(REPO, "scripts", "maintenance"))

from user_filters import GUEST_USERS, GUEST_PREFIX, SYNTHETIC_USER_RE  # noqa: E402  (stdlib-only module)

LOCAL_TZ = dt.datetime.now().astimezone().tzinfo

# ---------------------------------------------------------------------------
# Log markers (lane 1 / lane 4 / lane 7 definitions)
# ---------------------------------------------------------------------------

MARKERS = (
    "FLUE_TOOLS", "SEAM_CONTINUITY", "PROVENANCE_REPLY", "RECALL_EVERY_TURN",
    "VOICE TIMING", "DAY_REVIEW_RAISE", "VALUE_CORRECTION",
    # ALERTS section (counted only; never member-joined, never printed): see alert_* below
    "DISTRESS_NO_CONTACT", "PANEL_KV_MISS", "kv_miss_alert: alert not delivered",
    # INSTRUMENTS section (counted only; never member-joined, never printed): see inst_* below
    "EVENT_LOOP_BLOCK ms=", "FORGET_LEDGER state=", "FLUE_PREPOST ", "FORGET_CORRECTION_HELD ",
)
USER_MARKERS = ("SEAM_CONTINUITY", "PROVENANCE_REPLY", "RECALL_EVERY_TURN", "DAY_REVIEW_RAISE", "VALUE_CORRECTION")
SESSION_MARKERS = ("FLUE_TOOLS", "VOICE TIMING")

# Lane 1 traffic split: a line is harness when it carries traffic=harness, when
# its user= matches SYNTHETIC_USER_RE, or when its session starts qa/demo/bench/
# probe/test. Extended here (defensive, same intent) with bar- and replay-, the
# bar harness and the voice-replay gate session prefixes.
HARNESS_SESSION_PREFIXES = ("qa", "demo", "bench", "probe", "test", "bar-", "replay-")

_TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})([+-]\d{4})?")
_KV_RE = re.compile(r"\b([a-z_]+)=(\S*)")
_TOOLS_RE = re.compile(r"\btools=\[([^\]]*)\]")


@dataclass
class Members:
    """Log user= values and session= values that Postgres confirmed belong to a real member account.
    Kept in memory only; never printed."""
    users: set[str] = field(default_factory=set)
    sessions: set[str] = field(default_factory=set)


_TZ_CACHE: dict[str, dt.tzinfo] = {}


def parse_ts(line: str) -> dt.datetime | None:
    # Fast path for the zoe-data shape "YYYY-MM-DDTHH:MM:SS+HHMM" (hundreds of thousands of lines per run).
    if len(line) >= 24 and line[4] == "-" and line[10] == "T" and line[19] in "+-" and line[13] == ":":
        try:
            off = line[19:24]
            tz = _TZ_CACHE.get(off)
            if tz is None:
                sign = 1 if off[0] == "+" else -1
                tz = _TZ_CACHE[off] = dt.timezone(sign * dt.timedelta(hours=int(off[1:3]), minutes=int(off[3:5])))
            return dt.datetime(int(line[0:4]), int(line[5:7]), int(line[8:10]), int(line[11:13]),
                               int(line[14:16]), int(line[17:19]), tzinfo=tz)
        except ValueError:
            pass
    m = _TS_RE.match(line)
    if not m:
        return None
    raw = m.group(1) + (m.group(2) or "")
    fmt = "%Y-%m-%dT%H:%M:%S%z" if m.group(2) else "%Y-%m-%dT%H:%M:%S"
    try:
        t = dt.datetime.strptime(raw, fmt)
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=LOCAL_TZ)


def fields(line: str) -> dict[str, str]:
    """key=value fields of a marker line (first occurrence wins)."""
    out: dict[str, str] = {}
    for k, v in _KV_RE.findall(line):
        out.setdefault(k, v)
    return out


#: logging_setup.TrafficClassFormatter suffixes every line written in a NON-household context with its class
#: (harness, probe, ci, ...). Any class but an explicit household one is not the household's traffic.
_TRAFFIC_RE = re.compile(r"(?:^|\s)traffic=([A-Za-z_-]+)")
_HOUSEHOLD_TRAFFIC = frozenset({"household"})


def is_harness(line: str, f: dict[str, str]) -> bool:
    m = _TRAFFIC_RE.search(line)
    if m and m.group(1).lower() not in _HOUSEHOLD_TRAFFIC:
        return True
    user = f.get("user")
    if user is not None and SYNTHETIC_USER_RE.match(user.strip()):
        return True
    sess = f.get("session", "")
    return bool(sess) and sess.lower().startswith(HARNESS_SESSION_PREFIXES)


def _open_log(path: str):
    if path.endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")  # streaming, never fully in memory
    return open(path, encoding="utf-8", errors="replace")


@dataclass
class Rotation:
    """One log file as read: its own first/last timestamps, its largest internal gap, and any read error."""
    path: str
    first: dt.datetime | None = None
    last: dt.datetime | None = None
    max_gap_s: float = 0.0
    error: str | None = None
    lines: list = field(default_factory=list)  # (ts, line) marker lines, file order, multiplicity kept


@dataclass
class LogSet:
    lines: list  # (ts, line), time-sorted, overlap between consecutive rotations removed
    rotations: list  # Rotation, sorted by first timestamp (unreadable ones last)

    @property
    def earliest(self) -> dt.datetime | None:
        firsts = [r.first for r in self.rotations if r.first is not None]
        return min(firsts) if firsts else None


MIN_ROTATION_GAP_S = 600.0  # floor for the gap bound; the bound is the largest gap seen inside any rotation


def read_rotation(path: str, markers: tuple[str, ...]) -> Rotation:
    rot = Rotation(path)
    prev: dt.datetime | None = None
    key, cached = "", None  # many lines share a second: parse each distinct timestamp prefix once
    try:
        with _open_log(path) as fh:
            for line in fh:
                if line[:24] != key:
                    key, cached = line[:24], parse_ts(line)
                t = cached
                if t is not None:
                    if rot.first is None:
                        rot.first = t
                    if prev is not None and t > prev:
                        rot.max_gap_s = max(rot.max_gap_s, (t - prev).total_seconds())
                    prev = t
                    rot.last = t if rot.last is None or t > rot.last else rot.last
                if t is not None and any(m in line for m in markers):
                    rot.lines.append((t, line.rstrip("\n")))
    except (OSError, EOFError, gzip.BadGzipFile, UnicodeError) as exc:
        rot.error = type(exc).__name__
    return rot


def _drop_overlap(earlier: Rotation, later: Rotation) -> list:
    """Lines of `later` minus those also present in `earlier` within the two files' overlapping span.
    Multiplicity is kept: a line repeated N times inside one file stays N times."""
    if earlier.last is None or later.first is None or later.first > earlier.last:
        return list(later.lines)
    counts: dict[str, int] = {}
    for t, line in earlier.lines:
        if t >= later.first:
            counts[line] = counts.get(line, 0) + 1
    out = []
    for t, line in later.lines:
        if t <= earlier.last and counts.get(line, 0) > 0:
            counts[line] -= 1
            continue
        out.append((t, line))
    return out


def load_log_lines(paths: Iterable[str], markers: Iterable[str] = MARKERS) -> LogSet:
    """Marker lines from every rotation (plain or gzip), time-sorted. Only the overlap between consecutive
    rotations is de-duplicated; repeated events inside one file are kept."""
    markers = tuple(markers)
    rots = [read_rotation(p, markers) for p in paths]
    rots.sort(key=lambda r: (r.first is None, r.first or dt.datetime.min.replace(tzinfo=dt.timezone.utc)))
    out: list = []
    prev: Rotation | None = None
    for r in rots:
        out.extend(r.lines if prev is None else _drop_overlap(prev, r))
        if r.first is not None:
            prev = r
    out.sort(key=lambda x: x[0])
    return LogSet(out, rots)


def _fmt(t: dt.datetime) -> str:
    return f"{t.astimezone(LOCAL_TZ):%Y-%m-%d %H:%M}"


def coverage_reason(logs: LogSet, since: dt.datetime, until: dt.datetime) -> str | None:
    """None when the retained logs provably cover [since, until), else why the window cannot be measured:
    no logs, logs start after the window, a rotation touching the window could not be read in full, or a gap
    between consecutive rotations larger than any gap inside a rotation (a missing rotation)."""
    if not logs.rotations or logs.earliest is None:
        return "no readable zoe-data app log found"
    if logs.earliest > since:
        return f"retained app logs start {_fmt(logs.earliest)}, after the window start {_fmt(since)}; a partial count is not reported"
    for r in logs.rotations:
        # A damaged file's true span is unknown past what was read, so it counts unless it provably starts after the window.
        if r.error and (r.first is None or r.first < until):
            name = os.path.basename(r.path)
            return f"log rotation {name} could not be read in full ({r.error}); a partial count is not reported"
    timed = [r for r in logs.rotations if r.first is not None]
    bound = gap_bound(logs)
    for a, b in zip(timed, timed[1:]):
        gap = (b.first - a.last).total_seconds()
        if gap > bound and a.last < until and b.first > since:
            return (f"gap of {gap / 3600:.1f} h between log rotations ({_fmt(a.last)}..{_fmt(b.first)}) exceeds any gap "
                    f"inside a rotation ({bound / 3600:.1f} h): a rotation is missing; a partial count is not reported")
    latest = max(r.last for r in timed if r.last is not None)
    if latest < until - dt.timedelta(seconds=bound):
        return (f"retained app logs end {_fmt(latest)}, more than {bound / 60:.0f} min before the window end {_fmt(until)} "
                f"(stale or stopped log); a partial count is not reported")
    return None


def gap_bound(logs: LogSet) -> float:
    """Tolerance in seconds: the largest gap between lines inside any rotation, floor MIN_ROTATION_GAP_S."""
    return max([MIN_ROTATION_GAP_S] + [r.max_gap_s for r in logs.rotations if r.first is not None])


def in_window(lines, since, until, marker):
    for t, line in lines:
        if since <= t < until and marker in line:
            yield t, line


def candidate_values(lines, since, until) -> tuple[list[str], list[str]]:
    """Distinct non-harness user= values (user markers) and session= values (session markers) in window."""
    users: set[str] = set()
    sessions: set[str] = set()
    for t, line in lines:
        if not (since <= t < until):
            continue
        f = fields(line)
        if is_harness(line, f):
            continue
        if any(m in line for m in USER_MARKERS) and f.get("user"):
            users.add(f["user"])
        if any(m in line for m in SESSION_MARKERS) and f.get("session"):
            sessions.add(f["session"])
    return sorted(users), sorted(sessions)


# ---------------------------------------------------------------------------
# Small statistics helpers
# ---------------------------------------------------------------------------

def pct(values: list[float], q: float) -> float | None:
    """Percentile by linear interpolation between closest ranks (q in 0..100)."""
    if not values:
        return None
    s = sorted(values)
    if len(s) == 1:
        return float(s[0])
    k = (len(s) - 1) * q / 100.0
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return float(s[lo] + (s[hi] - s[lo]) * (k - lo))


def _num(v: str | None, default: str = "0") -> float:
    """A marker's numeric field; raises Malformed when it is not a number."""
    try:
        return float(default if v is None or v == "" else v)
    except ValueError:
        raise Malformed() from None


class Malformed(Exception):
    """A marker line whose numeric field is not a number."""


def malformed_metric(key: str, title: str, n_bad: int, since, until) -> dict:
    return unmeasured(key, title, f"malformed marker ({n_bad} line(s) with a non-numeric field)", window=wstr(since, until))


def frac(a: int, n: int) -> str:
    return f"{a} of {n}" + (f" ({100.0 * a / n:.1f}%)" if n else "")


# ---------------------------------------------------------------------------
# Metric record
# ---------------------------------------------------------------------------

def metric(key: str, title: str, value: Any, *, n: Any = None, window: str = "", method: str = "",
           label: str = "[Proven]", data: dict | None = None) -> dict:
    return {"key": key, "title": title, "status": "measured", "value": value, "n": n,
            "window": window, "method": method, "label": label, "data": data or {}}


def unmeasured(key: str, title: str, reason: str, *, window: str = "", method: str = "") -> dict:
    return {"key": key, "title": title, "status": "unmeasured", "value": "unmeasured", "n": None,
            "window": window, "method": method, "label": "", "reason": reason, "data": {}}


def wstr(since: dt.datetime, until: dt.datetime) -> str:
    return f"{since.astimezone(LOCAL_TZ):%Y-%m-%d %H:%M}..{until.astimezone(LOCAL_TZ):%Y-%m-%d %H:%M}"


# ---------------------------------------------------------------------------
# Log metrics (pure functions over parsed lines; unit-tested)
# ---------------------------------------------------------------------------

def m_provenance(lines, since, until, members: Members) -> dict:
    n = served = used = bad = 0
    for _, line in in_window(lines, since, until, "PROVENANCE_REPLY"):
        f = fields(line)
        if f.get("kind") != "brain" or is_harness(line, f) or f.get("user") not in members.users:
            continue
        try:
            sv, us = _num(f.get("served")), _num(f.get("used"))
        except Malformed:
            bad += 1
            continue
        n += 1
        served += sv > 0
        used += us > 0
    if bad:
        return malformed_metric("memory_served_used", "Memory served / used per member brain reply", bad, since, until)
    return metric("memory_served_used", "Memory served / used per member brain reply",
                  f"served {frac(served, n)}; used {frac(used, n)}", n=n, window=wstr(since, until),
                  method="PROVENANCE_REPLY kind=brain, member user=, harness excluded; served>0 / used>0",
                  label="[Experimental] (used= is a text-overlap proxy)",
                  data={"n": n, "served": served, "used": used})


# Selection.skipped reasons in services/zoe-data/recall_every_turn.py (comment at :75, plus "warming"); anything
# else is printed as `other`, so no log-derived string becomes an output key or label.
SKIP_REASONS = frozenset({"not_household", "guest", "unverified", "off_record", "empty", "timeout", "error", "warming"})


def m_recall_every_turn(lines, since, until, members: Members) -> dict:
    n = eligible = found = claimed = injected = bad = 0
    skipped: dict[str, int] = {}
    first = None
    for t, line in in_window(lines, since, until, "RECALL_EVERY_TURN"):
        f = fields(line)
        if is_harness(line, f) or f.get("user") not in members.users:
            continue
        first = first or t
        sk = f.get("skipped", "-")
        try:
            rows, inj = _num(f.get("rows")), _num(f.get("injected"))
        except Malformed:
            bad += 1
            continue
        n += 1
        if sk != "-":
            sk = sk if sk in SKIP_REASONS else "other"
            skipped[sk] = skipped.get(sk, 0) + 1
            continue
        eligible += 1
        found += rows > 0
        claimed += f.get("claimed", "-") not in ("-", "")
        injected += inj > 0
    if bad:
        return malformed_metric("recall_every_turn", "Every-turn recall (shadow)", bad, since, until)
    sk_txt = ", ".join(f"{k} {v}" for k, v in sorted(skipped.items())) or "none"
    return metric("recall_every_turn", "Every-turn recall (shadow): relevant row found on eligible member turns",
                  f"found {frac(found, eligible)}; claimed by another block {claimed}; injected {injected}",
                  n=eligible, window=wstr(since, until) + (f" (first marker {first.astimezone(LOCAL_TZ):%m-%d %H:%M})" if first else ""),
                  method=f"RECALL_EVERY_TURN member lines ({n}), skipped=- are eligible; rows>0 found; skipped: {sk_txt}",
                  data={"lines": n, "eligible": eligible, "found": found, "claimed": claimed,
                        "injected": injected, "skipped": skipped})


def m_continuity(lines, since, until, members: Members) -> dict:
    n = matched = 0
    for _, line in in_window(lines, since, until, "SEAM_CONTINUITY"):
        f = fields(line)
        if is_harness(line, f) or f.get("user") not in members.users:
            continue
        n += 1
        matched += f.get("matched") == "True"
    return metric("continuity", "Continuity carried (matched) on member brain turns", frac(matched, n), n=n,
                  window=wstr(since, until), method="SEAM_CONTINUITY matched=True, member user=, harness excluded",
                  data={"n": n, "matched": matched})


def m_tools(lines, since, until, members: Members) -> dict:
    mem = {"n": 0, "tool": 0, "recall": 0}
    other = {"n": 0, "tool": 0, "recall": 0}
    hz = {"n": 0, "tool": 0, "recall": 0}
    for _, line in in_window(lines, since, until, "FLUE_TOOLS"):
        f = fields(line)
        if is_harness(line, f):
            d = hz
        elif f.get("session") in members.sessions:
            d = mem
        else:
            d = other  # guest or unknown session
        d["n"] += 1
        m = _TOOLS_RE.search(line)
        d["tool"] += bool(m and m.group(1).strip())
        d["recall"] += f.get("recall_memory") == "called:yes"
    return metric("tool_calls", "Tool calls / recall_memory calls per member brain turn",
                  f"tool calls {frac(mem['tool'], mem['n'])}; recall_memory {frac(mem['recall'], mem['n'])}",
                  n=mem["n"], window=wstr(since, until),
                  method=(f"FLUE_TOOLS tools=[...] non-empty / recall_memory=called:yes on member sessions "
                          f"(chat_sessions of a real member account); other (guest/unknown) non-harness: "
                          f"tools {other['tool']}, recall {other['recall']} of {other['n']}; "
                          f"harness control: tools {hz['tool']}, recall {hz['recall']} of {hz['n']}"),
                  data={"member": mem, "other": other, "harness": hz})


def m_brain_first_text(lines, since, until, members: Members) -> dict:
    ttft: list[float] = []
    pre_plus: list[float] = []
    other = bad = no_token = 0
    for _, line in in_window(lines, since, until, "VOICE TIMING"):
        f = fields(line)
        if is_harness(line, f) or "brain_ttft_ms" not in f:
            continue
        if f.get("session") not in members.sessions:
            other += 1
            continue
        try:
            b = _num(f["brain_ttft_ms"])
            pre = _num(f["pre_brain_ms"]) if "pre_brain_ms" in f else None
        except Malformed:
            bad += 1
            continue
        if b < 0:                     # brain_ttft_ms=-1 is the "no token arrived" sentinel (a timed-out / interrupted turn), not a latency
            no_token += 1
            continue
        ttft.append(b)
        if pre is not None and pre >= 0:
            pre_plus.append(pre + b)
    title = "Brain dispatch to first text (member voice turns)"
    if bad:
        return malformed_metric("brain_first_text", title, bad, since, until)
    if not ttft:
        return unmeasured("brain_first_text", title,
                          f"no member VOICE TIMING line with a first token in window (no-token turns: {no_token}, other: {other})",
                          window=wstr(since, until))
    # Each timing stands on its own samples: E+F only from lines carrying BOTH fields, and `unmeasured` when none do.
    ef = (f"pre_brain+brain_ttft p50 {pct(pre_plus, 50) / 1000:.2f} s (n={len(pre_plus)})" if pre_plus
          else "pre_brain+brain_ttft unmeasured (no line carries pre_brain_ms)")
    val = f"brain_ttft p50 {pct(ttft, 50) / 1000:.2f} s / p90 {pct(ttft, 90) / 1000:.2f} s; {ef}"
    return metric("brain_first_text", title, val, n=len(ttft), window=wstr(since, until),
                  method=(f"VOICE TIMING brain_ttft_ms on member sessions (lane 1 10b); pre_brain_ms+brain_ttft_ms "
                          f"is lane 4 E+F; other (guest/unknown) lines excluded: {other}; no-token turns "
                          f"(brain_ttft_ms=-1) excluded: {no_token}"),
                  data={"brain_ttft_p50_ms": pct(ttft, 50), "brain_ttft_p90_ms": pct(ttft, 90),
                        "pre_plus_ttft_p50_ms": pct(pre_plus, 50), "pre_plus_ttft_p90_ms": pct(pre_plus, 90),
                        "other": other, "no_token": no_token})


def m_corrections(lines, since, until, members: Members) -> dict:
    """Corrections a member made that changed stored memory ("it is the 19th, not the 12th"), and the two ways one goes wrong.
    APPLIED carries rows= (stored rows rewritten); STALE and KIND_GUESS are the idle-consolidation lane's refusals."""
    applied = rows = stale = kind = bad = 0
    for _, line in in_window(lines, since, until, "VALUE_CORRECTION"):
        f = fields(line)
        if is_harness(line, f) or f.get("user") not in members.users:
            continue
        if "VALUE_CORRECTION_APPLIED" in line:
            try:
                r = _num(f.get("rows"))
            except Malformed:
                bad += 1
                continue
            applied += 1
            rows += int(r)
        elif "VALUE_CORRECTION_STALE" in line:
            stale += 1
        elif "VALUE_CORRECTION_KIND_GUESS" in line:
            kind += 1
    title = "Corrections applied to stored memory (member)"
    if bad:
        return malformed_metric("value_corrections", title, bad, since, until)
    return metric("value_corrections", title,
                  f"applied {applied} ({rows} rows rewritten); stale re-filing stopped {stale}; kind guess stopped {kind}",
                  n=applied, window=wstr(since, until),
                  method=("VALUE_CORRECTION_APPLIED lines (rows= summed), VALUE_CORRECTION_STALE and VALUE_CORRECTION_KIND_GUESS "
                          "lines, member user=, harness excluded; a count of what happened, not a success rate (a correction "
                          "that matched no stored row writes no line)"),
                  data={"applied": applied, "rows": rows, "stale": stale, "kind_guess": kind})


def count_raises(logs: LogSet, since, until) -> tuple[int | None, str | None]:
    """DAY_REVIEW_RAISE raised=1 lines (non-harness) in window, or (None, reason) when the logs do not cover it."""
    reason = coverage_reason(logs, since, until)
    if reason:
        return None, reason
    n = 0
    for _, line in in_window(logs.lines, since, until, "DAY_REVIEW_RAISE"):
        f = fields(line)
        if not is_harness(line, f) and f.get("raised") == "1":
            n += 1
    return n, None


LOG_METRICS = (
    ("memory_served_used", "Memory served / used per member brain reply", m_provenance),
    ("recall_every_turn", "Every-turn recall (shadow)", m_recall_every_turn),
    ("continuity", "Continuity carried", m_continuity),
    ("tool_calls", "Tool calls per member brain turn", m_tools),
    ("brain_first_text", "Brain dispatch to first text", m_brain_first_text),
    ("value_corrections", "Corrections applied to stored memory", m_corrections),
)


def log_section(logs: LogSet, since, until, members: Members | None, members_reason: str = "") -> dict[str, dict]:
    """Every short-window log metric, or `unmeasured` for all of them when the logs do not cover the window
    or the member join is unavailable."""
    reason = coverage_reason(logs, since, until)
    out: dict[str, dict] = {}
    for key, title, fn in LOG_METRICS:
        if reason:
            out[key] = unmeasured(key, title, reason, window=wstr(since, until))
        elif members is None:
            out[key] = unmeasured(key, title, f"member lines need the member join from the database: {members_reason}")
        else:
            out[key] = fn(logs.lines, since, until, members)
    return out


# ---------------------------------------------------------------------------
# Night window
# ---------------------------------------------------------------------------

_NIGHT_RE = re.compile(r"NIGHT_WINDOW_SUMMARY run=(\d{8})-\d{6} outcome='([^']*)'")


def night_outcomes(text_lines: Iterable[str]) -> dict[str, bool]:
    """night date (YYYYMMDD) -> True if any summary for that night had outcome ok."""
    out: dict[str, bool] = {}
    for line in text_lines:
        m = _NIGHT_RE.search(line)
        if m:
            ok = m.group(2).startswith("ok")
            out[m.group(1)] = out.get(m.group(1), False) or ok
    return out


def m_nights(path: str, until: dt.datetime, nights: int) -> dict:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            outcomes = night_outcomes(l for l in fh if "NIGHT_WINDOW_SUMMARY" in l)
    except OSError as exc:
        return unmeasured("night_windows", f"Night windows completed, last {nights}", f"cannot read night-window.log ({type(exc).__name__})")
    end = until.astimezone(LOCAL_TZ).date()
    days = [end - dt.timedelta(days=i) for i in range(nights)][::-1]
    keys = [d.strftime("%Y%m%d") for d in days]
    if not outcomes or min(outcomes) > keys[0]:
        return unmeasured("night_windows", f"Night windows completed, last {nights}",
                          "night-window.log does not reach back to the first night of the window")
    ok = sum(1 for k in keys if outcomes.get(k))
    no_summary = sum(1 for k in keys if k not in outcomes)
    return metric("night_windows", f"Night windows completed, last {nights}", f"{ok} of {nights}", n=nights,
                  window=f"nights {days[0]}..{days[-1]}",
                  method=f"NIGHT_WINDOW_SUMMARY outcome='ok...' per run date; {no_summary} nights with no summary line",
                  data={"ok": ok, "nights": nights, "no_summary": no_summary,
                        "per_night": {k: (None if k not in outcomes else outcomes[k]) for k in keys}})


# ---------------------------------------------------------------------------
# ALERTS: what told, or should have told, the owner something is wrong. One row per source, over the long window
# unless the source only supports less (each row says which). Every log-derived string that reaches the output goes
# through a fixed allowlist; anything else prints as `other`.
# ---------------------------------------------------------------------------

ALERTS = "ALERTS (told, or should have told, the owner something is wrong)"
PERFORMANCE = "PERFORMANCE (a regression the owner is alerted to)"
INSTRUMENTS = "INSTRUMENTS (the before/after number of a change; unmeasured until its marker is in the log)"

#: scripts/maintenance/zoe_crash_loop_watch.py: UNIT plus OTHER_UNITS. A unit named in an alert outside this list is `other`.
CRASH_UNITS = ("zoe-data.service", "flue-zoe-telegram.service", "flue-zoe-brain-2x.service", "llama-server.service",
               "kokoro-tts.service", "functiongemma-router.service", "github-runner.service")
CRASH_ALERT, CRASH_RECOVERY = "\U0001F534", "\U0001F7E2"  # the red / green circle each alert / recovery message starts with
_CRASH_STAMP_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{4}) ")
_UNIT_RE = re.compile(r"([A-Za-z0-9@_.-]+\.service)\b")
CRASH_TOLERANCE_S = 7200.0  # the watcher prints a timestamped heartbeat at least hourly (HEARTBEAT_S) on a 2-min timer

#: scripts/maintenance/zoe_health.py check names: SPINE ("<name> :<port>") plus the fixed checks.
HEALTH_CHECKS = ("zoe-data :8000", "llama-server :11434", "flue-zoe-brain-2x :3579", "kokoro-tts :10201",
                 "functiongemma-router :11436", "postgres", "containers", "failed units", "night-window timer",
                 "disk /", "memory")
_HEALTH_LINE_RE = re.compile(r"^\[(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})\] ")
_HEALTH_ALERT_RE = re.compile(r"^\[[^\]]+\] ALERT (DOWN|recovered): (.*?) \(")
HEALTH_TOLERANCE_S = 1800.0  # the timer runs every 5 min and every run prints timestamped lines

#: ALERT: kinds written by scripts/maintenance/zoe_restore_drill.py and zoe_offbox.py (via zoe_offbox.alert).
BACKUP_ALERT_KINDS = (("weekly restore drill FAILED", "drill_failed"), ("weekly restore drill refused", "drill_refused"),
                      ("weekly restore drill has not passed", "drill_stale"),
                      ("monthly off-box restore drill FAILED", "offbox_drill_failed"),
                      ("off-box backup landed with problems", "offbox_backup_problems"))
_BACKUP_END_RE = re.compile(r"^zoe-backup-verify: (done|\d+ problem\(s\) detected)\s*$")


def _read_lines(path: str) -> tuple[list[str] | None, str | None]:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return [l.rstrip("\n") for l in fh], None
    except OSError as exc:
        return None, f"cannot read {os.path.basename(path)} ({type(exc).__name__})"


def _stamp_coverage(stamps: list[dt.datetime], since, until, tol_s: float, what: str) -> str | None:
    """None when timestamped lines provably cover [since, until): they start at or before `since`, no two consecutive
    stamps that touch the window are more than `tol_s` apart, and the last one is within `tol_s` of `until`."""
    if not stamps:
        return f"{what} holds no timestamped line"
    if stamps[0] > since:
        return f"{what} starts {_fmt(stamps[0])}, after the window start {_fmt(since)}; a partial count is not reported"
    for a, b in zip(stamps, stamps[1:]):
        if b > since and a < until and (b - a).total_seconds() > tol_s:
            return (f"{what} is silent for {(b - a).total_seconds() / 3600:.1f} h ({_fmt(a)}..{_fmt(b)}), more than "
                    f"{tol_s / 60:.0f} min: the writer was not running; a partial count is not reported")
    if stamps[-1] < until - dt.timedelta(seconds=tol_s):
        return f"{what} ends {_fmt(stamps[-1])}, more than {tol_s / 60:.0f} min before the window end (stale or stopped log)"
    return None


def _crash_unit(line: str) -> str:
    m = _UNIT_RE.search(line)
    return m.group(1) if m and m.group(1) in CRASH_UNITS else "other"


def alert_crash_loops(path: str, since, until) -> dict:
    """Crash-loop alerts and recoveries in crash-loop-watch.log (the watcher's stdout+stderr, appended by its unit).
    The writer prints each message as ONE line (newlines become " | ") and WITHOUT a timestamp; only its status lines
    carry one. So an alert is dated by the timestamped lines around it: it counts in the window only when the stamp
    before it is at or after `since` and the stamp after it (or the file's mtime, after the last stamp) is before
    `until`. One straddling a window edge is reported apart, never guessed in or out."""
    key, title = "alerts_crash_loops", "Crash-loop alerts (zoe-data and the watched units)"
    w = wstr(since, until)
    lines, err = _read_lines(path)
    if lines is None:
        return unmeasured(key, title, err, window=w)
    try:
        mtime = dt.datetime.fromtimestamp(os.stat(path).st_mtime, LOCAL_TZ)
    except OSError as exc:
        return unmeasured(key, title, f"cannot stat {os.path.basename(path)} ({type(exc).__name__})", window=w)
    stamps: list[dt.datetime] = []
    events: list[tuple[int, str, str]] = []  # (index into stamps of the stamp before, kind, unit label)
    undelivered = 0
    for line in lines:
        m = _CRASH_STAMP_RE.match(line)
        if m:
            t = parse_ts(m.group(1))
            if t is not None:
                stamps.append(t)
            body = line[m.end():]
        else:
            body = line
        if body.startswith(CRASH_ALERT):
            events.append((len(stamps) - 1, "alert", _crash_unit(body)))
        elif body.startswith(CRASH_RECOVERY):
            events.append((len(stamps) - 1, "recovery", _crash_unit(body)))
        elif body.startswith("crash-watch:") and ("telegram send failed" in body or "NO ALERT RECIPIENT" in body
                                                   or "alert NOT delivered" in body):
            events.append((len(stamps) - 1, "undelivered", "-"))
    reason = _stamp_coverage(stamps, since, until, CRASH_TOLERANCE_S, os.path.basename(path))
    if reason:
        return unmeasured(key, title, reason, window=w)
    alerts = recoveries = edge = 0
    by_unit: dict[str, int] = {}
    for i, kind, unit in events:
        before = stamps[i] if i >= 0 else None
        after = stamps[i + 1] if i + 1 < len(stamps) else mtime
        if before is None or after < since or before >= until:
            continue                       # wholly outside the window
        if before < since or after >= until:
            edge += 1                      # straddles an edge: not datable to one side
            continue
        if kind == "alert":
            alerts += 1
            by_unit[unit] = by_unit.get(unit, 0) + 1
        elif kind == "recovery":
            recoveries += 1
        else:
            undelivered += 1
    units_txt = ", ".join(f"{k} {v}" for k, v in sorted(by_unit.items())) or "none"
    return metric(key, title, f"alerts {alerts}, recoveries {recoveries}; by unit: {units_txt}", n=alerts, window=w,
                  method=(f"crash-loop-watch.log lines starting with the red circle (alert) / green circle (recovery) "
                          f"written by zoe_crash_loop_watch.py; undated, so each is dated by the timestamped lines around "
                          f"it; at a window edge (not counted): {edge}; delivery failures logged: {undelivered}; "
                          f"a pending alert is re-printed each tick until delivered, so alerts are lines, not incidents"),
                  label="[Proven]", data={"alerts": alerts, "recoveries": recoveries, "by_unit": by_unit, "edge": edge,
                                         "undelivered": undelivered})


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
#: a REAL night's report name (write_report: <date>.json, or <date>-HHMM.json for a second run that day); exploratory runs
#: carry -cells / -trial / -sweep / -rehearse / -repass-only. An unreadable file is placed in a night by this name alone.
_NIGHT_NAME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})(?:-\d{4})?\.json$")


def _night_bucket(runs: list[dict]) -> str:
    """The night's headline from its best real run: ok > degraded > not_ok."""
    outs = [str(r.get("outcome", "")) for r in runs]
    if any(o.startswith("ok") for o in outs):
        return "ok"
    if any(o.startswith("degraded") for o in outs):
        return "degraded"
    return "not_ok"


def alert_nights(report_dir: str, since, until) -> dict:
    """Per night in the window, from the night window's own reports (<date>.json, the machine twin of <date>.md, written
    by scripts/night/night_window.py write_report). Only `mode == "night"` runs count (trials, rehearsals, sweeps and
    re-pass-only runs do not). A night D is in the window when D 04:00 local (after the window's ~02:30-03:10 run) is.
    The ALARM file beside the reports is one file that the next clean window deletes, so it only says "now"; the
    per-night record of an alarm is the report's `restore_status` (NOT HEALTHY), `night_lost` and `decode_alert`."""
    key, title = "alerts_night", "Night window: alarms, degraded and missing nights"
    try:
        names = sorted(os.listdir(report_dir))
    except OSError as exc:
        return unmeasured(key, title, f"cannot list the night-reports directory ({type(exc).__name__})")
    runs: dict[str, list[dict]] = {}
    unreadable: list[str] = []   # file-name dates of real-night reports that exist but cannot be read (never "missing")
    for name in names:
        if not name.endswith(".json") or not _DATE_RE.match(name[:10]):
            continue
        real_name = _NIGHT_NAME_RE.match(name)
        try:
            with open(os.path.join(report_dir, name), encoding="utf-8") as fh:
                d = json.load(fh)
        except (OSError, ValueError):
            if real_name:
                unreadable.append(real_name.group(1))
            continue
        if not isinstance(d, dict):
            if real_name:
                unreadable.append(real_name.group(1))
            continue
        if d.get("mode") != "night":
            continue
        day = str(d.get("started", ""))[:10]
        if not _DATE_RE.match(day) or not isinstance(d.get("outcome"), str):
            unreadable.append(real_name.group(1) if real_name else name[:10])
            continue
        runs.setdefault(day, []).append(d)
    if not runs:
        return unmeasured(key, title, "no readable real-night report in the night-reports directory")
    first_report = dt.date.fromisoformat(min(runs))
    nights: list[dt.date] = []
    d = since.astimezone(LOCAL_TZ).date() - dt.timedelta(days=1)
    while True:
        at4 = dt.datetime(d.year, d.month, d.day, 4, 0, tzinfo=LOCAL_TZ)
        if at4 >= until:
            break
        if at4 >= since:
            nights.append(d)
        d += dt.timedelta(days=1)
    asked = nights[:]
    nights = [n for n in nights if n >= first_report]
    if not nights:
        return unmeasured(key, title, f"the night reports start {first_report}, after every night in the window")
    bad = sum(1 for u in unreadable if u in {n.isoformat() for n in nights})
    if bad:
        return unmeasured(key, title, f"{bad} night report(s) in the window exist but cannot be read (corrupt or "
                                      f"malformed); a night whose report cannot be read is neither missing nor ok",
                          window=f"nights {nights[0]}..{nights[-1]}")
    c = {"ok": 0, "degraded": 0, "not_ok": 0, "no_report": 0, "alarm": 0, "decode_slow": 0, "restore_failed": 0,
         "night_lost": 0}
    for n in nights:
        rs = runs.get(n.isoformat())
        if not rs:
            c["no_report"] += 1
            continue
        c[_night_bucket(rs)] += 1
        restore = any(str(r.get("restore_status", "")).startswith("NOT HEALTHY") for r in rs)
        lost = any(r.get("night_lost") for r in rs)
        slow = any(r.get("decode_alert") for r in rs)
        c["restore_failed"] += restore
        c["night_lost"] += lost
        c["decode_slow"] += slow
        c["alarm"] += restore or lost or slow
    alarm_now = os.path.exists(os.path.join(report_dir, "ALARM"))
    narrowed = len(nights) < len(asked)
    w = f"nights {nights[0]}..{nights[-1]}" + (f" (narrowed: reports start {first_report}; window asked from {asked[0]})"
                                               if narrowed else "")
    return metric(key, title,
                  (f"alarm {c['alarm']} (restore failed {c['restore_failed']}, night lost {c['night_lost']}, decode slow "
                   f"{c['decode_slow']}); ok {c['ok']}, degraded {c['degraded']}, not ok {c['not_ok']}, no report "
                   f"{c['no_report']} of {len(nights)}"),
                  n=len(nights), window=w,
                  method=("night-reports/<date>.json mode=night; per night the best run: outcome ok... > degraded... > "
                          "anything else (refused/aborted/failed/noop); alarm = restore_status NOT HEALTHY, night_lost or "
                          "decode_alert (what the ALARM file carries); a night with no report is counted, not skipped; "
                          f"ALARM file present now: {'yes' if alarm_now else 'no'}; unreadable reports outside the window: {len(unreadable)}; "
                          "a 30-day per-night view from the reports, not the night-window.log row above"),
                  data={**c, "nights": len(nights), "alarm_file_now": alarm_now, "unreadable": len(unreadable),
                        "narrowed": narrowed})


def alert_backups(log_path: str, state_path: str) -> dict:
    """ALERT: lines in backup-verify.log. The log has no per-line timestamps (stdout/stderr of the weekly unit); runs are
    delimited by the script's last line ("zoe-backup-verify: done" / "N problem(s) detected"). So the count is over the
    whole log, never a window, with the file's mtime as the last write and the drill state file's last run time."""
    key, title = "alerts_backups", "Backup restore-drill alerts"
    lines, err = _read_lines(log_path)
    if lines is None:
        return unmeasured(key, title, err, window="whole log")
    runs = problem_runs = alerts = undelivered = 0
    kinds: dict[str, int] = {}
    tail = False
    for line in lines:
        s = line.strip()
        if s.startswith("ALERT: "):
            alerts += 1
            body = s[len("ALERT: "):]
            kind = next((label for pre, label in BACKUP_ALERT_KINDS if body.startswith(pre)), "other")
            kinds[kind] = kinds.get(kind, 0) + 1
        elif s.startswith("alert not delivered"):
            undelivered += 1
        m = _BACKUP_END_RE.match(s)
        if m:
            runs += 1
            problem_runs += m.group(1) != "done"
            tail = False
        elif s:
            tail = True
    counted = f"(readable: {alerts} ALERT lines over {runs} runs)"
    try:
        last_write = dt.datetime.fromtimestamp(os.stat(log_path).st_mtime, LOCAL_TZ)
    except OSError as exc:
        return unmeasured(key, title, f"cannot stat {os.path.basename(log_path)} ({type(exc).__name__}) {counted}",
                          window="whole log")
    try:
        with open(state_path, encoding="utf-8") as fh:
            st = json.load(fh)
        t = parse_local(str(st["time"]))
        last_ok = st["ok"]
        if not isinstance(last_ok, bool):
            raise ValueError("ok is not a boolean")
        last_run = _fmt(t)
    except OSError as exc:
        return unmeasured(key, title, f"cannot read the drill state file {os.path.basename(state_path)} "
                                      f"({type(exc).__name__}) {counted}", window="whole log")
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        return unmeasured(key, title, f"the drill state file {os.path.basename(state_path)} is malformed "
                                      f"({type(exc).__name__}) {counted}", window="whole log")
    kinds_txt = ", ".join(f"{k} {v}" for k, v in sorted(kinds.items())) or "none"
    return metric(key, title,
                  (f"{alerts} ALERT lines over {runs} runs ({problem_runs} with problems); by kind: {kinds_txt}; "
                   f"last write {_fmt(last_write)}"),
                  n=runs, window="whole log (undated: the count is all-time, not the long window)",
                  method=("backup-verify.log ALERT: lines (zoe_offbox.alert, used by the on-box and off-box drills); runs "
                          "end at the script's last line; the log has no per-line timestamps, so no date is guessed; "
                          f"alert not delivered: {undelivered}; unterminated last run: {'yes' if tail else 'no'}; last drill "
                          f"run (state file): {last_run}, passed: {'yes' if last_ok else 'no'}"),
                  data={"alerts": alerts, "runs": runs, "problem_runs": problem_runs, "by_kind": kinds,
                        "undelivered": undelivered, "last_write": last_write.isoformat(),
                        "last_drill_ok": last_ok})


def alert_health(path: str, since, until) -> dict:
    """zoe_health.py's alert lines ("[<local ISO>] ALERT DOWN: <check> (...)" / "ALERT recovered: ..."), incl. the
    night-window timer alarm of PR #2419. A check outside HEALTH_CHECKS is `other`."""
    key, title = "alerts_health", "Health-check alerts (incl. the night-window timer alarm)"
    w = wstr(since, until)
    lines, err = _read_lines(path)
    if lines is None:
        return unmeasured(key, title, err, window=w)
    stamps: list[dt.datetime] = []
    down = recovered = 0
    by_check: dict[str, int] = {}
    for line in lines:
        m = _HEALTH_LINE_RE.match(line)
        if not m:
            continue
        try:
            t = dt.datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S").replace(tzinfo=LOCAL_TZ)
        except ValueError:
            if "] ALERT " in line:   # an alert that cannot be dated cannot be placed in or out of the window
                return unmeasured(key, title, "malformed marker (an ALERT line with an invalid timestamp)", window=w)
            continue
        stamps.append(t)
        a = _HEALTH_ALERT_RE.match(line)
        if a and since <= t < until:
            if a.group(1) == "DOWN":
                down += 1
                name = a.group(2) if a.group(2) in HEALTH_CHECKS else "other"
                by_check[name] = by_check.get(name, 0) + 1
            else:
                recovered += 1
    stamps.sort()
    reason = _stamp_coverage(stamps, since, until, HEALTH_TOLERANCE_S, os.path.basename(path))
    if reason:
        return unmeasured(key, title, reason, window=w)
    checks_txt = ", ".join(f"{k} {v}" for k, v in sorted(by_check.items())) or "none"
    return metric(key, title,
                  f"DOWN {down} (night-window timer {by_check.get('night-window timer', 0)}), recovered {recovered}; by check: {checks_txt}",
                  n=down, window=w,
                  method="zoe_health.py ALERT DOWN / recovered lines (2 consecutive failures per alert); check names allowlisted",
                  data={"down": down, "recovered": recovered, "by_check": by_check})


def _app_log_window(logs: LogSet, since, until) -> tuple[dt.datetime, str | None, str]:
    """(start, reason, window text) for an app-log alert row: the long window when the retained rotations cover it;
    otherwise, when the ONLY problem is that retention starts later, the narrower window from the earliest retained
    line (stated in the window text); otherwise `reason`."""
    reason = coverage_reason(logs, since, until)
    if reason and logs.earliest is not None and logs.earliest > since:
        start = logs.earliest
        if coverage_reason(logs, start, until) is None:
            return start, None, wstr(start, until) + f" (narrowed: app logs are retained from {_fmt(start)}; window asked from {_fmt(since)})"
    return since, reason, wstr(since, until)


def alert_distress_no_contact(logs: LogSet, since, until) -> dict:
    """DISTRESS_NO_CONTACT WARNING lines (services/zoe-data/distress_handoff.py): a distress hand-off fired and nobody
    was told because no contact is configured. Only this marker is counted; distress events themselves are not."""
    key, title = "alerts_distress_no_contact", "Distress hand-offs where nobody was told"
    since, reason, w = _app_log_window(logs, since, until)
    if reason:
        return unmeasured(key, title, reason, window=w)
    n = harness = 0
    for _, line in in_window(logs.lines, since, until, "DISTRESS_NO_CONTACT"):
        if is_harness(line, fields(line)):
            harness += 1
        else:
            n += 1
    return metric(key, title, f"{n} (harness lines excluded: {harness})", n=n, window=w,
                  method="zoe-data app log DISTRESS_NO_CONTACT lines (fixed text, no id), harness traffic excluded",
                  data={"lines": n, "harness": harness})


def perf_panel_kv_miss(logs: LogSet, since, until) -> dict:
    """PANEL_KV_MISS WARNING lines (services/zoe-data/zoe_flue_client.py _panel_kv_alarm; household voice-panel turns
    only): kind=first is the cold first reply kv_miss_alert.py tells the owner about (at most one Telegram a day)."""
    key, title = "perf_panel_kv_miss", "Brain cache misses on household panel turns"
    since, reason, w = _app_log_window(logs, since, until)
    if reason:
        return unmeasured(key, title, reason, window=w)
    kinds = {"first": 0, "warm": 0, "other": 0}
    first_days: set[str] = set()
    undelivered = 0
    for t, line in in_window(logs.lines, since, until, "PANEL_KV_MISS"):
        f = fields(line)
        if is_harness(line, f):
            continue
        k = f.get("kind")
        k = k if k in ("first", "warm") else "other"
        kinds[k] += 1
        if k == "first":
            first_days.add(t.astimezone(LOCAL_TZ).date().isoformat())
    for _, line in in_window(logs.lines, since, until, "kv_miss_alert: alert not delivered"):
        if not is_harness(line, fields(line)):
            undelivered += 1
    n = sum(kinds.values())
    return metric(key, title,
                  (f"misses {n}: first {kinds['first']}, warm {kinds['warm']}"
                   + (f", other {kinds['other']}" if kinds["other"] else "")
                   + f"; days with a cold first reply {len(first_days)}; owner alert not delivered {undelivered}"),
                  n=n, window=w,
                  method=("zoe-data app log PANEL_KV_MISS kind=first|warm (re-prefill above ZOE_PANEL_KV_MISS_TOKENS); "
                          "kind=first days are the days kv_miss_alert.py sends its one owner alert; 'kv_miss_alert: alert "
                          "not delivered' lines counted. A performance regression, not a failure"),
                  label="[Performance]",
                  data={**kinds, "days_first": len(first_days), "undelivered": undelivered})


def alert_section(args, logs: LogSet, long_since, until) -> list[dict]:
    """The ALERTS rows then the PERFORMANCE row; each reader is defensive, and one that still raises becomes `unmeasured`."""
    log_dir = args.log_dir
    jobs = (
        ("alerts_crash_loops", "Crash-loop alerts", ALERTS,
         lambda: alert_crash_loops(args.crash_log or os.path.join(log_dir, "crash-loop-watch.log"), long_since, until)),
        ("alerts_night", "Night window: alarms, degraded and missing nights", ALERTS,
         lambda: alert_nights(args.night_reports, long_since, until)),
        ("alerts_backups", "Backup restore-drill alerts", ALERTS,
         lambda: alert_backups(args.backup_log or os.path.join(log_dir, "backup-verify.log"), args.backup_state)),
        ("alerts_health", "Health-check alerts", ALERTS,
         lambda: alert_health(args.health_log or os.path.join(log_dir, "health.log"), long_since, until)),
        ("alerts_distress_no_contact", "Distress hand-offs where nobody was told", ALERTS,
         lambda: alert_distress_no_contact(logs, long_since, until)),
        ("perf_panel_kv_miss", "Brain cache misses on household panel turns", PERFORMANCE,
         lambda: perf_panel_kv_miss(logs, long_since, until)),
    )
    return _run_rows(jobs)


def _run_rows(jobs) -> list[dict]:
    out = []
    for key, title, section, fn in jobs:
        try:
            m = fn()
        except Exception as exc:  # noqa: BLE001 - a scorecard reports, it never crashes
            m = unmeasured(key, title, f"reader failed ({type(exc).__name__})")
        m["section"] = section
        out.append(m)
    return out


# ---------------------------------------------------------------------------
# INSTRUMENTS: one row per log-only instrument (codes, counts and milliseconds only)
# ---------------------------------------------------------------------------
# Each row reads ONE marker whose format is pinned by the emitting module's own test. A window with no line of the marker
# is `unmeasured` (an instrument that is not deployed, or is off, is not a zero). A line with the marker but not the exact
# format makes the row `unmeasured` ("malformed marker"). A line whose timestamp is impossible (month 13) never gets here:
# parse_ts() returns None for it and read_rotation() drops it.

#: The first line zoe-data's lifespan writes (main.py: memory_forgotten.log_startup_state, or its fail-closed fallback with
#: reason=module_error): exactly one per process start, so it is the start marker for "the first 60 s after a start".
_START_RE = re.compile(r"\bFORGET_LEDGER state=(configured|unconfigured)(?: reason=([a-z_]{1,32}))?(?:\s|$)")
_LOOP_BLOCK_RE = re.compile(r"\bEVENT_LOOP_BLOCK ms=(\d{1,9}) ")
_PREPOST_RE = re.compile(r"\bFLUE_PREPOST sid=[0-9a-f]{10} total_ms=(\d{1,9}) blocks_ms=(\d{1,9}) other_ms=(\d{1,9}) "
                         r"awaited=(\d{1,4})(?:\s|$)")
STARTUP_WINDOW_S = 60.0


def _inst_lines(logs: LogSet, since, until, key: str, title: str, marker: str, absent: str, *, household_only: bool = True):
    """(start, window text, [(t, line)]) for ``marker`` in the (narrowed) window, or an unmeasured row: the logs do not cover
    the window, or no line carries the marker (``absent`` says what that can mean). Harness lines dropped when household_only."""
    since, reason, w = _app_log_window(logs, since, until)
    if reason:
        return unmeasured(key, title, reason, window=w)
    rows = [(t, line) for t, line in in_window(logs.lines, since, until, marker)
            if not (household_only and is_harness(line, fields(line)))]
    if not rows:
        return unmeasured(key, title, f"no {marker.strip()} line in window: {absent}", window=w)
    return since, w, rows


def _starts(logs: LogSet, since, until) -> list[dt.datetime]:
    return sorted(t for t, line in in_window(logs.lines, since, until, "FORGET_LEDGER state=") if _START_RE.search(line))


def _ms_txt(values: list[float], *qs: int) -> str:
    return " / ".join(f"p{q} {pct(values, q):.0f}" for q in qs)


def inst_loop_block(logs: LogSet, since, until) -> dict:
    """EVENT_LOOP_BLOCK (services/zoe-data/loop_block_stacks.py _emit, #2473): one line per stall >= 250 ms, written when the
    stall ENDS. Per day over the days the instrument was provably running, and the first 60 s after each process start."""
    key, title = "inst_loop_block", "Event-loop stalls (EVENT_LOOP_BLOCK episodes, >= 250 ms)"
    got = _inst_lines(logs, since, until, key, title, "EVENT_LOOP_BLOCK ms=",
                      "the watcher (ZOE_LOOP_BLOCK_STACKS, #2473) was not running, or no stall reached 250 ms; the log cannot "
                      "tell which", household_only=False)
    if isinstance(got, dict):
        return got
    since, w, rows = got
    eps = []
    for t, line in rows:
        m = _LOOP_BLOCK_RE.search(line)
        if not m:
            return malformed_metric(key, title, sum(1 for _, x in rows if not _LOOP_BLOCK_RE.search(x)), since, until)
        eps.append((t, int(m.group(1))))
    starts = _starts(logs, since, until)
    # the measured span opens at the last process start at or before the first episode (else at the first episode itself)
    before = [s for s in starts if s <= eps[0][0]]
    first = before[-1] if before else eps[0][0]
    days = max((until - first).total_seconds() / 86400.0, 1 / 1440)
    by_day: dict[str, int] = {}
    for t, _ in eps:
        d = t.astimezone(LOCAL_TZ).date().isoformat()
        by_day[d] = by_day.get(d, 0) + 1
    worst = max(sorted(by_day), key=lambda d: by_day[d])
    early = hit = 0
    if starts:
        per_start: dict[int, int] = {}
        for t, _ in eps:
            i = bisect.bisect_right(starts, t) - 1     # the latest start at or before the episode's end
            if i >= 0 and (t - starts[i]).total_seconds() < STARTUP_WINDOW_S:
                per_start[i] = per_start.get(i, 0) + 1
        early, hit = sum(per_start.values()), len(per_start)
        early_txt = f"{early} episodes over {len(starts)} starts ({hit} starts with any)"
    else:
        early_txt = "unmeasured (no FORGET_LEDGER start line in window)"
    ms = [float(v) for _, v in eps]
    return metric(key, title,
                  (f"episodes {len(eps)} ({len(eps) / days:.2f}/day over {days:.1f} d); worst day {worst} {by_day[worst]}; "
                   f"ms {_ms_txt(ms, 50, 90)} / max {max(ms):.0f}; first {STARTUP_WINDOW_S:.0f} s after a start: {early_txt}"),
                  n=len(eps), window=w + f" (measured from {_fmt(first)})",
                  method=("zoe-data app log EVENT_LOOP_BLOCK ms= lines (one per stall, logged when it ends; _TOP/_FRAMES not "
                          "counted); the span opens at the last FORGET_LEDGER start line at or before the first episode; an "
                          "episode is 'after a start' when it ENDED < 60 s after the latest start (1 s log resolution)"),
                  label="[Performance]",
                  data={"episodes": len(eps), "days": round(days, 3), "by_day": by_day, "starts": len(starts),
                        "startup_episodes": early, "starts_with_any": hit,
                        "ms_p50": pct(ms, 50), "ms_p90": pct(ms, 90), "ms_max": max(ms)})


def inst_forget_ledger(logs: LogSet, since, until) -> dict:
    """FORGET_LEDGER state= (services/zoe-data/memory_forgotten.log_startup_state, #2462): one line per process start."""
    key, title = "inst_forget_ledger", "Forget ledger state at each start"
    got = _inst_lines(logs, since, until, key, title, "FORGET_LEDGER state=",
                      "no zoe-data start in window carried the line (#2462 not deployed)", household_only=False)
    if isinstance(got, dict):
        return got
    since, w, rows = got
    counts = {"configured": 0, "unconfigured": 0, "module_error": 0}
    last = None
    for t, line in rows:
        m = _START_RE.search(line)
        if not m:
            return malformed_metric(key, title, sum(1 for _, x in rows if not _START_RE.search(x)), since, until)
        counts[m.group(1)] += 1
        counts["module_error"] += m.group(2) == "module_error"
        last = m.group(1)
    return metric(key, title,
                  (f"last start {last}; starts {len(rows)}: configured {counts['configured']}, unconfigured "
                   f"{counts['unconfigured']} (of which module_error {counts['module_error']})"),
                  n=len(rows), window=w,
                  method=("zoe-data app log FORGET_LEDGER state=configured|unconfigured [reason=module_error], one per start; "
                          "unconfigured = a forget is not permanent (only the 300 s tombstone guards it)"),
                  data={**counts, "starts": len(rows), "last": last})


def inst_flue_prepost(logs: LogSet, since, until) -> dict:
    """FLUE_PREPOST (PR #2484, services/zoe-data/zoe_flue_client.py): what a household brain turn awaited before the POST."""
    key, title = "inst_flue_prepost", "Pre-POST chain cost per household brain turn (FLUE_PREPOST)"
    got = _inst_lines(logs, since, until, key, title, "FLUE_PREPOST ", "the timing of PR #2484 is not deployed")
    if isinstance(got, dict):
        return got
    since, w, rows = got
    total, blocks = [], []
    for _, line in rows:
        m = _PREPOST_RE.search(line)
        if not m:
            return malformed_metric(key, title, sum(1 for _, x in rows if not _PREPOST_RE.search(x)), since, until)
        total.append(float(m.group(1)))
        blocks.append(float(m.group(2)))
    return metric(key, title,
                  f"total_ms {_ms_txt(total, 50, 90)}; blocks_ms {_ms_txt(blocks, 50, 90)}", n=len(total), window=w,
                  method=("zoe-data app log FLUE_PREPOST total_ms= (wrapper entry to payload built) and blocks_ms= (the "
                          "context-block awaits), one line per brain turn that reached the POST; harness traffic excluded"),
                  label="[Performance]",
                  data={"n": len(total), "total_p50": pct(total, 50), "total_p90": pct(total, 90),
                        "blocks_p50": pct(blocks, 50), "blocks_p90": pct(blocks, 90)})


_FORGET_HELD_RE = re.compile(r"\bFORGET_CORRECTION_HELD path=(topic|name|last|member|other) held=(-1|\d{1,4}) "
                             r"live=(-1|\d{1,4})(?:\s|$)")


def inst_forget_held(logs: LogSet, since, until) -> dict:
    """FORGET_CORRECTION_HELD (services/zoe-data/value_correction.log_held_after_forget): at each forget's completion, the
    corrections about what was forgotten that the in-process memory still holds. Before #2487 > 0 is possible; after, 0."""
    key, title = "inst_forget_held", "Forgotten subjects still held in process after a forget"
    got = _inst_lines(logs, since, until, key, title, "FORGET_CORRECTION_HELD ",
                      "the instrument is not deployed, or no household forget ran")
    if isinstance(got, dict):
        return got
    since, w, rows = got
    by_path: dict[str, int] = {}
    with_held = held = unknown = 0
    for _, line in rows:
        m = _FORGET_HELD_RE.search(line)
        if not m:
            return malformed_metric(key, title, sum(1 for _, x in rows if not _FORGET_HELD_RE.search(x)), since, until)
        by_path[m.group(1)] = by_path.get(m.group(1), 0) + 1
        h = int(m.group(2))
        if h < 0:
            unknown += 1
        else:
            held += h
            with_held += h > 0
    paths = ", ".join(f"{k} {v}" for k, v in sorted(by_path.items()))
    return metric(key, title,
                  (f"forgets with a correction still held {frac(with_held, len(rows))} ({held} held in all); by path: "
                   f"{paths}; could not count {unknown}"),
                  n=len(rows), window=w,
                  method=("zoe-data app log FORGET_CORRECTION_HELD path= held= live= at each forget's completion (topic, name, "
                          "'forget that', member erase); harness traffic excluded; should be 0 after #2487"),
                  data={"forgets": len(rows), "with_held": with_held, "held": held, "unknown": unknown, "by_path": by_path})


def instrument_section(args, logs: LogSet, long_since, until) -> list[dict]:
    """The INSTRUMENTS rows (long window, narrowed to the retained logs like the ALERTS app-log rows)."""
    jobs = (
        ("inst_loop_block", "Event-loop stalls", INSTRUMENTS, lambda: inst_loop_block(logs, long_since, until)),
        ("inst_forget_ledger", "Forget ledger state at each start", INSTRUMENTS,
         lambda: inst_forget_ledger(logs, long_since, until)),
        ("inst_flue_prepost", "Pre-POST chain cost", INSTRUMENTS, lambda: inst_flue_prepost(logs, long_since, until)),
        ("inst_forget_held", "Forgotten subjects still held in process", INSTRUMENTS,
         lambda: inst_forget_held(logs, long_since, until)),
    )
    return _run_rows(jobs)


# ---------------------------------------------------------------------------
# Panel (optional, over ssh)
# ---------------------------------------------------------------------------

# ALLOWLIST extraction, run ON THE PI with LC_ALL=C sed -nE. Each program line rewrites a matching log line to
# "<YYYY-MM-DD HH:MM:SS> <tag> <value>" built ONLY from captured timestamp digits, a fixed tag letter and a number
# or a fixed enum; every other line prints nothing. So no field, whatever its name or position, can carry text
# off the device. `t` ends the cycle after the first successful rewrite.
_PTS = r"([0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}),[0-9]+ "
PANEL_SED = (
    r"s/^" + _PTS + r".*turn_stream TTFA=([0-9]{1,4}(\.[0-9]{1,3})?)s( .*)?$/\1 T \2/p",
    "t",
    r"s/^" + _PTS + r".*Recorded (command|follow-up): .*close=(deep|quiet)=([0-9]{1,5})ms( .*)?$/\1 C \4/p",
    "t",
    r"s/^" + _PTS + r".*Recorded (command|follow-up): .*tail=(deep|quiet)( .*)?$/\1 Q \3/p",
    "t",
    r"s/^" + _PTS + r".*Recorded (command|follow-up):.*$/\1 R/p",
    "t",
    # The daemon's first-sound instrument (zoe_voice_daemon.py FIRST_SOUND): the WHOLE line shape is pinned, so a line
    # that carries any extra token matches nothing here and falls through to the timestamp-only rule below. Only the
    # nine captures (timestamp + eight integers / one 0-1 flag) leave the device; the enums are matched, never captured.
    r"s/^" + _PTS + r".*FIRST_SOUND close=[a-z_]{1,10}=[0-9]{1,5}ms post_ms=-?[0-9]{1,6} reflex=[a-z]{1,5} skip=[a-z_-]{1,24} "
    r"reflex_at_ms=(-?[0-9]{1,6}) reflex_dur_ms=-?[0-9]{1,6} reflex_cut=[01] reflex_wait_ms=[0-9]{1,6} "
    r"server_first_ms=(-?[0-9]{1,6}) server_kind=[a-z_]{1,10} gap_ms=-?[0-9]{1,6} still_talking=([01]) "
    r"talk_chunks=(-?[0-9]{1,6}) chunks=[0-9]{1,6} underrun_max_ms=([0-9]{1,6}) underrun_sum_ms=([0-9]{1,7}) "
    r"obs_ms=([0-9]{1,6}) win=[a-z]{1,12} quiet_ms=-?[0-9]{1,6} fall=[a-z]{1,12} muted_ms=(-?[0-9]{1,6}) first_end_ms=-?[0-9]{1,6}$/\1 F \2 \3 \4 \5 \6 \7 \8 \9/p",
    "t",
    r"s/^" + _PTS + r".*$/\1 E/p",  # any other timestamped line: its timestamp only, to prove how far the log reaches
)
PANEL_LOG = "/home/pi/.zoe-voice/voice.log"
_PANEL_LINE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) (?:T (\d+(?:\.\d+)?)|C (\d+)|Q (deep|quiet)|(R)|F (-?\d+) (-?\d+) ([01]) (-?\d+) (\d+) (\d+) (\d+) (-?\d+)|E)$")
#: The voice lane's bars: a turn "feels answered" when ANY sound starts inside 1 s of speech end; 0.6 s is the reflex bar.
FIRST_SOUND_BANDS_MS = (1000, 600)
TAIL_FLOOR_MS = {"deep": 640.0, "quiet": 800.0}  # live .env.voice floors per lane 4 (used only when close= is absent)


def _panel_ts(raw: str) -> "dt.datetime | None":
    """A reduced row's timestamp; None for a regex-shaped but impossible one (month 99): that row is skipped."""
    try:
        return dt.datetime.strptime(raw, "%Y-%m-%d %H:%M:%S").replace(tzinfo=LOCAL_TZ)
    except ValueError:
        return None


def panel_command(log_path: str = PANEL_LOG) -> str:
    """The remote shell command: the allowlist sed program over the panel log."""
    args = ["LC_ALL=C", "sed", "-nE"]
    for expr in PANEL_SED:
        args += ["-e", shlex.quote(expr)]
    return " ".join(args + [shlex.quote(log_path)])


def panel_pairs(reduced_lines: Iterable[str], since: dt.datetime, until: dt.datetime) -> list[float]:
    """Per turn: endpoint tail (ms) + Pi TTFA (ms), pairing each TTFA with the closest preceding Recorded line
    within 60 s. Input is the allowlist output; any line not in that exact shape is ignored."""
    totals: list[float] = []
    last: tuple[dt.datetime, float] | None = None
    for line in reduced_lines:
        m = _PANEL_LINE_RE.match(line.strip())
        if not m:
            continue
        t = _panel_ts(m.group(1))
        if t is None:
            continue
        ttfa, close, tail, rec = m.group(2), m.group(3), m.group(4), m.group(5)
        if close is not None:
            last = (t, float(close))
        elif tail is not None:
            last = (t, TAIL_FLOOR_MS[tail])
        elif rec is not None:
            last = None
        elif ttfa is not None:  # E rows (any other line) carry no turn data and leave `last` alone
            if last and 0 <= (t - last[0]).total_seconds() <= 60 and since <= last[0] < until:
                totals.append(last[1] + float(ttfa) * 1000.0)
            last = None
    return totals


def panel_metric(reduced: list[str], since: dt.datetime, until: dt.datetime) -> dict:
    """The panel row from the allowlist output: `unmeasured` unless the log reaches back to `since` and forward
    to `until` within the largest gap between its own lines in the window (floor 10 min)."""
    title = "Speech end to first sound, panel (inferred)"
    w = wstr(since, until)
    stamps = []
    for r in reduced:
        m = _PANEL_LINE_RE.match(r.strip())
        if m:
            t = _panel_ts(m.group(1))
            if t is not None:
                stamps.append(t)
    if not stamps:
        return unmeasured("speech_end_first_sound", title, "panel log returned no timestamped lines", window=w)
    stamps.sort()
    if stamps[0] > since:
        return unmeasured("speech_end_first_sound", title, "panel log does not reach back to the window start", window=w)
    inside = [t for t in stamps if since <= t < until]
    tol = max([MIN_ROTATION_GAP_S] + [(b - a).total_seconds() for a, b in zip(inside, inside[1:])])
    if stamps[-1] < until - dt.timedelta(seconds=tol):
        return unmeasured("speech_end_first_sound", title,
                          f"panel log ends {_fmt(stamps[-1])}, more than {tol / 60:.0f} min before the window end (stale or stopped log)",
                          window=w)
    totals = panel_pairs(reduced, since, until)
    if not totals:
        return unmeasured("speech_end_first_sound", title, "no paired Recorded/TTFA lines in window", window=w)
    under = {ms: sum(1 for x in totals if x < ms) for ms in FIRST_SOUND_BANDS_MS}
    return metric("speech_end_first_sound", title,
                  (f"p50 {pct(totals, 50) / 1000:.2f} s / p90 {pct(totals, 90) / 1000:.2f} s; "
                   f"under 1 s {frac(under[1000], len(totals))}; under 0.6 s {frac(under[600], len(totals))}"),
                  n=len(totals), window=w,
                  method=("Pi voice.log via an allowlist extractor: endpoint close (close=..ms, else tail floor 640/800) "
                          "+ turn_stream TTFA, paired per turn; excludes the Recorded-to-receipt upload (lane 4 stage B, "
                          "~0.16 s median)"),
                  label="[Experimental]",
                  data={"p50_ms": pct(totals, 50), "p90_ms": pct(totals, 90),
                        "under_1000_ms": under[1000], "under_600_ms": under[600], "turns": len(totals)})


def panel_first_sound_rows(reduced_lines: Iterable[str], since: dt.datetime, until: dt.datetime) -> list[tuple[int, ...]]:
    """The FIRST_SOUND rows inside the window as (reflex_at_ms, server_first_ms, still_talking, talk_chunks,
    underrun_max_ms, underrun_sum_ms, obs_ms, muted_ms). Input is the allowlist output; anything not in that exact shape is ignored."""
    rows: list[tuple[int, ...]] = []
    for line in reduced_lines:
        m = _PANEL_LINE_RE.match(line.strip())
        if not m or m.group(6) is None:
            continue
        t = _panel_ts(m.group(1))
        if t is not None and since <= t < until:
            rows.append(tuple(int(m.group(i)) for i in range(6, 14)))
    return rows


PANEL_MEASURED_ROWS = (("first_sound_from_close", "First sound after the close, panel (measured)"),
                       ("panel_post_close_talk", "Speech after the close, panel (measured)"),
                       ("panel_playback_underrun", "Playback starvation, panel (measured)"),
                       ("panel_server_first", "Server first sound after the close, panel (measured)"))


def panel_first_sound_metrics(reduced: list[str], since: dt.datetime, until: dt.datetime) -> list[dict]:
    """Measured first sound from the daemon's own FIRST_SOUND lines (ms from the committed close, so it excludes the
    ~0.8 s the recording needs to decide the person stopped). Four rows, each with n; `unmeasured` when no such line."""
    w = wstr(since, until)
    rows = panel_first_sound_rows(reduced, since, until)
    keys = PANEL_MEASURED_ROWS
    if not rows:
        return [unmeasured(k, t, "no FIRST_SOUND lines in window (daemon without the instrument, or no turns)", window=w)
                for k, t in keys]
    method = ("Pi voice.log FIRST_SOUND line (integers only), ms from the committed endpoint close; first sound is the "
              "time the first audio bytes were OFFERED to the player (a lower bound: first_end_ms on the line is when the "
              "write returned, and neither includes the player's device-open latency)")
    firsts = []
    servers = []
    for reflex_at, server_first, *_ in rows:
        known = [x for x in (reflex_at, server_first) if x >= 0]
        if known:
            firsts.append(float(min(known)))
        if server_first >= 0:
            servers.append(float(server_first))
    out = []
    if firsts:
        under = {ms: sum(1 for x in firsts if x < ms) for ms in FIRST_SOUND_BANDS_MS}
        out.append(metric(keys[0][0], keys[0][1],
                          (f"p50 {pct(firsts, 50) / 1000:.2f} s / p90 {pct(firsts, 90) / 1000:.2f} s; "
                           f"under 1.0 s {frac(under[1000], len(firsts))}; under 0.6 s {frac(under[600], len(firsts))}"),
                          n=len(firsts), window=w, method=method, label="[Proven]",
                          data={"p50_ms": pct(firsts, 50), "p90_ms": pct(firsts, 90),
                                "under_1000_ms": under[1000], "under_600_ms": under[600], "turns": len(firsts)}))
    else:
        out.append(unmeasured(keys[0][0], keys[0][1], "no turn in window made a sound", window=w))
    heard = [r for r in rows if r[3] >= 0]  # talk_chunks -1 = no barge monitor listened
    if heard:
        talking = sum(r[2] for r in heard)
        out.append(metric(keys[1][0], keys[1][1],
                          (f"{100.0 * talking / len(heard):.1f} per 100 turns with 3+ speech chunks inside 1.5 s of the close "
                           f"({talking} of {len(heard)}); {sum(r[3] for r in heard)} speech chunks in all; "
                           f"full 1.5 s window seen on {sum(1 for r in heard if r[6] >= 1400)} of {len(heard)} turns"),
                          n=len(heard), window=w, method=method + "; counted by the barge monitor, blind between close and mic open",
                          label="[Experimental]",
                          data={"still_talking": talking, "turns": len(heard), "talk_chunks": sum(r[3] for r in heard),
                                "full_window_turns": sum(1 for r in heard if r[6] >= 1400)}))
    else:
        out.append(unmeasured(keys[1][0], keys[1][1], "no turn in window had a listening barge monitor", window=w))
    wrote = rows  # a turn that played nothing carries 0/0, so it never starves the maths
    out.append(metric(keys[2][0], keys[2][1],
                      f"max {max(r[4] for r in wrote)} ms; total {sum(r[5] for r in wrote)} ms; "
                      f"{sum(1 for r in wrote if r[4] > 0)} of {len(wrote)} turns starved; "
                      f"mic muted on {sum(1 for r in wrote if r[7] > 0)} of {sum(1 for r in wrote if r[7] >= 0)} turns",
                      n=len(wrote), window=w, method=method, label="[Proven]",
                      data={"max_ms": max(r[4] for r in wrote), "sum_ms": sum(r[5] for r in wrote),
                            "starved_turns": sum(1 for r in wrote if r[4] > 0), "turns": len(wrote),
                            "muted_turns": sum(1 for r in wrote if r[7] > 0)}))
    if servers:
        out.append(metric(keys[3][0], keys[3][1], f"p50 {pct(servers, 50) / 1000:.2f} s", n=len(servers), window=w,
                          method=method, label="[Proven]", data={"p50_ms": pct(servers, 50), "turns": len(servers)}))
    else:
        out.append(unmeasured(keys[3][0], keys[3][1], "no turn in window had server audio", window=w))
    return out


def m_panel(host: str, since, until) -> list[dict]:
    """The panel rows: the inferred first-sound row, then the four measured FIRST_SOUND rows, from ONE ssh."""
    title = "Speech end to first sound, panel (inferred)"
    extra = PANEL_MEASURED_ROWS

    def fail(reason: str) -> list[dict]:
        return [unmeasured("speech_end_first_sound", title, reason)] + [unmeasured(k, t, reason) for k, t in extra]

    try:
        p = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", host, panel_command()],
                           capture_output=True, text=True, timeout=40)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return fail(f"ssh to the panel failed ({type(exc).__name__})")
    if p.returncode != 0:
        return fail(f"ssh to the panel failed (rc={p.returncode})")
    lines = p.stdout.splitlines()
    return [panel_metric(lines, since, until)] + panel_first_sound_metrics(lines, since, until)


# ---------------------------------------------------------------------------
# Database (read-only, numbers only)
# ---------------------------------------------------------------------------

_SYN = SYNTHETIC_USER_RE.pattern.replace("'", "''")
_GUESTS = ", ".join("'" + g + "'" for g in GUEST_USERS)
SESSION_SETTINGS = {"default_transaction_read_only": "on", "application_name": "felt-scorecard"}


def real_user_pred(col: str) -> str:
    """SQL predicate: col is not a guest sentinel, not a guest-<id> principal, not synthetic."""
    return (f"({col} IS NOT NULL AND {col} NOT IN ({_GUESTS}) AND lower({col}) NOT LIKE '{GUEST_PREFIX}%' "
            f"AND {col} !~* '{_SYN}')")


_NUMERIC = (int, float, Decimal, type(None))


async def _agg(conn, sql: str, *args) -> list[tuple]:
    """Run one SELECT; refuse anything that is not a single SELECT or that returns a non-number.
    Every query in this script goes through here (pinned by test_db_collect_read_only_and_all_via_agg)."""
    s = sql.strip()
    if not s.lower().startswith("select") or ";" in s:
        raise ValueError("only single SELECT statements are allowed")
    rows = await conn.fetch(s, *args)
    out = []
    for r in rows:
        vals = tuple(r)
        if not all(isinstance(v, _NUMERIC) and not isinstance(v, bool) for v in vals):
            raise ValueError("query returned a non-numeric value; refusing to continue")
        out.append(tuple(float(v) if isinstance(v, Decimal) else v for v in vals))
    return out


async def _member_ordinals(conn, values: list[str], *, sessions: bool) -> set[int]:
    """1-based positions of `values` that are real member account ids (or chat sessions owned by one).
    The values go INTO the query; only integers come back."""
    if not values:
        return set()
    if sessions:
        sql = f"""SELECT t.i FROM unnest($1::text[]) WITH ORDINALITY AS t(v, i)
                  JOIN chat_sessions s ON s.id = t.v JOIN users u ON u.id = s.user_id AND u.role = 'member'
                  WHERE {real_user_pred('s.user_id')}"""
    else:
        sql = f"""SELECT t.i FROM unnest($1::text[]) WITH ORDINALITY AS t(v, i)
                  JOIN users u ON u.id = t.v AND u.role = 'member' WHERE {real_user_pred('u.id')}"""
    return {int(r[0]) for r in await _agg(conn, sql, values)}


async def db_collect(dsn: str, long_since: dt.datetime, until: dt.datetime,
                     cand_users: list[str], cand_sessions: list[str]) -> dict:
    import asyncpg  # deferred, as the sibling maintenance scripts do

    conn = await asyncpg.connect(dsn, server_settings=dict(SESSION_SETTINGS), timeout=10)
    res: dict[str, Any] = {}
    try:
        async with conn.transaction(readonly=True):
            uo = await _member_ordinals(conn, cand_users, sessions=False)
            so = await _member_ordinals(conn, cand_sessions, sessions=True)
            res["member_users"] = {v for i, v in enumerate(cand_users, 1) if i in uo}
            res["member_sessions"] = {v for i, v in enumerate(cand_sessions, 1) if i in so}
            ls, un = long_since, until
            (row,) = await _agg(conn, f"""
                SELECT count(*), count(DISTINCT date((m.created_at::timestamptz) AT TIME ZONE '{_tzname()}')),
                       count(DISTINCT s.user_id)
                FROM chat_messages m JOIN chat_sessions s ON s.id = m.session_id
                JOIN users u ON u.id = s.user_id AND u.role = 'member'
                WHERE m.role = 'user' AND m.created_at::timestamptz >= $1 AND m.created_at::timestamptz < $2
                  AND {real_user_pred('s.user_id')}""", ls, un)
            res["usage"] = {"turns": row[0], "days": row[1], "members": row[2]}
            (row,) = await _agg(conn, """
                SELECT count(*) FROM chat_messages m JOIN chat_sessions s ON s.id = m.session_id
                WHERE m.role = 'user' AND m.created_at::timestamptz >= $1 AND m.created_at::timestamptz < $2""", ls, un)
            res["usage"]["all_user_rows"] = row[0]
            (row,) = await _agg(conn, """
                SELECT count(*), count(*) FILTER (WHERE last_raised_at IS NOT NULL AND last_raised_at <> '')
                FROM night_threads""")
            res["night_threads"] = {"threads": row[0], "raised": row[1]}
            (row,) = await _agg(conn, f"""
                SELECT count(*),
                       count(*) FILTER (WHERE session_id LIKE 'replay-%'),
                       count(*) FILTER (WHERE session_id NOT LIKE 'replay-%' AND {real_user_pred('user_id')}
                                        AND lower(coalesce(session_id, '')) !~ '^(qa|demo|bench|probe|test|bar-)'),
                       count(*) FILTER (WHERE status = 'kept'),
                       count(*) FILTER (WHERE status = 'missed'),
                       count(*) FILTER (WHERE status = 'open'),
                       count(*) FILTER (WHERE status = 'kept' AND session_id NOT LIKE 'replay-%' AND {real_user_pred('user_id')}
                                        AND lower(coalesce(session_id, '')) !~ '^(qa|demo|bench|probe|test|bar-)')
                FROM commitments
                WHERE created_at::timestamptz >= $1 AND created_at::timestamptz < $2""", ls, un)
            res["commitments"] = dict(zip(("rows", "replay", "household", "kept", "missed", "open", "household_kept"), row))
            (row,) = await _agg(conn, """
                SELECT count(*), coalesce(sum(duration_s), 0) / 3600.0,
                       count(*) FILTER (WHERE status = 'done' AND coalesce(length(transcript), 0) > 0),
                       coalesce(sum(duration_s) FILTER (WHERE status = 'done' AND coalesce(length(transcript), 0) > 0), 0) / 3600.0
                FROM omi_recordings
                WHERE started_at::timestamptz >= $1 AND started_at::timestamptz < $2""", ls, un)
            omi = {"recordings": row[0], "hours": row[1], "transcribed": row[2], "transcribed_hours": row[3]}
            (row,) = await _agg(conn, """
                SELECT count(*), count(*) FILTER (WHERE g.speaker = 'you')
                FROM omi_segments g JOIN omi_recordings r ON r.id = g.recording_id
                WHERE r.started_at::timestamptz >= $1 AND r.started_at::timestamptz < $2""", ls, un)
            omi.update(segments=row[0], wearer_segments=row[1])
            (row,) = await _agg(conn, """
                SELECT count(*), count(*) FILTER (WHERE status = 'ready'), count(*) FILTER (WHERE status = 'failed')
                FROM omi_day_reviews WHERE created_at::timestamptz >= $1 AND created_at::timestamptz < $2""", ls, un)
            omi.update(reviews=row[0], reviews_ready=row[1], reviews_failed=row[2])
            (row,) = await _agg(conn, """
                SELECT count(*), count(decided_via), count(*) FILTER (WHERE status = 'pending'),
                       count(written_ref)
                FROM omi_day_candidates WHERE created_at::timestamptz >= $1 AND created_at::timestamptz < $2""", ls, un)
            omi.update(candidates=row[0], decided=row[1], pending=row[2], written=row[3])
            res["omi"] = omi
    finally:
        await conn.close()
    return res


def _tzname() -> str:
    """IANA zone name for local-day bucketing in SQL (from /etc/timezone or the /etc/localtime link)."""
    try:
        with open("/etc/timezone") as fh:
            name = fh.read().strip()
            if re.fullmatch(r"[A-Za-z0-9_+\-/]{1,64}", name):
                return name
    except OSError:
        pass
    try:
        name = os.path.realpath("/etc/localtime").split("zoneinfo/", 1)[1]
        return name if re.fullmatch(r"[A-Za-z0-9_+\-/]{1,64}", name) else "UTC"
    except (IndexError, OSError):
        return "UTC"


def resolve_dsn() -> str:
    from purge_orphaned_test_data import _resolve_dsn  # the repo's shared POSTGRES_URL resolver
    return _resolve_dsn()


# ---------------------------------------------------------------------------
# MemPalace durable rows (chroma.sqlite3, mode=ro)
# ---------------------------------------------------------------------------

# Writer labels printed as-is; anything else is reported as `other` (lane 7 A1's observed writers).
SOURCE_ALLOWLIST = frozenset({
    "idle_consolidation", "voice_turn_digest", "digest", "voice", "synthesis", "conversation",
    "voice_regex", "turn_digest", "consolidation", "chat", "manual", "omi_day_review",
})

DURABLE_SQL = """
    SELECT u.string_value, src.string_value, st.string_value, count(*)
    FROM embeddings e
    JOIN segments s ON s.id = e.segment_id
    JOIN collections c ON c.id = s.collection AND c.name = 'mempalace_drawers'
    JOIN embedding_metadata u ON u.id = e.id AND u.key = 'user_id'
    LEFT JOIN embedding_metadata ts ON ts.id = e.id AND ts.key = 'added_ts'
    LEFT JOIN embedding_metadata at ON at.id = e.id AND at.key = 'added_at'
    LEFT JOIN embedding_metadata src ON src.id = e.id AND src.key = 'source'
    LEFT JOIN embedding_metadata st ON st.id = e.id AND st.key = 'status'
    WHERE (ts.float_value IS NOT NULL AND ts.float_value >= ? AND ts.float_value < ?)
       OR (ts.float_value IS NULL AND at.string_value >= ? AND at.string_value < ?)
    GROUP BY u.string_value, src.string_value, st.string_value"""


def durable_groups(palace: str, since: dt.datetime, until: dt.datetime) -> list[tuple[str, str | None, str | None, int]]:
    """One GROUP BY in the store (mode=ro). Columns read, and why:
      - user_id (account id): to keep only real-member rows; classified against Postgres, never printed;
      - source, status (writer and lifecycle labels): source is mapped through SOURCE_ALLOWLIST, status
        only tested for 'approved';
      - added_ts / added_at: the window filter, inside SQL.
    The document text (chroma:document) is never read."""
    def iso(t: dt.datetime) -> str:
        return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    con = sqlite3.connect(f"file:{os.path.join(palace, 'chroma.sqlite3')}?mode=ro", uri=True, timeout=5)
    try:
        return con.execute(DURABLE_SQL, (since.timestamp(), until.timestamp(), iso(since), iso(until))).fetchall()
    finally:
        con.close()


#: Pendant writers land in the same collection (omi_review_apply, the day review). They are the PENDANT supply channel
#: and are counted apart, so better pendant harvesting never reads as better panel/chat extraction.
PENDANT_SOURCES = frozenset({"omi_review", "omi_day_review"})


def summarise_durable(groups, member_users: set[str]) -> dict:
    n = approved = pendant = 0
    by_source: dict[str, int] = {}
    for uid, source, status, cnt in groups:
        if uid not in member_users:
            continue
        if source in PENDANT_SOURCES:
            pendant += cnt
            continue
        n += cnt
        approved += cnt if status == "approved" else 0
        label = source if source in SOURCE_ALLOWLIST else "other"
        by_source[label] = by_source.get(label, 0) + cnt
    return {"rows": n, "approved": approved, "by_source": by_source, "pendant_rows_excluded": pendant}


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------

def parse_local(s: str) -> dt.datetime:
    t = dt.datetime.fromisoformat(s)
    return t if t.tzinfo else t.replace(tzinfo=LOCAL_TZ)


def windows(args) -> tuple[dt.datetime, dt.datetime, dt.datetime]:
    """(since, until, long_since); raises ValueError for an empty or negative window."""
    until = parse_local(args.until) if args.until else dt.datetime.now(LOCAL_TZ)
    since = parse_local(args.since) if args.since else until - dt.timedelta(days=args.days)
    long_since = parse_local(args.long_since) if args.long_since else until - dt.timedelta(days=args.long_days)
    if not since < until:
        raise ValueError("the short window is empty or negative (--since/--days must give a start before --until)")
    if not long_since < until:
        raise ValueError("the long window is empty or negative (--long-since/--long-days must give a start before --until)")
    if args.nights < 1:
        raise ValueError("--nights must be at least 1")
    return since, until, long_since


def rate(count: float, days: float) -> str:
    return f"{count / days:.2f}/day" if days > 0 else "unmeasured (empty window)"


def build(args) -> dict:
    t_start = time.monotonic()
    since, until, long_since = windows(args)
    long_days = (until - long_since).total_seconds() / 86400.0
    short_days = (until - since).total_seconds() / 86400.0
    lw = wstr(long_since, until)
    out: list[dict] = []

    paths = sorted(glob.glob(os.path.join(args.log_dir, "zoe-data.app.log*")))
    logset = load_log_lines(paths)
    cand_users, cand_sessions = candidate_values(logset.lines, since, until)

    groups_long = groups_short = None
    store_err = ""
    try:
        groups_long = durable_groups(args.palace, long_since, until)
        groups_short = durable_groups(args.palace, since, until)
    except (sqlite3.Error, OSError) as exc:
        store_err = f"MemPalace store unreadable ({type(exc).__name__})"
    store_users = sorted({g[0] for g in (groups_long or []) if g[0]})

    db: dict | None = None
    db_err = ""
    dsn = resolve_dsn()
    if not dsn:
        db_err = "POSTGRES_URL not set and not found in the zoe-data service .env"
    else:
        try:
            db = asyncio.run(db_collect(dsn, long_since, until, sorted(set(cand_users) | set(store_users)), cand_sessions))
        except Exception as exc:  # noqa: BLE001 - a scorecard reports, it never crashes
            db_err = f"database read failed ({type(exc).__name__})"
    members = Members(db["member_users"], db["member_sessions"]) if db else None

    logs = log_section(logset, since, until, members, db_err)
    out += [logs["memory_served_used"], logs["recall_every_turn"], logs["continuity"], logs["tool_calls"],
            logs["value_corrections"]]

    if db:
        nt = db["night_threads"]
        out.append(metric("night_threads_raised", "Night-mind threads ever raised on a turn",
                          frac(int(nt["raised"]), int(nt["threads"])), n=int(nt["threads"]), window="all time",
                          method="night_threads count(*) vs last_raised_at set (no serve marker exists yet)",
                          data=nt))
    else:
        out.append(unmeasured("night_threads_raised", "Night-mind threads raised", db_err))

    if store_err or not db:
        out.append(unmeasured("durable_rows_panel", "Durable memory rows per day, panel/chat", store_err or db_err))
    else:
        d30 = summarise_durable(groups_long, members.users)
        d7 = summarise_durable(groups_short, members.users)
        src = ", ".join(f"{k} {v}" for k, v in sorted(d30["by_source"].items(), key=lambda kv: (-kv[1], kv[0])))
        out.append(metric("durable_rows_panel", "Durable memory rows per day, panel/chat (member)",
                          f"{rate(d30['rows'], long_days)} over {long_days:.0f} d ({d30['rows']} rows, {d30['approved']} approved); "
                          f"{rate(d7['rows'], short_days)} over last {short_days:.1f} d ({d7['rows']} rows)",
                          n=d30["rows"], window=lw,
                          method=(f"MemPalace mempalace_drawers, one GROUP BY (user_id, source, status), member user_id, "
                                  f"added_ts/added_at in window; pendant writers ({', '.join(sorted(PENDANT_SOURCES))}) excluded: "
                                  f"{d30['pendant_rows_excluded']}; by source: {src or 'none'}"),
                          data={"long": d30, "short": d7}))

    if db:
        o = db["omi"]
        raises, raise_reason = count_raises(logset, long_since, until)
        offered_txt = str(raises) if raises is not None else "unmeasured"
        out.append(metric("durable_rows_pendant", "Durable memory rows per day, pendant",
                          f"{rate(o['written'], long_days)} ({int(o['written'])} written)", n=int(o["candidates"]), window=lw,
                          method="omi_day_candidates written_ref set", data={"written": o["written"]}))
        out.append(metric("pendant_funnel", "Pendant funnel",
                          (f"recordings {int(o['recordings'])} ({o['hours']:.1f} h) -> transcripts {int(o['transcribed'])} "
                           f"({o['transcribed_hours']:.1f} h) -> segments {int(o['segments'])} -> wearer {int(o['wearer_segments'])} "
                           f"-> reviews {int(o['reviews'])} ({int(o['reviews_ready'])} ready, {int(o['reviews_failed'])} failed) "
                           f"-> candidates {int(o['candidates'])} -> offered {offered_txt} -> decided {int(o['decided'])} "
                           f"-> written {int(o['written'])}"),
                          n=int(o["recordings"]), window=lw,
                          method=("omi_recordings by started_at (transcript = status done and non-empty); omi_segments "
                                  "speaker='you'; omi_day_reviews / omi_day_candidates by created_at; offered = "
                                  "DAY_REVIEW_RAISE raised=1 log lines"
                                  + (f"; offered unmeasured: {raise_reason}" if raise_reason else "")),
                          data={**o, "offered_raises": raises, "offered_reason": raise_reason}))
        c = db["commitments"]
        out.append(metric("commitments", "Commitments ledger rows",
                          (f"{int(c['rows'])} rows: replay artifacts {int(c['replay'])}, household {int(c['household'])} "
                           f"(kept {int(c['household_kept'])}); all rows kept {int(c['kept'])} / missed {int(c['missed'])} / open {int(c['open'])}"),
                          n=int(c["rows"]), window=lw,
                          method="commitments by created_at; replay = session_id 'replay-%'; household = not replay, real user, not a qa/demo/bench/probe/test/bar session",
                          data=c))
    else:
        out.append(unmeasured("durable_rows_pendant", "Durable memory rows per day, pendant", db_err))
        out.append(unmeasured("pendant_funnel", "Pendant funnel", db_err))
        out.append(unmeasured("commitments", "Commitments ledger rows", db_err))

    if args.panel:
        out.extend(m_panel(args.panel_host, since, until))
    else:
        reason = "panel log lives on the Pi; skipped by default (pass --panel to read it over ssh)"
        out.append(unmeasured("speech_end_first_sound", "Speech end to first sound, panel", reason))
        out.extend(unmeasured(k, t, reason) for k, t in PANEL_MEASURED_ROWS)

    out.append(logs["brain_first_text"])
    out.append(m_nights(os.path.join(args.log_dir, "night-window.log"), until, args.nights))

    if db:
        u = db["usage"]
        out.append(metric("usage", "Members active / days of use / real member turns",
                          (f"{int(u['members'])} member(s) active, {int(u['days'])} days of use, "
                           f"{int(u['turns'])} real member user turns (of {int(u['all_user_rows'])} user rows)"),
                          n=int(u["turns"]), window=lw,
                          method="chat_messages role=user joined to chat_sessions and users role=member; guest/synthetic excluded; local days",
                          data=u))
    else:
        out.append(unmeasured("usage", "Members active / days of use", db_err))

    out += alert_section(args, logset, long_since, until)
    out += instrument_section(args, logset, long_since, until)

    for m in out:
        if m["status"] == "measured" and m["n"] == 0:
            m["note"] = "n=0: no data in window (a count, not a zero rate)"
    return {"generated_at": dt.datetime.now(LOCAL_TZ).isoformat(timespec="seconds"),
            "window": {"since": since.isoformat(), "until": until.isoformat(), "long_since": long_since.isoformat(),
                       "nights": args.nights},
            "runtime_s": round(time.monotonic() - t_start, 2), "metrics": out}


def render(report: dict) -> str:
    w = report["window"]
    lines = [f"Felt scorecard  generated {report['generated_at']}  runtime {report['runtime_s']} s",
             f"short window {w['since'][:16]}..{w['until'][:16]}   long window {w['long_since'][:16]}..{w['until'][:16]}", ""]
    section = None
    for m in report["metrics"]:
        if m.get("section") and m["section"] != section:
            section = m["section"]
            lines += ["", f"== {section} =="]
        lines.append(f"* {m['title']}")
        lines.append(f"    value : {m['value']}" + (f"   n={m['n']}" if m.get("n") is not None else ""))
        if m["status"] == "unmeasured":
            lines.append(f"    reason: {m['reason']}")
        else:
            lines.append(f"    window: {m['window']}")
            lines.append(f"    how   : {m['method']} {m['label']}".rstrip())
        if m.get("note"):
            lines.append(f"    note  : {m['note']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", help="short window start, local ISO (default: until - --days)")
    ap.add_argument("--until", help="window end, local ISO (default: now)")
    ap.add_argument("--long-since", help="long window start, local ISO (default: until - --long-days)")
    ap.add_argument("--days", type=float, default=7.0, help="short window for log metrics (default 7)")
    ap.add_argument("--long-days", type=float, default=30.0, help="long window for usage and supply (default 30)")
    ap.add_argument("--nights", type=int, default=8, help="night windows to score (default 8)")
    ap.add_argument("--log-dir", default=os.path.expanduser("~/.zoe-logs"))
    ap.add_argument("--palace", default=os.environ.get("MEMPALACE_DATA_DIR", os.path.expanduser("~/.mempalace")))
    ap.add_argument("--panel", action="store_true", help="also read the panel daemon log on the Pi over ssh")
    ap.add_argument("--panel-host", default="zoe-pi")
    ap.add_argument("--crash-log", help="crash-loop watcher log (default: <log-dir>/crash-loop-watch.log)")
    ap.add_argument("--night-reports", default=os.path.expanduser("~/.zoe/night-reports"))
    ap.add_argument("--backup-log", help="backup verify log (default: <log-dir>/backup-verify.log)")
    ap.add_argument("--backup-state", default=os.path.expanduser("~/.local/state/zoe-offbox/last_restore_drill.json"))
    ap.add_argument("--health-log", help="zoe_health.py log (default: <log-dir>/health.log)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    try:
        windows(args)
    except ValueError as exc:
        ap.error(str(exc))  # exit 2, before any log, store or database read
    report = build(args)
    print(json.dumps(report, indent=2, default=str) if args.json else render(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
