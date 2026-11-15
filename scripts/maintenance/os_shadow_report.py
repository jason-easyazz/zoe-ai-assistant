#!/usr/bin/env python3
"""Turn a week of Zoe OS list-shadow log lines into the comparison table (docs/knowledge/zoe-os-list-shadow.md, section 5).

Reads the ``OS_SHADOW_LIST_ADD`` lines ``services/zoe-data/os_shadow.py`` writes (codes and numbers only: no item, no list name, no
person) from the files named on the command line, or stdin. Read-only; stdlib only; prints counts and milliseconds, never a line.

    python3 scripts/maintenance/os_shadow_report.py ~/.zoe-logs/zoe-data.*
    python3 scripts/maintenance/os_shadow_report.py --json < some.log

With the flag on the client writes exactly ONE line per add, whatever happened to it, so every add is in the table. The numbers: match
rate = ``match`` / compared adds (an add the kernel was asked about and answered or failed on; the not-compared codes
``member_not_in_roster`` / ``dropped_erased`` / ``queue_full`` / ``live_noop`` / ``worker_down`` / ``no_input`` / ``shadow_error`` are
listed apart and are not in it); hop p50 / p95 over compared adds the kernel answered.

Nothing is dropped: a result code this reader does not know is counted as ``unknown`` (a compared add that is not a match: the
conservative side), a line with the marker that cannot be read (a repeated key, no result, a bad number) is counted as ``rejected``,
and ``unknown_fields`` counts lines with any other field outside its closed set. ``match + mismatch + unknown + not_compared`` always
equals ``adds_logged``, and ``adds_logged + rejected`` equals the marker lines read.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import sys
from collections import Counter

LINE = re.compile(r"OS_SHADOW_LIST_ADD\s+(.*)")
#: every field the report reads, with its CLOSED set of values (os_shadow.py writes only these). Anything else becomes ``unknown``: the
#: report never echoes a value it does not know, so a planted or corrupted field cannot print text.
UNKNOWN = "unknown"
ALLOWED = {
    "src": frozenset({"spoken", "single", "skybridge", "rest", "suggestion", "service", "other"}),
    "cls": frozenset({"shopping", "todo", "personal", "other"}),
    "live": frozenset({"added", "refused", "noop"}),
    "kernel": frozenset({"verified", "unverified", "uncertain", "refused", "none"}),
    "result": frozenset({"worker_down", "match", "kernel_refused_live_did", "kernel_unverified_live_did", "kernel_did_live_refused", "item_differs",
                         "list_differs", "kernel_unavailable", "timeout", "kernel_malformed", "member_not_in_roster", "dropped_erased",
                         "queue_full", "live_noop", "no_input", "shadow_error", "member_differs",
                         "member_unproven"}),
    "reason": frozenset({"-", "no_grant", "manifest", "params", "cause", "forgotten", "other", "timeout", "protocol", "socket",
                         "unavailable", "not_provisioned", "circuit", "no_credential", "closed", "auth", "handshake",
                         "hook", "worker", "code", "pending_forget", "crossed",
                         "forgotten_before"}),
}
NUMBERS = ("hop_ms", "q_ms")
#: a number above this is not a measurement (the add's whole budget is 1.5 s; a day in the queue is already absurd): rejected
MAX_MS = 86_400_000.0
NOT_COMPARED = {"member_not_in_roster", "dropped_erased", "queue_full", "live_noop", "worker_down", "no_input", "shadow_error"}
MISMATCH = ("kernel_refused_live_did", "kernel_unverified_live_did", "kernel_did_live_refused", "item_differs", "list_differs",
            "member_differs", "member_unproven", "kernel_malformed", "kernel_unavailable", "timeout")


def key_tag(key: str) -> str:
    """An unknown KEY's name is caller-controlled text in principle: never printed raw, only as an 8-hex tag (enough to tell two
    unknown keys apart and to grep the writer for them)."""
    return hashlib.sha256(key.encode("utf-8", "replace")).hexdigest()[:8]


def clean(fields: list) -> "dict | None":
    """One line's ``key=value`` pairs as a record of KNOWN keys with CLOSED values, or None (the line is rejected): a key given twice
    (a later copy must never override the writer's), no ``result``, or a number field (``hop_ms``, ``q_ms``) that is missing,
    empty, not a number, not finite (nan, inf), negative or above ``MAX_MS``. An unknown value becomes ``unknown``; an unknown KEY
    is kept as its tag in ``_unknown_keys`` (counted and shown, never its name)."""
    seen: dict = {}
    for kv in fields:
        if "=" not in kv:
            continue
        key, value = kv.split("=", 1)
        if key in seen:
            return None
        seen[key] = value
    if "result" not in seen:
        return None
    rec = {k: (seen[k] if seen[k] in ALLOWED[k] else UNKNOWN) for k in ALLOWED if k in seen}
    for k in NUMBERS:
        try:
            v = float(seen[k])
        except (KeyError, ValueError):
            return None
        if not math.isfinite(v) or v < 0 or v > MAX_MS:
            return None
        rec[k] = v
    extra = sorted(k for k in seen if k not in ALLOWED and k not in NUMBERS)
    if extra:
        rec["_unknown_keys"] = tuple(key_tag(k) for k in extra)
    return rec


def parse_counted(lines) -> tuple[list[dict], int]:
    """``(records, rejected)``: every marker line is either a record or counted as rejected, never silently skipped."""
    out, rejected = [], 0
    for raw in lines:
        m = LINE.search(raw)
        if m:
            rec = clean(m.group(1).split())
            if rec is None:
                rejected += 1
            else:
                out.append(rec)
    return out, rejected


def parse(lines) -> list[dict]:
    return parse_counted(lines)[0]


def pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    v = sorted(values)
    return v[min(len(v) - 1, max(0, int(round(p / 100 * (len(v) - 1)))))]


def summarize(recs: list[dict], rejected: int = 0) -> dict:
    results = Counter(r["result"] for r in recs)
    compared = [r for r in recs if r["result"] not in NOT_COMPARED]
    answered = [r["hop_ms"] for r in compared if r.get("kernel") not in (None, "none")]
    n = len(compared)
    return {
        "adds_logged": len(recs), "compared": n,
        "match": results["match"], "match_rate": round(results["match"] / n, 4) if n else None,
        "mismatch": {k: results[k] for k in MISMATCH if results[k]},
        "refused_by_reason": dict(Counter(r.get("reason", "-") for r in compared if r["result"] == "kernel_refused_live_did")),
        "not_compared": {k: results[k] for k in sorted(NOT_COMPARED) if results[k]},
        "unknown": results[UNKNOWN], "rejected": rejected,
        "unknown_fields": sum(1 for r in recs if any(v == UNKNOWN for k, v in r.items() if k != "result")),
        "unknown_keys": dict(sorted(Counter(t for r in recs for t in r.get("_unknown_keys", ())).items())),
        "shadow_errors": results["shadow_error"],
        "kernel_unavailable": results["kernel_unavailable"], "timeouts": results["timeout"],
        "hop_ms_p50": round(pct(answered, 50), 2), "hop_ms_p95": round(pct(answered, 95), 2), "hop_answered": len(answered),
        "by_src": dict(Counter(r.get("src", "?") for r in recs)), "by_cls": dict(Counter(r.get("cls", "?") for r in recs)),
    }


def render(s: dict) -> str:
    rate = "n/a" if s["match_rate"] is None else f"{s['match_rate'] * 100:.1f}%"
    rows = [f"list adds logged: {s['adds_logged']}    compared: {s['compared']}    match: {s['match']} ({rate})", "",
            "| result | count |", "|---|---|", f"| match | {s['match']} |"]
    rows += [f"| {k} | {v} |" for k, v in s["mismatch"].items()]
    if s["unknown"]:
        rows.append(f"| unknown result (counted as not a match) | {s['unknown']} |")
    rows += [f"| (not compared) {k} | {v} |" for k, v in s["not_compared"].items()]
    if s["refused_by_reason"]:
        rows += ["", "kernel refused, by reason: " + ", ".join(f"{k}={v}" for k, v in sorted(s["refused_by_reason"].items()))]
    rows += ["", f"kernel hop (publish + act), {s['hop_answered']} answered adds: p50 {s['hop_ms_p50']} ms, p95 {s['hop_ms_p95']} ms",
             f"kernel unavailable: {s['kernel_unavailable']}    timeouts: {s['timeouts']}    shadow errors: {s['shadow_errors']}",
             f"unreadable: rejected lines {s['rejected']}    unknown result {s['unknown']}    lines with an unknown field {s['unknown_fields']}",
             "unknown keys (tag=lines): " + (", ".join(f"{t}={n}" for t, n in s["unknown_keys"].items()) or "none"),
             "by source: " + ", ".join(f"{k}={v}" for k, v in sorted(s["by_src"].items())),
             "by list class: " + ", ".join(f"{k}={v}" for k, v in sorted(s["by_cls"].items()))]
    return "\n".join(rows)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    as_json = "--json" in args
    paths = [a for a in args if a != "--json"]
    lines: list[str] = []
    for p in paths:
        with open(p, encoding="utf-8", errors="replace") as fh:
            lines += fh.readlines()
    if not paths:
        lines = sys.stdin.readlines()
    s = summarize(*parse_counted(lines))
    print(json.dumps(s, indent=2) if as_json else render(s))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
