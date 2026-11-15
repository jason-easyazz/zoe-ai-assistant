#!/usr/bin/env python3
"""Turn a week of Zoe OS list-shadow log lines into the comparison table (docs/knowledge/zoe-os-list-shadow.md, section 5).

Reads the ``OS_SHADOW_LIST_ADD`` lines ``services/zoe-data/os_shadow.py`` writes (codes and numbers only: no item, no list name, no
person) from the files named on the command line, or stdin. Read-only; stdlib only; prints counts and milliseconds, never a line.

    python3 scripts/maintenance/os_shadow_report.py ~/.zoe-logs/zoe-data.*
    python3 scripts/maintenance/os_shadow_report.py --json < some.log

The numbers: match rate = ``match`` / compared adds (an add the kernel was asked about and answered or failed on; not-compared codes
``member_not_in_roster`` / ``dropped_erased`` / ``queue_full`` are listed apart and are not in it); hop p50 / p95 over compared adds
the kernel answered. ``live_noop`` and the not-compared codes that never log a line are in-process counters (``os_shadow.counters()``),
not in the log, so they are absent here.
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter

LINE = re.compile(r"OS_SHADOW_LIST_ADD\s+(.*)")
#: every field the report reads, with its CLOSED set of values (os_shadow.py writes only these). Anything else becomes ``unknown``: the
#: report never echoes a value it does not know, so a planted or corrupted field cannot print text.
UNKNOWN = "unknown"
ALLOWED = {
    "src": frozenset({"spoken", "single", "skybridge", "rest", "suggestion"}),
    "cls": frozenset({"shopping", "todo", "personal", "other"}),
    "live": frozenset({"added", "refused"}),
    "kernel": frozenset({"verified", "unverified", "uncertain", "refused", "none"}),
    "result": frozenset({"worker_down", "match", "kernel_refused_live_did", "kernel_unverified_live_did", "kernel_did_live_refused", "item_differs",
                         "list_differs", "kernel_unavailable", "timeout", "kernel_malformed", "member_not_in_roster", "dropped_erased",
                         "queue_full", "live_noop"}),
    "reason": frozenset({"-", "no_grant", "manifest", "params", "cause", "forgotten", "other", "timeout", "protocol", "socket",
                         "unavailable", "not_provisioned", "circuit", "no_credential", "closed", "auth", "handshake"}),
}
NUMBERS = ("hop_ms", "q_ms")
NOT_COMPARED = {"member_not_in_roster", "dropped_erased", "queue_full", "live_noop", "worker_down"}
MISMATCH = ("kernel_refused_live_did", "kernel_unverified_live_did", "kernel_did_live_refused", "item_differs", "list_differs",
            "kernel_malformed", "kernel_unavailable", "timeout")


def clean(fields: list) -> "dict | None":
    """One line's ``key=value`` pairs as a record of KNOWN keys with CLOSED values, or None (the line is rejected): a key given twice
    (a later copy must never override the writer's), no ``result``, or a ``hop_ms`` that is not a number. Unknown keys are dropped;
    an unknown value becomes ``unknown``."""
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
    try:
        rec["hop_ms"] = float(seen.get("hop_ms", "nan"))
    except ValueError:
        return None
    return rec


def parse(lines) -> list[dict]:
    out = []
    for raw in lines:
        m = LINE.search(raw)
        if m:
            rec = clean(m.group(1).split())
            if rec is not None:
                out.append(rec)
    return out


def pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    v = sorted(values)
    return v[min(len(v) - 1, max(0, int(round(p / 100 * (len(v) - 1)))))]


def summarize(recs: list[dict]) -> dict:
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
        "kernel_unavailable": results["kernel_unavailable"], "timeouts": results["timeout"],
        "hop_ms_p50": round(pct(answered, 50), 2), "hop_ms_p95": round(pct(answered, 95), 2), "hop_answered": len(answered),
        "by_src": dict(Counter(r.get("src", "?") for r in recs)), "by_cls": dict(Counter(r.get("cls", "?") for r in recs)),
    }


def render(s: dict) -> str:
    rate = "n/a" if s["match_rate"] is None else f"{s['match_rate'] * 100:.1f}%"
    rows = [f"list adds logged: {s['adds_logged']}    compared: {s['compared']}    match: {s['match']} ({rate})", "",
            "| result | count |", "|---|---|", f"| match | {s['match']} |"]
    rows += [f"| {k} | {v} |" for k, v in s["mismatch"].items()]
    rows += [f"| (not compared) {k} | {v} |" for k, v in s["not_compared"].items()]
    if s["refused_by_reason"]:
        rows += ["", "kernel refused, by reason: " + ", ".join(f"{k}={v}" for k, v in sorted(s["refused_by_reason"].items()))]
    rows += ["", f"kernel hop (publish + act), {s['hop_answered']} answered adds: p50 {s['hop_ms_p50']} ms, p95 {s['hop_ms_p95']} ms",
             f"kernel unavailable: {s['kernel_unavailable']}    timeouts: {s['timeouts']}",
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
    s = summarize(parse(lines))
    print(json.dumps(s, indent=2) if as_json else render(s))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
